"""Loads the YAML files in config/."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def deep_merge(base: dict, override: dict) -> dict:
    """base with override on top: nested dicts merge, anything else (lists too) is replaced."""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_with_local(config_dir: Path, name: str) -> dict:
    """config/<name>.yml, with your git-ignored config/<name>.local.yml on top, if it exists.

    Lets each person change any setting (tracks, sources, scoring, outreach) without editing
    the shared file, so `git pull` never conflicts with your choices."""
    base_path, local_path = config_dir / f"{name}.yml", config_dir / f"{name}.local.yml"
    base = load_yaml(base_path) if base_path.exists() else {}
    return deep_merge(base, load_yaml(local_path)) if local_path.exists() else base


@dataclass
class Config:
    titles: dict
    firms: dict
    sources: dict
    profile: dict
    profile_is_example: bool
    scoring: dict = field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: Path = CONFIG_DIR) -> Config:
        profile_path = config_dir / "profile.yml"
        is_example = not profile_path.exists()
        if is_example:
            profile_path = config_dir / "profile.example.yml"
        firms = load_yaml(config_dir / "firms.yml")
        local_firms = config_dir / "firms.local.yml"
        if local_firms.exists():
            for kind, entries in load_yaml(local_firms).items():
                firms.setdefault(kind, {}).update(entries or {})
        profile = load_yaml(profile_path)
        scoring = load_with_local(config_dir, "scoring")
        if not is_example and profile.get("what_i_want"):
            # Your own words from `jobengine init` replace the shared default.
            scoring["what_i_want"] = profile["what_i_want"]
        return cls(
            titles=load_with_local(config_dir, "titles"),
            firms=firms,
            sources=load_with_local(config_dir, "sources"),
            profile=profile,
            profile_is_example=is_example,
            scoring=scoring,
        )
