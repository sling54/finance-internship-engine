"""The few Gmail API calls outreach needs: a label, drafts, and (later) sending."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

LABEL = "jobengine/outreach"


def label_id(gmail, name: str = LABEL) -> str:
    """The id of the label, created if it doesn't exist yet."""
    labels = gmail.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label.get("name") == name:
            return label["id"]
    body = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
    return gmail.users().labels().create(userId="me", body=body).execute()["id"]


def create_draft(gmail, message: dict, label: str | None) -> tuple[str, str]:
    """Save a draft and label it. Returns (draft id, message id)."""
    draft = gmail.users().drafts().create(userId="me", body={"message": message}).execute()
    message_id = (draft.get("message") or {}).get("id", "")
    if label and message_id:
        try:
            gmail.users().messages().modify(
                userId="me", id=message_id, body={"addLabelIds": [label]}
            ).execute()
        except Exception as exc:  # the draft is saved either way; the label only helps you find it
            log.warning("couldn't label draft %s: %s", draft.get("id"), exc)
    return draft["id"], message_id


def is_not_found(exc: Exception) -> bool:
    """True for Gmail's 404, e.g. a draft you deleted or already sent yourself."""
    status = getattr(getattr(exc, "resp", None), "status", None) or getattr(exc, "status", None)
    return str(status) == "404"
