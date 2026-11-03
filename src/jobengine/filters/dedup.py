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


_TITLE_NOISE = re.compile(r"\b(20\d\d|program|programme|summer|the|of|and|for|in)\b")


def title_key(title: str) -> str:
    """Word set of the title minus years and filler, so "Quant Trading Intern - Summer 2027
    (DV Equities)" and "Quant Trading Intern - DV Equities" match. "Intern" is kept, so a
    full-time "Quantitative Researcher" never merges with "Quantitative Researcher - Intern"."""
    text = re.sub(r"\binternships?\b", "intern", normalize(title))
    words = _TITLE_NOISE.sub(" ", text).split()
    return " ".join(sorted(set(words))) or normalize(title)


def job_key(job: Posting) -> tuple[str, str]:
    return company_key(job.company), title_key(job.title)


def rank(job: Posting) -> int:
    return SOURCE_RANK.get(job.source, 3)


def _merge(keep: Posting, job: Posting) -> None:
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


def dedupe(jobs: list[Posting]) -> list[Posting]:
    jobs = sorted(jobs, key=rank)
    parents = list(range(len(jobs)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    keys: dict[tuple[str, str], int] = {}
    urls: dict[str, int] = {}
    for index, job in enumerate(jobs):
        matches = [keys.setdefault(job_key(job), index)]
        if job.url:
            matches.append(urls.setdefault(job.url, index))
        for match in matches:
            a, b = root(index), root(match)
            parents[max(a, b)] = min(a, b)

    best: dict[int, Posting] = {}
    for index, job in enumerate(jobs):
        group = root(index)
        if group not in best:
            job.sources = [job.source]
            best[group] = job
        else:
            _merge(best[group], job)
    return list(best.values())
