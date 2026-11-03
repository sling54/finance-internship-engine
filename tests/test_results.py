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

    status, _ = apply(port, token)
    assert status == 303
    html = request(port, path="/?track=trading")[1]
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


def raw_post(port, token, role="0", track="trading"):
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(
            "POST",
            "/applied",
            urlencode({"role": role, "track": track, "token": token}),
            {
                "Origin": f"http://127.0.0.1:{port}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        response = conn.getresponse()
        response.read()
        return response.status, response.getheader("Location")
    finally:
        conn.close()


def test_applied_redirects_and_shows_message_once(page):
    port, _ = page
    status, location = raw_post(port, token_for(port))
    assert status == 303 and location == "/?track=trading"
    _, html = request(port, path=location)
    assert "Added: =1+1" in html
    assert "no longer listed" not in html
    # Refreshing is a plain GET: no resubmission, and the confirmation is not repeated.
    _, again = request(port, path=location)
    assert "Added:" not in again and "no longer listed" not in again


def test_applied_redirect_without_track_filter(page):
    port, _ = page
    assert raw_post(port, token_for(port), track="") == (303, "/")


@pytest.mark.parametrize(
    "value,shown",
    [
        ("162500", "$162,500"),
        ("162500.0", "$162,500"),
        ("$1,000", "$1,000"),
        ("25.5", "$25.50"),
        ("$30/hour", "$30/hour"),
        ("100000-120000 yearly", "100000-120000 yearly"),
        ("Competitive", "Competitive"),
        ("", ""),
    ],
)
def test_salary_format(value, shown):
    assert results._salary(value) == shown


def test_salary_shown_formatted_on_page(tmp_path):
    rows = [{"company": "Pay Capital", "title": "Intern", "salary": "162500"}]
    with results.results_server(tmp_path, tmp_path / "missing.xlsx", rows) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            html = request(server.server_port)[1]
        finally:
            server.shutdown()
            thread.join()
    assert "<td>$162,500</td>" in html and "162500" not in html


def serve_once(root, tracker, rows, action):
    with results.results_server(root, tracker, [dict(r) for r in rows]) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            return action(server.server_port)
        finally:
            server.shutdown()
            thread.join()


def test_recorded_role_stays_hidden_after_restart(tmp_path):
    tracker = tmp_path / "ApplicationTracker.xlsx"
    wb = Workbook()
    wb.active.append(["Application Date", "Company", "Role Applied To"])
    wb.save(tracker)
    rows = [
        {"company": "Acme Capital", "title": "Summer Analyst", "track": "research"},
        {"company": "Other Partners", "title": "Trading Intern", "track": "trading"},
    ]

    def click_applied(port):
        token = token_for(port)
        assert "Acme Capital" in request(port)[1]
        assert raw_post(port, token, role="0", track="")[0] == 303

    serve_once(tmp_path, tracker, rows, click_applied)
    assert load_workbook(tracker).active["B2"].value == "Acme Capital"

    html = serve_once(tmp_path, tracker, rows, lambda port: request(port)[1])
    assert "Acme Capital" not in html
    assert "Other Partners" in html


def test_existing_tracker_row_hidden_with_scan_matching(tmp_path):
    tracker = tmp_path / "ApplicationTracker.xlsx"
    wb = Workbook()
    wb.active.append(["Application Date", "Company", "Role Applied To"])
    wb.active.append(["2026-10-01", "Acme Capital LLC", "Summer Analyst"])
    wb.save(tracker)
    rows = [
        {"company": "Acme Capital", "title": "Summer Analyst"},
        {"company": "Acme Capital", "title": "Quant Researcher"},
    ]
    from jobengine.filters.applied import AppliedFilter, read_tracker
    from jobengine.models import Posting, Rejected

    scan = AppliedFilter(read_tracker(tracker))
    dropped = []
    for row in rows:
        try:
            scan(Posting(company=row["company"], title=row["title"], url="", source="t"))
        except Rejected:
            dropped.append(row["title"])

    html = serve_once(tmp_path, tracker, rows, lambda port: request(port)[1])
    for row in rows:
        assert (row["title"] in html) == (row["title"] not in dropped)
    assert dropped == ["Summer Analyst"]


def close(port, token, role="0", track="trading", **headers):
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request(
            "POST",
            "/closed",
            urlencode({"role": role, "track": track, "token": token}),
            {
                "Origin": f"http://127.0.0.1:{port}",
                "Content-Type": "application/x-www-form-urlencoded",
                **headers,
            },
        )
        response = conn.getresponse()
        body = response.read().decode()
        return response.status, response.getheader("Location"), body
    finally:
        conn.close()


def test_closed_removes_role_without_touching_tracker(page):
    from jobengine import removed

    port, tracker = page
    root = tracker.parent
    before = tracker.read_bytes()
    _, html = request(port)
    assert 'action="/closed"' in html and ">Closed</button>" in html

    status, location, _ = close(port, token_for(port))
    assert (status, location) == (303, "/?track=trading")
    _, html = request(port, path=location)
    assert "Removed: =1+1 - &lt;script&gt;" in html and "(closed)" in html
    assert "<td>=1+1</td>" not in html
    assert "Removed:" not in request(port, path=location)[1]  # shown once

    entry = removed.load(root)["https://example.com/apply?a=1&b=2"]
    assert entry["reason"] == "closed" and entry["by"] == "user"
    assert entry["company"] == "=1+1"
    assert tracker.read_bytes() == before


@pytest.mark.parametrize(
    "headers,bad_token",
    [({"Host": "example.com"}, False), ({"Origin": "https://example.com"}, False), ({}, True)],
)
def test_closed_rejects_foreign_requests(page, headers, bad_token):
    from jobengine import removed

    port, tracker = page
    token = "wrong" if bad_token else token_for(port)
    assert close(port, token, **headers)[0] == 403
    assert not removed.path(tracker.parent).exists()
    assert "<td>=1+1</td>" in request(port)[1]


def test_closed_role_without_link_stays_listed(page):
    from jobengine import removed

    port, tracker = page
    status, _, html = close(port, token_for(port), role="3", track="")
    assert status == 400 and "no link" in html and "Later Capital" in html
    assert not removed.path(tracker.parent).exists()


def test_closed_role_stays_hidden_after_restart(tmp_path):
    rows = [
        {"company": "Shut Capital", "title": "Intern", "url": "https://example.com/shut"},
        {"company": "Open Capital", "title": "Intern", "url": "https://example.com/open"},
    ]
    tracker = tmp_path / "missing.xlsx"

    def click_closed(port):
        assert close(port, token_for(port), role="0", track="")[0] == 303

    serve_once(tmp_path, tracker, rows, click_closed)
    html = serve_once(tmp_path, tracker, rows, lambda port: request(port)[1])
    assert "Shut Capital" not in html and "Open Capital" in html
    assert not tracker.exists()
