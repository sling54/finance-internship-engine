"""`jobengine doctor`: checks your setup and says what to fix, one line each.

ok   works
fix  needed for the part named; the line says how
--   optional; only matters if you want that part

Exit code 1 if anything says fix, so a setup script can stop on it.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path

from .config import Config


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def checks(cfg: Config, root: Path, model_check=None) -> list[tuple[str, str]]:
    from .zone import api_key

    out: list[tuple[str, str]] = []

    def add(level: str, text: str) -> None:
        out.append((level, text))

    add("ok", f"Python {platform.python_version()}")  # pip refuses anything older than 3.10

    # Profile, resume, tracker: needed for everything personal.
    if cfg.profile_is_example:
        add("fix", "Profile: run jobengine init --resume path/to/Resume.pdf")
    else:
        add("ok", f"Profile for {cfg.profile.get('name') or '(no name)'}")
        if not cfg.profile.get("what_i_want") and not cfg.scoring.get("what_i_want"):
            add("--", "No what_i_want yet: rerun jobengine init to describe the roles you want")
    resume = root / cfg.profile.get("resume_path", "private/Resume.pdf")
    if resume.is_file():
        add("ok", f"Resume at {resume}")
    else:
        add("fix", f"Resume: no file at {resume}; set resume_path in config/profile.yml")
    tracker = root / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
    if not tracker.exists():
        add("fix", f"Tracker: no file at {tracker}; jobengine init creates one")
    elif (tracker.parent / f"~${tracker.name}").exists():
        add("--", "Tracker is open in Excel; close it before track, outreach or applied")
    else:
        add("ok", f"Tracker at {tracker}")

    # AI fit score.
    if not cfg.scoring.get("enabled", False):
        add("--", "AI fit score is off (scoring.yml enabled: false)")
    else:
        url = cfg.scoring.get("lm_studio", {}).get("url", "http://localhost:1234/v1")
        try:
            if model_check is not None:
                model = model_check(cfg.scoring, root)
            else:
                from .scorer import client_from

                model = client_from(cfg.scoring, root).pick_model()
            add("ok", f"AI model {model} at {url}")
        except Exception:
            add(
                "--",
                f"AI model not reachable at {url}: open LM Studio > Developer > Start Server "
                "(scans still run, just without fit scores)",
            )

    # Gmail: tracker sync, alert emails, outreach.
    alerts = cfg.sources.get("alert_emails", {})
    if not (_has("googleapiclient") and _has("google_auth_oauthlib")):
        add("fix", 'Gmail: run pip install -e ".[gmail]" (needed for track and outreach)')
    elif not (root / alerts.get("credentials_path", "auth/credentials.json")).exists():
        add("fix", "Gmail: save your Google OAuth client as auth/credentials.json (see README)")
    elif not (root / alerts.get("token_path", "auth/token.json")).exists():
        add("fix", "Gmail: run jobengine track once to sign in")
    else:
        add("ok", "Gmail signed in")

    # Optional sources and features.
    if cfg.sources.get("jobspy", {}).get("enabled") and not _has("jobspy"):
        add("fix", 'Job boards: run pip install -e ".[jobspy]" (or set jobspy.enabled: false)')
    if api_key(root, "HUNTER_API_KEY"):
        add("ok", "Hunter.io key for outreach")
    else:
        add("--", "Outreach: add HUNTER_API_KEY=... to .env")
    if (root / "config" / "outreach_templates.local.yml").exists():
        add("ok", "Your outreach templates")
    else:
        add("--", "Outreach: copy outreach_templates.example.yml to outreach_templates.local.yml")
    if _has("playwright"):
        add("ok", "Browser form filling (Playwright)")
    else:
        add("--", 'Form filling: run pip install -e ".[apply]" && playwright install chromium')

    # Daily schedule.
    if sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/com.jobengine.daily-scan.plist"
        if plist.exists():
            add("ok", "Daily scan scheduled (launchd)")
        else:
            add("--", "Daily scan: run ./scripts/schedule-daily-scan.sh")
    elif os.name == "nt":
        add("--", "Daily scan: run scripts\\schedule-daily-scan.ps1 in PowerShell")
    else:
        add("--", "Daily scan: add scripts/daily-scan.sh to cron (see README)")
    return out


def run(cfg: Config, root: Path, model_check=None) -> int:
    results = checks(cfg, root, model_check)
    for level, text in results:
        print(f"{level:<4} {text}")
    fixes = sum(level == "fix" for level, _ in results)
    print(f"\n{fixes} to fix." if fixes else "\nReady.")
    return 1 if fixes else 0
