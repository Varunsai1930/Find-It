"""Monthly report: built from the shared functions, honest about the evidence."""

import pytest

from findit.cli import report

from findit.research import backtest_quarterly
from tests.test_web import _copy_db


def test_report_has_every_section_on_a_small_db(tmp_path):
    text = report.build(str(_copy_db(tmp_path)), "2026-08")
    for heading in ("# Mutual fund activity report — 2026-08", "## Coverage",
                    "## Broadest buying", "## Broadest selling",
                    "## Evidence: does this predict returns?",
                    "## Track record of this monthly signal"):
        assert heading in text
    assert "| Reliance Industries |" in text
    assert "No quarterly history is stored yet" in text  # the fixture has no MF % history
    assert "HDFC NCD" not in text  # debt never ranked


def test_a_month_without_changes_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no holding changes stored for 2026-01"):
        report.build(str(_copy_db(tmp_path)), "2026-01")


def _quarter(excess_by_group):
    groups = {name: {"n": 5, "excess_mean": e, "n_size": 5, "excess_size": e}
              for name, e in excess_by_group.items()}
    return {"signal_quarter": "q", "price_dates": ("a", "b"), "partial_period": False,
            "groups": groups}


@pytest.mark.parametrize("excess, expected", [
    (0.02, "MF ownership up, FII not beats comparable stocks"),
    (-0.02, "MF ownership up, FII not lags comparable stocks"),
])
def test_evidence_states_the_direction_of_a_finding(monkeypatch, excess, expected):
    quarters = [_quarter({"mf_up_only": excess + wobble})
                for wobble in (-0.002, 0.0, 0.002, 0.001)]
    monkeypatch.setattr(backtest_quarterly, "run", lambda db_path: {"results": quarters})
    assert expected in "\n".join(report.evidence("unused.db"))


def test_evidence_without_a_finding_says_so(monkeypatch):
    quarters = [_quarter({"mf_up_only": e}) for e in (0.02, -0.02, 0.01, -0.01)]
    monkeypatch.setattr(backtest_quarterly, "run", lambda db_path: {"results": quarters})
    assert "No group beats comparable stocks reliably" in "\n".join(report.evidence("x.db"))


def test_coverage_states_the_dashboards_counts(tmp_path):
    """Report and dashboard read one coverage function, so their numbers match."""
    import sqlite3

    from tests.test_web import _coverage_db

    db = _coverage_db(tmp_path)
    with sqlite3.connect(db) as conn:
        text = "\n".join(report.coverage(conn, "2026-08"))
    # The same counts test_web asserts on the dashboard for this database.
    assert "**2** active equity schemes from **1** AMC compared 2026-07 → 2026-08" in text
    assert "1 loaded without a 2026-07 portfolio to compare" in text
    assert "Validation gate: 2 passed, 1 withheld, 1 not validated." in text
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM mf_holdings_monthly "
                     "WHERE scheme_id = 2 AND report_month = '2026-08'")
        text = "\n".join(report.coverage(conn, "2026-08"))
    # A fund missing this month is named as missing, never read as a seller.
    assert "1 held in 2026-07 but not loaded for 2026-08" in text
