#!/bin/bash
# Runs one full scan and shows a macOS notification with the number of new roles.
# Called every morning by launchd (see schedule-daily-scan.sh); safe to run by hand too.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
LOG="logs/scan_$(date +%F).log"

# Make sure LM Studio's server is up so the scan gets AI fit scores (on Nov 8-9 the 7:35 am
# scan ran with LM Studio closed and skipped scoring). Harmless if LM Studio isn't installed.
start_lm_studio() {
  local LMS
  LMS=$(command -v lms || echo "$HOME/.lmstudio/bin/lms")
  [ -x "$LMS" ] || { echo "LM Studio CLI (lms) not found; scan runs without AI scores"; return; }
  "$LMS" server start >/dev/null 2>&1
  # Load the model named in config/scoring.yml unless a model is already loaded.
  local MODEL
  MODEL=$(sed -n 's/^ *model: *\([^ #]*\).*/\1/p' config/scoring.yml | head -1)
  if [ -n "$MODEL" ] && ! "$LMS" ps 2>/dev/null | grep -qi "$MODEL"; then
    "$LMS" load "$MODEL" -y >/dev/null 2>&1 || echo "Couldn't load $MODEL in LM Studio"
  fi
}

# Keep the Mac awake until the scan is done: on Nov 10 and 11 the 7:30 am scan took 7-9 hours
# because the Mac slept partway through. caffeinate -i -w waits for this script to finish.
if command -v caffeinate >/dev/null; then
  caffeinate -i -w $$ &
fi

{
  echo "=== scan started $(date) ==="
  start_lm_studio
  .venv/bin/jobengine scan
  echo "=== scan finished $(date) (exit $?) ==="
  .venv/bin/jobengine track
  .venv/bin/jobengine outreach send    # only drafts you approved; nothing if there are none
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
