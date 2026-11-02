"""Feature 3: a 1-5 fit score for each role that passed the filters.

The model only rates five dimensions from 1 to 5 and gives a one-line reason. The weighted
total and the action (tailored / standard / skip) are computed here in code, so the rule is
the same on every run and can be unit-tested. Scores are cached, so a role is rated once.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from .filters.dedup import job_key
from .http import session
from .models import Posting

log = logging.getLogger(__name__)

DIMENSIONS = ("relevance", "skills", "pay", "odds", "learning")

SYSTEM_PROMPT = """You screen internship postings for one candidate.
Rate the posting on five dimensions, each an integer from 1 (poor) to 5 (excellent):
- relevance: how close the role is to trading, quantitative research or derivatives
  (5 = trading/quant desk role; 3 = adjacent finance or data role; 1 = unrelated work).
- skills: how well the candidate's resume covers the required skills.
- pay: posted pay, or what this firm is known to pay interns (5 = top-of-market prop/quant pay).
- odds: the candidate's realistic chance given stated requirements (GPA cutoffs, degree level,
  graduation date, programming depth). 5 = clearly qualified, 1 = clearly out of reach.
- learning: desk exposure, mentorship and the firm's reputation for training interns.
Also set "investment_banking" to true if the role is in investment banking / M&A coverage.
Reply with JSON only, no prose:
{"relevance": n, "skills": n, "pay": n, "odds": n, "learning": n,
 "investment_banking": false, "reason": "one sentence on the biggest factor"}"""

Complete = Callable[[list[dict]], str]


def openai_complete(llm: dict) -> Complete:
    """A chat call to any OpenAI-compatible server (LM Studio, Ollama, vLLM)."""
    sess = session()
    url = llm["base_url"].rstrip("/") + "/chat/completions"

    def complete(messages: list[dict]) -> str:
        resp = sess.post(
            url,
            json={"model": llm["model"], "messages": messages, "temperature": 0},
            timeout=llm.get("timeout_seconds", 180),
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return complete


def candidate_summary(profile: dict, resume: str) -> str:
    lines = [
        f"Degree: {profile.get('degree', 'bachelors')} in {profile.get('major', '')}"
        + (f", minor {profile['minor']}" if profile.get("minor") else ""),
        f"GPA: {profile.get('gpa', '')}",
        f"Graduation: {profile.get('graduation', '')}",
        f"Work authorization: {profile.get('work_authorization', '')}",
        *[f"- {h}" for h in profile.get("highlights", [])],
    ]
    if resume:
        lines += ["", "Resume:", resume.strip()]
    return "\n".join(lines)


def job_summary(job: Posting, max_chars: int) -> str:
    return "\n".join(
        [
            f"Company: {job.company}",
            f"Title: {job.title}",
            f"Track: {job.track} (priority {job.priority}, 1 = most wanted)",
            f"Season: {job.season}   Locations: {'; '.join(job.locations)}",
            f"Posted pay: {job.salary or 'not posted'}",
            "",
            job.description[:max_chars] or "(no description available; judge from title and firm)",
        ]
    )


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_ratings(reply: str) -> dict:
    """Pull the JSON object out of the reply and validate it. Raises ValueError if unusable."""
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.DOTALL)  # reasoning models
    m = _JSON.search(reply)
    if not m:
        raise ValueError("no JSON in reply")
    data = json.loads(m.group(0))
    ratings = {}
    for dim in DIMENSIONS:
        value = int(round(float(data[dim])))
        ratings[dim] = min(5, max(1, value))
    ratings["investment_banking"] = bool(data.get("investment_banking", False))
    ratings["reason"] = str(data.get("reason", "")).strip()[:300]
    return ratings


def weighted_score(ratings: dict, weights: dict) -> float:
    total = sum(weights.values())
    return round(sum(ratings[d] * weights[d] for d in DIMENSIONS) / total, 2)


def action_for(score: float, thresholds: dict) -> str:
    if score >= thresholds["tailored"]:
        return "tailored"
    if score >= thresholds["standard"]:
        return "standard"
    return "skip"


class Scorer:
    def __init__(self, cfg: dict, profile: dict, root: Path, complete: Complete | None = None):
        self.cfg = cfg
        self.complete = complete or openai_complete(cfg["llm"])
        resume_path = root / profile.get("resume_path", "config/resume.md")
        resume = resume_path.read_text(encoding="utf-8") if resume_path.exists() else ""
        if not resume:
            log.warning("no resume at %s; scoring from profile highlights only", resume_path)
        self.candidate = candidate_summary(profile, resume)
        self.cache_path = root / "data" / "scores.json"
        self.cache: dict = (
            json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        )

    def _rate(self, job: Posting) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"CANDIDATE\n{self.candidate}\n\nPOSTING\n"
                + job_summary(job, self.cfg["llm"].get("max_description_chars", 5000)),
            },
        ]
        try:
            return parse_ratings(self.complete(messages))
        except (ValueError, KeyError, json.JSONDecodeError):
            messages.append({"role": "user", "content": "Reply with the JSON object only."})
            return parse_ratings(self.complete(messages))  # one retry, then give up

    def score(self, jobs: list[Posting]) -> int:
        """Score each job (cached ones instantly). Returns how many new ratings were made."""
        new = 0
        limit = self.cfg.get("max_per_run", 200)
        for job in jobs:
            key = "|".join(job_key(job))
            ratings = self.cache.get(key)
            if ratings is None:
                if new >= limit:
                    job.flags.append("not scored yet")
                    continue
                try:
                    ratings = self._rate(job)
                except Exception as exc:  # model down or unusable reply: keep the role unscored
                    log.warning("could not score %s / %s: %s", job.company, job.title, exc)
                    job.flags.append("not scored yet")
                    continue
                self.cache[key] = ratings
                new += 1
            job.score = weighted_score(ratings, self.cfg["weights"])
            job.action = action_for(job.score, self.cfg["actions"])
            job.score_reason = ratings.get("reason", "")
            if ratings.get("investment_banking"):
                job.action = "skip"
                job.score_reason = "investment banking role. " + job.score_reason
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=1))
        return new
