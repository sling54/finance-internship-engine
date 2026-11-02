"""Loads the YAML files in config/."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Config:
    titles: dict
    firms: dict
    sources: dict
    profile: dict
    profile_is_example: bool
    scoring: dict

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
        return cls(
            titles=load_yaml(config_dir / "titles.yml"),
            firms=firms,
            sources=load_yaml(config_dir / "sources.yml"),
            profile=load_yaml(profile_path),
            profile_is_example=is_example,
            scoring=load_yaml(config_dir / "scoring.yml"),
        )
