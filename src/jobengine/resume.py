"""Reads profile facts (school, major, GPA, graduation...) from a resume for `jobengine init`.

Fixed rules always run. If LM Studio is running, the local model reads the resume too, and any
valid answer it gives replaces the rule's guess. Every value is only a default: init still
shows each one, and Enter accepts it.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

FIELDS = (
    "name",
    "school",
    "degree",
    "major",
    "minor",
    "gpa",
    "graduation",
    "work_authorization",
    "email",
    "phone",
    "linkedin",
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_LINKEDIN = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+/?", re.I)
DEGREES = ("bachelors", "masters", "phd")
AUTHORIZATIONS = ("us_citizen", "permanent_resident", "needs_sponsorship")
MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        1,
    )
    for m in names
}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_WORD = r"[A-Z][\w.&'-]*"
_SCHOOL = re.compile(
    rf"((?:{_WORD} )*(?:University|College|Institute of Technology)"
    rf"(?: (?:of|at) (?:the )?{_WORD}(?: {_WORD})*)?)"
)
_FIELD_OF_STUDY = r"([A-Z][A-Za-z&]*(?: (?:and |& )?[A-Z][A-Za-z&]*)*)"
_DEGREE_PATTERNS = [
    ("phd", r"\bPh\.? ?D\b|\bDoctor of\b"),
    ("masters", r"\bMaster'?s?\b|\bM\.?S\.?F?\b(?= |,|$)|\bMBA\b|\bM\.?A\.? (?:in|of)\b"),
    ("bachelors", r"\bBachelor'?s?\b|\bB\.?S\.?\b|\bB\.?A\.?\b|\bBBA\b|\bB\.?B\.?A\.?\b"),
]


def _month(year: str, month: str | None) -> str:
    return f"{year}-{MONTHS[month.lower().rstrip('.')] if month else 5:02d}"


def _degree(text: str) -> str:
    """The degree named first in the resume (the current one is normally listed first)."""
    found = []
    for name, pattern in _DEGREE_PATTERNS:
        hit = re.search(pattern, text)
        if hit:
            found.append((hit.start(), name))
    return min(found)[1] if found else ""


def _graduation(text: str) -> str:
    low = text.lower()
    for pattern in (
        rf"(?:expected|anticipated|graduat\w*|grad\.?)\b[^\n]{{0,30}}?\b{_MONTH} ?,? ?(20\d\d)",
        rf"\b{_MONTH} ?,? ?(20\d\d)[^\n]{{0,15}}\((?:expected|anticipated)\)",
    ):
        hit = re.search(pattern, low)
        if hit:
            return _month(hit.group(2), hit.group(1))
    hit = re.search(r"\bclass of (20\d\d)\b", low)
    if hit:
        return _month(hit.group(1), None)
    return ""


def _work_authorization(text: str) -> str:
    low = text.lower()
    if re.search(r"(?:require|need)s? (?:visa )?sponsorship|\bf-1\b|\bopt\b|\bh-1b\b", low):
        return "needs_sponsorship"
    if re.search(r"permanent resident|green card", low):
        return "permanent_resident"
    if re.search(r"\bu\.?s\.? citizen", low):
        return "us_citizen"
    return ""


def guess_from_resume(text: str) -> dict:
    """Profile values found by fixed rules. Missing values are left out."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    found: dict = {}
    if lines and re.fullmatch(r"[A-Z][\w'.-]+(?: [A-Z][\w'.-]+){1,3}", lines[0]):
        found["name"] = lines[0].title() if lines[0].isupper() else lines[0]
    school = _SCHOOL.search(text)
    if school:
        found["school"] = school.group(1).strip()
    degree = _degree(text)
    if degree:
        found["degree"] = degree
    major = re.search(rf"\bMajors?:? (?:in )?{_FIELD_OF_STUDY}", text) or re.search(
        r"(?:Bachelor|Master)(?:'?s)?(?: of (?:Science|Arts|Business Administration))?"
        rf"(?: degree)?(?: in|,| -) {_FIELD_OF_STUDY}"
        rf"|\b(?:B\.?S\.?|B\.?A\.?|BBA|M\.?S\.?)(?: in|,| -) {_FIELD_OF_STUDY}",
        text,
    )
    if major:
        found["major"] = next(g for g in major.groups() if g)
    minor = re.search(rf"\bMinors?:? (?:in )?{_FIELD_OF_STUDY}", text)
    if minor:
        found["minor"] = minor.group(1)
    gpa = re.search(r"\bGPA:? ?([0-4]\.\d{1,2})\b|\b([0-4]\.\d{1,2}) ?(?:/ ?4\.0+ )?GPA\b", text)
    if gpa:
        found["gpa"] = float(gpa.group(1) or gpa.group(2))
    graduation = _graduation(text)
    if graduation:
        found["graduation"] = graduation
    authorization = _work_authorization(text)
    if authorization:
        found["work_authorization"] = authorization
    # Contact details, for application forms (jobengine packet).
    for key, pattern in (("email", _EMAIL), ("phone", _PHONE), ("linkedin", _LINKEDIN)):
        hit = pattern.search(text)
        if hit:
            found[key] = hit.group(0).strip()
    if "linkedin" in found and not found["linkedin"].lower().startswith("http"):
        found["linkedin"] = "https://" + found["linkedin"]
    return found


def clean(values: dict) -> dict:
    """Keep only values that init would accept; drop the rest."""
    out: dict = {}
    for key in FIELDS:
        value = values.get(key)
        if value in (None, ""):
            continue
        if key == "gpa":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not 0 < value <= 4.5:
                continue
        elif key == "graduation":
            value = str(value)
            if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", value):
                continue
        elif key == "degree" and value not in DEGREES:
            continue
        elif key == "work_authorization" and value not in AUTHORIZATIONS:
            continue
        elif key == "email" and not _EMAIL.fullmatch(str(value)):
            continue
        elif key == "phone" and len(re.sub(r"\D", "", str(value))) < 10:
            continue
        elif key == "linkedin" and "linkedin.com/in/" not in str(value).lower():
            continue
        elif not isinstance(value, str):
            continue
        out[key] = value.strip() if isinstance(value, str) else value
    return out


MODEL_SYSTEM = "You read student resumes and reply with JSON only."


def model_prompt(text: str) -> str:
    return f"""Read this resume and fill in the facts it states. Use "" for anything not stated.
Do not guess.

Reply with only JSON in this form:
{{"name": "", "school": "", "degree": "bachelors | masters | phd", "major": "", "minor": "",
"gpa": "number, e.g. 3.5", "graduation": "expected graduation as YYYY-MM",
"work_authorization": "us_citizen | permanent_resident | needs_sponsorship"}}

Resume:
{text}
/no_think"""


def parse_model_reply(reply: str) -> dict:
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).split("</think>")[-1]
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON in reply: {reply[:120]!r}")
    return clean(json.loads(reply[start : end + 1]))


def read_profile_facts(text: str, client=None) -> tuple[dict, str]:
    """Profile defaults from a resume, and which reader found them ("rules" or the model name).

    `client` is an LMStudio (or a fake in tests). If it isn't reachable or its reply is
    unusable, the fixed rules' guesses are used on their own."""
    found = clean(guess_from_resume(text))
    if client is None:
        return found, "rules"
    try:
        model = client.pick_model()
        from_model = parse_model_reply(client.chat(MODEL_SYSTEM, model_prompt(text)))
    except Exception as exc:
        log.info("the local model couldn't read the resume: %s", exc)
        return found, "rules"
    return {**found, **from_model}, model
