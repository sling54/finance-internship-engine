"""The one data shape every source is converted into."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Posting:
    """A single job posting, normalized across sources."""

    company: str
    title: str
    url: str
    source: str  # e.g. "greenhouse", "simplify", "kadoa"
    locations: list[str] = field(default_factory=list)
    description: str = ""
    date_posted: str = ""  # ISO date, when known
    terms: list[str] = field(default_factory=list)  # e.g. ["Summer 2027"], when the source says
    degrees: list[str] = field(default_factory=list)  # e.g. ["Bachelor's"], when the source says
    is_internship: bool | None = None  # True when the source itself says so
    remote: bool | None = None
    salary: str = ""
    extra: dict = field(default_factory=dict)

    # Filled in by the pipeline
    track: str = ""
    priority: int = 0
    season: str = ""
    flags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def text(self) -> str:
        """Title and description together, for keyword checks."""
        return f"{self.title}\n{self.description}"

    def to_row(self) -> dict:
        row = asdict(self)
        for key in ("locations", "terms", "degrees", "flags", "sources"):
            row[key] = "; ".join(row[key])
        row.pop("description")
        row.pop("extra")
        return row


class Rejected(Exception):
    """Raised by a filter to drop a posting. The message is the logged reason."""
