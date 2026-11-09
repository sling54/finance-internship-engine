"""Feature 7: cold outreach after you apply.

`jobengine outreach plan` finds contacts for your recent applications with Hunter.io and writes
a Gmail draft to each from your templates. Nothing is sent until you approve it.
Settings: config/outreach.yml. Templates: config/outreach_templates.local.yml (git-ignored),
or the fake examples in config/outreach_templates.example.yml.
"""

from __future__ import annotations

from pathlib import Path

from ..config import load_yaml


def load_settings(config_dir: Path) -> dict:
    path = config_dir / "outreach.yml"
    return load_yaml(path) if path.exists() else {}


def load_templates(config_dir: Path) -> dict:
    """Your templates from the .local file if it exists, else the fake examples.

    The .local file replaces the examples entirely, so an example template is never sent
    under your name just because its kind of contact isn't covered by yours."""
    for name in ("outreach_templates.local.yml", "outreach_templates.example.yml"):
        path = config_dir / name
        if path.exists():
            return load_yaml(path).get("templates") or {}
    return {}
