from types import SimpleNamespace

from helpers import job
from test_scorer import PROFILE, SCORING, FakeLM

import jobengine.pipeline as pipeline
import jobengine.scorer as scorer


def fake_clock(monkeypatch, step):
    now = [0.0]

    def tick():
        now[0] += step
        return now[0]

    monkeypatch.setattr(scorer.time, "monotonic", tick)


def test_scoring_stops_at_time_limit_and_next_scan_continues(tmp_path, monkeypatch):
    fake_clock(monkeypatch, 60)  # every clock read moves one minute
    jobs = [job(f"Trading Intern {i}", company=f"Firm {i}") for i in range(10)]
    lm = FakeLM()
    report = scorer.score_jobs(
        jobs, {**SCORING, "max_minutes": 5}, PROFILE, tmp_path, client=lm, progress=False
    )
    assert 0 < report.scored < 10
    assert "time limit of 5 min" in report.summary()
    assert f"{10 - report.scored} roles left" in report.summary()
    assert jobs[-1].fit_score is None

    again = [job(f"Trading Intern {i}", company=f"Firm {i}") for i in range(10)]
    report2 = scorer.score_jobs(
        again, {**SCORING, "max_minutes": 0}, PROFILE, tmp_path, client=FakeLM(), progress=False
    )
    assert report2.cached == report.scored and report2.scored == 10 - report.scored
    assert all(j.fit_score for j in again)


def test_no_limit_when_zero(tmp_path, monkeypatch):
    fake_clock(monkeypatch, 3600)
    jobs = [job(f"Quant Intern {i}", company=f"Firm {i}") for i in range(4)]
    report = scorer.score_jobs(
        jobs, {**SCORING, "max_minutes": 0}, PROFILE, tmp_path, client=FakeLM(), progress=False
    )
    assert report.scored == 4 and not report.stopped


def test_listing_written_before_ai_scoring(tmp_path, monkeypatch):
    kept = [job("Trading Intern", company="A")]
    result = pipeline.Result(kept=kept, rejected=[(job("Sales Intern"), "excluded title term")])
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "run_filters", lambda *a, **k: result)
    monkeypatch.setattr(pipeline, "build_filters", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.DriveZone, "load", staticmethod(lambda root: None))
    monkeypatch.setattr(pipeline, "check_links", lambda *a, **k: None)

    def scoring_killed(*a, **k):
        assert (tmp_path / "data" / "jobs_latest.csv").exists()
        raise KeyboardInterrupt  # the scan is stopped while scoring

    monkeypatch.setattr(pipeline, "score_jobs", scoring_killed)
    cfg = SimpleNamespace(scoring={}, profile={}, sources={})
    try:
        pipeline.run(cfg, root=tmp_path)
    except KeyboardInterrupt:
        pass
    text = (tmp_path / "data" / "jobs_latest.csv").read_text(encoding="utf-8-sig")
    assert "Trading Intern" in text
    assert list((tmp_path / "data").glob("rejected_*.csv"))
