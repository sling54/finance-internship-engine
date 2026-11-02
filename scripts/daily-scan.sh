#!/bin/bash
# Runs one full scan and shows a macOS notification with the number of new roles.
# Called every morning by launchd (see schedule-daily-scan.sh); safe to run by hand too.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
LOG="logs/scan_$(date +%F).log"

{
  echo "=== scan started $(date) ==="
  .venv/bin/jobengine scan
  echo "=== scan finished $(date) (exit $?) ==="
  .venv/bin/jobengine track
} >>"$LOG" 2>&1

# Count roles flagged "new" in today's results.
NEW=$(.venv/bin/python - <<'PY' 2>/dev/null
import csv
try:
    with open("data/jobs_latest.csv", encoding="utf-8-sig") as fh:
        print(sum("new" in (r.get("flags") or "") for r in csv.DictReader(fh)))
except FileNotFoundError:
    print(0)
PY
)

if command -v osascript >/dev/null; then
  osascript -e "display notification \"${NEW:-0} new roles. Open data/jobs_latest.csv\" with title \"Internship scan done\""
fi
