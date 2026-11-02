"""Feature 3 hard filters: degree level and graduation window."""

from __future__ import annotations

import re

from ..models import Posting, Rejected

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
SEASON_MONTH = {"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 12}

_DEGREE_NAMES = {"bachelors": "bachelor", "masters": "master", "phd": "phd"}

# A month/season (optional) followed by a year.
_DATE = r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|spring|summer|fall|autumn|winter)[a-z]*\.?,?\s+)?(20\d\d)"
_GRAD_CONTEXT = re.compile(
    r"(?:graduat\w*|class of|degree completion|completing (?:your|their|a) degree)[^.\n]{0,60}?"
    + _DATE
    + r"(?:\s*(?:-|–|to|and|or|through)\s*"
    + _DATE
    + r")?",
    re.IGNORECASE,
)
_GRADS_BEFORE = re.compile(
    _DATE + r"(?:\s*(?:-|–|to|and|or|through)\s*" + _DATE + r")?\s+(?:graduates|grads)\b",
    re.IGNORECASE,
)


EARLIEST, LATEST = (1900, 1), (2100, 12)
# "graduate BY August 2028" sets a deadline; "May 2028 OR LATER" sets a floor.
_DEADLINE = re.compile(
    r"\b(by|before|no later than|prior to|on or before|not later than)\s*$", re.IGNORECASE
)
_FLOOR = re.compile(
    r"^\s*(or later|or after|and later|and after|or beyond|onwards?)\b", re.IGNORECASE
)
# Mismatches closer than this to the window are kept with a flag; farther ones are dropped.
SOFT_MONTHS = 12

Month = tuple[int, int]


def parse_month(value: str) -> Month:
    """'2027-12' -> (2027, 12)."""
    year, month = value.split("-")
    return int(year), int(month)


def _as_range(word: str | None, year: str) -> tuple[Month, Month]:
    y = int(year)
    if not word:
        return (y, 1), (y, 12)  # year only: the whole year
    w = word.lower()[:3] if word.lower()[:3] in MONTHS else word.lower()
    m = MONTHS.get(w) or SEASON_MONTH.get(w, 12)
    return (y, m), (y, m)


def grad_dates(text: str) -> list[tuple[Month, Month]]:
    """Every graduation date the posting accepts, as an (earliest, latest) range.
    "Class of 2028" -> the year; "between Dec 2027 and Jun 2028" -> that range;
    "by Aug 2028" -> anything up to Aug 2028; "May 2028 or later" -> May 2028 onward."""
    found = []
    for pattern in (_GRAD_CONTEXT, _GRADS_BEFORE):
        for m in pattern.finditer(text):
            w1, y1, w2, y2 = m.groups()
            start, end = _as_range(w1, y1)
            if y2:
                _, end = _as_range(w2, y2)
            else:
                # text between "graduate" and the date, e.g. "graduate by "
                before = text[m.start() : m.start(1) if w1 else m.start(2)]
                if _DEADLINE.search(before):
                    start = EARLIEST
                elif _FLOOR.search(text[m.end() : m.end() + 20]):
                    end = LATEST
            found.append((start, end))
    return found


def _months_apart(a: tuple[Month, Month], b: tuple[Month, Month]) -> int:
    """Gap in months between two ranges (0 if they overlap)."""

    def idx(m: Month) -> int:
        return m[0] * 12 + m[1]

    if idx(a[1]) < idx(b[0]):
        return idx(b[0]) - idx(a[1])
    if idx(b[1]) < idx(a[0]):
        return idx(a[0]) - idx(b[1])
    return 0


def _show(r: tuple[Month, Month]) -> str:
    start, end = r
    if start == EARLIEST:
        return f"by {end[1]:02d}/{end[0]}"
    if end == LATEST:
        return f"{start[1]:02d}/{start[0]} or later"
    if start == end:
        return f"{start[1]:02d}/{start[0]}"
    if start[0] == end[0] and (start[1], end[1]) == (1, 12):
        return str(start[0])
    return f"{start[1]:02d}/{start[0]}-{end[1]:02d}/{end[0]}"


class EligibilityFilter:
    def __init__(self, profile: dict):
        self.degree = profile.get("degree", "bachelors")
        window = profile.get("grad_window") or {}
        self.window = (
            parse_month(str(window.get("start", "2027-12"))),
            parse_month(str(window.get("end", "2028-06"))),
        )

    def __call__(self, job: Posting) -> None:
        if job.degrees:
            wanted = _DEGREE_NAMES.get(self.degree, self.degree)
            if not any(wanted in d.lower() for d in job.degrees):
                raise Rejected(f"degree: {', '.join(job.degrees)} only")

        dates = grad_dates(job.text())
        if not dates:
            return  # no date named: passes
        gaps = [_months_apart(d, self.window) for d in dates]
        if min(gaps) == 0:
            return
        shown = ", ".join(dict.fromkeys(_show(d) for d in dates[:3]))
        if min(gaps) < SOFT_MONTHS:
            job.flags.append(f"check grad date ({shown})")  # close call: keep, let a human judge
            return
        raise Rejected(f"graduation window mismatch: asks for {shown}")
