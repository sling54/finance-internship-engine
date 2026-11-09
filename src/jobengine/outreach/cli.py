"""The `jobengine outreach ...` commands."""

from __future__ import annotations

from pathlib import Path

from . import load_settings, load_templates


def local_model(scoring: dict, root: Path | None = None):
    """The model for the {hook} sentence, or None if scoring is off or the server is down."""
    if not scoring.get("enabled", False):
        return None
    from ..scorer import client_from

    client = client_from(scoring, root)
    try:
        client.pick_model()
    except Exception:
        return None
    return client


def run_plan(cfg, root: Path, dry_run: bool) -> int:
    from ..gmail import service
    from ..zone import api_key
    from .hunter import Hunter
    from .plan import plan

    settings = load_settings(root / "config")
    if not settings.get("enabled", True):
        print("Outreach is turned off in config/outreach.yml.")
        return 0
    templates = load_templates(root / "config")
    # Real drafts only from your own templates; the fake examples are fine for a dry run.
    if not templates or (
        not dry_run and not (root / "config" / "outreach_templates.local.yml").exists()
    ):
        print("Write your templates first: copy config/outreach_templates.example.yml to")
        print("config/outreach_templates.local.yml and edit it. (--dry-run works without it.)")
        return 1
    tracker = root / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
    if not tracker.exists():
        print(f"No tracker at {tracker}; set tracker_path in config/profile.yml.")
        return 1
    key = api_key(root, "HUNTER_API_KEY")
    if not key:
        print("Add HUNTER_API_KEY=... to .env first (hunter.io > API keys).")
        return 1
    limits = settings.get("hunter") or {}
    hunter = Hunter(
        key,
        root,
        int(limits.get("monthly_credit_limit", 25)),
        int(limits.get("cache_days", 30)),
        settings.get("domains") or {},
    )
    gmail = None
    if not dry_run:
        gmail = service(cfg.sources.get("alert_emails", {}), root)
        if gmail is None:
            print(
                'Gmail isn\'t set up; run pip install -e ".[gmail]" and add auth/credentials.json.'
            )
            return 1
    client = local_model(cfg.scoring, root)
    if client is None:
        print("LM Studio isn't running, so each template's fallback_hook is used.")
    report = plan(
        root,
        settings,
        templates,
        cfg.profile,
        tracker,
        hunter,
        gmail=gmail,
        client=client,
        dry_run=dry_run,
    )
    print(report.summary())
    print(f"Hunter lookups this month: {hunter.used} of {hunter.monthly_limit}.")
    return 0


def _gmail(cfg, root: Path):
    from ..gmail import service

    gmail = service(cfg.sources.get("alert_emails", {}), root)
    if gmail is None:
        print('Gmail isn\'t set up; run pip install -e ".[gmail]" and add auth/credentials.json.')
    return gmail


def update_sheet(cfg, root: Path, log_) -> None:
    """Copy the log's statuses to the tracker's Outreach sheet, unless Excel has it open."""
    from ..tracker_sync import Tracker, is_open_in_excel
    from .sheet import sync_sheet

    tracker = root / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
    if not tracker.exists() or is_open_in_excel(tracker):
        return
    t = Tracker(tracker)
    if sync_sheet(t, list(log_.entries.values())):
        t.save(root / "data" / "backups")


def run_review(cfg, root: Path) -> int:
    from .review import show_review
    from .store import Log

    log_ = Log.load(root)
    if not any(e.status == "drafted" for e in log_.entries.values()):
        print("No outreach drafts waiting for review. Run jobengine outreach plan first.")
        return 0
    gmail = _gmail(cfg, root)
    if gmail is None:
        return 1
    show_review(gmail, log_)
    update_sheet(cfg, root, log_)
    return 0


def run_send(cfg, root: Path) -> int:
    from .send import send_approved
    from .store import Log

    log_ = Log.load(root)
    if not any(e.status == "approved" for e in log_.entries.values()):
        print("No approved outreach drafts to send.")
        return 0
    gmail = _gmail(cfg, root)
    if gmail is None:
        return 1
    report = send_approved(gmail, log_, load_settings(root / "config"))
    print(report.summary())
    update_sheet(cfg, root, log_)
    return 0
