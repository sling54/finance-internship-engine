"""`jobengine outreach plan`: contacts for recent applications, and a Gmail draft to each.

1. Pick tracker rows with an outreach status (default Applied or Confirmed) that have no
   outreach logged yet, oldest first, up to max_applications_per_run.
2. Look the company up on Hunter.io and rank its people: recruiting first, then the role's
   team, then anyone else, each by Hunter's confidence.
3. Fill in the template for that kind of contact, with one AI-written sentence ({hook}).
4. Save the draft in Gmail under the jobengine/outreach label, log it, and copy the log to
   the tracker's Outreach sheet. Nothing is sent here.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..filters.dedup import company_key
from ..scorer import about_me, read_resume
from ..textutil import normalize, phrase_pattern
from ..tracker_sync import Row, Tracker, is_open_in_excel, same_role
from .compose import TemplateError, build_message, fill, pick_template, write_hook
from .hunter import CreditLimit, HunterError
from .mail import create_draft, label_id
from .sheet import sync_sheet
from .store import Entry, Log, role_key

log = logging.getLogger(__name__)

KIND_ORDER = {"recruiter": 0, "team": 1, "other": 2}
_ROLE_NOISE = {
    "intern",
    "internship",
    "summer",
    "winter",
    "fall",
    "spring",
    "analyst",
    "associate",
    "program",
    "programme",
    "the",
    "and",
    "of",
    "for",
    "in",
}


@dataclass
class PlanReport:
    dry_run: bool = False
    drafted: list[str] = field(default_factory=list)  # "Company - Role: Name <email>"
    planned: list[str] = field(default_factory=list)  # dry run: to | subject | body start
    short: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stopped: str = ""
    not_reached: list[str] = field(default_factory=list)
    sheet_skipped: bool = False
    applications: int = 0

    def summary(self) -> str:
        if not self.applications:
            return "No applications need outreach right now."
        lines = []
        if self.dry_run:
            lines.append(f"Dry run: {len(self.planned)} drafts planned; nothing was written.")
            lines += [f"  {p}" for p in self.planned]
        else:
            lines.append(
                f"Drafted {len(self.drafted)} emails for {self.applications} applications. "
                "Review them in Gmail (label jobengine/outreach); nothing was sent."
            )
            lines += [f"  + {d}" for d in self.drafted]
        lines += [f"  ! {s}" for s in self.short]
        lines += [f"  - skipped {s}" for s in self.skipped]
        if self.stopped:
            lines.append(f"Stopped: {self.stopped}.")
            if self.not_reached:
                lines.append("Left without contacts for now: " + "; ".join(self.not_reached))
        if self.sheet_skipped:
            lines.append(
                "The tracker is open in Excel, so its Outreach sheet wasn't updated; "
                "the next run adds the missing rows."
            )
        return "\n".join(lines)


def pick_applications(rows: list[Row], log_: Log, settings: dict, today: date) -> list[Row]:
    statuses = set(settings.get("statuses", ["Applied", "Confirmed"]))
    min_days = int(settings.get("min_days_after_apply", 0))
    picked = [
        r
        for r in rows
        if r.status in statuses
        and r.role.strip()
        and (
            (r.applied is not None and (today - r.applied).days >= min_days)
            or (r.applied is None and min_days == 0)
        )
        and not log_.has_role(r.company, r.role)
    ]
    picked.sort(key=lambda r: (r.applied is None, r.applied or date.max, r.index))
    return picked[: int(settings.get("max_applications_per_run", 25))]


def contact_kind(person: dict, role: str, track: str, settings: dict) -> str:
    position = person.get("position") or ""
    recruiting = phrase_pattern(settings.get("recruiting_keywords") or ["recruiter"])
    if recruiting.search(position) or (person.get("department") or "") == "hr":
        return "recruiter"
    teams = settings.get("team_keywords") or {}
    words = [w for w in normalize(role).split() if w not in _ROLE_NOISE and not w.isdigit()]
    words += teams.get(track) or [w for ws in teams.values() for w in ws]
    if words and phrase_pattern(words).search(position):
        return "team"
    return "other"


def usable(person: dict, settings: dict) -> bool:
    email = person.get("value") or ""
    status = (person.get("verification") or {}).get("status")
    return (
        "@" in email
        and (person.get("type") or "personal") == "personal"
        and int(person.get("confidence") or 0) >= int(settings.get("min_confidence", 80))
        and status != "invalid"
    )


def choose(
    people: list[dict], app: Row, track: str, log_: Log, settings: dict, today: date
) -> list[tuple[str, dict]]:
    """The contacts to email for this application, best first."""
    ranked = sorted(
        ((contact_kind(p, app.role, track, settings), p) for p in people if usable(p, settings)),
        key=lambda kp: (KIND_ORDER[kp[0]], -int(kp[1].get("confidence") or 0)),
    )
    want = int(settings.get("contacts_per_application", 2))
    room = int(settings.get("max_contacts_per_company", 3)) - log_.company_count(app.company)
    days = int(settings.get("recontact_after_days", 90))
    out: list[tuple[str, dict]] = []
    for kind, person in ranked:
        if len(out) >= min(want, room):
            break
        email = person["value"]
        if f"{email.lower()}|{company_key(app.company)}" in log_.entries:
            continue
        if log_.contacted_recently(email, days, today):
            continue
        out.append((kind, person))
    return out


def read_postings(root: Path) -> list[dict]:
    path = root / "data" / "jobs_latest.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        return []


def find_posting(postings: list[dict], company: str, role: str) -> dict:
    key = company_key(company)
    for p in postings:
        if company_key(p.get("company", "")) == key and same_role(p.get("title", ""), role):
            return p
    return {}


def plan(
    root: Path,
    settings: dict,
    templates: dict,
    profile: dict,
    tracker_path: Path,
    hunter,
    gmail=None,
    client=None,
    today: date | None = None,
    dry_run: bool = False,
) -> PlanReport:
    today = today or date.today()
    report = PlanReport(dry_run=dry_run)
    tracker = Tracker(tracker_path)
    rows = tracker.rows()
    log_ = Log.load(root)
    apps = pick_applications(rows, log_, settings, today)
    report.applications = len(apps)
    if not apps:
        return report

    postings = read_postings(root)
    others = sorted({r.company for r in rows})
    resume = root / profile.get("resume_path", "private/Resume.pdf")
    try:
        resume_text = read_resume(resume)
    except Exception as exc:  # a damaged PDF: hooks just go without the resume
        log.warning("couldn't read %s for the AI hook: %s", resume.name, exc)
        resume_text = ""
    about = about_me(profile, resume_text)
    label = None if dry_run else label_id(gmail)
    want = int(settings.get("contacts_per_application", 2))
    changed = False

    for i, app in enumerate(apps):
        try:
            people = hunter.people(app.company)
        except (CreditLimit, HunterError) as exc:
            report.stopped = str(exc)
            report.not_reached = [f"{a.company} - {a.role}" for a in apps[i:]]
            break
        posting = find_posting(postings, app.company, app.role)
        chosen = choose(people, app, posting.get("track", ""), log_, settings, today)
        hook = None
        if chosen:
            hook = write_hook(
                client,
                app.company,
                app.role,
                posting.get("description", ""),
                about,
                [c for c in others if company_key(c) != company_key(app.company)],
            )
        made = 0
        for kind, person in chosen:
            found = pick_template(templates, kind)
            name = f"{person.get('first_name') or ''} {person.get('last_name') or ''}".strip()
            who = f"{app.company} - {app.role}: {name or person['value']} <{person['value']}>"
            if found is None:
                report.skipped.append(f"{who} (no template for {kind} contacts)")
                continue
            template_name, template = found
            values = {
                "first_name": person.get("first_name") or "there",
                "company": app.company,
                "role": app.role,
                "my_name": profile.get("name", ""),
                "school": profile.get("school", ""),
                "hook": hook or template.get("fallback_hook", ""),
            }
            try:
                subject = fill(template.get("subject", ""), values)
                body = fill(template.get("body", ""), values)
            except TemplateError as exc:
                report.skipped.append(f"{who} (template {template_name}: {exc})")
                continue
            attachment = None
            if template.get("attach_resume"):
                if not resume.is_file():
                    report.skipped.append(f"{who} (resume to attach not found at {resume})")
                    continue
                attachment = resume
            entry = Entry(
                date=today.isoformat(),
                company=app.company,
                role=app.role,
                contact=name,
                first_name=person.get("first_name") or "",
                position=person.get("position") or "",
                email=person["value"],
                kind=kind,
                template=template_name,
            )
            log_.add(entry)  # in memory, so the company cap counts it even in a dry run
            made += 1
            if dry_run:
                preview = " ".join(body.split())[:200]
                report.planned.append(f"{entry.email} | {subject} | {preview}")
                continue
            entry.draft_id, entry.message_id = create_draft(
                gmail, build_message(entry.email, subject, body, attachment), label
            )
            log_.save()  # after every draft, so stopping halfway never loses one
            changed = True
            report.drafted.append(who)
        if len(chosen) < want:
            note = f"short: {len(chosen)} of {want}"
            report.short.append(f"{app.company} - {app.role}: {note} contacts found")
            if not dry_run and not made:
                log_.roles[role_key(app.company, app.role)] = {
                    "date": today.isoformat(),
                    "note": note,
                }
                changed = True

    if dry_run or not changed:
        return report
    log_.save()
    if is_open_in_excel(tracker_path):
        report.sheet_skipped = True
        return report
    sync_sheet(tracker, list(log_.entries.values()))
    tracker.save(root / "data" / "backups")
    return report
