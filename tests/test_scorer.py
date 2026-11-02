import json

import pytest
from helpers import job

from jobengine.scorer import LMStudio, ScoreReport, build_prompt, parse_reply, score_jobs

SCORING = {
    "enabled": True,
    "what_i_want": "Trading and quant internships.",
    "rubric": "5 = great, 1 = not a fit",
}
PROFILE = {"degree": "bachelors", "major": "Finance", "graduation": "2027-12"}


class FakeLM:
    """Stands in for LM Studio: returns canned replies and counts calls."""

    def __init__(self, replies=None, down=False):
        self.replies = list(replies or [])
        self.down = down
        self.calls = 0

    def pick_model(self):
        if self.down:
            raise ConnectionError("connection refused")
        return "qwen-test"

    def chat(self, system, user):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else '{"score": 4, "reason": "ok"}'
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize(
    "reply, expected",
    [
        ('{"score": 5, "reason": "Options market making"}', (5, "Options market making")),
        ('<think>hmm, trading...</think>\n{"score": 4, "reason": "Buy-side"}', (4, "Buy-side")),
        ('Sure! ```json\n{"score": "3", "reason": "Data role"}\n```', (3, "Data role")),
        ('thinking out loud</think>{"score": 2, "reason": "Sales"}', (2, "Sales")),
        ('"score": 1', (1, "")),
    ],
)
def test_parse_reply(reply, expected):
    assert parse_reply(reply) == expected


@pytest.mark.parametrize("reply", ['{"score": 9}', "no idea", '{"score": 0, "reason": "x"}'])
def test_parse_reply_rejects_bad_scores(reply):
    with pytest.raises(ValueError):
        parse_reply(reply)


def test_prompt_has_job_and_wants():
    j = job("Options Trading Intern", description="Make markets   in equity options.")
    prompt = build_prompt(j, SCORING, "Degree: bachelors")
    assert "Options Trading Intern" in prompt
    assert "Make markets in equity options." in prompt  # whitespace collapsed
    assert "Trading and quant internships." in prompt
    assert prompt.endswith("/no_think")


def test_scores_then_uses_cache(tmp_path):
    jobs = [job("Trading Intern", company="A"), job("Data Analyst Intern", company="B")]
    lm = FakeLM(['{"score": 5, "reason": "Trading"}', '{"score": 3, "reason": "Data"}'])
    report = score_jobs(jobs, SCORING, PROFILE, tmp_path, client=lm, progress=False)
    assert [j.fit_score for j in jobs] == [5, 3]
    assert report.scored == 2 and report.cached == 0
    assert json.loads((tmp_path / "data" / "scores.json").read_text())

    again = [job("Trading Intern", company="A"), job("Data Analyst Intern", company="B")]
    lm2 = FakeLM()
    report2 = score_jobs(again, SCORING, PROFILE, tmp_path, client=lm2, progress=False)
    assert lm2.calls == 0 and report2.cached == 2
    assert again[0].fit_reason == "Trading"


def test_editing_what_i_want_rescores(tmp_path):
    score_jobs([job()], SCORING, PROFILE, tmp_path, client=FakeLM(), progress=False)
    changed = {**SCORING, "what_i_want": "Only data roles."}
    lm = FakeLM()
    score_jobs([job()], changed, PROFILE, tmp_path, client=lm, progress=False)
    assert lm.calls == 1


def test_server_down_skips_without_crashing(tmp_path):
    jobs = [job()]
    report = score_jobs(jobs, SCORING, PROFILE, tmp_path, client=FakeLM(down=True), progress=False)
    assert "isn't reachable" in report.summary()
    assert jobs[0].fit_score is None


def test_turned_off(tmp_path):
    report = score_jobs([job()], {**SCORING, "enabled": False}, PROFILE, tmp_path, client=FakeLM())
    assert report.skipped.startswith("turned off")


def test_stops_after_repeated_failures(tmp_path):
    jobs = [job(f"Trading Intern {i}", company=f"Firm {i}") for i in range(6)]
    lm = FakeLM([TimeoutError("timed out")] * 6)
    report = score_jobs(jobs, SCORING, PROFILE, tmp_path, client=lm, progress=False)
    assert lm.calls == 3 and report.failed == 3 and "Stopped early" in report.summary()


def test_one_bad_reply_does_not_stop_the_run(tmp_path):
    jobs = [job("Trading Intern", company="A"), job("Quant Intern", company="B")]
    lm = FakeLM(["gibberish", '{"score": 5, "reason": "Quant"}'])
    report = score_jobs(jobs, SCORING, PROFILE, tmp_path, client=lm, progress=False)
    assert report.failed == 1 and report.scored == 1 and jobs[1].fit_score == 5


def test_summary_text():
    r = ScoreReport(model="qwen", scored=4, cached=10, seconds=8.0)
    assert r.summary() == "AI scoring (qwen): 4 scored in 0.1 min (avg 2.0s each), 10 from cache"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def reply(content, reasoning="", finish="stop"):
    message = {"content": content, "reasoning_content": reasoning}
    return FakeResponse({"choices": [{"message": message, "finish_reason": finish}]})


def test_thinking_cut_off_gives_clear_error(monkeypatch):
    lm = LMStudio("http://x/v1", max_tokens=1024)
    monkeypatch.setattr(
        lm.http, "post", lambda *a, **k: reply("", "Here's a thinking...", "length")
    )
    with pytest.raises(ValueError, match="max_tokens"):
        lm.chat("s", "u")


def test_asks_model_to_skip_thinking_and_notices_when_it_does_anyway(monkeypatch):
    sent = {}

    def post(url, json, timeout):
        sent.update(json)
        return reply('\n\n{"score": 5, "reason": "Trading"}', "Here's a thinking process...")

    lm = LMStudio("http://x/v1")
    monkeypatch.setattr(lm.http, "post", post)
    assert parse_reply(lm.chat("s", "u")) == (5, "Trading")
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["max_tokens"] == 4096 and lm.thought


def test_summary_reports_thinking(tmp_path):
    lm = FakeLM()
    lm.thought = True
    report = score_jobs([job()], SCORING, PROFILE, tmp_path, client=lm, progress=False)
    assert "thought before answering on 1 of 1" in report.summary()
