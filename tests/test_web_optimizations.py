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


def test_month_cache_reuses_work_and_isolates_filters_and_wal_updates(tmp_path, monkeypatch):
    import sqlite3
    from findit.web import app as web
    db = _copy_db(tmp_path)
    overview, coverage = web._month_overview, web.month_coverage
    calls = {"overview": 0, "coverage": 0}
    def counted_overview(*args):
        calls["overview"] += 1
        return overview(*args)
    def counted_coverage(*args):
        calls["coverage"] += 1
        return coverage(*args)
    monkeypatch.setattr(web, "_month_overview", counted_overview)
    monkeypatch.setattr(web, "month_coverage", counted_coverage)
    client = TestClient(create_app(db))
    url = "/fragments/month/2026-08"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert calls == {"overview": 1, "coverage": 1}
    client.get(url + "?equity_only=0")
    assert calls == {"overview": 1, "coverage": 2}
    client.get(url + "?active_only=0")
    assert calls == {"overview": 2, "coverage": 3}
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE schemes SET amc_name='Changed AMC' WHERE scheme_id=1")
        writer.commit()
        assert client.get(url).status_code == 200
        assert calls == {"overview": 3, "coverage": 4}


def test_cache_identity_stays_with_open_file_after_atomic_replacement(tmp_path, monkeypatch):
    import os
    import sqlite3
    from findit.web import app as web
    db = _copy_db(tmp_path)
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(db) as source, sqlite3.connect(replacement) as target:
        source.backup(target)
        target.execute("UPDATE schemes SET amc_name='Replacement AMC' WHERE scheme_id=1")
    connect, overview = web._ro_connect, web._month_overview
    calls = []
    def opening(*args):
        conn = connect(*args)
        if replacement.exists():
            os.replace(replacement, db)
        return conn
    def counted(*args):
        calls.append(args[0].execute("SELECT amc_name FROM schemes WHERE scheme_id=1").fetchone()[0])
        return overview(*args)
    monkeypatch.setattr(web, "_ro_connect", opening)
    monkeypatch.setattr(web, "_month_overview", counted)
    client = TestClient(create_app(db))
    assert client.get("/fragments/month/2026-08").status_code == 200
    assert client.get("/fragments/month/2026-08").status_code == 200
    assert calls == ["A AMC", "Replacement AMC"]


def test_readonly_open_accepts_sqlite_initializing_empty_wal(tmp_path):
    import sqlite3
    db = _copy_db(tmp_path)
    with sqlite3.connect(db) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
    writer.close()
    client = TestClient(create_app(db))
    assert client.get("/fragments/month/2026-08").status_code == 200
    assert client.get("/fragments/month/2026-08").status_code == 200


def test_fund_catalog_is_loaded_separately_from_initial_page(tmp_path):
    import sqlite3
    db = _copy_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO schemes(amc_name,scheme_name) VALUES ('Catalog AMC',?)",
                         [(f"Catalog Fund {i}",) for i in range(1000)])
    client = TestClient(create_app(db))
    page = client.get("/").text
    assert 'id="scheme-search"' in page and 'id="scheme-data"' not in page
    assert "Catalog Fund 999" not in page
    catalog = client.get("/api/schemes").json()
    assert catalog["count"] == 1003
    fund = next(s for s in catalog["schemes"] if s["scheme_name"] == "Catalog Fund 999")
    assert fund["label"] == "Catalog Fund 999" and fund["votes"] is True
