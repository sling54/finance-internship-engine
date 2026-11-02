from helpers import load_fixture

from jobengine.scanners.alerts import parse_alert_html
from jobengine.scanners.ats import parse_ashby, parse_greenhouse, parse_lever
from jobengine.scanners.boards import parse_jobspy_records
from jobengine.scanners.lists import parse_kadoa, parse_simplify


def test_greenhouse_strips_escaped_html():
    jobs = parse_greenhouse("Example", load_fixture("greenhouse.json"))
    assert len(jobs) == 2
    first = jobs[0]
    assert first.title == "Quantitative Trader Intern - Summer 2027"
    assert first.locations == ["Chicago, IL"]
    assert first.date_posted == "2026-09-01"
    assert "December 2027 and June 2028" in first.description
    assert "<" not in first.description


def test_lever_reads_commitment_and_all_locations():
    (job,) = parse_lever("Example", load_fixture("lever.json"))
    assert job.is_internship is True
    assert job.locations == ["Chicago, IL", "Boulder, CO"]
    assert job.date_posted == "2026-11-02"


def test_ashby_skips_unlisted_roles():
    jobs = parse_ashby("Example", load_fixture("ashby.json"))
    assert [j.title for j in jobs] == [
        "Quantitative Trader - 2027 Micro-Internship Program (January Start)"
    ]
    assert jobs[0].locations == ["Chicago", "New York"]
    assert jobs[0].is_internship is True


def test_simplify_keeps_active_roles_in_wanted_categories():
    jobs = parse_simplify(load_fixture("simplify.json"), ["Quant"])
    assert [j.title for j in jobs] == [
        "Quantitative Research Intern",
        "Quant Researcher Intern - PhD",
    ]
    assert jobs[0].terms == ["Summer 2027"]
    assert jobs[1].degrees == ["PhD"]


def test_kadoa_keeps_only_internships():
    (job,) = parse_kadoa(load_fixture("kadoa.json"))
    assert job.company == "Example Markets"
    assert job.url == "https://example.com/apply/q1"
    assert job.salary == "150000"


def test_jobspy_records_handle_missing_values():
    nan = float("nan")
    (job,) = parse_jobspy_records(
        [
            {
                "site": "indeed",
                "title": "Trading Intern",
                "company": "Acme",
                "location": "Miami, FL",
                "job_url": "https://indeed.com/viewjob?jk=1",
                "job_url_direct": nan,
                "job_type": "internship",
                "min_amount": nan,
                "max_amount": nan,
                "description": nan,
                "date_posted": "2026-11-01",
            }
        ]
    )
    assert job.source == "jobspy:indeed"
    assert job.url == "https://indeed.com/viewjob?jk=1"
    assert job.is_internship is True
    assert job.salary == "" and job.description == ""


def test_linkedin_alert_email():
    jobs = parse_alert_html(load_fixture("alert_linkedin.html"))
    assert [(j.title, j.company, j.locations) for j in jobs] == [
        ("Options Trading Intern", "Example Capital", ["Chicago, IL"]),
        ("Quantitative Analyst Intern", "Sample Fund", ["New York, NY"]),
    ]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4012345678/"


def test_mixed_alert_email_with_tracking_link():
    jobs = {j.source: j for j in parse_alert_html(load_fixture("alert_mixed.html"))}
    assert set(jobs) == {"alert:indeed", "alert:handshake", "alert:efinancialcareers"}
    assert jobs["alert:indeed"].url == "https://www.indeed.com/viewjob?jk=1a2b3c4d5e6f7a8b"
    assert jobs["alert:indeed"].company == "Example Health"
    assert jobs["alert:handshake"].url == "https://app.joinhandshake.com/jobs/9876543"
    assert jobs["alert:handshake"].locations == ["Coral Gables, FL"]
    assert jobs["alert:efinancialcareers"].title == "Trading Intern"
