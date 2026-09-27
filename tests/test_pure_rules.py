"""The calculations run on plain DataFrames: no database, no files.

Each database-facing function (compute_deltas, compute_consensus,
join_shareholding_increase, build_summary) reads rows and hands them to one
of these, so a surprising number can be reproduced from the rows alone.
"""
from datetime import date

import pandas as pd

from findit.core.consensus_signals import ACTIVE_COLUMNS, add_shareholding_signal, rank_consensus
from findit.core.delta_calculator import diff_holdings
from findit.summary import render_summary


def _holdings(rows):
    return pd.DataFrame(rows, columns=["scheme_id", "isin", "quantity",
                                       "market_value_lakhs", "pct_nav"])


def test_diff_holdings_classifies_and_decomposes_flow():
    prev = _holdings([(1, "A", 100, 100.0, 5.0), (1, "B", 50, 50.0, 2.0),
                      (1, "C", 10, 10.0, 1.0), (2, "A", 10, 10.0, 1.0)])
    curr = _holdings([(1, "A", 120, 132.0, 6.0), (1, "B", 50, 55.0, 2.0),
                      (1, "D", 30, 30.0, 1.5)])
    out = diff_holdings(prev, curr, "2026-07", "2026-08").set_index("isin")
    assert dict(out["action"]) == {"A": "added", "B": "unchanged", "C": "exited", "D": "new"}
    # Flow at the month-end price (132/120 = 1.1 a share), price effect the rest.
    assert out.loc["A", "flow_lakhs"] == 20 * 1.1
    assert round(out.loc["A", "flow_lakhs"] + out.loc["A", "price_effect_lakhs"], 9) == 32.0
    assert out.loc["C", "flow_lakhs"] == -10.0 and out.loc["D", "flow_lakhs"] == 30.0
    # Scheme 2 has no August snapshot: left out, not read as selling everything.
    assert set(out["scheme_id"]) == {1}


def _deltas(rows):
    return pd.DataFrame(rows, columns=["isin", "scheme_id", "amc_name", "action", "flow_lakhs",
                                       "stock_name", "industry", "instrument_type",
                                       "value_change_lakhs", "prev_month"])


def _vote(isin, scheme, amc, action, flow):
    return (isin, scheme, amc, action, flow, isin, "X", "equity", flow, "2026-07")


def test_rank_consensus_orders_by_breadth_then_accumulation():
    deltas = _deltas([
        _vote("A", 1, "P", "added", 5.0), _vote("A", 2, "Q", "added", 1.0),
        _vote("B", 1, "P", "new", 90.0), _vote("B", 2, "Q", "new", 90.0),
        _vote("C", 1, "P", "trimmed", -3.0),
    ])
    out = rank_consensus(deltas, {"A", "C"}, pd.DataFrame(columns=["isin"] + ACTIVE_COLUMNS))
    # A and B both have two AMCs buying; B is a fresh listing, so A leads.
    assert list(out["isin"]) == ["A", "B", "C"]
    row = out.set_index("isin").loc["B"]
    assert row["universe_status"] == "new_listing" and row["accumulation_flow_lakhs"] == 0.0
    assert out.set_index("isin").loc["C", "net_amc_count"] == -1
    unknown = rank_consensus(deltas, None, pd.DataFrame(columns=["isin"] + ACTIVE_COLUMNS))
    assert set(unknown["universe_status"]) == {"unknown"}


def test_a_stock_with_no_filing_is_no_data_never_a_decrease():
    consensus = pd.DataFrame({"isin": ["A", "B"], "net_amc_count": [2, 1]})
    filings = pd.DataFrame({"isin": ["A"], "shareholding_quarter_end": ["2026-06-30"],
                            "fii_pct_change": [0.4], "dii_pct_change": [-0.1]})
    out = add_shareholding_signal(consensus, filings, date(2026, 9, 10)).set_index("isin")
    assert out.loc["A", "fii_direction"] == "increased" and bool(out.loc["A", "is_common"])
    assert out.loc["B", "fii_direction"] == "no_data" and bool(out.loc["B", "shareholding_stale"])
    assert out.loc["A", "staleness_days"] == 72


def test_render_summary_keeps_maturities_out_of_equity_calls():
    deltas = pd.DataFrame([
        {"isin": "A", "stock_name": "Alpha", "action": "added", "qty_change": 10,
         "flow_lakhs": 250.0, "value_change_lakhs": 260.0, "price_effect_lakhs": 10.0,
         "pct_nav_change": 0.2, "instrument_type": "equity"},
        {"isin": "T", "stock_name": "T-Bill", "action": "exited", "qty_change": -5,
         "flow_lakhs": -90.0, "value_change_lakhs": -90.0, "price_effect_lakhs": 0.0,
         "pct_nav_change": -1.0, "instrument_type": "money_market"},
    ])
    text = render_summary("Fund", "AMC", "2026-08", deltas, split_non_equity=True)
    assert "biggest addition was Alpha" in text
    assert "Non-equity exits (maturities, not equity exits): T-Bill" in text
    assert "Fully exited" not in text
    assert "No holding-change data available" in render_summary(
        "Fund", "AMC", "2026-08", deltas.iloc[0:0])
