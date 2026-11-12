"""Feature 4, part 1: work out each role's season, and drop terms that are over or start
after graduation."""

from __future__ import annotations

import re
from datetime import date

from ..models import Posting, Rejected
from ..textutil import normalize

_TERM = re.compile(
    r"\b(summer|fall|autumn|spring|winter)\s*(?:of\s*|term\s*)?'?(20\d\d|\d\d)\b", re.IGNORECASE
)
_SEASON_WORD = re.compile(r"\b(summer|fall|autumn|spring|winter)\b", re.IGNORECASE)
_OFF_CYCLE = re.compile(r"\b(off[- ]cycle|co-?op)\b", re.IGNORECASE)
_PART_TIME = re.compile(
    r"\b(part[- ]time|during the (?:school year|semester|academic year)|"
    r"\d{1,2}\s*(?:-|–|to)\s*\d{1,2}\s*(?:hours|hrs)\s*(?:per|a|/)\s*week|year[- ]round)\b",
    re.IGNORECASE,
)
_NAMES = {
    "summer": "Summer",
    "fall": "Fall",
    "autumn": "Fall",
    "spring": "Spring",
    "winter": "Winter",
}
_MONTH_YEAR = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(20\d\d)\b", re.IGNORECASE
)
_YEAR_FIRST = re.compile(
    r"\b(20\d\d)\b(?![^\n]{0,40}?\b(?:summer|fall|autumn|spring|winter)\s*'?\d)[^\n]{0,40}?"
    r"\b(summer|fall|autumn|spring|winter)\b",
    re.IGNORECASE,
)
_MONTH_START = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:20\d\d\s+)?start",
    re.IGNORECASE,
)

# "Dates: 10 week program, starting June 7th 2027", "start date: January 12, 2027"
_DATED_START = re.compile(
    r"\b(?:start\w*|begin\w*|dates?)\b[^.\n]{0,40}?"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"\d{1,2}(?:st|nd|rd|th)?,?\s+20\d\d",
    re.IGNORECASE,
)
# "you'll spend your summer embedded...", "our Summer Internship Program", "summer interns"
_SEASON_PHRASE = re.compile(
    r"\b(?:(?:your|the|this|our)\s+(summer|fall|autumn|spring|winter)\b(?!\s+(?:of\s+)?20\d\d)"
    r"|(summer|fall|autumn|spring|winter)\s+(?:intern\w*|program\w*|analysts?|cohort|class))",
    re.IGNORECASE,
)


def month_season(month: str) -> str:
    """Season a start month falls in: Jan-Apr Spring, May-Jul Summer, Aug-Nov Fall, Dec Winter."""
    m = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    i = m.index(month.lower()[:3]) + 1
    return "Spring" if i <= 4 else "Summer" if i <= 7 else "Fall" if i <= 11 else "Winter"


# Month each season starts, to compare a term against graduation.
# "Winter 2027" means a Jan-Apr 2027 co-op term, as SimplifyJobs uses it.
_START_MONTH = {"Winter": 1, "Spring": 1, "Summer": 5, "Fall": 8}


def _year(raw: str) -> int:
    return int(raw) if len(raw) == 4 else 2000 + int(raw)


def explicit_terms(job: Posting) -> list[tuple[str, int]]:
    """Season-year pairs stated in the source's term list, the title or the URL."""
    terms = []
    for text in [*job.terms, job.title, _url_words(job.url)]:
        for m in _TERM.finditer(text):
            terms.append((_NAMES[m.group(1).lower()], _year(m.group(2))))
    for m in _YEAR_FIRST.finditer(job.title):  # "2027 Summer Internship"
        terms.append((_NAMES[m.group(2).lower()], int(m.group(1))))
    for m in _MONTH_YEAR.finditer(job.title):
        season, year = month_season(m.group(1)), int(m.group(2))
        if season == "Winter":
            year += 1  # a December start belongs to the next winter term
        terms.append((season, year))
    return terms


def _url_words(url: str) -> str:
    """The URL's path as words, so ".../Computer-Programming-Internship-Summer-2026" reads
    "summer 2026". The query string is left out (tracking ids)."""
    return normalize(url.split("?")[0]) if url else ""


def detect_season(job: Posting) -> str:
    terms = explicit_terms(job)
    if terms:
        return terms[0][0]
    head = f"{job.title}\n{job.description[:1500]}"
    start = _MONTH_START.search(head)
    if start:
        return month_season(start.group(1))
    if _OFF_CYCLE.search(job.title):
        return "Fall"
    if _PART_TIME.search(head):
        return "Part-time"
    m = _TERM.search(head) or _SEASON_WORD.search(job.title)
    if m:
        return _NAMES[m.group(1).lower()]
    # The rest of the description: a dated start, a stated term, or a season phrase.
    text = job.description
    start = _DATED_START.search(text)
    if start:
        return month_season(start.group(1))
    m = _TERM.search(text)
    if m:
        return _NAMES[m.group(1).lower()]
    m = _SEASON_PHRASE.search(text)
    if m:
        return _NAMES[(m.group(1) or m.group(2)).lower()]
    return "Unknown"


def _drop_stale_list_terms(job: Posting) -> list[tuple[str, int]]:
    """Remove source-list terms that had already started when the role was posted; return them."""
    posted = re.match(r"(20\d\d)-(\d\d)", job.date_posted or "")
    if not posted or not job.terms:
        return []
    when = (int(posted.group(1)), int(posted.group(2)))
    kept, stale = [], []
    for text in job.terms:
        found = [(_NAMES[m.group(1).lower()], _year(m.group(2))) for m in _TERM.finditer(text)]
        if found and all((y, _START_MONTH[s]) < when for s, y in found):
            stale += found
        else:
            kept.append(text)
    job.terms = kept
    return stale


class SeasonFilter:
    def __init__(self, profile: dict, today: date | None = None):
        grad = str(profile.get("graduation", "2027-12"))
        self.grad_year, self.grad_month = (int(x) for x in grad.split("-"))
        self.today = today or date.today()

    def __call__(self, job: Posting) -> None:
        stale = _drop_stale_list_terms(job)
        terms = explicit_terms(job)
        if stale and not terms:
            # The list's term is older than the posting itself, so it's a stale label
            # (SimplifyJobs kept "Summer 2026" on Citadel's evergreen intern posts from July
            # 2026). Keep the season, not the year, and ask to check it.
            job.season = stale[0][0]
            job.flags.append("check term")
            return
        if terms:
            valid = [t for t in terms if self._term_ok(*t)]
            if not valid:
                season, year = terms[0]
                if (year, _START_MONTH[season]) < (self.today.year, self.today.month):
                    raise Rejected(f"term already started or over: {season} {year}")
                raise Rejected(f"starts after graduation: {season} {year}")
            job.terms = list(dict.fromkeys(f"{s} {y}" for s, y in valid))
            job.season = valid[0][0]
        else:
            job.season = detect_season(job)
        if job.season == "Unknown":
            job.flags.append("check season")

    def _term_ok(self, season: str, year: int) -> bool:
        start = (year, _START_MONTH[season])
        started = start < (self.today.year, self.today.month)
        after_grad = start > (self.grad_year, self.grad_month)
        return not started and not after_grad
