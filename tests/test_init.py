from shutil import copytree

import pytest
from openpyxl import load_workbook

from jobengine import cli
from jobengine.config import CONFIG_DIR, Config, load_yaml


@pytest.fixture
def project(tmp_path, monkeypatch):
    config = tmp_path / "config"
    copytree(CONFIG_DIR, config, ignore=lambda *_: ["profile.yml", "firms.local.yml"])
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    load = Config.load
    monkeypatch.setattr(cli.Config, "load", lambda: load(config))
    return tmp_path


def answer_prompts(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _: next(replies, ""))


def test_init_fresh_setup(project, monkeypatch, capsys):
    answer_prompts(
        monkeypatch,
        "Alex Example",
        "Example State University",
        "",
        "",
        "Economics",
        "3.50",
        "2027-12",
        "",
        "2028-06",
        "needs_sponsorship",
        "",
        "11200 SW 8th St, Miami, FL 33199",
        "-80.3733",
        "25.7562",
    )

    assert cli.main(["init"]) == 0
    profile = load_yaml(project / "config/profile.yml")
    assert profile == {
        "name": "Alex Example",
        "school": "Example State University",
        "degree": "bachelors",
        "major": "Finance",
        "minor": "Economics",
        "gpa": 3.5,
        "graduation": "2027-12",
        "grad_window": {"start": "2027-12", "end": "2028-06"},
        "work_authorization": "needs_sponsorship",
        "home_base": {
            "name": "Example State University",
            "address": "11200 SW 8th St, Miami, FL 33199",
            "lon": -80.3733,
            "lat": 25.7562,
            "max_drive_minutes": 60,
        },
        "tracker_path": "private/ApplicationTracker.xlsx",
        "resume_path": "private/Resume.pdf",
    }
    sheet = load_workbook(project / profile["tracker_path"])["Applications"]
    assert list(sheet.values) == [("Application Date", "Company", "Role Applied To")]
    assert "jobengine scan --no-ai" in capsys.readouterr().out


@pytest.mark.parametrize("confirmation", ["", "no"])
def test_init_keeps_profile_without_confirmation(project, monkeypatch, confirmation):
    profile = project / "config/profile.yml"
    profile.write_text("name: Existing Example\n")
    replies = iter([confirmation])
    monkeypatch.setattr("builtins.input", lambda _: next(replies))

    assert cli.main(["init"]) == 0
    assert profile.read_text() == "name: Existing Example\n"
    assert not (project / "private").exists()


def test_init_keeps_existing_tracker_and_replaces_confirmed_profile(project, monkeypatch):
    profile = project / "config/profile.yml"
    profile.write_text("name: Existing Example\n")
    tracker = project / "private/ApplicationTracker.xlsx"
    tracker.parent.mkdir()
    tracker.write_bytes(b"existing tracker contents")
    answer_prompts(monkeypatch, "yes", "Alex Example")

    assert cli.main(["init"]) == 0
    assert tracker.read_bytes() == b"existing tracker contents"
    assert load_yaml(profile)["name"] == "Alex Example"


def test_applied_accepts_tracker_created_by_init(project, monkeypatch):
    answer_prompts(monkeypatch)
    assert cli.main(["init"]) == 0
    assert cli.main(["applied", "--company", "Example Capital", "--role", "Finance Intern"]) == 0

    sheet = load_workbook(project / "private/ApplicationTracker.xlsx")["Applications"]
    assert [sheet[cell].value for cell in ("B2", "C2", "D2")] == [
        "Example Capital",
        "Finance Intern",
        "Applied",
    ]


def test_applied_still_requires_company_or_search(project):
    with pytest.raises(SystemExit, match="2"):
        cli.main(["applied"])


def test_zone_requires_coordinates_when_skipped_in_init(project, monkeypatch, capsys):
    answer_prompts(monkeypatch)
    assert cli.main(["init"]) == 0
    monkeypatch.setattr(cli, "api_key", lambda _: "fake-key")

    assert cli.main(["zone"]) == 1
    assert "Set campus longitude and latitude" in capsys.readouterr().out
