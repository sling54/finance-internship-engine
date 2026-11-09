"""PlaywrightPage against a real (headless) Chromium and a local copy of a form.
Skipped where Playwright or its browser isn't installed, such as CI."""

from pathlib import Path

import pytest

from jobengine.formfill import PlaywrightPage, fill_form
from jobengine.packet import Field, Packet

sync_api = pytest.importorskip("playwright.sync_api")

FORM = """<!doctype html><form>
<label for="first_name">First Name</label><input id="first_name" name="first_name">
<label for="email">Email</label><input id="email" name="email">
<input type="file" id="resume" name="resume">
<select name="question_1"><option></option><option>Yes</option><option>No</option></select>
<label for="why">Why are you interested?</label><textarea id="why"></textarea>
<button type="submit" id="submit">Submit</button>
</form><script>document.forms[0].onsubmit = () => { document.title = "SUBMITTED"; return false; };</script>"""


@pytest.fixture
def page(tmp_path):
    html = tmp_path / "form.html"
    html.write_text(FORM)
    try:
        p = sync_api.sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as exc:  # no browser binary here
        pytest.skip(f"Chromium isn't available: {exc}")
    page = browser.new_page()
    page.goto(html.as_uri())
    yield page
    browser.close()
    p.stop()


def test_real_browser_fills_without_submitting(page, tmp_path):
    resume = tmp_path / "Resume.pdf"
    resume.write_bytes(b"%PDF fake")
    packet = Packet(
        "Example Capital",
        "Trading Intern",
        "https://job-boards.greenhouse.io/x/jobs/1",
        "https://job-boards.greenhouse.io/x/jobs/1",
        "greenhouse",
        fields=[
            Field("First Name", "first_name", answer="Alex", source="profile"),
            Field("Email", "email", answer="alex@example.com", source="profile"),
            Field("Resume/CV", "resume", kind="file", answer=str(resume), source="profile"),
            Field("Sponsorship?", "question_1", kind="select", answer="No", source="rule"),
            Field("Why are you interested?", "", kind="textarea", answer="Markets.", source="ai"),
            Field("Missing field", "nope", answer="x", source="profile"),
        ],
    )
    report = fill_form(PlaywrightPage(page), packet)
    assert page.input_value("#first_name") == "Alex"
    assert page.input_value("#email") == "alex@example.com"
    assert page.eval_on_selector("#resume", "e => e.files[0].name") == "Resume.pdf"
    assert page.input_value("select") == "No"
    assert page.input_value("#why") == "Markets."  # found by its label
    assert report.missed == ["Missing field"]
    assert page.title() != "SUBMITTED"
