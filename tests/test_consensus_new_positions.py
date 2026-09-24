"""New-listing vs accumulation flow in the consensus. In-memory DBs, no network."""

import sqlite3

import pytest

import consensus_signals


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schemes (scheme_id INTEGER PRIMARY KEY, "
                 "amc_name TEXT NOT NULL, scheme_name TEXT NOT NULL, is_active_equity INTEGER)")
    conn.execute("CREATE TABLE stocks (isin TEXT PRIMARY KEY, name TEXT NOT NULL, "
                 "industry TEXT, instrument_type TEXT)")
    conn.execute("CREATE TABLE mf_holdings_monthly (scheme_id INTEGER, isin TEXT, "
                 "report_month TEXT, quantity REAL, market_value_lakhs REAL, pct_nav REAL, "
                 "PRIMARY KEY (scheme_id, isin, report_month))")
    conn.execute("CREATE TABLE mf_holding_deltas (scheme_id INTEGER NOT NULL, "
                 "isin TEXT NOT NULL, report_month TEXT NOT NULL, prev_month TEXT, "
                 "qty_change REAL, value_change_lakhs REAL, flow_lakhs REAL, "
                 "price_effect_lakhs REAL, pct_nav_change REAL, action TEXT NOT NULL, "
                 "PRIMARY KEY (scheme_id, isin, report_month))")
    return conn


def _delta(conn, scheme_id, amc, isin, name, action, flow, month="2026-08",
           prev="2026-07", held_prev=False):
    conn.execute("INSERT OR IGNORE INTO schemes VALUES (?, ?, ?, 1)",
                 (scheme_id, amc, f"{amc} Fund"))
    conn.execute("INSERT OR REPLACE INTO stocks VALUES (?, ?, 'Ind', 'equity')", (isin, name))
    conn.execute("INSERT OR REPLACE INTO mf_holding_deltas (scheme_id, isin, report_month, "
                 "prev_month, qty_change, value_change_lakhs, flow_lakhs, price_effect_lakhs, "
                 "pct_nav_change, action) VALUES (?, ?, ?, ?, 1, ?, ?, 0, 0.1, ?)",
                 (scheme_id, isin, month, prev, flow, flow, action))
    if held_prev:
        conn.execute("INSERT OR REPLACE INTO mf_holdings_monthly VALUES (?, ?, ?, 1, 1, 0.1)",
                     (scheme_id, isin, prev))
    conn.commit()


NEW_LISTING = "INE111A01011"
ESTABLISHED = "INE222A01012"


def _two_stock_month(conn):
    """A fresh listing two AMCs opened, and a held name two AMCs added to."""
    _delta(conn, 1, "AMC1", NEW_LISTING, "Fresh IPO Ltd.", "new", 900.0)
    _delta(conn, 2, "AMC2", NEW_LISTING, "Fresh IPO Ltd.", "new", 900.0)
    _delta(conn, 1, "AMC1", ESTABLISHED, "Held Co Ltd.", "added", 100.0, held_prev=True)
    _delta(conn, 2, "AMC2", ESTABLISHED, "Held Co Ltd.", "added", 100.0, held_prev=True)


def test_new_position_flow_is_split_from_accumulation():
    conn = _conn()
    _two_stock_month(conn)
    out = consensus_signals.compute_consensus(conn, "2026-08").set_index("isin")
    fresh = out.loc[NEW_LISTING]
    assert fresh["new_position_flow_lakhs"] == pytest.approx(1800.0)
    assert fresh["accumulation_flow_lakhs"] == pytest.approx(0.0)
    assert fresh["amcs_opening"] == 2
    held = out.loc[ESTABLISHED]
    assert held["new_position_flow_lakhs"] == pytest.approx(0.0)
    assert held["accumulation_flow_lakhs"] == pytest.approx(200.0)
    assert held["amcs_opening"] == 0
    # The reported total is unchanged: the split partitions it, never alters it.
    for row in (fresh, held):
        assert row["total_flow_lakhs"] == pytest.approx(
            row["new_position_flow_lakhs"] + row["accumulation_flow_lakhs"])


def test_new_listing_is_flagged_and_ranked_below_real_accumulation():
    conn = _conn()
    _two_stock_month(conn)
    out = consensus_signals.compute_consensus(conn, "2026-08")
    status = dict(zip(out["isin"], out["universe_status"]))
    assert status[NEW_LISTING] == "new_listing"
    assert status[ESTABLISHED] == "established"
    # Same breadth (2 buying, 0 selling) and 9x the raw flow, but the fresh
    # listing's flow is entry value, so it must not outrank accumulation.
    assert out.iloc[0]["isin"] == ESTABLISHED
    assert out.iloc[1]["isin"] == NEW_LISTING


def test_consensus_breadth_still_outranks_the_new_listing_penalty():
    """The penalty only breaks ties; it never overrides more AMCs buying."""
    conn = _conn()
    _two_stock_month(conn)
    _delta(conn, 3, "AMC3", NEW_LISTING, "Fresh IPO Ltd.", "new", 900.0)
    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert out.iloc[0]["isin"] == NEW_LISTING
    assert out.iloc[0]["net_amc_count"] == 3


def test_unknown_when_no_previous_month_is_on_record():
    """First month tracked: say so, rather than branding everything new."""
    conn = _conn()
    _delta(conn, 1, "AMC1", ESTABLISHED, "Held Co Ltd.", "new", 100.0, prev=None)
    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert list(out["universe_status"]) == ["unknown"]
    assert not out["is_new_to_universe"].any()


def test_empty_consensus_still_exposes_the_new_columns():
    conn = _conn()
    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert out.empty
    for column in ("amcs_opening", "new_position_flow_lakhs",
                   "accumulation_flow_lakhs", "universe_status", "is_new_to_universe"):
        assert column in out.columns


def test_a_fund_missing_from_either_month_is_not_compared(tmp_path):
    import db
    import delta_calculator

    conn = db.get_connection(str(tmp_path / "t.db"))
    conn.executemany("INSERT INTO schemes (scheme_id, amc_name, scheme_name) VALUES (?, ?, ?)",
                     [(1, "A AMC", "Ongoing"), (2, "B AMC", "Launched in August"),
                      (3, "C AMC", "File missing in August")])
    conn.execute("INSERT INTO stocks (isin, name, instrument_type) VALUES "
                 "('INEAAA01001', 'A', 'equity')")
    conn.executemany(
        "INSERT INTO mf_holdings_monthly (scheme_id, isin, report_month, quantity, "
        "market_value_lakhs, pct_nav) VALUES (?, 'INEAAA01001', ?, 10, 10.0, 1.0)",
        [(1, "2026-07"), (1, "2026-08"), (2, "2026-08"), (3, "2026-07")])
    deltas = delta_calculator.compute_deltas(conn, "2026-07", "2026-08")
    assert sorted(deltas["scheme_id"]) == [1]
    assert delta_calculator.unmatched_schemes(conn, "2026-07", "2026-08") == {
        "only_prev": [3], "only_curr": [2]}
    conn.close()


def test_recomputing_a_month_replaces_it_instead_of_leaving_stale_rows(tmp_path):
    import db
    import delta_calculator

    conn = db.get_connection(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO mf_holding_deltas (scheme_id, isin, report_month, prev_month, "
                 "qty_change, value_change_lakhs, pct_nav_change, action) VALUES "
                 "(9, 'INEOLD01001', '2026-08', '2026-07', 5, 5, 1, 'new')")
    conn.commit()
    empty = delta_calculator.compute_deltas(conn, "2026-07", "2026-08")
    assert empty.empty
    delta_calculator.persist_deltas(conn, empty, report_month="2026-08")
    assert conn.execute("SELECT COUNT(*) FROM mf_holding_deltas").fetchone()[0] == 0
    conn.close()
