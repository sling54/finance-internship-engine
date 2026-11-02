"""Small text helpers shared by scanners and filters."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n+")


def html_to_text(raw: str | None) -> str:
    """Strip HTML (including entity-escaped HTML, as Greenhouse returns) to plain text."""
    if not raw:
        return ""
    text = html.unescape(html.unescape(raw))
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", text)
    text = _TAG.sub(" ", text)
    text = _SPACE.sub(" ", text)
    return _BLANKS.sub("\n\n", text).strip()


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse spaces. Used for matching and dedup keys."""
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def epoch_to_date(value: float | int | None, millis: bool = False) -> str:
    if not value:
        return ""
    seconds = value / 1000 if millis else value
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()


def iso_date(value: str | None) -> str:
    return (value or "")[:10]


def phrase_pattern(phrases: list[str]) -> re.Pattern:
    """One regex matching any phrase as whole words, case-insensitive."""
    parts = sorted((re.escape(p.lower()) for p in phrases), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])", re.IGNORECASE)
