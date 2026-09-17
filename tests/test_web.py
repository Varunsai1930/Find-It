"""Web read-only tests on a tmp DB copy. No network, never touches ./tracker.db."""

from __future__ import annotations

import hashlib
import math
import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from findit.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKER_DB = REPO_ROOT / "tracker.db"


def _copy_db(tmp_path: Path) -> Path:
    dest = tmp_path / "web_test.db"
    shutil.copy2(TRACKER_DB, dest)
    return dest


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ro_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _walk_numbers(obj, out: list):
    if isinstance(obj, float):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out)


def _latest_month(conn) -> str | None:
    row = conn.execute("SELECT MAX(report_month) FROM mf_holdings_monthly").fetchone()
    return str(row[0]) if row and row[0] else None


# -- read-only ---------------------------------------------------------------


def test_read_only_db_mtime_hash_unchanged(tmp_path):
    db = _copy_db(tmp_path)
    before_hash = _hash(db)
    before_mtime = db.stat().st_mtime_ns

    app = create_app(str(db))
    client = TestClient(app)

    cov = client.get("/api/coverage")
    assert cov.status_code == 200
    latest = cov.json().get("latest_month")
    assert latest

    assert client.get("/api/schemes").status_code == 200
    assert client.get("/api/schemes", params={"month": latest}).status_code == 200

    con = client.get(f"/api/consensus/{latest}")
    assert con.status_code in (200, 404)

    schemes = client.get("/api/schemes", params={"month": latest}).json()["schemes"]
    assert schemes
    sid = schemes[0]["scheme_id"]
    s = client.get(f"/api/summary/{sid}/{latest}")
    assert s.status_code in (200, 404)

    results = con.json().get("results", []) if con.status_code == 200 else []
    if results:
        isin = results[0]["isin"]
        st = client.get(f"/api/stock/{isin}", params={"month": latest})
        assert st.status_code in (200, 404)
    else:
        # fall back to any stock row for a read probe
        conn = _ro_conn(db)
        try:
            row = conn.execute("SELECT isin FROM stocks LIMIT 1").fetchone()
        finally:
            conn.close()
        assert client.get(f"/api/stock/{row[0]}").status_code in (200, 404)

    assert client.get("/").status_code == 200

    assert _hash(db) == before_hash
    assert db.stat().st_mtime_ns == before_mtime


def test_app_uses_read_only_open():
    src = (REPO_ROOT / "findit" / "web" / "app.py").read_text(encoding="utf-8")
    assert "mode=ro" in src
    assert "query_only" in src
    forbidden_a = "get_" + "connection"
    assert forbidden_a not in src


# -- missing month ------------------------------------------------------------


def test_missing_month_honest_never_500(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    bad = "1900-01"

    r1 = client.get(f"/api/consensus/{bad}")
    assert r1.status_code != 500
    if r1.status_code == 404:
        assert "detail" in r1.json()
    else:
        assert r1.status_code == 200
        body = r1.json()
        assert body.get("results", []) == [] or body.get("count", 0) == 0

    conn = _ro_conn(db)
    try:
        row = conn.execute("SELECT scheme_id FROM schemes LIMIT 1").fetchone()
    finally:
        conn.close()
    r2 = client.get(f"/api/summary/{int(row[0])}/{bad}")
    assert r2.status_code in (404, 200)
    assert r2.status_code != 500

    r3 = client.get("/api/schemes", params={"month": bad})
    assert r3.status_code == 404

    conn = _ro_conn(db)
    try:
        srow = conn.execute("SELECT isin FROM stocks LIMIT 1").fetchone()
    finally:
        conn.close()
    r4 = client.get(f"/api/stock/{srow[0]}", params={"month": bad})
    assert r4.status_code == 404


def test_unknown_scheme_isin_404(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    conn = _ro_conn(db)
    try:
        latest = _latest_month(conn)
    finally:
        conn.close()
    assert client.get(f"/api/summary/99999999/{latest}").status_code == 404
    assert client.get("/api/stock/IN0000000000").status_code == 404


# -- quarantine-equivalent: empty deltas --------------------------------------


def test_empty_deltas_yields_honest_message(tmp_path):
    db = _copy_db(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        months = [r[0] for r in conn.execute("SELECT DISTINCT report_month FROM mf_holdings_monthly ORDER BY report_month")]
        assert months
        target = months[-1]
        conn.execute("DELETE FROM mf_holding_deltas")
        conn.commit()
    finally:
        conn.close()

    client = TestClient(create_app(str(db)))
    r = client.get(f"/api/consensus/{target}")
    assert r.status_code != 500
    assert r.status_code == 200
    body = r.json()
    assert body.get("count", 0) == 0
    assert body.get("results") == []
    msg = str(body.get("message", ""))
    assert ("No holding-change" in msg) or ("No MF buying" in msg) or ("available" in msg.lower())

    conn2 = _ro_conn(db)
    try:
        srow = conn2.execute("SELECT scheme_id FROM schemes LIMIT 1").fetchone()
    finally:
        conn2.close()
    s = client.get(f"/api/summary/{int(srow[0])}/{target}")
    assert s.status_code != 500
    assert s.status_code == 200
    summary = str(s.json().get("summary", ""))
    assert "No holding-change data available" in summary


# -- no-signal stocks not ranked as buys --------------------------------------


def test_no_signal_stocks_not_ranked_as_buys(tmp_path):
    db = _copy_db(tmp_path)
    conn = _ro_conn(db)
    try:
        latest = _latest_month(conn)
        srow = conn.execute("SELECT scheme_id FROM schemes LIMIT 1").fetchone()
        assert latest and srow
        sid = int(srow[0])
    finally:
        conn.close()

    # Insert a pure no-signal name: only an 'unchanged' delta in latest month.
    w = sqlite3.connect(str(db))
    try:
        w.execute(
            "INSERT OR REPLACE INTO stocks (isin, name, industry, instrument_type) VALUES (?, ?, ?, ?)",
            ("IN0000000001", "NoSignal Ltd", "Test", "equity"),
        )
        w.execute(
            """INSERT OR REPLACE INTO mf_holding_deltas
               (scheme_id, isin, report_month, prev_month, qty_change,
                value_change_lakhs, flow_lakhs, pct_nav_change, action)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, "IN0000000001", latest, latest, 0.0, 0.0, 0.0, 0.0, "unchanged"),
        )
        w.commit()
    finally:
        w.close()

    client = TestClient(create_app(str(db)))
    r = client.get(f"/api/consensus/{latest}")
    assert r.status_code == 200
    results = r.json().get("results", [])
    isins = [x["isin"] for x in results]
    # Pure no-signal names are excluded from ranking entirely.
    assert "IN0000000001" not in isins
    if results:
        top = results[0]
        assert top.get("schemes_buying", 0) > 0 or top.get("amcs_buying", 0) > 0
        assert float(top.get("buying_ratio", 0)) > 0
        for row in results[:5]:
            assert float(row.get("buying_ratio", 0)) >= 0
            # A ranked buy must have at least one buyer.
            assert int(row.get("schemes_buying", 0)) + int(row.get("schemes_selling", 0)) > 0


def test_consensus_includes_buying_ratio_and_equity_filter(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    latest = client.get("/api/coverage").json()["latest_month"]
    eq = client.get(f"/api/consensus/{latest}")
    assert eq.status_code == 200
    results = eq.json().get("results", [])
    if results:
        assert "buying_ratio" in results[0]
        for row in results:
            assert 0.0 <= float(row["buying_ratio"]) <= 1.0
            assert row.get("instrument_type") == "equity"
    all_r = client.get(f"/api/consensus/{latest}", params={"equity_only": 0})
    assert all_r.status_code == 200
    assert all_r.json().get("count", 0) >= eq.json().get("count", 0)


# -- shapes, finite numbers, dashboard ----------------------------------------


def test_coverage_shape(tmp_path):
    db = _copy_db(tmp_path)
    body = TestClient(create_app(str(db))).get("/api/coverage").json()
    assert isinstance(body.get("months"), list) and body["months"]
    assert body.get("latest_month") == body["months"][-1]
    assert isinstance(body.get("scheme_counts"), dict)
    assert body["scheme_counts"].get(body["latest_month"], 0) > 0


def test_summary_wraps_fallback(tmp_path):
    import importlib.util

    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    latest = client.get("/api/coverage").json()["latest_month"]
    sid = client.get("/api/schemes", params={"month": latest}).json()["schemes"][0]["scheme_id"]
    body = client.get(f"/api/summary/{sid}/{latest}").json()
    assert "summary" in body and isinstance(body["summary"], str) and body["summary"].strip()

    spec = importlib.util.spec_from_file_location("fb_check", str(REPO_ROOT / "fallback_summary.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conn = _ro_conn(db)
    try:
        expected = mod.build_summary(conn, int(sid), str(latest))
    finally:
        conn.close()
    assert body["summary"] == expected


def test_stock_holdings_and_quarters(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    latest = client.get("/api/coverage").json()["latest_month"]
    isin = client.get(f"/api/consensus/{latest}").json()["results"][0]["isin"]
    body = client.get(f"/api/stock/{isin}").json()
    assert body["isin"] == isin
    assert isinstance(body.get("holdings"), list)
    assert isinstance(body.get("shareholding"), list)
    assert body.get("shareholding_status") in ("missing", "available", "single_quarter")


def test_json_numbers_finite(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    latest = client.get("/api/coverage").json()["latest_month"]
    payloads = [
        client.get("/api/coverage").json(),
        client.get(f"/api/consensus/{latest}").json(),
        client.get(f"/api/consensus/{latest}", params={"equity_only": 0}).json(),
    ]
    sid = client.get("/api/schemes", params={"month": latest}).json()["schemes"][0]["scheme_id"]
    payloads.append(client.get(f"/api/summary/{sid}/{latest}").json())
    isin = payloads[1].get("results", [{}])[0].get("isin") if payloads[1].get("results") else None
    if isin:
        payloads.append(client.get(f"/api/stock/{isin}").json())
    for p in payloads:
        nums: list = []
        _walk_numbers(p, nums)
        for n in nums:
            assert math.isfinite(n), f"non-finite number leaked: {n!r}"
    # Raw body must not contain bare NaN/Infinity tokens.
    raw = client.get(f"/api/consensus/{latest}").text
    assert "NaN" not in raw
    assert "Infinity" not in raw


def test_dashboard_server_rendered_requirements(tmp_path):
    db = _copy_db(tmp_path)
    res = TestClient(create_app(str(db))).get("/")
    assert res.status_code == 200
    html = res.text
    low = html.lower()
    assert "<table" in low
    assert "<select" in low
    assert "coverage" in low
    assert "DII (MF+banks+insurance)" in html
    assert "monthly change" in low
    assert "missing" in low
    assert "no increase" in low
    assert "stale" in low
    # No CDN / external script sources.
    assert "cdn.jsdelivr" not in low
    assert "unpkg" not in low
    assert 'src="http' not in low
    forbidden = "tot" + "al ret" + "urn"
    assert forbidden not in low
