"""Store tests. tmp_path SQLite copies only — never ./tracker.db."""

import json
import sqlite3

import pandas as pd
import pytest

from findit.store.repository import connect, fetch_holdings, ingest_dataframe, migrate


ISIN_A = "INE002A01018"  # equity (series 01)
ISIN_B = "INE009A01021"  # equity (series 01)


def _df(rows, *, amc="HDFC AMC", scheme="Flexi Cap", month="2026-03"):
    base = []
    for r in rows:
        base.append(
            {
                "isin": r["isin"],
                "instrument_name": r.get("name", f"Name {r['isin']}"),
                "quantity": r["quantity"],
                "market_value_lakhs": r["market_value_lakhs"],
                "pct_nav": r["pct_nav"],
                "scheme_name": scheme,
                "amc_name": amc,
                "report_month": month,
            }
        )
    return pd.DataFrame(base)


def _tables(conn):
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _holding_cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(mf_holdings_monthly)").fetchall()}


def _delta_cols(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(mf_holding_deltas)").fetchall()}


def _sh_cols(conn):
    return {
        r[1] for r in conn.execute("PRAGMA table_info(shareholding_quarterly)").fetchall()
    }


# ---- migrate idempotence ----------------------------------------------------


def test_migrate_idempotent(tmp_path):
    path = tmp_path / "m.db"
    conn = connect(str(path))
    migrate(conn)
    migrate(conn)  # second run must not error
    tables = _tables(conn)
    for t in [
        "instruments",
        "scheme_aliases",
        "instrument_prices_monthly",
        "corporate_actions",
        "mf_holding_flows",
        "consensus_signals",
        "fund_summaries",
        "ingest_runs",
        "scheme_month_status",
        "schemes",
        "stocks",
        "mf_holdings_monthly",
        "mf_holding_deltas",
        "shareholding_quarterly",
    ]:
        assert t in tables, f"missing table {t}"
    hcols = _holding_cols(conn)
    for c in ("pct_nav_raw", "pct_nav_scale", "source_file_hash", "ingest_run_id"):
        assert c in hcols, f"holdings missing {c}"
    scols = _sh_cols(conn)
    for c in ("filing_type", "validation_status", "source_url", "source_sha256"):
        assert c in scols, f"shareholding missing {c}"
    dcols = _delta_cols(conn)
    assert "price_effect_lakhs" in dcols, "mf_holding_deltas missing price_effect_lakhs"
    conn.close()


# ---- read_only never migrates ----------------------------------------------


def test_read_only_never_migrates(tmp_path):
    # Missing file + read_only must raise and must NOT create the file.
    missing = tmp_path / "missing.db"
    with pytest.raises(Exception):
        connect(str(missing), read_only=True)
    assert not missing.exists()

    # Seeded file with only a legacy table: read_only must not add v2 tables.
    path = tmp_path / "ro.db"
    raw = sqlite3.connect(str(path))
    raw.execute(
        "CREATE TABLE schemes (scheme_id INTEGER PRIMARY KEY, amc_name TEXT, scheme_name TEXT)"
    )
    raw.commit()
    raw.close()

    ro = connect(str(path), read_only=True)
    assert ro.execute("PRAGMA query_only;").fetchone()[0] == 1
    tables = _tables(ro)
    assert "instruments" not in tables
    assert "scheme_month_status" not in tables
    with pytest.raises(Exception):
        ro.execute("CREATE TABLE should_fail (x TEXT)")
    ro.close()
    # Re-open writable and confirm the failed write left no trace.
    check = sqlite3.connect(str(path))
    assert "should_fail" not in _tables(check)
    check.close()


# ---- quarantine blocks bad month --------------------------------------------


def test_quarantine_blocks_bad_month(tmp_path):
    path = tmp_path / "q.db"
    conn = connect(str(path))

    good = _df(
        [
            {"isin": ISIN_A, "quantity": 1000.0, "market_value_lakhs": 60.0, "pct_nav": 60.0},
            {"isin": ISIN_B, "quantity": 500.0, "market_value_lakhs": 40.0, "pct_nav": 40.0},
        ],
        month="2026-03",
    )
    res = ingest_dataframe(conn, good, "hash-good")
    assert res["quarantined"] == []

    bad = _df(
        [
            # A 10x qty jump may be ordinary trading and must stay publishable.
            {"isin": ISIN_A, "quantity": 10000.0, "market_value_lakhs": 600.0, "pct_nav": 60.0},
            {"isin": ISIN_B, "quantity": 500.0, "market_value_lakhs": 40.0, "pct_nav": 40.0},
        ],
        month="2026-04",
    )
    res2 = ingest_dataframe(conn, bad, "hash-bad")
    assert res2["quarantined"] == []

    scheme_id = conn.execute(
        "SELECT scheme_id FROM schemes WHERE amc_name='HDFC AMC' AND scheme_name='Flexi Cap'"
    ).fetchone()[0]
    st = conn.execute(
        "SELECT status, validation_report_json, source_data_hash FROM scheme_month_status "
        "WHERE scheme_id=? AND report_month='2026-04'",
        (scheme_id,),
    ).fetchone()
    assert st is not None
    assert st[0] == "validated"
    assert st[2] == "hash-bad"
    report = json.loads(st[1])
    assert report["passed"] is True
    assert any(i["code"] == "qty_ratio_out_of_band" and i["severity"] == "warn"
               for i in report["issues"])

    assert len(fetch_holdings(conn, "2026-04", validated_only=True)) == 2
    assert len(fetch_holdings(conn, "2026-03", validated_only=True)) == 2

    # Real over-counting still quarantines and is hidden by the validated view.
    over_sum = bad.copy()
    over_sum["report_month"] = "2026-05"
    over_sum["pct_nav"] = [90.0, 40.0]
    res3 = ingest_dataframe(conn, over_sum, "hash-over-sum")
    assert len(res3["quarantined"]) == 1
    assert len(fetch_holdings(conn, "2026-05", validated_only=True)) == 0
    status, report_json = conn.execute(
        "SELECT status, validation_report_json FROM scheme_month_status "
        "WHERE scheme_id=? AND report_month='2026-05'", (scheme_id,)
    ).fetchone()
    assert status == "quarantined"
    assert json.loads(report_json)["passed"] is False
    conn.close()


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


# ---- atomic reload removes stale rows ---------------------------------------


def test_atomic_reload_removes_stale_rows(tmp_path):
    path = tmp_path / "a.db"
    conn = connect(str(path))

    first = _df(
        [
            {"isin": ISIN_A, "quantity": 1000.0, "market_value_lakhs": 60.0, "pct_nav": 60.0},
            {"isin": ISIN_B, "quantity": 500.0, "market_value_lakhs": 40.0, "pct_nav": 40.0},
        ],
        month="2026-06",
    )
    ingest_dataframe(conn, first, "h1")
    assert len(fetch_holdings(conn, "2026-06")) == 2

    # Reload same scheme-month with only one holding: whole snapshot replace.
    # (pct_nav sums to 100: a valid full snapshot, so validation passes.)
    second = _df(
        [
            {"isin": ISIN_A, "quantity": 1000.0, "market_value_lakhs": 60.0, "pct_nav": 100.0},
        ],
        month="2026-06",
    )
    ingest_dataframe(conn, second, "h2")
    got = fetch_holdings(conn, "2026-06")
    assert len(got) == 1
    assert set(got["isin"].tolist()) == {ISIN_A}
    conn.close()


# ---- raw provenance preserved -----------------------------------------------


def test_raw_provenance_preserved(tmp_path):
    path = tmp_path / "p.db"
    conn = connect(str(path))

    # Fraction-scale sheet (sums to 1.0) must keep raw + normalized + scale.
    frac = _df(
        [
            {"isin": ISIN_A, "quantity": 1000.0, "market_value_lakhs": 60.0, "pct_nav": 0.6},
            {"isin": ISIN_B, "quantity": 500.0, "market_value_lakhs": 40.0, "pct_nav": 0.4},
        ],
        month="2026-07",
    )
    res = ingest_dataframe(conn, frac, "abc123")
    run_id = res["run_id"]
    assert run_id

    rows = conn.execute(
        "SELECT isin, pct_nav, pct_nav_raw, pct_nav_scale, source_file_hash, ingest_run_id "
        "FROM mf_holdings_monthly WHERE report_month='2026-07' ORDER BY isin"
    ).fetchall()
    assert len(rows) == 2
    by_isin = {r[0]: r for r in rows}
    assert by_isin[ISIN_A][2] == pytest.approx(0.6)  # raw preserved
    assert by_isin[ISIN_A][1] == pytest.approx(60.0)  # normalized to percent
    assert by_isin[ISIN_A][3] == "fraction"
    assert by_isin[ISIN_A][4] == "abc123"
    assert by_isin[ISIN_A][5] == run_id
    assert by_isin[ISIN_B][2] == pytest.approx(0.4)
    assert by_isin[ISIN_B][1] == pytest.approx(40.0)

    # ingest_runs row exists for this run.
    run = conn.execute(
        "SELECT run_id, status FROM ingest_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert run is not None
    assert run[0] == run_id
    conn.close()
