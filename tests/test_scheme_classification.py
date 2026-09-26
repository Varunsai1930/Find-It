"""Scheme-title classifier: debt "Savings Fund" vs hybrid "savings" funds.

"ICICI Prudential Savings Fund" is SEBI's low-duration debt category (it
holds only T-bills, CDs and NCDs) but was classified as an active
stock-picker because the title rule only knew "Equity Savings". Hybrids
named "savings" -- "Regular Savings Fund", a children's "Savings Plan" --
keep their chosen equity and stay active, like other conservative hybrids.

No network; every test builds its own temporary database.
"""

import pandas as pd
import pytest

import db
from findit.cli import scheme_titles


# ---- classify_scheme_title: debt "Savings Fund" vs. hybrid "Equity Savings"

@pytest.mark.parametrize("title, active", [
    ("ICICI Prudential Savings Fund", 0),           # low-duration debt (the bug)
    ("SBI Equity Savings Fund", 0),                 # arbitrage-hedged hybrid
    ("ICICI Prudential Equity Savings Fund", 0),
    ("ICICI Prudential Regular Savings Fund", 1),   # conservative hybrid
    ("SBI Children's Fund - Savings Plan", 1),      # hybrid children's plan
    ("DSP Liquidity Fund", 0),                      # debt: "liquidity" as well as "liquid"
])
def test_debt_and_hybrid_titles(title, active):
    assert db.classify_scheme_title(title) == active


@pytest.mark.parametrize("title", [
    "ICICI Prudential Bluechip Fund",
    "SBI Contra Fund",
    "HDFC Mid-Cap Opportunities Fund",
    "ICICI Prudential Value Discovery Fund",
])
def test_genuine_active_equity_titles_stay_active(title):
    assert db.classify_scheme_title(title) == 1


def test_savings_fund_is_not_caught_by_a_stray_saver_substring():
    # "ELSS Tax Saver" must not be swept up by a careless "save"-prefix match.
    assert db.classify_scheme_title("SBI ELSS Tax Saver Fund") == 1


# ---- re-deriving flags already stored in an existing DB --------------------

def _parsed_csv(tmp_path, name, scheme_name, title):
    path = tmp_path / name
    pd.DataFrame([{
        "isin": "INEAAA01001", "instrument_name": "A Ltd", "quantity": 1,
        "market_value_lakhs": 1.0, "pct_nav": 100.0, "pct_nav_scale": "percent",
        "scheme_name": scheme_name, "scheme_title": title, "amc_name": "ICICI Prudential AMC",
        "report_month": "2026-08",
    }]).to_csv(path, index=False)
    return path


def test_from_db_flag_updates_a_stored_misclassification(tmp_path):
    """Simulates the real bug: a title was recorded under the old regex
    (is_active_equity=1 stuck from before the fix existed), and --from-db
    re-derives it from the classifier with no workbook re-read."""
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _parsed_csv(tmp_path, "p.csv", "SAVING", "ICICI Prudential Savings Fund"))
    db.backfill_is_active_equity(conn)
    # A fresh load is already classified correctly by the fixed rule.
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] == 0

    # Force the DB back into the pre-fix (buggy) state, as if the row had
    # been written before this classifier fix existed.
    conn.execute("UPDATE schemes SET is_active_equity = 1")
    conn.commit()

    dry = scheme_titles.reclassify_from_stored_titles(conn, dry_run=True)
    assert dry["changes"] == [
        ("ICICI Prudential AMC", "SAVING", "ICICI Prudential Savings Fund", 1, 0)
    ]
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] == 1  # unwritten

    live = scheme_titles.reclassify_from_stored_titles(conn, dry_run=False)
    assert live["changes"] == dry["changes"]
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] == 0
    conn.close()


def test_from_db_is_a_no_op_when_nothing_is_untitled_or_stale(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(
        conn, _parsed_csv(tmp_path, "p.csv", "BLUECHIP", "ICICI Prudential Bluechip Fund")
    )
    db.backfill_is_active_equity(conn)
    result = scheme_titles.reclassify_from_stored_titles(conn, dry_run=True)
    assert result["changes"] == []
    assert result["recorded"] == 1
    conn.close()


def test_from_db_cli_flag_rejects_amc_and_files(tmp_path, capsys):
    db_path = str(tmp_path / "t.db")
    conn = db.get_connection(db_path)
    conn.close()
    with pytest.raises(SystemExit):
        scheme_titles.main(["--db", db_path, "--from-db", "--amc", "SBI AMC"])


def test_from_db_reports_an_unset_flag_it_would_write(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _parsed_csv(tmp_path, "p.csv", "SAVING", "ICICI Prudential Savings Fund"))
    conn.execute("UPDATE schemes SET is_active_equity = NULL")
    conn.commit()
    dry = scheme_titles.reclassify_from_stored_titles(conn, dry_run=True)
    assert dry["changes"] == [
        ("ICICI Prudential AMC", "SAVING", "ICICI Prudential Savings Fund", None, 0)]
    assert conn.execute("SELECT is_active_equity FROM schemes").fetchone()[0] is None
    conn.close()
