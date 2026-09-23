"""Publication dates: when a disclosure could first be acted on."""

from datetime import date

import pytest

from findit.core import publication


def test_mf_portfolio_is_public_ten_days_after_month_end():
    assert publication.mf_disclosure_deadline("2026-08") == date(2026, 9, 10)
    assert publication.mf_disclosure_deadline("2026-02") == date(2026, 3, 10)


def test_mf_signal_is_tradable_the_day_after_publication():
    assert publication.mf_signal_entry_date("2026-08") == date(2026, 9, 11)


def test_observed_shareholding_date_wins_over_the_deadline():
    day, basis = publication.shareholding_published("2019-03-31", "2019-05-15T18:00")
    assert (day, basis) == (date(2019, 5, 15), publication.BASIS_OBSERVED)


@pytest.mark.parametrize("missing", [None, "", "nan", float("nan")])
def test_unknown_shareholding_date_falls_back_to_the_deadline(missing):
    value = None if isinstance(missing, float) else missing
    day, basis = publication.shareholding_published("2026-06-30", value)
    assert (day, basis) == (date(2026, 7, 21), publication.BASIS_DEADLINE)


def test_bad_month_is_rejected():
    with pytest.raises(ValueError):
        publication.mf_disclosure_deadline("2026-13")
