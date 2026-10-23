"""Research pages and summaries share the dashboard's readable names."""

import sqlite3

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
import pytest

from findit.summary import build_summary, get_summary
from findit.store.releases import snapshot
from findit.web.app import create_app
from tests.test_web import _copy_db


_ISIN = "INE002A01018"
_TITLE = "HDFC Mid Cap Fund (An open ended equity scheme)"


def _named_db(tmp_path):
    path = _copy_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE schemes SET scheme_name='MIDCAP', scheme_title=? WHERE scheme_id=1",
                     (_TITLE,))
        conn.execute("UPDATE stocks SET name=' EQ - Reliance Industries ' WHERE isin=?", (_ISIN,))
    return path


def test_summary_shows_the_full_fund_title_and_clean_company_names(tmp_path):
    path = _named_db(tmp_path)
    client = TestClient(create_app(path))
    response = client.get("/api/summary/1/2026-08")
    assert response.status_code == 200
    result = response.json()
    assert result["scheme_name"] == "HDFC Mid Cap Fund"
    assert result["summary"].startswith("HDFC Mid Cap Fund (A AMC)")
    assert "Reliance Industries, up 50 shares" in result["summary"]
    assert "MIDCAP" not in result["summary"] and "EQ -" not in result["summary"]
    # Rendering names must never rewrite the original disclosure data.
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT scheme_name FROM schemes WHERE scheme_id=1").fetchone()[0] == "MIDCAP"
        assert conn.execute("SELECT name FROM stocks WHERE isin=?", (_ISIN,)).fetchone()[0] == " EQ - Reliance Industries "


@pytest.mark.parametrize("month,quarantined,expected", [
    ("2026-07", False, "No holding-change data available for HDFC Mid Cap Fund"),
    ("2026-08", True, "HDFC Mid Cap Fund (A AMC) — 2026-08 data withheld"),
])
def test_unavailable_and_withheld_summaries_keep_the_fund_title(tmp_path, month, quarantined, expected):
    with sqlite3.connect(_named_db(tmp_path)) as conn:
        if quarantined:
            conn.execute("UPDATE scheme_month_status SET status='quarantined' WHERE scheme_id=1")
        assert get_summary(conn, 1, month)["text"].startswith(expected)


def test_summary_falls_back_to_scheme_name_on_a_legacy_database():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE schemes(scheme_id INTEGER, amc_name TEXT, scheme_name TEXT)")
        conn.execute("INSERT INTO schemes VALUES(1,'A AMC','Legacy fund')")
        conn.execute("CREATE TABLE stocks(isin TEXT, name TEXT)")
        conn.execute("INSERT INTO stocks VALUES('INE002A01018','EQ - Reliance Industries')")
        conn.execute("CREATE TABLE mf_holding_deltas(scheme_id INTEGER, isin TEXT, report_month TEXT, "
                     "action TEXT, qty_change REAL, value_change_lakhs REAL, pct_nav_change REAL)")
        conn.execute("INSERT INTO mf_holding_deltas VALUES(1,'INE002A01018','2026-08','added',10,100,1)")
        text = build_summary(conn, 1, "2026-08")
    assert text.startswith("Legacy fund (A AMC)")
    assert "Reliance Industries, up 10 shares" in text and "EQ -" not in text


def test_research_pages_show_clean_company_name_and_keep_the_isin(tmp_path):
    client = TestClient(create_app(_named_db(tmp_path)))
    history = client.get(f"/api/history/{_ISIN}?start=2026-07&end=2026-08").json()
    assert history["stock_name"] == "Reliance Industries"
    urls = [f"/history/{_ISIN}?start=2026-07&end=2026-08",
            f"/evidence/{_ISIN}?month=2026-08"]
    # Includes the first-month snapshot and the fixed-cohort comparison.
    urls.extend(point["evidence_url"] for point in history["points"])
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, response.text
        page = BeautifulSoup(response.text, "html.parser")
        assert page.h1.get_text() == "Reliance Industries"
        assert page.select_one(".evidence-id").get_text() == _ISIN
        assert "EQ -" not in response.text
    evidence = client.get(f"/api/evidence/{_ISIN}?month=2026-08").json()
    assert evidence["stock_name"] == "Reliance Industries"


def test_unknown_company_keeps_a_useful_research_heading(tmp_path):
    client = TestClient(create_app(_named_db(tmp_path)))
    for url, heading in [("/history/INE123A01012?start=2026-07&end=2026-08", "Holding history"),
                         ("/evidence/INE123A01012?month=2026-08", "View evidence")]:
        response = client.get(url)
        assert response.status_code == 200, response.text
        page = BeautifulSoup(response.text, "html.parser")
        assert page.h1.get_text() == heading
        assert page.select_one(".evidence-id").get_text() == "INE123A01012"


def test_research_names_come_from_the_selected_release(tmp_path):
    path = _named_db(tmp_path)
    releases = tmp_path / "releases"
    retained = snapshot(path, releases, "Original company name")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE stocks SET name='EQ - Renamed Industries' WHERE isin=?", (_ISIN,))
    current = snapshot(path, releases, "Updated company name")
    client = TestClient(create_app(releases / f"{current['release_id']}.db"))
    for section, period in [("history", {"start": "2026-07", "end": "2026-08"}),
                            ("evidence", {"month": "2026-08"})]:
        for release, name in [(retained, "Reliance Industries"), (current, "Renamed Industries")]:
            params = {**period, "release": release["release_id"], "rules": release["rule_version"]}
            result = client.get(f"/api/{section}/{_ISIN}", params=params)
            assert result.status_code == 200, result.text
            assert result.json()["stock_name"] == name
            page = client.get(f"/{section}/{_ISIN}", params=params)
            assert BeautifulSoup(page.text, "html.parser").h1.get_text() == name
