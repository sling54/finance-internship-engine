"""Checked by `jobengine track`: did anyone answer an outreach email, or did it bounce?

For each sent email, the engine reads who wrote in its Gmail thread. A message from a mail
server (mailer-daemon, postmaster) means it bounced; one from anyone but you is a reply,
which also sets the application's Next Action. App Status is never changed here.
"""

from __future__ import annotations

import logging
import re
from email.utils import parseaddr

from ..filters.dedup import company_key
from ..gmail import header
from ..tracker_sync import Row, Tracker, same_role
from .mail import is_not_found
from .store import Log

log = logging.getLogger(__name__)

_BOUNCE = re.compile(r"mailer-daemon|postmaster|mail delivery (subsystem|system)", re.I)
REPLY_NOTE = "Outreach reply from"


def check_replies(gmail, log_: Log, tracker: Tracker, rows: list[Row]) -> list[str]:
    """Mark sent entries replied or bounced. Returns one line per change."""
    sent = [e for e in log_.entries.values() if e.status == "sent" and e.thread_id]
    if not sent:
        return []
    me = gmail.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
    changes = []
    for e in sent:
        try:
            thread = (
                gmail.users()
                .threads()
                .get(userId="me", id=e.thread_id, format="metadata", metadataHeaders=["From"])
                .execute(num_retries=3)
            )
        except Exception as exc:
            if not is_not_found(exc):
                log.warning("couldn't read the thread for %s: %s", e.email, exc)
            continue
        for msg in thread.get("messages", []):
            if msg.get("id") == e.message_id:
                continue
            sender = header(msg.get("payload", {}), "From")
            name, address = parseaddr(sender)
            if _BOUNCE.search(sender):
                e.status, e.note = "bounced", "address bounced"
                changes.append(f"{e.company}: {e.email} bounced")
                break
            if address and address.lower() != me:
                who = name or e.contact or address
                e.status, e.note = "replied", f"reply from {who}"
                changes.append(f"{e.company}: reply from {who}")
                row = _application(rows, e.company, e.role)
                if row is not None:
                    tracker.set(row, "Next Action", f"{REPLY_NOTE} {who}: respond")
                break
    if changes:
        log_.save()
    return changes


def _application(rows: list[Row], company: str, role: str) -> Row | None:
    matches = [
        r
        for r in rows
        if company_key(r.company) == company_key(company) and same_role(r.role, role)
    ]
    return max(matches, key=lambda r: r.index) if matches else None
