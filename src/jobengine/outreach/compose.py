"""Turns a template, a contact and an application into an email.

The only text the AI writes is {hook}: one sentence linking the role to your background.
Everything else is your template, word for word. A hook that fails any check below is
replaced by the template's fallback_hook, so a bad AI answer never reaches a draft.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
from email.message import EmailMessage
from pathlib import Path

from ..textutil import normalize

log = logging.getLogger(__name__)

PLACEHOLDERS = ("first_name", "company", "role", "my_name", "school", "hook")
_LEFTOVER = re.compile(r"\{[A-Za-z_]+\}")
MAX_HOOK_WORDS = 30

HOOK_SYSTEM = (
    "You write one sentence for a student's cold email to someone at a company. "
    "Use only facts given to you. Reply with the sentence only."
)


class TemplateError(ValueError):
    """The template can't be filled in (a missing or unknown placeholder)."""


def pick_template(templates: dict, kind: str) -> tuple[str, dict] | None:
    """The first template written for this kind of contact, else the first one for anyone."""
    for wanted in (kind, "any"):
        for name, template in templates.items():
            if template.get("for", "any") == wanted:
                return name, template
    return None


def fill(text: str, values: dict) -> str:
    for key in PLACEHOLDERS:
        text = text.replace("{" + key + "}", str(values.get(key, "")))
    leftover = _LEFTOVER.search(text)
    if leftover:
        raise TemplateError(f"unknown placeholder {leftover.group(0)}")
    return text


def hook_prompt(company: str, role: str, description: str, about: str) -> str:
    return f"""Write ONE sentence (at most {MAX_HOOK_WORDS} words) for my cold email to someone
at {company} about the {role} role. Link something specific about the role or team to my
background. No greeting, no sign-off, no links, no numbers that aren't below, and don't
invent facts.

Role description:
{" ".join(description.split())[:1500] or "(not available; use the role title)"}

About me:
{about[:1500]}
/no_think"""


def check_hook(
    text: str,
    company: str,
    inputs: str,
    other_companies: list[str],
    max_words: int = MAX_HOOK_WORDS,
) -> str | None:
    """The cleaned-up hook (or longer AI answer), or None if it breaks a rule."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).split("</think>")[-1]
    text = " ".join(text.split()).strip().strip("\"'").strip()
    if not text or "{" in text or "}" in text:
        return None
    if re.search(r"https?://|www\.|\.com\b|@", text, re.I):
        return None
    if len(text.split()) > max_words:
        return None
    known = set(re.findall(r"\d+(?:\.\d+)?", inputs))
    if any(n not in known for n in re.findall(r"\d+(?:\.\d+)?", text)):
        return None
    said = f" {normalize(text)} "
    own = normalize(company)
    for other in other_companies:
        name = normalize(other)
        if name and name not in own and own not in name and f" {name} " in said:
            return None
    if text[-1] not in ".!?":
        text += "."
    return text


def write_hook(
    client, company: str, role: str, description: str, about: str, others: list[str]
) -> str | None:
    """A checked hook from the local model, or None (the caller uses the fallback)."""
    if client is None:
        return None
    try:
        reply = client.chat(HOOK_SYSTEM, hook_prompt(company, role, description, about))
    except Exception as exc:
        log.info("the local model couldn't write a hook for %s: %s", company, exc)
        return None
    return check_hook(reply, company, f"{company} {role} {description} {about}", others)


def build_message(to: str, subject: str, body: str, attachment: Path | None = None) -> dict:
    """A Gmail API message body ({"raw": ...}) with an optional file attached."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment is not None:
        kind, _ = mimetypes.guess_type(attachment.name)
        main, sub = (kind or "application/octet-stream").split("/", 1)
        msg.add_attachment(
            attachment.read_bytes(), maintype=main, subtype=sub, filename=attachment.name
        )
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")}
