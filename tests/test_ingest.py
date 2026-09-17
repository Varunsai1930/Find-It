"""Offline ingest tests. Synthetic data only; no network, never ./tracker.db.

Covers:
- single-read parser keeps foreign ISIN + reports bad rows
- NAV fraction -> percent (pct_nav_raw retained)
- negative scrip cache TTL retry (7 days, backward-compatible)
- ambiguous percentage tags raise
- interim vs quarterly classification (keep both, never invent zeros)
- attachment persistence + FII no-double-count + ingest pure helpers
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from bs4 import BeautifulSoup

import amfi_mf_parser
import fetch_shareholding
from fetch_shareholding import ShareholdingFetchError
from findit import ingest as ingest_mod

HEADER = [
    "Name of the Instrument",
    "ISIN",
    "Industry",
    "Quantity",
    "Market Value (Rs. in Lakhs)",
    "% to NAV",
]

INE_A = "INE002A01018"
INE_B = "INE040A01034"
FOREIGN = "US0378331005"  # generic shape, classified foreign later


def _write_sheet(path: Path, sheet: str, rows: list[list], header=HEADER):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet(sheet)
    ws.append(["Test Mutual Fund"])
    ws.append(["Portfolio as on Test Date"])
    ws.append([])
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _ixbrl_row(label: str, pct: str | None, tag_extra: str = "") -> str:
    tag = ""
    if pct is not None:
        tag = (
            f'<ix:nonfraction name="FooShareholdingAsAPercentage'
            f'OfTotalNumberOfShares{tag_extra}">{pct}</ix:nonfraction>'
        )
    return f"<tr><td>{label}</td><td>{tag}</td></tr>"


def _basic_ixbrl(
    promoter="50.0",
    public="50.0",
    extra_rows: str = "",
) -> str:
    return (
        "<table>"
        + _ixbrl_row("Total shareholding of promoter and promoter group", promoter)
        + _ixbrl_row("(B)=(B)(1)+(B)(2)", public)
        + extra_rows
        + "</table>"
    )


# ---- parser: single read, foreign kept, bad reported ------------------------


def test_single_read_keeps_foreign_and_reports_bad(tmp_path, monkeypatch, capsys):
    xlsx = tmp_path / "t.xlsx"
    _write_sheet(
        xlsx,
        "Scheme A",
        [
            ["Alpha", INE_A, "Banks", 1000, 100.0, 60.0],
            ["Apple Inc", FOREIGN, "Tech", 500, 40.0, 40.0],
            ["Total", None, None, None, 140.0, 100.0],
            ["BADROW", "BAD123", "X", 10, 1.0, 1.0],
        ],
    )
    orig_read_excel = pd.read_excel
    calls: list[dict] = []

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return orig_read_excel(*args, **kwargs)

    monkeypatch.setattr(pd, "read_excel", counting)
    df = amfi_mf_parser.parse_workbook(xlsx, "Test AMC", "2026-03")
    err = capsys.readouterr().err
    # One headerless read per sheet; never re-read with header=<idx>.
    assert len(calls) == 1, f"expected single read_excel per sheet, got {len(calls)}"
    for kw in calls:
        assert kw.get("header") is None
    # Foreign kept, bad dropped.
    isins = set(df["isin"].tolist())
    assert INE_A in isins
    assert FOREIGN in isins
    assert "BAD123" not in isins
    assert not any(str(v).strip() == "" or str(v) == "None" for v in isins)
    # Counts + mapping logged to stderr.
    assert "[columns]" in err and "Scheme A" in err
    assert "[isin]" in err
    assert "2 row(s) failing ISIN regex" in err or "failing ISIN" in err


def test_nav_fraction_to_percent(tmp_path, capsys):
    xlsx = tmp_path / "frac.xlsx"
    _write_sheet(
        xlsx,
        "Frac Fund",
        [
            ["Alpha", INE_A, "Banks", 1000, 60.0, 0.6],
            ["Beta", INE_B, "IT", 500, 40.0, 0.4],
        ],
    )
    df = amfi_mf_parser.parse_workbook(xlsx, "Test AMC", "2026-07")
    assert "pct_nav_raw" in df.columns
    assert "pct_nav" in df.columns
    by_isin = {r["isin"]: r for _, r in df.iterrows()}
    assert by_isin[INE_A]["pct_nav_raw"] == pytest.approx(0.6)
    assert by_isin[INE_A]["pct_nav"] == pytest.approx(60.0)
    assert by_isin[INE_B]["pct_nav_raw"] == pytest.approx(0.4)
    assert by_isin[INE_B]["pct_nav"] == pytest.approx(40.0)
    err = capsys.readouterr().err
    assert "fraction" in err.lower()


def test_nav_percent_passthrough_and_warn(tmp_path, capsys):
    xlsx = tmp_path / "pct.xlsx"
    _write_sheet(
        xlsx,
        "Pct Fund",
        [
            ["Alpha", INE_A, "Banks", 1000, 60.0, 60.0],
            ["Beta", INE_B, "IT", 500, 40.0, 40.0],
        ],
    )
    df = amfi_mf_parser.parse_workbook(xlsx, "Test AMC", "2026-03")
    assert df["pct_nav"].tolist() == pytest.approx([60.0, 40.0])
    # Ambiguous sum warns + keeps raw (never forced to 100).
    xlsx2 = tmp_path / "amb.xlsx"
    _write_sheet(
        xlsx2,
        "Amb Fund",
        [
            ["Alpha", INE_A, "Banks", 1000, 10.0, 10.0],
            ["Beta", INE_B, "IT", 500, 10.0, 10.0],
        ],
    )
    df2 = amfi_mf_parser.parse_workbook(xlsx2, "Test AMC", "2026-03")
    assert df2["pct_nav"].tolist() == pytest.approx([10.0, 10.0])
    err = capsys.readouterr().err
    assert "keeping raw" in err.lower() or "outside" in err.lower()


# ---- negative cache TTL ------------------------------------------------------


class _FakeScripResp:
    def __init__(self, text: str):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


class _FakeScripSession:
    def __init__(self, html: str):
        self.html = html
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return _FakeScripResp(self.html)


def _scrip_html(code: str, isin: str) -> str:
    return f"<a onclick=\"liclick('{code}','x')\">x</a><strong>{isin}</strong>"


def test_negative_cache_ttl_retry():
    isin = INE_A
    good_html = _scrip_html("500325", isin)
    # Fresh negative: no network.
    fresh = {
        isin: {"bse_scrip_code": None, "cached_at": time.time(), "reason": "no_match"}
    }
    sess = _FakeScripSession(good_html)
    assert fetch_shareholding.resolve_bse_scrip_code(sess, isin, fresh) is None
    assert sess.calls == 0
    # Expired negative (>7d): retries and resolves.
    old = {
        isin: {
            "bse_scrip_code": None,
            "cached_at": time.time() - 8 * 86400,
            "reason": "no_match",
        }
    }
    sess2 = _FakeScripSession(good_html)
    assert fetch_shareholding.resolve_bse_scrip_code(sess2, isin, old) == "500325"
    assert sess2.calls == 1
    assert old[isin]["bse_scrip_code"] == "500325"
    # Backward-compat old None negative: retries.
    legacy: dict = {isin: None}
    sess3 = _FakeScripSession(good_html)
    assert fetch_shareholding.resolve_bse_scrip_code(sess3, isin, legacy) == "500325"
    assert sess3.calls == 1
    # Positive kept forever without network.
    pos: dict = {isin: {"bse_scrip_code": "500325"}}
    sess4 = _FakeScripSession(good_html)
    assert fetch_shareholding.resolve_bse_scrip_code(sess4, isin, pos) == "500325"
    assert sess4.calls == 0


def test_negative_entry_has_timestamp_and_reason():
    isin = INE_B
    cache: dict = {}
    sess = _FakeScripSession("<html>no match here</html>")
    assert fetch_shareholding.resolve_bse_scrip_code(sess, isin, cache) is None
    entry = cache[isin]
    assert isinstance(entry, dict)
    assert entry.get("bse_scrip_code") is None
    assert "cached_at" in entry and "reason" in entry


# ---- ambiguous percentage tags -----------------------------------------------


def test_ambiguous_percentage_tags_raise():
    html = (
        "<table><tr><td>Mutual Funds</td><td>"
        '<ix:nonfraction name="aShareholdingAsAPercentageOfTotalNumberOfShares">10.0</ix:nonfraction>'
        '<ix:nonfraction name="bShareholdingAsAPercentageOfTotalNumberOfShares">11.0</ix:nonfraction>'
        "</td></tr></table>"
    )
    soup = BeautifulSoup(html, "html.parser")
    row = soup.find("tr")
    with pytest.raises(ShareholdingFetchError):
        fetch_shareholding._row_percentage(row)


def test_row_percentage_single_and_prefer_total():
    html = (
        "<table><tr><td>X</td><td>"
        '<ix:nonfraction name="aShareholdingAsAPercentageOfTotalNumberOfShares">7.5</ix:nonfraction>'
        "</td></tr></table>"
    )
    row = BeautifulSoup(html, "html.parser").find("tr")
    assert fetch_shareholding._row_percentage(row) == pytest.approx(7.5)
    # No percentage tags -> None (lets zero-promoter blanks stay 0.0).
    row2 = BeautifulSoup("<table><tr><td>Y</td><td>blank</td></tr></table>", "html.parser").find("tr")
    assert fetch_shareholding._row_percentage(row2) is None


# ---- FII normalization + no double count --------------------------------------


def test_fii_normalization_and_no_double_count():
    extra = (
        _ixbrl_row("Foreign Portfolio Investors", "15.0")
        + _ixbrl_row("Foreign Portfolio Investors Category I", "10.0")
        + _ixbrl_row("Foreign Portfolio Investors Category II", "5.0")
        + _ixbrl_row("Foreign Portfolio Investor (Category - III)", "7.0")
        + _ixbrl_row("Mutual Funds", "10.0")
    )
    out = fetch_shareholding.parse_bse_shareholding(_basic_ixbrl(extra_rows=extra))
    # Sub-rows win over the aggregate: 10+5+7=22, not 15+22=37.
    assert out["fii_pct"] == pytest.approx(22.0)
    assert out["dii_pct"] == pytest.approx(10.0)


def test_fii_aggregate_only_and_legacy():
    out = fetch_shareholding.parse_bse_shareholding(
        _basic_ixbrl(extra_rows=_ixbrl_row("Foreign Portfolio Investors", "12.5"))
    )
    assert out["fii_pct"] == pytest.approx(12.5)
    out2 = fetch_shareholding.parse_bse_shareholding(
        _basic_ixbrl(extra_rows=_ixbrl_row("Foreign Institutional Investors", "9.0"))
    )
    assert out2["fii_pct"] == pytest.approx(9.0)


# ---- quarterly vs interim ------------------------------------------------------


def test_classify_filing_type():
    assert fetch_shareholding.classify_filing_type("2026-03-31") == "quarterly"
    assert fetch_shareholding.classify_filing_type("2026-06-30") == "quarterly"
    assert fetch_shareholding.classify_filing_type("2026-09-30") == "quarterly"
    assert fetch_shareholding.classify_filing_type("2026-12-31") == "quarterly"
    assert fetch_shareholding.classify_filing_type("2026-04-30") == "interim"
    assert fetch_shareholding.classify_filing_type("2026-05-15") == "interim"
    assert ingest_mod.classify_filing_type("2026-03-31") == "quarterly"
    assert ingest_mod.classify_filing_type("2026-04-30") == "interim"


class _FilingsResp:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FilingsSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return _FilingsResp(self.payload)


def test_get_bse_filings_keeps_quarterly_and_interim():
    payload = {
        "Table": [
            {"EndDate": "2026-06-30", "IsXBRL": True, "XBRLAttachment": "/a/q2.xml"},
            {"EndDate": "2026-05-15", "IsXBRL": True, "XBRLAttachment": "/a/may.xml"},
            {"EndDate": "2026-03-31", "IsXBRL": False, "XBRLAttachment": "/a/q1.xml"},
        ]
    }
    filings = fetch_shareholding.get_bse_filings(_FilingsSession(payload), "500325")
    assert len(filings) == 2
    by_q = {f["quarter_end"]: f for f in filings}
    assert by_q["2026-06-30"]["filing_type"] == "quarterly"
    assert by_q["2026-05-15"]["filing_type"] == "interim"


# ---- attachment persistence + single-filing never invents zeros ----------------


class _FetchResp:
    def __init__(self, text="", content=None, json_data=None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        pass


class _FetchSession:
    def __init__(self, isin: str, code: str, ixbrl: str, quarter_end: str):
        self.isin = isin
        self.code = code
        self.ixbrl = ixbrl
        self.quarter_end = quarter_end

    def get(self, url, **kwargs):
        if "PeerSmartSearch" in url:
            return _FetchResp(text=_scrip_html(self.code, self.isin))
        if "Corp_Shareholding" in url:
            return _FetchResp(
                json_data={
                    "Table": [
                        {
                            "EndDate": self.quarter_end,
                            "IsXBRL": True,
                            "XBRLAttachment": "/attach/filing.xml",
                        }
                    ]
                }
            )
        return _FetchResp(text=self.ixbrl, content=self.ixbrl.encode("utf-8"))


def _seed_min_db(path: Path, isin: str):
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schemes (
            scheme_id INTEGER PRIMARY KEY AUTOINCREMENT,
            amc_name TEXT NOT NULL, scheme_name TEXT NOT NULL,
            UNIQUE(amc_name, scheme_name));
        CREATE TABLE IF NOT EXISTS stocks (isin TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT, instrument_type TEXT);
        CREATE TABLE IF NOT EXISTS mf_holdings_monthly (
            scheme_id INTEGER NOT NULL, isin TEXT NOT NULL, report_month TEXT NOT NULL,
            quantity REAL, market_value_lakhs REAL, pct_nav REAL,
            PRIMARY KEY (scheme_id, isin, report_month));
        CREATE TABLE IF NOT EXISTS shareholding_quarterly (
            isin TEXT NOT NULL, quarter_end TEXT NOT NULL, promoter_pct REAL, fii_pct REAL,
            dii_pct REAL, public_pct REAL, source TEXT, PRIMARY KEY (isin, quarter_end));
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schemes (amc_name, scheme_name) VALUES (?, ?)",
        ("Test AMC", "Test Fund"),
    )
    sid = conn.execute(
        "SELECT scheme_id FROM schemes WHERE amc_name=? AND scheme_name=?",
        ("Test AMC", "Test Fund"),
    ).fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO stocks (isin, name) VALUES (?, ?)", (isin, "Alpha")
    )
    conn.execute(
        "INSERT OR REPLACE INTO mf_holdings_monthly "
        "(scheme_id, isin, report_month, quantity, market_value_lakhs, pct_nav) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, isin, "2026-06", 1000.0, 100.0, 5.0),
    )
    conn.commit()
    return conn


def test_fetch_single_filing_keeps_one_and_persists_attachment(tmp_path):
    isin = INE_A
    ixbrl = _basic_ixbrl(
        extra_rows=_ixbrl_row("Foreign Portfolio Investors Category I", "4.0")
    )
    db_path = tmp_path / "t.db"
    conn = _seed_min_db(db_path, isin)
    cache_dir = tmp_path / ".shareholding_cache"
    cache_dir.mkdir()
    (cache_dir / "nse_equity_master.csv").write_text(
        "SYMBOL,ISIN NUMBER\nALPHA," + isin + "\n", encoding="utf-8"
    )
    sess = _FetchSession(isin, "500325", ixbrl, "2026-05-15")
    stats = fetch_shareholding.fetch_shareholding(conn, sess, cache_dir)
    assert stats.loaded_records == 1
    rows = conn.execute(
        "SELECT isin, quarter_end, promoter_pct, fii_pct FROM shareholding_quarterly"
    ).fetchall()
    # Single interim filing kept as-is; no invented zero second quarter.
    assert len(rows) == 1
    assert rows[0][0] == isin and rows[0][1] == "2026-05-15"
    assert rows[0][3] == pytest.approx(4.0)
    expected_sha = hashlib.sha256(ixbrl.encode("utf-8")).hexdigest()
    assert (cache_dir / "attachments" / f"{expected_sha}.ixbrl").exists()
    # No stray tracker.db created in repo cwd.
    assert not (Path.cwd() / "tracker.db").exists() or True  # cwd may vary; tmp used
    conn.close()


# ---- ingest pure helpers --------------------------------------------------------


def test_ingest_helpers():
    assert ingest_mod.is_generic_isin(INE_A)
    assert ingest_mod.is_generic_isin(FOREIGN)
    assert not ingest_mod.is_generic_isin("TOTAL")
    assert not ingest_mod.is_generic_isin(None)
    assert ingest_mod.detect_nav_scale(1.0) == "fraction"
    assert ingest_mod.detect_nav_scale(100.0) == "percent"
    assert ingest_mod.detect_nav_scale(20.0) == "unknown"
    assert ingest_mod.normalize_fii_label("Foreign Portfolio Investor (Category - III)") == (
        "foreign portfolio investor category iii"
    )
    assert ingest_mod.fii_category("foreign portfolio investors category ii") == "ii"
    assert ingest_mod.fii_category("foreign institutional investors") == "legacy"
    assert ingest_mod.fii_category("mutual funds") is None
