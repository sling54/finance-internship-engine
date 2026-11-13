"""Source 5: job boards (LinkedIn, Indeed, ZipRecruiter, Google Jobs) through JobSpy.

JobSpy reads public listings without logging in. LinkedIn rate-limits logged-out access,
so each site is fetched separately and a failing site is skipped, not fatal.
Install with: pip install -e ".[jobspy]"
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time

from ..models import Posting

log = logging.getLogger(__name__)


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def parse_jobspy_records(records: list[dict]) -> list[Posting]:
    out = []
    for row in records:
        lo, hi = _clean(row.get("min_amount")), _clean(row.get("max_amount"))
        salary = f"{lo}-{hi} {_clean(row.get('interval'))}".strip(" -") if lo or hi else ""
        job_type = _clean(row.get("job_type")).lower()
        out.append(
            Posting(
                company=_clean(row.get("company")),
                title=_clean(row.get("title")),
                url=_clean(row.get("job_url_direct")) or _clean(row.get("job_url")),
                source=f"jobspy:{_clean(row.get('site'))}",
                locations=[_clean(row.get("location"))] if _clean(row.get("location")) else [],
                description=_clean(row.get("description")),
                date_posted=_clean(row.get("date_posted"))[:10],
                is_internship=True if "intern" in job_type else None,
                remote=True if row.get("is_remote") is True else None,
                salary=salary,
            )
        )
    return out


def _unwrap_bigint(value):
    """ZipRecruiter now writes numbers as "$n<digits>" (a serialized BigInt); return the int."""
    if isinstance(value, str) and value.startswith("$n") and value[2:].isdigit():
        return int(value[2:])
    return value


def fix_zip_posted_date(raw):
    """Turn ZipRecruiter's "$n1794511320" posted time back into seconds JobSpy can read.

    Since Nov 13 JobSpy 1.4.1 skipped every ZipRecruiter job with "datetime_from_date_parsing"."""
    if not isinstance(raw, dict) or not isinstance(raw.get("status"), dict):
        return raw
    status = raw["status"]
    posted = status.get("postedAtUtc")
    if isinstance(posted, dict) and "seconds" in posted:
        fixed = {**posted, "seconds": _unwrap_bigint(posted["seconds"])}
    else:
        fixed = _unwrap_bigint(posted)
    if fixed == posted:
        return raw
    return {**raw, "status": {**status, "postedAtUtc": fixed}}


def _patch_ziprecruiter() -> None:
    """Wrap JobSpy's ZipRecruiter job parser so it reads the new posted-time format."""
    try:
        from jobspy.ziprecruiter import ZipRecruiter
    except Exception:  # JobSpy missing or laid out differently: nothing to patch
        return
    original = getattr(ZipRecruiter, "_process_job", None)
    if original is None or getattr(original, "_jobengine_fixed", False):
        return

    def process_job(self, raw):
        return original(self, fix_zip_posted_date(raw))

    process_job._jobengine_fixed = True
    ZipRecruiter._process_job = process_job


class _BlockWatcher(logging.Handler):
    """JobSpy logs a 429 instead of raising it. Watch its log to know when a site blocks us."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.blocked = False

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage().lower()
        if "429" in msg or "blocked" in msg or "timed out" in msg:
            self.blocked = True


class CallTimeout(Exception):
    pass


def _with_deadline(seconds: float, fn, **kwargs):
    """Run fn in a daemon thread; raise CallTimeout if it hasn't finished in time.

    A daemon thread, unlike ThreadPoolExecutor, doesn't hold up exit while a call hangs."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn(**kwargs)
        except Exception as exc:  # handed back to the caller below
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise CallTimeout(f"no answer in {seconds}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


# Sites where JobSpy can't combine hours_old with job_type; the search terms say "intern" instead.
_NO_JOB_TYPE = {"linkedin", "indeed"}


def scan(settings: dict, pause_seconds: tuple[float, float] = (3.0, 7.0)) -> list[Posting]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning('JobSpy not installed; run: pip install -e ".[jobspy]"')
        return []
    _patch_ziprecruiter()

    watcher = _BlockWatcher()
    logging.getLogger("jobspy").addHandler(watcher)
    blocked: set[str] = set()
    jobs: list[Posting] = []
    try:
        for run in settings.get("passes", []):
            for site in settings.get("sites", []):
                for term in run.get("terms", []):
                    if site in blocked:
                        break
                    watcher.blocked = False
                    try:
                        # JobSpy has no overall timeout: on Nov 9 one ZipRecruiter call hung
                        # 12 minutes. Give each call a deadline and skip the site when it's hit.
                        df = _with_deadline(
                            settings.get("call_timeout_seconds", 120),
                            scrape_jobs,
                            site_name=[site],
                            search_term=term,
                            google_search_term=f"{term} near {run['location']} since yesterday",
                            location=run["location"],
                            distance=run.get("distance_miles", 50),
                            job_type=None if site in _NO_JOB_TYPE else "internship",
                            hours_old=settings.get("hours_old", 24),
                            results_wanted=run.get("results_wanted", 30),
                            country_indeed="USA",
                        )
                        found = parse_jobspy_records(df.to_dict("records"))
                        log.info("jobspy %s '%s': %d postings", site, term, len(found))
                        jobs.extend(found)
                    except CallTimeout:
                        log.warning("jobspy %s '%s' took too long; giving up on it", site, term)
                        watcher.blocked = True
                    except Exception as exc:
                        log.warning("jobspy %s '%s' failed: %s", site, term, exc)
                        watcher.blocked = True
                    if watcher.blocked:
                        # Stop asking: more requests only extend the block. Alert emails cover it.
                        log.warning("jobspy: %s is blocking requests; skipping it this run", site)
                        blocked.add(site)
                    elif site == "linkedin":
                        time.sleep(random.uniform(*pause_seconds))  # space out LinkedIn calls
    finally:
        logging.getLogger("jobspy").removeHandler(watcher)
    return jobs
