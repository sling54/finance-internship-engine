"""Feature 2: keep internships whose title matches a track in titles.yml."""

from __future__ import annotations

from ..models import Posting, Rejected
from ..textutil import phrase_pattern


class TitleFilter:
    def __init__(self, titles_cfg: dict):
        self.exclude = phrase_pattern(titles_cfg["exclude"])
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
        self.location_rules = {
            t["name"]: t.get("location_rule", "season") for t in titles_cfg["tracks"]
        }

    def __call__(self, job: Posting) -> None:
        title = job.title.replace("\u2019", "'")  # curly apostrophe: Master’s
        hit = self.exclude.search(title)
        if hit:
            raise Rejected(f"excluded title term: {hit.group(0)}")
        if not (job.is_internship or self.internship.search(title)):
            raise Rejected("not an internship")
        for name, priority, _, pattern in self.tracks:
            if pattern.search(title):
                job.track, job.priority = name, priority
                return
        if self.ambiguous.search(title) and not self.not_finance.search(title):
            job.track, job.priority = "Unclear division", 3
            job.flags.append("check division")
            return
        raise Rejected("no matching track")

    def location_rule(self, job: Posting) -> str:
        return self.location_rules.get(job.track, "season")
