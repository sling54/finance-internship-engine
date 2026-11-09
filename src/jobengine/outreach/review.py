"""`jobengine outreach review`: a local page to approve or skip each outreach draft.

The page shows each draft as it is in Gmail right now (so your edits there show up), with a
link to open it in Gmail. Approve marks it for `jobengine outreach send`; Skip deletes the
draft. It listens on 127.0.0.1 only, and every change needs the page's one-time token, the
same protections as `jobengine results`.
"""

from __future__ import annotations

import secrets
import webbrowser
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from .mail import delete_draft, draft_link, get_draft
from .send import label, resolve_gone
from .store import Log

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def _card(key: str, e, draft: dict, token: str) -> str:
    hidden = (
        f'<input type="hidden" name="token" value="{token}">'
        f'<input type="hidden" name="entry" value="{escape(key)}">'
    )
    return f"""<section>
<h2>{escape(e.company)} - {escape(e.role)}</h2>
<p><strong>To:</strong> {escape(draft["to"])} ({escape(e.position or e.kind)})<br>
<strong>Subject:</strong> {escape(draft["subject"])}</p>
<pre>{escape(draft["body"])}</pre>
<p><a href="{escape(draft_link(e.message_id))}" target="_blank" rel="noopener noreferrer">Open in Gmail</a> to edit.</p>
<form method="post" action="/approve">{hidden}<button>Approve</button></form>
<form method="post" action="/skip">{hidden}<button>Skip</button></form>
</section>"""


def page(cards: list[str], message: str, approved: int) -> str:
    body = "".join(cards) or "<p>No drafts waiting for review.</p>"
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Outreach drafts</title>
<style>
body {{ font: 1rem system-ui; margin: 2rem; max-width: 50rem; }}
section {{ border-bottom: 1px solid #ccc; padding: 1rem 0; }}
pre {{ white-space: pre-wrap; font: inherit; background: #f4f4f4; padding: .75rem; }}
form {{ display: inline-block; margin-right: .5rem; }}
</style>
<h1>Outreach drafts</h1>
<p role="status">{escape(message)}</p>
<p>{approved} approved and waiting for <code>jobengine outreach send</code>.</p>
{body}</html>"""


def review_server(gmail, log_: Log, now=datetime.now) -> HTTPServer:
    token = secrets.token_urlsafe(32)
    notice = {"message": ""}

    def cards() -> list[str]:
        out = []
        changed = False
        for key, e in list(log_.entries.items()):
            if e.status != "drafted":
                continue
            draft = get_draft(gmail, e.draft_id)
            if draft is None:
                notice["message"] = resolve_gone(gmail, e, now())
                changed = True
                continue
            out.append(_card(key, e, draft, token))
        if changed:
            log_.save()
        return out

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _valid_host(self):
            return self.headers.get_all("Host") == [f"127.0.0.1:{self.server.server_port}"]

        def _send(self, status: int, body: bytes = b"", location: str = ""):
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            else:
                self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._valid_host():
                self.send_error(403)
                return
            if self.path != "/":
                self.send_error(404)
                return
            try:
                shown = cards()
            except Exception as exc:
                self._send(502, f"Couldn't read the drafts from Gmail: {escape(str(exc))}".encode())
                return
            message, notice["message"] = notice["message"], ""
            approved = sum(e.status == "approved" for e in log_.entries.values())
            self._send(200, page(shown, message, approved).encode("utf-8"))

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self._valid_host() or self.headers.get_all("Origin") != [origin]:
                self.send_error(403)
                return
            if self.path not in ("/approve", "/skip"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not 0 < length <= 8192 or self.headers.get_content_type() != (
                "application/x-www-form-urlencoded"
            ):
                self.send_error(400)
                return
            try:
                form = parse_qs(self.rfile.read(length).decode("ascii"), max_num_fields=2)
            except ValueError:
                self.send_error(400)
                return
            supplied = form.get("token", [""])[0]
            if not secrets.compare_digest(supplied.encode(), token.encode()):
                self.send_error(403)
                return
            e = log_.entries.get(form.get("entry", [""])[0])
            if e is None or e.status != "drafted":
                notice["message"] = "That draft was already handled."
            elif self.path == "/approve":
                e.status, e.approved_at = "approved", now().isoformat(timespec="seconds")
                log_.save()
                notice["message"] = f"Approved: {label(e)}."
            else:
                try:
                    delete_draft(gmail, e.draft_id)
                except Exception as exc:
                    notice["message"] = f"Couldn't delete the draft in Gmail: {exc}"
                else:
                    e.status = "skipped"
                    log_.save()
                    notice["message"] = f"Skipped and deleted: {label(e)}."
            self._send(303, location="/")

    return HTTPServer(("127.0.0.1", 0), Handler)


def show_review(gmail, log_: Log) -> int:
    with review_server(gmail, log_) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Outreach drafts at {url} (press Ctrl+C to stop).", flush=True)
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
