"""Collect -> dedupe -> filter -> write CSVs. Every dropped role keeps its reason."""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import ROOT, Config
from .filters.applied import AppliedFilter, read_tracker
from .filters.dedup import dedupe, job_key
from .filters.eligibility import EligibilityFilter
from .filters.location import LocationFilter
from .filters.season import SeasonFilter
from .filters.titles import TitleFilter
from .models import Posting, Rejected
from .scanners import alerts, ats, boards, lists
from .zone import DriveZone

log = logging.getLogger(__name__)


@dataclass
class Result:
    kept: list[Posting] = field(default_factory=list)
    rejected: list[tuple[Posting, str]] = field(default_factory=list)
    collected: Counter = field(default_factory=Counter)

    def summary(self) -> str:
        reasons = Counter(r.split(":")[0] for _, r in self.rejected)
        by_source = ", ".join(f"{k} {v}" for k, v in self.collected.most_common())
        lines = [
            f"Collected: {sum(self.collected.values())} ({by_source})",
            f"Kept: {len(self.kept)}   Dropped: {len(self.rejected)}",
        ]
        lines += [f"  - {reason}: {n}" for reason, n in reasons.most_common(8)]
        return "\n".join(lines)


def collect(cfg: Config, only: set[str] | None = None, root: Path = ROOT) -> list[Posting]:
    src = cfg.sources
    steps = {
        "ats": lambda: ats.scan(cfg.firms),
        "simplify": lambda: lists.scan_simplify(src["simplify"]),
        "kadoa": lambda: lists.scan_kadoa(src["kadoa"]),
        "jobspy": lambda: boards.scan(src["jobspy"]),
        "alert_emails": lambda: alerts.scan(src["alert_emails"], root),
    }
    jobs: list[Posting] = []
    for name, run in steps.items():
        enabled = src.get(name, {}).get("enabled", False)
        if (only and name not in only) or (not only and not enabled):
            continue
        try:
            found = run()
            log.info("%s: %d postings", name, len(found))
            jobs.extend(found)
        except Exception as exc:
            log.warning("source %s failed: %s", name, exc)
    return jobs


def build_filters(cfg: Config, root: Path = ROOT, zone: DriveZone | None = None):
    titles = TitleFilter(cfg.titles)
    tracker = root / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
    return [
        ("title", titles),
        ("eligibility", EligibilityFilter(cfg.profile)),
        ("season", SeasonFilter(cfg.profile)),
        ("location", LocationFilter(cfg.sources, titles.location_rule, zone)),
        ("applied", AppliedFilter(read_tracker(tracker))),
    ]


def run_filters(jobs: list[Posting], filters) -> Result:
    result = Result()
    result.collected.update(j.source for j in jobs)
    for job in dedupe(jobs):
        try:
            for _, check in filters:
                check(job)
        except Rejected as why:
            result.rejected.append((job, str(why)))
            continue
        result.kept.append(job)
    result.kept.sort(key=lambda j: j.date_posted, reverse=True)
    result.kept.sort(key=lambda j: j.priority)  # stable: priority first, newest within it
    return result


def mark_new(kept: list[Posting], seen_path: Path) -> None:
    """Flag roles not seen on an earlier run, and remember them for next time."""
    seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
    for job in kept:
        key = "|".join(job_key(job))
        if key not in seen:
            job.flags.insert(0, "new")
            seen.add(key)
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(sorted(seen)))


COLUMNS = [
    "priority",
    "track",
    "company",
    "title",
    "season",
    "locations",
    "flags",
    "date_posted",
    "salary",
    "url",
    "sources",
    "terms",
]


def write_csv(path: Path, jobs: list[Posting], reasons: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = COLUMNS + (["reason"] if reasons is not None else [])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for i, job in enumerate(jobs):
            row = job.to_row()
            if reasons is not None:
                row["reason"] = reasons[i]
            writer.writerow(row)


def run(cfg: Config, only: set[str] | None = None, root: Path = ROOT) -> Result:
    jobs = collect(cfg, only, root)
    result = run_filters(jobs, build_filters(cfg, root, DriveZone.load(root)))
    data = root / "data"
    mark_new(result.kept, data / "seen.json")
    today = date.today().isoformat()
    write_csv(data / f"jobs_{today}.csv", result.kept)
    write_csv(data / "jobs_latest.csv", result.kept)
    write_csv(
        data / f"rejected_{today}.csv",
        [j for j, _ in result.rejected],
        [r for _, r in result.rejected],
    )
    return result
