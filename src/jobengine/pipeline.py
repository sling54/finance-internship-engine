"""Collect -> dedupe -> filter -> write CSVs. Every dropped role keeps its reason."""

from __future__ import annotations

import csv
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import link_check, removed
from .config import ROOT, Config
from .filters.applied import AppliedFilter, read_tracker
from .filters.dedup import dedupe, job_key
from .filters.eligibility import EligibilityFilter
from .filters.location import LocationFilter
from .filters.removed import RemovedFilter
from .filters.season import SeasonFilter
from .filters.titles import TitleFilter
from .models import Posting, Rejected
from .scanners import alerts, ats, boards, lists
from .scorer import ScoreReport, score_jobs
from .zone import DriveZone

log = logging.getLogger(__name__)

NAMES = {
    "ats": "firm career pages",
    "simplify": "SimplifyJobs list",
    "kadoa": "Kadoa quant list",
    "jobspy": "LinkedIn, Indeed, ZipRecruiter (slowest)",
    "alert_emails": "job-alert emails",
}


def say(msg: str, end: str = "\n") -> None:
    """Progress for the person watching. Goes to stderr so it never mixes with results."""
    print(msg, end=end, file=sys.stderr, flush=True)


@dataclass
class Result:
    kept: list[Posting] = field(default_factory=list)
    rejected: list[tuple[Posting, str]] = field(default_factory=list)
    collected: Counter = field(default_factory=Counter)
    ai: ScoreReport | None = None
    links: link_check.Report | None = None

    def summary(self) -> str:
        reasons = Counter(r.split(":")[0] for _, r in self.rejected)
        by_source = ", ".join(f"{k} {v}" for k, v in self.collected.most_common())
        lines = [
            f"Collected: {sum(self.collected.values())} ({by_source})",
            f"Kept: {len(self.kept)}   Dropped: {len(self.rejected)}",
        ]
        lines += [f"  - {reason}: {n}" for reason, n in reasons.most_common(8)]
        if self.links:
            lines.append(self.links.summary())
        if self.ai:
            lines.append(self.ai.summary())
        return "\n".join(lines)


# Sources that answer in seconds get retried after a pause: on Nov 10 and 11 a single dropped
# connection lost the whole Kadoa list, then the whole SimplifyJobs list (3,900 postings).
RETRIED = {"simplify", "kadoa", "alert_emails"}
RETRY_PAUSES = (10, 30)


def _with_retries(name: str, run):
    pauses = RETRY_PAUSES if name in RETRIED else ()
    for pause in pauses:
        try:
            return run()
        except Exception as exc:
            log.warning("source %s failed (%s); trying again in %ss", name, exc, pause)
            time.sleep(pause)
    return run()


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
        say(f"Collecting from {NAMES.get(name, name)}...", end=" ")
        start = time.monotonic()
        try:
            found = _with_retries(name, run)
            log.info("%s: %d postings", name, len(found))
            jobs.extend(found)
            say(f"{len(found):,} postings ({time.monotonic() - start:.0f}s)")
        except Exception as exc:
            say("failed (see warning below)")
            log.warning("source %s failed: %s", name, exc)
    return jobs


def build_filters(cfg: Config, root: Path = ROOT, zone: DriveZone | None = None):
    titles = TitleFilter(cfg.titles)
    tracker = root / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
    return [
        ("removed", RemovedFilter(removed.load(root))),  # first, so its reason is the one logged
        ("title", titles),
        ("eligibility", EligibilityFilter(cfg.profile)),
        ("season", SeasonFilter(cfg.profile)),
        ("location", LocationFilter(cfg.sources, titles.location_rule, zone)),
        ("applied", AppliedFilter(read_tracker(tracker))),
    ]


def run_filters(jobs: list[Posting], filters) -> Result:
    result = Result()
    result.collected.update(j.source for j in jobs)
    for job in jobs:  # collapse non-breaking and repeated spaces some sources put in titles
        job.title = " ".join(job.title.replace("\xa0", " ").split())
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
    "fit_score",
    "fit_reason",
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
    # utf-8-sig adds the marker Excel and Numbers need to show "–" and "’" correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for i, job in enumerate(jobs):
            row = job.to_row()
            if reasons is not None:
                row["reason"] = reasons[i]
            writer.writerow(row)


def check_links(result: Result, cfg: Config, root: Path, get=None) -> None:
    """Drop closed listings. A failure here never fails the scan."""
    settings = link_check.settings(cfg.sources)
    if not settings["enabled"] or not result.kept:
        return
    say(f"Checking {len(result.kept)} listings are still open...")
    try:
        result.links = link_check.apply(result, settings, root, get)
    except Exception as exc:
        say("link check failed (see warning below); keeping every role")
        log.warning("link check failed: %s", exc)
        return


def run(cfg: Config, only: set[str] | None = None, root: Path = ROOT, ai: bool = True) -> Result:
    jobs = collect(cfg, only, root)
    say(f"Filtering {len(jobs):,} postings...")
    result = run_filters(jobs, build_filters(cfg, root, DriveZone.load(root)))
    say(f"{len(result.kept)} roles kept")
    check_links(result, cfg, root)
    data = root / "data"
    mark_new(result.kept, data / "seen.json")
    if ai:
        result.ai = score_jobs(result.kept, cfg.scoring, cfg.profile, root)
    today = date.today().isoformat()
    write_csv(data / f"jobs_{today}.csv", result.kept)
    write_csv(data / "jobs_latest.csv", result.kept)
    write_csv(
        data / f"rejected_{today}.csv",
        [j for j, _ in result.rejected],
        [r for _, r in result.rejected],
    )
    record_history(data / "scan_history.json", today, result)
    return result


def record_history(path: Path, day: str, result: Result) -> None:
    """One line per scan (collected, kept, dropped), for `jobengine stats`."""
    try:
        history = json.loads(path.read_text()) if path.exists() else []
    except json.JSONDecodeError:
        history = []
    history.append(
        {
            "date": day,
            "collected": sum(result.collected.values()),
            "kept": len(result.kept),
            "dropped": len(result.rejected),
        }
    )
    path.write_text(json.dumps(history[-1000:], indent=0))
