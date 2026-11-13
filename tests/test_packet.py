import csv
import json
from argparse import Namespace
from pathlib import Path

import pytest
from outreach_fakes import FakeModel, FakeResponse, make_tracker

from jobengine.config import CONFIG_DIR, Config, load_with_local, load_yaml
from jobengine.formfill import FillReport, FormPage, fill_form
from jobengine.packet import (
    Field,
    ashby_fields,
    ashby_job,
    board_of,
    build,
    greenhouse_fields,
    greenhouse_job,
    is_open_question,
    load,
    rule_answer,
    write,
)
from jobengine.packet_cli import run

SETTINGS = load_with_local(CONFIG_DIR, "apply")
PROFILE = load_yaml(CONFIG_DIR / "profile.example.yml")

GREENHOUSE = {
    "content": "&lt;p&gt;Join our options market-making desk.&lt;/p&gt;",
    "questions": [
        {
            "label": "First Name",
            "required": True,
            "fields": [{"name": "first_name", "type": "input_text"}],
        },
        {
            "label": "Last Name",
            "required": True,
            "fields": [{"name": "last_name", "type": "input_text"}],
        },
        {"label": "Email", "required": True, "fields": [{"name": "email", "type": "input_text"}]},
        {
            "label": "Resume/CV",
            "required": True,
            "fields": [
                {"name": "resume", "type": "input_file"},
                {"name": "resume_text", "type": "textarea"},
            ],
        },
        {
            "label": "Cover Letter",
            "required": False,
            "fields": [{"name": "cover_letter", "type": "input_file"}],
        },
        {
            "label": "Will you now or in the future require sponsorship?",
            "required": True,
            "fields": [
                {
                    "name": "question_1",
                    "type": "multi_value_single_select",
                    "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
                }
            ],
        },
        {
            "label": "Why are you interested in Example Capital?",
            "required": True,
            "fields": [{"name": "question_2", "type": "textarea"}],
        },
        {
            "label": "Gender",
            "required": False,
            "fields": [
                {
                    "name": "gender",
                    "type": "multi_value_single_select",
                    "values": [
                        {"label": "Male"},
                        {"label": "Female"},
                        {"label": "Decline To Self Identify"},
                    ],
                }
            ],
        },
        {
            "label": "What is your favorite trading book?",
            "required": False,
            "fields": [{"name": "question_3", "type": "input_text"}],
        },
        {"label": "Hidden", "fields": [{"name": "token", "type": "input_hidden"}]},
    ],
}
GH_URL = "https://job-boards.greenhouse.io/examplecapital/jobs/4012345"


def fake_get(calls=None, status=200, data=GREENHOUSE):
    def get(url):
        if calls is not None:
            calls.append(url)
        return FakeResponse(status, data)

    return get


@pytest.fixture
def root(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / PROFILE["resume_path"]).write_bytes(b"%PDF fake")
    return tmp_path


# ---------- boards and questions ----------


@pytest.mark.parametrize(
    "url,board",
    [
        (GH_URL, "greenhouse"),
        ("https://boards.greenhouse.io/examplecapital/jobs/1", "greenhouse"),
        ("https://example.com/careers?gh_jid=123", "greenhouse"),
        ("https://jobs.lever.co/example/abc-123", "lever"),
        ("https://jobs.ashbyhq.com/example/abc", "ashby"),
        ("https://example.wd1.myworkdayjobs.com/External/job/x", "workday"),
        ("https://example.com/job/1", "other"),
    ],
)
def test_board_of(url, board):
    assert board_of(url) == board


def test_greenhouse_job_ids():
    assert greenhouse_job(GH_URL) == ("examplecapital", "4012345")
    embed = "https://boards.greenhouse.io/embed/job_app?for=examplecapital&token=55"
    assert greenhouse_job(embed) == ("examplecapital", "55")
    assert greenhouse_job("https://example.com/careers?gh_jid=123") is None  # board unknown


def test_greenhouse_fields_skip_hidden_and_keep_options():
    fields = greenhouse_fields(GREENHOUSE)
    assert [f.name for f in fields][:4] == ["first_name", "last_name", "email", "resume"]
    assert "token" not in [f.name for f in fields]
    sponsor = next(f for f in fields if f.name == "question_1")
    assert (sponsor.kind, sponsor.options, sponsor.required) == ("select", ["Yes", "No"], True)


# ---------- answers ----------


def ans(label, kind="text", options=(), profile=PROFILE, resume=Path("/nope")):
    return rule_answer(Field(label, kind=kind, options=list(options)), profile, SETTINGS, resume)


def test_rule_answers_from_the_profile(root):
    assert ans("First Name") == ("Alex", "profile")
    assert ans("Last Name") == ("Example", "profile")
    assert ans("Email") == ("alex@example.com", "profile")
    assert ans("Phone Number") == ("(555) 010-0100", "profile")
    assert ans("LinkedIn Profile") == ("https://www.linkedin.com/in/alex-example", "profile")
    assert ans("School") == ("Example State University", "profile")
    assert ans("Degree") == ("Bachelor's", "profile")
    assert ans("Discipline") == ("Finance", "profile")
    assert ans("GPA") == ("3.5", "profile")
    assert ans("Expected Graduation Date") == ("December 2027", "profile")
    assert ans("How did you hear about this job?") == ("Company website", "profile")
    # Labels seen on real Greenhouse forms (SESCO, Graviton) on Nov 11, 2026
    assert ans("CGPA") == ("3.5", "profile")
    assert ans("How were you referred to us?") == ("Company website", "profile")
    assert ans("Preferred contact method?") == ("Email", "profile")
    assert ans("Preferred contact method?", "select", ["Phone", "Email"]) == ("Email", "rule")
    assert ans("Willing to relocate?", "select", ["Yes", "No"]) == ("Yes", "rule")
    resume = root / PROFILE["resume_path"]
    assert ans("Resume/CV", "file", resume=resume) == (str(resume), "profile")
    assert ans("Cover Letter", "file", resume=resume) == ("", "you")


@pytest.mark.parametrize(
    "auth,authorized,sponsor",
    [
        ("us_citizen", "Yes", "No"),
        ("permanent_resident", "Yes", "No"),
        ("needs_sponsorship", "", "Yes"),
    ],
)
def test_work_authorization_answers(auth, authorized, sponsor):
    profile = dict(PROFILE, work_authorization=auth)
    assert ans("Are you legally authorized to work in the US?", profile=profile)[0] == authorized
    assert ans("Will you require visa sponsorship?", profile=profile)[0] == sponsor


def test_dropdown_answers_must_be_an_option():
    assert ans("Require sponsorship?", "select", ["Yes", "No"]) == ("No", "rule")
    assert ans("Degree", "select", ["Bachelor's Degree", "Master's Degree"]) == (
        "Bachelor's Degree",
        "rule",
    )
    assert ans("Degree", "select", ["High school"]) == ("", "you")


def test_eeo_questions_decline_when_possible():
    assert ans("Gender", "select", ["Male", "Female", "Decline To Self Identify"]) == (
        "Decline To Self Identify",
        "rule",
    )
    assert ans("Veteran status", "select", ["I am a veteran", "I am not a veteran"]) == ("", "you")


def test_unknown_questions_are_left_for_you():
    assert ans("What is your favorite trading book?") == ("", "you")
    assert ans("Are you a graduate student?") == ("", "you")  # not the graduation date


@pytest.mark.parametrize(
    "label,kind,open_",
    [
        ("Why are you interested in Example Capital?", "text", True),
        ("Tell us about a trade you made", "text", True),
        ("Anything else?", "textarea", True),
        ("What is your favorite trading book?", "text", False),
        ("Why us?", "select", False),
    ],
)
def test_open_questions(label, kind, open_):
    assert is_open_question(Field(label, kind=kind)) is open_


# ---------- building the packet ----------


def test_greenhouse_packet_with_ai_draft(root):
    calls = []
    model = FakeModel(
        "Your options market-making desk fits my finance studies and trading club work."
    )
    row = {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL}
    packet = build(row, PROFILE, SETTINGS, root, model, fake_get(calls), about="Finance major")
    assert calls == [
        "https://boards-api.greenhouse.io/v1/boards/examplecapital/jobs/4012345?questions=true"
    ]
    by = {f.label: f for f in packet.fields}
    assert by["First Name"].answer == "Alex"
    assert by["Will you now or in the future require sponsorship?"].answer == "No"
    assert by["Gender"].answer == "Decline To Self Identify"
    why = by["Why are you interested in Example Capital?"]
    assert why.source == "ai" and why.answer.startswith("Your options market-making desk")
    assert "options market-making desk" in model.prompts[0]  # the description reached the model
    assert by["What is your favorite trading book?"].source == "you"
    assert by["Cover Letter"].source == "you"


def test_bad_ai_draft_is_dropped(root):
    model = FakeModel("Visit https://example.com, where I made 300% returns.")
    row = {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL}
    packet = build(row, PROFILE, SETTINGS, root, model, fake_get())
    why = next(f for f in packet.fields if f.label.startswith("Why"))
    assert (why.answer, why.source) == ("", "you")


class ThinkingModel:
    """Runs out of tokens while thinking unless given more than its first budget."""

    def __init__(self, reply):
        self.reply, self.max_tokens, self.budgets = reply, 4096, []

    def chat(self, system, user):
        self.budgets.append(self.max_tokens)
        if self.max_tokens <= 4096:
            raise ValueError("the model was still thinking when it hit max_tokens (4096)")
        return self.reply


def test_ai_draft_retries_with_more_tokens_when_the_model_overthinks(root):
    model = ThinkingModel("Your options desk fits my finance studies and trading club work.")
    row = {"company": "Sample Markets", "title": "Quant Intern", "url": "https://jobs.lever.co/s/a"}
    packet = build(row, PROFILE, SETTINGS, root, model, fake_get())
    why = next(f for f in packet.fields if f.label.startswith("Why"))
    assert why.source == "ai" and why.answer.startswith("Your options desk")
    assert model.budgets[:2] == [4096, 8192] and model.max_tokens == 4096  # budget restored


def test_other_boards_get_standard_fields_and_lever_apply_link(root):
    row = {
        "company": "Sample Markets",
        "title": "Quant Intern",
        "url": "https://jobs.lever.co/sample/abc",
    }
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get())
    assert packet.apply_url == "https://jobs.lever.co/sample/abc/apply"
    labels = [f.label for f in packet.fields]
    assert "First Name" in labels and labels[-2:] == SETTINGS["default_questions"]
    assert all(f.source == "you" for f in packet.fields if f.label in SETTINGS["default_questions"])


def test_greenhouse_api_failure_falls_back_to_standard_fields(root):
    row = {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL}
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get(status=404))
    assert [f.label for f in packet.fields][:2] == ["First Name", "Last Name"]


def test_write_and_load_round_trip(root):
    row = {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL}
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get())
    folder = write(packet, root)
    assert folder == root / "data/packets/example-capital-trading-intern"
    text = (folder / "packet.md").read_text()
    assert "# Example Capital - Trading Intern" in text and "LEFT FOR YOU" in text
    assert "Nothing has been submitted." in text
    assert load(folder) == packet


# ---------- filling the form ----------


class FakePage(FormPage):
    """Records what the filler does. It can't click: FormPage has no click."""

    def __init__(self, names=(), labels=()):
        self.names, self.labels, self.calls = set(names), set(labels), []

    def _has(self, selector):
        return any(f'"{n}"' in selector for n in self.names)

    def fill(self, selector, value):
        self.calls.append(("fill", selector, value))
        return self._has(selector)

    def upload(self, selector, path):
        self.calls.append(("upload", selector, path))
        return self._has(selector)

    def choose(self, selector, option):
        self.calls.append(("choose", selector, option))
        return self._has(selector)

    def fill_label(self, label, value):
        self.calls.append(("fill_label", label, value))
        return label in self.labels

    def upload_label(self, label, path):
        self.calls.append(("upload_label", label, path))
        return label in self.labels


def test_fill_greenhouse_by_field_name(root):
    row = {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL}
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get())
    page = FakePage(names=["first_name", "last_name", "email", "resume", "question_1", "gender"])
    report = fill_form(page, packet)
    assert report.filled == [
        "First Name",
        "Last Name",
        "Email",
        "Resume/CV",
        "Will you now or in the future require sponsorship?",
        "Gender",
    ]
    assert ("choose", 'select[name="question_1"]', "No") in page.calls
    assert any(c[0] == "upload" and 'input[type="file"][name="resume"]' == c[1] for c in page.calls)
    assert "Why are you interested in Example Capital? (required)" in report.yours
    assert not any("click" in c[0] for c in page.calls)
    assert not hasattr(FormPage, "click")  # Submit stays yours


def test_fill_lever_uses_one_full_name_box(root):
    row = {
        "company": "Sample Markets",
        "title": "Quant Intern",
        "url": "https://jobs.lever.co/sample/abc",
    }
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get())
    page = FakePage(names=["name", "email", "phone", "urls[LinkedIn]", "resume"])
    report = fill_form(page, packet)
    assert ("fill", 'input[name="name"]', "Alex Example") in page.calls
    assert report.filled.count("Full name") == 1 and "Email" in report.filled
    assert "Resume/CV" in report.filled


def test_fill_other_boards_by_label_and_list_misses(root):
    row = {"company": "Other Firm", "title": "Analyst Intern", "url": "https://example.com/job/1"}
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get())
    report = fill_form(FakePage(labels=["Email", "First Name"]), packet)
    assert report.filled == ["First Name", "Email"]
    assert "Last Name" in report.missed
    assert "Filled 2 fields. Nothing was submitted." in report.summary()


# ---------- the command ----------


def args(**kw):
    base = {"search": [], "url": None, "company": None, "role": None, "fill": False}
    return Namespace(**{**base, **kw})


@pytest.fixture
def project(root, monkeypatch):
    rows = [
        {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL, "description": ""},
        {
            "company": "Sample Markets",
            "title": "Quant Intern",
            "url": "https://jobs.lever.co/s/1",
            "description": "",
        },
    ]
    (root / "data").mkdir(exist_ok=True)
    with open(root / "data/jobs_latest.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    make_tracker(root / PROFILE["tracker_path"], [])
    monkeypatch.setattr("jobengine.outreach.cli.local_model", lambda *a: None)
    cfg = Config.load(CONFIG_DIR)
    cfg.profile = PROFILE
    return root, cfg


def test_command_writes_the_packet(project, capsys):
    root, cfg = project
    assert run(args(search=["example", "trading"]), cfg, root, get=fake_get()) == 0
    out = capsys.readouterr().out
    assert "Example Capital - Trading Intern (greenhouse)" in out and "left for you" in out
    assert (root / "data/packets/example-capital-trading-intern/answers.json").exists()


def test_command_fill_records_only_when_you_say_you_submitted(project, capsys):
    root, cfg = project
    filled = []
    run(
        args(search=["sample"], fill=True),
        cfg,
        root,
        get=fake_get(),
        ask=lambda _: "n",
        fill=lambda p, r, s: filled.append(p.apply_url),
    )
    assert filled == ["https://jobs.lever.co/s/1/apply"]
    assert "Not recorded" in capsys.readouterr().out

    run(
        args(search=["sample"], fill=True),
        cfg,
        root,
        get=fake_get(),
        ask=lambda _: "y",
        fill=lambda p, r, s: None,
    )
    assert "Added: Sample Markets - Quant Intern" in capsys.readouterr().out


def test_command_with_url_not_in_scan(project):
    root, cfg = project
    url = "https://example.com/job/9"
    assert run(args(url=url, company="New Firm", role="Ops Intern"), cfg, root, get=fake_get()) == 0
    data = json.loads((root / "data/packets/new-firm-ops-intern/answers.json").read_text())
    assert data["url"] == url and data["board"] == "other"


def test_command_needs_a_role(project, capsys):
    root, cfg = project
    assert run(args(), cfg, root) == 2
    assert run(args(search=["nothing", "matches"]), cfg, root) == 1
    assert "No role in data/jobs_latest.csv" in capsys.readouterr().out


def test_fill_report_summary_lists_both_kinds():
    text = FillReport(["Email"], ["Phone"], ["Cover Letter"]).summary()
    assert "Couldn't place these" in text and "Left for you" in text


# ---------- Ashby: the real form from Ashby's API ----------

ASHBY_URL = (
    "https://jobs.ashbyhq.com/samplecap/5e2ea37b-2369-474e-b717-c24c60976e96/application?embed=true"
)


def _ashby_entry(path, title, type_, required=True, values=None, hidden=False):
    field = {"path": path, "title": title, "type": type_}
    if values:
        field["selectableValues"] = [{"label": v, "value": v} for v in values]
    return {"field": field, "isRequired": required, "isHidden": hidden}


ASHBY = {
    "data": {
        "jobPosting": {
            "title": "Quant Research Intern",
            "applicationForm": {
                "sections": [
                    {
                        "fieldEntries": [
                            _ashby_entry("_systemfield_name", "Name", "String"),
                            _ashby_entry("_systemfield_email", "Email", "Email"),
                            _ashby_entry("_systemfield_resume", "Resume", "File"),
                            _ashby_entry(
                                "q1",
                                "Are you legally authorized to work in the United States?",
                                "Boolean",
                            ),
                            _ashby_entry(
                                "q2",
                                "Will you now or in the future require sponsorship for "
                                "employment visa status in the United States?",
                                "Boolean",
                            ),
                            _ashby_entry(
                                "q3",
                                "Please select all academic terms you are available",
                                "MultiValueSelect",
                                values=["Fall (Sept - Dec)", "Summer (June - Aug)"],
                            ),
                            _ashby_entry(
                                "q4",
                                "Are you currently enrolled in an accredited undergraduate or "
                                "graduate program? What's the name of the school / program? "
                                "What is your expected graduation month and year?",
                                "LongText",
                            ),
                            _ashby_entry("q5", "Earliest start date?", "Date"),
                            _ashby_entry("q6", "Secret", "String", hidden=True),
                        ]
                    },
                    {"isHidden": True, "fieldEntries": [_ashby_entry("q7", "Hidden", "String")]},
                ]
            },
        }
    }
}


def fake_post(calls=None, status=200, data=ASHBY):
    def post(url, body):
        if calls is not None:
            calls.append((url, body))
        return FakeResponse(status, data)

    return post


def test_ashby_job_ids():
    assert ashby_job(ASHBY_URL) == ("samplecap", "5e2ea37b-2369-474e-b717-c24c60976e96")
    assert ashby_job("https://jobs.ashbyhq.com/samplecap") is None
    assert ashby_job("https://example.com/samplecap/5e2ea37b-2369-474e-b717-c24c60976e96") is None


def test_ashby_fields_follow_the_real_form():
    fields = ashby_fields(ASHBY)
    assert [f.name for f in fields] == [
        "_systemfield_name",
        "_systemfield_email",
        "_systemfield_resume",
        "q1",
        "q2",
        "q3",
        "q4",
        "q5",
    ]
    kinds = {f.name: f.kind for f in fields}
    assert kinds["_systemfield_resume"] == "file" and kinds["q1"] == "boolean"
    assert kinds["q3"] == "multiselect" and kinds["q4"] == "textarea" and kinds["q5"] == "date"
    assert fields[3].options == ["Yes", "No"]
    assert ashby_fields({"errors": [{"message": "bad"}]}) == []


def test_ashby_packet_uses_the_real_form(root):
    calls = []
    model = FakeModel("I am a finance major and want to bring that to systematic research.")
    row = {"company": "Sample Capital", "title": "Quant Research Intern", "url": ASHBY_URL}
    packet = build(
        row,
        PROFILE,
        SETTINGS,
        root,
        model,
        fake_get(),
        about="Finance major",
        post=fake_post(calls),
    )
    assert calls[0][0].startswith("https://jobs.ashbyhq.com/api/non-user-graphql")
    assert calls[0][1]["variables"] == {
        "organizationHostedJobsPageName": "samplecap",
        "jobPostingId": "5e2ea37b-2369-474e-b717-c24c60976e96",
    }
    assert packet.apply_url == (
        "https://jobs.ashbyhq.com/samplecap/5e2ea37b-2369-474e-b717-c24c60976e96/application"
    )
    by = {f.name: f for f in packet.fields}
    assert by["_systemfield_name"].answer == PROFILE["name"]
    assert by["_systemfield_resume"].answer.endswith(PROFILE["resume_path"])
    assert by["q2"].answer == "No"  # the example profile is a US citizen
    assert by["q4"].source == "ai"  # a three-part essay box is drafted, not one profile value
    assert by["q5"].source == "you"
    assert not any(f.label in SETTINGS["default_questions"] for f in packet.fields)


def test_ashby_api_failure_falls_back_to_standard_fields(root):
    row = {"company": "Sample Capital", "title": "Quant Research Intern", "url": ASHBY_URL}
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get(), post=fake_post(status=500))
    assert [f.label for f in packet.fields][:2] == ["First Name", "Last Name"]
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get())  # no post: standard fields
    assert [f.label for f in packet.fields][:2] == ["First Name", "Last Name"]


def test_fill_ashby_by_field_id_and_leave_yes_no_buttons(root):
    row = {"company": "Sample Capital", "title": "Quant Research Intern", "url": ASHBY_URL}
    packet = build(row, PROFILE, SETTINGS, root, None, fake_get(), post=fake_post())
    page = FakePage(names=["_systemfield_name", "_systemfield_email", "_systemfield_resume"])
    report = fill_form(page, packet)
    assert report.filled == ["Name", "Email", "Resume"]
    assert ("fill", '[id="_systemfield_name"]', PROFILE["name"]) in page.calls
    assert any(
        c[:2] == ("upload", 'input[type="file"][id="_systemfield_resume"]') for c in page.calls
    )
    assert not any(c[0] == "upload_label" for c in page.calls)  # never the autofill upload
    assert "Are you legally authorized to work in the United States? (pick: Yes)" in report.yours
