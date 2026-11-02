"""Shared HTTP session with retries and a polite timeout."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TIMEOUT = 30
USER_AGENT = "finance-internship-engine/0.1 (+https://github.com/sling54/finance-internship-engine)"


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    return s


def get_json(url: str, sess: requests.Session | None = None, **params):
    sess = sess or session()
    resp = sess.get(url, params=params or None, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()
