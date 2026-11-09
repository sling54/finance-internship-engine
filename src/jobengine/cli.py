"""Command line: `jobengine scan` and `jobengine zone`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ROOT, Config
from .zone import api_key, build_zone

SOURCES = ["ats", "simplify", "kadoa", "jobspy", "alert_emails"]


def find_jobs(csv_path, query: str) -> list[dict]:
    """Rows of the results CSV whose URL equals the query, or that contain every query word."""
    import csv

    from .textutil import normalize

    if not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    exact = [r for r in rows if r.get("url") == query.strip()]
    if exact:
        return exact
    words = normalize(query).split()
    return [
        r
        for r in rows
        if all(
            w in normalize(f"{r.get('company', '')} {r.get('title', '')}").split() for w in words
        )
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobengine", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every source call")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="set up your local profile and application tracker")
    init.add_argument(
        "--resume", metavar="PATH", help="fill in the answers from this resume (PDF or text)"
    )
    sub.add_parser("results", help="open the latest scan results in your browser")
    scan = sub.add_parser("scan", help="collect, filter and write data/jobs_latest.csv")
    scan.add_argument(
        "--only",
        nargs="+",
        choices=SOURCES,
        help="run just these sources (default: all enabled in sources.yml)",
    )
    scan.add_argument("--no-ai", action="store_true", help="skip the AI fit score this run")
    track = sub.add_parser("track", help="update the application tracker from your Gmail")
    track.add_argument(
        "--days",
        type=int,
        default=2,
        help="how far back to read email (default 2; use 30 for a first run)",
    )
    applied = sub.add_parser(
        "applied", help="record an application you just submitted (for firms that never email)"
    )
    applied.add_argument(
        "search", nargs="*", help="words from the role in data/jobs_latest.csv, or its URL"
    )
    applied.add_argument("--company", help="add this firm directly instead of searching")
    applied.add_argument("--role", default="", help="role title, with --company")
    sub.add_parser("zone", help="draw the drive-time zone with OpenRouteService (one API call)")
    sub.add_parser("removed", help="list roles you or the engine removed")
    outreach = sub.add_parser("outreach", help="cold outreach: contacts and Gmail drafts")
    steps = outreach.add_subparsers(dest="outreach_command", required=True)
    plan = steps.add_parser("plan", help="find contacts for recent applications and draft emails")
    plan.add_argument(
        "--dry-run", action="store_true", help="show the drafts it would write; write nothing"
    )
    steps.add_parser("review", help="approve or skip each draft on a local page")
    steps.add_parser("send", help="send approved drafts (at most 20 in any 24 hours)")
    stats = sub.add_parser("stats", help="scans, applications, response rate and outreach")
    stats.add_argument("--markdown", action="store_true", help="print a table for a README")
    restore = sub.add_parser("restore", help="bring back a removed role")
    restore.add_argument("url", help="the role's exact URL, as shown by jobengine removed")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "init":
        from .setup import initialize

        return initialize(ROOT, args.resume)
    if args.command == "applied" and not (args.company or args.search):
        applied.error("provide a search query or --company")
    if args.command == "removed":
        from . import removed

        entries = removed.load(ROOT)
        if not entries:
            print("No removed roles.")
        for url, e in sorted(entries.items(), key=lambda item: item[1]["date"]):
            print(f"{e['date']}  {e['reason']} (by {e['by']})  {e['company']} - {e['title']}")
            print(f"    {url}")
        return 0
    if args.command == "restore":
        from . import removed

        entry = removed.remove(ROOT, args.url)
        if entry is None:
            print("No removed role has that URL. Run jobengine removed to see them.")
            return 1
        print(
            f"Restored: {entry['company']} - {entry['title']}. It will appear again after the next scan."
        )
        return 0

    cfg = Config.load()
    if cfg.profile_is_example:
        print(
            "Note: using config/profile.example.yml. Run jobengine init to set up your profile.",
            file=sys.stderr,
        )

    if args.command == "outreach":
        from .outreach import cli as outreach

        if args.outreach_command == "review":
            return outreach.run_review(cfg, ROOT)
        if args.outreach_command == "send":
            return outreach.run_send(cfg, ROOT)
        return outreach.run_plan(cfg, ROOT, args.dry_run)

    if args.command == "stats":
        from .stats import compute

        tracker = ROOT / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
        numbers = compute(ROOT, tracker)
        print(numbers.markdown() if args.markdown else numbers.text())
        return 0

    if args.command == "results":
        from .results import show_results

        tracker = ROOT / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
        return show_results(ROOT, tracker)

    if args.command == "track":
        from .tracker_sync import sync

        tracker = ROOT / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
        if not tracker.exists():
            print(f"No tracker at {tracker}; set tracker_path in config/profile.yml.")
            return 1
        report = sync(cfg.sources.get("alert_emails", {}), tracker, ROOT, days=args.days)
        print(report.summary())
        return 0

    if args.command == "applied":
        from .tracker_sync import record_application

        tracker = ROOT / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
        if args.company:
            print(record_application(tracker, ROOT, args.company, args.role))
            return 0
        matches = find_jobs(ROOT / "data" / "jobs_latest.csv", " ".join(args.search))
        if len(matches) != 1:
            if not matches:
                print(
                    "No role in data/jobs_latest.csv matches that. Try fewer words, or use "
                    '--company "Firm" --role "Role".'
                )
            else:
                print("Several roles match; add more words or paste the URL:")
                for m in matches[:10]:
                    print(f"  {m['company']} - {m['title']}  {m['url']}")
            return 1
        m = matches[0]
        print(record_application(tracker, ROOT, m["company"], m["title"], m["url"]))
        return 0

    if args.command == "zone":
        key = api_key(ROOT)
        if not key:
            print("Add ORS_API_KEY=... to .env first (free key at openrouteservice.org).")
            return 1
        base = cfg.profile["home_base"]
        if base.get("lon") is None or base.get("lat") is None:
            print("Set campus longitude and latitude in config/profile.yml to draw the zone.")
            return 1
        build_zone(
            key,
            base["lon"],
            base["lat"],
            base.get("max_drive_minutes", 60),
            ROOT / "data" / "drive_zone.geojson",
        )
        print("Saved data/drive_zone.geojson")
        return 0

    from .pipeline import run

    result = run(cfg, set(args.only) if args.only else None, ai=not args.no_ai)
    print(result.summary())
    print("Wrote data/jobs_latest.csv and today's rejected_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
