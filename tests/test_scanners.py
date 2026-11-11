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


def test_real_indeed_layout_with_wrapped_links():
    (job,) = parse_alert_html(load_fixture("alert_indeed_real_layout.html"))
    assert (job.title, job.company, job.locations) == (
        "Associate - Portfolio Strategy",
        "Example Capital LLC",
        ["Coral Gables, FL 33134"],
    )
    assert job.url == "https://www.indeed.com/viewjob?jk=2b26f3a0529ff10e"


def test_real_linkedin_layout_with_whole_card_links():
    jobs = parse_alert_html(load_fixture("alert_linkedin_real_layout.html"))
    assert [(j.title, j.company, j.locations) for j in jobs] == [
        ("Trading Services Associate I", "Example Bank", ["New York, NY (On-site)"]),
        (
            "Graduate Equity Analyst (2027 Start - Chicago)",
            "Sample Markets",
            ["Chicago, IL (On-site)"],
        ),
    ]


def test_mixed_alert_email_with_tracking_link():
    jobs = {j.source: j for j in parse_alert_html(load_fixture("alert_mixed.html"))}
    assert set(jobs) == {"alert:indeed", "alert:handshake", "alert:efinancialcareers"}
    assert jobs["alert:indeed"].url == "https://www.indeed.com/viewjob?jk=1a2b3c4d5e6f7a8b"
    assert jobs["alert:indeed"].company == "Example Health"
    assert jobs["alert:handshake"].url == "https://app.joinhandshake.com/jobs/9876543"
    assert jobs["alert:handshake"].locations == ["Coral Gables, FL"]
    assert jobs["alert:efinancialcareers"].title == "Trading Intern"


class _Frame:
    """Stands in for the pandas DataFrame JobSpy returns."""

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        return self.rows


def test_jobspy_stops_a_site_after_it_blocks(monkeypatch):
    import logging
    import sys
    import types

    from jobengine.scanners import boards

    calls = []

    def fake_scrape_jobs(site_name, search_term, **kw):
        site = site_name[0]
        calls.append((site, search_term, kw["job_type"]))
        if site == "linkedin":
            logging.getLogger("jobspy.linkedin").error("response status code 429")
            return _Frame([])
        row = {
            "site": site,
            "title": f"{search_term} role",
            "company": "Acme",
            "job_url": f"https://example.com/{site}/{search_term}",
        }
        return _Frame([row])

    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=fake_scrape_jobs))
    settings = {
        "sites": ["linkedin", "zip_recruiter"],
        "passes": [{"location": "United States", "terms": ["trading intern", "quant intern"]}],
    }
    jobs = boards.scan(settings, pause_seconds=(0, 0))

    assert [c[0] for c in calls].count("linkedin") == 1  # blocked after the first call
    assert [c for c in calls if c[0] == "linkedin"][0][2] is None  # no job_type on LinkedIn
    assert len(jobs) == 2 and all(j.source == "jobspy:zip_recruiter" for j in jobs)


def test_jobspy_gives_up_on_a_site_that_hangs(monkeypatch):
    import sys
    import threading
    import types

    from jobengine.scanners import boards

    release = threading.Event()
    calls = []

    def fake_scrape_jobs(site_name, search_term, **kw):
        site = site_name[0]
        calls.append(site)
        if site == "zip_recruiter":
            release.wait(5)  # hangs past the deadline
            return _Frame([])
        row = {
            "site": site,
            "title": "Intern",
            "company": "Acme",
            "job_url": f"https://x/{site}/{search_term}",
        }
        return _Frame([row])

    monkeypatch.setitem(sys.modules, "jobspy", types.SimpleNamespace(scrape_jobs=fake_scrape_jobs))
    settings = {
        "sites": ["zip_recruiter", "indeed"],
        "call_timeout_seconds": 0.2,
        "passes": [{"location": "United States", "terms": ["a", "b"]}],
    }
    jobs = boards.scan(settings, pause_seconds=(0, 0))
    release.set()
    assert calls.count("zip_recruiter") == 1  # skipped after the first hang
    assert len(jobs) == 2 and all(j.source == "jobspy:indeed" for j in jobs)
