"""Monthly report: built from the shared functions, honest about the evidence."""

import pytest

from findit.cli import backtest_quarterly, report
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
