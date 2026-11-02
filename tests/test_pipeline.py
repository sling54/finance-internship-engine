import csv

from helpers import load_fixture
from openpyxl import Workbook

from jobengine.pipeline import build_filters, mark_new, run_filters, write_csv
from jobengine.scanners.ats import parse_greenhouse
from jobengine.scanners.lists import parse_kadoa, parse_simplify


def make_tracker(path):
    wb = Workbook()
    ws = wb.active
    ws.append(["My applications"])  # a title row above the header, as real trackers have
    ws.append(["Company", "Position", "Status"])
    ws.append(["Example Markets LLC", "Quantitative Research Intern", "Applied"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def collected():
    return (
        parse_greenhouse("Example Prop", load_fixture("greenhouse.json"))
        + parse_simplify(load_fixture("simplify.json"), ["Quant"])
        + parse_kadoa(load_fixture("kadoa.json"))
    )


def test_end_to_end(cfg, tmp_path):
    make_tracker(tmp_path / "data" / "ApplicationTracker.xlsx")
    result = run_filters(collected(), build_filters(cfg, tmp_path))

    kept = {(j.company, j.title) for j in result.kept}
    assert ("Example Prop", "Quantitative Trader Intern - Summer 2027") in kept
    reasons = {j.title: r for j, r in result.rejected}
    # Simplify and Kadoa list the same role; the merged row matches the tracker despite the
    # different title wording ("- Summer 2027") and company suffix ("LLC").
    assert reasons["Quantitative Research Intern - Summer 2027"].startswith("already applied")
    assert reasons["Quant Researcher Intern - PhD"].startswith("excluded title term")
    assert reasons["Software Engineer"] == "not an internship"
    assert sum(result.collected.values()) == 5


def test_new_flag_and_csv(cfg, tmp_path):
    result = run_filters(collected(), build_filters(cfg, tmp_path))
    seen = tmp_path / "seen.json"
    mark_new(result.kept, seen)
    assert all("new" in j.flags for j in result.kept)

    again = run_filters(collected(), build_filters(cfg, tmp_path))
    mark_new(again.kept, seen)
    assert not any("new" in j.flags for j in again.kept)

    out = tmp_path / "jobs.csv"
    write_csv(out, again.kept)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 marker for Excel/Numbers
    rows = list(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert rows and rows[0]["company"] and rows[0]["url"].startswith("https://")
