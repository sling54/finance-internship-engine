"""`jobengine outreach send`: send the drafts you approved, at most 20 in any 24 hours.

Each draft is sent as it is in Gmail at that moment, so edits you made there are what goes
out. Only entries marked approved are ever sent. A random pause between sends keeps the
pace human, which helps deliverability.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .mail import send_draft, sent_copy
from .store import Entry, Log


@dataclass
class SendReport:
    sent: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    waiting: int = 0  # approved but over today's limit
    limit: int = 20

    def summary(self) -> str:
        if not (self.sent or self.gone or self.waiting):
            return "No approved outreach drafts to send."
        lines = [f"Sent {len(self.sent)} outreach emails."]
        lines += [f"  > {s}" for s in self.sent]
        lines += [f"  - {g}" for g in self.gone]
        if self.waiting:
            lines.append(
                f"{self.waiting} approved drafts wait for tomorrow "
                f"(limit {self.limit} in any 24 hours)."
            )
        return "\n".join(lines)


def label(e: Entry) -> str:
    return f"{e.company} - {e.role}: {e.contact or e.email} <{e.email}>"


def resolve_gone(gmail, e: Entry, now: datetime) -> str:
    """The draft no longer exists: sent from Gmail by hand (track it), or deleted."""
    msg = sent_copy(gmail, e.message_id)
    if msg is not None:
        e.status, e.sent_at = "sent", now.isoformat(timespec="seconds")
        e.thread_id, e.note = msg.get("threadId", ""), "sent from Gmail"
        return f"{label(e)} was already sent from Gmail"
    e.status, e.note = "deleted", "draft deleted in Gmail"
    return f"{label(e)}: draft deleted in Gmail, not sent"


def send_approved(
    gmail,
    log_: Log,
    settings: dict,
    now=datetime.now,
    sleep=time.sleep,
    rng: random.Random | None = None,
) -> SendReport:
    limit = int(settings.get("daily_send_limit", 20))
    low, high = settings.get("send_gap_seconds", [45, 120])
    rng = rng or random.Random()
    report = SendReport(limit=limit)
    queue = sorted(
        (e for e in log_.entries.values() if e.status == "approved"),
        key=lambda e: (e.approved_at or e.date, e.date),
    )
    for i, e in enumerate(queue):
        if log_.sent_since(now() - timedelta(hours=24)) >= limit:
            report.waiting = len(queue) - i
            break
        if report.sent:  # pause between sends, not before the first
            sleep(rng.uniform(float(low), float(high)))
        msg = send_draft(gmail, e.draft_id)
        if msg is None:
            report.gone.append(resolve_gone(gmail, e, now()))
        else:
            e.status, e.sent_at = "sent", now().isoformat(timespec="seconds")
            e.thread_id, e.message_id = msg.get("threadId", ""), msg.get("id", e.message_id)
            report.sent.append(label(e))
        log_.save()  # after every send, so a crash never sends one twice
    return report
