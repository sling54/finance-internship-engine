import json

import pytest
from test_init import answer_prompts, project  # noqa: F401  (fixture)

from jobengine import cli
from jobengine.config import Config, load_yaml
from jobengine.resume import clean, guess_from_resume, parse_model_reply, read_profile_facts

RESUME = """ALEX EXAMPLE
Miami, FL | alex@example.com | (555) 010-0100

EDUCATION
Example State University, College of Business                     Miami, FL
Bachelor of Business Administration in Finance, Minor in Economics
Expected Graduation: December 2027 | GPA: 3.50/4.00
U.S. Citizen

EXPERIENCE
Example Capital - Trading Intern                                   Jun 2026 - Aug 2026
"""


class FakeModel:
    def __init__(self, reply="", fail=False):
        self.reply, self.fail, self.prompts = reply, fail, []

    def pick_model(self):
        if self.fail:
            raise ConnectionError("LM Studio isn't running")
        return "fake-model"

    def chat(self, system, user):
        self.prompts.append(user)
        return self.reply


def test_rules_read_the_example_resume():
    assert guess_from_resume(RESUME) == {
        "name": "Alex Example",
        "school": "Example State University",
        "degree": "bachelors",
        "major": "Finance",
        "minor": "Economics",
        "gpa": 3.5,
        "graduation": "2027-12",
        "work_authorization": "us_citizen",
        "email": "alex@example.com",
        "phone": "(555) 010-0100",
    }


def test_rules_read_contact_details():
    found = guess_from_resume("Alex Example\nlinkedin.com/in/alex-example | 555.010.0100")
    assert found["linkedin"] == "https://linkedin.com/in/alex-example"
    assert found["phone"] == "555.010.0100"
    assert clean({"email": "not an email", "phone": "12345", "linkedin": "x.com/alex"}) == {}


def test_init_saves_contact_details_from_the_resume(project, monkeypatch):  # noqa: F811
    resume = write_resume(project)
    no_model(monkeypatch)
    answer_prompts(monkeypatch)
    assert cli.main(["init", "--resume", str(resume)]) == 0
    profile = load_yaml(project / "config/profile.yml")
    assert (profile["email"], profile["phone"]) == ("alex@example.com", "(555) 010-0100")
    assert "linkedin" not in profile  # not on the resume and left blank: not saved


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "B.S. in Mathematics\nGPA 3.8",
            {"degree": "bachelors", "major": "Mathematics", "gpa": 3.8},
        ),
        (
            "University of Example\nMaster of Science in Financial Engineering",
            {
                "school": "University of Example",
                "degree": "masters",
                "major": "Financial Engineering",
            },
        ),
        ("Class of 2028", {"graduation": "2028-05"}),
        ("May 2027 (expected)", {"graduation": "2027-05"}),
        ("Anticipated graduation: Sept. 2027", {"graduation": "2027-09"}),
        ("3.72 GPA", {"gpa": 3.72}),
        ("Will require visa sponsorship", {"work_authorization": "needs_sponsorship"}),
        ("Permanent Resident", {"work_authorization": "permanent_resident"}),
        ("Major: Economics and Data Science", {"major": "Economics and Data Science"}),
    ],
)
def test_rules_read_common_resume_wording(text, expected):
    found = guess_from_resume(text)
    assert {key: found.get(key) for key in expected} == expected


def test_rules_leave_out_what_the_resume_does_not_say():
    assert guess_from_resume("trading club member\nexcel, python") == {}


def test_clean_drops_values_init_would_reject():
    assert clean(
        {
            "name": "Alex Example",
            "gpa": "9.9",
            "graduation": "December 2027",
            "degree": "associate",
            "work_authorization": "maybe",
            "major": "",
            "minor": None,
        }
    ) == {"name": "Alex Example"}


def test_model_answers_replace_rule_guesses():
    reply = '<think>reading</think>{"school": "Example State University", "major": "Finance and Economics", "gpa": "3.5", "graduation": "2027-12", "degree": "phd?"}'
    found, reader = read_profile_facts(RESUME, FakeModel(reply))
    assert reader == "fake-model"
    assert found["major"] == "Finance and Economics"  # the model's answer wins
    assert found["degree"] == "bachelors"  # its invalid degree is ignored, the rule's kept
    assert found["minor"] == "Economics"  # the model left it out, the rule's kept


@pytest.mark.parametrize("client", [FakeModel(fail=True), FakeModel("not json"), None])
def test_rules_alone_when_the_model_is_missing_or_unusable(client):
    found, reader = read_profile_facts(RESUME, client)
    assert reader == "rules"
    assert found == guess_from_resume(RESUME)


def test_parse_model_reply_needs_json():
    with pytest.raises(ValueError):
        parse_model_reply("sorry, I can't")
    assert parse_model_reply(json.dumps({"name": "Alex Example"})) == {"name": "Alex Example"}


def write_resume(project):  # noqa: F811
    resume = project / "private/Resume.txt"
    resume.parent.mkdir(exist_ok=True)
    resume.write_text(RESUME)
    return resume


def no_model(monkeypatch):
    def unreachable(self):
        raise ConnectionError("LM Studio isn't running")

    monkeypatch.setattr("jobengine.scorer.LMStudio.pick_model", unreachable)


def test_init_from_resume_needs_only_enter(project, monkeypatch, capsys):  # noqa: F811
    resume = write_resume(project)
    no_model(monkeypatch)
    answer_prompts(monkeypatch)  # Enter at every prompt

    assert cli.main(["init", "--resume", str(resume)]) == 0
    profile = load_yaml(project / "config/profile.yml")
    assert {k: profile[k] for k in ("name", "school", "degree", "major", "minor", "gpa")} == {
        "name": "Alex Example",
        "school": "Example State University",
        "degree": "bachelors",
        "major": "Finance",
        "minor": "Economics",
        "gpa": 3.5,
    }
    assert profile["graduation"] == "2027-12"
    assert profile["grad_window"] == {"start": "2027-12", "end": "2027-12"}
    assert profile["work_authorization"] == "us_citizen"
    assert profile["resume_path"] == str(resume.resolve())
    assert "what_i_want" not in profile
    out = capsys.readouterr().out
    assert "fixed rules (LM Studio isn't running)" in out
    assert "found name, school, degree, major, minor, gpa, graduation, work_authorization" in out


def test_init_from_resume_lets_answers_override(project, monkeypatch):  # noqa: F811
    resume = write_resume(project)
    no_model(monkeypatch)
    answer_prompts(monkeypatch, "", "", "", "Economics", "Finance")

    assert cli.main(["init", "--resume", str(resume)]) == 0
    profile = load_yaml(project / "config/profile.yml")
    assert (profile["major"], profile["minor"]) == ("Economics", "Finance")


def test_init_saves_what_i_want_and_scoring_uses_it(project, monkeypatch):  # noqa: F811
    resume = write_resume(project)
    no_model(monkeypatch)
    wants = "Trading and quant internships in Miami or New York. No investment banking."
    replies = iter([wants])
    monkeypatch.setattr(
        "builtins.input",
        lambda label: next(replies) if label.startswith("What internships") else "",
    )

    assert cli.main(["init", "--resume", str(resume)]) == 0
    assert load_yaml(project / "config/profile.yml")["what_i_want"] == wants
    # The project fixture points Config.load at its own config folder.
    assert Config.load().scoring["what_i_want"] == wants


def test_example_profile_never_overrides_scoring(project):  # noqa: F811
    example = project / "config/profile.example.yml"
    example.write_text(example.read_text() + "what_i_want: example words\n")
    assert Config.load().scoring["what_i_want"] != "example words"


def test_init_stops_when_the_resume_is_missing(project, capsys):  # noqa: F811
    assert cli.main(["init", "--resume", str(project / "missing.pdf")]) == 1
    assert "no resume at" in capsys.readouterr().out
    assert not (project / "config/profile.yml").exists()
