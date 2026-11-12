"""Feature 5: an application packet for one role, ready before you open the form.

`jobengine packet <words from the role | its URL>` writes data/packets/<firm-role>/ with:
- packet.md: every question the form asks, with the answer to give and where it came from
  (your profile, a fixed rule, an AI draft for you to edit, or "you": left for you);
- answers.json: the same, for `--fill`, which types them into the form in a browser.

Greenhouse forms are read from Greenhouse's public job API, so the packet has the form's
real questions, custom ones included. For other boards it uses the standard fields plus
default_questions from config/apply.yml. Nothing is ever submitted for you.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .outreach.compose import check_hook
from .textutil import html_to_text, normalize

log = logging.getLogger(__name__)

GREENHOUSE_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io",
)
DEGREE_NAMES = {"bachelors": "Bachelor's", "masters": "Master's", "phd": "PhD"}
MONTHS = "January February March April May June July August September October November December"


@dataclass
class Field:
    label: str
    name: str = ""  # the form's field name, when the board says (Greenhouse)
    kind: str = "text"  # text, textarea, file, select, multiselect
    required: bool = False
    options: list[str] = field(default_factory=list)
    answer: str = ""
    source: str = "you"  # profile, rule, ai (a draft to edit), you (left for you)


@dataclass
class Packet:
    company: str
    title: str
    url: str
    apply_url: str
    board: str
    fields: list[Field] = field(default_factory=list)
    description: str = ""

    @property
    def slug(self) -> str:
        return "-".join(normalize(f"{self.company} {self.title}").split())[:80] or "role"


# ---------- which board, and what it asks ----------


def board_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in GREENHOUSE_HOSTS or "gh_jid=" in url:
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "workday"
    return "other"


def greenhouse_job(url: str) -> tuple[str, str] | None:
    """(board, job id) from a Greenhouse link, or None if the board can't be told."""
    parts = urlsplit(url)
    seg = [s for s in parts.path.split("/") if s]
    query = parse_qs(parts.query)
    if parts.hostname in GREENHOUSE_HOSTS:
        if len(seg) >= 3 and seg[1] == "jobs" and seg[2].isdigit():
            return seg[0], seg[2]
        if seg == ["embed", "job_app"] and query.get("for") and query.get("token"):
            return query["for"][0], query["token"][0]
    return None


def greenhouse_fields(data: dict) -> list[Field]:
    out = []
    for q in data.get("questions") or []:
        for f in q.get("fields") or []:
            kind = {
                "input_file": "file",
                "textarea": "textarea",
                "multi_value_single_select": "select",
                "multi_value_multi_select": "multiselect",
                "input_hidden": "hidden",
            }.get(f.get("type", ""), "text")
            if kind == "hidden":
                continue
            out.append(
                Field(
                    label=q.get("label", f.get("name", "")),
                    name=f.get("name", ""),
                    kind=kind,
                    required=bool(q.get("required")),
                    options=[str(v.get("label", "")) for v in f.get("values") or []],
                )
            )
    return out


STANDARD = [
    ("First Name", "text"),
    ("Last Name", "text"),
    ("Email", "text"),
    ("Phone", "text"),
    ("Resume/CV", "file"),
    ("LinkedIn Profile", "text"),
    ("School", "text"),
    ("Degree", "text"),
    ("Major", "text"),
    ("GPA", "text"),
    ("Expected Graduation Date", "text"),
    ("Are you legally authorized to work in the United States?", "text"),
    ("Will you now or in the future require visa sponsorship?", "text"),
    ("How did you hear about this job?", "text"),
]


def standard_fields(settings: dict) -> list[Field]:
    fields = [Field(label, kind=kind) for label, kind in STANDARD]
    fields += [Field(q, kind="textarea") for q in settings.get("default_questions", [])]
    return fields


# ---------- answers ----------


def _first_last(name: str) -> tuple[str, str]:
    parts = name.split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def _graduation(value: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})", str(value or ""))
    return f"{MONTHS.split()[int(m.group(2)) - 1]} {m.group(1)}" if m else str(value or "")


def _option(options: list[str], wanted: str) -> str:
    """The option that matches the wanted answer, or "" if none does."""
    want = normalize(wanted)
    for o in options:
        if normalize(o) == want:
            return o
    for o in options:
        if normalize(o).startswith(want) or (want and want in normalize(o)):
            return o
    return ""


def rule_answer(f: Field, profile: dict, settings: dict, resume: Path) -> tuple[str, str]:
    """(answer, source) from your profile or a fixed rule, or ("", "you")."""
    label = normalize(f.label)
    first, last = _first_last(profile.get("name", ""))
    auth = profile.get("work_authorization", "")
    is_free = auth in ("us_citizen", "permanent_resident")

    def has(*words: str) -> bool:
        return any(re.search(rf"\b{w}\b", label) for w in words)

    if f.kind == "file":
        if has("resume", "cv") and resume.is_file():
            return str(resume), "profile"
        return "", "you"
    if has("gender", "race", "ethnicity", "hispanic", "latino", "veteran", "disability"):
        phrases = settings.get("decline_phrases", ["decline"])
        pick = next(
            (o for o in f.options if any(p in o.lower() for p in phrases)),
            "",
        )
        return (pick, "rule") if pick else ("", "you")
    found = ""
    if has("first name", "preferred name", "preferred first name"):
        found = first
    elif has("last name", "surname", "family name"):
        found = last
    elif label in ("name", "full name", "legal name"):
        found = profile.get("name", "")
    elif has("email"):
        found = profile.get("email", "")
    elif has("phone", "mobile"):
        found = profile.get("phone", "")
    elif has("linkedin"):
        found = profile.get("linkedin", "")
    elif has("github"):
        found = profile.get("github", "")
    elif has("website", "portfolio"):
        found = profile.get("website", "")
    elif has("sponsorship", "visa"):
        if auth:
            found = "No" if is_free else "Yes"
    elif has("authorized", "authorised", "eligible", "legally"):
        if is_free:
            found = "Yes"
    elif has("hear", "referral source", "referred", "how did you find", "where did you find"):
        found = settings.get("how_did_you_hear", "")
    elif has("relocate", "relocation"):
        found = settings.get("willing_to_relocate", "")
    elif has("contact method", "preferred method of contact"):
        found = settings.get("preferred_contact_method", "Email")
    elif has("school", "university", "college", "institution"):
        found = profile.get("school", "")
    elif has("degree"):
        found = DEGREE_NAMES.get(profile.get("degree", ""), profile.get("degree", ""))
    elif has("discipline", "major", "field of study", "concentration"):
        found = profile.get("major", "")
    elif has("gpa", "cgpa", "grade point"):
        found = str(profile.get("gpa", "") or "")
    elif has("graduation", "grad date"):
        found = _graduation(profile.get("graduation", ""))
    if not found:
        return "", "you"
    if f.options:  # a dropdown: the answer must be one of its options
        found = _option(f.options, found)
        return (found, "rule") if found else ("", "you")
    return found, "profile"


def is_open_question(f: Field) -> bool:
    """An essay-style question the AI may draft: a text box, or "Why...?", "Tell us...", etc."""
    if f.kind in ("file", "select", "multiselect"):
        return False
    label = normalize(f.label)
    return f.kind == "textarea" or bool(
        re.match(r"why\b", label)
        or re.search(r"\b(tell us|describe|motivat\w*|interest(ed|s) you|excites you)\b", label)
    )


ANSWER_SYSTEM = (
    "You draft short answers to internship application questions for one student. "
    "Use only facts given to you. Reply with the answer only."
)


def answer_prompt(question: str, packet: Packet, about: str, words: int) -> str:
    return f"""Draft my answer to this application question, at most {words} words, first person,
plain and specific. Link the firm and role to my background. No links, no greeting, no numbers
that aren't below, and don't invent experience.

Firm: {packet.company}
Role: {packet.title}
Question: {question}

Role description:
{" ".join(packet.description.split())[:2500] or "(not available)"}

About me:
{about[:2500]}
/no_think"""


def draft_answer(client, question: str, packet: Packet, about: str, words: int) -> str:
    if client is None:
        return ""
    prompt = answer_prompt(question, packet, about, words)
    try:
        try:
            reply = client.chat(ANSWER_SYSTEM, prompt)
        except ValueError as exc:
            # Qwen 3.6 sometimes thinks despite /no_think and runs out of tokens (Nov 11:
            # "Why are you interested in this role?" was left blank on 2 of 4 packets).
            # One more try with twice the room lets it finish thinking and answer.
            limit = getattr(client, "max_tokens", None)
            if not limit or "max_tokens" not in str(exc):
                raise
            log.info("the local model ran out of room on %r; trying again with more", question)
            client.max_tokens = limit * 2
            try:
                reply = client.chat(ANSWER_SYSTEM, prompt)
            finally:
                client.max_tokens = limit
    except Exception as exc:
        log.info("the local model couldn't draft %r: %s", question, exc)
        return ""
    inputs = f"{packet.company} {packet.title} {question} {packet.description} {about}"
    # The same checks as an outreach hook (no links, no invented numbers), with room for more.
    return check_hook(reply, packet.company, inputs, [], max_words=words) or ""


# ---------- building and writing ----------


def build(
    row: dict,
    profile: dict,
    settings: dict,
    root: Path,
    client=None,
    get=None,
    about: str = "",
) -> Packet:
    url = row.get("url", "")
    packet = Packet(
        company=row.get("company", ""),
        title=row.get("title", ""),
        url=url,
        apply_url=url,
        board=board_of(url),
        description=row.get("description", ""),
    )
    gh = greenhouse_job(url) if packet.board == "greenhouse" else None
    if gh and get is not None:
        board, job_id = gh
        try:
            resp = get(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"
            )
            if resp.status_code == 200:
                data = resp.json()
                packet.fields = greenhouse_fields(data)
                packet.description = packet.description or html_to_text(data.get("content", ""))
        except Exception as exc:
            log.warning("couldn't read the Greenhouse form for %s: %s", url, exc)
    if not packet.fields:
        packet.fields = standard_fields(settings)
    if packet.board == "lever" and not url.rstrip("/").endswith("/apply"):
        packet.apply_url = url.rstrip("/") + "/apply"

    resume = root / profile.get("resume_path", "private/Resume.pdf")
    words = int(settings.get("max_answer_words", 150))
    for f in packet.fields:
        f.answer, f.source = rule_answer(f, profile, settings, resume)
        if f.name == "resume_text":
            continue  # the resume file is attached; never have the AI write a resume
        if not f.answer and is_open_question(f):
            drafted = draft_answer(client, f.label, packet, about, words)
            if drafted:
                f.answer, f.source = drafted, "ai"
    return packet


SOURCE_NOTE = {
    "profile": "from your profile",
    "rule": "fixed rule",
    "ai": "AI draft: edit before submitting",
    "you": "LEFT FOR YOU",
}


def markdown(packet: Packet) -> str:
    lines = [
        f"# {packet.company} - {packet.title}",
        "",
        f"- Apply: {packet.apply_url}",
        f"- Board: {packet.board}",
        "",
        "Nothing has been submitted. Check every answer; AI drafts especially.",
        "",
    ]
    for f in packet.fields:
        star = " *" if f.required else ""
        lines.append(f"## {f.label}{star}")
        lines.append(f"_{SOURCE_NOTE[f.source]}_")
        if f.options and f.source == "you":
            lines.append("Options: " + "; ".join(f.options))
        lines += ["", f.answer or "(blank)", ""]
    return "\n".join(lines)


def write(packet: Packet, root: Path) -> Path:
    folder = root / "data" / "packets" / packet.slug
    folder.mkdir(parents=True, exist_ok=True)
    cover_file = next((f for f in packet.fields if f.name == "cover_letter"), None)
    cover_text = next((f for f in packet.fields if f.name == "cover_letter_text"), None)
    if cover_file and not cover_file.answer and cover_text and cover_text.answer:
        # Greenhouse shows the cover letter as a file upload; its text box sits behind an
        # "Enter manually" button the filler never clicks. Attach the draft as a file.
        path = folder / "cover_letter.txt"
        path.write_text(cover_text.answer + "\n", encoding="utf-8")
        cover_file.answer, cover_file.source = str(path), cover_text.source
    (folder / "packet.md").write_text(markdown(packet), encoding="utf-8")
    (folder / "answers.json").write_text(json.dumps(asdict(packet), indent=1), encoding="utf-8")
    return folder


def load(folder: Path) -> Packet:
    data = json.loads((folder / "answers.json").read_text(encoding="utf-8"))
    data["fields"] = [Field(**f) for f in data.get("fields", [])]
    return Packet(**data)
