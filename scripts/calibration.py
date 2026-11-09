"""Calibrate the AI fit score against your own judgment.

    .venv/bin/python scripts/calibration.py make      # writes data/calibration.csv (10 roles)
    # fill in your_score (1-5) for each row, then:
    .venv/bin/python scripts/calibration.py compare   # how close the AI is, and the biggest misses

`make` picks roles from data/jobs_latest.csv that have an AI score, spread across tracks and
scores, so one sheet covers the range. `compare` reports how many AI scores are within 1 point of
yours and which roles it got most wrong; use those to edit `rubric` or `what_i_want`
(in config/scoring.local.yml or via jobengine init), then run `jobengine scan --rescore`.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

FIELDS = ["company", "title", "track", "fit_score", "fit_reason", "your_score", "url"]


def pick(rows: list[dict], n: int = 10) -> list[dict]:
    """Up to n scored roles, alternating tracks and covering scores from high to low."""
    scored = [r for r in rows if str(r.get("fit_score", "")).strip().isdigit()]
    scored.sort(key=lambda r: (-int(r["fit_score"]), r.get("track", ""), r.get("company", "")))
    by_score: dict[int, list[dict]] = {}
    for r in scored:
        by_score.setdefault(int(r["fit_score"]), []).append(r)
    picked: list[dict] = []
    seen_tracks: dict[int, set] = {s: set() for s in by_score}
    while len(picked) < n and any(by_score.values()):
        for score in sorted(by_score, reverse=True):
            group = by_score[score]
            if not group or len(picked) >= n:
                continue
            # prefer a track this score hasn't shown yet
            i = next(
                (k for k, r in enumerate(group) if r.get("track") not in seen_tracks[score]), 0
            )
            row = group.pop(i)
            seen_tracks[score].add(row.get("track"))
            picked.append(row)
    return picked


def make(root: Path) -> Path:
    with open(root / "data/jobs_latest.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = root / "data/calibration.csv"
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in pick(rows):
            writer.writerow({**r, "your_score": ""})
    return out


def compare(path: Path) -> str:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if str(r.get("your_score", "")).strip()]
    if not rows:
        return "No your_score filled in yet."
    diffs = [(int(r["fit_score"]) - int(r["your_score"]), r) for r in rows]
    close = sum(abs(d) <= 1 for d, _ in diffs)
    exact = sum(d == 0 for d, _ in diffs)
    lines = [
        f"{len(rows)} roles: {exact} exact, {close} within 1 point "
        f"({close / len(rows):.0%}). Average AI minus you: {sum(d for d, _ in diffs) / len(rows):+.1f}."
    ]
    for d, r in sorted(diffs, key=lambda x: -abs(x[0]))[:3]:
        if d:
            lines.append(
                f"  AI {r['fit_score']} vs you {r['your_score']}: {r['company']} - {r['title']} "
                f"({r['track']}). AI said: {r['fit_reason']}"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "make":
        print(f"Wrote {make(Path('.'))}; fill in your_score (1-5) for each row.")
    elif command == "compare":
        print(compare(Path("data/calibration.csv")))
    else:
        sys.exit(__doc__)
