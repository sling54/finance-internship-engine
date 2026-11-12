"""Precision audit (Nov 12): kept roles that didn't fit."""

import pytest
from helpers import job

from jobengine.filters.titles import TitleFilter
from jobengine.models import Rejected

# ---------- Precision audit (Nov 12): roles that passed but don't fit ----------


@pytest.mark.parametrize(
    "title",
    [
        "Quantitative Research Intern, Summer 2027 | Columbia University",
        "Quant Development & Strategy Intern, Summer 2027 | Massachusetts Institute of Technology",
        "Quantitative Development & Strategy Intern, Summer 2027 | Harvard",
    ],
)
def test_program_for_another_school_is_dropped(titles, title):
    with pytest.raises(Rejected, match="program for one school"):
        titles(job(title))


def test_program_for_my_own_school_is_kept(cfg):
    titles = TitleFilter({**cfg.titles, "my_school": "Florida International University"})
    j = job("Quantitative Research Intern, Summer 2027 | Florida International University")
    titles(j)
    assert j.track == "Quant / derivatives"


def test_pipe_without_a_school_is_kept(titles):
    j = job("Quantitative Research Intern | Summer 2027")
    titles(j)
    assert j.track == "Quant / derivatives"


FULL_TIME_TEXT = (
    "Jane Street is a quantitative trading firm. Our traders work on a wide range of products "
    "and markets, build models, and collaborate with developers and researchers every day. "
) * 3


def test_list_internship_whose_firm_posting_never_mentions_interns_is_dropped(titles):
    j = job("Quantitative Trader", is_internship=True, description=FULL_TIME_TEXT)
    with pytest.raises(Rejected, match="never mentions interns"):
        titles(j)


def test_list_internship_whose_firm_posting_mentions_interns_is_kept(titles):
    text = FULL_TIME_TEXT + "As a Sales and Trading intern, you'll work alongside our team."
    j = job("Quantitative Trader", is_internship=True, description=text)
    titles(j)
    assert j.track == "Trading"


def test_list_internship_with_short_or_no_description_is_kept(titles):
    titles(job("Quantitative Trader", is_internship=True, description="Trade markets."))
    titles(job("Quantitative Trader", is_internship=True))
