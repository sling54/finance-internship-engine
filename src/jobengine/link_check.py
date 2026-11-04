"""Check that each kept role's listing is still open, after filtering and before results.

Each role gets a status: open, closed, or unknown. Firm job boards are asked through
their public job endpoints (Workday, Greenhouse, Lever, Ashby, SmartRecruiters); any
other link is fetched and read for a 404/410 or a "no longer accepting" phrase.

Only a clear answer counts as closed. Timeouts, 403, 429, 5xx and anything unexpected
are unknown, so a slow or blocking site never removes a role. Closed roles are recorded
with jobengine.removed (by="link_check"); unknown ones are kept with a "not verified"
flag, unless they are older than max_unknown_age_days.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import removed
from .http import USER_AGENT

log = logging.getLogger(__name__)

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"
NOT_VERIFIED = "not verified"
CAREERS_REDIRECT = "redirects to careers page"

DEFAULTS = {
    "enabled": True,
    "concurrency": 8,
    "per_host_seconds": 1.0,
    "timeout_seconds": 15,
    "cache_hours": 24,
    "max_unknown_age_days": 120,
    "closed_phrases": [
        "no longer accepting applications",
        "position has been filled",
        "job is no longer available",
        "this job is no longer open",
        "this position is no longer available",
        "job posting has expired",
    ],
}


def settings(sources: dict) -> dict:
    return {**DEFAULTS, **(sources.get("link_check") or {})}


@dataclass
class Check:
    status: str
    detail: str = ""
    flag: str = ""
    evidence: str = ""  # for closed: the URL that answered and how, so it can be audited


# ---------- fetching ----------


def http_get(timeout: float):
    """A GET that follows redirects and never retries: a slow site is just unknown."""
    import requests

    local = threading.local()  # one session per worker thread

    def get(url: str):
        if not hasattr(local, "session"):
            local.session = requests.Session()
            local.session.headers["User-Agent"] = USER_AGENT
        return local.session.get(url, timeout=timeout, allow_redirects=True)

    return get


class HostPacer:
    """At most one request per host every `gap` seconds, across all threads."""

    def __init__(self, gap: float, clock=time.monotonic, sleep=time.sleep):
        self.gap, self.clock, self.sleep = gap, clock, sleep
        self.lock = threading.Lock()
        self.next_slot: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self.lock:
            now = self.clock()
            slot = max(now, self.next_slot.get(host, now))
            self.next_slot[host] = slot + self.gap
        if slot > now:
            self.sleep(slot - now)


# ---------- reading answers ----------


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def _api_status(resp, name: str) -> Check | None:
    """Closed on 404/410, unknown on anything but 200, None (look closer) on 200."""
    code = resp.status_code
    if resp.history:  # the endpoint sent us elsewhere: not a clear answer about this job
        return Check(UNKNOWN, f"HTTP {code} from {name} after a redirect")
    if code in (404, 410):
        return Check(CLOSED, f"HTTP {code} from {name}", evidence=f"HTTP {code} {resp.url}")
    if code != 200:
        return Check(UNKNOWN, f"HTTP {code} from {name}")
    return None


def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


GREENHOUSE_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
)


def check_greenhouse(url, get, board: str = ""):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    seg = _segments(url)
    if parts.hostname in GREENHOUSE_HOSTS and len(seg) >= 3 and seg[1] == "jobs":
        if seg[2].isdigit():
            resp = get(f"https://boards-api.greenhouse.io/v1/boards/{seg[0]}/jobs/{seg[2]}")
            return _api_status(resp, "Greenhouse") or Check(OPEN, "listed on Greenhouse")
    # Firms that show Greenhouse jobs on their own site link with ?gh_jid=<id>, and those
    # pages can break while the job stays open (a real GSA case), so the page is not asked.
    job_id = query.get("gh_jid", [""])[0]
    if parts.hostname in GREENHOUSE_HOSTS and seg == ["embed", "job_app"]:
        job_id = query.get("token", [""])[0]
    if not job_id.isdigit():
        return None
    if board:  # the board the scan found the job on: its job API is a clear answer
        resp = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}")
        return _api_status(resp, "Greenhouse") or Check(OPEN, "listed on Greenhouse")
    # Without the board, only the embed page is left. It loads for an open job, but firms
    # can turn embedding off (Jane Street does), so its 404 proves nothing.
    resp = get(f"https://boards.greenhouse.io/embed/job_app?token={job_id}")
    if resp.status_code == 200:
        return Check(OPEN, "listed on Greenhouse")
    return Check(UNKNOWN, f"Greenhouse embed page answered HTTP {resp.status_code}")


def check_lever(url, get):
    host = urlsplit(url).hostname
    api = {"jobs.lever.co": "api.lever.co", "jobs.eu.lever.co": "api.eu.lever.co"}.get(host)
    seg = _segments(url)
    if not api or len(seg) < 2:
        return None
    resp = get(f"https://{api}/v0/postings/{seg[0]}/{seg[1]}")
    return _api_status(resp, "Lever") or Check(OPEN, "listed on Lever")


def check_ashby(url, get, boards: dict | None = None):
    seg = _segments(url)
    if urlsplit(url).hostname != "jobs.ashbyhq.com" or len(seg) < 2:
        return None
    org, job_id = seg[0], seg[1]
    resp = get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    if resp.status_code != 200:  # a missing board is not proof the job closed
        return Check(UNKNOWN, f"HTTP {resp.status_code} from Ashby")
    data = _json(resp)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return Check(UNKNOWN, "unexpected answer from Ashby")
    if any(job.get("id") == job_id for job in data["jobs"]):
        return Check(OPEN, "listed on Ashby")
    evidence = f"HTTP 200 {resp.url}: {len(data['jobs'])} jobs, none with id {job_id}"
    return Check(CLOSED, f"not on {org}'s Ashby job board", evidence=evidence)


_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def check_workday(url, get):
    host = urlsplit(url).hostname or ""
    seg = _segments(url)
    if seg and _LOCALE.match(seg[0]):
        seg = seg[1:]
    if host.endswith(".myworkdayjobs.com") and len(seg) >= 3 and seg[1] == "job":
        tenant, site, rest = host.split(".")[0], seg[0], seg[2:]
    elif (
        host.endswith(".myworkdaysite.com")
        and len(seg) >= 5
        and seg[0] == "recruiting"
        and seg[3] == "job"
    ):
        tenant, site, rest = seg[1], seg[2], seg[4:]
    else:
        return None
    resp = get(f"https://{host}/wday/cxs/{tenant}/{site}/job/{'/'.join(rest)}")
    found = _api_status(resp, "Workday")
    if found:
        return found
    info = (_json(resp) or {}).get("jobPostingInfo")
    if not isinstance(info, dict):
        return Check(UNKNOWN, "unexpected answer from Workday")
    if info.get("canApply") is False:
        return Check(
            CLOSED, "Workday says applications are closed", evidence=f"canApply false {resp.url}"
        )
    return Check(OPEN, "listed on Workday")


def check_smartrecruiters(url, get):
    seg = _segments(url)
    if urlsplit(url).hostname != "jobs.smartrecruiters.com" or len(seg) < 2:
        return None
    posting = re.match(r"\d+", seg[1])
    if not posting:
        return None
    resp = get(f"https://api.smartrecruiters.com/v1/companies/{seg[0]}/postings/{posting[0]}")
    found = _api_status(resp, "SmartRecruiters")
    if found:
        return found
    if (_json(resp) or {}).get("active") is False:
        return Check(
            CLOSED,
            "SmartRecruiters says the posting is inactive",
            evidence=f"active false {resp.url}",
        )
    return Check(OPEN, "listed on SmartRecruiters")


_TAG = re.compile(r"<[^>]+>")
_HIDDEN = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)


def visible_text(page: str) -> str:
    return " ".join(_TAG.sub(" ", _HIDDEN.sub(" ", page)).lower().split())


def careers_home(original: str, final: str) -> bool:
    """A redirect that lands on a page with no job id: a careers home or search page."""
    a, b = urlsplit(original), urlsplit(final)
    if (a.netloc, a.path.rstrip("/"), a.query) == (b.netloc, b.path.rstrip("/"), b.query):
        return False
    if "error" in parse_qs(b.query):
        return True
    path = b.path.strip("/")
    return not re.search(r"\d", path + "?" + b.query) and path.count("/") <= 1


def check_page(url, get, phrases):
    """A plain page is closed only on an unambiguous answer from the link itself: a 404 or
    410 with no redirect, or a closed phrase in the visible text of a page served without a
    redirect. Anything else that isn't a clean 200 is unknown."""
    resp = get(url)
    code = resp.status_code
    redirected = bool(resp.history)
    if code in (404, 410):
        if redirected:  # e.g. a firm site that moved its job pages: proves nothing
            return Check(UNKNOWN, f"HTTP {code} after a redirect")
        return Check(CLOSED, f"HTTP {code}", evidence=f"HTTP {code} {url}")
    if code != 200:
        return Check(UNKNOWN, f"HTTP {code}")
    if redirected and careers_home(url, resp.url):
        return Check(UNKNOWN, "redirected to a careers home page", CAREERS_REDIRECT)
    text = visible_text(resp.text)
    for phrase in phrases:
        if phrase.lower() in text:
            if redirected:
                return Check(UNKNOWN, f'page says "{phrase}" after a redirect')
            return Check(CLOSED, f'page says "{phrase}"', evidence=f'HTTP 200 {url}: "{phrase}"')
    return Check(OPEN, "page loads")


BOARDS = (check_workday, check_greenhouse, check_lever, check_ashby, check_smartrecruiters)


def check_url(url: str, get, phrases, greenhouse_board: str = "") -> Check:
    """One role's status. Never raises: any failure is unknown."""
    if not url or urlsplit(url).scheme not in ("http", "https"):
        return Check(UNKNOWN, "no usable link")
    try:
        for board in BOARDS:
            if board is check_greenhouse:
                found = board(url, get, greenhouse_board)
            else:
                found = board(url, get)
            if found:
                return found
        return check_page(url, get, phrases)
    except Exception as exc:  # timeouts, connection errors, odd answers
        return Check(UNKNOWN, type(exc).__name__)


# ---------- cache ----------


def _cache_path(root: Path) -> Path:
    return root / "data" / "link_check.json"


def load_cache(root: Path) -> dict:
    path = _cache_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable link-check cache %s: %s", path, exc)
        return {}


def save_cache(root: Path, cache: dict) -> None:
    path = _cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".link_check-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def check_all(
    urls, cfg: dict, root: Path, get=None, now: datetime | None = None, boards: dict | None = None
):
    """Status for every URL, using cached answers younger than cache_hours. `boards` maps a
    URL to the Greenhouse board the scan found it on, when known."""
    boards = boards or {}
    now = now or datetime.now(timezone.utc)
    cache = load_cache(root)
    fresh_after = now - timedelta(hours=cfg["cache_hours"])
    results: dict[str, Check] = {}
    todo = []
    for url in dict.fromkeys(urls):
        hit = cache.get(url)
        try:
            fresh = hit and datetime.fromisoformat(hit["checked"]) > fresh_after
        except (KeyError, TypeError, ValueError):
            fresh = False
        if fresh and hit["status"] == CLOSED and not hit.get("evidence"):
            fresh = False  # a closed answer without evidence is never reused
        if fresh:
            results[url] = Check(
                hit["status"], hit.get("detail", ""), hit.get("flag", ""), hit.get("evidence", "")
            )
        else:
            todo.append(url)

    raw_get = get or http_get(cfg["timeout_seconds"])
    pacer = HostPacer(cfg["per_host_seconds"])

    def paced_get(target: str):
        pacer.wait(urlsplit(target).hostname or "")
        return raw_get(target)

    phrases = cfg["closed_phrases"]
    with ThreadPoolExecutor(max_workers=max(1, int(cfg["concurrency"]))) as pool:
        # Workers only fetch; results come back here and are written by this one thread.
        checked = pool.map(lambda u: check_url(u, paced_get, phrases, boards.get(u, "")), todo)
        for url, found in zip(todo, checked, strict=True):
            results[url] = found
            cache[url] = {
                "status": found.status,
                "detail": found.detail,
                "flag": found.flag,
                "evidence": found.evidence,
                "checked": now.isoformat(),
            }
    if todo:
        try:
            save_cache(root, cache)
        except OSError as exc:
            log.warning("could not save link-check cache: %s", exc)
    return results


# ---------- applying to a scan ----------


@dataclass
class Report:
    open: int = 0
    closed: int = 0
    unknown: int = 0
    too_old: int = 0
    seconds: float = 0.0

    def summary(self) -> str:
        return (
            f"Link check: {self.open} open, {self.closed} closed, {self.unknown} unknown "
            f"({self.too_old} unknown dropped as too old) in {self.seconds:.0f}s"
        )


def _posted(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def apply(result, cfg: dict, root: Path, get=None, today: date | None = None) -> Report:
    """Check result.kept; move closed and too-old unknown roles to result.rejected."""
    start = time.monotonic()
    today = today or date.today()
    boards = {
        job.url: job.extra["greenhouse_board"]
        for job in result.kept
        if job.extra.get("greenhouse_board")
    }
    checks = check_all([job.url for job in result.kept], cfg, root, get, boards=boards)
    report = Report()
    kept = []
    for job in result.kept:
        found = checks.get(job.url) or Check(UNKNOWN, "not checked")
        if found.status == CLOSED and not found.evidence:
            found = Check(UNKNOWN, f"{found.detail} (no evidence)")  # never remove on a guess
        if found.status == CLOSED:
            report.closed += 1
            reason = f"listing closed ({found.detail})"
            try:
                removed.add(root, job.url, job.company, job.title, reason, "link_check")
            except Exception as exc:  # still dropped from this scan; logged for the user
                log.warning("could not record closed role %s: %s", job.url, exc)
            result.rejected.append((job, f"removed: {reason}"))
            continue
        if found.status == OPEN:
            report.open += 1
            kept.append(job)
            continue
        report.unknown += 1
        posted = _posted(job.date_posted)
        limit = cfg["max_unknown_age_days"]
        if posted and (today - posted).days > limit:
            report.too_old += 1
            result.rejected.append(
                (job, f"unverified and posted {posted} (over {limit} days ago): {found.detail}")
            )
            continue
        job.flags.append(NOT_VERIFIED)
        if found.flag:
            job.flags.append(found.flag)
        kept.append(job)
    result.kept = kept
    report.seconds = time.monotonic() - start
    return report
