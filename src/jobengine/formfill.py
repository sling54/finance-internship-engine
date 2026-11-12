"""`jobengine packet ... --fill`: types a packet's answers into the real form. You click Submit.

A Chromium window opens on the apply page (sign-ins are kept in data/browser-profile, so a
Workday or Handshake login only happens once). Each answer is put in the field the board
names (Greenhouse), a known field (Lever), or the field with that label (anything else).
Fields it can't find are listed for you. There is no code here that clicks a button: the
page interface below has no click at all, so Submit is always yours.

Needs: pip install -e ".[apply]" && playwright install chromium
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .packet import Field, Packet

log = logging.getLogger(__name__)

# Lever's application form uses the same field names on every company's page.
LEVER = {
    "full name": 'input[name="name"]',
    "name": 'input[name="name"]',
    "email": 'input[name="email"]',
    "phone": 'input[name="phone"]',
    "linkedin profile": 'input[name="urls[LinkedIn]"]',
    "linkedin": 'input[name="urls[LinkedIn]"]',
    "resume/cv": 'input[name="resume"]',
}


class FormPage:
    """What filling a form needs from a browser page. Deliberately has no click()."""

    def fill(self, selector: str, value: str) -> bool:
        raise NotImplementedError

    def upload(self, selector: str, path: str) -> bool:
        raise NotImplementedError

    def choose(self, selector: str, option: str) -> bool:
        raise NotImplementedError

    def fill_label(self, label: str, value: str) -> bool:
        raise NotImplementedError

    def upload_label(self, label: str, path: str) -> bool:
        raise NotImplementedError

    def pick(self, selector: str, option: str) -> bool:
        """A script-built dropdown (role=combobox, e.g. Greenhouse's React selects):
        type the option and press Tab to take it. Tab never submits a form."""
        return False


class PlaywrightPage(FormPage):
    def __init__(self, page):
        self.page = page

    def _one(self, locator):
        try:
            return locator.first if locator.count() else None
        except Exception:
            return None

    def fill(self, selector, value):
        found = self._one(self.page.locator(selector))
        return self._try(lambda: found.fill(value)) if found else False

    def upload(self, selector, path):
        found = self._one(self.page.locator(selector))
        return self._try(lambda: found.set_input_files(path)) if found else False

    def choose(self, selector, option):
        found = self._one(self.page.locator(selector))
        return self._try(lambda: found.select_option(label=option)) if found else False

    def fill_label(self, label, value):
        found = self._one(self.page.get_by_label(label, exact=False))
        return self._try(lambda: found.fill(value)) if found else False

    def upload_label(self, label, path):
        found = self._one(self.page.get_by_label(label, exact=False))
        return self._try(lambda: found.set_input_files(path)) if found else False

    def pick(self, selector, option):
        found = self._one(self.page.locator(selector))
        if not found:
            return False
        if self._shows(found, option):
            return True

        def typed():  # searchable dropdowns: type the option, Tab takes the match
            found.fill(option)
            self.page.wait_for_timeout(500)  # let the menu filter to the option
            found.press("Tab")  # react-select takes the highlighted option on Tab, never Enter
            self.page.wait_for_timeout(200)

        try:
            editable = found.is_editable()
        except Exception:
            editable = False
        if editable and self._try(typed) and self._shows(found, option):
            return True
        # Read-only dropdowns (Greenhouse's Yes/No questions) can't be typed into: open the
        # menu with ArrowDown, walk it until the option is highlighted, take it with Tab.
        return self._try(lambda: self._walk_menu(found, option)) and self._shows(found, option)

    def _walk_menu(self, found, option):
        want = option.strip().lower()
        found.focus()
        found.press("ArrowDown")
        self.page.wait_for_timeout(300)
        seen = set()
        for _ in range(80):
            current = self._highlighted(found)
            if current.lower() == want:
                found.press("Tab")
                self.page.wait_for_timeout(200)
                return
            if not current or current in seen:
                break  # menu didn't open, or it wrapped around without the option
            seen.add(current)
            found.press("ArrowDown")
            self.page.wait_for_timeout(50)
        found.press("Escape")
        raise LookupError(f"option {option!r} isn't in the dropdown")

    @staticmethod
    def _highlighted(found) -> str:
        """Text of the highlighted menu option ('' if the menu is closed)."""
        return found.evaluate(
            """box => {
              const id = box.getAttribute("aria-activedescendant");
              const el = (id && document.getElementById(id))
                || document.querySelector('[class*="option--is-focused"]');
              return el ? el.textContent.trim() : "";
            }"""
        )

    @staticmethod
    def _shows(found, option) -> bool:
        """The dropdown's box shows exactly this option (one value per line)."""
        try:
            control = found.locator('xpath=ancestor::div[contains(@class, "control")][1]')
            if not control.count():
                return False
            shown = [line.strip().lower() for line in control.inner_text().splitlines()]
            return option.strip().lower() in shown
        except Exception:
            return False

    @staticmethod
    def _try(action) -> bool:
        try:
            action()
            return True
        except Exception as exc:
            log.info("couldn't fill a field: %s", exc)
            return False


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)  # had an answer, but no field took it
    yours: list[str] = field(default_factory=list)  # no answer: fill these yourself

    def summary(self) -> str:
        lines = [f"Filled {len(self.filled)} fields. Nothing was submitted."]
        if self.missed:
            lines.append("Couldn't place these; copy them from packet.md:")
            lines += [f"  - {m}" for m in self.missed]
        if self.yours:
            lines.append("Left for you:")
            lines += [f"  - {y}" for y in self.yours]
        return "\n".join(lines)


def _css_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def _put(page: FormPage, f: Field, packet: Packet) -> bool:
    if packet.board == "greenhouse" and f.name:
        # Older Greenhouse forms name each field; the newer job-boards.greenhouse.io forms
        # give it only an id (file inputs hidden behind "Attach", React dropdowns).
        name = _css_name(f.name)
        sel, by_id = f'[name="{name}"]', f'[id="{name}"]'
        if f.kind == "file":
            return (
                page.upload(f'input[type="file"]{sel}', f.answer)
                or page.upload(f'input[type="file"]{by_id}', f.answer)
                or page.upload_label(f.label, f.answer)
            )
        if f.kind in ("select", "multiselect"):
            return page.choose(f"select{sel}", f.answer) or page.pick(
                f'input[role="combobox"]{by_id}', f.answer
            )
        return (
            page.fill(sel, f.answer)
            or page.fill(by_id, f.answer)
            or page.fill_label(f.label, f.answer)
        )
    if packet.board == "lever":
        sel = LEVER.get(f.label.strip().lower())
        if sel:
            return page.upload(sel, f.answer) if f.kind == "file" else page.fill(sel, f.answer)
    if f.kind == "file":
        return page.upload_label(f.label, f.answer)
    return page.fill_label(f.label, f.answer)


def fill_form(page: FormPage, packet: Packet) -> FillReport:
    report = FillReport()
    seen_lever_name = False
    files = {x.name for x in packet.fields if x.kind == "file" and x.answer}
    for f in packet.fields:
        if packet.board == "greenhouse" and f.name.endswith("_text") and f.name[:-5] in files:
            continue  # "Enter manually" box for a file already attached (resume, cover letter)
        if not f.answer:
            report.yours.append(f.label + (" (required)" if f.required else ""))
            continue
        if packet.board == "lever" and f.label.lower() in ("first name", "last name"):
            if seen_lever_name:
                continue  # Lever has one Full name box: fill it once, from both parts
            seen_lever_name = True
            first = next((x.answer for x in packet.fields if x.label == "First Name"), "")
            last = next((x.answer for x in packet.fields if x.label == "Last Name"), "")
            full = Field("Full name", answer=f"{first} {last}".strip())
            (report.filled if _put(page, full, packet) else report.missed).append("Full name")
            continue
        (report.filled if _put(page, f, packet) else report.missed).append(f.label)
    return report


def open_and_fill(packet: Packet, root: Path, settings: dict) -> FillReport:  # pragma: no cover
    """Real browser: open the form, fill it, and leave the window for you to review."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            'Form filling needs Playwright: pip install -e ".[apply]" && playwright install chromium'
        ) from exc
    profile = root / settings.get("browser_profile", "data/browser-profile")
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(packet.apply_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # let script-built forms (Greenhouse, Ashby) render
        report = fill_form(PlaywrightPage(page), packet)
        print(report.summary())
        input("Review the form in the browser and click Submit yourself. Press Enter when done: ")
        context.close()
    return report
