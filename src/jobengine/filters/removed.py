"""Drop roles listed in data/removed.json (see jobengine.removed)."""

from __future__ import annotations

from ..models import Posting, Rejected


class RemovedFilter:
    def __init__(self, entries: dict[str, dict]):
        self.entries = entries

    def __call__(self, job: Posting) -> None:
        entry = self.entries.get(job.url)
        if entry is not None:
            raise Rejected(f"removed: {entry['reason']}")
