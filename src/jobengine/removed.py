"""Roles removed from consideration, shared by every part of the engine.

Anything that decides a role should go away (you, a link checker, an application
agent) records it here, and the scan and results page leave it out until it is
restored. Entries are keyed by the role's exact URL and kept in data/removed.json,
which is git-ignored like the rest of data/.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

REMOVED_BY = ("user", "link_check", "apply_agent")


def path(root: Path) -> Path:
    return root / "data" / "removed.json"


def load(root: Path) -> dict[str, dict]:
    """Every entry, keyed by URL."""
    file = path(root)
    if not file.exists():
        return {}
    return json.loads(file.read_text(encoding="utf-8"))


def _save(root: Path, entries: dict[str, dict]) -> None:
    file = path(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    # Write a temporary file and swap it in, so a crash never leaves half a file.
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".removed-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def contains(root: Path, url: str) -> bool:
    return url in load(root)


def add(
    root: Path,
    url: str,
    company: str,
    title: str,
    reason: str,
    by: str,
    when: date | None = None,
) -> bool:
    """Remove a role. Returns False if its URL was already listed (the first entry is kept)."""
    if not url:
        raise ValueError("a removed role needs its URL")
    if by not in REMOVED_BY:
        raise ValueError(f"by must be one of {', '.join(REMOVED_BY)}")
    if not reason:
        raise ValueError("a removed role needs a reason")
    entries = load(root)
    if url in entries:
        return False
    entries[url] = {
        "company": company,
        "title": title,
        "reason": reason,
        "date": (when or date.today()).isoformat(),
        "by": by,
    }
    _save(root, entries)
    return True


def remove(root: Path, url: str) -> dict | None:
    """Restore a role. Returns its entry, or None if the URL wasn't listed."""
    entries = load(root)
    entry = entries.pop(url, None)
    if entry is not None:
        _save(root, entries)
    return entry
