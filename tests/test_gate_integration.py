"""Gate integration tests: quarantine + passive exclusion. tmp DBs only."""
import consensus_signals
import db
import delta_calculator
import fallback_summary
from run_pipeline import run_validation_gate

PREV = "2026-03"
CURR = "2026-04"
EQUITY = "INE002A01018"


def _conn(tmp_path, name="g.db"):
    return db.get_connection(str(tmp_path / name))


def _add_scheme(conn, amc, scheme):
    conn.execute(
        "INSERT INTO schemes (amc_name, scheme_name) VALUES (?, ?)", (amc, scheme)
    )
    conn.commit()
    db.backfill_is_active_equity(conn)
    return conn.execute(
        "SELECT scheme_id FROM schemes WHERE amc_name=? AND scheme_name=?",
        (amc, scheme),
    ).fetchone()[0]


def _add_stock(conn, isin=EQUITY, name="Equity Co"):
    conn.execute(
        "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type)"
        " VALUES (?, ?, ?, ?)",
        (isin, name, "X", "equity"),
    )
    conn.commit()


def _add_holding(conn, sid, isin, month, qty, mv, nav):
    conn.execute(
        "INSERT OR REPLACE INTO mf_holdings_monthly"
        " (scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (sid, isin, month, qty, mv, nav),
    )
    conn.commit()


def test_far_outside_nav_sum_quarantined_and_hidden(tmp_path):
    conn = _conn(tmp_path)
    sid = _add_scheme(conn, "Test AMC", "Active Flexi Fund")
    _add_stock(conn)
    _add_holding(conn, sid, EQUITY, PREV, 1000.0, 100.0, 100.0)
    # Curr month sums to 5.0 — far outside any sane range.
    _add_holding(conn, sid, EQUITY, CURR, 1000.0, 100.0, 5.0)
    touched = {(sid, CURR): {"files": ["t.csv"], "hashes": ["h"]}}

    report = run_validation_gate(conn, touched)
    assert report["scheme_months"][0]["status"] == "quarantined"
    status = conn.execute(
        "SELECT status FROM scheme_month_status WHERE scheme_id=? AND report_month=?",
        (sid, CURR),
    ).fetchone()[0]
    assert status == "quarantined"
    run = conn.execute("SELECT status FROM ingest_runs").fetchall()
    assert not run  # run_validation_gate alone writes no ingest_runs row

    delta_calculator.persist_deltas(
        conn, delta_calculator.compute_deltas(conn, PREV, CURR)
    )
    consensus = consensus_signals.compute_consensus(conn, CURR)
    assert EQUITY not in set(consensus["isin"]) if not consensus.empty else True
    summary = fallback_summary.build_summary(conn, sid, CURR)
    assert "withheld" in summary and "quarantined" in summary
    conn.close()


def test_passive_excluded_from_consensus_but_kept_in_summary(tmp_path):
    conn = _conn(tmp_path)
    active = _add_scheme(conn, "Test AMC", "Active Flexi Fund")
    passive = _add_scheme(conn, "Passive AMC", "Nifty 50 Index Fund")
    flags = dict(
        conn.execute("SELECT scheme_name, is_active_equity FROM schemes").fetchall()
    )
    assert flags["Active Flexi Fund"] == 1
    assert flags["Nifty 50 Index Fund"] == 0
    _add_stock(conn)
    for sid in (active, passive):
        _add_holding(conn, sid, EQUITY, PREV, 1000.0, 100.0, 50.0)
        _add_holding(conn, sid, EQUITY, CURR, 1200.0, 120.0, 50.0)
    # Second holding so NAV sums to 100 (sane) for both schemes.
    conn.execute(
        "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type)"
        " VALUES ('INE009A01021', 'Other Equity', 'X', 'equity')"
    )
    for sid in (active, passive):
        _add_holding(conn, sid, "INE009A01021", PREV, 500.0, 50.0, 50.0)
        _add_holding(conn, sid, "INE009A01021", CURR, 500.0, 50.0, 50.0)
    conn.commit()
    touched = {
        (active, CURR): {"files": ["a.csv"], "hashes": ["h"]},
        (passive, CURR): {"files": ["p.csv"], "hashes": ["h"]},
    }
    report = run_validation_gate(conn, touched)
    assert {o["status"] for o in report["scheme_months"]} == {"ok"}
    delta_calculator.persist_deltas(
        conn, delta_calculator.compute_deltas(conn, PREV, CURR)
    )

    # Default: passive AMC's vote excluded.
    consensus = consensus_signals.compute_consensus(conn, CURR)
    row = consensus[consensus["isin"] == EQUITY].iloc[0]
    assert int(row["amcs_buying"]) == 1
    # Opt-out restores old count.
    consensus_all = consensus_signals.compute_consensus(
        conn, CURR, active_equity_only=False
    )
    row_all = consensus_all[consensus_all["isin"] == EQUITY].iloc[0]
    assert int(row_all["amcs_buying"]) == 2
    # Passive fund's own summary is untouched by the consensus filter.
    summary = fallback_summary.build_summary(conn, passive, CURR)
    assert "withheld" not in summary and "Nifty 50 Index Fund" in summary
    conn.close()
