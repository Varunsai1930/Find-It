"""Full scheme names drive the passive/arbitrage filter. No network."""

import pandas as pd
import pytest

import amfi_mf_parser
import db
from findit.cli import scheme_titles


def _rows(*rows):
    return pd.DataFrame(list(rows))


@pytest.mark.parametrize("rows, expected", [
    # SBI: label cell then name cell.
    (_rows([None, None], ["SBI Mutual Fund", "055"], ["SCHEME NAME :", "SBI Nifty Index Fund"],
           ["PORTFOLIO STATEMENT AS ON :", pd.Timestamp("2026-08-31")]),
     "SBI Nifty Index Fund"),
    # ICICI: bare name under the AMC line.
    (_rows(["ICICI Prudential Mutual Fund"], ["ICICI Prudential Arbitrage Fund"],
           ["Portfolio as on Aug 31,2026"]),
     "ICICI Prudential Arbitrage Fund"),
    # HDFC: name with the SEBI description, first row.
    (_rows(["HDFC Mid Cap Fund (An open ended equity scheme)"], ["Portfolio as on 31-Aug-2026"]),
     "HDFC Mid Cap Fund (An open ended equity scheme)"),
    (_rows(["Portfolio as on 31-Aug-2026"]), None),
])
def test_title_extraction_layouts(rows, expected):
    assert amfi_mf_parser.extract_scheme_title(rows) == expected


@pytest.mark.parametrize("title, active", [
    ("SBI Arbitrage Fund", 0),
    ("ICICI Prudential Nifty200 Value 30 Index Fund", 0),
    ("SBI Nifty 50 ETF", 0),
    ("ICICI Prudential Multi Sector Passive FOF", 0),
    ("ICICI Prudential Global Advantage Fund (FOF)", 0),
    ("SBI Equity Savings Fund", 0),
    ("SBI Gold Fund", 0),
    ("SBI Fixed Maturity Plan (FMP)- Series 1", 0),
    ("ICICI Prudential Long Term Fund", 0),  # a debt fund
    ("SBI Long Term Advantage Fund - Series IV", 1),  # ELSS
    ("ICICI Prudential Long Term Wealth Enhancement Fund", 1),
    ("HDFC Mid Cap Fund (An open ended equity scheme predominantly investing in mid cap "
     "stocks)", 1),
    ("ICICI Prudential Balanced Advantage Fund", 1),
    ("ICICI Prudential Aggressive Hybrid Fund", 1),
    ("ICICI Prudential Commodities Fund", 1),
])
def test_title_classification(title, active):
    assert db.classify_scheme_title(title) == active


def test_sheet_code_heuristic_is_only_a_fallback():
    assert db.classify_scheme("NIF30DEX", None) == 1  # the code tells us nothing
    assert db.classify_scheme("NIF30DEX", "ICICI Prudential Nifty200 Value 30 Index Fund") == 0


def _parsed_csv(tmp_path, title):
    path = tmp_path / "p.csv"
    pd.DataFrame([{
        "isin": "INEAAA01001", "instrument_name": "A Ltd", "quantity": 1,
        "market_value_lakhs": 1.0, "pct_nav": 100.0, "pct_nav_scale": "percent",
        "scheme_name": "SAOF", "scheme_title": title, "amc_name": "SBI AMC",
        "report_month": "2026-08",
    }]).to_csv(path, index=False)
    return path


def test_loading_a_titled_csv_reclassifies_and_reports(tmp_path, capsys):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _parsed_csv(tmp_path, None))
    db.backfill_is_active_equity(conn)
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] == 1
    db.load_parsed_csv(conn, _parsed_csv(tmp_path, "SBI Arbitrage Fund"))
    row = conn.execute("SELECT is_active_equity, scheme_title FROM schemes").fetchone()
    assert row == (0, "SBI Arbitrage Fund")
    assert "[is_active_equity 1->0] SBI AMC | SAOF | SBI Arbitrage Fund" in capsys.readouterr().out
    conn.close()


def test_titles_cli_dry_run_writes_nothing(tmp_path, monkeypatch):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _parsed_csv(tmp_path, None))
    db.backfill_is_active_equity(conn)
    monkeypatch.setattr(amfi_mf_parser, "read_scheme_titles",
                        lambda path: {"SAOF": "SBI Arbitrage Fund", "NEWCODE": "X Fund"})
    dry = scheme_titles.apply_titles(conn, "SBI AMC", [tmp_path / "w.xlsx"], dry_run=True)
    assert [c[1] for c in dry["changes"]] == ["SAOF"]
    assert dry["unknown"] == ["w.xlsx: NEWCODE"]
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] == 1
    scheme_titles.apply_titles(conn, "SBI AMC", [tmp_path / "w.xlsx"])
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] == 0
    conn.close()
