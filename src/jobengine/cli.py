"""Command line: `jobengine scan` and `jobengine zone`."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ROOT, Config
from .zone import api_key, build_zone

SOURCES = ["ats", "simplify", "kadoa", "jobspy", "alert_emails"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobengine", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every source call")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="collect, filter and write data/jobs_latest.csv")
    scan.add_argument(
        "--only",
        nargs="+",
        choices=SOURCES,
        help="run just these sources (default: all enabled in sources.yml)",
    )
    scan.add_argument("--no-ai", action="store_true", help="skip the AI fit score this run")
    sub.add_parser("zone", help="draw the drive-time zone with OpenRouteService (one API call)")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load()
    if cfg.profile_is_example:
        print(
            "Note: using config/profile.example.yml. Copy it to config/profile.yml "
            "and fill in your details.",
            file=sys.stderr,
        )

    if args.command == "zone":
        key = api_key(ROOT)
        if not key:
            print("Add ORS_API_KEY=... to .env first (free key at openrouteservice.org).")
            return 1
        base = cfg.profile["home_base"]
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
