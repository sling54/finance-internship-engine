"""Feature 2: keep internships whose title matches a track in titles.yml."""

from __future__ import annotations

import re

from ..models import Posting, Rejected
from ..textutil import normalize, phrase_pattern


class TitleFilter:
    def __init__(self, titles_cfg: dict):
        self.exclude = phrase_pattern(titles_cfg["exclude"])
        # "Sales & Trading" survives the "sales" exclude: the excepted phrase is blanked first.
        self.exclude_except = (
            phrase_pattern(titles_cfg["exclude_except"])
            if titles_cfg.get("exclude_except")
            else None
        )
        self.internship = phrase_pattern(titles_cfg["internship_keywords"])
        self.ambiguous = phrase_pattern(titles_cfg.get("ambiguous_keywords", []))
        self.not_finance = phrase_pattern(titles_cfg.get("ambiguous_exclude", ["technology"]))
        self.tracks = [
            (
                t["name"],
                t["priority"],
                t.get("location_rule", "season"),
                phrase_pattern(t["keywords"]),
            )
            for t in titles_cfg["tracks"]
        ]
        priorities = {name: priority for name, priority, _, _ in self.tracks}
        self.reassign = []
        for rule in titles_cfg.get("reassign", []):
            target = rule["to"]
            if target not in priorities:
                raise ValueError(f"unknown reassignment track: {target}")
            self.reassign.append(
                (
                    target,
                    priorities[target],
                    phrase_pattern(rule["when_any"]),
                    phrase_pattern(rule["unless_any"]) if rule.get("unless_any") else None,
                )
            )
        self.graduate_only = (
            phrase_pattern(titles_cfg["graduate_only"]) if titles_cfg.get("graduate_only") else None
        )
        self.location_rules = {
            t["name"]: t.get("location_rule", "season") for t in titles_cfg["tracks"]
        }
        # The profile's school, so its own one-school programs are kept (set by the pipeline).
        self.school = normalize(titles_cfg.get("my_school") or "")

    def __call__(self, job: Posting) -> None:
        title = job.title.replace("\u2019", "'")  # curly apostrophe: Master’s
        hit = (
            self.graduate_only.search(title) or self.graduate_only.search(normalize(job.url))
            if self.graduate_only
            else None
        )
        if hit:
            raise Rejected(f"graduate-only role: {hit.group(0)}")
        checked = self.exclude_except.sub(" ", title) if self.exclude_except else title
        hit = self.exclude.search(checked)
        if hit:
            raise Rejected(f"excluded title term: {hit.group(0)}")
        if not (job.is_internship or self.internship.search(title)):
            raise Rejected("not an internship")
        self._check_precision(job, title)
        for name, priority, _, pattern in self.tracks:
            if pattern.search(title):
                job.track, job.priority = name, priority
                for target, target_priority, when, unless in self.reassign:
                    if when.search(title) and (unless is None or not unless.search(title)):
                        job.track, job.priority = target, target_priority
                return
        if self.ambiguous.search(title) and not self.not_finance.search(title):
            job.track, job.priority = "Unclear division", 3
            job.flags.append("check division")
            return
        raise Rejected("no matching track")

    def _check_precision(self, job: Posting, title: str) -> None:
        one_school = _ONE_SCHOOL.search(title)
        if one_school and not self._my_school(one_school.group(1)):
            raise Rejected(f"program for one school: {one_school.group(1).strip()}")
        if not self.internship.search(title) and _full_time_description(job):
            # A list marked it as an internship, but the firm's own posting never mentions
            # interns or students (Jane Street's evergreen "Quantitative Trader").
            raise Rejected("not an internship: the firm's posting never mentions interns")

    def _my_school(self, named: str) -> bool:
        named = normalize(named)
        return bool(self.school) and (self.school in named or named in self.school)

    def location_rule(self, job: Posting) -> str:
        return self.location_rules.get(job.track, "season")


# "Quantitative Research Intern, Summer 2027 | Columbia University": a program for one school.
_ONE_SCHOOL = re.compile(
    r"\|\s*([^|]*\b(?:university|college|institute of technology|harvard|yale|princeton|"
    r"stanford|wharton)\b[^|]*)$",
    re.IGNORECASE,
)
# Words that show a description is about an internship (or a student program).
_STUDENT_WORDS = re.compile(
    r"\b(intern|interns|internship|internships|co-?op|students?|summer|campus|graduat\w*|"
    r"undergrad\w*|university|class of)\b",
    re.IGNORECASE,
)


def _full_time_description(job: Posting) -> bool:
    text = job.description or ""
    return len(text) >= 300 and not _STUDENT_WORDS.search(text)
