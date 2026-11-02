import json

import pytest
from helpers import job

from jobengine.scoring import Scorer, action_for, parse_ratings, weighted_score

WEIGHTS = {"relevance": 0.30, "skills": 0.20, "pay": 0.20, "odds": 0.15, "learning": 0.15}
ACTIONS = {"tailored": 4.0, "standard": 3.0}


def reply(relevance=5, skills=4, pay=5, odds=3, learning=4, ib=False, reason="Options desk."):
    return json.dumps(
        {
            "relevance": relevance,
            "skills": skills,
            "pay": pay,
            "odds": odds,
            "learning": learning,
            "investment_banking": ib,
            "reason": reason,
        }
    )


def test_weighted_score_and_actions():
    ratings = parse_ratings(reply())
    # 5*.30 + 4*.20 + 5*.20 + 3*.15 + 4*.15 = 4.35
    assert weighted_score(ratings, WEIGHTS) == 4.35
    assert action_for(4.35, ACTIONS) == "tailored"
    assert action_for(3.0, ACTIONS) == "standard"
    assert action_for(2.99, ACTIONS) == "skip"


def test_parse_handles_reasoning_text_and_clamps():
    raw = "<think>long reasoning {not json}</think>Sure! " + reply(relevance=7, odds=0) + " done"
    ratings = parse_ratings(raw)
    assert ratings["relevance"] == 5 and ratings["odds"] == 1


def test_parse_rejects_missing_dimension():
    with pytest.raises(KeyError):
        parse_ratings('{"relevance": 5}')


@pytest.fixture
def scoring_cfg():
    return {
        "llm": {"max_description_chars": 100},
        "weights": WEIGHTS,
        "actions": ACTIONS,
        "max_per_run": 10,
    }


def test_scorer_caches_and_retries(tmp_path, scoring_cfg):
    calls = []

    def fake(messages):
        calls.append(messages)
        return "not json" if len(calls) == 1 else reply()

    profile = {"resume_path": "resume.md", "highlights": ["Derivatives lead"]}
    (tmp_path / "resume.md").write_text("Python, options pricing")
    scorer = Scorer(scoring_cfg, profile, tmp_path, complete=fake)
    j = job("Options Trading Intern", track="Trading", priority=1)
    assert scorer.score([j]) == 1
    assert len(calls) == 2  # first reply unusable, one retry
    assert "Python, options pricing" in calls[0][1]["content"]  # resume reached the model
    assert (j.score, j.action, j.score_reason) == (4.35, "tailored", "Options desk.")

    again = Scorer(scoring_cfg, profile, tmp_path, complete=fake)
    j2 = job("Options Trading Intern")
    assert again.score([j2]) == 0 and j2.score == 4.35  # from cache, no new call
    assert len(calls) == 2


def test_investment_banking_is_skipped(tmp_path, scoring_cfg):
    scorer = Scorer(scoring_cfg, {}, tmp_path, complete=lambda m: reply(ib=True))
    j = job("Summer Analyst Intern")
    scorer.score([j])
    assert j.action == "skip" and j.score_reason.startswith("investment banking")


def test_model_down_leaves_role_unscored(tmp_path, scoring_cfg):
    def down(messages):
        raise ConnectionError("LM Studio not running")

    j = job("Trading Intern")
    assert Scorer(scoring_cfg, {}, tmp_path, complete=down).score([j]) == 0
    assert j.score is None and "not scored yet" in j.flags
