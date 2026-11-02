"""Merge the same job found by several sources into one row."""

from __future__ import annotations

import re

from ..models import Posting
from ..textutil import normalize

# Lower number = more trusted. Firm career pages beat lists, lists beat boards and emails.
SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "kadoa": 1, "simplify": 2}
_COMPANY_NOISE = re.compile(
    r"\b(the|inc|llc|lp|llp|ltd|plc|group|capital|trading|securities|financial|holdings|"
    r"management|partners|company|co|corp|corporation|technologies|markets)\b"
)


def company_key(name: str) -> str:
    key = _COMPANY_NOISE.sub(" ", normalize(name))
    return " ".join(key.split()) or normalize(name)


_TITLE_NOISE = re.compile(
    r"\b(20\d\d|intern|internship|internships|program|programme|summer|the|of|and|for|in)\b"
)


def title_key(title: str) -> str:
    """Word set of the title minus years and filler, so "Quant Trading Intern - Summer 2027
    (DV Equities)" and "Quant Trading Intern - DV Equities" match."""
    words = _TITLE_NOISE.sub(" ", normalize(title)).split()
    return " ".join(sorted(set(words))) or normalize(title)


def job_key(job: Posting) -> tuple[str, str]:
    return company_key(job.company), title_key(job.title)


def rank(job: Posting) -> int:
    return SOURCE_RANK.get(job.source, 3)


def dedupe(jobs: list[Posting]) -> list[Posting]:
    best: dict[tuple[str, str], Posting] = {}
    for job in sorted(jobs, key=rank):
        key = job_key(job)
        if key not in best:
            job.sources = [job.source]
            best[key] = job
            continue
        keep = best[key]
        if job.source not in keep.sources:
            keep.sources.append(job.source)
        for place in job.locations:
            if place not in keep.locations:
                keep.locations.append(place)
        for attr in ("description", "date_posted", "salary"):
            if not getattr(keep, attr) and getattr(job, attr):
                setattr(keep, attr, getattr(job, attr))
        keep.terms = keep.terms or job.terms
        keep.degrees = keep.degrees or job.degrees
        if keep.is_internship is None:
            keep.is_internship = job.is_internship
    return list(best.values())
