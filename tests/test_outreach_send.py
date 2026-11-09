import base64
import random
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from http.client import HTTPConnection
from threading import Thread
from urllib.parse import urlencode

import pytest
from openpyxl import load_workbook
from outreach_fakes import DEFAULT_ROWS, EXAMPLE_CAPITAL, PROFILE, FakeGmail, FakeHTTP, make_tracker

from jobengine.config import CONFIG_DIR
from jobengine.outreach import load_settings, load_templates
from jobengine.outreach.hunter import Hunter
from jobengine.outreach.plan import plan
from jobengine.outreach.review import review_server
from jobengine.outreach.send import send_approved
from jobengine.outreach.store import Log
from jobengine.tracker_sync import SyncReport, Tracker, apply_reminders, sync

TODAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 9, 0, 0)
SETTINGS = load_settings(CONFIG_DIR)


@pytest.fixture
def drafted(tmp_path):
    """A project after `outreach plan`: two drafts for Example Capital in a fake Gmail."""
    make_tracker(tmp_path / PROFILE["tracker_path"], DEFAULT_ROWS)
    (tmp_path / PROFILE["resume_path"]).write_bytes(b"%PDF-1.4 fake resume")
    gmail = FakeGmail()
    h = Hunter("k", tmp_path, http=FakeHTTP({"Example Capital": EXAMPLE_CAPITAL}), today=TODAY)
    plan(
        tmp_path,
        SETTINGS,
        load_templates(CONFIG_DIR),
        PROFILE,
        tmp_path / PROFILE["tracker_path"],
        h,
        gmail=gmail,
        today=TODAY,
    )
    return tmp_path, gmail


def approve_all(root, when=NOW):
    log = Log.load(root)
    for e in log.entries.values():
        e.status, e.approved_at = "approved", when.isoformat()
    log.save()
    return log


class Clock:
    def __init__(self, start=NOW):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += timedelta(seconds=seconds)

    slept: list


def clock():
    c = Clock()
    c.slept = []
    return c


# ---------- sending ----------


def test_only_approved_drafts_are_sent(drafted):
    root, gmail = drafted
    log = Log.load(root)
    first = next(iter(log.entries.values()))
    first.status = "approved"
    log.save()
    c = clock()
    report = send_approved(gmail, Log.load(root), SETTINGS, now=c, sleep=c.sleep)
    assert len(report.sent) == 1 and len(gmail.sent) == 1
    assert gmail.sent[0][0] == first.draft_id
    statuses = sorted(e.status for e in Log.load(root).entries.values())
    assert statuses == ["drafted", "sent"]


def test_nothing_is_sent_without_approval(drafted):
    root, gmail = drafted
    report = send_approved(gmail, Log.load(root), SETTINGS, now=clock(), sleep=lambda s: None)
    assert gmail.sent == [] and report.summary() == "No approved outreach drafts to send."


def test_sends_the_draft_as_edited_in_gmail(drafted):
    root, gmail = drafted
    log = approve_all(root)
    draft_id = next(iter(log.entries.values())).draft_id
    edited = EmailMessage()
    edited["To"], edited["Subject"] = "riley.stone@examplecapital.com", "Edited subject"
    edited.set_content("My own words.")
    gmail.drafts_[draft_id]["message"]["raw"] = base64.urlsafe_b64encode(edited.as_bytes()).decode()

    send_approved(gmail, log, SETTINGS, now=clock(), sleep=lambda s: None)
    sent = dict(gmail.sent)[draft_id]
    assert base64.urlsafe_b64decode(sent["message"]["raw"]).decode().count("My own words.") == 1


def test_sent_entries_record_thread_and_time_and_pause_between_sends(drafted):
    root, gmail = drafted
    c = clock()
    send_approved(gmail, approve_all(root), SETTINGS, now=c, sleep=c.sleep, rng=random.Random(1))
    assert len(c.slept) == 1 and 45 <= c.slept[0] <= 120  # between the two sends only
    for e in Log.load(root).entries.values():
        assert e.status == "sent" and e.thread_id.startswith("t") and e.sent_at


def test_daily_limit_holds_across_runs(drafted):
    root, gmail = drafted
    settings = dict(SETTINGS, daily_send_limit=1)
    c = clock()
    first = send_approved(gmail, approve_all(root), settings, now=c, sleep=c.sleep)
    assert len(first.sent) == 1 and first.waiting == 1
    assert "1 approved drafts wait for tomorrow" in first.summary()

    c.t += timedelta(hours=23)  # still inside the 24 hours
    second = send_approved(gmail, Log.load(root), settings, now=c, sleep=c.sleep)
    assert second.sent == [] and second.waiting == 1

    c.t += timedelta(hours=2)
    third = send_approved(gmail, Log.load(root), settings, now=c, sleep=c.sleep)
    assert len(third.sent) == 1 and len(gmail.sent) == 2


def test_oldest_approval_goes_first(drafted):
    root, gmail = drafted
    log = approve_all(root)
    entries = list(log.entries.values())
    entries[1].approved_at = (NOW - timedelta(days=1)).isoformat()
    log.save()
    send_approved(
        gmail, Log.load(root), dict(SETTINGS, daily_send_limit=1), now=clock(), sleep=lambda s: None
    )
    assert gmail.sent[0][0] == entries[1].draft_id


def test_draft_deleted_or_sent_by_hand_in_gmail(drafted):
    root, gmail = drafted
    log = approve_all(root)
    deleted, by_hand = list(log.entries.values())
    del gmail.drafts_[deleted.draft_id]
    del gmail.drafts_[by_hand.draft_id]  # you sent it yourself from Gmail:
    gmail.messages_[by_hand.message_id] = {
        "id": by_hand.message_id,
        "threadId": "t77",
        "labelIds": ["SENT"],
    }
    report = send_approved(gmail, log, SETTINGS, now=clock(), sleep=lambda s: None)
    assert gmail.sent == [] and len(report.gone) == 2
    after = Log.load(root).entries
    assert after[deleted.key].status == "deleted"
    assert (after[by_hand.key].status, after[by_hand.key].thread_id) == ("sent", "t77")


# ---------- the review page ----------


@pytest.fixture
def page(drafted):
    root, gmail = drafted
    log = Log.load(root)
    with review_server(gmail, log, now=lambda: NOW) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port, root, gmail, log
        finally:
            server.shutdown()
            thread.join()


def request(port, method="GET", path="/", body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read().decode()
    finally:
        conn.close()


def token_of(html):
    return html.split('name="token" value="')[1].split('"')[0]


def post(port, path, token, entry, **headers):
    base = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": f"http://127.0.0.1:{port}",
    }
    base.update(headers)
    return request(port, "POST", path, urlencode({"token": token, "entry": entry}), base)


def test_review_shows_drafts_as_they_are_in_gmail(page):
    port, root, gmail, log = page
    status, html = request(port)
    assert status == 200
    assert "riley.stone@examplecapital.com" in html and "Trading Intern application" in html
    assert "#drafts?compose=" in html and "Approve" in html and "Skip" in html


def test_review_approve_and_skip(page):
    port, root, gmail, log = page
    _, html = request(port)
    approve, skip = list(log.entries)
    assert post(port, "/approve", token_of(html), approve)[0] == 303
    assert post(port, "/skip", token_of(html), skip)[0] == 303
    saved = Log.load(root).entries
    assert saved[approve].status == "approved" and saved[approve].approved_at == NOW.isoformat()
    assert saved[skip].status == "skipped" and saved[skip].draft_id not in gmail.drafts_
    _, html = request(port)
    assert "No drafts waiting for review." in html and "1 approved" in html


def test_review_marks_drafts_deleted_in_gmail(page):
    port, root, gmail, log = page
    gone = next(iter(log.entries.values()))
    del gmail.drafts_[gone.draft_id]
    _, html = request(port)
    assert "draft deleted in Gmail" in html
    assert Log.load(root).entries[gone.key].status == "deleted"


@pytest.mark.parametrize(
    "headers,good_token",
    [({"Host": "example.com"}, True), ({"Origin": "https://example.com"}, True), ({}, False)],
)
def test_review_rejects_foreign_requests(page, headers, good_token):
    port, root, gmail, log = page
    _, html = request(port)
    token = token_of(html) if good_token else "wrong"
    entry = next(iter(log.entries))
    assert post(port, "/approve", token, entry, **headers)[0] == 403
    assert Log.load(root).entries[entry].status == "drafted"


def test_review_rejects_foreign_host_on_get(page):
    port, *_ = page
    assert request(port, headers={"Host": "example.com"})[0] == 403


# ---------- replies and bounces in `jobengine track` ----------


def sent_project(drafted):
    root, gmail = drafted
    send_approved(gmail, approve_all(root), SETTINGS, now=clock(), sleep=lambda s: None)
    return root, gmail, list(Log.load(root).entries.values())


def test_track_marks_replies_and_bounces_without_touching_app_status(drafted):
    root, gmail, (riley, sam) = sent_project(drafted)
    inbox = gmail
    inbox.threads_[riley.thread_id].append(("m900", "Riley Stone <riley.stone@examplecapital.com>"))
    inbox.threads_[sam.thread_id].append(
        ("m901", "Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
    )

    tracker = root / PROFILE["tracker_path"]
    report = sync({}, tracker, root, today=TODAY, gmail=inbox, pause=0)
    assert report.outreach == [
        "Example Capital: reply from Riley Stone",
        "Example Capital: sam.lee@examplecapital.com bounced",
    ]
    assert "Outreach: 2 replies or bounces." in report.summary()
    after = Log.load(root).entries
    assert (after[riley.key].status, after[sam.key].status) == ("replied", "bounced")

    t = Tracker(tracker)
    row = next(r for r in t.rows() if r.company == "Example Capital")
    assert row.status == "Applied"  # App Status never changes
    assert t.get(row, "Next Action") == "Outreach reply from Riley Stone: respond"
    sheet = load_workbook(tracker)["Outreach"]
    assert sorted(sheet.cell(r, 7).value for r in (2, 3)) == ["Bounced", "Replied"]


def test_own_follow_up_in_the_thread_is_not_a_reply(drafted):
    root, gmail, (riley, _) = sent_project(drafted)
    inbox = gmail
    inbox.threads_[riley.thread_id].append(("m902", f"Alex Example <{gmail.me}>"))
    report = sync({}, root / PROFILE["tracker_path"], root, today=TODAY, gmail=inbox, pause=0)
    assert report.outreach == []
    assert Log.load(root).entries[riley.key].status == "sent"


def test_reply_note_survives_the_ghosted_reminder(tmp_path):
    path = make_tracker(
        tmp_path / "t.xlsx", [(date(2026, 9, 1), "Example Capital", "Trading Intern", "Applied")]
    )
    t = Tracker(path)
    rows = t.rows()
    t.set(rows[0], "Next Action", "Outreach reply from Riley Stone: respond")
    apply_reminders(t, rows, TODAY, SyncReport())
    assert t.get(rows[0], "Next Action") == "Outreach reply from Riley Stone: respond"


def test_track_survives_a_broken_outreach_log(tmp_path):
    path = make_tracker(tmp_path / "t.xlsx", DEFAULT_ROWS)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "outreach.json").write_text("{not json")
    report = sync({}, path, tmp_path, today=TODAY, gmail=FakeGmail(), pause=0)
    assert report.outreach == [] and not report.skipped_open


# ---------- the commands ----------


@pytest.mark.parametrize(
    "command,message",
    [("send", "No approved outreach drafts to send."), ("review", "No outreach drafts waiting")],
)
def test_commands_do_nothing_without_drafts(tmp_path, monkeypatch, capsys, command, message):
    from jobengine import cli
    from jobengine.config import Config

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    load = Config.load
    monkeypatch.setattr(cli.Config, "load", lambda: load(CONFIG_DIR))
    monkeypatch.setattr("jobengine.gmail.service", lambda *a: pytest.fail("Gmail was opened"))
    assert cli.main(["outreach", command]) == 0
    assert message in capsys.readouterr().out
