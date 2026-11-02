"""AI fit score: a local model in LM Studio rates each kept role 1-5 with a one-line reason.

Scores are cached in data/scores.json, so a daily run only asks about roles it hasn't seen.
Editing config/scoring.yml, your profile or your resume re-scores everything automatically.
If LM Studio isn't running, the scan still finishes, just without scores.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .filters.dedup import job_key
from .models import Posting

log = logging.getLogger(__name__)

SYSTEM = (
    "You screen internship postings for one student. Judge only from the facts given. "
    "Be strict and consistent. Reply with JSON only."
)
STOP_AFTER_FAILURES = 3  # in a row: the server has likely crashed, so stop asking


@dataclass
class ScoreReport:
    model: str = ""
    scored: int = 0
    cached: int = 0
    failed: int = 0
    seconds: float = 0.0
    thinking: int = 0  # replies where the model thought before answering
    skipped: str = ""  # why scoring didn't run at all
    stopped: str = ""  # why scoring stopped early

    def summary(self) -> str:
        if self.skipped:
            return f"AI scoring skipped: {self.skipped}"
        avg = self.seconds / self.scored if self.scored else 0.0
        text = (
            f"AI scoring ({self.model}): {self.scored} scored in {self.seconds / 60:.1f} min "
            f"(avg {avg:.1f}s each), {self.cached} from cache"
        )
        if self.failed:
            text += f", {self.failed} failed"
        if self.thinking:
            text += f"\n  The model thought before answering on {self.thinking} of {self.scored}."
        if self.stopped:
            text += f"\n  Stopped early: {self.stopped}"
        return text


class LMStudio:
    """Talks to LM Studio's OpenAI-compatible local server."""

    def __init__(
        self,
        url: str,
        model: str = "",
        timeout: int = 180,
        max_tokens: int = 4096,
        no_thinking: bool = True,
    ):
        self.url = url.rstrip("/")
        self.wanted = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.no_thinking = no_thinking
        self.model = ""
        self.thought = False  # did the last reply include thinking?
        self.http = requests.Session()

    def pick_model(self) -> str:
        """Find the model to use. Raises if the server isn't reachable."""
        resp = self.http.get(f"{self.url}/models", timeout=10)
        resp.raise_for_status()
        ids = [m["id"] for m in resp.json().get("data", []) if "embed" not in m["id"].lower()]
        if not ids:
            raise RuntimeError("no chat model is available in LM Studio")
        match = [i for i in ids if self.wanted and self.wanted.lower() in i.lower()]
        self.model = match[0] if match else ids[0]
        if self.wanted and not match:
            log.warning("model %r not found in LM Studio; using %s", self.wanted, self.model)
        return self.model

    def chat(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if self.no_thinking:  # Qwen-style switch; servers that don't know it ignore it
            body["chat_template_kwargs"] = {"enable_thinking": False}
        resp = self.http.post(f"{self.url}/chat/completions", json=body, timeout=self.timeout)
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        self.thought = bool(message.get("reasoning_content")) or "<think>" in content
        if not content.strip() and choice.get("finish_reason") == "length":
            raise ValueError(
                f"the model was still thinking when it hit max_tokens ({self.max_tokens}); "
                "raise max_tokens in config/scoring.yml"
            )
        return content


def parse_reply(text: str) -> tuple[int, str]:
    """Pull {"score": 1-5, "reason": "..."} out of a model reply, ignoring any thinking text."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.split("</think>")[-1]  # reply that only closes a thinking block
    for blob in reversed(re.findall(r"\{[^{}]*\}", text)):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        score = data.get("score")
        if isinstance(score, str) and score.strip().isdigit():
            score = int(score)
        if isinstance(score, int) and 1 <= score <= 5:
            return score, str(data.get("reason", "")).strip()
    found = re.search(r'score"?\s*[:=]\s*"?([1-5])\b', text)
    if found:
        return int(found.group(1)), ""
    raise ValueError(f"no score in reply: {text[:120]!r}")


def read_resume(path: Path, limit: int = 3000) -> str:
    """Resume text for the AI, or "" if the file or the PDF reader is missing."""
    if not path.exists():
        return ""
    if path.suffix.lower() != ".pdf":
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("install pypdf to let the AI read your resume: pip install pypdf")
        return ""
    pages = PdfReader(str(path)).pages
    return "\n".join(p.extract_text() or "" for p in pages)[:limit]


def about_me(profile: dict, resume: str) -> str:
    lines = [
        f"Degree: {profile.get('degree', '')} in {profile.get('major', '')}"
        + (f", minor in {profile['minor']}" if profile.get("minor") else ""),
        f"School: {profile.get('school', '')}",
        f"GPA: {profile.get('gpa', '')}",
        f"Graduating: {profile.get('graduation', '')}",
        f"Work authorization: {profile.get('work_authorization', '')}",
    ]
    if resume:
        lines += ["Resume:", resume]
    return "\n".join(lines)


def build_prompt(job: Posting, scoring: dict, about: str) -> str:
    limit = int(scoring.get("max_description_chars", 4000))
    desc = " ".join(job.description.split())[:limit]
    prompt = f"""About me:
{about}

What I want:
{scoring.get("what_i_want", "").strip()}

Score this internship from 1 to 5 using this rubric:
{scoring.get("rubric", "").strip()}

Job:
Company: {job.company}
Title: {job.title}
Track (engine's guess): {job.track}
Locations: {"; ".join(job.locations) or "unknown"}
Season: {job.season or "unknown"}
Pay: {job.salary or "not listed"}
Engine flags: {"; ".join(f for f in job.flags if f != "new") or "none"}
Description:
{desc or "(no description available; judge from the title and company)"}

Reply with only JSON: {{"score": <1-5>, "reason": "<one sentence, at most 20 words>"}}"""
    if scoring.get("fast_mode", True):
        prompt += "\n/no_think"  # Qwen models skip their long thinking step
    return prompt


def score_jobs(
    jobs: list[Posting],
    scoring: dict,
    profile: dict,
    root: Path,
    client: LMStudio | None = None,
    progress: bool = True,
) -> ScoreReport:
    report = ScoreReport()
    if not scoring.get("enabled", False):
        report.skipped = "turned off in config/scoring.yml"
        return report

    lm = scoring.get("lm_studio", {})
    url = lm.get("url", "http://localhost:1234/v1")
    client = client or LMStudio(
        url,
        lm.get("model", ""),
        lm.get("timeout_seconds", 180),
        lm.get("max_tokens", 4096),
        scoring.get("fast_mode", True),
    )
    try:
        report.model = client.pick_model()
    except Exception as exc:
        report.skipped = (
            f"LM Studio isn't reachable at {url}. Open LM Studio and start its server. ({exc})"
        )
        return report

    resume = read_resume(root / profile.get("resume_path", "private/Resume.pdf"))
    about = about_me(profile, resume)
    # Changing what you want, the rubric, your profile or resume invalidates old scores.
    version = hashlib.sha1(
        "\n".join([about, scoring.get("what_i_want", ""), scoring.get("rubric", "")]).encode()
    ).hexdigest()[:10]

    cache_path = root / "data" / "scores.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    todo = []
    for job in jobs:
        hit = cache.get("|".join(job_key(job)))
        if hit and hit.get("version") == version:
            job.fit_score, job.fit_reason = hit["score"], hit["reason"]
            report.cached += 1
        else:
            todo.append(job)

    failures_in_a_row = 0
    for i, job in enumerate(todo, 1):
        if progress:
            print(f"\rAI scoring {i}/{len(todo)} new roles...", end="", file=sys.stderr, flush=True)
        start = time.monotonic()
        try:
            score, reason = parse_reply(client.chat(SYSTEM, build_prompt(job, scoring, about)))
        except Exception as exc:
            report.failed += 1
            failures_in_a_row += 1
            log.warning("AI score failed for %s - %s: %s", job.company, job.title, exc)
            if failures_in_a_row >= STOP_AFTER_FAILURES:
                report.stopped = f"{STOP_AFTER_FAILURES} failures in a row ({exc})"
                break
            continue
        report.seconds += time.monotonic() - start
        report.scored += 1
        report.thinking += bool(getattr(client, "thought", False))
        failures_in_a_row = 0
        job.fit_score, job.fit_reason = score, reason
        cache["|".join(job_key(job))] = {"score": score, "reason": reason, "version": version}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1))  # saved each time: safe to stop early
    if progress and todo:
        print(file=sys.stderr)
    return report
