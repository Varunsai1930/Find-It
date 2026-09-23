"""Discretionary vs flow-driven buying. Synthetic holdings; no network."""

import pandas as pd
import pytest

import consensus_signals
import db
from findit.core import active_weight as aw


def _h(rows):
    return pd.DataFrame(rows, columns=["scheme_id", "isin", "quantity", "market_value_lakhs"])


def _by_isin(changes):
    return changes.set_index("isin")["active_weight_change_pp"].to_dict()


def test_proportional_inflow_buying_scores_zero():
    # 20% inflow spread pro rata, prices +10%: every quantity is up, nothing was chosen.
    prev = _h([(1, "A", 100, 100.0), (1, "B", 300, 300.0)])
    curr = _h([(1, "A", 120, 132.0), (1, "B", 360, 396.0)])
    changes = aw.active_weight_changes(prev, curr)
    assert all(abs(v) < 1e-9 for v in _by_isin(changes).values())


def test_a_tilt_is_positive_on_the_stock_bought_and_negative_on_the_funding():
    prev = _h([(1, "A", 100, 100.0), (1, "B", 100, 100.0)])
    curr = _h([(1, "A", 150, 150.0), (1, "B", 50, 50.0)])  # same prices, rotated
    changes = _by_isin(aw.active_weight_changes(prev, curr))
    assert changes["A"] == pytest.approx(25.0)
    assert changes["B"] == pytest.approx(-25.0)


def test_price_moves_alone_are_not_decisions():
    prev = _h([(1, "A", 100, 100.0), (1, "B", 100, 100.0)])
    curr = _h([(1, "A", 100, 200.0), (1, "B", 100, 100.0)])  # A doubled, no trades
    assert all(abs(v) < 1e-9 for v in _by_isin(aw.active_weight_changes(prev, curr)).values())


def test_new_and_exited_positions():
    prev = _h([(1, "A", 100, 100.0), (1, "OLD", 100, 100.0)])
    curr = _h([(1, "A", 100, 100.0), (1, "NEW", 100, 100.0), (2, "OLD", 10, 12.0)])
    prev = pd.concat([prev, _h([(2, "OLD", 10, 10.0)])])
    changes = aw.active_weight_changes(prev, curr)
    one = changes[changes["scheme_id"] == 1].set_index("isin")
    assert one.loc["NEW", "active_weight_change_pp"] == pytest.approx(50.0)
    assert one.loc["NEW", "price_basis"] == "new_position"
    assert one.loc["OLD", "active_weight_change_pp"] < 0
    # The exit is valued at the price another scheme reports this month.
    assert one.loc["OLD", "price_basis"] == "cross_scheme"


def test_scheme_without_a_previous_month_is_not_scored():
    changes = aw.active_weight_changes(_h([(1, "A", 1, 1.0)]), _h([(2, "A", 1, 1.0)]))
    assert changes.empty


def test_split_is_inferred_and_adjusted():
    # 1:2 split: every holder's quantity exactly doubles, price halves.
    prev = _h([(1, "S", 100, 100.0), (1, "B", 100, 100.0), (2, "S", 40, 40.0), (2, "B", 10, 10.0)])
    curr = _h([(1, "S", 200, 100.0), (1, "B", 100, 100.0), (2, "S", 80, 40.0), (2, "B", 10, 10.0)])
    splits = aw.infer_split_ratios(prev, curr)
    assert splits == {"S": 2.0}
    changes = aw.active_weight_changes(prev, curr, split_ratios=splits)
    assert all(abs(v) < 1e-9 for v in changes["active_weight_change_pp"])


def test_a_crash_is_not_mistaken_for_a_split():
    prev = _h([(1, "S", 100, 100.0), (2, "S", 40, 40.0)])
    curr = _h([(1, "S", 100, 50.0), (2, "S", 40, 20.0)])  # price halves, no share change
    assert aw.infer_split_ratios(prev, curr) == {}


def test_funds_buying_different_amounts_are_not_a_split():
    prev = _h([(1, "S", 100, 100.0), (2, "S", 40, 40.0)])
    curr = _h([(1, "S", 200, 100.0), (2, "S", 60, 30.0)])
    assert aw.infer_split_ratios(prev, curr) == {}


def test_confirmed_corporate_action_wins():
    prev = _h([(1, "S", 100, 100.0)])
    curr = _h([(1, "S", 100, 100.0)])
    assert aw.infer_split_ratios(prev, curr, confirmed={"S": 5}) == {"S": 5.0}


def test_one_amc_counts_once_and_noise_is_ignored():
    changes = pd.DataFrame([
        {"scheme_id": 1, "isin": "A", "active_weight_change_pp": 1.0,
         "discretionary_flow_lakhs": 10.0},
        {"scheme_id": 2, "isin": "A", "active_weight_change_pp": 2.0,
         "discretionary_flow_lakhs": 20.0},
        {"scheme_id": 3, "isin": "A", "active_weight_change_pp": -0.5,
         "discretionary_flow_lakhs": -50.0},
        {"scheme_id": 4, "isin": "A", "active_weight_change_pp": 0.01,  # rounding noise
         "discretionary_flow_lakhs": 0.1},
    ])
    amc = {1: "X", 2: "X", 3: "Y", 4: "Z"}
    out = aw.aggregate_by_stock(changes, amc).set_index("isin").loc["A"]
    assert out["active_amcs_buying"] == 1  # X, once, despite two schemes
    assert out["active_amcs_selling"] == 1  # Y
    assert out["net_active_amc_count"] == 0  # Z's 0.01pp is below the floor


def test_unknown_scheme_amc_fails_loudly():
    changes = pd.DataFrame([{"scheme_id": 9, "isin": "A", "active_weight_change_pp": 1.0,
                             "discretionary_flow_lakhs": 1.0}])
    with pytest.raises(ValueError, match="no AMC"):
        aw.aggregate_by_stock(changes, {})


def _seed(conn):
    conn.executemany("INSERT INTO schemes (scheme_id, amc_name, scheme_name) VALUES (?, ?, ?)",
                     [(1, "X AMC", "Growth"), (2, "Y AMC", "Value"), (3, "Y AMC", "Nifty ETF")])
    conn.execute("UPDATE schemes SET is_active_equity = CASE scheme_id WHEN 3 THEN 0 ELSE 1 END")
    conn.executemany("INSERT INTO stocks (isin, name, instrument_type) VALUES (?, ?, 'equity')",
                     [("INEAAA01001", "A Ltd"), ("INEBBB01001", "B Ltd")])
    holdings = [
        # X: 20% inflow, all pro rata -> raw "added" both, active nothing.
        (1, "INEAAA01001", "2026-07", 100, 100.0), (1, "INEBBB01001", "2026-07", 100, 100.0),
        (1, "INEAAA01001", "2026-08", 120, 120.0), (1, "INEBBB01001", "2026-08", 120, 120.0),
        # Y: rotates from B into A.
        (2, "INEAAA01001", "2026-07", 100, 100.0), (2, "INEBBB01001", "2026-07", 100, 100.0),
        (2, "INEAAA01001", "2026-08", 150, 150.0), (2, "INEBBB01001", "2026-08", 50, 50.0),
        # Passive: huge tilt into A that must not count.
        (3, "INEAAA01001", "2026-07", 10, 10.0), (3, "INEBBB01001", "2026-07", 90, 90.0),
        (3, "INEAAA01001", "2026-08", 90, 90.0), (3, "INEBBB01001", "2026-08", 10, 10.0),
    ]
    conn.executemany(
        "INSERT INTO mf_holdings_monthly (scheme_id, isin, report_month, quantity, "
        "market_value_lakhs, pct_nav) VALUES (?, ?, ?, ?, ?, 1.0)", holdings)
    for sid, isin, q_prev, q_curr in [(1, "INEAAA01001", 100, 120), (1, "INEBBB01001", 100, 120),
                                      (2, "INEAAA01001", 100, 150), (2, "INEBBB01001", 100, 50),
                                      (3, "INEAAA01001", 10, 90), (3, "INEBBB01001", 90, 10)]:
        conn.execute(
            "INSERT INTO mf_holding_deltas (scheme_id, isin, report_month, prev_month, "
            "qty_change, flow_lakhs, action) VALUES (?, ?, '2026-08', '2026-07', ?, ?, ?)",
            (sid, isin, q_curr - q_prev, float(q_curr - q_prev),
             "added" if q_curr > q_prev else "trimmed"))
    conn.commit()


def test_consensus_carries_discretionary_breadth(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    _seed(conn)
    out = consensus_signals.compute_consensus(conn, "2026-08").set_index("isin")
    # Raw counts: X added both; Y added A and trimmed B.
    assert out.loc["INEAAA01001", "net_amc_count"] == 2
    assert out.loc["INEBBB01001", "net_amc_count"] == 0
    # Discretionary: only Y chose anything; X's pro-rata buying and the ETF don't count.
    assert out.loc["INEAAA01001", "net_active_amc_count"] == 1
    assert out.loc["INEBBB01001", "net_active_amc_count"] == -1
    conn.close()


def test_quarantined_previous_month_is_excluded(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    _seed(conn)
    conn.execute("INSERT INTO scheme_month_status (scheme_id, report_month, status) "
                 "VALUES (2, '2026-07', 'quarantined')")
    conn.commit()
    active = consensus_signals.compute_active_consensus(conn, "2026-08").set_index("isin")
    assert active["active_amcs_buying"].sum() == 0
    conn.close()
