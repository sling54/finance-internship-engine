"""Newer Greenhouse forms (job-boards.greenhouse.io): fields have ids, not names; dropdowns
are React comboboxes; the resume and cover letter are file uploads."""

import pytest
from test_packet import GH_URL, PROFILE, SETTINGS, FakePage, fake_get

from jobengine.formfill import FormPage, fill_form
from jobengine.packet import Field, Packet, build, load, write


class IdOnlyPage(FakePage):
    """A page where fields answer only to [id="..."] and dropdowns are comboboxes."""

    def _has(self, selector):
        return any(f'[id="{n}"]' in selector for n in self.names)

    def pick(self, selector, option):
        self.calls.append(("pick", selector, option))
        return self._has(selector)


def gh_packet(fields):
    return Packet("SESCO", "Quant Intern", GH_URL, GH_URL, "greenhouse", fields=fields)


def test_id_only_fields_files_and_combobox_dropdowns(tmp_path):
    resume = tmp_path / "Resume.pdf"
    resume.write_bytes(b"%PDF fake")
    packet = gh_packet(
        [
            Field("First Name", "first_name", answer="Alex", source="profile"),
            Field("Resume/CV", "resume", kind="file", answer=str(resume), source="profile"),
            Field("Resume/CV", "resume_text", kind="textarea", answer="", source="you"),
            Field("Contact method?", "question_7", kind="select", answer="Email", source="rule"),
        ]
    )
    page = IdOnlyPage(names=["first_name", "resume", "question_7"])
    report = fill_form(page, packet)
    assert report.filled == ["First Name", "Resume/CV", "Contact method?"]
    assert report.missed == [] and report.yours == []  # resume text box isn't asked for
    assert ("pick", 'input[role="combobox"][id="question_7"]', "Email") in page.calls
    assert ("upload", 'input[type="file"][id="resume"]', str(resume)) in page.calls


def test_base_page_has_no_click_and_pick_defaults_to_not_found():
    assert not hasattr(FormPage, "click")
    assert FormPage().pick('input[id="x"]', "Yes") is False


GREENHOUSE_FILES = {
    "title": "Trading Intern",
    "content": "",
    "questions": [
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
            "fields": [
                {"name": "cover_letter", "type": "input_file"},
                {"name": "cover_letter_text", "type": "textarea"},
            ],
        },
    ],
}


class Model:
    def __init__(self):
        self.asked = []

    def chat(self, system, user, **kw):
        self.asked.append(user)
        return "I trade power markets in my student fund and want to learn from your desk."


@pytest.fixture
def project(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / PROFILE["resume_path"]).write_bytes(b"%PDF fake")
    return tmp_path


def test_no_ai_resume_and_cover_letter_attached_as_a_file(project):
    root = project
    model = Model()
    row = {"company": "Example Capital", "title": "Trading Intern", "url": GH_URL}
    packet = build(row, PROFILE, SETTINGS, root, model, fake_get(data=GREENHOUSE_FILES))
    by_name = {f.name: f for f in packet.fields}
    assert by_name["resume_text"].answer == ""  # the AI never writes a resume
    assert len(model.asked) == 1  # only the cover letter was drafted

    folder = write(packet, root)
    letter = folder / "cover_letter.txt"
    assert "power markets" in letter.read_text()
    again = {f.name: f for f in load(folder).fields}
    assert again["cover_letter"].answer == str(letter)
    assert again["cover_letter"].source == "ai"

    page = IdOnlyPage(names=["resume", "cover_letter"])
    report = fill_form(page, load(folder))
    assert report.filled == ["Resume/CV", "Cover Letter"]
    assert report.missed == [] and report.yours == []
