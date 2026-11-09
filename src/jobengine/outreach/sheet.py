"""The tracker's Outreach sheet: one row per contact, rebuilt from data/outreach.json.

Rows are matched by Email plus Company, so the sheet can be re-synced any time: a run that
found the tracker open in Excel leaves the sheet alone, and the next run adds what's missing.
"""

from __future__ import annotations

from datetime import date

from ..filters.dedup import company_key
from .store import Entry

SHEET = "Outreach"
HEADERS = ["Date", "Company", "Role", "Contact", "Position", "Email", "Status"]


def sync_sheet(tracker, entries: list[Entry]) -> int:
    """Add or update a row per entry. Returns how many rows changed."""
    wb = tracker.wb
    if SHEET not in wb.sheetnames:
        ws = wb.create_sheet(SHEET)
        ws.append(HEADERS)
    ws = wb[SHEET]
    where = {}
    for r in range(2, ws.max_row + 1):
        email, company = ws.cell(r, 6).value, ws.cell(r, 2).value
        if email:
            where[(str(email).lower(), company_key(str(company or "")))] = r
    changed = 0
    for e in entries:
        values = [
            date.fromisoformat(e.date),
            e.company,
            e.role,
            e.contact,
            e.position,
            e.email,
            e.status.title(),
        ]
        key = (e.email.lower(), company_key(e.company))
        row = where.get(key)
        if row is None:
            ws.append(values)
            row = where[key] = ws.max_row
            for c in (2, 3):  # posting text stays text, even if it starts with "="
                ws.cell(row, c).data_type = "s"
            changed += 1
        elif ws.cell(row, 7).value != values[6]:
            ws.cell(row, 7, values[6])
            changed += 1
    return changed
