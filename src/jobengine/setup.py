"""Interactive setup for a student's local profile and application tracker."""

from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from pathlib import Path

import yaml
from openpyxl import Workbook

from .config import load_yaml


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


def _profile(example):
    today = date.today()
    graduation_default = f"{today.year + 1}-05"
    profile = {
        "name": _ask("Name"),
        "school": _ask("School"),
        "degree": _ask(
            "Degree (bachelors, masters, phd)",
            example["degree"],
            _choice("bachelors", "masters", "phd"),
        ),
        "major": _ask("Major", example["major"]),
        "minor": _ask("Minor"),
        "gpa": _ask("GPA", None, float),
        "graduation": _ask("Expected graduation (YYYY-MM)", graduation_default, _month),
    }
    graduation = profile["graduation"]
    profile["grad_window"] = {
        "start": _ask("Earliest eligible graduation (YYYY-MM)", graduation, _month),
        "end": _ask("Latest eligible graduation (YYYY-MM)", graduation, _month),
    }
    profile["work_authorization"] = _ask(
        "US work authorization (us_citizen, permanent_resident, needs_sponsorship)",
        "",
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
    profile["resume_path"] = str(Path(_ask("Resume PDF path", example["resume_path"])).expanduser())
    return profile


def initialize(root: Path) -> int:
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
        profile = _profile(load_yaml(config / "profile.example.yml"))
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
