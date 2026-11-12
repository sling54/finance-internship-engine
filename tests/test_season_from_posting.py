"""Roles a list gives no term for (SimplifyJobs "N/A") get their season from the open
posting the link check already reads."""

from test_link_check import SETTINGS, Resp, fake

from jobengine import link_check
from jobengine.link_check import OPEN, check_url, season_of
from jobengine.models import Posting
from jobengine.pipeline import Result

PHRASES = SETTINGS["closed_phrases"]
WD = "https://invesco.wd1.myworkdayjobs.com/IVZearlycareers/job/New-York/Quant-Intern_R-1"
WD_API = "https://invesco.wd1.myworkdayjobs.com/wday/cxs/invesco/IVZearlycareers/job/New-York/Quant-Intern_R-1"
GH = "https://job-boards.greenhouse.io/janestreet/jobs/8498547002"
GH_API = "https://boards-api.greenhouse.io/v1/boards/janestreet/jobs/8498547002"
ASHBY = "https://jobs.ashbyhq.com/anthelion/abc"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/anthelion"


def test_season_of_reads_terms_dates_and_phrases():
    assert season_of({"content": "&lt;p&gt;Our 10-week Summer 2027 program&lt;/p&gt;"}) == "Summer"
    assert season_of(["Dates: starting January 12, 2027 for one term"]) == "Spring"
    assert season_of("Join our summer interns on the desk") == "Summer"
    assert season_of({"title": "Quant Intern", "id": 5}) == ""


def test_workday_greenhouse_and_ashby_postings_give_the_season():
    info = {"jobPostingInfo": {"jobDescription": "<p>Summer 2027 internship in NYC</p>"}}
    found = check_url(WD, fake({WD_API: Resp(200, data=info)}), PHRASES)
    assert (found.status, found.season) == (OPEN, "Summer")

    gh = {"content": "&lt;p&gt;Fall 2027 internship&lt;/p&gt;"}
    assert check_url(GH, fake({GH_API: Resp(200, data=gh)}), PHRASES).season == "Fall"

    board = {
        "jobs": [
            {"id": "other", "descriptionPlain": "Summer 2027 analyst"},
            {"id": "abc", "descriptionPlain": "Winter 2027 co-op"},
        ]
    }
    assert check_url(ASHBY, fake({ASHBY_API: Resp(200, data=board)}), PHRASES).season == "Winter"

    plain = "https://careers.sig.com/global-susquehanna/jobs/11491"
    page = Resp(200, text="<html><p>Our summer internship program runs ten weeks</p></html>")
    assert check_url(plain, fake({plain: page}), PHRASES).season == "Summer"


def role(url, season):
    job = Posting("Invesco", "Quantitative Research Intern", url, "simplify")
    job.season = season
    job.flags = ["check season"] if season == "Unknown" else []
    return job


def test_apply_fills_only_unknown_seasons_and_caches_them(tmp_path):
    info = {"jobPostingInfo": {"jobDescription": "Summer 2027 internship"}}
    unknown = role(WD, "Unknown")
    known_url = "https://x.example.com/jobs/1"
    known = role(known_url, "Fall")
    get = fake({WD_API: Resp(200, data=info), known_url: Resp(200, text="summer 2027 interns")})
    result = Result()
    result.kept = [unknown, known]
    report = link_check.apply(result, SETTINGS, tmp_path, get)
    assert unknown.season == "Summer" and "check season" not in unknown.flags
    assert known.season == "Fall"  # a season the list or title gave is kept
    assert report.seasons == 1 and "season read from 1 postings" in report.summary()

    again = role(WD, "Unknown")
    result.kept = [again]
    link_check.apply(result, SETTINGS, tmp_path, fake({}))  # answered from the cache
    assert again.season == "Summer"
