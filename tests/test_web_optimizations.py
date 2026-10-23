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
