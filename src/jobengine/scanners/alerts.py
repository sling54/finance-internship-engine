"""Source 6: job-alert emails from LinkedIn, Indeed, Handshake and eFinancialCareers.

Each site emails new matches to Gmail; a Gmail filter puts them under one label.
This module reads that label and pulls the job links out of each email.
Nothing logs into the job sites themselves.
"""

from __future__ import annotations

import base64
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


class _Flow(HTMLParser):
    """Turns HTML into an ordered list of ("link", href, text) and ("text", text) items."""

    def __init__(self):
        super().__init__()
        self.items: list[tuple] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush_text()
            self._href = dict(attrs).get("href") or ""
        elif tag in ("br", "p", "div", "td", "tr", "li"):
            self._flush_text()

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.items.append(("link", self._href, " ".join("".join(self._buf).split())))
            self._href, self._buf = None, []
        elif tag in ("p", "div", "td", "tr", "li"):
            self._flush_text()

    def handle_data(self, data):
        self._buf.append(data)

    def _flush_text(self):
        if self._href is None:
            text = " ".join("".join(self._buf).split())
            if text:
                self.items.append(("text", text))
            self._buf = []


def _match_job(href: str) -> tuple[str, str] | None:
    decoded = unquote(unquote(href))
    for site, (pattern, _) in JOB_LINKS.items():
        m = pattern.search(decoded)
        if m:
            return site, m.group(1)
    return None


def parse_alert_html(html_body: str) -> list[Posting]:
    """Extract jobs from one alert email: title from the link text, company and location
    from the text right after it (sites print "Company · Location" under each title)."""
    flow = _Flow()
    flow.feed(html_body)
    flow._flush_text()
    items = flow.items

    jobs: dict[tuple[str, str], Posting] = {}
    for i, item in enumerate(items):
        if item[0] != "link":
            continue
        hit = _match_job(item[1])
        if not hit:
            continue
        site, job_id = hit
        title = item[2]
        key = (site, job_id)
        if key in jobs:
            continue  # same job linked twice (logo + title); first title wins
        if not title or _NOT_TITLES.match(title) or len(title) < 4:
            # Logo or button link: look for the real title link for this job later.
            title = ""
        details: list[str] = []
        for nxt in items[i + 1 : i + 4]:
            if nxt[0] == "link":
                if _match_job(nxt[1]) and _match_job(nxt[1]) != hit:
                    break
                continue
            details.extend(p for p in _SEPARATORS.split(nxt[1]) if p)
            if len(details) >= 2:
                break
        if not title:
            # Try the next link to the same job for its text.
            for nxt in items[i + 1 : i + 6]:
                if (
                    nxt[0] == "link"
                    and _match_job(nxt[1]) == hit
                    and nxt[2]
                    and not _NOT_TITLES.match(nxt[2])
                ):
                    title = nxt[2]
                    break
        if not title:
            continue
        jobs[key] = Posting(
            company=details[0] if details else "",
            title=title,
            url=JOB_LINKS[site][1].format(id=job_id),
            source=f"alert:{site}",
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
