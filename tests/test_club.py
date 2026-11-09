"""Settings each member keeps to themselves (config/*.local.yml), any OpenAI-compatible model
server, and `jobengine doctor`."""

from shutil import copytree, ignore_patterns

import pytest

from jobengine import cli, doctor
from jobengine.config import CONFIG_DIR, Config, deep_merge, load_yaml
from jobengine.outreach import load_settings
from jobengine.scorer import client_from


@pytest.fixture
def config(tmp_path):
    folder = tmp_path / "config"
    copytree(CONFIG_DIR, folder, ignore=ignore_patterns("profile.yml", "*.local.yml"))
    return folder


def test_deep_merge_merges_dicts_and_replaces_lists():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 2}
    assert deep_merge(base, {"a": {"y": [3]}, "c": 4}) == {"a": {"x": 1, "y": [3]}, "b": 2, "c": 4}
    assert base == {"a": {"x": 1, "y": [1, 2]}, "b": 2}  # the shared settings aren't changed


def test_local_files_override_shared_settings(config):
    (config / "sources.local.yml").write_text("jobspy:\n  enabled: false\n")
    (config / "scoring.local.yml").write_text("lm_studio:\n  model: smaller-model\n")
    (config / "outreach.local.yml").write_text("daily_send_limit: 5\n")
    tracks = load_yaml(config / "titles.yml")["tracks"][:1]
    (config / "titles.local.yml").write_text(
        "tracks:\n"
        + "".join(f"  - name: {t['name']}\n    priority: 1\n    any: [trader]\n" for t in tracks)
    )

    cfg = Config.load(config)
    assert cfg.sources["jobspy"]["enabled"] is False
    assert cfg.sources["jobspy"]["sites"]  # the rest of the shared block is kept
    assert cfg.scoring["lm_studio"]["model"] == "smaller-model"
    assert cfg.scoring["lm_studio"]["url"] == "http://localhost:1234/v1"
    assert [t["name"] for t in cfg.titles["tracks"]] == [tracks[0]["name"]]
    assert load_settings(config)["daily_send_limit"] == 5
    assert load_settings(config)["contacts_per_application"] == 2


def test_without_local_files_settings_are_the_shared_ones(config):
    assert Config.load(config).sources == load_yaml(CONFIG_DIR / "sources.yml")


def test_model_server_api_key_from_settings_or_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    local = client_from({"lm_studio": {"url": "http://localhost:1234/v1"}}, tmp_path)
    assert "Authorization" not in local.http.headers

    (tmp_path / ".env").write_text("LLM_API_KEY=from-env-file\n")
    hosted = client_from({"lm_studio": {"url": "https://api.example.com/v1"}}, tmp_path)
    assert hosted.http.headers["Authorization"] == "Bearer from-env-file"

    direct = client_from({"lm_studio": {"api_key": "in-settings"}}, tmp_path)
    assert direct.http.headers["Authorization"] == "Bearer in-settings"


# ---------- doctor ----------


def test_doctor_on_a_fresh_clone(config, tmp_path):
    cfg = Config.load(config)
    results = doctor.checks(cfg, tmp_path, model_check=lambda *a: (_ for _ in ()).throw(OSError()))
    found = dict((text.split(":")[0], level) for level, text in results)
    assert found["Profile"] == "fix"
    assert any(level == "fix" and text.startswith("Resume") for level, text in results)
    assert any(
        level == "--" and text.startswith("AI model not reachable") for level, text in results
    )
    assert any(text.startswith("Outreach: add HUNTER_API_KEY") for _, text in results)


def test_doctor_when_set_up(config, tmp_path, monkeypatch):
    (config / "profile.yml").write_text(
        "name: Alex Example\nresume_path: private/Resume.pdf\n"
        "tracker_path: private/ApplicationTracker.xlsx\nwhat_i_want: trading internships\n"
    )
    (tmp_path / "private").mkdir()
    (tmp_path / "private/Resume.pdf").write_bytes(b"%PDF")
    (tmp_path / "private/ApplicationTracker.xlsx").write_bytes(b"x")
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth/credentials.json").write_text("{}")
    (tmp_path / "auth/token.json").write_text("{}")
    (tmp_path / ".env").write_text("HUNTER_API_KEY=k\n")
    (config / "outreach_templates.local.yml").write_text("templates: {}\n")
    monkeypatch.setattr(doctor, "_has", lambda module: True)

    cfg = Config.load(config)
    results = doctor.checks(cfg, tmp_path, model_check=lambda *a: "qwen-test")
    oks = [text for level, text in results if level == "ok"]
    assert (
        "Profile for Alex Example" in oks
        and "AI model qwen-test at http://localhost:1234/v1" in oks
    )
    assert "Gmail signed in" in oks and "Hunter.io key for outreach" in oks
    assert not [text for level, text in results if level == "fix"]


def test_doctor_flags_tracker_open_in_excel(config, tmp_path):
    (config / "profile.yml").write_text("name: A\ntracker_path: t.xlsx\n")
    (tmp_path / "t.xlsx").write_bytes(b"x")
    (tmp_path / "~$t.xlsx").write_text("")
    results = doctor.checks(Config.load(config), tmp_path, model_check=lambda *a: "m")
    assert ("--", "Tracker is open in Excel; close it before track, outreach or applied") in results


def test_doctor_command_exit_code(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    load = Config.load
    monkeypatch.setattr(cli.Config, "load", lambda: load(config))
    monkeypatch.setattr("jobengine.scorer.LMStudio.pick_model", lambda self: "m")
    assert cli.main(["doctor"]) == 1  # no profile yet
    out = capsys.readouterr().out
    assert "fix  Profile: run jobengine init --resume" in out and "to fix." in out
