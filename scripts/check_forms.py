"""Check real application forms headlessly: fill each one from its packet, screenshot, never submit.

    .venv/bin/python scripts/check_forms.py data/packets/<firm-role> [more packet folders...]
    .venv/bin/python scripts/check_forms.py --fields data/packets/<firm-role> [...]

For each packet it opens the apply page in headless Chromium, runs the same fill_form as
`jobengine packet --fill`, saves data/form-checks/<packet>.png and prints what was filled,
missed or left for you. Nothing is ever clicked, so nothing is submitted. Personal answers
stay in the browser and the screenshot (data/ is git-ignored).

--fields opens no browser: it prints the questions the packet builder reads from the board's
API for each packet's link (label, kind, required, options), with no answers, so you can see
whether a packet matches the real form.
"""

from __future__ import annotations

import sys
from pathlib import Path

from jobengine.formfill import PlaywrightPage, fill_form
from jobengine.packet import build, load


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
            except Exception as exc:
                lines.append(f"{packet.board:<10} {packet.company} - {packet.title}: ERROR {exc}")
            finally:
                page.close()
        browser.close()
    return lines


def form_fields(folders: list[Path], get, post) -> list[str]:
    lines = []
    for folder in folders:
        packet = load(folder)
        row = {"company": packet.company, "title": packet.title, "url": packet.url}
        built = build(row, {}, {}, folder, None, get, "", post)
        lines.append(
            f"{packet.board:<10} {packet.company} - {packet.title}: {len(built.fields)} fields"
        )
        for f in built.fields:
            star = " *" if f.required else ""
            opts = f" [{'; '.join(f.options)}]" if f.options else ""
            lines.append(f"    {f.kind:<11} {f.name or '-'} | {f.label[:90]}{star}{opts[:120]}")
    return lines


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "--fields":
        from jobengine.http import TIMEOUT, session

        http = session()
        lines = form_fields(
            [Path(a) for a in args[1:]],
            lambda url: http.get(url, timeout=TIMEOUT),
            lambda url, body: http.post(url, json=body, timeout=TIMEOUT),
        )
    else:
        lines = check([Path(a) for a in args], Path("data/form-checks"))
    for line in lines:
        print(line)
