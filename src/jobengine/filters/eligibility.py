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


def parse_month(value: str) -> tuple[int, int]:
    """'2027-12' -> (2027, 12)."""
    year, month = value.split("-")
    return int(year), int(month)


def _as_range(word: str | None, year: str, is_end: bool) -> tuple[tuple[int, int], tuple[int, int]]:
    y = int(year)
    if not word:
        return (y, 1), (y, 12)  # year only: the whole year
    w = word.lower()[:3] if word.lower()[:3] in MONTHS else word.lower()
    m = MONTHS.get(w) or SEASON_MONTH.get(w, 12)
    return (y, m), (y, m)


def grad_dates(text: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Every graduation date or range the posting mentions, as (start, end) month pairs."""
    found = []
    for pattern in (_GRAD_CONTEXT, _GRADS_BEFORE):
        for m in pattern.finditer(text):
            w1, y1, w2, y2 = m.groups()
            start, end = _as_range(w1, y1, False)
            if y2:
                _, end = _as_range(w2, y2, True)
            found.append((start, end))
    return found


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
        lo, hi = self.window
        if any(start <= hi and end >= lo for start, end in dates):
            return
        shown = ", ".join(f"{s[1]:02d}/{s[0]}" if s == e else f"{s[0]}" for s, e in dates[:3])
        raise Rejected(f"graduation window mismatch: asks for {shown}")
