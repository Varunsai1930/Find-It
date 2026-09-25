"""Web read-only tests on a synthetic tmp DB. No network, never touches ./tracker.db.

The DB is built here rather than copied from ./tracker.db, so the tests run
on a fresh clone (the real DB is not committed) and never change meaning
when the real data does.
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import db as db_module
import delta_calculator
from findit.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]

# Two AMCs, three active schemes; one equity everyone buys, one mixed, one
# held unchanged, and a debt holding the equity filter must drop.
_SCHEMES = [(1, "A AMC", "A Flexi Cap"), (2, "A AMC", "A Mid Cap"), (3, "B AMC", "B Value")]
_STOCKS = [("INE002A01018", "Reliance Industries", "Oil", "equity"),
           ("INE009A01021", "Infosys", "IT", "equity"),
           ("INE040A01034", "HDFC Bank", "Banks", "equity"),
           ("INE001A07PB6", "HDFC NCD", "Debt", "ncd")]
_HOLDINGS = {  # (scheme, isin) -> (qty_jul, qty_aug)
    (1, "INE002A01018"): (100, 150), (1, "INE009A01021"): (100, 80),
    (1, "INE040A01034"): (50, 50), (1, "INE001A07PB6"): (10, 20),
    (2, "INE002A01018"): (40, 60), (2, "INE009A01021"): (30, 30),
    (3, "INE002A01018"): (70, 90), (3, "INE009A01021"): (60, 90),
}


def _copy_db(tmp_path: Path) -> Path:
    """A small, complete tracker DB: holdings, deltas, statuses, filings."""
    dest = tmp_path / "web_test.db"
    conn = db_module.get_connection(str(dest))
    conn.executemany("INSERT INTO schemes (scheme_id, amc_name, scheme_name, is_active_equity) "
                     "VALUES (?, ?, ?, 1)", _SCHEMES)
    conn.executemany("INSERT INTO stocks (isin, name, industry, instrument_type) "
                     "VALUES (?, ?, ?, ?)", _STOCKS)
    for (sid, isin), quantities in _HOLDINGS.items():
        for month, qty in zip(("2026-07", "2026-08"), quantities):
            conn.execute("INSERT INTO mf_holdings_monthly (scheme_id, isin, report_month, "
                         "quantity, market_value_lakhs, pct_nav) VALUES (?, ?, ?, ?, ?, 10.0)",
                         (sid, isin, month, qty, qty * 0.1))
    for sid, _amc, _name in _SCHEMES:
        for month in ("2026-07", "2026-08"):
            conn.execute("INSERT INTO scheme_month_status (scheme_id, report_month, status) "
                         "VALUES (?, ?, 'ok')", (sid, month))
    delta_calculator.persist_deltas(
        conn, delta_calculator.compute_deltas(conn, "2026-07", "2026-08"))
    conn.executemany(
        "INSERT INTO shareholding_quarterly (isin, quarter_end, promoter_pct, fii_pct, "
        "dii_pct, public_pct, source, filing_type) VALUES (?, ?, 50, ?, 10, 50, 'test', "
        "'quarterly')",
        [("INE002A01018", "2026-03-31", 20.0), ("INE002A01018", "2026-06-30", 21.0)])
    conn.commit()
    conn.close()
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


# -- month view fragment and stock lookup -------------------------------------


def test_month_fragment_is_one_table_in_ranking_order(tmp_path):
    import consensus_signals

    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    conn = _ro_conn(db)
    try:
        ranked = consensus_signals.ranked_consensus(conn, "2026-08")
    finally:
        conn.close()
    for side, expected in (("buy", ranked),
                           ("sell", consensus_signals.broadest_selling(ranked))):
        res = client.get("/fragments/month/2026-08", params={"side": side})
        assert res.status_code == 200
        html = res.text
        # The fragment replaces the table; it must never carry a second one.
        assert html.count("<table") == 1
        positions = [html.index(f'data-isin="{isin}"') for isin in expected["isin"]]
        assert positions == sorted(positions), side
    assert client.get("/fragments/month/1900-01").status_code == 404


def _row(html: str, isin: str) -> str:
    row = html[html.index(f'data-isin="{isin}"'):]
    return row[:row.index("</tr>")]


def test_month_fragment_keeps_missing_distinct_from_no_increase(tmp_path):
    db = _copy_db(tmp_path)
    html = TestClient(create_app(str(db))).get("/fragments/month/2026-08",
                                               params={"cols": "all"}).text
    # Infosys has no filing: missing, never "no increase". Reliance has two.
    infosys = _row(html, "INE009A01021")
    assert "missing" in infosys and "no increase" not in infosys
    reliance = _row(html, "INE002A01018")
    assert "+1.00" in reliance and "missing" not in reliance


def test_default_table_is_the_scannable_columns(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    core = client.get("/fragments/month/2026-08").text
    for heading in ("AMCs net", "Schemes<br>buy / sell", "Existing-position<br>flow ₹ Cr", ">Filing<"):
        assert heading in core
    for heading in ("Discretionary", "New positions", "FII Δ pp", "DII Δ pp"):
        assert heading not in core
    # The filing column alone still separates missing from filed.
    assert "missing" in _row(core, "INE009A01021")
    assert "Jun 2026 qtr" in _row(core, "INE002A01018")
    wide = client.get("/fragments/month/2026-08", params={"cols": "all"}).text
    for heading in ("Discretionary", "New positions", "FII Δ pp", "DII Δ pp"):
        assert heading in wide
    # Each view links to the other, keeping every other parameter.
    assert 'href="/?month=2026-08&amp;side=buy&amp;limit=25&amp;cols=all&amp;' in core
    assert "cols=core" in wide


def test_dashboard_reflects_query_filters(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    html = client.get("/", params={"side": "sell", "equity_only": 0}).text
    assert "Broadest selling" in html
    assert 'id="equity-only" value="1" checked' not in html
    # An unknown month falls back to the latest rather than failing the page.
    assert "Aug 2026" in client.get("/", params={"month": "1900-01"}).text


def test_stock_fragment_by_isin_and_name(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    by_isin = client.get("/fragments/stock", params={"q": "ine002a01018"}).text
    assert "Reliance Industries" in by_isin and "Fund holdings" in by_isin
    # One name match opens the stock directly; several list the candidates.
    assert "Fund holdings" in client.get("/fragments/stock", params={"q": "infos"}).text
    several = client.get("/fragments/stock", params={"q": "HDFC"}).text
    assert "2 stocks match" in several
    assert "No stock matches" in client.get("/fragments/stock", params={"q": "zzz"}).text


def test_stock_flags_filings_published_after_the_month(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    # July portfolios were public 10 Aug; the June-quarter filing's deadline is 21 Jul.
    body = client.get("/api/stock/INE002A01018", params={"month": "2026-07"}).json()
    june = next(q for q in body["shareholding"] if q["quarter_end"] == "2026-06-30")
    assert (june["published_on"], june["published_basis"]) == ("2026-07-21", "regulatory_deadline")
    html = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-07"}).text
    assert "after Jul 2026" not in html
    # Broadcast observed after 10 Aug: July's ranking could not have used it.
    w = sqlite3.connect(str(db))
    w.execute("UPDATE shareholding_quarterly SET published_at = '2026-08-20T18:00:00'"
              " WHERE quarter_end = '2026-06-30'")
    w.commit()
    w.close()
    html = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-07"}).text
    assert "after Jul 2026" in html


def _fact(html: str, label: str) -> str:
    fact = html[html.index(f'<dt class="fact__label">{label}</dt>'):]
    return fact[:fact.index("</div>")]


def _add_scheme(conn, sid, amc, name, active, holdings):
    conn.execute("INSERT INTO schemes (scheme_id, amc_name, scheme_name, is_active_equity) "
                 "VALUES (?, ?, ?, ?)", (sid, amc, name, active))
    for isin, month, qty in holdings:
        conn.execute("INSERT INTO mf_holdings_monthly (scheme_id, isin, report_month, quantity, "
                     "market_value_lakhs, pct_nav) VALUES (?, ?, ?, ?, ?, 1)",
                     (sid, isin, month, qty, qty * 0.1))


def _coverage_db(tmp_path) -> Path:
    """The base DB plus one scheme in each state the coverage counts separate."""
    db = _copy_db(tmp_path)
    w = sqlite3.connect(str(db))
    # Loaded for August only: no July to compare, so it cannot be compared.
    _add_scheme(w, 4, "C AMC", "C New Fund", 1, [("INE002A01018", "2026-08", 5)])
    # A debt fund: compared, but on nothing the equity or active filters keep.
    _add_scheme(w, 5, "D AMC", "D Liquid", 0, [("INE001A07PB6", "2026-07", 10),
                                              ("INE001A07PB6", "2026-08", 30)])
    # A compared fund quarantined for August is withheld, not counted.
    w.execute("UPDATE scheme_month_status SET status = 'quarantined' "
              "WHERE scheme_id = 3 AND report_month = '2026-08'")
    delta_calculator.persist_deltas(w, delta_calculator.compute_deltas(w, "2026-07", "2026-08"))
    w.commit()
    w.close()
    return db


def test_compared_count_is_the_set_the_ranking_counts(tmp_path):
    import consensus_signals

    db = _coverage_db(tmp_path)
    client = TestClient(create_app(str(db)))
    conn = _ro_conn(db)
    try:
        for equity, active, expected in ((1, 1, {1, 2}), (1, 0, {1, 2}), (0, 0, {1, 2, 5})):
            voters = consensus_signals.voting_schemes(
                conn, "2026-08", "equity" if equity else None, bool(active))
            assert set(voters) == expected, (equity, active)
            html = client.get("/fragments/month/2026-08",
                              params={"equity_only": equity, "active_only": active}).text
            compared = _fact(html, "Schemes compared")
            assert f'<dd class="fact__value">{len(expected)}</dd>' in compared
            assert "1 AMC · vs Jul 2026" in compared if expected == {1, 2} else "2 AMCs" in compared
            # Scheme 3 is quarantined: withheld under every filter.
            assert '<dd class="fact__value">1</dd>' in _fact(html, "Withheld")
    finally:
        conn.close()
    html = client.get("/fragments/month/2026-08").text
    # Active filter: 4 active schemes loaded of 5; one had no July, one was withheld.
    assert "5 loaded for Aug 2026, 4 of them active stock-pickers" in html
    assert "1 loaded without a previous month to compare" in html
    assert "hold nothing the equity filter keeps" not in html
    wide = client.get("/fragments/month/2026-08", params={"active_only": 0}).text
    assert "1 hold nothing the equity filter keeps" in wide


def test_validation_wording_never_assumes_a_pass(tmp_path):
    db = _coverage_db(tmp_path)
    client = TestClient(create_app(str(db)))
    html = client.get("/fragments/month/2026-08").text
    # Scheme 4 has holdings but no status row: counted as not validated.
    assert "2 passed · 1 withheld · 1 not validated" in html
    assert "all loaded schemes passed" not in html
    # A failure outside the active filter is not withheld from this view, and the
    # "none failed" note names the schemes it covers.
    w = sqlite3.connect(str(db))
    w.execute("INSERT INTO scheme_month_status (scheme_id, report_month, status) "
              "VALUES (5, '2026-08', 'quarantined')")
    w.execute("UPDATE scheme_month_status SET status = 'ok' "
              "WHERE scheme_id = 3 AND report_month = '2026-08'")
    w.commit()
    w.close()
    html = client.get("/fragments/month/2026-08").text
    assert "none of 4 active schemes failed validation" in _fact(html, "Withheld")
    html = client.get("/fragments/month/2026-08", params={"active_only": 0}).text
    assert '<dd class="fact__value">1</dd>' in _fact(html, "Withheld")
    w = sqlite3.connect(str(db))
    w.execute("DROP TABLE scheme_month_status")
    w.commit()
    w.close()
    html = client.get("/fragments/month/2026-08").text
    withheld = _fact(html, "Withheld")
    assert '<dd class="fact__value">–</dd>' in withheld and "validation not run" in withheld
    assert "nothing has been checked" in html


def test_last_ingest_is_labelled_database_wide(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    assert "None recorded · whole database" in client.get("/fragments/month/2026-08").text
    w = sqlite3.connect(str(db))
    w.execute("INSERT INTO ingest_runs (run_id, started_at, status) "
              "VALUES ('r1', '2026-09-17T12:00:00+00:00', 'completed')")
    w.commit()
    w.close()
    html = client.get("/fragments/month/2026-07").text
    assert "17 Sep 2026 · whole database, not specific to Jul 2026" in html


# -- stock search, detail and month continuity -----------------------------------


def test_stock_search_suggestions(tmp_path):
    db = _copy_db(tmp_path)
    w = sqlite3.connect(str(db))
    w.execute("INSERT INTO stocks (isin, name, industry, instrument_type) "
              "VALUES ('INE999Z01011', 'Bank of Test', 'Banks', 'equity')")
    w.commit()
    w.close()
    client = TestClient(create_app(str(db)))

    def names(**params):
        res = client.get("/api/stocks/search", params=params)
        assert res.status_code == 200
        return [r["name"] for r in res.json()["results"]]

    assert names(q="relia") == ["Reliance Industries"]
    assert names(q="ine009") == ["Infosys"]  # ISIN prefix, case-insensitive
    # Names starting with the query lead; then the most widely held.
    assert names(q="bank") == ["Bank of Test", "HDFC Bank"]
    assert names(q="hdfc") == ["HDFC Bank", "HDFC NCD"]
    # Too short, or only LIKE wildcards: nothing rather than everything.
    assert names(q="r") == [] and names(q="%_") == [] and names(q="  ") == []
    assert names(q="zzz") == []
    held = client.get("/api/stocks/search", params={"q": "reliance", "month": "2026-08"}).json()
    assert held["results"][0]["schemes_holding"] == 3
    assert names(q="bank", limit=1) == ["Bank of Test"]
    assert client.get("/api/stocks/search", params={"q": "rel", "month": "1900-01"}).status_code == 404


def test_stock_detail_follows_the_selected_month_and_filters(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    aug = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-08"}).text
    assert "Fund holdings · Aug 2026" in aug and "Fund activity · Aug 2026" in aug
    # The detail adds the columns the default table hides.
    for label in ("Discretionary net", "New positions", "FII Δ pp", "DII Δ pp"):
        assert label in aug
    assert "+2" in aug and "2 buy · 0 sell" in aug  # schemes 1-2 are one AMC
    assert "(active stock-pickers, equity holdings)" in aug
    # July is the first month: holdings but nothing compared, so not ranked.
    jul = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-07"}).text
    assert "Fund holdings · Jul 2026" in jul
    assert "Not in the Jul 2026 ranking: no counted scheme bought or sold it" in jul
    # Filters carry through: the NCD is ranked only without the equity filter.
    ncd = {"q": "INE001A07PB6", "month": "2026-08"}
    assert "it is not equity" in client.get("/fragments/stock", params=ncd).text
    wide = client.get("/fragments/stock", params={**ncd, "equity_only": 0}).text
    assert "Fund activity · Aug 2026" in wide and "all holdings" in wide
    assert "Not in the" not in wide
    # A name that matches several stocks lists them with their month's holdings.
    several = client.get("/fragments/stock", params={"q": "HDFC", "month": "2026-08"}).text
    assert "2 stocks match" in several and "held by 1 scheme in Aug 2026" in several


def test_page_keeps_the_selected_month_everywhere(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    html = client.get("/", params={"month": "2026-07", "side": "sell", "cols": "all"}).text
    assert '<option value="2026-07" selected>' in html
    assert 'id="cols-input" value="all"' in html
    assert "Broadest selling" in html
    # The first-render table is exactly the fragment for the same view.
    frag = client.get("/fragments/month/2026-07", params={"side": "sell", "cols": "all"}).text
    assert frag.strip() in html
    # Server-rendered links keep month, side and filters.
    page = client.get("/", params={"month": "2026-08", "side": "sell", "active_only": 0}).text
    assert "/?month=2026-08&amp;side=sell&amp;limit=25&amp;cols=all&amp;equity_only=1&amp;active_only=0" in page
    assert page.count("<table") == 1


def test_empty_and_error_states(tmp_path):
    empty = tmp_path / "empty.db"
    db_module.get_connection(str(empty)).close()
    client = TestClient(create_app(str(empty)))
    html = client.get("/").text
    assert "No coverage data available yet" in html and "<table" not in html
    assert client.get("/api/stocks/search", params={"q": "rel"}).json()["results"] == []

    client = TestClient(create_app(str(_copy_db(tmp_path))))
    # The first month has holdings but no earlier month to compare.
    jul = client.get("/fragments/month/2026-07").text
    assert "No holding-change data available for 2026-07" in jul
    assert "no earlier month to compare" in jul and "<table" not in jul
    assert client.get("/fragments/month/1900-01").status_code == 404
    assert client.get("/fragments/stock", params={"q": "INE002A01018",
                                                  "month": "1900-01"}).status_code == 404
    assert "No stock matches" in client.get("/fragments/stock", params={"q": "zzz"}).text


def test_ranking_cache_sees_database_changes(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    assert "Infosys" in client.get("/fragments/month/2026-08").text
    w = sqlite3.connect(str(db))
    w.execute("DELETE FROM mf_holding_deltas WHERE isin = 'INE009A01021'")
    w.commit()
    w.close()
    assert "Infosys" not in client.get("/fragments/month/2026-08").text


# -- stock detail validation status ----------------------------------------------


def _holding_row(html: str, scheme: str) -> str:
    row = html[:html.index(scheme)]
    row = html[row.rindex("<tr"):]
    return row[:row.index("</tr>")]


def test_stock_detail_marks_withheld_and_unvalidated_rows(tmp_path):
    db = _copy_db(tmp_path)
    w = sqlite3.connect(str(db))
    # Scheme 3 (B Value, which bought Reliance) fails validation for August;
    # scheme 2 (A Mid Cap) has no validation result at all.
    w.execute("UPDATE scheme_month_status SET status = 'quarantined' "
              "WHERE scheme_id = 3 AND report_month = '2026-08'")
    w.execute("DELETE FROM scheme_month_status WHERE scheme_id = 2 AND report_month = '2026-08'")
    w.commit()
    w.close()
    client = TestClient(create_app(str(db)))

    body = client.get("/api/stock/INE002A01018", params={"month": "2026-08"}).json()
    statuses = {h["scheme_id"]: h["validation_status"] for h in body["holdings"]}
    assert statuses == {1: "ok", 2: "not_validated", 3: "quarantined"}
    assert (body["withheld_count"], body["not_validated_count"], body["validation"]) == (1, 1, "checked")
    # The raw withheld row stays available for inspection.
    withheld = next(h for h in body["holdings"] if h["scheme_id"] == 3)
    assert (withheld["quantity"], withheld["action"]) == (90, "added")

    html = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-08"}).text
    row = _holding_row(html, "B Value")
    assert 'class="is-withheld"' in row and ">withheld<" in row
    assert "raw: added · not validated" in row
    assert "tag--added" not in row  # never styled as validated activity
    assert "1 scheme failed validation for Aug 2026: its row is shown for inspection only" in html
    assert "1 has no validation result for this month." in html
    unvalidated = _holding_row(html, "A Mid Cap")
    assert "tag--added" in unvalidated and "not validated" in unvalidated
    assert "not validated" not in _holding_row(html, "A Flexi Cap")
    # The activity block is the ranking's, which leaves scheme 3 out: one AMC buys.
    activity = html[html.index("Fund activity"):html.index("Fund holdings")]
    assert "1 buy · 0 sell" in activity


def test_stock_detail_without_a_status_table_says_validation_never_ran(tmp_path):
    db = _copy_db(tmp_path)
    w = sqlite3.connect(str(db))
    w.execute("DROP TABLE scheme_month_status")
    w.commit()
    w.close()
    client = TestClient(create_app(str(db)))
    body = client.get("/api/stock/INE002A01018", params={"month": "2026-08"}).json()
    # Holdings still load; each row says validation never ran, not that it passed.
    assert len(body["holdings"]) == 3
    assert {h["validation_status"] for h in body["holdings"]} == {"not_run"}
    assert (body["withheld_count"], body["validation"]) == (0, "not_run")
    html = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-08"}).text
    assert "Validation has not been run on this database" in html
    assert "is-withheld" not in html


def test_stock_detail_with_every_row_validated_has_no_warning(tmp_path):
    db = _copy_db(tmp_path)
    client = TestClient(create_app(str(db)))
    html = client.get("/fragments/stock", params={"q": "INE002A01018", "month": "2026-08"}).text
    assert "state--warn" not in html and "not validated" not in html
    body = client.get("/api/stock/INE002A01018", params={"month": "2026-08"}).json()
    assert {h["validation_status"] for h in body["holdings"]} == {"ok"}


def test_missing_database_is_reported_not_a_server_error(tmp_path):
    missing = tmp_path / "absent.db"
    client = TestClient(create_app(str(missing)))
    page = client.get("/")
    assert page.status_code == 503
    assert f"No database at {missing}" in page.text and "run_pipeline.py" in page.text
    for url in ("/api/coverage", "/api/stocks/search?q=rel", "/fragments/month/2026-08"):
        res = client.get(url)
        assert res.status_code == 503 and "No database at" in res.json()["detail"]
    assert not missing.exists()  # read-only: nothing is created
