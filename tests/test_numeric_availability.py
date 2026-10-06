"""Unknown cells are not absent positions; calculations never invent zeros."""
import pandas as pd
import pytest

from findit.core import active_weight, consensus_signals, delta_calculator
from findit.core.data_quality import bad_domestic_quantity_pairs
from findit.pipeline import run_validation_gate
from findit.store import db
from findit.store.validation_gate import validate_holdings_month
from findit.summary import get_summary

ISIN = "INE002A01018"
OTHER = "INE040A01034"


def holdings(quantity=100, value=10, nav=100):
    return pd.DataFrame([(1, ISIN, quantity, value, nav)], columns=[
        "scheme_id", "isin", "quantity", "market_value_lakhs", "pct_nav"])


@pytest.mark.parametrize("unknown", [None, float("nan"), float("inf"), "unreadable"])
def test_present_unknown_quantity_is_not_an_exit(unknown):
    row = delta_calculator.diff_holdings(holdings(), holdings(unknown), "2026-07", "2026-08").iloc[0]
    assert pd.isna(row.qty_change) and pd.isna(row.flow_lakhs)
    assert row.action == "unavailable"


@pytest.mark.parametrize("unknown", [None, -1, float("inf")])
def test_unknown_previous_quantity_is_not_a_new_purchase(unknown):
    row = delta_calculator.diff_holdings(holdings(unknown), holdings(), "2026-07", "2026-08").iloc[0]
    assert pd.isna(row.qty_change) and pd.isna(row.flow_lakhs)
    assert row.action == "unavailable"


def test_absent_positions_remain_real_zero_and_unknown_nav_stays_unknown():
    prev = pd.concat([holdings(), holdings(20, 2, 10).assign(isin=OTHER)])
    curr = holdings(nav=None)
    rows = delta_calculator.diff_holdings(prev, curr, "2026-07", "2026-08").set_index("isin")
    assert pd.isna(rows.loc[ISIN, "pct_nav_change"])
    assert rows.loc[ISIN, "qty_change"] == 0
    assert rows.loc[OTHER, "qty_change"] == -20
    assert rows.loc[OTHER, "flow_lakhs"] == -2


def test_unreadable_value_keeps_known_shares_but_withholds_estimate():
    row = delta_calculator.diff_holdings(holdings(), holdings(110, None), "2026-07", "2026-08").iloc[0]
    assert row.qty_change == 10 and row.action == "added"
    assert pd.isna(row.value_change_lakhs) and pd.isna(row.flow_lakhs)
    assert pd.isna(row.price_effect_lakhs)


def test_previous_value_gap_withholds_residual_but_not_current_priced_flow():
    row = delta_calculator.diff_holdings(holdings(value=None), holdings(110, 11), "2026-07", "2026-08").iloc[0]
    assert row.qty_change == 10 and row.flow_lakhs == pytest.approx(1)
    assert pd.isna(row.value_change_lakhs) and pd.isna(row.price_effect_lakhs)


def test_gate_quarantines_domestic_quantity_but_not_foreign_or_unknown_nav():
    current = holdings().drop(columns="scheme_id")
    assert not validate_holdings_month(current.assign(quantity=None))["passed"]
    assert validate_holdings_month(current.assign(pct_nav=None))["passed"]
    foreign = current.assign(isin="US0378331005", quantity=None, pct_nav=0)
    assert validate_holdings_month(pd.concat([current, foreign]))["passed"]


def seeded(tmp_path):
    conn = db.get_connection(tmp_path / "numeric.db")
    conn.execute("INSERT INTO schemes(scheme_id,amc_name,scheme_name,is_active_equity) VALUES(1,'Test AMC','Equity Fund',1)")
    conn.execute("INSERT INTO stocks(isin,name,instrument_type) VALUES(?,'Reliance','equity')", (ISIN,))
    for month, qty in [("2026-07", 100), ("2026-08", 110)]:
        conn.execute("INSERT INTO mf_holdings_monthly(scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) VALUES(1,?,?,?,10,100)", (ISIN, month, qty))
        conn.execute("INSERT INTO scheme_month_status(scheme_id,report_month,status) VALUES(1,?,'ok')", (month,))
    conn.commit()
    delta_calculator.persist_deltas(conn, delta_calculator.compute_deltas(conn, "2026-07", "2026-08"))
    return conn


def test_recomputed_comparison_withholds_unknown_value_and_nav(tmp_path):
    conn = seeded(tmp_path)
    conn.execute("UPDATE mf_holdings_monthly SET market_value_lakhs=NULL,pct_nav=NULL WHERE report_month='2026-08'")
    conn.commit()
    row = consensus_signals.comparison_rows(conn, "2026-08").iloc[0]
    assert row.qty_change == 10
    assert pd.isna(row.flow_lakhs) and pd.isna(row.pct_nav_curr)
    conn.close()


def test_historical_ok_status_cannot_make_unknown_quantity_vote(tmp_path):
    conn = seeded(tmp_path)
    conn.execute("UPDATE mf_holdings_monthly SET quantity=NULL WHERE report_month='2026-08'")
    conn.commit()
    assert consensus_signals.comparison_rows(conn, "2026-08").empty
    assert consensus_signals.voting_schemes(conn, "2026-08") == {}
    assert get_summary(conn, 1, "2026-08")["reason"] == "quarantined"
    conn.close()


def test_foreign_quantity_gap_keeps_domestic_comparison_eligible(tmp_path):
    conn = seeded(tmp_path)
    conn.execute("INSERT INTO stocks(isin,name,instrument_type) VALUES('US0378331005','Apple','foreign')")
    conn.execute("INSERT INTO mf_holdings_monthly(scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) VALUES(1,'US0378331005','2026-08',NULL,10,0)")
    conn.commit()
    assert bad_domestic_quantity_pairs(conn) == set()
    assert len(consensus_signals.comparison_rows(conn, "2026-08")) == 1
    assert consensus_signals.voting_schemes(conn, "2026-08") == {1: "Test AMC"}
    conn.close()


def test_nullable_legs_round_trip_and_do_not_withhold_known_share_summary(tmp_path):
    conn = seeded(tmp_path)
    conn.execute("UPDATE mf_holdings_monthly SET market_value_lakhs=NULL,pct_nav=NULL WHERE report_month='2026-08'")
    conn.commit()
    report = run_validation_gate(conn, {(1, "2026-08"): {"files": [], "hashes": []}})
    assert report["scheme_months"][0]["status"] == "ok"
    delta_calculator.persist_deltas(conn, delta_calculator.compute_deltas(conn, "2026-07", "2026-08"))
    assert conn.execute("SELECT value_change_lakhs,flow_lakhs,pct_nav_change FROM mf_holding_deltas").fetchone() == (None, None, None)
    result = get_summary(conn, 1, "2026-08")
    assert result["eligible"] and "10 shares" in result["text"]
    assert "nan" not in result["text"].lower()
    conn.close()


def test_unpriced_new_entry_summary_does_not_claim_largest_or_invent_nav(tmp_path):
    conn = seeded(tmp_path)
    conn.execute("INSERT INTO stocks(isin,name,instrument_type) VALUES(?,'Other Stock','equity')", (OTHER,))
    conn.execute("INSERT INTO mf_holdings_monthly(scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) VALUES(1,?,'2026-08',20,NULL,NULL)", (OTHER,))
    conn.commit()
    delta_calculator.persist_deltas(conn, delta_calculator.compute_deltas(conn, "2026-07", "2026-08"))
    result = get_summary(conn, 1, "2026-08")
    assert result["eligible"]
    assert "20 shares; value unavailable" in result["text"]
    assert "NAV weight unavailable" in result["text"]
    assert "largest new" not in result["text"]
    conn.close()


@pytest.mark.parametrize("unknown", [None, float("inf"), "unreadable"])
def test_partial_currency_totals_stay_unavailable(unknown):
    deltas = pd.DataFrame([
        {"isin": ISIN, "scheme_id": sid, "amc_name": str(sid), "action": "added",
         "flow_lakhs": value, "price_effect_lakhs": value, "stock_name": "Reliance",
         "industry": "Test", "instrument_type": "equity"}
        for sid, value in [(1, 5), (2, unknown)]])
    result = consensus_signals.rank_consensus(deltas, {ISIN}, pd.DataFrame(columns=["isin"] + consensus_signals.ACTIVE_COLUMNS)).iloc[0]
    assert pd.isna(result.total_flow_lakhs) and pd.isna(result.total_price_effect_lakhs)
    assert pd.isna(result.accumulation_flow_lakhs)
    assert result.net_amc_count == 2


def test_unknown_new_value_does_not_hide_known_existing_accumulation():
    deltas = pd.DataFrame([
        {"isin": ISIN, "scheme_id": sid, "amc_name": str(sid), "action": action,
         "flow_lakhs": value, "stock_name": "Reliance", "industry": "Test",
         "instrument_type": "equity"}
        for sid, action, value in [(1, "added", 5), (2, "new", None)]])
    result = consensus_signals.rank_consensus(deltas, {ISIN}, pd.DataFrame(columns=["isin"] + consensus_signals.ACTIVE_COLUMNS)).iloc[0]
    assert result.accumulation_flow_lakhs == 5
    assert pd.isna(result.total_flow_lakhs) and pd.isna(result.new_position_flow_lakhs)


def test_incomplete_sleeve_does_not_create_discretionary_tilts():
    prev = pd.concat([holdings(), holdings(100, 10).assign(isin=OTHER)]).drop(columns="pct_nav")
    curr = prev.copy()
    curr.loc[curr["isin"] == OTHER, "market_value_lakhs"] = None
    changes = active_weight.active_weight_changes(prev, curr)
    assert set(changes["isin"]) == {ISIN, OTHER}
    assert changes.active_weight_change_pp.isna().all()
    totals = active_weight.aggregate_by_stock(changes, {1: "Test AMC"})
    assert totals.discretionary_flow_lakhs.isna().all()
    assert totals.net_active_amc_count.isna().all()


def test_nonfinite_discretionary_contributor_cannot_vote_or_partially_sum():
    changes = pd.DataFrame([
        {"scheme_id": sid, "isin": ISIN, "active_weight_change_pp": weight,
         "discretionary_flow_lakhs": value}
        for sid, weight, value in [(1, 1, 5), (2, float("inf"), float("inf"))]])
    result = active_weight.aggregate_by_stock(changes, {1: "One", 2: "Two"}).iloc[0]
    assert pd.isna(result.discretionary_flow_lakhs)
    assert pd.isna(result.net_active_amc_count)
