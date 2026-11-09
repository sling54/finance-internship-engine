import json
from collections import Counter
from datetime import date

from outreach_fakes import make_tracker

from jobengine import cli
from jobengine.config import CONFIG_DIR, Config
from jobengine.pipeline import Result, record_history
from jobengine.stats import compute

TODAY = date(2026, 11, 9)
ROWS = [
    (date(2026, 9, 20), "Example Capital", "Trading Intern", "Rejected"),
    (date(2026, 10, 1), "Sample Markets", "Quant Intern", "Interview"),
    (date(2026, 10, 20), "Third Firm", "Research Intern", "Confirmed"),
    (date(2026, 11, 2), "Fourth Firm", "Data Intern", "Applied"),
    (date(2026, 11, 3), "Fifth Firm", "Wealth Intern", "Offer"),
    (date(2026, 11, 4), "Sixth Firm", "Ops Intern", "Ghosted"),
]


def project(tmp_path):
    tracker = make_tracker(tmp_path / "private/ApplicationTracker.xlsx", ROWS)
    data = tmp_path / "data"
    data.mkdir()
    (data / "scan_history.json").write_text(
        json.dumps(
            [
                {"date": "2026-11-01", "collected": 1200, "kept": 40, "dropped": 1160},
                {"date": "2026-11-02", "collected": 1300, "kept": 45, "dropped": 1255},
            ]
        )
    )
    (data / "seen.json").write_text(json.dumps(["a|x", "b|y", "c|z"]))
    statuses = ["sent", "replied", "bounced", "drafted", "approved", "skipped", "replied"]
    (data / "outreach.json").write_text(
        json.dumps({"entries": {str(i): {"status": s} for i, s in enumerate(statuses)}})
    )
    return tracker


def test_compute_counts_everything(tmp_path):
    s = compute(tmp_path, project(tmp_path), TODAY)
    assert (s.scans, s.collected, s.roles_seen, s.first_scan) == (2, 2500, 3, "2026-11-01")
    assert s.applications == 6
    assert (s.first_application, s.last_application) == (date(2026, 9, 20), date(2026, 11, 4))
    assert s.last_4_weeks == 4  # Oct 20 onward
    assert s.responded == 3  # Rejected, Interview, Offer; Confirmed is only a receipt
    assert s.interviews == 2
    assert s.outreach_sent == 4 and s.outreach["replied"] == 2


def test_text_and_markdown(tmp_path):
    s = compute(tmp_path, project(tmp_path), TODAY)
    text = s.text()
    assert "Employer responses" in text and "3 (50%)" in text
    assert "Outreach replies" in text and "2 (50%)" in text
    assert "Ghosted" in text
    md = s.markdown()
    assert md.splitlines()[:2] == ["| Metric | Value |", "|---|---|"]
    assert "| Postings collected | 2,500 |" in md
    assert "| Offer | 1 |" in md  # indented status rows lose their indent in the table


def test_compute_with_nothing_yet(tmp_path):
    s = compute(tmp_path, tmp_path / "missing.xlsx", TODAY)
    assert s.rows() == [("Applications", "0")]


def test_unknown_statuses_still_show(tmp_path):
    tracker = make_tracker(tmp_path / "t.xlsx", [(date(2026, 11, 1), "A", "B", "Phone call")])
    s = compute(tmp_path, tracker, TODAY)
    assert s.statuses == Counter({"Phone call": 1})
    assert ("  Phone call", "1") in s.rows()


def test_scan_history_is_appended_and_survives_damage(tmp_path):
    path = tmp_path / "scan_history.json"
    result = Result()
    result.collected = Counter({"ats": 10, "simplify": 5})
    record_history(path, "2026-11-08", result)
    record_history(path, "2026-11-09", result)
    assert [h["date"] for h in json.loads(path.read_text())] == ["2026-11-08", "2026-11-09"]
    path.write_text("{broken")
    record_history(path, "2026-11-10", result)
    assert json.loads(path.read_text()) == [
        {"date": "2026-11-10", "collected": 15, "kept": 0, "dropped": 0}
    ]


def test_stats_command(tmp_path, monkeypatch, capsys):
    project(tmp_path)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    load = Config.load

    def config():
        cfg = load(CONFIG_DIR)
        cfg.profile = {"tracker_path": "private/ApplicationTracker.xlsx"}
        return cfg

    monkeypatch.setattr(cli.Config, "load", config)
    assert cli.main(["stats", "--markdown"]) == 0
    assert "| Applications | 6 |" in capsys.readouterr().out
