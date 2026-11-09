from datetime import date
from shutil import copytree

import pytest
from openpyxl import load_workbook
from outreach_fakes import (
    DEFAULT_ROWS,
    EXAMPLE_CAPITAL,
    PROFILE,
    FakeGmail,
    FakeHTTP,
    FakeModel,
    make_tracker,
)

from jobengine import cli
from jobengine.config import CONFIG_DIR, Config, load_yaml
from jobengine.outreach import load_settings, load_templates
from jobengine.outreach.compose import TemplateError, check_hook, fill, pick_template
from jobengine.outreach.hunter import CreditLimit, Hunter, HunterError
from jobengine.outreach.plan import choose, contact_kind, pick_applications, plan
from jobengine.outreach.store import Entry, Log, role_key
from jobengine.tracker_sync import Row

TODAY = date(2026, 11, 9)
SETTINGS = load_settings(CONFIG_DIR)
TEMPLATES = load_templates(CONFIG_DIR)


@pytest.fixture
def root(tmp_path):
    make_tracker(tmp_path / PROFILE["tracker_path"], DEFAULT_ROWS)
    (tmp_path / PROFILE["resume_path"]).write_bytes(b"%PDF-1.4 fake resume")
    return tmp_path


def hunter(root, people=None, **kw):
    http = FakeHTTP(people if people is not None else {"Example Capital": EXAMPLE_CAPITAL})
    h = Hunter("fake-key", root, http=http, today=TODAY, **kw)
    return h, http


def run(root, h, gmail=None, client=None, settings=None, templates=None, dry_run=False):
    return plan(
        root,
        settings or SETTINGS,
        templates or TEMPLATES,
        PROFILE,
        root / PROFILE["tracker_path"],
        h,
        gmail=gmail,
        client=client,
        today=TODAY,
        dry_run=dry_run,
    )


# ---------- which applications ----------


def rows():
    return [
        Row(2, "Example Capital", "Trading Intern", date(2026, 11, 1), "Applied"),
        Row(3, "Sample Markets", "Quant Intern", date(2026, 10, 20), "Confirmed"),
        Row(4, "Rejecting Partners", "Trading Intern", date(2026, 11, 2), "Rejected"),
        Row(5, "Interviewing Group", "Research Intern", date(2026, 11, 3), "Interview"),
        Row(6, "Fresh Firm", "Trading Intern", date(2026, 11, 9), "Applied"),
        Row(7, "No Role Firm", "", date(2026, 11, 1), "Applied"),
    ]


def test_picks_outreach_statuses_oldest_first(tmp_path):
    picked = pick_applications(rows(), Log(tmp_path / "o.json"), SETTINGS, TODAY)
    assert [r.company for r in picked] == ["Sample Markets", "Example Capital", "Fresh Firm"]


def test_waits_min_days_and_caps_the_run(tmp_path):
    settings = dict(SETTINGS, min_days_after_apply=1, max_applications_per_run=1)
    picked = pick_applications(rows(), Log(tmp_path / "o.json"), settings, TODAY)
    assert [r.company for r in picked] == ["Sample Markets"]


def test_skips_applications_with_outreach_logged(tmp_path):
    log = Log(tmp_path / "o.json")
    log.add(entry("Sample Markets", "Quant Intern - Summer 2027"))
    log.roles[role_key("Example Capital", "Trading Intern")] = {"date": "2026-11-02", "note": "x"}
    picked = pick_applications(rows(), log, SETTINGS, TODAY)
    assert [r.company for r in picked] == ["Fresh Firm"]


def entry(company, role, email="a@b.com", status="drafted", day="2026-11-01", sent_at=""):
    return Entry(
        day,
        company,
        role,
        "A B",
        "A",
        "Trader",
        email,
        "team",
        "team_member",
        status,
        sent_at=sent_at,
    )


# ---------- Hunter ----------


def test_hunter_looks_up_by_company_and_caches(root):
    h, http = hunter(root)
    assert h.people("Example Capital") == EXAMPLE_CAPITAL
    assert http.calls == [
        {"api_key": "fake-key", "type": "personal", "limit": 10, "company": "Example Capital"}
    ]
    again, http2 = hunter(root)  # a new run reads the cache instead of spending a credit
    assert again.people("Example Capital") == EXAMPLE_CAPITAL
    assert http2.calls == [] and again.used == 1


def test_hunter_cache_expires(root):
    h, _ = hunter(root)
    h.people("Example Capital")
    later = Hunter("fake-key", root, http=FakeHTTP({}), today=date(2026, 12, 20))
    assert later.people("Example Capital") == []
    assert later.used == 1  # a new month starts its own count


def test_hunter_uses_the_domain_override(root):
    h, http = hunter(
        root,
        {"examplecapital.com": EXAMPLE_CAPITAL},
        domains={"Example Capital LLC": "examplecapital.com"},
    )
    assert h.people("Example Capital") == EXAMPLE_CAPITAL
    assert http.calls[0]["domain"] == "examplecapital.com" and "company" not in http.calls[0]


def test_hunter_stops_at_the_monthly_limit(root):
    h, http = hunter(root, monthly_limit=1)
    h.people("Example Capital")
    with pytest.raises(CreditLimit, match="1 of 1"):
        h.people("Sample Markets")
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "status,message", [(401, "API key"), (429, "rate limit"), (503, "trouble")]
)
def test_hunter_errors_are_not_cached(root, status, message):
    h = Hunter("fake-key", root, http=FakeHTTP({}, status=status), today=TODAY)
    with pytest.raises(HunterError, match=message):
        h.people("Example Capital")
    assert "example capital" not in h.cache


def test_hunter_unknown_company_is_cached_as_empty(root):
    h = Hunter("fake-key", root, http=FakeHTTP({}, status=400), today=TODAY)
    assert h.people("Unknown Firm") == []
    assert h.cache["unknown firm"]["emails"] == []


# ---------- choosing contacts ----------


def test_contact_kinds():
    assert (
        contact_kind({"position": "Campus Recruiter"}, "Trading Intern", "", SETTINGS)
        == "recruiter"
    )
    assert (
        contact_kind({"position": "Recruiting Lead", "department": ""}, "x", "", SETTINGS)
        == "recruiter"
    )
    assert (
        contact_kind({"position": "Head of HR", "department": "hr"}, "x", "", SETTINGS)
        == "recruiter"
    )
    assert (
        contact_kind({"position": "Equity Trader"}, "Trading Intern", "Trading", SETTINGS) == "team"
    )
    assert (
        contact_kind({"position": "Commodities Desk Head"}, "Analyst Intern", "Trading", SETTINGS)
        == "team"
    )
    assert (
        contact_kind({"position": "Office Manager"}, "Trading Intern", "Trading", SETTINGS)
        == "other"
    )


def test_choose_prefers_recruiting_then_team_and_drops_unusable(tmp_path):
    app = rows()[0]
    chosen = choose(EXAMPLE_CAPITAL, app, "Trading", Log(tmp_path / "o.json"), SETTINGS, TODAY)
    assert [(k, p["first_name"]) for k, p in chosen] == [("recruiter", "Riley"), ("team", "Sam")]


def test_choose_respects_company_cap_and_recontact_window(tmp_path):
    app = rows()[0]
    log = Log(tmp_path / "o.json")
    log.add(entry("Example Capital", "Quant Intern", "riley.stone@examplecapital.com"))
    log.add(entry("Example Capital", "Quant Intern", "x@examplecapital.com"))
    chosen = choose(EXAMPLE_CAPITAL, app, "Trading", log, SETTINGS, TODAY)
    assert [p["first_name"] for _, p in chosen] == ["Sam"]  # cap of 3 leaves room for one

    recent = Log(tmp_path / "p.json")
    recent.add(
        entry(
            "Other Firm",
            "Trading Intern",
            "sam.lee@examplecapital.com",
            status="sent",
            sent_at="2026-10-01T10:00:00",
        )
    )
    chosen = choose(EXAMPLE_CAPITAL, app, "Trading", recent, SETTINGS, TODAY)
    assert [p["first_name"] for _, p in chosen] == ["Riley", "Pat"]


def test_skipped_contacts_dont_count(tmp_path):
    log = Log(tmp_path / "o.json")
    for i in range(3):
        log.add(entry("Example Capital", "Quant Intern", f"x{i}@examplecapital.com", "skipped"))
    assert len(choose(EXAMPLE_CAPITAL, rows()[0], "Trading", log, SETTINGS, TODAY)) == 2


# ---------- templates and hooks ----------


def test_templates_fill_and_reject_leftovers():
    values = {
        "first_name": "Riley",
        "company": "Example Capital",
        "role": "Trading Intern",
        "my_name": "Alex Example",
        "school": "Example State University",
        "hook": "Hi.",
    }
    assert fill("{first_name} at {company}: {hook}", values) == "Riley at Example Capital: Hi."
    with pytest.raises(TemplateError, match="{last_name}"):
        fill("Dear {last_name}", values)


def test_pick_template_by_kind():
    assert pick_template(TEMPLATES, "recruiter")[0] == "recruiter"
    assert pick_template(TEMPLATES, "team")[0] == "team_member"
    assert pick_template(TEMPLATES, "other")[0] == "alumni_or_general"
    assert pick_template({"r": {"for": "recruiter"}}, "team") is None


def test_local_templates_replace_the_examples(tmp_path):
    config = tmp_path / "config"
    copytree(CONFIG_DIR, config, ignore=lambda *_: ["profile.yml", "outreach_templates.local.yml"])
    assert set(load_templates(config)) == {"recruiter", "team_member", "alumni_or_general"}
    (config / "outreach_templates.local.yml").write_text(
        "templates:\n  mine:\n    for: any\n    subject: s\n    body: b\n"
    )
    assert set(load_templates(config)) == {"mine"}


INPUTS = "Example Capital Trading Intern options market making 3.5 GPA finance"


@pytest.mark.parametrize(
    "reply,expected",
    [
        (
            "Your options market-making desk fits my finance background",
            "Your options market-making desk fits my finance background.",
        ),
        (
            '<think>hmm</think> "My 3.5 GPA in finance fits the role."',
            "My 3.5 GPA in finance fits the role.",
        ),
        ("See https://example.com for my work.", None),
        ("Email me at alex@example.com.", None),
        ("I {love} it.", None),
        ("I traded 40 options last year.", None),  # 40 isn't in the inputs
        ("I'd love to join Sample Markets.", None),  # names a different company
        (" ".join(["word"] * 31) + ".", None),
        ("", None),
    ],
)
def test_check_hook(reply, expected):
    assert check_hook(reply, "Example Capital", INPUTS, ["Sample Markets"]) == expected


# ---------- the whole plan ----------


def test_plan_writes_labelled_drafts_log_and_sheet(root):
    h, _ = hunter(root)
    gmail = FakeGmail()
    model = FakeModel("Your options desk fits my finance background.")
    report = run(root, h, gmail, model)

    assert len(report.drafted) == 2
    assert [label["name"] for label in gmail.labels_] == ["jobengine/outreach"]
    drafts = list(gmail.drafts_)
    first = gmail.parsed(drafts[0])
    assert first["To"] == "riley.stone@examplecapital.com"
    assert first["Subject"] == "Trading Intern application - Alex Example"
    text = next(p for p in first.walk() if p.get_content_type() == "text/plain").get_content()
    assert "Hi Riley," in text and "Your options desk fits my finance background." in text
    assert [p.get_filename() for p in first.walk() if p.get_filename()] == ["Resume.pdf"]
    second = gmail.parsed(drafts[1])
    assert second["To"] == "sam.lee@examplecapital.com" and not second.is_multipart()
    assert len(gmail.modified) == 2  # both drafts labelled

    log = Log.load(root)
    assert sorted(e.status for e in log.entries.values()) == ["drafted", "drafted"]
    assert all(e.draft_id for e in log.entries.values())
    sheet = load_workbook(root / PROFILE["tracker_path"])["Outreach"]
    assert [c.value for c in sheet[1]] == [
        "Date",
        "Company",
        "Role",
        "Contact",
        "Position",
        "Email",
        "Status",
    ]
    assert [sheet.cell(r, 6).value for r in (2, 3)] == [
        "riley.stone@examplecapital.com",
        "sam.lee@examplecapital.com",
    ]
    # Sample Markets had nobody on Hunter: noted, and not looked up again next run.
    assert report.short == ["Sample Markets - Quant Analyst Intern: short: 0 of 2 contacts found"]
    assert role_key("Sample Markets", "Quant Analyst Intern") in log.roles
    assert run(root, hunter(root)[0], FakeGmail()).applications == 0


def test_plan_uses_fallback_hook_when_the_model_is_down(root):
    gmail = FakeGmail()
    run(root, hunter(root)[0], gmail, FakeModel(fail=True))
    text = gmail.parsed(next(iter(gmail.drafts_))).get_body(("plain",)).get_content()
    assert TEMPLATES["recruiter"]["fallback_hook"] in text


def test_plan_reuses_an_existing_label(root):
    gmail = FakeGmail(labels=[{"id": "Label_9", "name": "jobengine/outreach"}])
    run(root, hunter(root)[0], gmail)
    assert len(gmail.labels_) == 1
    assert all(body["addLabelIds"] == ["Label_9"] for _, body in gmail.modified)


def test_plan_skips_a_draft_whose_template_breaks(root):
    templates = dict(TEMPLATES)
    templates["recruiter"] = dict(templates["recruiter"], body="Dear {last_name}")
    gmail = FakeGmail()
    report = run(root, hunter(root)[0], gmail, templates=templates)
    assert len(report.drafted) == 1 and "template recruiter" in report.skipped[0]


def test_plan_skips_attachment_when_resume_is_missing(root):
    (root / PROFILE["resume_path"]).unlink()
    report = run(root, hunter(root)[0], FakeGmail())
    assert len(report.drafted) == 1 and "resume to attach not found" in report.skipped[0]


def test_plan_stops_at_the_credit_limit_and_names_whats_left(root):
    h, _ = hunter(root, monthly_limit=1)
    report = run(root, h, FakeGmail())
    assert "Hunter limit reached" in report.stopped
    assert report.not_reached == ["Example Capital - Trading Intern"]  # Sample Markets went first


def test_plan_keeps_the_log_when_excel_has_the_tracker_open(root):
    tracker = root / PROFILE["tracker_path"]
    (tracker.parent / f"~${tracker.name}").write_text("")
    report = run(root, hunter(root)[0], FakeGmail())
    assert report.sheet_skipped and len(Log.load(root).entries) == 2
    assert "Outreach" not in load_workbook(tracker).sheetnames

    (tracker.parent / f"~${tracker.name}").unlink()  # closed: the next run fills the sheet in
    from jobengine.outreach.sheet import sync_sheet
    from jobengine.tracker_sync import Tracker

    t = Tracker(tracker)
    assert sync_sheet(t, list(Log.load(root).entries.values())) == 2
    assert sync_sheet(t, list(Log.load(root).entries.values())) == 0


def test_dry_run_writes_nothing(root):
    tracker = root / PROFILE["tracker_path"]
    before = tracker.read_bytes()
    report = run(root, hunter(root)[0], gmail=None, dry_run=True)
    assert len(report.planned) == 2
    assert report.planned[0].startswith(
        "riley.stone@examplecapital.com | Trading Intern application"
    )
    assert not (root / "data" / "outreach.json").exists()
    assert tracker.read_bytes() == before
    assert "Dry run: 2 drafts planned" in report.summary()


# ---------- the command ----------


@pytest.fixture
def project(root, monkeypatch):
    config = root / "config"
    copytree(
        CONFIG_DIR,
        config,
        ignore=lambda *_: ["profile.yml", "firms.local.yml", "outreach_templates.local.yml"],
    )
    (config / "profile.yml").write_text("\n".join(f"{k}: {v}" for k, v in PROFILE.items()) + "\n")
    monkeypatch.setattr(cli, "ROOT", root)
    load = Config.load
    monkeypatch.setattr(cli.Config, "load", lambda: load(config))
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.setattr("jobengine.outreach.cli.local_model", lambda scoring: None)
    return root


def test_command_needs_a_hunter_key(project, capsys):
    (project / "config/outreach_templates.local.yml").write_text(
        (project / "config/outreach_templates.example.yml").read_text()
    )
    assert cli.main(["outreach", "plan"]) == 1
    assert "HUNTER_API_KEY" in capsys.readouterr().out


def test_command_needs_your_own_templates_unless_dry_run(project, capsys, monkeypatch):
    (project / ".env").write_text("HUNTER_API_KEY=fake-key\n")
    assert cli.main(["outreach", "plan"]) == 1
    assert "outreach_templates.local.yml" in capsys.readouterr().out

    monkeypatch.setattr(
        "jobengine.outreach.hunter.session", lambda: FakeHTTP({"Example Capital": EXAMPLE_CAPITAL})
    )
    assert cli.main(["outreach", "plan", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Dry run: 2 drafts planned" in out and "Hunter lookups this month: 2 of 25" in out
    assert load_yaml(project / "config/profile.yml")["name"] == "Alex Example"


def test_ors_key_still_reads_from_env_file(tmp_path, monkeypatch):
    from jobengine.zone import api_key

    monkeypatch.delenv("ORS_API_KEY", raising=False)
    (tmp_path / ".env").write_text("ORS_API_KEY=ors\nHUNTER_API_KEY=hunter\n")
    assert api_key(tmp_path) == "ors"
    assert api_key(tmp_path, "HUNTER_API_KEY") == "hunter"
