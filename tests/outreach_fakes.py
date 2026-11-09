"""Fakes for the outreach tests: Hunter's HTTP API, the Gmail API and the local model.
All data is made up, in the style of config/profile.example.yml."""

from __future__ import annotations

import base64
import email
import email.policy
from datetime import date

from openpyxl import Workbook

PROFILE = {
    "name": "Alex Example",
    "school": "Example State University",
    "degree": "bachelors",
    "major": "Finance",
    "gpa": 3.5,
    "graduation": "2027-12",
    "resume_path": "private/Resume.pdf",
    "tracker_path": "private/ApplicationTracker.xlsx",
}


def person(first, last, position, confidence=95, kind="personal", domain="examplecapital.com"):
    return {
        "value": f"{first.lower()}.{last.lower()}@{domain}",
        "type": kind,
        "confidence": confidence,
        "first_name": first,
        "last_name": last,
        "position": position,
        "verification": {"status": "valid"},
    }


EXAMPLE_CAPITAL = [
    person("Riley", "Stone", "Head of Campus Recruiting"),
    person("Sam", "Lee", "Equity Trader"),
    person("Pat", "Cole", "Office Manager", confidence=99),
    person("Jo", "Low", "Trader", confidence=60),  # below min_confidence
    person("Info", "Desk", "General inbox", kind="generic"),  # not a person
]


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {"data": {"emails": []}}

    def json(self):
        return self._data


class FakeHTTP:
    """Answers Hunter Domain Search requests from a {company or domain: emails} dict."""

    def __init__(self, people=None, status=200):
        self.people = people or {}
        self.status = status
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        found = self.people.get(params.get("domain") or params.get("company"), [])
        return FakeResponse(self.status, {"data": {"emails": found}})


class _Call:
    def __init__(self, result):
        self.result = result

    def execute(self, num_retries=0):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class NotFound(Exception):
    """Like googleapiclient's HttpError for a 404."""

    class resp:  # noqa: N801
        status = 404


class FakeGmail:
    """The parts of the Gmail API outreach uses. Drafts live in self.drafts by id."""

    def __init__(self, labels=None, me="alex@example.com"):
        self.labels_ = list(labels or [])
        self.drafts_ = {}
        self.sent = []
        self.modified = []
        self.threads_ = {}
        self.me = me
        self._next = 0

    # resource chain: gmail.users().drafts().create(...).execute()
    def users(self):
        return self

    def labels(self):
        return _Labels(self)

    def drafts(self):
        return _Drafts(self)

    def messages(self):
        return _Messages(self)

    def threads(self):
        return _Threads(self)

    def getProfile(self, userId):  # noqa: N802
        return _Call({"emailAddress": self.me})

    def new_id(self, prefix):
        self._next += 1
        return f"{prefix}{self._next}"

    def parsed(self, draft_id):
        raw = self.drafts_[draft_id]["message"]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)


class _Labels:
    def __init__(self, g):
        self.g = g

    def list(self, userId):
        return _Call({"labels": self.g.labels_})

    def create(self, userId, body):
        label = {"id": self.g.new_id("Label_"), "name": body["name"]}
        self.g.labels_.append(label)
        return _Call(label)


class _Drafts:
    def __init__(self, g):
        self.g = g

    def create(self, userId, body):
        draft_id = self.g.new_id("r")
        draft = {"id": draft_id, "message": {"id": self.g.new_id("m"), **body["message"]}}
        self.g.drafts_[draft_id] = draft
        return _Call(draft)

    def get(self, userId, id, format="full"):
        draft = self.g.drafts_.get(id)
        if draft is None:
            return _Call(NotFound())
        msg = self.g.parsed(id)
        text = next(
            (
                p.get_payload(decode=True)
                for p in msg.walk()
                if p.get_content_type() == "text/plain"
            ),
            b"",
        )
        payload = {
            "mimeType": "text/plain",
            "headers": [
                {"name": "To", "value": msg["To"]},
                {"name": "Subject", "value": msg["Subject"]},
            ],
            "body": {"data": base64.urlsafe_b64encode(text).decode()},
        }
        return _Call({"id": id, "message": {"id": draft["message"]["id"], "payload": payload}})

    def delete(self, userId, id):
        if id not in self.g.drafts_:
            return _Call(NotFound())
        del self.g.drafts_[id]
        return _Call({})

    def send(self, userId, body):
        draft = self.g.drafts_.pop(body["id"], None)
        if draft is None:
            return _Call(NotFound())
        thread = self.g.new_id("t")
        self.g.sent.append((body["id"], draft))
        return _Call({"id": draft["message"]["id"], "threadId": thread, "labelIds": ["SENT"]})


class _Messages:
    def __init__(self, g):
        self.g = g

    def modify(self, userId, id, body):
        self.g.modified.append((id, body))
        return _Call({"id": id})


class _Threads:
    def __init__(self, g):
        self.g = g

    def get(self, userId, id, format="metadata", metadataHeaders=None):
        messages = self.g.threads_.get(id)
        if messages is None:
            return _Call(NotFound())
        return _Call(
            {
                "id": id,
                "messages": [
                    {"id": mid, "payload": {"headers": [{"name": "From", "value": sender}]}}
                    for mid, sender in messages
                ],
            }
        )


class FakeModel:
    def __init__(self, reply="", fail=False):
        self.reply, self.fail, self.prompts = reply, fail, []

    def chat(self, system, user):
        self.prompts.append(user)
        if self.fail:
            raise ConnectionError("LM Studio isn't running")
        return self.reply


def make_tracker(path, rows):
    """A tracker like the one `jobengine init` makes, plus App Status. rows: (date, company,
    role, status)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    ws.append(["Application Date", "Company", "Role Applied To", "App Status"])
    for when, company, role, status in rows:
        ws.append([when, company, role, status])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


DEFAULT_ROWS = [
    (date(2026, 11, 1), "Example Capital", "Trading Intern", "Applied"),
    (date(2026, 10, 20), "Sample Markets", "Quant Analyst Intern", "Confirmed"),
    (date(2026, 11, 2), "Rejecting Partners", "Trading Intern", "Rejected"),
    (date(2026, 11, 3), "Interviewing Group", "Research Intern", "Interview"),
]
