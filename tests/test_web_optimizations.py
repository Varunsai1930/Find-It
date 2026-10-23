"""Regression checks for dashboard performance and delivery."""
from fastapi.testclient import TestClient

from findit.web.app import create_app
from tests.test_web import _copy_db
from tools.benchmark_web import benchmark


def test_local_benchmark_measures_page_search_and_filter_queries(tmp_path):
    db = _copy_db(tmp_path)
    report = benchmark(db, "2026-08", rounds=2)
    assert len(report["samples"]) == 9
    assert set(report["metrics"]) == {"page", "search", "filters"}
    assert all(s["sql_statements"] > 0 and s["bytes"] > 0 for s in report["samples"])
    response = TestClient(create_app(db)).get("/")
    assert float(response.headers["server-timing"].split("=")[-1]) >= 0


def test_month_and_stock_lookups_use_covering_indexes(tmp_path):
    import sqlite3
    db = _copy_db(tmp_path)
    with sqlite3.connect(db) as conn:
        month_plan = str(conn.execute("EXPLAIN QUERY PLAN SELECT isin, scheme_id FROM "
                                     "mf_holdings_monthly WHERE report_month=?", ("2026-08",)).fetchall())
        stock_plan = str(conn.execute("EXPLAIN QUERY PLAN SELECT COUNT(DISTINCT scheme_id) "
                                     "FROM mf_holdings_monthly WHERE isin=? AND report_month=?",
                                     ("INE002A01018", "2026-08")).fetchall())
    assert "SEARCH" in month_plan and "COVERING INDEX idx_holdings_month_stock" in month_plan
    assert "SEARCH" in stock_plan and "COVERING INDEX" in stock_plan


def test_search_counts_distinct_funds_across_months_and_keeps_unheld_matches(tmp_path):
    import sqlite3
    db = _copy_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO stocks VALUES ('INE999A01010', 'Reliance New', 'Test', 'equity')")
    client = TestClient(create_app(db))
    for month in (None, "2026-08"):
        rows = client.get("/api/stocks/search", params={"q": "reliance", **({"month": month} if month else {})}).json()["results"]
        assert [(r["isin"], r["schemes_holding"]) for r in rows] == [("INE002A01018", 3), ("INE999A01010", 0)]


def test_search_preserves_legacy_release_plan_without_stock_index(tmp_path):
    import sqlite3
    from findit.web.app import _find_stocks, _ro_connect
    db = _copy_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP INDEX idx_holdings_stock_month")
        conn.execute("DROP INDEX idx_holdings_month_stock")
    conn = _ro_connect(str(db))
    statements = []
    try:
        conn.set_trace_callback(statements.append)
        rows = _find_stocks(conn, "reliance", "2026-08")
        assert rows[0]["schemes_holding"] == 3
        assert any("GROUP BY isin" in sql for sql in statements)
        assert not any("WHERE h.isin = s.isin" in sql for sql in statements)
    finally:
        conn.close()
