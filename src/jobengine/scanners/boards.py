"""Source 5: job boards (LinkedIn, Indeed, ZipRecruiter, Google Jobs) through JobSpy.

JobSpy reads public listings without logging in. LinkedIn rate-limits logged-out access,
so each site is fetched separately and a failing site is skipped, not fatal.
Install with: pip install -e ".[jobspy]"
"""

from __future__ import annotations

import logging
import math

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


def scan(settings: dict) -> list[Posting]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.warning('JobSpy not installed; run: pip install -e ".[jobspy]"')
        return []

    jobs: list[Posting] = []
    for run in settings.get("passes", []):
        for site in settings.get("sites", []):
            for term in run.get("terms", []):
                try:
                    df = scrape_jobs(
                        site_name=[site],
                        search_term=term,
                        google_search_term=f"{term} near {run['location']} since yesterday",
                        location=run["location"],
                        distance=run.get("distance_miles", 50),
                        job_type="internship",
                        hours_old=settings.get("hours_old", 24),
                        results_wanted=run.get("results_wanted", 30),
                        country_indeed="USA",
                    )
                    found = parse_jobspy_records(df.to_dict("records"))
                    log.info("jobspy %s '%s': %d postings", site, term, len(found))
                    jobs.extend(found)
                except Exception as exc:
                    log.warning("jobspy %s '%s' failed, skipping: %s", site, term, exc)
                    if site == "linkedin":
                        break  # LinkedIn blocked us; the alert emails still cover it
    return jobs
