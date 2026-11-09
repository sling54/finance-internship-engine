import csv
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calibration = load("calibration")


def row(company, score, track, reason="r"):
    return {
        "company": company,
        "title": "Intern",
        "track": track,
        "fit_score": str(score),
        "fit_reason": reason,
        "url": f"https://example.com/{company}",
    }


def test_pick_spreads_scores_and_tracks_and_skips_unscored():
    rows = [row(f"A{i}", 5, "Trading") for i in range(5)]
    rows += [row("B1", 5, "Quant / derivatives"), row("C1", 3, "Data analytics")]
    rows += [
        row("D1", 1, "Wealth management"),
        row("E1", 2, "Trading"),
        {**row("U", 0, "x"), "fit_score": ""},
    ]
    picked = calibration.pick(rows, n=6)
    assert len(picked) == 6
    assert {int(r["fit_score"]) for r in picked} == {5, 3, 2, 1}
    fives = [r for r in picked if r["fit_score"] == "5"]
    assert {r["track"] for r in fives} == {"Trading", "Quant / derivatives"}
    assert "U" not in [r["company"] for r in picked]


def test_make_and_compare(tmp_path):
    (tmp_path / "data").mkdir()
    rows = [
        row("Example Capital", 5, "Trading", "great fit"),
        row("Sample Markets", 2, "Data analytics"),
    ]
    with open(tmp_path / "data/jobs_latest.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    out = calibration.make(tmp_path)
    assert calibration.compare(out) == "No your_score filled in yet."

    filled = list(csv.DictReader(open(out, encoding="utf-8")))
    filled[0]["your_score"], filled[1]["your_score"] = "2", "2"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=calibration.FIELDS)
        writer.writeheader()
        writer.writerows(filled)
    text = calibration.compare(out)
    assert text.startswith("2 roles: 1 exact, 1 within 1 point (50%). Average AI minus you: +1.5.")
    assert "AI 5 vs you 2: Example Capital - Intern (Trading). AI said: great fit" in text


def test_check_forms_never_clicks_or_presses_keys():
    source = (SCRIPTS / "check_forms.py").read_text()
    assert ".click(" not in source and ".press(" not in source and ".tap(" not in source
