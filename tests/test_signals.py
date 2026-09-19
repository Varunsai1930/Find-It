"""Signal + summary tests. In-memory DBs only; never ./tracker.db, no network."""

import sqlite3

import pandas as pd

import consensus_signals
import fallback_summary
from findit.narrate.template import render_summary
from run_pipeline import _overlap_summary, build_overlap_view


# ---- helpers ---------------------------------------------------------------

def _mem_conn(with_filing_type: bool = False, deltas_pk: bool = True,
              with_price_effect: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE schemes (scheme_id INTEGER PRIMARY KEY, amc_name TEXT NOT NULL, scheme_name TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE stocks (isin TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT, instrument_type TEXT)"
    )
    delta_cols = (
        "scheme_id INTEGER NOT NULL, isin TEXT NOT NULL, report_month TEXT NOT NULL,\n"
        "            prev_month TEXT, qty_change REAL, value_change_lakhs REAL,\n"
        "            flow_lakhs REAL, pct_nav_change REAL, action TEXT NOT NULL"
    )
    if with_price_effect:
        delta_cols += ", price_effect_lakhs REAL"
    if deltas_pk:
        delta_cols += ", PRIMARY KEY (scheme_id, isin, report_month)"
    conn.execute(f"CREATE TABLE mf_holding_deltas (\n            {delta_cols}\n        )")
    if with_filing_type:
        conn.execute(
            """CREATE TABLE shareholding_quarterly (
                isin TEXT NOT NULL, quarter_end TEXT NOT NULL,
                promoter_pct REAL, fii_pct REAL, dii_pct REAL,
                public_pct REAL, source TEXT, filing_type TEXT,
                PRIMARY KEY (isin, quarter_end)
            )"""
        )
    else:
        conn.execute(
            """CREATE TABLE shareholding_quarterly (
                isin TEXT NOT NULL, quarter_end TEXT NOT NULL,
                promoter_pct REAL, fii_pct REAL, dii_pct REAL,
                public_pct REAL, source TEXT,
                PRIMARY KEY (isin, quarter_end)
            )"""
        )
    return conn


def _add_scheme_stock_delta(
    conn: sqlite3.Connection,
    scheme_id: int = 1,
    amc: str = "AMC",
    scheme: str = "SCHEME",
    isin: str = "INE000A01001",
    name: str = "Stock",
    itype: str = "equity",
    qty: float = 100.0,
    val: float = 100.0,
    flow=None,
    pct: float = 0.5,
    action: str = "new",
    month: str = "2026-08",
    prev: str = "2026-07",
    price=None,
):
    conn.execute(
        "INSERT OR IGNORE INTO schemes (scheme_id, amc_name, scheme_name) VALUES (?, ?, ?)",
        (scheme_id, amc, scheme),
    )
    conn.execute(
        "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type) VALUES (?, ?, ?, ?)",
        (isin, name, "Ind", itype),
    )
    if flow is None:
        flow = val
    if price is None:
        # Identity: value_change == flow + price_effect (Worker A convention).
        price = val - flow
    has_price = any(
        r[1] == "price_effect_lakhs"
        for r in conn.execute("PRAGMA table_info(mf_holding_deltas)").fetchall()
    )
    if has_price:
        conn.execute(
            """INSERT OR REPLACE INTO mf_holding_deltas
               (scheme_id, isin, report_month, prev_month, qty_change,
                value_change_lakhs, flow_lakhs, pct_nav_change, action, price_effect_lakhs)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scheme_id, isin, month, prev, qty, val, flow, pct, action, price),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO mf_holding_deltas
               (scheme_id, isin, report_month, prev_month, qty_change,
                value_change_lakhs, flow_lakhs, pct_nav_change, action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scheme_id, isin, month, prev, qty, val, flow, pct, action),
        )
    conn.commit()


def _add_sh(conn, isin, qend, fii, dii, filing_type=None):
    if filing_type is None:
        conn.execute(
            """INSERT OR REPLACE INTO shareholding_quarterly
               (isin, quarter_end, promoter_pct, fii_pct, dii_pct, public_pct, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (isin, qend, 50.0, fii, dii, 50.0, "test"),
        )
    else:
        conn.execute(
            """INSERT OR REPLACE INTO shareholding_quarterly
               (isin, quarter_end, promoter_pct, fii_pct, dii_pct, public_pct, source, filing_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (isin, qend, 50.0, fii, dii, 50.0, "test", filing_type),
        )
    conn.commit()


def _consensus_row(isin="INE000A01001", net=2, name="Test Equity"):
    return pd.DataFrame([{
        "isin": isin, "stock_name": name,
        "amcs_buying": max(net, 0), "amcs_selling": 0,
        "total_flow_lakhs": 100.0, "total_price_effect_lakhs": 20.0,
        "net_amc_count": net, "buying_ratio": 1.0,
    }])


# ---- compute_consensus -----------------------------------------------------

def test_buying_ratio_and_ranking():
    conn = _mem_conn()
    # AAA: net 2, flow 100. BBB: net 1 but huge flow. CCC: net 1 small flow.
    _add_scheme_stock_delta(conn, 1, "HDFC AMC", "Top100", "INEAAA01001", "AAA", "equity", 10, 100, 100, 0.5, "new", price=10.0)
    _add_scheme_stock_delta(conn, 2, "SBI AMC", "Bluechip", "INEAAA01001", "AAA", "equity", 10, 100, 100, 0.5, "added", price=-4.0)
    # BBB: 2 buying, 1 selling
    _add_scheme_stock_delta(conn, 1, "HDFC AMC", "Top100", "INEBBB01001", "BBB", "equity", 10, 5000, 5000, 0.5, "new")
    _add_scheme_stock_delta(conn, 2, "SBI AMC", "Bluechip", "INEBBB01001", "BBB", "equity", 10, 5000, 5000, 0.5, "added")
    _add_scheme_stock_delta(conn, 3, "ICICI AMC", "Bluechip", "INEBBB01001", "BBB", "equity", -5, -100, -100, -0.1, "trimmed")
    # CCC: 1 buying
    _add_scheme_stock_delta(conn, 1, "HDFC AMC", "Top100", "INECCC01001", "CCC", "equity", 5, 50, 50, 0.2, "new")

    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert "buying_ratio" in out.columns
    assert "total_flow_lakhs" in out.columns
    assert "total_price_effect_lakhs" in out.columns
    assert "total_value_change_lakhs" not in out.columns
    row = {r["isin"]: r for _, r in out.iterrows()}
    assert row["INEAAA01001"]["buying_ratio"] == 1.0
    assert abs(row["INEBBB01001"]["buying_ratio"] - 2 / 3) < 1e-9
    assert row["INEBBB01001"]["net_amc_count"] == 1
    # Price effects aggregate per ISIN (10.0 + -4.0 = 6.0 for AAA).
    assert abs(row["INEAAA01001"]["total_price_effect_lakhs"] - 6.0) < 1e-9
    # Rank: net first, then flow.
    order = out["isin"].tolist()
    assert order[0] == "INEAAA01001"  # net 2
    assert order[1] == "INEBBB01001"  # net 1, flow 9900
    assert order[2] == "INECCC01001"  # net 1, flow 50
    conn.close()


# ---- join_shareholding_increase --------------------------------------------

def test_interim_filing_never_displaces_quarterlies():
    conn = _mem_conn()
    isin = "INE000A01001"
    _add_sh(conn, isin, "2026-03-31", 10.0, 5.0)
    _add_sh(conn, isin, "2026-04-15", 99.0, 99.0)  # interim between quarters
    _add_sh(conn, isin, "2026-06-30", 12.0, 4.5)
    _add_sh(conn, isin, "2026-07-20", 99.0, 99.0)  # interim after quarter
    out = consensus_signals.join_shareholding_increase(conn, _consensus_row(isin))
    r = out.iloc[0]
    assert r["shareholding_quarter_end"] == "2026-06-30"
    assert r["previous_shareholding_quarter_end"] == "2026-03-31"
    assert r["fii_pct_change"] == 2.0
    assert r["fii_direction"] == "increased"
    assert r["dii_direction"] == "decreased"
    conn.close()


def test_filing_type_filter_used_when_column_exists():
    conn = _mem_conn(with_filing_type=True)
    isin = "INE000A01001"
    _add_sh(conn, isin, "2026-03-31", 10.0, 5.0, filing_type="quarterly")
    _add_sh(conn, isin, "2026-05-15", 11.0, 5.5, filing_type="quarterly")
    _add_sh(conn, isin, "2026-06-30", 99.0, 99.0, filing_type="interim")
    out = consensus_signals.join_shareholding_increase(conn, _consensus_row(isin))
    r = out.iloc[0]
    # filing_type filter keeps the non-standard 05-15 quarterly, drops 06-30 interim.
    assert r["shareholding_quarter_end"] == "2026-05-15"
    assert r["previous_shareholding_quarter_end"] == "2026-03-31"
    conn.close()


def test_single_quarter_yields_no_data_not_decreased():
    conn = _mem_conn()
    isin = "INE000A01001"
    _add_sh(conn, isin, "2026-06-30", 10.0, 5.0)
    out = consensus_signals.join_shareholding_increase(conn, _consensus_row(isin, net=1))
    r = out.iloc[0]
    assert r["fii_direction"] == "no_data"
    assert r["dii_direction"] == "no_data"
    assert bool(r["fii_increased"]) is False
    assert bool(r["dii_increased"]) is False
    assert bool(r["is_common_with_fii_increase"]) is False
    assert bool(r["is_common"]) is False
    conn.close()


def test_future_quarter_excluded_by_as_of_month():
    conn = _mem_conn()
    isin = "INE000A01001"
    _add_sh(conn, isin, "2026-03-31", 10.0, 5.0)
    _add_sh(conn, isin, "2026-06-30", 12.0, 6.0)
    _add_sh(conn, isin, "2026-09-30", 20.0, 20.0)
    out = consensus_signals.join_shareholding_increase(
        conn, _consensus_row(isin), as_of_month="2026-06"
    )
    assert out.iloc[0]["shareholding_quarter_end"] == "2026-06-30"
    assert out.iloc[0]["previous_shareholding_quarter_end"] == "2026-03-31"
    out2 = consensus_signals.join_shareholding_increase(
        conn, _consensus_row(isin), as_of_month="2026-09"
    )
    assert out2.iloc[0]["shareholding_quarter_end"] == "2026-09-30"
    # as_of April: only Q1 visible -> single quarter -> no_data.
    out3 = consensus_signals.join_shareholding_increase(
        conn, _consensus_row(isin), as_of_month="2026-04"
    )
    assert out3.iloc[0]["shareholding_quarter_end"] == "2026-03-31"
    assert out3.iloc[0]["fii_direction"] == "no_data"
    conn.close()


def test_staleness_flag():
    conn = _mem_conn()
    isin = "INE000A01001"
    _add_sh(conn, isin, "2025-09-30", 10.0, 5.0)
    _add_sh(conn, isin, "2025-12-31", 11.0, 5.5)
    out = consensus_signals.join_shareholding_increase(
        conn, _consensus_row(isin), as_of_month="2026-08"
    )
    assert out.iloc[0]["staleness_days"] > 200
    assert bool(out.iloc[0]["shareholding_stale"]) is True
    conn.close()

    conn2 = _mem_conn()
    _add_sh(conn2, isin, "2026-03-31", 10.0, 5.0)
    _add_sh(conn2, isin, "2026-06-30", 11.0, 5.5)
    out2 = consensus_signals.join_shareholding_increase(
        conn2, _consensus_row(isin), as_of_month="2026-08"
    )
    assert out2.iloc[0]["staleness_days"] <= 200
    assert bool(out2.iloc[0]["shareholding_stale"]) is False
    conn2.close()


def test_tristate_and_is_common_requires_increased_direction():
    conn = _mem_conn()
    # FII up, DII down, MF up -> common via FII.
    _add_sh(conn, "INE001A01001", "2026-03-31", 10.0, 5.0)
    _add_sh(conn, "INE001A01001", "2026-06-30", 12.0, 4.0)
    cons = _consensus_row("INE001A01001", net=1)
    out = consensus_signals.join_shareholding_increase(conn, cons)
    r = out.iloc[0]
    assert r["fii_direction"] == "increased"
    assert r["dii_direction"] == "decreased"
    assert bool(r["fii_increased"]) is True
    assert bool(r["dii_increased"]) is False
    assert bool(r["is_common"]) is True
    # MF down even with FII up -> not common.
    out2 = consensus_signals.join_shareholding_increase(
        conn, _consensus_row("INE001A01001", net=-1)
    )
    assert bool(out2.iloc[0]["is_common"]) is False
    conn.close()


# ---- fallback_summary -------------------------------------------------------

def test_bluechip_hdfc_bank_cd_exit_not_claimed_as_equity():
    conn = _mem_conn()
    # Bluechip scheme: one equity exit + one HDFC Bank CD maturity.
    _add_scheme_stock_delta(conn, 289, "ICICI Prudential AMC", "BLUECHIP",
                            "INE093I01010", "Oberoi Realty Ltd.", "equity",
                            -244300, -4465.07, -4465.07, -0.05, "exited")
    _add_scheme_stock_delta(conn, 289, "ICICI Prudential AMC", "BLUECHIP",
                            "INE040A16HO2", "HDFC Bank Ltd.", "cp_or_cd",
                            -10000, -49791.65, -49791.65, -0.61, "exited")
    text = fallback_summary.build_summary(conn, 289, "2026-08")  # default equity
    assert "Oberoi Realty" in text
    assert "HDFC Bank" not in text
    # Unfiltered view must separate non-equity, not claim CD as equity exit.
    text_all = fallback_summary.build_summary(conn, 289, "2026-08", instrument_type=None)
    assert "Oberoi Realty" in text_all
    assert "HDFC Bank" in text_all
    assert "Non-equity" in text_all or "non-equity" in text_all
    conn.close()


def test_bse_nav_066_case():
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 289, "ICICI Prudential AMC", "BLUECHIP",
                            "INE118H01025", "BSE Ltd.", "equity",
                            1619026, 53136.43, 53136.43, 0.66249776321, "new")
    text = fallback_summary.build_summary(conn, 289, "2026-08")
    assert "BSE Ltd." in text
    assert "0.66%" in text
    assert "flow" in text and "value change" in text
    assert "+₹-" not in text
    conn.close()


def test_no_plus_rupee_minus_rendering():
    conn = _mem_conn()
    # Added shares but value fell (flow +, value -): must not render '+₹-'.
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME",
                            "INE000A01001", "Up Qty Down Px", "equity",
                            18400, -870.40, 603.88, -0.03, "added")
    # Trimmed shares but value rose (flow -, value +).
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME",
                            "INE000B01001", "Down Qty Up Px", "equity",
                            -100, 500.0, -1000.0, 0.10, "trimmed")
    text = fallback_summary.build_summary(conn, 1, "2026-08")
    assert "+₹-" not in text
    assert "flow" in text and "value change" in text
    conn.close()


def test_exits_capped_and_deduped():
    conn = _mem_conn(deltas_pk=False)
    for i in range(7):
        conn.execute(
            "INSERT OR IGNORE INTO schemes (scheme_id, amc_name, scheme_name) VALUES (1, 'AMC', 'SCHEME')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type) VALUES (?, ?, ?, ?)",
            (f"INE00{i:04d}01001", f"ExitCo {i}", "Ind", "equity"),
        )
        # ExitCo 0 is the largest exit so it survives the top-5 cap.
        val = -10000 if i == 0 else -(100 + i)
        conn.execute(
            """INSERT INTO mf_holding_deltas
               (scheme_id, isin, report_month, prev_month, qty_change,
                value_change_lakhs, flow_lakhs, pct_nav_change, action, price_effect_lakhs)
               VALUES (1, ?, '2026-08', '2026-07', -100, ?, ?, -0.1, 'exited', 0.0)""",
            (f"INE00{i:04d}01001", val, val),
        )
    # Duplicate ISIN row (table has no PK here): same ISIN as ExitCo 0,
    # must be deduped to one exit.
    conn.execute(
        """INSERT INTO mf_holding_deltas
           (scheme_id, isin, report_month, prev_month, qty_change,
            value_change_lakhs, flow_lakhs, pct_nav_change, action, price_effect_lakhs)
           VALUES (1, 'INE00000001001', '2026-08', '2026-07', -50, -50, -50, -0.05, 'exited', 0.0)"""
    )
    conn.commit()
    text = fallback_summary.build_summary(conn, 1, "2026-08")
    # 7 unique ISINs (one duplicated) -> 5 shown + 'and 2 more'.
    assert "and 2 more" in text
    assert "+₹-" not in text
    # Duplicate name appears only once in the exit list.
    assert text.count("ExitCo 0") == 1
    conn.close()


def test_added_trimmed_rank_by_flow_fallback_value():
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME", "INE000A01001", "LowFlow", "equity",
                            10, 9000, 100, 0.5, "added")
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME", "INE000B01001", "HighFlow", "equity",
                            20, 100, 5000, 0.6, "added")
    text = fallback_summary.build_summary(conn, 1, "2026-08")
    assert "HighFlow" in text  # ranked by flow, not value
    conn.close()


# ---- price effect (Worker A column) ------------------------------------------

def test_consensus_price_totals_and_no_value_total():
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 1, "HDFC AMC", "Top100", "INEAAA01001", "AAA", "equity",
                            10, 100, 70, 0.5, "added", price=30.0)
    _add_scheme_stock_delta(conn, 2, "SBI AMC", "Bluechip", "INEAAA01001", "AAA", "equity",
                            5, 50, 60, 0.3, "added", price=-10.0)
    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert "total_price_effect_lakhs" in out.columns
    assert "total_value_change_lakhs" not in out.columns
    r = out.set_index("isin").loc["INEAAA01001"]
    assert abs(r["total_price_effect_lakhs"] - 20.0) < 1e-9
    assert abs(r["total_flow_lakhs"] - 130.0) < 1e-9
    conn.close()


def test_consensus_legacy_db_without_price_column():
    conn = _mem_conn(with_price_effect=False)
    _add_scheme_stock_delta(conn, 1, "HDFC AMC", "Top100", "INEAAA01001", "AAA", "equity",
                            10, 100, 100, 0.5, "new")
    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert "total_price_effect_lakhs" in out.columns
    assert "total_value_change_lakhs" not in out.columns
    assert abs(out.iloc[0]["total_price_effect_lakhs"] - 0.0) < 1e-9
    assert abs(out.iloc[0]["total_flow_lakhs"] - 100.0) < 1e-9
    conn.close()


def test_consensus_empty_frame_has_price_total():
    conn = _mem_conn()
    out = consensus_signals.compute_consensus(conn, "2026-08")
    assert out.empty
    assert "total_price_effect_lakhs" in out.columns
    assert "total_value_change_lakhs" not in out.columns
    conn.close()


def test_sign_flip_flow_positive_value_negative():
    # Golden-ish: bought shares (flow > 0) while the position value fell
    # (value < 0) because the negative price effect overwhelmed the inflow.
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME", "INE000A01001", "FlipCo", "equity",
                            18400, -870.40, 603.88, -0.03, "added")
    out = consensus_signals.compute_consensus(conn, "2026-08")
    r = out.iloc[0]
    assert r["total_flow_lakhs"] > 0
    assert abs(r["total_price_effect_lakhs"] - (-1474.28)) < 1e-6
    text = fallback_summary.build_summary(conn, 1, "2026-08")
    assert "price effect" in text
    assert "flow" in text and "value change" in text
    assert "+₹-" not in text
    conn.close()


def test_added_detail_shows_price_effect_when_material():
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME", "INE000A01001", "AddCo", "equity",
                            100, 50.0, 200.0, 0.10, "added", price=-150.0)
    text = fallback_summary.build_summary(conn, 1, "2026-08")
    assert "price effect" in text
    assert "flow" in text and "value change" in text
    assert "+₹-" not in text
    conn.close()


def test_new_detail_stays_flow_plus_value():
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 1, "AMC", "SCHEME", "INE000A01001", "NewCo", "equity",
                            100, 500.0, 500.0, 0.5, "new")
    text = fallback_summary.build_summary(conn, 1, "2026-08")
    # flow == value for new positions: no separate price-effect leg.
    assert "price effect" not in text
    assert "flow" in text and "value change" in text
    conn.close()


# ---- narrate template --------------------------------------------------------

def test_template_deterministic_and_validated():
    payload = {
        "scheme_name": "BLUECHIP", "amc_name": "ICICI Prudential AMC",
        "report_month": "2026-08",
        "new_count": 1, "new_largest_name": "BSE Ltd.",
        "new_largest_value_cr": 531.4, "new_largest_flow_cr": 531.4,
        "new_largest_nav_pct": 0.66,
        "added_count": 0, "trimmed_count": 0,
        "exited_names": ["A", "B", "C", "D", "E", "F", "G"],
    }
    first = render_summary(payload)
    second = render_summary(dict(payload))
    assert first == second
    assert "BSE Ltd." in first
    assert "0.66%" in first
    assert "and 2 more" in first
    assert "+₹-" not in first


# ---- Phase 1 pipeline overlap (build_overlap_view) ----------------------------

def test_pipeline_overlap_common_via_end_to_end_consensus():
    conn = _mem_conn()
    _add_scheme_stock_delta(conn, 1, "HDFC AMC", "Top100", "INEAAA01001", "AAA", "equity",
                            10, 100, 100, 0.5, "added")
    _add_scheme_stock_delta(conn, 2, "SBI AMC", "Bluechip", "INEAAA01001", "AAA", "equity",
                            10, 100, 100, 0.5, "added")
    _add_sh(conn, "INEAAA01001", "2026-03-31", 10.0, 5.0)
    _add_sh(conn, "INEAAA01001", "2026-06-30", 12.0, 4.0)  # FII up -> common
    consensus = consensus_signals.compute_consensus(conn, "2026-08")
    assert not consensus.empty
    overlap = build_overlap_view(conn, consensus, "2026-08")
    assert overlap["status"] == "ok"
    assert len(overlap["common"]) == 1
    assert overlap["common"].iloc[0]["isin"] == "INEAAA01001"
    summary = _overlap_summary(overlap["joined"], overlap["common"], overlap["status"])
    assert summary == {
        "status": "ok", "common_count": 1, "consensus_count": 1,
        "stale_count": 0, "missing_shareholding_count": 0,
    }
    conn.close()


def test_pipeline_overlap_no_lookahead():
    conn = _mem_conn()
    # Q2 shows an FII increase; a future Q3 filing reverses it. With a June
    # cutoff the pipeline must rank Q2 (common), not the future quarter.
    _add_sh(conn, "INE000A01001", "2026-03-31", 10.0, 5.0)
    _add_sh(conn, "INE000A01001", "2026-06-30", 12.0, 6.0)
    _add_sh(conn, "INE000A01001", "2026-09-30", 1.0, 1.0)
    overlap = build_overlap_view(conn, _consensus_row("INE000A01001"), "2026-06")
    assert overlap["status"] == "ok"
    assert overlap["joined"].iloc[0]["shareholding_quarter_end"] == "2026-06-30"
    assert bool(overlap["common"].iloc[0]["is_common"]) is True
    conn.close()


def test_pipeline_overlap_empty_consensus_and_missing_table():
    empty = pd.DataFrame([{
        "isin": "INE000A01001", "stock_name": "X",
        "amcs_buying": 0, "amcs_selling": 0,
        "total_flow_lakhs": 0.0, "total_price_effect_lakhs": 0.0,
        "net_amc_count": 0, "buying_ratio": 0.0,
    }]).iloc[0:0]
    conn = _mem_conn()
    overlap = build_overlap_view(conn, empty, "2026-08")
    assert overlap["status"] == "no_consensus"
    conn.close()

    # Legacy DB without the shareholding table: never raises, MF-only survives.
    bare = sqlite3.connect(":memory:")
    overlap2 = build_overlap_view(bare, _consensus_row("INE000A01001"), "2026-08")
    assert overlap2["status"] == "unavailable"
    assert overlap2["joined"].equals(_consensus_row("INE000A01001"))
    summary = _overlap_summary(overlap2["joined"], overlap2["common"], overlap2["status"])
    assert summary == {"status": "unavailable", "common_count": 0}
    bare.close()
