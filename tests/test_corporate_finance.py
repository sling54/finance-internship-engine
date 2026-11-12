"""Amir, Nov 12: corporate finance / FP&A roles don't lead to quant trading."""

import pytest
from helpers import job

from jobengine.models import Rejected

# ---------- Amir, Nov 12: no corporate finance / FP&A roles ----------


@pytest.mark.parametrize(
    "title",
    [
        "Finance Intern",
        "Finance Intern - Data Analysis",
        "Financial Analyst Intern, Hardware FP&A - Summer 2027",
        "Finance Intern - Undergrad Summer 2027",
        "Corporate Development - Finance Summer Analyst - NAELFY27",
        "Corporate Finance Summer Analyst",
        "Financial Planning & Analysis Intern",
    ],
)
def test_corporate_finance_roles_are_dropped(titles, title):
    with pytest.raises(Rejected):
        titles(job(title))


@pytest.mark.parametrize(
    "title, track",
    [
        ("Summer Analyst", "Unclear division"),
        ("Quantitative Finance Intern", "Quant / derivatives"),
        ("Investment Intern", "Buy-side investing"),
    ],
)
def test_finance_roles_that_can_lead_to_trading_stay(titles, title, track):
    j = job(title)
    titles(j)
    assert j.track == track
