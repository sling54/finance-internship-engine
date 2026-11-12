"""Check real application forms headlessly: fill each one from its packet, screenshot, never submit.

    .venv/bin/python scripts/check_forms.py data/packets/<firm-role> [more packet folders...]

For each packet it opens the apply page in headless Chromium, runs the same fill_form as
`jobengine packet --fill`, saves data/form-checks/<packet>.png and prints what was filled,
missed or left for you. Nothing is ever clicked, so nothing is submitted. Personal answers
stay in the browser and the screenshot (data/ is git-ignored).
"""

from __future__ import annotations

import sys
from pathlib import Path

from jobengine.formfill import PlaywrightPage, fill_form
from jobengine.packet import load

EMPTY_DROPDOWNS = """() => [...document.querySelectorAll('input[role="combobox"]')].map(box => {
  const control = box.closest('[class*="control"]');
  const shown = control ? control.innerText.trim() : "";
  const label = box.id && document.querySelector(`label[for="${box.id}"]`);
  return {id: box.id, label: label ? label.innerText.trim() : "", shown,
          readonly: box.readOnly};
}).filter(d => !d.shown || /^select/i.test(d.shown))"""


def check(folders: list[Path], out: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    lines = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for folder in folders:
            packet = load(folder)
            page = browser.new_page()
            try:
                page.goto(packet.apply_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)  # let script-built forms render
                report = fill_form(PlaywrightPage(page), packet)
                page.screenshot(path=str(out / f"{folder.name}.png"), full_page=True)
                lines.append(
                    f"{packet.board:<10} {packet.company} - {packet.title}: "
                    f"{len(report.filled)} filled, {len(report.missed)} missed "
                    f"({'; '.join(report.missed) or '-'}), {len(report.yours)} left for you"
                )
                for d in page.evaluate(EMPTY_DROPDOWNS):  # still on "Select...": why?
                    lines.append(
                        f"           dropdown still empty: id={d['id']!r} label={d['label']!r}"
                        f"{' (read-only)' if d['readonly'] else ''}"
                    )
            except Exception as exc:
                lines.append(f"{packet.board:<10} {packet.company} - {packet.title}: ERROR {exc}")
            finally:
                page.close()
        browser.close()
    return lines


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for line in check([Path(a) for a in sys.argv[1:]], Path("data/form-checks")):
        print(line)
