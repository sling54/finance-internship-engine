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
        "Candidates graduating in Spring 2027.",
        "Open to students graduating December 2028 through June 2029.",
        "Class of 2026.",
    ],
)
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
    ],
)
def test_season_rejects_past_and_post_graduation_terms(season, title, terms, reason):
    with pytest.raises(Rejected, match=reason):
        season(job(title, terms=terms))


def test_keeps_valid_term_among_old_ones(season):
    j = job("Trading Intern", terms=["Summer 2026", "Summer 2027"])
    season(j)
    assert j.terms == ["Summer 2027"]


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
    with pytest.raises(Rejected, match="outside 1-hour zone"):
        location(located(titles, season, "Software Engineer Intern", ["Remote in UK"]))


def test_multi_city_with_one_us_office_passes(titles, season, location):
    location(
        located(titles, season, "Quant Trader Intern - Summer 2027", ["Amsterdam", "Chicago, IL"])
    )


def test_data_roles_stay_miami_in_summer(titles, season, location):
    with pytest.raises(Rejected, match="Miami-area track"):
        location(located(titles, season, "Data Analyst Intern - Summer 2027", ["Chicago, IL"]))
    location(located(titles, season, "Data Analyst Intern - Summer 2027", ["Doral, FL"]))


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


def test_dedup_keeps_different_roles():
    jobs = [
        job("Trading Intern", company="A"),
        job("Research Intern", company="A"),
        job("Trading Intern", company="B"),
    ]
    assert len(dedupe(jobs)) == 3
