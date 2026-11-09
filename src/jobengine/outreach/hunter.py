"""Finds people at a company with Hunter.io's Domain Search.

Each company's answer is cached (default 30 days) in data/hunter_cache.json, and lookups are
counted by calendar month in data/hunter_usage.json, so a run never goes over your plan's
monthly limit. Every lookup is counted as one credit, even one that finds nobody, to stay safe.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from ..filters.dedup import company_key
from ..http import TIMEOUT, session
from ..textutil import normalize

log = logging.getLogger(__name__)

URL = "https://api.hunter.io/v2/domain-search"


class CreditLimit(Exception):
    """This month's Hunter lookups are used up."""


class HunterError(Exception):
    """Hunter refused the request (bad key, rate limit, server error)."""


class Hunter:
    def __init__(
        self,
        key: str,
        root: Path,
        monthly_limit: int = 25,
        cache_days: int = 30,
        domains: dict | None = None,
        http=None,
        today: date | None = None,
    ):
        self.key = key
        self.monthly_limit = monthly_limit
        self.cache_days = cache_days
        self.domains = {company_key(k): v for k, v in (domains or {}).items()}
        self.http = http or session()
        self.today = today or date.today()
        self.cache_path = root / "data" / "hunter_cache.json"
        self.usage_path = root / "data" / "hunter_usage.json"
        self.cache = _read(self.cache_path)
        self.usage = _read(self.usage_path)

    @property
    def month(self) -> str:
        return f"{self.today:%Y-%m}"

    @property
    def used(self) -> int:
        return int(self.usage.get(self.month, 0))

    def people(self, company: str) -> list[dict]:
        """Hunter's email records for the company: value, type, confidence, names, position."""
        domain = self.domains.get(company_key(company))
        # Cache by domain, or by the full name: "Citadel" and "Citadel Securities" differ.
        key = domain or normalize(company)
        hit = self.cache.get(key)
        if hit and date.fromisoformat(hit["fetched"]) >= self.today - timedelta(
            days=self.cache_days
        ):
            return hit["emails"]
        if self.used >= self.monthly_limit:
            raise CreditLimit(
                f"Hunter limit reached ({self.used} of {self.monthly_limit} lookups this month)"
            )
        params = {"api_key": self.key, "type": "personal", "limit": 10}
        if domain:
            params["domain"] = domain
        else:
            params["company"] = company
        resp = self.http.get(URL, params=params, timeout=TIMEOUT)
        self.usage[self.month] = self.used + 1
        _write(self.usage_path, self.usage)
        if resp.status_code == 401:
            raise HunterError("Hunter rejected the API key; check HUNTER_API_KEY in .env")
        if resp.status_code == 429:
            raise HunterError("Hunter's rate limit was hit; try again later")
        if resp.status_code >= 500:
            raise HunterError(f"Hunter is having trouble (HTTP {resp.status_code}); try later")
        if resp.status_code >= 400:
            # 400 here usually means Hunter couldn't work out the company's domain.
            log.info("Hunter answered %s for %s", resp.status_code, company)
            emails = []
        else:
            emails = (resp.json().get("data") or {}).get("emails") or []
        self.cache[key] = {"fetched": self.today.isoformat(), "emails": emails}
        _write(self.cache_path, self.cache)
        return emails


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
