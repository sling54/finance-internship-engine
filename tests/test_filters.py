import pytest
from helpers import job

from jobengine.filters.dedup import dedupe
from jobengine.filters.eligibility import EligibilityFilter
from jobengine.models import Rejected

# ---------- Feature 2: titles ----------


@pytest.mark.parametrize(
    "title, track",
    [
        ("Quantitative Trading Intern", "Trading"),
        ("Derivatives Summer Intern", "Quant / derivatives"),
        ("2027 Investment Analyst Internship", "Buy-side investing"),
        ("Data Analyst Intern", "Data analytics"),
        ("Software Engineer Intern", "Software engineering"),
        # Dropped as "no matching track" on Nov 12 (318 software titles, e.g.):
        ("Software Development Intern", "Software engineering"),
        ("C++ Developer (Intern)", "Software engineering"),
        ("Tradeweb International Intern Program - JavaScript UI Developer", "Software engineering"),
        ("Full-Stack Intern", "Software engineering"),
        ("Junior Front End Developer Intern", "Software engineering"),
        ("Frontend Engineer Intern - Ads Interface", "Software engineering"),
        ("Back-End Engineer Intern", "Software engineering"),
        ("SW Engineer Intern, iOS", "Software engineering"),
        ("Private Wealth Management Intern", "Wealth management"),
        ("Summer Analyst Intern", "Unclear division"),
    ],
)
def test_title_tracks(titles, title, track):
    j = job(title)
    titles(j)
    assert j.track == track


@pytest.mark.parametrize(
    "title, reason",
    [
        ("Investment Banking Summer Analyst", "excluded"),
        ("M&A Intern", "excluded"),
        ("Quantitative Research Intern – Master’s", "excluded"),  # curly apostrophe
        ("Quant Researcher Intern - PhD", "excluded"),
        ("Quantitative Trader", "not an internship"),
        ("Hardware Intern", "no matching track"),
        ("Technology Summer Analyst", "no matching track"),
        ("Quant Trading Analyst Intern - IAP Winternship (MIT Students Only)", "excluded"),
    ],
)
def test_title_rejections(titles, title, reason):
    with pytest.raises(Rejected, match=reason):
        titles(job(title))


def test_source_marked_internship_needs_no_intern_word(titles):
    j = job("Quantitative Researcher", is_internship=True)
    titles(j)
    assert j.track == "Quant / derivatives"


# ---------- Feature 3: graduation window and degree ----------


@pytest.fixture
def eligibility(cfg):
    return EligibilityFilter(cfg.profile)  # window Dec 2027 - Jun 2028


@pytest.mark.parametrize(
    "text",
    [
        "Open to students graduating between December 2027 and June 2028.",
        "Must be graduating in May 2028.",
        "Class of 2028 students only.",
        "For 2027 and 2028 graduates.",
        "No graduation date mentioned at all.",
    ],
)
def test_grad_window_passes(eligibility, text):
    eligibility(job(description=text))


@pytest.mark.parametrize(
    "text",
    [
        "Must graduate by August 2028.",  # a deadline you meet (real Akuna wording)
        "Graduating May 2028 or later.",
    ],
)
def test_grad_deadlines_and_floors_pass(eligibility, text):
    j = job(description=text)
    eligibility(j)
    assert j.flags == []


@pytest.mark.parametrize(
    "text, shown",
    [
        ("With an expected graduation by Summer 2027.", "by 08/2027"),  # real DV wording
        ("Candidates graduating in Spring 2027.", "05/2027"),
        ("Open to students graduating December 2028 through June 2029.", "12/2028-06/2029"),
    ],
)
def test_near_misses_are_flagged_not_dropped(eligibility, text, shown):
    j = job(description=text)
    eligibility(j)
    assert j.flags == [f"check grad date ({shown})"]


@pytest.mark.parametrize("text", ["Class of 2026.", "Must be graduating in 2030."])
def test_grad_window_rejects(eligibility, text):
    with pytest.raises(Rejected, match="graduation window"):
        eligibility(job(description=text))


def test_degree_level(eligibility):
    eligibility(job(degrees=["Bachelor's", "Master's"]))
    with pytest.raises(Rejected, match="degree"):
        eligibility(job(degrees=["Master's", "PhD"]))


# ---------- Feature 4: seasons ----------


@pytest.mark.parametrize(
    "title, terms, expected",
    [
        ("Trading Intern - Summer 2027", [], "Summer"),
        ("2027 Summer Internship - Investment Services", [], "Summer"),
        ("Trading Intern", ["Fall 2027"], "Fall"),
        ("Quant Trader - Micro-Internship (January Start)", [], "Spring"),
        ("Off-Cycle Trading Intern", [], "Fall"),
        ("Trading Intern", [], "Unknown"),
    ],
)
def test_season_detected(season, title, terms, expected):
    j = job(title, terms=terms)
    season(j)
    assert j.season == expected
    assert ("check season" in j.flags) == (expected == "Unknown")


def test_part_time_from_description(season):
    j = job("Trading Intern", description="Work 15-20 hours per week during the semester.")
    season(j)
    assert j.season == "Part-time"


@pytest.mark.parametrize(
    "title, terms, reason",
    [
        ("Trading Intern", ["Summer 2026"], "already started or over"),
        ("Trading Intern", ["Fall 2026"], "already started or over"),
        ("2028 Academy Summer Internship", [], "after graduation"),
        ("Data Science Co-op", ["Winter 2026"], "already started or over"),  # Jan-Apr 2026
        ("Data Science Co-op", ["Winter 2028"], "after graduation"),
    ],
)
def test_season_rejects_past_and_post_graduation_terms(season, title, terms, reason):
    with pytest.raises(Rejected, match=reason):
        season(job(title, terms=terms))


def test_december_start_counts_as_next_winter(season):
    j = job("Quant Credit Intern - December 2026")
    season(j)
    assert (j.season, j.terms) == ("Winter", ["Winter 2027"])


def test_keeps_valid_term_among_old_ones(season):
    j = job("Trading Intern", terms=["Summer 2026", "Summer 2027"])
    season(j)
    assert j.terms == ["Summer 2027"]


def test_list_term_older_than_the_posting_is_ignored(season):
    # SimplifyJobs labeled Citadel's July 2026 evergreen intern post "Summer 2026".
    j = job("Quantitative Trader - Intern (US)", terms=["Summer 2026"], date_posted="2026-07-06")
    season(j)
    assert (j.season, j.terms) == ("Summer", []) and "check term" in j.flags


def test_list_term_newer_than_the_posting_still_counts(season):
    with pytest.raises(Rejected, match="already started or over"):
        season(job("Trading Intern", terms=["Summer 2026"], date_posted="2025-08-01"))


# ---------- Feature 4: location (the five cases from the action plan, plus more) ----------


def located(titles, season, title, places, **kw):
    j = job(title, locations=places, **kw)
    titles(j)
    season(j)
    return j


def test_nyc_summer_passes(titles, season, location):
    location(located(titles, season, "Trading Intern - Summer 2027", ["New York, NY"]))


def test_nyc_fall_fails(titles, season, location):
    with pytest.raises(Rejected, match="outside 1-hour zone"):
        location(located(titles, season, "Trading Intern - Fall 2027", ["New York, NY"]))


def test_brickell_fall_passes(titles, season, location):
    j = located(titles, season, "Trading Intern - Fall 2027", ["Brickell, Miami, FL"])
    location(j)
    assert j.flags == []


def test_boca_fall_is_flagged(titles, season, location):
    j = located(titles, season, "Trading Intern - Fall 2027", ["Boca Raton, FL"])
    location(j)
    assert "check drive time" in j.flags


def test_remote_spring_passes(titles, season, location):
    j = located(titles, season, "Trading Intern - Spring 2027", ["Remote in USA"])
    location(j)
    assert "remote" in j.flags


def test_london_summer_fails(titles, season, location):
    with pytest.raises(Rejected, match="outside the US"):
        location(located(titles, season, "Trading Intern - Summer 2027", ["London, UK"]))


def test_remote_abroad_fails(titles, season, location):
    with pytest.raises(Rejected, match="outside the US"):
        location(located(titles, season, "Software Engineer Intern", ["Remote in UK"]))


def test_multi_city_with_one_us_office_passes(titles, season, location):
    location(
        located(titles, season, "Quant Trader Intern - Summer 2027", ["Amsterdam", "Chicago, IL"])
    )


def test_data_and_software_roles_go_anywhere_in_summer_but_stay_local_otherwise(
    titles, season, location
):
    # Amir, Nov 11: no location limit in Summer; Fall and Spring within 60 min of FIU.
    location(located(titles, season, "Data Analyst Intern - Summer 2027", ["Chicago, IL"]))
    location(located(titles, season, "Software Engineer Intern - Summer 2027", ["Seattle, WA"]))
    with pytest.raises(Rejected, match="Fall role"):
        location(located(titles, season, "Data Analyst Intern - Fall 2027", ["Chicago, IL"]))
    location(located(titles, season, "Data Analyst Intern - Fall 2027", ["Doral, FL"]))


@pytest.mark.parametrize(
    "title, reason",
    [
        ("2027 Summer Associate - Financial Restructuring", "graduate-only role"),
        ("Trading Operations Analyst Intern", "excluded title term"),
        ("Investment Operations Intern - Summer 2027", "excluded title term"),
        ("ETF Sales Internship: Summer 2027", "excluded title term"),
    ],
)
def test_mba_associate_ops_and_sales_roles_are_dropped(titles, title, reason):
    with pytest.raises(Rejected, match=reason):
        titles(job(title))


@pytest.mark.parametrize(
    "title",
    [
        "Sales and Trading Summer Analyst",
        "Sales & Trading Intern",
        "Quantitative Operations Research Intern",
    ],
)
def test_sales_and_trading_and_operations_research_stay(titles, title):
    titles(job(title))


def test_unknown_place_is_flagged_not_dropped(titles, season, location):
    j = located(titles, season, "Trading Intern - Summer 2027", ["Somewhereville"])
    location(j)
    assert "check location" in j.flags


# ---------- Dedup ----------


def test_dedup_merges_across_sources_and_keeps_best_source():
    a = job(
        "Quantitative Trading Intern - Summer 2027 (DV Equities)",
        company="DV Trading",
        locations=["New York"],
        source="simplify",
    )
    b = job(
        "Quantitative Trading Intern - DV Equities",
        company="DV Trading LLC",
        locations=["New York, NY"],
        source="greenhouse",
        description="Full text",
    )
    (merged,) = dedupe([a, b])
    assert merged.source == "greenhouse"
    assert merged.sources == ["greenhouse", "simplify"]
    assert merged.locations == ["New York, NY", "New York"]


def test_dedup_never_merges_full_time_with_intern():
    full_time = job("Quantitative Researcher", company="Point72", source="greenhouse")
    intern = job(
        "Quantitative Researcher - Intern", company="Point72", source="kadoa", is_internship=True
    )
    assert len(dedupe([full_time, intern])) == 2


def test_dedup_treats_internship_and_intern_alike():
    a = job("Quant Trading Internship - Summer 2027", company="X", source="greenhouse")
    b = job("Quant Trading Intern", company="X", source="simplify")
    assert len(dedupe([a, b])) == 1


def test_dedup_keeps_different_roles():
    jobs = [
        job("Trading Intern", company="A"),
        job("Research Intern", company="A"),
        job("Trading Intern", company="B"),
    ]
    assert len(dedupe(jobs)) == 3


@pytest.mark.parametrize(
    "title,track,priority,rule",
    [
        ("Quantitative Developer Internship", "Software engineering", 3, "season"),
        ("Quant Developer / Quant Research Intern", "Quant / derivatives", 1, "season"),
        (
            "2027 Summer Internship Program – Systematic Trading Technology Software Engineer, New York",
            "Software engineering",
            3,
            "season",
        ),
        ("Quant Trader Intern", "Trading", 1, "season"),
        ("Quant Systems Engineer Intern", "Software engineering", 3, "season"),
        ("Quant Developer Researcher Intern", "Quant / derivatives", 1, "season"),
        ("Quant Developer Trader Intern", "Trading", 1, "season"),
        ("Quant Developers Intern", "Quant / derivatives", 1, "season"),
        ("QUANT DEVELOPER INTERN", "Software engineering", 3, "season"),
    ],
)
def test_title_reassignment(titles, title, track, priority, rule):
    posting = job(title)
    titles(posting)
    assert (posting.track, posting.priority, titles.location_rule(posting)) == (
        track,
        priority,
        rule,
    )


def test_reassignment_does_not_admit_unmatched_titles(titles):
    with pytest.raises(Rejected, match="no matching track"):
        titles(job("Systems Engineer Intern"))


def test_reassignment_requires_known_target(cfg):
    from jobengine.filters.titles import TitleFilter

    config = dict(cfg.titles, reassign=[{"to": "Missing", "when_any": ["developer"]}])
    with pytest.raises(ValueError, match="unknown reassignment track: Missing"):
        TitleFilter(config)


@pytest.mark.parametrize(
    "title,url",
    [
        ("Quant Graduate Intern", "https://example.com/role"),
        (
            "Quantitative Risk Management Intern",
            "https://freddiemac.wd5.myworkdayjobs.com/External/job/McLean-VA/"
            "Risk-Management-Graduate-Intern---Quantitative-Summer-2027_JR17553",
        ),
        ("Quant Intern", "https://example.com/GRADUATE_INTERN"),
    ],
)
def test_graduate_only_rejection(titles, title, url):
    with pytest.raises(Rejected, match="(?i)graduate-only role: graduate intern"):
        titles(job(title, url=url))


@pytest.mark.parametrize("phrase", ["Undergraduate Intern", "Undergraduate-Intern"])
def test_undergraduate_is_not_graduate_only(titles, phrase):
    titles(job(f"Quant {phrase}", url=f"https://example.com/{phrase}"))


def test_exact_url_dedup_merges_all_metadata():
    url = "https://example.com/job?id=1"
    preferred = job("Quant Intern", source="greenhouse", url=url, locations=["Miami"])
    other = job(
        "Trading Internship",
        company="Other",
        source="simplify",
        url=url,
        locations=["Miami", "New York"],
        description="Details",
        date_posted="2026-11-01",
        salary="$50",
        terms=["Summer 2027"],
        degrees=["Bachelor's"],
        is_internship=True,
    )
    assert dedupe([other, preferred]) == [preferred]
    assert preferred.sources == ["greenhouse", "simplify"]
    assert preferred.locations == ["Miami", "New York"]
    for attr in ("description", "date_posted", "salary", "terms", "degrees", "is_internship"):
        assert getattr(preferred, attr) == getattr(other, attr)


def test_query_strings_are_distinct_urls():
    a = job(
        "Quant Research Intern",
        company="Point72",
        url="https://careers.point72.com/CSJobDetail?jobName=a&jobCode=1",
    )
    b = job(
        "Quant Trading Intern",
        company="Point72",
        url="https://careers.point72.com/CSJobDetail?jobName=b&jobCode=2",
    )
    assert dedupe([a, b]) == [a, b]


def test_empty_urls_do_not_merge_different_keys():
    assert len(dedupe([job("Trading Intern", url=""), job("Quant Intern", url="")])) == 2


def test_dedup_never_chains_two_jobs_together():
    a = job("Trading Intern", source="greenhouse", url="https://example.com/a")
    b = job("Quant Intern", source="lever", url="https://example.com/b")
    # Same title as a, same URL as b: the exact URL wins, and a and b stay separate.
    bridge = job("Trading Intern", source="simplify", url=b.url)
    assert dedupe([a, b, bridge]) == [a, b]
    assert a.sources == ["greenhouse"]
    assert b.sources == ["lever", "simplify"]


def test_dedup_keeps_same_titled_roles_in_two_cities_apart():
    # Real Brevan Howard case: Simplify titles both roles "Trading Intern", Kadoa names the
    # city. Each Simplify row must join its own Kadoa row by URL, not merge across cities.
    ny = "https://wd3.myworkdaysite.com/recruiting/brevanhoward/x/job/New-York/Trading_JR101583"
    ldn = "https://wd3.myworkdaysite.com/recruiting/brevanhoward/x/job/London/Trading_JR101584"
    rows = [
        job("Trading Intern - Trading", "Brevan Howard", ["NYC"], "simplify", url=ny),
        job("Trading Intern", "Brevan Howard", ["London, UK"], "simplify", url=ldn),
        job(
            "2027 Summer Internship Program – Trading, London",
            "Brevan Howard",
            ["London"],
            "kadoa",
            url=ldn,
        ),
        job(
            "2027 Summer Internship Program – Trading, New York",
            "Brevan Howard",
            ["New York"],
            "kadoa",
            url=ny,
        ),
    ]
    merged = dedupe(rows)
    assert sorted(j.url for j in merged) == sorted([ny, ldn])
    by_url = {j.url: j for j in merged}
    assert by_url[ny].locations == ["New York", "NYC"]
    assert by_url[ldn].locations == ["London", "London, UK"]
    assert by_url[ny].sources == by_url[ldn].sources == ["kadoa", "simplify"]


def test_job_helper_urls_are_unique_and_overridable():
    assert job().url != job().url
    assert job(url="https://example.com/custom").url == "https://example.com/custom"


# ---------- Season "Unknown" (Nov 12): terms in URLs and deeper in descriptions ----------


def test_term_in_the_url_counts(season):
    # Simplify listed this with no term and the company name as its title.
    url = "https://fintools.applytojob.com/apply/o7r6gV8IMh/Computer-Programming-Internship-Summer-2026"
    with pytest.raises(Rejected, match="already started or over: Summer 2026"):
        season(job("Montgomery Investment Technology", url=url))


def test_url_query_string_is_not_read_for_terms(season):
    j = job("Trading Intern", url="https://example.com/job/1?campaign=summer-2026")
    season(j)
    assert j.season == "Unknown"


@pytest.mark.parametrize(
    "description, expected",
    [
        # Optiver: "Institutional Trader Intern (2027 Start - Chicago)"
        (
            "As an Institutional Trader Intern at Optiver, you'll spend your summer embedded in "
            "the fast-paced environment of our trading floor.",
            "Summer",
        ),
        # Virtu: the dates come after 1,400 characters of firm boilerplate
        (
            "x " * 900 + "Location: Singapore Dates: 10 week program, starting June 7th 2027- "
            "August 13th 2027.",
            "Summer",
        ),
        ("x " * 900 + "Our Fall Internship Program runs September to December.", "Fall"),
        ("x " * 900 + "Interns join us for Spring 2027 in our Miami office.", "Spring"),
        ("Interns work on real trading problems with mentors.", "Unknown"),
    ],
)
def test_season_from_the_whole_description(season, description, expected):
    j = job("Quantitative Trading Intern", description=description)
    season(j)
    assert j.season == expected
