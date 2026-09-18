"""Trusted narration facts use temporary SQLite databases, never tracker.db."""

import sqlite3

import pytest

import fallback_summary
from findit.narrate.facts import build_snapshot, render_plan


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "facts.db")
    connection.executescript("""
        CREATE TABLE schemes (scheme_id INTEGER PRIMARY KEY, amc_name TEXT, scheme_name TEXT);
        CREATE TABLE stocks (isin TEXT PRIMARY KEY, name TEXT, instrument_type TEXT);
        CREATE TABLE mf_holdings_monthly (scheme_id INTEGER, isin TEXT, report_month TEXT,
            quantity REAL, market_value_lakhs REAL, pct_nav REAL);
        CREATE TABLE mf_holding_deltas (scheme_id INTEGER, isin TEXT, report_month TEXT,
            prev_month TEXT, qty_change REAL, value_change_lakhs REAL, flow_lakhs REAL,
            price_effect_lakhs REAL, pct_nav_change REAL, action TEXT);
        CREATE TABLE scheme_month_status (scheme_id INTEGER, report_month TEXT, status TEXT,
            validation_report_json TEXT, source_data_hash TEXT);
        INSERT INTO schemes VALUES (1, 'Example AMC', 'Equity Fund');
        INSERT INTO stocks VALUES ('A', 'Stock A', 'equity'), ('B', 'Stock B', 'equity');
        INSERT INTO mf_holdings_monthly VALUES
            (1, 'A', '2026-07', 100, 1000, 10),
            (1, 'A', '2026-08', 120, 1200, 12),
            (1, 'B', '2026-08', 100, 500, 5);
        INSERT INTO mf_holding_deltas VALUES
            (1, 'A', '2026-08', '2026-07', 20, 200, 200, 0, 2, 'added'),
            (1, 'B', '2026-08', '2026-07', 100, 500, 500, 0, 5, 'new');
        INSERT INTO scheme_month_status VALUES
            (1, '2026-07', 'validated', '{}', 'previous-hash'),
            (1, '2026-08', 'ok', '{}', 'current-hash');
    """)
    yield connection
    connection.close()


def test_snapshot_read_only_and_canonical_fallback(conn):
    conn.execute("PRAGMA query_only = ON")
    snapshot = build_snapshot(conn, 1, "2026-08")
    assert snapshot.eligible and snapshot.reason is None
    assert snapshot.fallback_text == fallback_summary.build_summary(conn, 1, "2026-08")
    assert len(snapshot.prompt["facts"]) == 2
    assert snapshot.source_hash == build_snapshot(conn, 1, "2026-08").source_hash
    conn.row_factory = sqlite3.Row
    assert snapshot == build_snapshot(conn, 1, "2026-08")


@pytest.mark.parametrize("sql", [
    "UPDATE mf_holding_deltas SET qty_change=21 WHERE isin='A'",
    "UPDATE mf_holdings_monthly SET quantity=99 WHERE report_month='2026-07'",
    "UPDATE mf_holdings_monthly SET pct_nav=11 WHERE report_month='2026-08'",
    "UPDATE stocks SET name='Renamed stock' WHERE isin='A'",
    "UPDATE stocks SET instrument_type='bond' WHERE isin='A'",
    "UPDATE schemes SET scheme_name='New name'",
    "UPDATE scheme_month_status SET validation_report_json='{\"warning\": true}'",
    "UPDATE scheme_month_status SET source_data_hash='reloaded'",
    "UPDATE scheme_month_status SET status='validated' WHERE report_month='2026-08'",
])
def test_source_hash_changes_for_material_sources(conn, sql):
    before = build_snapshot(conn, 1, "2026-08")
    conn.execute(sql)
    assert before.source_hash != build_snapshot(conn, 1, "2026-08").source_hash


def test_hash_ignores_cache_and_physical_row_order(conn):
    before = build_snapshot(conn, 1, "2026-08")
    conn.executescript("""
        CREATE TABLE fund_summaries (summary_text TEXT, generated_at TEXT);
        INSERT INTO fund_summaries VALUES ('irrelevant', '2099-01-01');
        CREATE TEMP TABLE saved AS SELECT * FROM mf_holding_deltas;
        DELETE FROM mf_holding_deltas;
        INSERT INTO mf_holding_deltas SELECT * FROM saved ORDER BY isin DESC;
    """)
    assert before.source_hash == build_snapshot(conn, 1, "2026-08").source_hash


def test_render_reorders_only_trusted_phrasing(conn):
    snapshot = build_snapshot(conn, 1, "2026-08")
    facts = snapshot.prompt["facts"]
    plan = {"sentences": [{"fact_id": f["id"], "variant": 1} for f in reversed(facts)]}
    rendered = render_plan(snapshot, plan)
    assert rendered == snapshot.prompt["header"] + "\n\n" + " ".join(
        f["variants"][1] for f in reversed(facts)
    )
    assert "₹5.0 Cr" in rendered and "20 shares" in rendered


@pytest.mark.parametrize("plan", [
    None, [], {}, {"sentences": [], "text": "invented"},
    {"sentences": "not a list"}, {"sentences": []},
    {"sentences": [{"fact_id": "f0", "variant": 0}]},
    {"sentences": [{"fact_id": "f0", "variant": 0}, {"fact_id": "f0", "variant": 1}]},
    {"sentences": [{"fact_id": "unknown", "variant": 0}]},
    {"sentences": [{"fact_id": [], "variant": 0}]},
    {"sentences": [{"fact_id": "f0", "variant": True}]},
    {"sentences": [{"fact_id": "f0", "variant": 0.0}]},
    {"sentences": [{"fact_id": "f0", "variant": -1}]},
    {"sentences": [{"fact_id": "f0", "variant": 2}]},
    {"sentences": [{"fact_id": "f0", "variant": 0, "text": "invented"}]},
    {"sentences": ["invented"]},
])
def test_render_rejects_invalid_plans(conn, plan):
    with pytest.raises(ValueError):
        render_plan(build_snapshot(conn, 1, "2026-08"), plan)


@pytest.mark.parametrize("month", ["2026-07", "2026-08"])
def test_quarantined_comparison_withholds_summary(conn, month):
    conn.execute("UPDATE scheme_month_status SET status='quarantined' WHERE report_month=?", (month,))
    snapshot = build_snapshot(conn, 1, "2026-08")
    assert not snapshot.eligible and snapshot.reason == "quarantined"
    assert "data withheld" in snapshot.fallback_text
    assert "Not zero activity" in snapshot.fallback_text
    assert "₹" not in snapshot.fallback_text
    assert snapshot.prompt["facts"] == []
    with pytest.raises(ValueError):
        render_plan(snapshot, {"sentences": []})


def test_unvalidated_keeps_normal_fallback(conn):
    conn.execute("DELETE FROM scheme_month_status WHERE report_month='2026-08'")
    snapshot = build_snapshot(conn, 1, "2026-08")
    assert not snapshot.eligible and snapshot.reason == "unvalidated"
    assert snapshot.fallback_text == fallback_summary.build_summary(conn, 1, "2026-08")


def test_legacy_optional_tables_missing(conn):
    conn.execute("DROP TABLE scheme_month_status")
    conn.execute("DROP TABLE mf_holdings_monthly")
    snapshot = build_snapshot(conn, 1, "2026-08")
    assert not snapshot.eligible and snapshot.reason == "unvalidated"
    assert "portfolio changes" in snapshot.fallback_text


def test_no_data(conn):
    conn.execute("DELETE FROM mf_holding_deltas")
    snapshot = build_snapshot(conn, 1, "2026-08")
    assert not snapshot.eligible and snapshot.reason == "no_data"
    assert "No holding-change data" in snapshot.fallback_text


@pytest.mark.parametrize("column,value", [
    ("qty_change", float("inf")), ("value_change_lakhs", float("-inf")),
    ("pct_nav_change", None), ("flow_lakhs", float("inf")),
    ("price_effect_lakhs", float("inf")),
])
def test_nonfinite_required_or_supplied_values_withhold(conn, column, value):
    conn.execute(f"UPDATE mf_holding_deltas SET {column}=? WHERE isin='A'", (value,))
    snapshot = build_snapshot(conn, 1, "2026-08")
    assert not snapshot.eligible
    assert "data withheld" in snapshot.fallback_text
    assert "₹" not in snapshot.fallback_text


def test_legacy_missing_optional_flow_values(conn):
    conn.execute("UPDATE mf_holding_deltas SET flow_lakhs=NULL, price_effect_lakhs=NULL")
    assert build_snapshot(conn, 1, "2026-08").eligible


def test_unknown_scheme(conn):
    with pytest.raises(ValueError, match="No scheme"):
        build_snapshot(conn, 99, "2026-08")


def test_hash_changes_when_trusted_wording_changes(conn, monkeypatch):
    import findit.narrate.facts as facts
    before = build_snapshot(conn, 1, "2026-08")
    original = facts._variants
    monkeypatch.setattr(facts, "_variants", lambda sentence: original(sentence)[:1])
    assert build_snapshot(conn, 1, "2026-08").source_hash != before.source_hash
