"""Gate integration tests: publication, peer evidence and provenance. tmp DBs only."""
import json

import pytest

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
    # Over-counting NAV is still a genuine publication-blocking error.
    _add_holding(conn, sid, EQUITY, CURR, 1000.0, 100.0, 130.0)
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


def _peer_holdings(conn, ratios, price_ratios=None):
    """Seed equal previous prices, independently varying quantity and price."""
    _add_stock(conn)
    if price_ratios is None:
        price_ratios = [1.0] * len(ratios)
    sids = []
    for i, (ratio, price_ratio) in enumerate(zip(ratios, price_ratios)):
        sid = _add_scheme(conn, f"AMC {i}", f"Active Flexi Fund {i}")
        _add_holding(conn, sid, EQUITY, PREV, 1000.0, 100.0, 100.0)
        _add_holding(conn, sid, EQUITY, CURR, 1000 * ratio,
                     100 * ratio * price_ratio, 100.0)
        sids.append(sid)
    return sids


def _touched(sids):
    return {(sid, CURR): {"files": ["test.csv"], "hashes": ["test-hash"]}
            for sid in sids}


def test_extreme_trade_with_flat_peers_stays_published(tmp_path):
    conn = _conn(tmp_path)
    sids = _peer_holdings(conn, [10.0, 1.0, 1.0])
    # Peers already in the database count even when not touched this run.
    report = run_validation_gate(conn, _touched(sids[:1]))
    outcome = report["scheme_months"][0]
    assert outcome["status"] == "ok"
    qty_issues = [i for i in outcome["issues"] if i["code"].startswith("qty_ratio")]
    assert len(qty_issues) == 1
    assert qty_issues[0]["code"] == "qty_ratio_out_of_band"
    assert qty_issues[0]["severity"] == "warn"
    assert not report["price_warnings"]
    assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 0

    stored = conn.execute(
        "SELECT status, validation_report_json FROM scheme_month_status "
        "WHERE scheme_id=? AND report_month=?", (sids[0], CURR)
    ).fetchone()
    assert stored[0] == "ok"
    assert qty_issues[0] in json.loads(stored[1])["issues"]
    delta_calculator.persist_deltas(conn, delta_calculator.compute_deltas(conn, PREV, CURR))
    consensus = consensus_signals.compute_consensus(conn, CURR)
    stock = consensus[consensus["isin"] == EQUITY].iloc[0]
    assert int(stock["amcs_buying"]) == 1
    summary = fallback_summary.build_summary(conn, sids[0], CURR)
    assert "withheld" not in summary
    assert "Equity Co" in summary
    conn.close()


@pytest.mark.parametrize("inverse_price", [True, False])
def test_peer_agreement_requires_inverse_price_to_record_candidate(tmp_path, inverse_price):
    conn = _conn(tmp_path)
    ratios = [2.04, 2.04, 2.04]
    price_ratios = [1 / 2.04 if inverse_price else 1.0] * 3
    sids = _peer_holdings(conn, ratios, price_ratios)
    report = run_validation_gate(conn, _touched(sids))
    assert {o["status"] for o in report["scheme_months"]} == {"ok"}
    for outcome in report["scheme_months"]:
        qty_issues = [i for i in outcome["issues"] if i["code"].startswith("qty_ratio")]
        assert len(qty_issues) == 1
        assert qty_issues[0]["code"] == "qty_ratio_corporate_action_candidate"
        assert qty_issues[0]["severity"] == "warn"
    rows = conn.execute(
        "SELECT isin, effective_month, confirmed, detected_by FROM corporate_actions"
    ).fetchall()
    assert rows == ([(EQUITY, CURR, 0, "peer_ratio_agreement")] if inverse_price else [])
    # Repeated detections across holders and reruns produce a single row.
    run_validation_gate(conn, _touched(sids))
    assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == len(rows)
    conn.close()


def test_single_holder_is_not_its_own_peer(tmp_path):
    conn = _conn(tmp_path)
    sids = _peer_holdings(conn, [2.04], [1 / 2.04])
    report = run_validation_gate(conn, _touched(sids))
    assert report["scheme_months"][0]["status"] == "ok"
    codes = {i["code"] for i in report["scheme_months"][0]["issues"]}
    assert "qty_ratio_out_of_band" in codes
    assert "qty_ratio_corporate_action_candidate" not in codes
    assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 0
    conn.close()


def test_missing_market_value_retains_peer_warning_without_candidate(tmp_path):
    conn = _conn(tmp_path)
    sids = _peer_holdings(conn, [2.04] * 3, [1 / 2.04] * 3)
    _add_holding(conn, sids[0], EQUITY, CURR, 2040.0, None, 100.0)
    # Only validate the holder lacking a price; its peers still supply ratios.
    report = run_validation_gate(conn, _touched(sids[:1]))
    outcome = report["scheme_months"][0]
    assert outcome["status"] == "ok"
    assert any(i["code"] == "qty_ratio_corporate_action_candidate"
               and i["severity"] == "warn" for i in outcome["issues"])
    assert not any(i["code"] == "gate_crashed" for i in outcome["issues"])
    assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 0
    conn.close()


def test_rerun_preserves_confirmed_corporate_action(tmp_path):
    conn = _conn(tmp_path)
    sids = _peer_holdings(conn, [2.04] * 3, [1 / 2.04] * 3)
    conn.execute(
        "INSERT INTO corporate_actions "
        "(isin, effective_month, kind, ratio, detected_by, confirmed) "
        "VALUES (?, ?, 'reviewed_split', 2.0, 'manual_review', 1)", (EQUITY, CURR)
    )
    conn.commit()
    run_validation_gate(conn, _touched(sids))
    assert conn.execute(
        "SELECT kind, ratio, detected_by, confirmed FROM corporate_actions"
    ).fetchall() == [("reviewed_split", 2.0, "manual_review", 1)]
    conn.close()


def test_low_nav_warns_and_retains_dropped_row_provenance(tmp_path):
    conn = _conn(tmp_path)
    sids = _peer_holdings(conn, [1.0])
    sid = sids[0]
    _add_holding(conn, sid, EQUITY, CURR, 1000.0, 100.0, 4.0)
    touched = _touched(sids)
    touched[(sid, CURR)].update({
        "dropped_non_isin_count": 3,
        "dropped_non_isin_pct_nav": 96.0,
    })
    report = run_validation_gate(conn, touched)
    outcome = report["scheme_months"][0]
    assert outcome["status"] == "ok"
    assert any(i["code"] == "nav_sum_out_of_band" and i["severity"] == "warn"
               for i in outcome["issues"])
    assert not any(i["code"] == "nav_sum_quarantine" for i in outcome["issues"])
    stored = conn.execute(
        "SELECT validation_report_json FROM scheme_month_status "
        "WHERE scheme_id=? AND report_month=?", (sid, CURR)
    ).fetchone()[0]
    provenance = json.loads(stored)
    assert provenance["nav_sum"] == pytest.approx(4.0)
    assert provenance["dropped_non_isin_count"] == 3
    assert provenance["dropped_non_isin_pct_nav"] == pytest.approx(96.0)
    assert "withheld" not in fallback_summary.build_summary(conn, sid, CURR)
    conn.close()


def test_inconsistent_implied_prices_still_warn(tmp_path):
    conn = _conn(tmp_path)
    sids = _peer_holdings(conn, [1.0, 1.0, 1.0], [1.0, 1.0, 100.0])
    report = run_validation_gate(conn, _touched(sids))
    assert {o["status"] for o in report["scheme_months"]} == {"ok"}
    assert any(i["code"] == "price_cv_high" and i["severity"] == "warn"
               and i["isin"] == EQUITY for i in report["price_warnings"])
    conn.close()
