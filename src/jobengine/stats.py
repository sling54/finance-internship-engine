"""`jobengine stats`: the numbers behind your search, from files the engine already keeps.

- Scans: data/scan_history.json (one line per scan) and data/seen.json (every role ever kept).
- Applications: the tracker's rows and their App Status.
- Outreach: data/outreach.json.

Response rate counts an employer acting on an application (assessment, interview, offer or
rejection); an automatic "we received it" email doesn't count. `--markdown` prints a table to
paste into a README.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

RESPONDED = ("Assessment", "Interview", "Offer", "Rejected")
STATUS_ORDER = (
    "Applied",
    "Confirmed",
    "Assessment",
    "Interview",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Ghosted",
)


@dataclass
class Stats:
    scans: int = 0
    collected: int = 0
    roles_seen: int = 0
    first_scan: str = ""
    applications: int = 0
    statuses: Counter = field(default_factory=Counter)
    first_application: date | None = None
    last_application: date | None = None
    last_4_weeks: int = 0
    outreach: Counter = field(default_factory=Counter)

    @property
    def responded(self) -> int:
        return sum(self.statuses[s] for s in RESPONDED)

    @property
    def interviews(self) -> int:
        # An offer means there was an interview; a rejection may have come after one too,
        # but the tracker only keeps the latest status, so this is a lower bound.
        return self.statuses["Interview"] + self.statuses["Offer"]

    @property
    def outreach_sent(self) -> int:
        return sum(self.outreach[s] for s in ("sent", "replied", "bounced"))

    def rows(self) -> list[tuple[str, str]]:
        def pct(part: int, whole: int) -> str:
            return f"{part} ({part / whole:.0%})" if whole else "0"

        out = []
        if self.scans:
            out += [
                ("Scans run", f"{self.scans} since {self.first_scan}"),
                ("Postings collected", f"{self.collected:,}"),
                ("Roles that passed the filters", f"{self.roles_seen:,}"),
            ]
        out.append(("Applications", str(self.applications)))
        if self.applications:
            span = f"{self.first_application} to {self.last_application}"
            out += [
                ("Applied between", span),
                ("Applications in the last 4 weeks", str(self.last_4_weeks)),
                ("Employer responses", pct(self.responded, self.applications)),
                ("Interviews (incl. offers)", pct(self.interviews, self.applications)),
                ("Offers", str(self.statuses["Offer"])),
            ]
            out += [(f"  {s}", str(self.statuses[s])) for s in STATUS_ORDER if self.statuses[s]]
            out += [(f"  {s}", str(n)) for s, n in self.statuses.items() if s not in STATUS_ORDER]
        if self.outreach:
            out += [
                ("Outreach emails sent", str(self.outreach_sent)),
                ("Outreach replies", pct(self.outreach["replied"], self.outreach_sent)),
                ("Outreach bounces", str(self.outreach["bounced"])),
                (
                    "Outreach drafts waiting",
                    str(self.outreach["drafted"] + self.outreach["approved"]),
                ),
            ]
        return out

    def text(self) -> str:
        width = max(len(k) for k, _ in self.rows())
        return "\n".join(f"{k:<{width}}  {v}" for k, v in self.rows())

    def markdown(self) -> str:
        lines = ["| Metric | Value |", "|---|---|"]
        lines += [f"| {k.strip()} | {v} |" for k, v in self.rows()]
        return "\n".join(lines)


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def compute(root: Path, tracker_path: Path, today: date | None = None) -> Stats:
    today = today or date.today()
    s = Stats()
    data = root / "data"
    history = _json(data / "scan_history.json", [])
    if isinstance(history, list) and history:
        s.scans = len(history)
        s.collected = sum(int(h.get("collected", 0)) for h in history)
        s.first_scan = str(history[0].get("date", ""))
    s.roles_seen = len(_json(data / "seen.json", []))

    if tracker_path.exists():
        from .tracker_sync import Tracker

        rows = Tracker(tracker_path).rows()  # read only; nothing is saved
        s.applications = len(rows)
        s.statuses = Counter(r.status for r in rows)
        days = [r.applied for r in rows if r.applied]
        if days:
            s.first_application, s.last_application = min(days), max(days)
            s.last_4_weeks = sum(d >= today - timedelta(days=28) for d in days)

    outreach = _json(data / "outreach.json", {})
    if isinstance(outreach, dict):
        s.outreach = Counter(e.get("status", "") for e in outreach.get("entries", {}).values())
    return s
