"""Gmail access shared by the alert reader and the tracker sync.

Uses your own Google OAuth client (auth/credentials.json); the first run opens a browser to
sign in, and the token is saved in auth/token.json. Both files are git-ignored.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from .textutil import html_to_text

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def service(settings: dict, root: Path):
    """An authorized Gmail API client, or None if the Google libraries aren't installed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        log.warning('Gmail libraries not installed; run: pip install -e ".[gmail]"')
        return None

    token_path = root / settings.get("token_path", "auth/token.json")
    creds = (
        Credentials.from_authorized_user_file(token_path, SCOPES) if token_path.exists() else None
    )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                root / settings.get("credentials_path", "auth/credentials.json"), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data).decode("utf-8", "replace")


def part(payload: dict, mime: str) -> str:
    """The first body part of the given MIME type in a Gmail API message payload."""
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for sub in payload.get("parts", []) or []:
        found = part(sub, mime)
        if found:
            return found
    return ""


def header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def plain_text(payload: dict) -> str:
    return part(payload, "text/plain") or html_to_text(part(payload, "text/html"))
