"""Roles removed from consideration, shared by every part of the engine.

Anything that decides a role should go away (you, a link checker, an application
agent) records it here, and the scan and results page leave it out until it is
restored. Entries are keyed by the role's exact URL and kept in data/removed.json,
which is git-ignored like the rest of data/.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

REMOVED_BY = ("user", "link_check", "apply_agent")

log = logging.getLogger(__name__)


class UnreadableRemovedFile(Exception):
    """data/removed.json is damaged and no copy of it could be saved, so it must not be
    overwritten."""


def path(root: Path) -> Path:
    return root / "data" / "removed.json"


def _parse(raw: bytes) -> dict[str, dict]:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError("expected an object of entries keyed by URL")
    return data


def _keep_copy(file: Path, raw: bytes) -> Path:
    """Save the damaged file beside it, once per distinct content."""
    copy = file.with_name(f"{file.name}.unreadable-{hashlib.sha256(raw).hexdigest()[:12]}")
    if not copy.exists():
        shutil.copy2(file, copy)
    return copy


def _read(root: Path, for_write: bool) -> dict[str, dict]:
    file = path(root)
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        return _parse(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        problem = exc
    try:
        copy = _keep_copy(file, raw)
    except OSError as exc:
        log.warning(
            "%s can't be read (%s) and no copy could be saved (%s). Roles removed earlier "
            "will show again, and nothing will be written to that file until it is fixed.",
            file,
            problem,
            exc,
        )
        if for_write:
            raise UnreadableRemovedFile(str(file)) from exc
        return {}
    log.warning(
        "%s can't be read (%s). A copy was saved as %s. Roles removed earlier will show "
        "again until you fix the file or copy its entries back.",
        file,
        problem,
        copy.name,
    )
    return {}


def load(root: Path) -> dict[str, dict]:
    """Every entry, keyed by URL. A damaged file is copied aside and read as empty."""
    return _read(root, for_write=False)


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
    entries = _read(root, for_write=True)  # a damaged file is copied aside before any write
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
    entries = _read(root, for_write=True)
    entry = entries.pop(url, None)
    if entry is not None:
        _save(root, entries)
    return entry
