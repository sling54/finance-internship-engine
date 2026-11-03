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


# ---------- adding applications the tracker doesn't have yet ----------

from jobengine.cli import find_jobs  # noqa: E402
from jobengine.tracker_sync import firm_and_role, record_application  # noqa: E402


@pytest.mark.parametrize(
    "sender, subject, expected",
    [  # real subject lines from the inbox
        ("pimco@myworkday.com", "Thank you for applying to PIMCO!", ("PIMCO", "")),
        (
            "no-reply@us.greenhouse-mail.io",
            "Thank you for applying to Gelber Group for the Discretionary Trading Internship - "
            "Summer 2027 Opportunity",
            ("Gelber Group", "Discretionary Trading Internship - Summer 2027"),
        ),
        (
            "no-reply@citadel.com",
            "Your application for Citadel's Trading Fundamental Analyst - "
            "Intern (US) role has been received.",
            ("Citadel", "Trading Fundamental Analyst - Intern"),
        ),
        (
            "campusus@imc.com",
            "We've got it! Your application for Quantitative Trader Intern - "
            "Summer 2027 at IMC is underway",
            ("IMC", "Quantitative Trader Intern - Summer 2027"),
        ),
        (
            "x@people.bny.com",
            "Thank You for Applying to BNY - 81350 - 2027 BNY Summer Internship "
            "Program - Trading (New York, NY)",
            ("BNY", "2027 BNY Summer Internship Program - Trading"),
        ),
        ("x@flowtraders.jobs", "Thank You For Applying to Flow Traders US!", ("Flow Traders", "")),
        ("rbc@myworkday.com", "Thank You For Applying!", ("RBC", "")),
        (
            "blackstone@pymetrics.com",
            "Blackstone Application – Invitation to pymetrics Assessment",
            ("Blackstone", ""),
        ),
        ("x@mail.pageuppeople.com", "Application Received", None),
        ("invites@pymetrics.com", "Thank you for completing pymetrics!", None),
    ],
)
def test_firm_and_role_from_subject(sender, subject, expected):
    assert firm_and_role(sender, subject) == expected


def app_status(path, row):
    ws = load_workbook(path)["Applications"]
    header = [c.value for c in ws[6]]
    return [
        ws.cell(row, header.index(h) + 1).value
        for h in ("Company", "Role Applied To", "App Status")
    ]


def test_unknown_firms_and_new_roles_become_rows(tmp_path):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    gmail = FakeGmail(
        [
            email("c1", "pimco@myworkday.com", "Thank you for applying to PIMCO!", "", 1),
            email(
                "c2",
                "no-reply@hire.lever.co",
                "Thank you for your application to Belvedere "
                "Trading - Quantitative Trading Intern - Summer 2027",
                "",
                1,
            ),
            email(
                "c3",
                "no-reply@hire.lever.co",
                "Thank you for your application to Belvedere "
                "Trading - Quantitative Trading Intern - Winter Quarter 2027",
                "",
                1,
            ),
            email(
                "c4",
                "no-reply@hire.lever.co",
                "Thank you for your application to Belvedere "
                "Trading - Quantitative Trading Intern - Summer 2027",
                "",
                2,
            ),  # duplicate email
        ]
    )
    report = sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=gmail, pause=0)
    assert len(report.added) == 3 and report.review == 0
    assert app_status(path, 11) == ["PIMCO", None, "Confirmed"]
    assert app_status(path, 12)[:2] == [
        "Belvedere Trading",
        "Quantitative Trading Intern - Summer 2027",
    ]
    assert app_status(path, 13)[1] == "Quantitative Trading Intern - Winter Quarter 2027"
    assert app_status(path, 14) == [None, None, None]


def test_review_backlog_is_turned_into_rows(tmp_path):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    wb = load_workbook(path)
    review = wb.create_sheet("Review")
    review.append(["Email date", "From", "Subject", "Looks like", "What to do"])
    review.append(
        [
            datetime(2026, 10, 20),
            "no-reply@drwholdings.com",
            "Thank you for applying to DRW",
            "Confirmed",
            "",
        ]
    )
    review.append(
        [datetime(2026, 10, 21), "x@mail.pageuppeople.com", "Application Received", "Confirmed", ""]
    )
    wb.save(path)
    report = sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=FakeGmail([]), pause=0)
    assert report.added == ["DRW - (role not named): Confirmed"]
    left = [r[2] for r in load_workbook(path)["Review"].iter_rows(min_row=2, values_only=True)]
    assert left == ["Application Received"]


def test_record_application_without_email(tmp_path):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    msg = record_application(
        path,
        tmp_path,
        "Group One Trading",
        "Trading Analyst Intern",
        "https://group1.applicantpro.com/jobs/4192883",
        date(2026, 11, 2),
    )
    assert msg.startswith("Added") and app_status(path, 11)[2] == "Applied"
    again = record_application(path, tmp_path, "Group One Trading", "Trading Analyst Intern")
    assert again.startswith("Already in the tracker")


def test_find_jobs_by_words_or_url(tmp_path):
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_text(
        "company,title,url\nGelber Group,Discretionary Trading Internship,https://a\n"
        "DV Trading,Trading Intern - DV Commodities,https://b\n",
        encoding="utf-8-sig",
    )
    assert [r["company"] for r in find_jobs(csv_path, "gelber discretionary")] == ["Gelber Group"]
    assert [r["company"] for r in find_jobs(csv_path, "https://b")] == ["DV Trading"]
    assert len(find_jobs(csv_path, "trading")) == 2


def test_review_rejection_uses_stored_status(tmp_path):
    """Belvedere's rejection subject is just "Application Update"; the status read from the
    full email is kept in Review's "Looks like" column and used on the retry."""
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    wb = load_workbook(path)
    review = wb.create_sheet("Review")
    review.append(["Email date", "From", "Subject", "Looks like", "What to do"])
    lever = "no-reply@hire.lever.co"
    review.append(
        [
            datetime(2026, 10, 6),
            lever,
            "Thank you for your application to Belvedere "
            "Trading - Quantitative Trading Intern - Summer 2027",
            "Confirmed",
            "",
        ]
    )
    review.append(
        [
            datetime(2026, 10, 6),
            lever,
            "Thank you for your application to Belvedere "
            "Trading - Quantitative Trading Intern - Winter Quarter 2027",
            "Confirmed",
            "",
        ]
    )
    review.append(
        [
            datetime(2026, 10, 27),
            lever,
            "Belvedere Trading Application Update - Quantitative Trading Intern - Summer 2027",
            "Rejected",
            "",
        ]
    )
    wb.save(path)
    sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=FakeGmail([]), pause=0)
    assert app_status(path, 11)[1:] == ["Quantitative Trading Intern - Summer 2027", "Rejected"]
    assert app_status(path, 12)[2] == "Confirmed"  # the Winter Quarter role is untouched
    assert load_workbook(path)["Review"].max_row == 1  # all three cleared


@pytest.mark.parametrize("existing_row", [True, False])
def test_sync_uses_domain_alias_for_rejection(tmp_path, existing_row):
    path = tmp_path / "ApplicationTracker.xlsx"
    make_tracker(path)
    if existing_row:
        wb = load_workbook(path)
        wb["Applications"].append([datetime(2026, 10, 30), "Northern Trust", "Summer Intern"])
        wb.save(path)

    gmail = FakeGmail(
        [
            email(
                "ntrs-rejection",
                "Recruiting <careers@ntrs.com>",
                "Your application",
                "We have decided to move forward with other candidates.",
                2,
            )
        ]
    )
    report = sync({}, path, tmp_path, today=date(2026, 11, 2), gmail=gmail, pause=0)

    ws = load_workbook(path)["Applications"]
    status_col = [cell.value for cell in ws[6]].index("App Status") + 1
    assert ws.max_row == 11
    assert ws["B11"].value == "Northern Trust"
    assert ws.cell(11, status_col).value == "Rejected"
    assert len(report.updated) == int(existing_row)
    assert len(report.added) == int(not existing_row)
