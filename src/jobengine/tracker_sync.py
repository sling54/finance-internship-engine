"""Feature 6: keep ApplicationTracker.xlsx up to date from Gmail.

Every run reads recent job emails, works out the new status (Confirmed, Assessment, Interview,
Offer, Rejected) with fixed rules, matches the email to a tracker row by company, and moves that
row forward. A row never moves backwards, except to Rejected. Emails that look like application
updates but match no row go to a Review sheet. Firms that never write get follow-up reminders.

Your existing columns are left alone; four columns are added next to them the first time:
App Status, Status Date, Last Email, Next Action. A backup is saved before every write, and
nothing is written while the file is open in Excel.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from copy import copy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .filters.dedup import company_key
from .gmail import header, plain_text, service
from .textutil import normalize

log = logging.getLogger(__name__)

LADDER = ["Applied", "Confirmed", "Assessment", "Interview", "Offer"]
CLOSED = {"Rejected", "Withdrawn", "Offer"}  # set by an email or by you; never overwritten
NEW_COLUMNS = ["App Status", "Status Date", "Last Email", "Next Action", "Job Link"]

# Checked in this order; the first that matches wins. Phrases are specific on purpose: a
# confirmation that says "unfortunately we can't reply to everyone" must not read as a rejection.
RULES: list[tuple[str, re.Pattern]] = [
    (
        "Offer",
        re.compile(
            r"\b(pleased|delighted|excited|happy) to (extend|offer) you (an? )?"
            r"(offer|the position|a position|an internship|a summer|the internship)|"
            r"\boffer letter\b|\bextend(ing)? (you )?an offer\b",
            re.I,
        ),
    ),
    (
        "Rejected",
        re.compile(
            r"move forward with other (candidates|applicants)|pursue other (candidates|applicants)|"
            r"not (be )?(moving|proceeding) forward|decided not to (move|proceed)|regret to inform|"
            r"(have|has) not been selected|were not selected|"
            r"no longer (under consideration|being considered)|"
            r"position has (been|since been) filled|other candidates whose (experience|qualifications)|"
            r"unfortunately[^.]{0,120}\b(decided|other candidates|"
            r"not (be )?(able to offer|moving|selected)|unable to offer)",
            re.I,
        ),
    ),
    (
        "Interview",
        re.compile(
            r"invit\w* (you )?(to|for) (an?|your|a first|a final|the next)?\s*(round of )?"
            r"(interviews?|superday)|"
            r"schedul\w* (an?|your|the) (\w+ )?interview|interview (invitation|request|availability)|"
            r"\bsuperday\b|phone screen|first[- ]round interview|final[- ]round",
            re.I,
        ),
    ),
    (
        "Assessment",
        re.compile(
            r"\b(hirevue|pymetrics|codesignal|hackerrank|karat|harver|mettl|imocha)\b|"
            r"criteria (corp|assessment)|skills insight assessment|"
            r"(complete|take) (a|the|your|an|our) (\w+ )?(online |technical |coding )?"
            r"(assessment|challenge|test)|assessment invitation|online assessment",
            re.I,
        ),
    ),
    (
        "Confirmed",
        re.compile(
            r"thank(s| you) for (applying|your application|submitting)|"
            r"application (has been |was )?(received|submitted|sent)|received your application|"
            r"application confirmation|your application (to|for|with) ",
            re.I,
        ),
    ),
]

# Job boards: their emails can confirm an application, but unmatched ones aren't worth review.
BOARD_DOMAINS = (
    "linkedin.com",
    "indeed.com",
    "joinhandshake.com",
    "efinancialcareers.com",
    "ziprecruiter.com",
    "glassdoor.com",
    "simplify.jobs",
)

# Applicant-tracking and assessment services. Their emails name the firm in the body, so a body
# match counts for them; for any other sender (newsletters, ads) the firm must be named in the
# sender or subject, or "excited to offer you 50% off our Citadel guide" would read as an offer.
ATS_DOMAINS = (
    "greenhouse.io",
    "greenhouse-mail.io",
    "lever.co",
    "ashbyhq.com",
    "myworkday.com",
    "myworkdayjobs.com",
    "icims.com",
    "smartrecruiters.com",
    "jobvite.com",
    "taleo.net",
    "successfactors.com",
    "oraclecloud.com",
    "pymetrics.com",
    "pymetrics.ai",
    "hirevue.com",
    "criteriacorp.com",
    "shl.com",
    "codesignal.com",
    "hackerrank.com",
    "applicantemails.com",
    "pageuppeople.com",
    "profils.org",
    "workablemail.com",
)

SEARCH = (
    '{"thank you for applying" "your application" "application received" candidacy '
    "hirevue pymetrics codesignal hackerrank assessment interview superday unfortunately "
    '"other candidates" "next steps" offer}'
)


def classify(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body[:4000]}"
    for status, pattern in RULES:
        if pattern.search(text):
            return status
    return None


def can_move(current: str, new: str) -> bool:
    """Forward only on the ladder; Rejected from anywhere open; closed rows stay closed."""
    if current in CLOSED or current == new:
        return False
    if new == "Rejected":
        return True
    rank = {s: i for i, s in enumerate(LADDER)}
    return rank.get(new, -1) > rank.get(current, 0)


def _domain(sender: str) -> str:
    m = re.search(r"@([\w.-]+)", sender)
    return m.group(1).lower() if m else ""


def match_company(sender: str, subject: str, body: str, companies: list[str]) -> str | None:
    """The tracker company this email is about: its name in the sender or subject, its name at
    the start of the sender's domain (veritionfund.com -> Verition), or, for applicant-tracking
    senders only, its name in the body. Longest name wins."""
    strong = f" {normalize(sender)} {normalize(subject)} "
    weak = f" {normalize(body[:3000])} " if _domain(sender).endswith(ATS_DOMAINS) else ""
    domain = _domain(sender).split(".")[0]
    best = None
    for company in companies:
        key = company_key(company)
        if not key:
            continue
        squashed = key.replace(" ", "")
        hit = (
            f" {key} " in strong
            or f" {key} " in weak
            or (len(squashed) >= 4 and domain.startswith(squashed))
        )
        if hit and (best is None or len(key) > len(company_key(best))):
            best = company
    return best


# ---------- reading firm and role from a subject line ----------

_END = r"(?=\s*(?:[!.,|]|\s-\s|\s–\s|\(|$| for the | for our | for your | role\b| position\b))"
SUBJECT_PATTERNS = [
    # "Your application for Citadel's Trading Fundamental Analyst - Intern (US) role has been ..."
    re.compile(
        r"application for (?P<company>[\w&.' ]+?)'s (?P<role>.+?) (?:role|position)\b", re.I
    ),
    # "Your application for Quantitative Trader Intern - Summer 2027 at IMC is underway"
    re.compile(
        r"application for (?P<role>.+?) at (?P<company>[\w&.' ]+?)(?= is | has |[!.,]|$)", re.I
    ),
    # "Thank you for applying to Gelber Group for the Discretionary Trading Internship - ..."
    re.compile(
        r"(?:applying|application|apply) (?:to|with) (?P<company>[\w&.' ]+?) for the "
        r"(?P<role>.+?)(?: opportunity| position| role|$)",
        re.I,
    ),
    # "Thank you for your application to Belvedere Trading - Quantitative Trading Intern - ..."
    # "Thank You for Applying to BNY - 81350 - 2027 BNY Summer Internship Program - Trading"
    re.compile(
        r"(?:applying|application) (?:to|with) (?P<company>[\w&.' ]+?)\s[-–]\s"
        r"(?:\d+\s[-–]\s)?(?P<role>.+)$",
        re.I,
    ),
    # "Thank you for applying to PIMCO!", "Thank you for applying with Group One Trading"
    re.compile(r"(?:applying|application|apply) (?:to|with) (?P<company>[\w&.' ]+?)" + _END, re.I),
    # "Blackstone Application – Invitation to pymetrics ..."
    re.compile(r"^(?P<company>[\w&.' ]+?) application\s*[-–:]", re.I),
]
_NOT_COMPANIES = {"the", "our", "your", "this", "a", "an", "us", "you", "position", "role", "job"}


def firm_and_role(sender: str, subject: str) -> tuple[str, str] | None:
    """Firm and role named in an application email's subject, if it names them clearly."""
    for pattern in SUBJECT_PATTERNS:
        m = pattern.search(subject.strip())
        if not m:
            continue
        company = m.group("company").strip(" '.")
        company = re.sub(
            r"\s+(US|USA|LLC|Inc|Ltd|LP|Internships?|Careers|Campus|Recruiting)$",
            "",
            company,
            flags=re.I,
        )
        if len(company) < 2 or company.lower() in _NOT_COMPANIES:
            continue
        role = (m.groupdict().get("role") or "").strip(" -–!.")
        role = re.sub(r"\s*\((?:[^)]*,\s*)?[A-Z]{2}\)$", "", role)  # trailing "(New York, NY)"
        return company, role
    # Workday sends from <firm>@myworkday.com with a bare "Thank You For Applying!"
    m = re.search(r"^\W*([a-z0-9]+)@myworkday\.com", sender.split("<")[-1], re.I)
    if m:
        return m.group(1).upper() if len(m.group(1)) <= 4 else m.group(1).title(), ""
    return None


# ---------- the spreadsheet ----------


@dataclass
class Row:
    index: int
    company: str
    role: str
    applied: date | None
    status: str


class Tracker:
    def __init__(self, path: Path):
        from openpyxl import load_workbook

        self.path = path
        self.wb = load_workbook(path)
        self.ws, self.header_row, self.cols = self._find_sheet()
        self._ensure_columns()

    def _find_sheet(self):
        for ws in self.wb.worksheets:
            for r in range(1, 21):
                names = {normalize(str(c.value or "")): c.column for c in ws[r]}
                company = next(
                    (v for k, v in names.items() if k.startswith(("company", "firm"))), None
                )
                role = next(
                    (v for k, v in names.items() if k.startswith(("role", "position", "title"))),
                    None,
                )
                when = next((v for k, v in names.items() if "date" in k.split()), None)
                # The applications sheet has an application date; a "To Apply" list doesn't.
                if company and role and when:
                    return (
                        ws,
                        r,
                        {
                            "company": company,
                            "role": role,
                            "date": when,
                            **{normalize(k): v for k, v in names.items() if k},
                        },
                    )
        raise ValueError(f"no sheet with Company, Role and a date column in {self.path.name}")

    def _ensure_columns(self) -> None:
        from openpyxl.worksheet.datavalidation import DataValidation

        missing = [c for c in NEW_COLUMNS if normalize(c) not in self.cols]
        if not missing:
            return
        # First run of columns past the existing ones with nothing in them, in any row.
        start = max(self.cols.values()) + 1
        while any(
            self.ws.cell(r, c).value not in (None, "")
            for r in range(1, self.ws.max_row + 1)
            for c in range(start, start + len(missing))
        ):
            start += 1
        for offset, name in enumerate(missing):
            cell = self.ws.cell(self.header_row, start + offset, name)
            cell.font = copy(self.ws.cell(self.header_row, self.cols["company"]).font)
            self.cols[normalize(name)] = start + offset
        col = self.cols["app status"]
        letter = self.ws.cell(1, col).column_letter
        dv = DataValidation(
            type="list",
            allow_blank=True,
            formula1='"' + ",".join(LADDER + ["Rejected", "Withdrawn", "Ghosted"]) + '"',
        )
        dv.add(f"{letter}{self.header_row + 1}:{letter}{self.header_row + 500}")
        self.ws.add_data_validation(dv)

    def rows(self) -> list[Row]:
        out = []
        for r in range(self.header_row + 1, self.ws.max_row + 1):
            company = self.ws.cell(r, self.cols["company"]).value
            if not company:
                continue
            raw = self.ws.cell(r, self.cols["date"]).value
            applied = (
                raw.date() if isinstance(raw, datetime) else raw if isinstance(raw, date) else None
            )
            status = self.ws.cell(r, self.cols["app status"]).value or "Applied"
            out.append(
                Row(
                    r,
                    str(company),
                    str(self.ws.cell(r, self.cols["role"]).value or ""),
                    applied,
                    str(status),
                )
            )
        return out

    def set(self, row: Row, column: str, value) -> None:
        self.ws.cell(row.index, self.cols[normalize(column)], value)

    def get(self, row: Row, column: str):
        return self.ws.cell(row.index, self.cols[normalize(column)]).value

    def add_row(self, when: date, company: str, role: str, status: str, link: str = "") -> Row:
        """Append an application below the last filled row, formatted like the rows above."""
        last = max([r.index for r in self.rows()] or [self.header_row])
        index = last + 1
        row = Row(index, company, role, when, status)
        values = {"date": when, "company": company, "role": role}
        for key, value in values.items():
            cell = self.ws.cell(index, self.cols[key], value)
            if last > self.header_row:
                above = self.ws.cell(last, self.cols[key])
                cell.number_format = above.number_format
                cell.font = copy(above.font)
                cell.alignment = copy(above.alignment)
        self.set(row, "App Status", status)
        self.set(row, "Status Date", when)
        if link:
            self.set(row, "Job Link", link)
        return row

    def review_items(self) -> list[tuple[int, date, str, str, str]]:
        if "Review" not in self.wb.sheetnames:
            return []
        ws = self.wb["Review"]
        items = []
        for r in range(2, ws.max_row + 1):
            when, sender, subject, status = (ws.cell(r, c).value for c in range(1, 5))
            if subject:
                day = when.date() if isinstance(when, datetime) else when
                items.append((r, day, str(sender or ""), str(subject), str(status or "")))
        return items

    def drop_review_rows(self, indexes: list[int]) -> None:
        for r in sorted(indexes, reverse=True):
            self.wb["Review"].delete_rows(r)

    def add_review(self, when: date, sender: str, subject: str, status: str) -> None:
        if "Review" not in self.wb.sheetnames:
            ws = self.wb.create_sheet("Review")
            ws.append(["Email date", "From", "Subject", "Looks like", "What to do"])
        self.wb["Review"].append(
            [when, sender, subject, status, "Add the application to the tracker, or ignore"]
        )

    def save(self, backup_dir: Path) -> None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(self.path, backup_dir / f"{self.path.stem}_{stamp}{self.path.suffix}")
        self.wb.save(self.path)


def is_open_in_excel(path: Path) -> bool:
    return (path.parent / f"~${path.name}").exists()


_ROLE_NOISE = {"intern", "internship", "analyst", "program", "the", "and", "of", "us", "-"}
_SEASONS = {"summer", "winter", "fall", "autumn", "spring", "quarter", "off", "cycle"}


def same_role(a: str, b: str) -> bool:
    """True when two role titles are the same job, ignoring years and filler words.
    Different terms ("Summer 2027" vs "Winter Quarter 2027") are different jobs.
    An empty title (the email didn't name the role) counts as the same."""
    wa = {w for w in normalize(a).split() if not w.isdigit()} - _ROLE_NOISE
    wb = {w for w in normalize(b).split() if not w.isdigit()} - _ROLE_NOISE
    if not wa or not wb:
        return True
    sa, sb = wa & _SEASONS, wb & _SEASONS
    if sa and sb and sa != sb:
        return False
    wa, wb = wa - _SEASONS, wb - _SEASONS
    if not wa or not wb:
        return True
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def pick_row(rows: list[Row], company: str, subject: str, body: str) -> Row:
    """Among this company's rows, the one whose role shares the most words with the email."""
    candidates = [r for r in rows if r.company == company]
    words = set(normalize(f"{subject} {body[:2000]}").split())

    def overlap(r: Row) -> tuple[int, date]:
        return len(set(normalize(r.role).split()) & words), r.applied or date.min

    return max(candidates, key=overlap)


# ---------- recording an application yourself ----------


def record_application(
    tracker_path: Path,
    root: Path,
    company: str,
    role: str,
    link: str = "",
    when: date | None = None,
) -> str:
    """Add an application you just submitted, for firms that never send a confirmation."""
    if is_open_in_excel(tracker_path):
        return "Tracker is open in Excel; close it and run again (nothing was changed)."
    tracker = Tracker(tracker_path)
    for row in tracker.rows():
        if company_key(row.company) == company_key(company) and same_role(row.role, role):
            if not role or not row.role or normalize(row.role) == normalize(role):
                return f"Already in the tracker: {row.company} - {row.role} (row {row.index})."
    row = tracker.add_row(when or date.today(), company, role, "Applied", link)
    tracker.save(root / "data" / "backups")
    return f"Added: {company} - {role} (row {row.index}), status Applied."


# ---------- the sync ----------


@dataclass
class SyncReport:
    emails: int = 0
    updated: list[str] = field(default_factory=list)
    review: int = 0
    reminders: int = 0
    skipped_open: bool = False
    left: int = 0  # emails not read because Gmail stopped answering
    added: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped_open:
            return "Tracker is open in Excel; close it and run again (nothing was changed)."
        lines = [
            f"Read {self.emails} new job emails. Added {len(self.added)} applications, "
            f"updated {len(self.updated)} rows, "
            f"{self.review} sent to Review, {self.reminders} follow-up reminders."
        ]
        lines += [f"  + {a}" for a in self.added]
        lines += [f"  - {u}" for u in self.updated]
        if self.left:
            lines.append(
                f"Gmail paused us with {self.left} emails left; run again in a minute "
                "to finish (progress is saved)."
            )
        return "\n".join(lines)


def apply_email(
    tracker: Tracker,
    rows: list[Row],
    when: date,
    sender: str,
    subject: str,
    body: str,
    report: SyncReport,
    known_status: str = "",
) -> None:
    status = classify(subject, body) or known_status
    if not status:
        return
    company = match_company(sender, subject, body, sorted({r.company for r in rows}))
    if not company:
        if _domain(sender).endswith(BOARD_DOMAINS):
            return
        found = firm_and_role(sender, subject)
        if not found:
            tracker.add_review(when, sender, subject, status)
            report.review += 1
            return
        firm, role = found
        row = tracker.add_row(when, firm, role, status)
        tracker.set(row, "Last Email", f"{when:%Y-%m-%d}: {subject[:80]}")
        rows.append(row)
        report.added.append(f"{firm} - {role[:50] or '(role not named)'}: {status}")
        return
    found = firm_and_role(sender, subject)
    if (
        found
        and found[1]
        and not any(same_role(r.role, found[1]) for r in rows if r.company == company)
    ):
        # A known firm, but a role the tracker doesn't have yet (e.g. a second application).
        row = tracker.add_row(when, company, found[1], status)
        tracker.set(row, "Last Email", f"{when:%Y-%m-%d}: {subject[:80]}")
        rows.append(row)
        report.added.append(f"{company} - {found[1][:50]}: {status}")
        return
    row = pick_row(rows, company, subject, body)
    tracker.set(row, "Last Email", f"{when:%Y-%m-%d}: {subject[:80]}")
    if can_move(row.status, status):
        tracker.set(row, "App Status", status)
        tracker.set(row, "Status Date", when)
        if status in ("Rejected", "Offer"):
            tracker.set(row, "Next Action", "")
        elif status in ("Assessment", "Interview"):
            tracker.set(row, "Next Action", f"{status}: reply / complete it")
        report.updated.append(f"{row.company} - {row.role[:50]}: {row.status} -> {status}")
        row.status = status


def apply_reminders(tracker: Tracker, rows: list[Row], today: date, report: SyncReport) -> None:
    """No email at all after 21 days: follow up. After 45: probably ghosted (you decide)."""
    for row in rows:
        if not tracker.get(row, "App Status"):
            tracker.set(row, "App Status", row.status)
        if row.status not in ("Applied", "Confirmed") or not row.applied:
            continue
        days = (today - row.applied).days
        current = tracker.get(row, "Next Action") or ""
        if days >= 45 and "ghosted" not in current:
            tracker.set(row, "Next Action", f"No reply in {days} days: mark Ghosted?")
            report.reminders += 1
        elif 21 <= days < 45 and not current:
            tracker.set(row, "Next Action", f"Follow up (applied {days} days ago)")
            report.reminders += 1


def sync(
    settings: dict,
    tracker_path: Path,
    root: Path,
    days: int = 2,
    today: date | None = None,
    gmail=None,
    pause: float = 0.1,
) -> SyncReport:
    report = SyncReport()
    if is_open_in_excel(tracker_path):
        report.skipped_open = True
        return report
    tracker = Tracker(tracker_path)
    rows = tracker.rows()

    # Emails parked in Review by earlier runs get another try with the current rules.
    cleared = []
    for index, when, sender, subject, looked_like in tracker.review_items():
        before = len(report.added) + len(report.updated)
        review_before = report.review
        # The Review sheet keeps the status read from the full email ("Looks like"), because a
        # subject such as "Application Update" doesn't say whether it was a rejection.
        apply_email(
            tracker,
            rows,
            when or date.today(),
            sender,
            subject,
            "",
            report,
            known_status=looked_like,
        )
        if report.review > review_before:  # still unreadable: remove the duplicate just added
            tracker.wb["Review"].delete_rows(tracker.wb["Review"].max_row)
            report.review -= 1
        elif len(report.added) + len(report.updated) > before or looked_like:
            cleared.append(index)
    tracker.drop_review_rows(cleared)

    gmail = gmail or service(settings, root)
    state_path = root / "data" / "tracker_sync.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"processed": []}
    done = set(state["processed"])
    if gmail is not None:
        query = f"newer_than:{days}d -label:{settings.get('gmail_label', 'job-alerts')} {SEARCH}"
        refs, token = [], None
        while len(refs) < 500:
            page = (
                gmail.users()
                .messages()
                .list(userId="me", q=query, pageToken=token, maxResults=100)
                .execute()
            )
            refs += page.get("messages", [])
            token = page.get("nextPageToken")
            if not token:
                break
        for ref in sorted(refs, key=lambda r: r["id"]):
            if ref["id"] in done:
                continue
            try:
                # num_retries backs off and retries when Gmail answers "rate limit exceeded".
                msg = (
                    gmail.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute(num_retries=6)
                )
            except Exception as exc:  # still refused after retries: keep what's done, stop
                log.warning("Gmail stopped answering (%s); saving progress", exc)
                report.left = len([r for r in refs if r["id"] not in done])
                break
            payload = msg.get("payload", {})
            when = datetime.fromtimestamp(
                int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc
            ).date()
            apply_email(
                tracker,
                rows,
                when,
                header(payload, "From"),
                header(payload, "Subject"),
                plain_text(payload),
                report,
            )
            report.emails += 1
            done.add(ref["id"])
            time.sleep(pause)  # stay well under Gmail's per-user rate limit

    apply_reminders(tracker, rows, today or date.today(), report)
    tracker.save(root / "data" / "backups")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"processed": sorted(done)[-5000:]}))
    return report
