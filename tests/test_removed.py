import csv
import subprocess
from datetime import date
from pathlib import Path

import pytest
from helpers import job
from test_pipeline import collected

from jobengine import cli, pipeline, removed
from jobengine.filters.removed import RemovedFilter
from jobengine.models import Rejected

URL = "https://boards.greenhouse.io/example/jobs/1"


def test_add_contains_remove(tmp_path):
    assert not removed.contains(tmp_path, URL)
    assert removed.add(tmp_path, URL, "Acme", "Intern", "closed", "user", date(2026, 11, 3))
    assert removed.contains(tmp_path, URL)
    assert not removed.contains(tmp_path, URL + "?x=1")  # exact URL only
    assert removed.load(tmp_path)[URL] == {
        "company": "Acme",
        "title": "Intern",
        "reason": "closed",
        "date": "2026-11-03",
        "by": "user",
    }
    # Adding again keeps the first entry.
    assert not removed.add(tmp_path, URL, "Acme", "Intern", "link dead", "link_check")
    assert removed.load(tmp_path)[URL]["reason"] == "closed"

    assert removed.remove(tmp_path, URL)["company"] == "Acme"
    assert not removed.contains(tmp_path, URL)
    assert removed.remove(tmp_path, URL) is None
    assert removed.path(tmp_path) == tmp_path / "data" / "removed.json"


@pytest.mark.parametrize("by", removed.REMOVED_BY)
def test_every_remover_is_accepted(tmp_path, by):
    assert removed.add(tmp_path, URL, "Acme", "Intern", "closed", by)
    assert removed.load(tmp_path)[URL]["by"] == by


@pytest.mark.parametrize(
    "url,reason,by",
    [("", "closed", "user"), (URL, "", "user"), (URL, "closed", "someone_else")],
)
def test_add_rejects_bad_entries(tmp_path, url, reason, by):
    with pytest.raises(ValueError):
        removed.add(tmp_path, url, "Acme", "Intern", reason, by)
    assert not removed.path(tmp_path).exists()


def test_removed_file_is_git_ignored():
    repo = Path(__file__).resolve().parents[1]
    out = subprocess.run(["git", "check-ignore", "-q", "data/removed.json"], cwd=repo, check=False)
    assert out.returncode == 0


def test_filter_reason():
    check = RemovedFilter({URL: {"reason": "closed"}})
    with pytest.raises(Rejected, match="^removed: closed$"):
        check(_job(URL))
    check(_job("https://example.com/other"))  # not listed: kept


def _job(url):
    posting = job()
    posting.url = url
    return posting


def _scan(cfg, root, monkeypatch):
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kw: collected())
    result = pipeline.run(cfg, root=root, ai=False)
    rejected = root / "data" / f"rejected_{date.today().isoformat()}.csv"
    with rejected.open(encoding="utf-8-sig") as fh:
        reasons = {row["url"]: row["reason"] for row in csv.DictReader(fh)}
    return {j.url for j in result.kept}, reasons


def test_role_stays_removed_across_scans_until_restored(cfg, tmp_path, monkeypatch):
    kept, _ = _scan(cfg, tmp_path, monkeypatch)
    target = next(j for j in collected() if j.url in kept)

    removed.add(tmp_path, target.url, target.company, target.title, "closed", "user")
    for _ in range(2):  # stays removed on every scan, not just the next one
        kept, reasons = _scan(cfg, tmp_path, monkeypatch)
        assert target.url not in kept
        assert reasons[target.url] == "removed: closed"
        with (tmp_path / "data" / "jobs_latest.csv").open(encoding="utf-8-sig") as fh:
            assert target.url not in {row["url"] for row in csv.DictReader(fh)}

    removed.remove(tmp_path, target.url)
    kept, reasons = _scan(cfg, tmp_path, monkeypatch)
    assert target.url in kept and target.url not in reasons


def test_removed_and_restore_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["removed"]) == 0
    assert "No removed roles." in capsys.readouterr().out

    removed.add(tmp_path, URL, "Acme", "Intern", "closed", "user", date(2026, 11, 3))
    assert cli.main(["removed"]) == 0
    out = capsys.readouterr().out
    assert "2026-11-03  closed (by user)  Acme - Intern" in out and URL in out

    assert cli.main(["restore", URL]) == 0
    assert "Restored: Acme - Intern" in capsys.readouterr().out
    assert not removed.contains(tmp_path, URL)
    assert cli.main(["restore", URL]) == 1
    assert "No removed role has that URL" in capsys.readouterr().out
