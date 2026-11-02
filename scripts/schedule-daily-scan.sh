#!/bin/bash
# Schedules scripts/daily-scan.sh every day at the time given (default 07:30) with launchd.
# If the Mac is asleep at that time, launchd runs the scan as soon as it wakes.
#   ./scripts/schedule-daily-scan.sh          # every day at 07:30
#   ./scripts/schedule-daily-scan.sh 6 45     # every day at 06:45
#   ./scripts/schedule-daily-scan.sh off      # stop the daily scan
set -eu
LABEL="com.jobengine.daily-scan"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "off" ]; then
  rm -f "$PLIST"
  echo "Daily scan turned off."
  exit 0
fi

HOUR="${1:-7}"
MINUTE="${2:-30}"
chmod +x "$REPO/scripts/daily-scan.sh"
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
cat >"$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$REPO/scripts/daily-scan.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>
  <key>StandardErrorPath</key><string>$REPO/logs/launchd.err</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"
printf "Daily scan scheduled for %02d:%02d. Logs go to %s/logs/\n" "$HOUR" "$MINUTE" "$REPO"
