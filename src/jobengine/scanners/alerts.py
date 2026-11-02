"""Source 6: job-alert emails from LinkedIn, Indeed, Handshake and eFinancialCareers.

Each site emails new matches to Gmail; a Gmail filter puts them under one label.
This module reads that label and pulls the job links out of each email.
Nothing logs into the job sites themselves.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from ..models import Posting

log = logging.getLogger(__name__)

# site -> (pattern finding the job id inside a (possibly tracking) link, canonical URL)
JOB_LINKS = {
    "linkedin": (
        re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d{6,})"),
        "https://www.linkedin.com/jobs/view/{id}/",
    ),
    "indeed": (
        re.compile(r"indeed\.com/[^\s\"']*?[?&]jk=([0-9a-f]{10,})"),
        "https://www.indeed.com/viewjob?jk={id}",
    ),
    "handshake": (
        re.compile(r"joinhandshake\.com/(?:stu/|job-search/)?(?:jobs|postings)/(\d+)"),
        "https://app.joinhandshake.com/jobs/{id}",
    ),
    "efinancialcareers": (
        re.compile(r"efinancialcareers\.com/[^\s\"']*?\.id(\d{5,})"),
        "https://www.efinancialcareers.com/jobs.id{id}",
    ),
}

# Link texts that are buttons, not job titles.
_NOT_TITLES = re.compile(
    r"^(apply|easy apply|view( job| details)?|see (all|more)( jobs)?|save|unsubscribe|"
    r"manage|learn more|search|jobs?|new|promoted|actively recruiting)\b",
    re.IGNORECASE,
)
_SEPARATORS = re.compile(r"\s*(?:·|•|\||–| - )\s*")


_BLOCKS = ("br", "p", "div", "td", "tr", "li", "table")


class _Flow(HTMLParser):
    """Turns HTML into an ordered list of ("link", href, [text lines]) and ("text", text) items.
    A link's text keeps its line breaks: LinkedIn puts a whole job card (title, then
    "Company · Location", then pay) inside one link."""

    def __init__(self):
        super().__init__()
        self.items: list[tuple] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush_text()
            self._href = dict(attrs).get("href") or ""
        elif tag in _BLOCKS:
            if self._href is None:
                self._flush_text()
            else:
                self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            lines = [" ".join(x.split()) for x in "".join(self._buf).split("\n")]
            self.items.append(("link", self._href, [x for x in lines if x]))
            self._href, self._buf = None, []
        elif tag in _BLOCKS:
            if self._href is None:
                self._flush_text()
            else:
                self._buf.append("\n")

    def handle_data(self, data):
        self._buf.append(data)

    def _flush_text(self):
        if self._href is None:
            text = " ".join("".join(self._buf).split())
            if text:
                self.items.append(("text", text))
            self._buf = []


# Indeed wraps every link as cts.indeed.com/v3/<gzip+base64 JSON>; the real URL is in "u".
_INDEED_WRAP = re.compile(r"cts\.indeed\.com/v\d+/(H4sI[A-Za-z0-9_\-]+)")


def _unwrap(href: str) -> str:
    m = _INDEED_WRAP.search(href)
    if not m:
        return href
    payload = m.group(1) + "=" * (-len(m.group(1)) % 4)
    try:
        return json.loads(gzip.decompress(base64.urlsafe_b64decode(payload))).get("u", href)
    except (ValueError, OSError, AttributeError):
        return href


def _match_job(href: str) -> tuple[str, str] | None:
    decoded = unquote(unquote(_unwrap(href)))
    for site, (pattern, _) in JOB_LINKS.items():
        m = pattern.search(decoded)
        if m:
            return site, m.group(1)
    return None


def _is_title(lines: list[str]) -> bool:
    return bool(lines) and len(lines[0]) >= 4 and not _NOT_TITLES.match(lines[0])


def parse_alert_html(html_body: str) -> list[Posting]:
    """Extract jobs from one alert email. The title is the job link's first line; company and
    location come from the link's next line (LinkedIn cards) or the text after it (Indeed,
    Handshake, eFinancialCareers print "Company · Location" under each title)."""
    flow = _Flow()
    flow.feed(html_body)
    flow._flush_text()
    items = flow.items

    jobs: dict[tuple[str, str], Posting] = {}
    for i, item in enumerate(items):
        if item[0] != "link":
            continue
        hit = _match_job(item[1])
        if not hit or hit in jobs:
            continue  # not a job link, or the same job linked twice (logo + title)
        lines = item[2]
        if not _is_title(lines):
            # Logo or "View job" button: use a nearby link to the same job that has a title.
            nearby = [n for n in items[i + 1 : i + 6] if n[0] == "link" and _match_job(n[1]) == hit]
            lines = next((n[2] for n in nearby if _is_title(n[2])), [])
        if not lines:
            continue
        details = [p for line in lines[1:2] for p in _SEPARATORS.split(line) if p]
        if not details:
            for nxt in items[i + 1 : i + 8]:
                if nxt[0] == "link":
                    if _match_job(nxt[1]) not in (None, hit):
                        break  # reached the next job
                    continue
                details.extend(p for p in _SEPARATORS.split(nxt[1]) if p)
                if len(details) >= 2:
                    break
        jobs[hit] = Posting(
            company=details[0] if details else "",
            title=lines[0],
            url=JOB_LINKS[hit[0]][1].format(id=hit[1]),
            source=f"alert:{hit[0]}",
            locations=details[1:2],
            flags=[] if details else ["company unknown"],
        )
    return list(jobs.values())


def _html_part(payload: dict) -> str:
    """Find the text/html part of a Gmail API message payload."""
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        found = _html_part(part)
        if found:
            return found
    return ""


def scan(settings: dict, root: Path) -> list[Posting]:
    """Read unread messages under the Gmail label, parse them, then mark them read."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        log.warning('Gmail libraries not installed; run: pip install -e ".[gmail]"')
        return []

    scopes = ["https://www.googleapis.com/auth/gmail.modify"]
    token_path = root / settings["token_path"]
    creds = (
        Credentials.from_authorized_user_file(token_path, scopes) if token_path.exists() else None
    )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                root / settings["credentials_path"], scopes
            )
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    labels = gmail.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((lb["id"] for lb in labels if lb["name"] == settings["gmail_label"]), None)
    if not label_id:
        log.warning("Gmail label '%s' not found; create it first", settings["gmail_label"])
        return []

    listing = (
        gmail.users()
        .messages()
        .list(
            userId="me", labelIds=[label_id, "UNREAD"], maxResults=settings.get("max_messages", 100)
        )
        .execute()
    )
    jobs: list[Posting] = []
    for ref in listing.get("messages", []):
        msg = gmail.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        found = parse_alert_html(_html_part(msg.get("payload", {})))
        log.info("alert email %s: %d jobs", ref["id"], len(found))
        jobs.extend(found)
        gmail.users().messages().modify(
            userId="me", id=ref["id"], body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    return jobs
