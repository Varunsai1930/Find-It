"""Validation gate checks: NAV sum bands and quantity-ratio warnings. Pure; no DB."""

import pandas as pd
import pytest

ISIN_A = "INE002A01018"


@pytest.mark.parametrize("nav,passed", [(0.0, True), (4.0, True), (94.9, True),
                                        (95.0, True), (105.0, True), (110.0, True),
                                        (110.01, False), (130.0, False)])
def test_nav_sum_warn_and_quarantine_boundaries(nav, passed):
    from findit.store.validation_gate import validate_nav_sum, validate_holdings_month

    result = validate_nav_sum(nav)
    assert result["passed"] is passed
    codes = {i["code"] for i in result["issues"]}
    assert ("nav_sum_out_of_band" in codes) == (nav < 95 or nav > 105)
    assert ("nav_sum_quarantine" in codes) == (nav > 110)
    assert validate_holdings_month(pd.DataFrame({"pct_nav": [nav]}))["passed"] is passed


@pytest.mark.parametrize("prev,curr,peer,expected", [
    (100, 10300, 1.0, "qty_ratio_out_of_band"),
    (100, 4, 1.0, "qty_ratio_out_of_band"),
    (100, 204, 2.04, "qty_ratio_corporate_action_candidate"),
    (100, 40, 0.42, "qty_ratio_corporate_action_candidate"),
    (100, 1000, 10.5, "qty_ratio_corporate_action_candidate"),
    (100, 1000, 10.51, "qty_ratio_out_of_band"),
    (100, 1000, None, "qty_ratio_out_of_band"),
    (100, 1000, float("nan"), "qty_ratio_out_of_band"),
    (100, 50, 0.5, None),
    (100, 200, 2.0, None),
    (0, 1000, 10.0, None),
    (100, 0, 0.0, None),
    ("bad", 100, None, "qty_ratio_unreadable"),
])
def test_quantity_changes_never_quarantine(prev, curr, peer, expected):
    from findit.store.validation_gate import validate_qty_ratio

    result = validate_qty_ratio(prev, curr, isin=ISIN_A, peer_median_ratio=peer)
    assert result["passed"] is True
    assert all(i["severity"] == "warn" for i in result["issues"])
    assert [i["code"] for i in result["issues"]] == ([expected] if expected else [])


def test_known_corporate_action_is_warn_only():
    from findit.store.validation_gate import validate_qty_ratio

    result = validate_qty_ratio(100, 1000, has_corporate_action=True)
    assert result["passed"] is True
    assert result["issues"][0]["severity"] == "warn"


def test_combined_gate_passes_peer_evidence():
    from findit.store.validation_gate import validate_holdings_month

    prev = pd.DataFrame({"isin": [ISIN_A], "quantity": [100], "pct_nav": [4]})
    curr = prev.assign(quantity=204)
    result = validate_holdings_month(curr, prev, peer_median_ratios={ISIN_A: 2.04})
    assert result["passed"] is True
    issue = next(i for i in result["issues"]
                 if i["code"] == "qty_ratio_corporate_action_candidate")
    assert issue["isin"] == ISIN_A
    assert issue["peer_median_ratio"] == 2.04
