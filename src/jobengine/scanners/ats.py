"""Source 1: firm career boards on Greenhouse, Lever and Ashby, through their public job APIs."""

from __future__ import annotations

import logging

from ..http import get_json, session
from ..models import Posting
from ..textutil import epoch_to_date, html_to_text, iso_date

log = logging.getLogger(__name__)

GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_URL = "https://api.lever.co/v0/postings/{token}"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def parse_greenhouse(company: str, data: dict) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        location = (job.get("location") or {}).get("name", "")
        out.append(
            Posting(
                company=company,
                title=job.get("title", "").strip(),
                url=job.get("absolute_url", ""),
                source="greenhouse",
                locations=[location] if location else [],
                description=html_to_text(job.get("content")),
                date_posted=iso_date(job.get("first_published") or job.get("updated_at")),
            )
        )
    return out


def parse_lever(company: str, data: list) -> list[Posting]:
    out = []
    for job in data:
        cats = job.get("categories") or {}
        locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        commitment = (cats.get("commitment") or "").lower()
        out.append(
            Posting(
                company=company,
                title=job.get("text", "").strip(),
                url=job.get("hostedUrl", ""),
                source="lever",
                locations=locations,
                description=job.get("descriptionPlain") or html_to_text(job.get("description")),
                date_posted=epoch_to_date(job.get("createdAt"), millis=True),
                is_internship=True if "intern" in commitment else None,
                remote=True if job.get("workplaceType") == "remote" else None,
            )
        )
    return out


def parse_ashby(company: str, data: dict) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        locations = [job.get("location", "")] + [
            loc.get("location", "") for loc in job.get("secondaryLocations") or []
        ]
        out.append(
            Posting(
                company=company,
                title=job.get("title", "").strip(),
                url=job.get("jobUrl", ""),
                source="ashby",
                locations=[loc for loc in locations if loc],
                description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                date_posted=iso_date(job.get("publishedAt")),
                is_internship=True if job.get("employmentType") == "Intern" else None,
                remote=bool(job.get("isRemote")) or None,
            )
        )
    return out


def scan(firms: dict) -> list[Posting]:
    """Fetch every board in firms.yml. A firm that fails is logged and skipped."""
    sess = session()
    jobs: list[Posting] = []
    boards = [
        ("greenhouse", GREENHOUSE_URL, {"content": "true"}, parse_greenhouse),
        ("lever", LEVER_URL, {"mode": "json"}, parse_lever),
        ("ashby", ASHBY_URL, {}, parse_ashby),
    ]
    for kind, url, params, parser in boards:
        for company, token in (firms.get(kind) or {}).items():
            try:
                data = get_json(url.format(token=token), sess, **params)
                found = parser(company, data)
                if kind == "greenhouse":  # lets the link check ask this board about the job
                    for job in found:
                        job.extra["greenhouse_board"] = token
                log.info("%s/%s: %d postings", kind, token, len(found))
                jobs.extend(found)
            except Exception as exc:  # one bad board must not stop the scan
                log.warning("%s/%s failed: %s", kind, token, exc)
    return jobs
