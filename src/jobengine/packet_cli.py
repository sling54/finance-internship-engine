"""The `jobengine packet` command."""

from __future__ import annotations

from pathlib import Path


def _row(args, root: Path) -> dict | None:
    from .cli import find_jobs

    csv_path = root / "data" / "jobs_latest.csv"
    if args.url:
        found = find_jobs(csv_path, args.url)
        if found:
            return found[0]
        return {"company": args.company or "", "title": args.role or "", "url": args.url}
    matches = find_jobs(csv_path, " ".join(args.search))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print(
            "No role in data/jobs_latest.csv matches that. Try fewer words, or use "
            '--url "https://..." --company "Firm" --role "Role".'
        )
    else:
        print("Several roles match; add more words or use --url:")
        for m in matches[:10]:
            print(f"  {m['company']} - {m['title']}  {m['url']}")
    return None


def run(args, cfg, root: Path, get=None, ask=input, fill=None) -> int:
    from .config import load_with_local
    from .http import TIMEOUT, session
    from .outreach.cli import local_model
    from .packet import build, write
    from .scorer import about_me, read_resume

    if not args.search and not args.url:
        print('Name the role: jobengine packet <words from the role>, or --url "https://..."')
        return 2
    row = _row(args, root)
    if row is None:
        return 1
    settings = load_with_local(root / "config", "apply")
    client = local_model(cfg.scoring, root)
    if client is None:
        print("The AI model isn't running, so open questions are left for you.")
    resume = root / cfg.profile.get("resume_path", "private/Resume.pdf")
    try:
        about = about_me(cfg.profile, read_resume(resume))
    except Exception:
        about = about_me(cfg.profile, "")
    if get is None:
        http = session()

        def get(url):
            return http.get(url, timeout=TIMEOUT)

    packet = build(row, cfg.profile, settings, root, client, get, about)
    folder = write(packet, root)
    counts = {
        s: sum(f.source == s for f in packet.fields) for s in ("profile", "rule", "ai", "you")
    }
    print(f"{packet.company} - {packet.title} ({packet.board})")
    print(
        f"{counts['profile'] + counts['rule']} answered from your profile, {counts['ai']} AI drafts "
        f"to edit, {counts['you']} left for you."
    )
    print(f"Packet: {folder / 'packet.md'}")
    if not args.fill:
        print(f"Apply: {packet.apply_url}")
        return 0

    if fill is None:
        from .formfill import open_and_fill as fill
    try:
        fill(packet, root, settings)
    except RuntimeError as exc:
        print(exc)
        return 1
    if ask("Did you submit the application? [y/N]: ").strip().lower() in ("y", "yes"):
        from .tracker_sync import record_application

        tracker = root / cfg.profile.get("tracker_path", "data/ApplicationTracker.xlsx")
        print(record_application(tracker, root, packet.company, packet.title, packet.url))
    else:
        print("Not recorded. Run jobengine applied later if you submit it.")
    return 0
