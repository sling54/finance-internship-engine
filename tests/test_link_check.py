import csv
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from test_pipeline import collected

from jobengine import link_check, pipeline, removed
from jobengine.link_check import CLOSED, OPEN, UNKNOWN, HostPacer, check_url
from jobengine.models import Posting
from jobengine.pipeline import Result

PHRASES = link_check.DEFAULTS["closed_phrases"]
SETTINGS = {**link_check.DEFAULTS, "per_host_seconds": 0, "concurrency": 4}


class Resp:
    def __init__(self, status=200, url="", text="", data=None, history=()):
        self.status_code, self.url, self.text = status, url, text
        self.history, self.data = list(history), data

    def json(self):
        if self.data is None:
            raise ValueError("not JSON")
        return self.data


def fake(answers):
    """A get() that answers from a dict; anything else is a connection error."""
    calls = []

    def get(url):
        calls.append(url)
        answer = answers.get(url)
        if answer is None:
            raise ConnectionError(url)
        if isinstance(answer, Exception):
            raise answer
        if not answer.url:
            answer.url = url
        return answer

    get.calls = calls
    return get


def status(url, answers):
    return check_url(url, fake(answers), PHRASES)


# ---------- job boards ----------

GH = "https://job-boards.greenhouse.io/imc/jobs/4842595101"
GH_API = "https://boards-api.greenhouse.io/v1/boards/imc/jobs/4842595101"


@pytest.mark.parametrize("url", [GH, "https://boards.greenhouse.io/imc/jobs/4842595101?gh_src=x"])
def test_greenhouse(url):
    assert status(url, {GH_API: Resp(200, data={})}).status == OPEN
    closed = status(url, {GH_API: Resp(404)})
    assert closed.status == CLOSED and "Greenhouse" in closed.detail


EMBED = "https://boards.greenhouse.io/embed/job_app?token=8570661002"
GSA_API = "https://boards-api.greenhouse.io/v1/boards/gsacapital/jobs/8570661002"
FIRM_LINKS = [
    "https://www.gsacapital.com/careers/gh/?gh_jid=8570661002",
    "https://www.janestreet.com/join-jane-street/apply/8570661002?gh_jid=8570661002",
]


@pytest.mark.parametrize("url", FIRM_LINKS)
def test_firm_site_greenhouse_link_uses_the_known_board(url):
    # Real GSA case: the firm's page was a 404 while the Greenhouse job was open.
    def check(answers):
        return check_url(url, fake({url: Resp(404), **answers}), PHRASES, "gsacapital")

    assert check({GSA_API: Resp(200, data={})}).status == OPEN
    closed = check({GSA_API: Resp(404)})
    assert closed.status == CLOSED and closed.evidence == f"HTTP 404 {GSA_API}"
    assert check({GSA_API: Resp(503)}).status == UNKNOWN


@pytest.mark.parametrize("url", [*FIRM_LINKS, EMBED])
def test_greenhouse_embed_404_is_not_proof(url):
    # Real Jane Street case: open jobs whose embed page is a 404 (embedding turned off).
    assert status(url, {url: Resp(404), EMBED: Resp(200, url=EMBED + "&for=x")}).status == OPEN
    found = status(url, {url: Resp(404), EMBED: Resp(404)})
    assert found.status == UNKNOWN and "embed" in found.detail


def test_scanner_board_reaches_the_check(tmp_path):
    role = job("Firm Intern", FIRM_LINKS[1])
    role.extra["greenhouse_board"] = "gsacapital"
    get = fake({GSA_API: Resp(200, data={})})
    report = link_check.apply(_result(role), SETTINGS, tmp_path, get)
    assert report.open == 1 and get.calls == [GSA_API]


def test_greenhouse_scanner_records_the_board(monkeypatch):
    from jobengine.scanners import ats

    data = {"jobs": [{"title": "Intern", "absolute_url": FIRM_LINKS[1], "location": {}}]}
    monkeypatch.setattr(ats, "get_json", lambda *a, **k: data)
    monkeypatch.setattr(ats, "session", lambda: None)
    (found,) = ats.scan({"greenhouse": {"Jane Street": "janestreet"}})
    assert found.extra["greenhouse_board"] == "janestreet"


def test_greenhouse_eu_board_uses_the_same_api():
    url = "https://job-boards.eu.greenhouse.io/imc/jobs/4842595101"
    assert status(url, {GH_API: Resp(200, data={})}).status == OPEN


def test_lever():
    url = "https://jobs.lever.co/acme/006928a3-9a6d-49a3-be4f-46d77f0bb302/apply"
    api = "https://api.lever.co/v0/postings/acme/006928a3-9a6d-49a3-be4f-46d77f0bb302"
    assert status(url, {api: Resp(200, data={})}).status == OPEN
    assert status(url, {api: Resp(404)}).status == CLOSED


def test_ashby():
    url = "https://jobs.ashbyhq.com/acme/5e2ea37b/application?embed=true"
    board = "https://api.ashbyhq.com/posting-api/job-board/acme"
    listed = {board: Resp(200, data={"jobs": [{"id": "5e2ea37b"}]})}
    assert status(url, listed).status == OPEN
    gone = status(url, {board: Resp(200, data={"jobs": [{"id": "other"}]})})
    assert gone.status == CLOSED and "Ashby" in gone.detail
    # A missing board or odd answer is not proof the job closed.
    assert status(url, {board: Resp(404)}).status == UNKNOWN
    assert status(url, {board: Resp(200, data={"oops": 1})}).status == UNKNOWN


@pytest.mark.parametrize(
    "url,api",
    [
        (
            "https://invesco.wd1.myworkdayjobs.com/IVZearlycareers/job/New-York/Intern_R-15803",
            "https://invesco.wd1.myworkdayjobs.com/wday/cxs/invesco/IVZearlycareers/job/New-York/Intern_R-15803",
        ),
        (
            "https://invesco.wd1.myworkdayjobs.com/en-US/IVZ/job/NY/Intern_R-1?source=x",
            "https://invesco.wd1.myworkdayjobs.com/wday/cxs/invesco/IVZ/job/NY/Intern_R-1",
        ),
        (
            "https://wd3.myworkdaysite.com/recruiting/brevanhoward/BH_ExternalCareers/job/New-York/AI_JR101602",
            "https://wd3.myworkdaysite.com/wday/cxs/brevanhoward/BH_ExternalCareers/job/New-York/AI_JR101602",
        ),
    ],
)
def test_workday(url, api):
    info = {"jobPostingInfo": {"canApply": True}}
    assert status(url, {api: Resp(200, data=info)}).status == OPEN
    assert status(url, {api: Resp(404, data={"errorCode": "S21"})}).status == CLOSED
    no_apply = {"jobPostingInfo": {"canApply": False}}
    assert status(url, {api: Resp(200, data=no_apply)}).status == CLOSED
    assert status(url, {api: Resp(200, data={"other": 1})}).status == UNKNOWN


def test_smartrecruiters():
    url = "https://jobs.smartrecruiters.com/InfiniteQuant/744000145263134-quant-dev"
    api = "https://api.smartrecruiters.com/v1/companies/InfiniteQuant/postings/744000145263134"
    assert status(url, {api: Resp(200, data={"active": True})}).status == OPEN
    assert status(url, {api: Resp(200, data={"active": False})}).status == CLOSED
    assert status(url, {api: Resp(404)}).status == CLOSED
    assert status(url, {api: Resp(400)}).status == UNKNOWN  # a bad id is not a closed job


# ---------- other pages ----------

PAGE = "https://careers.example.com/jobs/12345-quant-intern"


@pytest.mark.parametrize("code", [404, 410])
def test_page_gone_is_closed(code):
    assert status(PAGE, {PAGE: Resp(code)}).status == CLOSED


@pytest.mark.parametrize("phrase", ["No longer accepting applications", "Position has been FILLED"])
def test_page_phrase_is_closed(phrase):
    found = status(PAGE, {PAGE: Resp(200, text=f"<div><b>{phrase}</b></div>")})
    assert found.status == CLOSED and phrase.lower() in found.detail.lower()


@pytest.mark.parametrize("code", [403, 429, 500, 502, 503, 999])
def test_blocked_or_failing_pages_are_unknown(code):
    assert status(PAGE, {PAGE: Resp(code)}).status == UNKNOWN


@pytest.mark.parametrize("error", [TimeoutError("slow"), ConnectionError("down"), ValueError()])
def test_errors_are_unknown(error):
    found = status(PAGE, {PAGE: error})
    assert found.status == UNKNOWN and found.detail == type(error).__name__


def test_api_timeouts_are_unknown():
    assert status(GH, {GH_API: TimeoutError()}).status == UNKNOWN
    assert status(GH, {GH_API: Resp(503)}).status == UNKNOWN
    assert status(GH, {GH_API: Resp(429)}).status == UNKNOWN


@pytest.mark.parametrize(
    "final",
    [
        "https://careers.example.com/",
        "https://careers.example.com/careers",
        "https://careers.example.com/en/jobs",
        "https://job-boards.greenhouse.io/imc?error=true",
    ],
)
def test_redirect_to_careers_home_is_flagged(final):
    found = status(PAGE, {PAGE: Resp(200, url=final, text="Join us", history=[Resp(302)])})
    assert found.status == UNKNOWN and found.flag == link_check.CAREERS_REDIRECT


def test_redirect_to_another_job_page_is_open():
    final = "https://careers.example.com/en/jobs/12345"
    assert status(PAGE, {PAGE: Resp(200, url=final, history=[Resp(301)])}).status == OPEN


def test_redirect_that_keeps_the_job_id_in_the_query_is_open():
    url = "https://careers.example.com/job?id=8300829"
    final = "https://careers.example.com/en/job?for=fanduel&id=8300829"
    assert status(url, {url: Resp(200, url=final, history=[Resp(301)])}).status == OPEN


def test_page_without_closed_signs_is_open():
    assert status(PAGE, {PAGE: Resp(200, text="Apply now")}).status == OPEN


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "mailto:jobs@example.com"])
def test_unusable_links_are_unknown(url):
    assert status(url, {}).status == UNKNOWN


# ---------- pacing and caching ----------


def test_host_pacer_spaces_one_host_only():
    now = [0.0]
    slept = []
    pacer = HostPacer(2.0, clock=lambda: now[0], sleep=slept.append)
    pacer.wait("a.com")
    pacer.wait("b.com")
    pacer.wait("a.com")
    pacer.wait("a.com")
    assert slept == [2.0, 4.0]


def test_per_host_pacing_with_threads(tmp_path, monkeypatch):
    # Checks only things a busy machine can't break: a thread delayed between its wait and
    # its request made the old timestamp check fail on Amir's Mac though pacing was right.
    urls = [f"https://slow.example.com/jobs/{i}" for i in range(4)]
    pacers = []
    original = link_check.HostPacer.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        pacers.append(self)

    monkeypatch.setattr(link_check.HostPacer, "__init__", init)
    cfg = {**SETTINGS, "per_host_seconds": 0.05, "concurrency": 4}
    start = time.monotonic()
    link_check.check_all(urls, cfg, tmp_path, lambda url: Resp(200, url=url, text="ok"))
    assert time.monotonic() - start >= 0.149  # the 4th request waits 3 gaps for its slot
    (pacer,) = pacers
    assert pacer.next_slot["slow.example.com"] - start >= 0.199  # 4 slots handed out


def test_results_are_cached_for_a_day(tmp_path):
    get = fake({PAGE: Resp(200, text="ok")})
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    assert link_check.check_all([PAGE], SETTINGS, tmp_path, get, now)[PAGE].status == OPEN
    assert (tmp_path / "data" / "link_check.json").exists()
    link_check.check_all([PAGE], SETTINGS, tmp_path, get, now + timedelta(hours=23))
    assert len(get.calls) == 1
    link_check.check_all([PAGE], SETTINGS, tmp_path, get, now + timedelta(hours=25))
    assert len(get.calls) == 2


def test_unreadable_cache_is_ignored(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "link_check.json").write_text("{not json")
    get = fake({PAGE: Resp(200, text="ok")})
    assert link_check.check_all([PAGE], SETTINGS, tmp_path, get)[PAGE].status == OPEN


# ---------- applying to a scan ----------


def job(title, url, date_posted=""):
    return Posting(company="Acme", title=title, url=url, source="test", date_posted=date_posted)


def _result(*jobs):
    result = Result()
    result.kept = list(jobs)
    return result


def test_apply_sorts_roles_by_status(tmp_path):
    today = date(2026, 11, 3)
    open_job = job("Open Intern", "https://x.example.com/jobs/1", date_posted="2026-10-01")
    closed_job = job("Closed Intern", "https://x.example.com/jobs/2")
    old_unknown = job("Old Intern", "https://x.example.com/jobs/3", date_posted="2026-06-01")
    new_unknown = job("New Intern", "https://x.example.com/jobs/4", date_posted="2026-10-01")
    blank_date = job("Undated Intern", "https://x.example.com/jobs/5", date_posted="")
    redirect = job("Moved Intern", "https://x.example.com/jobs/6", date_posted="")
    get = fake(
        {
            open_job.url: Resp(200, text="ok"),
            closed_job.url: Resp(404),
            old_unknown.url: Resp(429),
            new_unknown.url: TimeoutError(),
            blank_date.url: Resp(403),
            redirect.url: Resp(200, url="https://x.example.com/careers", history=[Resp(302)]),
        }
    )
    result = _result(open_job, closed_job, old_unknown, new_unknown, blank_date, redirect)
    report = link_check.apply(result, SETTINGS, tmp_path, get, today)

    assert (report.open, report.closed, report.unknown, report.too_old) == (1, 1, 4, 1)
    assert [j.title for j in result.kept] == [
        "Open Intern",
        "New Intern",
        "Undated Intern",
        "Moved Intern",
    ]
    reasons = {j.title: r for j, r in result.rejected}
    assert reasons["Closed Intern"] == "removed: listing closed (HTTP 404)"
    assert reasons["Old Intern"].startswith("unverified and posted 2026-06-01 (over 120 days")
    assert open_job.flags == []
    assert new_unknown.flags == ["not verified"]
    assert redirect.flags == ["not verified", "redirects to careers page"]

    entry = removed.load(tmp_path)[closed_job.url]
    assert entry["by"] == "link_check" and entry["reason"] == "listing closed (HTTP 404)"
    assert old_unknown.url not in removed.load(tmp_path)  # unknown is never recorded


def test_closed_roles_are_all_recorded_from_one_thread(tmp_path, monkeypatch):
    jobs = [job(f"Role {i}", f"https://h{i % 5}.example.com/jobs/{i}") for i in range(60)]
    get = fake({j.url: Resp(410) for j in jobs})
    writers = set()
    real_add = removed.add

    def add(*args, **kw):
        writers.add(threading.current_thread().name)
        return real_add(*args, **kw)

    monkeypatch.setattr(removed, "add", add)
    link_check.apply(_result(*jobs), {**SETTINGS, "concurrency": 8}, tmp_path, get)
    assert len(removed.load(tmp_path)) == 60
    assert writers == {threading.current_thread().name}


def _scan(cfg, root, monkeypatch, answers):
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: collected())
    monkeypatch.setattr(link_check, "http_get", lambda timeout: fake(answers))
    monkeypatch.setattr(link_check, "BOARDS", ())  # fixtures' board APIs: tested above
    result = pipeline.run(cfg, root=root, ai=False)
    with (root / "data" / f"rejected_{date.today().isoformat()}.csv").open(
        encoding="utf-8-sig"
    ) as fh:
        reasons = {row["url"]: row["reason"] for row in csv.DictReader(fh)}
    with (root / "data" / "jobs_latest.csv").open(encoding="utf-8-sig") as fh:
        latest = {row["url"]: row for row in csv.DictReader(fh)}
    return result, reasons, latest


@pytest.fixture
def checking_cfg(cfg):
    cfg = type(cfg)(**vars(cfg))
    cfg.sources = {**cfg.sources, "link_check": {**SETTINGS, "per_host_seconds": 0}}
    return cfg


def test_scan_drops_closed_role_and_keeps_it_removed(checking_cfg, tmp_path, monkeypatch):
    first = pipeline.run_filters(collected(), pipeline.build_filters(checking_cfg, tmp_path))
    urls = [j.url for j in first.kept]
    closed_url, open_url = urls[0], urls[1]
    answers = {closed_url: Resp(404), open_url: Resp(200, text="ok")}
    result, reasons, latest = _scan(checking_cfg, tmp_path, monkeypatch, answers)
    assert reasons[closed_url] == "removed: listing closed (HTTP 404)"
    assert closed_url not in latest and open_url in latest
    assert "Link check: 1 open, 1 closed" in result.summary()

    # Next scan: already removed, so the scan filter drops it before any check.
    later = tmp_path / "data" / "link_check.json"
    later.unlink()
    _, reasons, latest = _scan(checking_cfg, tmp_path, monkeypatch, {open_url: Resp(200)})
    assert reasons[closed_url] == "removed: listing closed (HTTP 404)"
    assert closed_url not in latest


def test_unknown_roles_show_not_verified_in_results_csv(checking_cfg, tmp_path, monkeypatch):
    _, _, latest = _scan(checking_cfg, tmp_path, monkeypatch, {})  # every fetch fails
    assert latest
    assert all("not verified" in row["flags"] for row in latest.values())


def test_failed_check_never_fails_the_scan(checking_cfg, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("checker crashed")

    monkeypatch.setattr(link_check, "apply", boom)
    result, _, latest = _scan(checking_cfg, tmp_path, monkeypatch, {})
    assert result.kept and len(latest) == len(result.kept)
    assert result.links is None


def test_link_check_can_be_turned_off(cfg, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not run")

    monkeypatch.setattr(link_check, "apply", boom)
    result, _, _ = _scan(cfg, tmp_path, monkeypatch, {})  # conftest turns it off
    assert result.kept and result.links is None


# ---------- a damaged removed.json ----------


def _damage(root, text="{this is not json"):
    (root / "data").mkdir(exist_ok=True)
    removed.path(root).write_text(text)
    return text


def test_damaged_removed_file_is_copied_and_read_as_empty(tmp_path, caplog):
    bad = _damage(tmp_path)
    assert removed.load(tmp_path) == {}
    copies = list((tmp_path / "data").glob("removed.json.unreadable-*"))
    assert len(copies) == 1 and copies[0].read_text() == bad
    assert "can't be read" in caplog.text and copies[0].name in caplog.text
    removed.load(tmp_path)  # reading again doesn't pile up copies
    assert len(list((tmp_path / "data").glob("removed.json.unreadable-*"))) == 1


@pytest.mark.parametrize("text", ["[1, 2]", '{"https://x": "not an entry"}', "\xff\xfe"])
def test_wrong_shapes_count_as_damaged(tmp_path, text):
    _damage(tmp_path, text)
    assert removed.load(tmp_path) == {}
    assert list((tmp_path / "data").glob("removed.json.unreadable-*"))


def test_write_after_damage_keeps_a_copy_first(tmp_path):
    bad = _damage(tmp_path, '{"https://old": {"reason": "closed"}, truncated')
    removed.add(tmp_path, "https://new", "Acme", "Intern", "closed", "user")
    (copy,) = (tmp_path / "data").glob("removed.json.unreadable-*")
    assert copy.read_text() == bad  # the old entries survive in the copy
    assert list(removed.load(tmp_path)) == ["https://new"]


def test_no_write_when_the_copy_cannot_be_saved(tmp_path, monkeypatch, caplog):
    bad = _damage(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(removed.shutil, "copy2", fail)
    with pytest.raises(removed.UnreadableRemovedFile):
        removed.add(tmp_path, "https://new", "Acme", "Intern", "closed", "link_check")
    with pytest.raises(removed.UnreadableRemovedFile):
        removed.remove(tmp_path, "https://new")
    assert removed.path(tmp_path).read_text() == bad  # untouched
    assert removed.load(tmp_path) == {}  # readers still carry on
    assert "nothing will be written" in caplog.text


def test_scan_continues_with_damaged_removed_file(checking_cfg, tmp_path, monkeypatch):
    _damage(tmp_path)
    first = pipeline.run_filters(collected(), pipeline.build_filters(checking_cfg, tmp_path))
    closed_url = first.kept[0].url
    result, reasons, _ = _scan(checking_cfg, tmp_path, monkeypatch, {closed_url: Resp(404)})
    assert result.kept
    assert reasons[closed_url].startswith("removed: listing closed")
    assert list((tmp_path / "data").glob("removed.json.unreadable-*"))
    assert closed_url in removed.load(tmp_path)


def test_damaged_file_with_copy_failure_does_not_stop_scan(checking_cfg, tmp_path, monkeypatch):
    bad = _damage(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(removed.shutil, "copy2", fail)
    first = pipeline.run_filters(collected(), pipeline.build_filters(checking_cfg, tmp_path))
    closed_url = first.kept[0].url
    result, reasons, latest = _scan(checking_cfg, tmp_path, monkeypatch, {closed_url: Resp(404)})
    assert closed_url not in latest  # still dropped from this scan
    assert removed.path(tmp_path).read_text() == bad  # but the damaged file is untouched
    assert result.kept


def test_cache_file_is_json(tmp_path):
    link_check.check_all([PAGE], SETTINGS, tmp_path, fake({PAGE: Resp(200)}))
    data = json.loads((tmp_path / "data" / "link_check.json").read_text())
    assert data[PAGE]["status"] == OPEN and data[PAGE]["checked"]


# ---------- a failed or unclear check is never closed ----------


def test_404_after_a_redirect_is_unknown():
    # Real GSA case: the firm's page redirected (dropping a slash) and then 404'd.
    final = "https://www.gsacapital.com/careers/gh?gh_jid=1"
    found = status(PAGE, {PAGE: Resp(404, url=final, history=[Resp(301)])})
    assert found.status == UNKNOWN and "after a redirect" in found.detail


def test_phrase_after_a_redirect_is_unknown():
    final = "https://careers.example.com/jobs/99999-other-role"
    page = Resp(200, url=final, text="No longer accepting applications", history=[Resp(302)])
    assert status(PAGE, {PAGE: page}).status == UNKNOWN


def test_phrase_only_in_scripts_or_styles_is_ignored():
    page = (
        "<script>var msg = 'No longer accepting applications';</script>"
        "<style>.x:after{content:'position has been filled'}</style><p>Apply now</p>"
    )
    assert status(PAGE, {PAGE: Resp(200, text=page)}).status == OPEN


def test_board_api_redirect_is_unknown():
    assert status(GH, {GH_API: Resp(404, history=[Resp(301)])}).status == UNKNOWN


FAILURES = [Resp(code) for code in range(100, 600) if code not in (200, 404, 410)] + [
    TimeoutError(),
    ConnectionError(),
    OSError(),
    ValueError(),
    RuntimeError(),
]


@pytest.mark.parametrize(
    "answer", FAILURES, ids=lambda a: getattr(a, "status_code", None) or type(a).__name__
)
def test_no_failure_of_any_kind_counts_as_closed(answer):
    workday = "https://acme.wd1.myworkdayjobs.com/Site/job/NY/Intern_R-1"
    workday_api = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Site/job/NY/Intern_R-1"
    ashby = "https://jobs.ashbyhq.com/acme/abc"
    ashby_api = "https://api.ashbyhq.com/posting-api/job-board/acme"
    for url, endpoint in [(PAGE, PAGE), (GH, GH_API), (workday, workday_api), (ashby, ashby_api)]:
        assert status(url, {endpoint: answer}).status != CLOSED, (url, answer)


CLOSED_CASES = [
    (PAGE, {PAGE: Resp(404)}),
    (PAGE, {PAGE: Resp(410)}),
    (PAGE, {PAGE: Resp(200, text="<p>Position has been filled</p>")}),
    (GH, {GH_API: Resp(404)}),
    (
        "https://jobs.ashbyhq.com/acme/abc",
        {"https://api.ashbyhq.com/posting-api/job-board/acme": Resp(200, data={"jobs": []})},
    ),
    (
        "https://acme.wd1.myworkdayjobs.com/Site/job/NY/Intern_R-1",
        {
            "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Site/job/NY/Intern_R-1": Resp(
                200, data={"jobPostingInfo": {"canApply": False}}
            )
        },
    ),
    (
        "https://jobs.smartrecruiters.com/Acme/123-x",
        {
            "https://api.smartrecruiters.com/v1/companies/Acme/postings/123": Resp(
                200, data={"active": False}
            )
        },
    ),
]


@pytest.mark.parametrize("url,answers", CLOSED_CASES)
def test_every_closed_answer_carries_evidence(url, answers):
    found = status(url, answers)
    assert found.status == CLOSED and found.evidence


def test_closed_without_evidence_is_never_removed(tmp_path, monkeypatch):
    role = job("Guess Intern", "https://x.example.com/jobs/1")
    monkeypatch.setattr(
        link_check, "check_all", lambda *a, **k: {role.url: link_check.Check(CLOSED, "guess")}
    )
    result = _result(role)
    report = link_check.apply(result, SETTINGS, tmp_path, get=None)
    assert report.closed == 0 and report.unknown == 1
    assert result.kept == [role] and not removed.load(tmp_path)


def test_cached_closed_without_evidence_is_checked_again(tmp_path):
    (tmp_path / "data").mkdir()
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    old = {PAGE: {"status": CLOSED, "detail": "HTTP 404", "checked": now.isoformat()}}
    (tmp_path / "data" / "link_check.json").write_text(json.dumps(old))
    get = fake({PAGE: Resp(200, text="ok")})
    assert link_check.check_all([PAGE], SETTINGS, tmp_path, get, now)[PAGE].status == OPEN
    assert get.calls == [PAGE]


def test_site_that_blocks_the_checker_never_drops_an_old_role(tmp_path):
    # citadel.com answers scripts with 403, so its evergreen intern posts (first posted
    # well over 120 days ago) were dropped as "unverified and old" on Nov 11.
    today = date(2026, 11, 11)
    citadel = job(
        "Quantitative Research Analyst - Intern (US)",
        "https://www.citadel.com/careers/details/quantitative-research-analyst-intern-us/",
        date_posted="2026-05-01",
    )
    old_timeout = job("Old Intern", "https://x.example.com/jobs/9", date_posted="2026-05-01")
    get = fake({citadel.url: Resp(403), old_timeout.url: TimeoutError()})
    result = _result(citadel, old_timeout)
    report = link_check.apply(result, SETTINGS, tmp_path, get, today)

    assert [j.title for j in result.kept] == ["Quantitative Research Analyst - Intern (US)"]
    assert citadel.flags == ["not verified", "site blocks the link check"]
    assert (report.unknown, report.too_old) == (2, 1)
    assert status(citadel.url, {citadel.url: Resp(401)}).status == UNKNOWN
