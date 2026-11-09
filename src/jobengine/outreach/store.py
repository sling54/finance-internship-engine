"""The outreach log, data/outreach.json: every contact drafted, approved, sent or answered.

It is the source of truth; the tracker's Outreach sheet is a copy rebuilt from it.
Contacts are keyed by email address plus company. Roles that got fewer contacts than wanted
are kept under "roles" so they aren't looked up again every run.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..filters.dedup import company_key
from ..tracker_sync import same_role

# drafted -> approved -> sent -> replied / bounced; or skipped / deleted before sending.
STATUSES = ("drafted", "approved", "sent", "replied", "bounced", "skipped", "deleted")
ACTIVE = ("drafted", "approved", "sent", "replied", "bounced")


@dataclass
class Entry:
    date: str
    company: str
    role: str
    contact: str
    first_name: str
    position: str
    email: str
    kind: str  # recruiter, team or other
    template: str
    status: str = "drafted"
    draft_id: str = ""
    message_id: str = ""
    thread_id: str = ""
    sent_at: str = ""
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.email.lower()}|{company_key(self.company)}"


@dataclass
class Log:
    path: Path
    entries: dict[str, Entry] = field(default_factory=dict)
    roles: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> Log:
        path = root / "data" / "outreach.json"
        if not path.exists():
            return cls(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {k: Entry(**v) for k, v in data.get("entries", {}).items()}
        return cls(path, entries, data.get("roles", {}))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": {k: asdict(e) for k, e in self.entries.items()}, "roles": self.roles}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)  # never leave a half-written log

    def add(self, entry: Entry) -> None:
        self.entries[entry.key] = entry

    def has_role(self, company: str, role: str) -> bool:
        """True once this application has outreach logged (contacts, or a short-lookup note)."""
        if role_key(company, role) in self.roles:
            return True
        return any(
            company_key(e.company) == company_key(company) and same_role(e.role, role)
            for e in self.entries.values()
        )

    def company_count(self, company: str) -> int:
        return sum(
            1
            for e in self.entries.values()
            if company_key(e.company) == company_key(company) and e.status in ACTIVE
        )

    def contacted_recently(self, email: str, days: int, today: date) -> bool:
        cutoff = today - timedelta(days=days)
        for e in self.entries.values():
            if e.email.lower() != email.lower() or e.status not in ACTIVE:
                continue
            when = e.sent_at[:10] or e.date
            if date.fromisoformat(when) >= cutoff:
                return True
        return False

    def sent_since(self, moment: datetime) -> int:
        return sum(
            1
            for e in self.entries.values()
            if e.sent_at and datetime.fromisoformat(e.sent_at) >= moment
        )


def role_key(company: str, role: str) -> str:
    return f"{company_key(company)}|{role.strip().lower()}"
