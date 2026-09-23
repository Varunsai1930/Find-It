"""A check that cannot run must fail the gate, never pass in silence."""

import sqlite3

import pandas as pd
import pytest

import consensus_signals
from findit.store import validation_gate
from findit.store.validation_gate import validate_holdings_month


def _df(pct=99.0, qty=100.0, isin="INE002A01018"):
    return pd.DataFrame([{"isin": isin, "quantity": qty,
                          "market_value_lakhs": 500.0, "pct_nav": pct}])


def test_clean_month_still_passes():
    result = validate_holdings_month(_df(), _df(qty=120.0))
    assert result["passed"] is True
    assert not [i for i in result["issues"] if i["severity"] == "error"]


def test_real_quarantine_still_fails():
    result = validate_holdings_month(_df(pct=126.5))
    assert result["passed"] is False
    assert any(i["code"] == "nav_sum_quarantine" for i in result["issues"])


@pytest.mark.parametrize("target, code", [
    ("validate_nav_sum", "nav_sum_crashed"),
    ("validate_implied_price_cv", "implied_price_cv_crashed"),
    ("validate_qty_ratio", "qty_ratio_crashed"),
])
def test_a_crashing_check_quarantines_and_names_itself(monkeypatch, target, code):
    def boom(*args, **kwargs):
        raise RuntimeError("check exploded")

    monkeypatch.setattr(validation_gate, target, boom)
    current = pd.concat([_df(), _df(isin="INE002A01018", qty=90.0)], ignore_index=True)
    result = validate_holdings_month(current, _df(qty=120.0))
    assert result["passed"] is False, "a check that did not run cannot count as validated"
    crashed = [i for i in result["issues"] if i["code"] == code]
    assert crashed and crashed[0]["severity"] == "error"
    assert "RuntimeError" in crashed[0]["message"]


def test_nav_failure_is_not_overwritten_by_a_later_passing_check():
    """`passed` used to be assigned, not accumulated, inside one try block."""
    result = validate_holdings_month(_df(pct=126.5), _df(qty=120.0))
    assert result["passed"] is False


def test_quarantine_filter_does_not_fail_open_on_a_broken_table():
    """Returning [] on error would publish quarantined schemes as passed."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE scheme_month_status (scheme_id INTEGER, "
                 "report_month TEXT, status TEXT)")
    conn.execute("INSERT INTO scheme_month_status VALUES (1, '2026-08', 'quarantined')")
    assert consensus_signals._quarantined_scheme_ids(conn, "2026-08") == [1]
    conn.execute("DROP TABLE scheme_month_status")
    # Absent table is the one case that legitimately means "nothing quarantined".
    assert consensus_signals._quarantined_scheme_ids(conn, "2026-08") == []
    conn.close()


def test_quarantined_scheme_is_excluded_from_consensus(tmp_path):
    import db
    conn = db.get_connection(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO schemes (amc_name, scheme_name, scheme_key, is_active_equity) "
                 "VALUES ('A', 'F', 'f', 1)")
    conn.execute("INSERT INTO stocks VALUES ('INE002A01018', 'R', 'I', 'equity')")
    conn.execute("INSERT INTO mf_holding_deltas (scheme_id, isin, report_month, prev_month, "
                 "qty_change, value_change_lakhs, flow_lakhs, price_effect_lakhs, "
                 "pct_nav_change, action) VALUES (1, 'INE002A01018', '2026-08', '2026-07', "
                 "1, 10, 10, 0, 0.1, 'added')")
    conn.commit()
    assert len(consensus_signals.compute_consensus(conn, "2026-08")) == 1
    conn.execute("INSERT INTO scheme_month_status (scheme_id, report_month, status) "
                 "VALUES (1, '2026-08', 'quarantined')")
    conn.commit()
    assert consensus_signals.compute_consensus(conn, "2026-08").empty
    conn.close()
