"""Rule summaries and the data-safety checks kept from the narration layer."""

import sqlite3

import pytest

import db
import fallback_summary
from findit.summary import RULES_VERSION, get_summary


def _db(tmp_path, status="ok", action="added", qty=10.0, value=100.0,
        itype="equity", prev_status=None):
    conn = db.get_connection(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO schemes (amc_name, scheme_name, scheme_key, is_active_equity) "
                 "VALUES ('HDFC AMC', 'Top 100', 'top 100', 1)")
    conn.execute("INSERT INTO stocks (isin, name, industry, instrument_type) "
                 "VALUES ('INE002A01018', 'Reliance Industries Ltd.', 'Refineries', ?)",
                 (itype,))
    for month, qty_, val in (("2026-07", 100.0, 1000.0), ("2026-08", 110.0, 1100.0)):
        conn.execute("INSERT INTO mf_holdings_monthly (scheme_id, isin, report_month, "
                     "quantity, market_value_lakhs, pct_nav) VALUES (1, 'INE002A01018', "
                     "?, ?, ?, 99.0)", (month, qty_, val))
    conn.execute("INSERT INTO mf_holding_deltas (scheme_id, isin, report_month, prev_month, "
                 "qty_change, value_change_lakhs, flow_lakhs, price_effect_lakhs, "
                 "pct_nav_change, action) VALUES (1, 'INE002A01018', '2026-08', '2026-07', "
                 "?, ?, ?, 0, 0.1, ?)", (qty, value, value, action))
    if status:
        conn.execute("INSERT INTO scheme_month_status (scheme_id, report_month, status) "
                     "VALUES (1, '2026-08', ?)", (status,))
    if prev_status:
        conn.execute("INSERT INTO scheme_month_status (scheme_id, report_month, status) "
                     "VALUES (1, '2026-07', ?)", (prev_status,))
    conn.commit()
    return conn


def test_validated_month_returns_the_rule_summary(tmp_path):
    conn = _db(tmp_path)
    result = get_summary(conn, 1, "2026-08")
    assert result["eligible"] is True and result["reason"] is None
    assert result["generated_by"] == "rules"
    assert result["model_version"] == RULES_VERSION
    assert result["text"] == fallback_summary.build_summary(conn, 1, "2026-08")
    conn.close()


def test_quarantined_month_is_withheld_not_summarised(tmp_path):
    conn = _db(tmp_path, status="quarantined")
    result = get_summary(conn, 1, "2026-08")
    assert result["reason"] == "quarantined"
    assert "withheld" in result["text"]
    assert "Not zero activity" in result["text"], "silence must not read as no activity"
    assert "Added to" not in result["text"]
    conn.close()


def test_a_quarantined_previous_month_also_withholds(tmp_path):
    """A delta is a comparison; bad data on either side makes it unsafe."""
    conn = _db(tmp_path, status="ok", prev_status="quarantined")
    result = get_summary(conn, 1, "2026-08")
    assert result["reason"] == "quarantined"
    assert "withheld" in result["text"]
    conn.close()


def test_unvalidated_month_is_flagged_but_still_described(tmp_path):
    conn = _db(tmp_path, status=None)
    result = get_summary(conn, 1, "2026-08")
    assert result["reason"] == "unvalidated"
    assert result["eligible"] is False
    conn.close()


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_numbers_are_withheld(tmp_path, bad):
    conn = _db(tmp_path)
    conn.execute("UPDATE mf_holding_deltas SET value_change_lakhs = ?", (bad,))
    conn.commit()
    result = get_summary(conn, 1, "2026-08")
    assert result["reason"] == "nonfinite_or_invalid_data"
    assert "withheld" in result["text"]
    conn.close()


def test_an_unrecognised_action_is_withheld(tmp_path):
    conn = _db(tmp_path, action="teleported")
    result = get_summary(conn, 1, "2026-08")
    assert result["reason"] == "nonfinite_or_invalid_data"
    conn.close()


def test_no_equity_deltas_reads_as_no_data(tmp_path):
    conn = _db(tmp_path, itype="ncd")
    assert get_summary(conn, 1, "2026-08")["reason"] == "no_data"
    conn.close()


def test_unclassified_instrument_is_flagged(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE stocks SET instrument_type = NULL")
    conn.commit()
    assert get_summary(conn, 1, "2026-08")["reason"] == "unknown_instrument_type"
    conn.close()


def test_unknown_scheme_raises(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="No scheme with scheme_id"):
        get_summary(conn, 99999, "2026-08")
    conn.close()


def test_missing_tables_do_not_crash():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schemes (scheme_id INTEGER PRIMARY KEY, amc_name TEXT, "
                 "scheme_name TEXT)")
    conn.execute("INSERT INTO schemes VALUES (1, 'A', 'F')")
    conn.commit()
    result = get_summary(conn, 1, "2026-08")
    assert "No holding-change data available" in result["text"]
    # Nothing has been validated in this DB, and that is reported as such
    # rather than as an absence of activity.
    assert result["reason"] == "unvalidated"
    conn.close()


def test_summary_never_writes(tmp_path):
    conn = _db(tmp_path)
    before = [r for r in conn.execute("SELECT * FROM mf_holding_deltas")]
    get_summary(conn, 1, "2026-08")
    assert [r for r in conn.execute("SELECT * FROM mf_holding_deltas")] == before
    tables_after = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "fund_summaries" not in tables_after, "the summary cache table is gone"
    conn.close()
