"""The few Gmail API calls outreach needs: a label, drafts, sending and threads."""

from __future__ import annotations

import logging

from ..gmail import header, plain_text

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


def draft_link(message_id: str) -> str:
    """Opens the draft in Gmail's web app."""
    return f"https://mail.google.com/mail/u/0/#drafts?compose={message_id}"


def get_draft(gmail, draft_id: str) -> dict | None:
    """The draft as it is in Gmail now ({to, subject, body}), or None if it's gone."""
    try:
        draft = gmail.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise
    payload = (draft.get("message") or {}).get("payload", {})
    return {
        "to": header(payload, "To"),
        "subject": header(payload, "Subject"),
        "body": plain_text(payload),
    }


def sent_copy(gmail, message_id: str) -> dict | None:
    """The sent message for a draft that is gone (you sent it from Gmail yourself), or None."""
    if not message_id:
        return None
    try:
        msg = gmail.users().messages().get(userId="me", id=message_id, format="minimal").execute()
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise
    return msg if "SENT" in (msg.get("labelIds") or []) else None


def send_draft(gmail, draft_id: str) -> dict | None:
    """Send the draft exactly as it is in Gmail now. None if the draft no longer exists."""
    try:
        return gmail.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    except Exception as exc:
        if is_not_found(exc):
            return None
        raise


def delete_draft(gmail, draft_id: str) -> None:
    try:
        gmail.users().drafts().delete(userId="me", id=draft_id).execute()
    except Exception as exc:
        if not is_not_found(exc):
            raise
