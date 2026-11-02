"""Sources 3 and 4: curated internship lists published on GitHub (SimplifyJobs and Kadoa)."""

from __future__ import annotations

from ..http import get_json
from ..models import Posting
from ..textutil import epoch_to_date


def parse_simplify(data: list, categories: list[str] | None = None) -> list[Posting]:
    wanted = {c.lower() for c in categories or []}
    out = []
    for job in data:
        if not job.get("active") or job.get("is_visible") is False:
            continue
        if wanted and (job.get("category") or "").lower() not in wanted:
            continue
        out.append(
            Posting(
                company=job.get("company_name", "").strip(),
                title=job.get("title", "").strip(),
                url=job.get("url", ""),
                source="simplify",
                locations=job.get("locations") or [],
                date_posted=epoch_to_date(job.get("date_posted")),
                terms=[t for t in job.get("terms") or [] if t != "N/A"],
                degrees=job.get("degrees") or [],
                is_internship=True,  # the whole list is internships
                extra={"category": job.get("category"), "sponsorship": job.get("sponsorship")},
            )
        )
    return out


_KADOA_DEGREES = {
    "bachelors": ["Bachelor's"],
    "masters": ["Master's"],
    "phd_required": ["PhD"],
}


def parse_kadoa(data: list) -> list[Posting]:
    out = []
    for job in data:
        is_intern = job.get("seniorityLevel") == "intern" or job.get("jobType") == "INTERN"
        if not is_intern:
            continue  # Kadoa is mostly full-time roles; keep only internships
        salary = job.get("salary")
        out.append(
            Posting(
                company=job.get("firmName", "").strip(),
                title=job.get("jobTitle", "").strip(),
                url=job.get("applyUrl") or job.get("url", ""),
                source="kadoa",
                locations=job.get("locations") or [],
                date_posted=job.get("datePosted") or "",
                degrees=_KADOA_DEGREES.get(job.get("educationRequirement") or "", []),
                is_internship=True,
                remote=True if job.get("workMode") == "remote" else None,
                salary=str(salary) if salary else "",
                extra={"role_category": job.get("roleCategory"), "firm_type": job.get("firmType")},
            )
        )
    return out


def scan_simplify(settings: dict) -> list[Posting]:
    return parse_simplify(get_json(settings["url"]), settings.get("categories"))


def scan_kadoa(settings: dict) -> list[Posting]:
    return parse_kadoa(get_json(settings["url"]))
