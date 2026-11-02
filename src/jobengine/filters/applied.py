"""Drop roles already in the application tracker spreadsheet."""

from __future__ import annotations

import logging
from pathlib import Path

from ..models import Posting, Rejected
from ..textutil import normalize
from .dedup import company_key, title_key

log = logging.getLogger(__name__)

COMPANY_HEADERS = ("company", "firm", "employer", "organization")
TITLE_HEADERS = ("position", "role", "title", "job")


def read_tracker(path: Path) -> set[tuple[str, str]]:
    """(company key, title key) for every row of every sheet that has company + title columns."""
    if not path.exists():
        return set()
    from openpyxl import load_workbook

    seen: set[tuple[str, str]] = set()
    wb = load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        for header in rows:  # first row that has both columns is the header
            names = [normalize(str(h or "")) for h in header]
            c = next((i for i, n in enumerate(names) if n.startswith(COMPANY_HEADERS)), None)
            t = next((i for i, n in enumerate(names) if n.startswith(TITLE_HEADERS)), None)
            if c is not None and t is not None:
                break
        else:
            continue
        for row in rows:
            if row and len(row) > max(c, t) and row[c] and row[t]:
                seen.add((company_key(str(row[c])), title_key(str(row[t]))))
    log.info("tracker: %d applications loaded", len(seen))
    return seen


class AppliedFilter:
    def __init__(self, applied: set[tuple[str, str]]):
        self.applied = applied

    def __call__(self, job: Posting) -> None:
        if (company_key(job.company), title_key(job.title)) in self.applied:
            raise Rejected("already applied (in tracker)")
