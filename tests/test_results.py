import re
from http.client import HTTPConnection
from threading import Thread
from urllib.parse import urlencode

import pytest
from openpyxl import Workbook, load_workbook

from jobengine import results


@pytest.fixture
def page(tmp_path):
    tracker = tmp_path / "ApplicationTracker.xlsx"
    wb = Workbook()
    wb.active.title = "Applications"
    wb.active.append(["Application Date", "Company", "Role Applied To"])
    wb.save(tracker)
    rows = [
        {"company": "Later Capital", "track": "research", "priority": "2", "fit_score": "5"},
        {"company": "Lower Fit Capital", "track": "research", "priority": "1", "fit_score": "2"},
        {
            "company": "=1+1",
            "title": '<script>alert("x")</script>',
            "track": "trading",
            "season": "Summer",
            "locations": "New York & Miami",
            "salary": "$30/hour",
            "priority": "1",
            "fit_score": "5",
            "url": "https://example.com/apply?a=1&b=2",
        },
        {"company": "Unsafe URL", "priority": "1", "url": "javascript:alert(1)"},
    ]
    with results.results_server(tmp_path, tracker, rows) as server:
        assert server.server_address[0] == "127.0.0.1"
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port, tracker
        finally:
            server.shutdown()
            thread.join()


def request(port, method="GET", path="/", body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read().decode()
    finally:
        conn.close()


def test_results_sort_filter_and_escape(page):
    port, _ = page
    status, html = request(port)
    assert status == 200
    assert html.index("=1+1") < html.index("Lower Fit Capital") < html.index("Unsafe URL")
    assert html.index("Unsafe URL") < html.index("Later Capital")
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "New York &amp; Miami" in html
    assert "Summer" in html and "$30/hour" in html
    assert 'href="https://example.com/apply?a=1&amp;b=2" target="_blank"' in html
    assert "javascript:" not in html
    _, filtered = request(port, path="/?track=trading")
    assert "=1+1" in filtered and "Lower Fit Capital" not in filtered


def apply(port, token, **headers):
    return request(
        port,
        "POST",
        "/applied",
        urlencode({"role": "0", "track": "trading", "token": token}),
        {
            "Origin": f"http://127.0.0.1:{port}",
            "Content-Type": "application/x-www-form-urlencoded",
            **headers,
        },
    )


def token_for(port):
    _, html = request(port)
    return re.search(r'name="token" value="([^"]+)"', html)[1]


def test_results_rejects_invalid_writes_and_records_valid_application(page):
    port, tracker = page
    token = token_for(port)
    assert apply(port, "wrong")[0] == 403
    assert apply(port, token, Host="example.com")[0] == 403
    assert apply(port, token, Origin="https://example.com")[0] == 403
    assert load_workbook(tracker).active.max_row == 1

    status, html = apply(port, token)
    assert status == 200
    assert "No roles to show." in html
    assert "=1+1" not in request(port)[1]
    sheet = load_workbook(tracker).active
    assert sheet["B2"].value == "=1+1" and sheet["B2"].data_type == "s"
    assert sheet["C2"].value == '<script>alert("x")</script>'
    assert sheet["D2"].value == "Applied"
    assert apply(port, token)[0] == 404


@pytest.mark.parametrize("locked", [True, False])
def test_results_keeps_role_if_tracker_cannot_be_written(page, locked):
    port, tracker = page
    if locked:
        tracker.with_name("~$" + tracker.name).touch()
    else:
        tracker.unlink()
    status, html = apply(port, token_for(port))
    assert status == (409 if locked else 500)
    assert ("Tracker is open in Excel" if locked else "Could not update the tracker") in html
    assert "=1+1" in html


def test_results_command_with_no_scan(tmp_path, monkeypatch, capsys):
    from jobengine import cli

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["results"]) == 1
    assert "jobengine scan --no-ai" in capsys.readouterr().out


def test_results_opens_default_browser(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "jobs_latest.csv").write_text("company,title\nExample Capital,Intern\n")
    opened = []
    monkeypatch.setattr(results.webbrowser, "open", opened.append)
    monkeypatch.setattr(results.HTTPServer, "serve_forever", lambda self: None)

    assert results.show_results(tmp_path, tmp_path / "ApplicationTracker.xlsx") == 0
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/", opened[0])
