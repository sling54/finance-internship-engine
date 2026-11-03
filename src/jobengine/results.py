"""Serve the latest scan results in a local browser."""

from __future__ import annotations

import csv
import re
import secrets
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from . import removed
from .filters.applied import AppliedFilter, read_tracker
from .models import Posting, Rejected
from .tracker_sync import TRACKER_OPEN_MESSAGE, record_application

FIELDS = ("company", "title", "track", "season", "locations", "fit_score", "salary")


def _number(value, default):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _salary(value):
    """Format a plain number like 162500 as $162,500; leave anything else unchanged."""
    text = value.strip()
    if not re.fullmatch(r"\$?\d[\d,]*(\.\d+)?", text):
        return value
    try:
        amount = float(text.lstrip("$").replace(",", ""))
    except ValueError:
        return value
    return f"${amount:,.0f}" if amount.is_integer() else f"${amount:,.2f}"


def _already_applied(tracker):
    """The scan's own tracker rule, so restarting the page never shows a recorded role."""
    applied = AppliedFilter(read_tracker(tracker))

    def check(row):
        try:
            applied(Posting(company=row["company"], title=row["title"], url="", source="results"))
        except Rejected:
            return True
        return False

    return check


def _apply_url(value):
    try:
        url = urlsplit(value)
        if url.scheme in {"http", "https"} and url.hostname:
            return value
    except ValueError:
        pass
    return ""


NOTES = {
    "not verified": "Not verified: we couldn't confirm this listing is still open.",
    "redirects to careers page": "Link goes to a careers home page; search for the role there.",
}


def _notes(flags):
    names = {flag.strip() for flag in flags.split(";")}
    return "".join(
        f'<strong class="note">{escape(text)}</strong>'
        for flag, text in NOTES.items()
        if flag in names
    )


def _page(roles, track, token, message):
    tracks = sorted({row["track"] for row in roles.values()} - {""})
    options = ['<option value="">All tracks</option>']
    for name in tracks:
        selected = " selected" if name == track else ""
        options.append(f'<option value="{escape(name)}"{selected}>{escape(name)}</option>')
    headers = "".join(f'<th scope="col">{field.replace("_", " ").title()}</th>' for field in FIELDS)
    rows = []
    for key, row in roles.items():
        if track and row["track"] != track:
            continue
        cells = "".join(
            f"<td>{escape(_salary(row[field]) if field == 'salary' else row[field])}</td>"
            for field in FIELDS
        )
        url = _apply_url(row["url"])
        link = (
            f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">Apply</a>'
            if url
            else "Apply link unavailable"
        )
        rows.append(
            f"<tr>{cells}<td>{link}{_notes(row['flags'])}"
            '<form method="post" action="/applied">'
            f'<input type="hidden" name="token" value="{token}">'
            f'<input type="hidden" name="track" value="{escape(track)}">'
            f'<button name="role" value="{key}">I applied</button></form>'
            '<form method="post" action="/closed">'
            f'<input type="hidden" name="token" value="{token}">'
            f'<input type="hidden" name="track" value="{escape(track)}">'
            f'<button name="role" value="{key}">Closed</button></form></td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="8">No roles to show.</td></tr>')
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Internship results</title>
<style>
body {{ font: 1rem system-ui; margin: 2rem; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
th, td {{ padding: .65rem; border-bottom: 1px solid #ccc; text-align: left; }}
td form {{ margin-top: .4rem; }}
.note {{ display: block; margin-top: .4rem; padding: .2rem .4rem; background: #fff3cd;
        border: 1px solid #d4a72c; color: #5c4400; font-size: .9rem; }}
</style>
<h1>Internship results</h1>
<p role="status">{escape(message)}</p>
<form method="get" action="/">
<label>Track <select name="track">{"".join(options)}</select></label>
<button>Filter</button>
</form>
<table><thead><tr>{headers}<th scope="col">Actions</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></html>"""


def results_server(root, tracker, rows):
    rows.sort(
        key=lambda row: (
            _number(row.get("priority"), float("inf")),
            -_number(row.get("fit_score"), -1),
        )
    )
    applied = _already_applied(tracker)
    gone = removed.load(root)
    roles = {}
    for index, row in enumerate(rows):
        role = {field: row.get(field) or "" for field in (*FIELDS, "url", "flags")}
        if not applied(role) and role["url"] not in gone:
            roles[str(index)] = role
    token = secrets.token_urlsafe(32)
    # Confirmation shown on the next page view, so refreshing never resubmits the form.
    notice = {"message": ""}

    class Handler(BaseHTTPRequestHandler):
        def _valid_host(self):
            return self.headers.get_all("Host") == [f"127.0.0.1:{self.server.server_port}"]

        def _render(self, track="", message="", status=200):
            body = _page(roles, track, token, message).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._valid_host():
                self.send_error(403)
                return
            path, _, query = self.path.partition("?")
            if path != "/":
                self.send_error(404)
                return
            message, notice["message"] = notice["message"], ""
            self._render(parse_qs(query).get("track", [""])[0], message)

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self._valid_host() or self.headers.get_all("Origin") != [origin]:
                self.send_error(403)
                return
            if self.path not in ("/applied", "/closed"):
                self.send_error(404)
                return
            length = _number(self.headers.get("Content-Length"), 0)
            if not 0 < length <= 8192 or self.headers.get_content_type() != (
                "application/x-www-form-urlencoded"
            ):
                self.send_error(400)
                return
            try:
                form = parse_qs(self.rfile.read(length).decode("ascii"), max_num_fields=3)
            except ValueError:
                self.send_error(400)
                return
            supplied = form.get("token", [""])[0]
            if not secrets.compare_digest(supplied.encode(), token.encode()):
                self.send_error(403)
                return

            key = form.get("role", [""])[0]
            track = form.get("track", [""])[0]
            row = roles.get(key)
            if row is None:
                self._render(track, "This role is no longer listed.", 404)
                return
            if self.path == "/closed":
                if not row["url"]:
                    self._render(track, "This role has no link, so it can't be removed.", 400)
                    return
                try:
                    removed.add(root, row["url"], row["company"], row["title"], "closed", "user")
                except Exception as exc:
                    self._render(track, f"Could not save the removed role: {exc}", 500)
                    return
                message = f"Removed: {row['company']} - {row['title']} (closed)."
            else:
                try:
                    message = record_application(
                        tracker, root, row["company"], row["title"], _apply_url(row["url"])
                    )
                except Exception as exc:
                    self._render(track, f"Could not update the tracker: {exc}", 500)
                    return
                if message == TRACKER_OPEN_MESSAGE:
                    self._render(track, message, 409)
                    return
            del roles[key]
            notice["message"] = message
            self.send_response(303)
            self.send_header("Location", "/" + ("?" + urlencode({"track": track}) if track else ""))
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    # Serialize requests so that concurrent clicks can't race tracker reads and writes.
    return HTTPServer(("127.0.0.1", 0), Handler)


def show_results(root, tracker):
    path = root / "data" / "jobs_latest.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError:
        print("No results yet. Run jobengine scan --no-ai first.")
        return 1

    with results_server(root, tracker, rows) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Results at {url} (press Ctrl+C to stop).", flush=True)
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
