import base64
from datetime import date, datetime

import pytest
from openpyxl import Workbook, load_workbook

from jobengine.tracker_sync import can_move, classify, match_company, sync

# ---------- classifying emails ----------


@pytest.mark.parametrize(
    "subject, body, expected",
    [
        ("Thank you for applying to Example Capital", "We will review it.", "Confirmed"),
        (
            "Application received",
            "Due to volume, unfortunately we cannot reply to everyone.",
            "Confirmed",
        ),
        (
            "Your Example Capital application",
            "After careful review, we have decided to move forward with other candidates.",
            "Rejected",
        ),
        (
            "Update",
            "Unfortunately, we have decided not to proceed with your application.",
            "Rejected",
        ),
        ("Next steps", "Please complete your HireVue interview within 5 days.", "Assessment"),
        ("Next steps", "We'd like to invite you to complete the online assessment.", "Assessment"),
        ("Interview invitation", "We would like to schedule an interview with you.", "Interview"),
        ("Superday", "You have been selected for our superday.", "Interview"),
        (
            "Congratulations",
            "We are pleased to extend an offer for the summer internship.",
            "Offer",
        ),
        ("Weekly newsletter", "Markets were volatile this week.", None),
        ("$375k Quant Offer Breakdown", "We're excited to offer you 50% off our course.", None),
        ("CTC invites you to complete a Criteria Assessment - QT Intern", "", "Assessment"),
        ("Reminder to complete Skills Insight Assessment", "", "Assessment"),
        (
            "We've got it! Your application for Quant Trader Intern at IMC is underway",
            "",
            "Confirmed",
        ),
    ],
)
def test_classify(subject, body, expected):
    assert classify(subject, body) == expected


def test_ladder_moves_forward_and_closed_rows_stay_closed():
    assert can_move("Applied", "Assessment")
    assert not can_move("Interview", "Confirmed")  # never backwards
    assert can_move("Interview", "Rejected")
    assert not can_move("Rejected", "Interview")
    assert not can_move("Offer", "Rejected")


def test_match_company_by_name_and_domain():
    companies = ["Verition", "Loop Capital", "DV Trading", "Jane Street"]
    assert (
        match_company("Recruiting <careers@veritionfund.com>", "Update", "", companies)
        == "Verition"
    )
    assert (
        match_company("noreply@myworkday.com", "Your Loop Capital application", "", companies)
        == "Loop Capital"
    )
    assert (
        match_company("noreply@greenhouse.io", "Thanks for applying to DV Trading", "", companies)
        == "DV Trading"
    )
    assert match_company("noreply@greenhouse.io", "Thanks for applying", "", companies) is None
    # A firm named only in a newsletter's body is not a match...
    assert (
        match_company(
            "resources@quantnewsletter.com",
            "Interview tips",
            "How we prepped for Jane Street",
            companies,
        )
        is None
    )
    # ...but an applicant-tracking email that names it in the body is.
    assert (
        match_company(
            "no-reply@us.greenhouse-mail.io",
            "Update on your application",
            "Thank you for your interest in Jane Street.",
            companies,
        )
        == "Jane Street"
    )


# ---------- end to end, on a workbook laid out like the real tracker ----------


def make_tracker(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    ws.append(["Trading & Investment Application Tracker"])
    ws.append(["Add a row the day you submit."])
    ws.append([])
    ws.append(["Applications", "=COUNTA(B7:B100)"])
    ws.append([])
    ws.append(
        [
            "Application Date",
            "Company",
            "Role Applied To",
            "Company LinkedIn",
            "Connection 1",
            "Email 1",
            "LinkedIn 1",
            "Status 1",
            "Follow-Up 1",
            "Connection 2",
            "Email 2",
            "LinkedIn 2",
            "Status 2",
            "Follow-Up 2",
            "Notes",
        ]
    )
    ws.append([datetime(2026, 10, 2), "Verition", "2027 Investment Internship"])
    ws.append([datetime(2026, 10, 2), "Loop Capital", "Asset Management 2027 Summer Intern"])
    ws.append([datetime(2026, 10, 30), "DV Trading", "Trading Intern - DV Commodities"])
    ws.append([datetime(2026, 10, 30), "DV Trading", "Quantitative Trading Intern - DV Equities"])
    ws["Q7"] = "stray note"  # real tracker has stray cells past Notes; new columns skip them
    todo = wb.create_sheet("To Apply")
    todo.append(["#", "Group", "Company", "Role", "Location", "Deadline", "Days Left", "Status"])
    wb.save(path)


class FakeGmail:
    """Stands in for the Gmail API client: list() -> ids, get() -> full messages."""

    def __init__(self, messages):
        self.messages_ = messages

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        self._result = {"messages": [{"id": m["id"]} for m in self.messages_]}
        return self

    def get(self, userId, id, format):
        self._result = next(m for m in self.messages_ if m["id"] == id)
        return self

    def execute(self, num_retries=0):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def email(mid, sender, subject, body, day):
    data = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": mid,
        "internalDate": str(int(datetime(2026, 11, day, 12).timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": data},
            "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
        },
    }


def test_sync_updates_rows_reviews_unknowns_and_reminds(tmp_path):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    gmail = FakeGmail(
        [
            email(
                "a1",
                "careers@veritionfund.com",
                "Your application",
                "We have decided to move forward with other candidates.",
                1,
            ),
            email(
                "a2",
                "no-reply@greenhouse.io",
                "Next steps with DV Trading",
                "Please complete your HireVue for the DV Commodities Trading Intern role.",
                2,
            ),
            email(
                "a3",
                "talent@unknownfund.com",
                "Thank you for applying",
                "We received your application.",
                2,
            ),
            email(
                "a4", "jobs-noreply@linkedin.com", "Your application was sent to Other Co", "", 2
            ),
        ]
    )
    report = sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=gmail, pause=0)

    ws = load_workbook(path)["Applications"]
    header = [c.value for c in ws[6]]
    col = {name: header.index(name) + 1 for name in ("App Status", "Last Email", "Next Action")}
    assert header.index("App Status") + 1 > 17  # placed after the stray cell in column Q
    assert ws.cell(7, col["App Status"]).value == "Rejected"  # Verition, matched by domain
    assert ws.cell(9, col["App Status"]).value == "Assessment"  # the Commodities row, not Equities
    assert ws.cell(10, col["App Status"]).value == "Applied"
    assert ws.cell(8, col["Next Action"]).value.startswith("Follow up")  # Loop: 31 days, no email
    assert ws["Q7"].value == "stray note" and ws["B4"].value == "=COUNTA(B7:B100)"

    review = load_workbook(path)["Review"]
    assert [r[1] for r in review.iter_rows(min_row=2, values_only=True)] == [
        "talent@unknownfund.com"
    ]
    assert report.emails == 4 and len(report.updated) == 2 and report.review == 1
    assert list((tmp_path / "data" / "backups").iterdir())  # backup written first

    # Running again reads nothing new and changes no status.
    again = sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=gmail, pause=0)
    assert again.emails == 0 and again.updated == []


def test_sync_refuses_while_open_in_excel(tmp_path):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    (tmp_path / "~$ApplicationTracker.xlsx").write_text("")
    assert sync({}, path, tmp_path, gmail=FakeGmail([])).skipped_open


def test_sync_saves_progress_when_gmail_stops_answering(tmp_path):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    ok = email(
        "b1",
        "careers@veritionfund.com",
        "Your application",
        "We have decided to move forward with other candidates.",
        1,
    )
    gmail = FakeGmail([ok, {"id": "b2"}])
    original_get = gmail.get

    def get(userId, id, format):
        if id == "b2":
            gmail._result = RuntimeError("rateLimitExceeded")
            return gmail
        return original_get(userId, id, format)

    gmail.get = get
    report = sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=gmail, pause=0)
    assert report.emails == 1 and report.left == 1  # first email applied, second left for later
    ws = load_workbook(path)["Applications"]
    header = [c.value for c in ws[6]]
    assert ws.cell(7, header.index("App Status") + 1).value == "Rejected"  # saved despite the stop
