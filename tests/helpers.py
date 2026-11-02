"""Shared test helpers."""

import json
from datetime import date
from pathlib import Path

from jobengine.models import Posting

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 2)


def load_fixture(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


def job(title="Trading Intern", company="Acme Trading", locations=None, source="test", **kw):
    return Posting(
        company=company,
        title=title,
        url="https://example.com/1",
        source=source,
        locations=locations or [],
        **kw,
    )
