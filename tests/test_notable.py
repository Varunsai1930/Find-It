"""Notable lists: each rule, its thresholds and its missing-month reporting. Pure."""

import pandas as pd

from findit.core import notable


def _ranking(*rows):
    """Rows of (isin, net, opening, exiting, active net, FII change); rank order as given."""
    return pd.DataFrame([{
        "isin": isin, "stock_name": isin.title(), "industry": "Ind", "instrument_type": "equity",
        "net_amc_count": net, "amcs_opening": opening, "amcs_exiting": exiting,
        "net_active_amc_count": active, "universe_status": "established",
        "fii_pct_change": fii, "shareholding_status": "available" if fii is not None else "missing",
        "shareholding_stale": fii is None,
    } for isin, net, opening, exiting, active, fii in rows])


def _lists(ranked, last=None, before_last=None, side="buy"):
    out = notable.notable_lists(ranked, [("2026-07", last), ("2026-06", before_last)], side)
    return {item["key"]: item for item in out}


def _isins(item):
    return [stock["isin"] for stock in item["stocks"]]


def test_turnarounds_need_two_net_houses_after_net_selling_last_month():
    now = _ranking(("flip", 3, 0, 0, 0, None), ("small", 1, 0, 0, 0, None),
                   ("steady", 4, 0, 0, 0, None), ("quiet", 2, 0, 0, 0, None))
    last = _ranking(("flip", -2, 0, 0, 0, None), ("small", -3, 0, 0, 0, None),
                    ("steady", 1, 0, 0, 0, None))
    turn = _lists(now, last)["turnaround"]
    # "small" did not reach two houses; "quiet" was untraded last month, not selling.
    assert _isins(turn) == ["flip"]
    assert turn["stocks"][0]["reason"] == "+3 net fund houses, from −2 last month"
    assert turn["title"] == "Turned to buying"


def test_rules_that_read_earlier_months_name_the_month_that_is_missing():
    lists = _lists(_ranking(("a", 3, 2, 0, 2, None)), last=None)
    assert lists["turnaround"]["needs"] == "2026-07" and lists["turnaround"]["stocks"] == []
    assert lists["streak"]["needs"] == "2026-07"
    last = _ranking(("a", 1, 0, 0, 0, None))
    assert _lists(_ranking(("a", 3, 2, 0, 2, None)), last=last)["streak"]["needs"] == "2026-06"
    # Rules on this month alone never wait for earlier months.
    assert lists["opened"]["needs"] is None and _isins(lists["opened"]) == ["a"]


def test_fresh_positions_leave_out_stocks_new_to_the_loaded_portfolios():
    now = _ranking(("ipo", 3, 3, 0, 0, None), ("held", 2, 2, 0, 0, None),
                   ("one", 2, 1, 0, 0, None))
    now.loc[0, "universe_status"] = "new_listing"
    opened = _lists(now)["opened"]
    assert _isins(opened) == ["held"]
    assert opened["stocks"][0]["reason"] == "Schemes at 2 fund houses opened new positions"


def test_streaks_hold_the_direction_in_every_month_and_read_oldest_first():
    now = _ranking(("long", 2, 0, 0, 0, None), ("broke", 3, 0, 0, 0, None))
    last = _ranking(("long", 1, 0, 0, 0, None), ("broke", 2, 0, 0, 0, None))
    before = _ranking(("long", 4, 0, 0, 0, None))  # "broke" was untraded then.
    streak = _lists(now, last, before)["streak"]
    assert _isins(streak) == ["long"]
    assert streak["stocks"][0]["reason"] == "Net buying 3 months running: +4, +1, +2"


def test_foreign_agreement_needs_a_fresh_filing_moving_the_same_way():
    now = _ranking(("agree", 2, 0, 0, 0, 0.5), ("against", 3, 0, 0, 0, -0.2),
                   ("nofiling", 4, 0, 0, 0, None), ("stale", 2, 0, 0, 0, 0.9))
    now.loc[3, "shareholding_stale"] = True
    fii = _lists(now)["fii"]
    assert _isins(fii) == ["agree"]
    assert fii["stocks"][0]["reason"] == "+2 net fund houses; FII +0.50 pp in the latest filing"


def test_selling_side_mirrors_every_rule():
    now = _ranking(("exit", -3, 0, 2, -2, -0.4), ("trim", -2, 0, 1, -1, 0.3))
    last = _ranking(("exit", 2, 0, 0, 0, None))
    before = _ranking(("trim", -1, 0, 0, 0, None))
    lists = _lists(now, last, before, side="sell")
    assert lists["turnaround"]["title"] == "Turned to selling"
    assert _isins(lists["turnaround"]) == ["exit"]
    assert lists["exited"]["stocks"][0]["reason"] == "Schemes at 2 fund houses sold their whole position"
    assert "opened" not in lists
    assert _isins(lists["active"]) == ["exit"]
    assert lists["active"]["stocks"][0]["reason"] == "−2 net fund houses beyond price movement"
    assert _isins(lists["fii"]) == ["exit"]
    assert _isins(lists["streak"]) == []  # "trim" was untraded last month.


def test_ties_keep_ranking_order_and_an_empty_month_lists_nothing():
    now = _ranking(("first", 2, 2, 0, 3, None), ("second", 2, 2, 0, 3, None))
    lists = _lists(now)
    assert _isins(lists["opened"]) == ["first", "second"]
    assert _isins(lists["active"]) == ["first", "second"]
    empty = _lists(_ranking(), _ranking(), _ranking())
    assert all(item["stocks"] == [] and item["needs"] is None for item in empty.values())
