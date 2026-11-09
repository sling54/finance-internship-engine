"""Interactive setup for a student's local profile and application tracker.

With `jobengine init --resume Resume.pdf`, the answers are filled in from the resume first,
so most prompts only need Enter.
"""

from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from pathlib import Path

import yaml
from openpyxl import Workbook

from .config import load_with_local, load_yaml


def _ask(label, default="", convert=str):
    while True:
        answer = input(f"{label} [{default or 'blank'}]: ").strip()
        if not answer:
            return default
        try:
            return convert(answer)
        except ValueError:
            print("Invalid value. Try again.")


def _choice(*options):
    def convert(value):
        value = value.lower()
        if value not in options:
            raise ValueError
        return value

    return convert


def _month(value):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError
    return value


def _profile(example, found=None, resume_path=""):
    found = found or {}
    today = date.today()
    graduation_default = found.get("graduation", f"{today.year + 1}-05")
    profile = {
        "name": _ask("Name", found.get("name", "")),
        "school": _ask("School", found.get("school", "")),
        "degree": _ask(
            "Degree (bachelors, masters, phd)",
            found.get("degree", example["degree"]),
            _choice("bachelors", "masters", "phd"),
        ),
        "major": _ask("Major", found.get("major", example["major"])),
        "minor": _ask("Minor", found.get("minor", "")),
        "gpa": _ask("GPA", found.get("gpa"), float),
        "graduation": _ask("Expected graduation (YYYY-MM)", graduation_default, _month),
    }
    graduation = profile["graduation"]
    profile["grad_window"] = {
        "start": _ask("Earliest eligible graduation (YYYY-MM)", graduation, _month),
        "end": _ask("Latest eligible graduation (YYYY-MM)", graduation, _month),
    }
    profile["work_authorization"] = _ask(
        "US work authorization (us_citizen, permanent_resident, needs_sponsorship)",
        found.get("work_authorization", ""),
        _choice("us_citizen", "permanent_resident", "needs_sponsorship"),
    )
    profile["home_base"] = {
        "name": _ask("Campus name", profile["school"]),
        "address": _ask("Campus address"),
        "lon": _ask("Campus longitude (optional, for jobengine zone)", None, float),
        "lat": _ask("Campus latitude (optional, for jobengine zone)", None, float),
        "max_drive_minutes": _ask(
            "Maximum commute in minutes", example["home_base"]["max_drive_minutes"], int
        ),
    }
    profile["tracker_path"] = str(Path(_ask("Tracker path", example["tracker_path"])).expanduser())
    profile["resume_path"] = str(
        Path(_ask("Resume PDF path", resume_path or example["resume_path"])).expanduser()
    )
    wants = _ask(
        "What internships do you want? Describe them in plain English for the AI fit score "
        "(Enter keeps config/scoring.yml)"
    )
    if wants:
        profile["what_i_want"] = wants
    return profile


def _resume_facts(config: Path, resume: Path) -> dict:
    """Profile defaults read from the resume, with a line saying what was found."""
    from .resume import read_profile_facts
    from .scorer import client_from, read_resume

    if not resume.is_file():
        raise RuntimeError(f"no resume at {resume}")
    try:
        text = read_resume(resume, limit=6000)
    except Exception as exc:  # a damaged or protected PDF
        raise RuntimeError(f"couldn't read {resume.name}: {exc}") from exc
    if not text.strip():
        print(f"Found no text in {resume.name}, so every prompt starts blank.")
        return {}
    scoring = load_with_local(config, "scoring")
    client = client_from(scoring, config.parent) if scoring.get("enabled", False) else None
    found, reader = read_profile_facts(text, client)
    how = reader
    if reader == "rules":
        how = "fixed rules (LM Studio isn't running)" if client else "fixed rules"
    print(
        f"Read {resume.name} with {how}: found {', '.join(found) or 'nothing'}. "
        "Check each answer below and press Enter to keep it."
    )
    return found


def initialize(root: Path, resume: str | None = None) -> int:
    config = root / "config"
    path = config / "profile.yml"
    try:
        overwrite = path.exists()
        if overwrite and input("Replace config/profile.yml? [y/N]: ").strip().lower() not in {
            "y",
            "yes",
        }:
            print("Kept the existing profile.")
            return 0
        found, resume_path = {}, ""
        if resume:
            resume_path = str(Path(resume).expanduser().resolve())
            found = _resume_facts(config, Path(resume_path))
        profile = _profile(load_yaml(config / "profile.example.yml"), found, resume_path)
        tracker = root / profile["tracker_path"]
        tracker.parent.mkdir(parents=True, exist_ok=True)
        if tracker.exists():
            print(f"Kept the existing tracker at {tracker}.")
        else:
            wb = Workbook()
            wb.active.title = "Applications"
            wb.active.append(["Application Date", "Company", "Role Applied To"])
            contents = BytesIO()
            wb.save(contents)
            try:
                with tracker.open("xb") as handle:
                    handle.write(contents.getbuffer())
                print(f"Created {tracker}.")
            except FileExistsError:
                print(f"Kept the existing tracker at {tracker}.")
        with path.open("w" if overwrite else "x", encoding="utf-8") as handle:
            yaml.safe_dump(profile, handle, sort_keys=False, allow_unicode=True)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"Setup could not finish: {exc}")
        return 1

    print("Saved config/profile.yml.")
    print("Run jobengine scan --no-ai to find postings without Gmail or a local model.")
    print('Run jobengine applied --company "Firm" --role "Role" after applying.')
    return 0
