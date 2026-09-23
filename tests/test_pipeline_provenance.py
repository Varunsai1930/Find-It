"""Parser-to-pipeline provenance regression; all files live under tmp_path."""
import json
import sqlite3
import sys

import openpyxl
import pytest

import amfi_mf_parser
import db
import run_pipeline


@pytest.mark.parametrize("equity_nav", [0.0, 1.0, 4.0])
def test_dropped_nav_survives_csv_and_pipeline(tmp_path, monkeypatch, capsys, equity_nav):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Cash Heavy Fund"
    sheet.append([
        "Name of the Instrument", "ISIN", "Industry", "Quantity",
        "Market Value (Rs. in Lakhs)", "% to NAV",
    ])
    if equity_nav:
        sheet.append(["Equity Co", "INE002A01018", "X", 100, equity_nav, equity_nav])
    sheet.append(["TREPS", None, None, None, 100 - equity_nav, 100 - equity_nav])
    sheet.append(["Grand Total", None, None, None, 100, 100])
    xlsx = tmp_path / "source.xlsx"
    workbook.save(xlsx)
    csv_path = tmp_path / "source.csv"
    amfi_mf_parser.parse_workbook(xlsx, "Test AMC", "2026-08").to_csv(csv_path, index=False)
    db_path = tmp_path / "tracker.db"
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py", "--db", str(db_path), "--load", str(csv_path),
        "--prev", "2026-07", "--curr", "2026-08",
    ])
    run_pipeline.main()
    output = capsys.readouterr().out
    assert "Validation gate: 1 ok, 0 quarantined" in output
    with sqlite3.connect(db_path) as conn:
        status, raw = conn.execute(
            "SELECT status, validation_report_json FROM scheme_month_status"
        ).fetchone()
        assert status == "ok"
        report = json.loads(raw)
        assert report["nav_sum"] == pytest.approx(equity_nav)
        assert report["dropped_non_isin_count"] == 1
        assert report["dropped_non_isin_pct_nav"] == pytest.approx(100 - equity_nav)
        assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == bool(equity_nav)
        assert conn.execute("SELECT COUNT(*) FROM mf_holdings_monthly").fetchone()[0] == bool(equity_nav)
        count = conn.execute("SELECT COUNT(*) FROM schemes").fetchone()[0]
        assert count == 1
    # Reopening the database must not mistake a 1% equity allocation for
    # fractional units and silently multiply already-normalized data by100.
    reopened = db.get_connection(str(db_path))
    total = reopened.execute("SELECT COALESCE(SUM(pct_nav),0) FROM mf_holdings_monthly").fetchone()[0]
    assert total == pytest.approx(equity_nav)
    reopened.close()


def test_legacy_reload_clears_dropped_row_provenance(tmp_path, monkeypatch, capsys):
    import pandas as pd

    row = {
        "isin": "INE002A01018", "instrument_name": "Equity Co", "quantity": 100,
        "market_value_lakhs": 4, "pct_nav": 4, "pct_nav_scale": "percent",
        "amc_name": "Test AMC", "scheme_name": "Cash Heavy Fund", "report_month": "2026-08",
        "dropped_non_isin_count": 1, "dropped_non_isin_pct_nav": 96,
    }
    modern = tmp_path / "modern.csv"
    legacy = tmp_path / "legacy.csv"
    pd.DataFrame([row]).to_csv(modern, index=False)
    pd.DataFrame([row]).drop(columns=[
        "dropped_non_isin_count", "dropped_non_isin_pct_nav", "pct_nav_scale",
    ]).to_csv(legacy, index=False)
    db_path = tmp_path / "reload.db"
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py", "--db", str(db_path), "--load", str(modern), str(legacy),
        "--prev", "2026-07", "--curr", "2026-08",
    ])
    run_pipeline.main()
    with sqlite3.connect(db_path) as conn:
        report = json.loads(conn.execute(
            "SELECT validation_report_json FROM scheme_month_status"
        ).fetchone()[0])
    assert report["nav_sum"] == 4
    assert report["dropped_non_isin_count"] is None
    assert report["dropped_non_isin_pct_nav"] is None


def test_pipeline_classifies_fresh_schemes_and_reports_overlap(tmp_path, monkeypatch, capsys):
    """Fresh schemes must reach consensus in the same run (no NULL active flag).

    Regression: backfill ran only at connect time, so newly loaded schemes
    stayed NULL and the ``is_active_equity = 1`` consensus filter silently
    emptied the consensus (and the MF+FII overlap with it).
    """
    import pandas as pd

    def _row(amc, scheme, month, qty, val, nav):
        return {
            "isin": "INE002A01018", "instrument_name": "Reliance Industries Ltd.",
            "quantity": qty, "market_value_lakhs": val, "pct_nav": nav,
            "amc_name": amc, "scheme_name": scheme, "report_month": month,
        }

    prev = tmp_path / "prev.csv"
    curr_a = tmp_path / "curr_a.csv"
    curr_b = tmp_path / "curr_b.csv"
    pd.DataFrame([
        _row("HDFC AMC", "Alpha Growth Fund", "2026-03", 1000, 380.0, 6.0),
        _row("SBI AMC", "Beta Growth Fund", "2026-03", 500, 190.0, 5.0),
    ]).to_csv(prev, index=False)
    pd.DataFrame([_row("HDFC AMC", "Alpha Growth Fund", "2026-04", 1200, 460.0, 6.5)]).to_csv(
        curr_a, index=False)
    pd.DataFrame([_row("SBI AMC", "Beta Growth Fund", "2026-04", 600, 230.0, 5.5)]).to_csv(
        curr_b, index=False)
    db_path = tmp_path / "tracker.db"
    monkeypatch.setattr(sys, "argv", [
        "run_pipeline.py", "--db", str(db_path),
        "--load", str(prev), "--load", str(curr_a), str(curr_b),
        "--prev", "2026-03", "--curr", "2026-04",
    ])
    run_pipeline.main()
    output = capsys.readouterr().out
    # Both AMCs added to Reliance: MF consensus must be non-empty in this run.
    assert "INE002A01018" in output
    assert "=== MF + FII/DII overlap, 2026-04 ===" in output
    # No shareholding loaded yet: honestly empty, never zero-filled.
    assert "(no MF+FII/DII common increases this month)" in output
    with sqlite3.connect(db_path) as conn:
        flags = conn.execute("SELECT DISTINCT is_active_equity FROM schemes").fetchall()
        assert flags == [(1,)]
        raw = conn.execute(
            "SELECT validation_report_json FROM ingest_runs"
        ).fetchone()[0]
        overlap = json.loads(raw)["mf_fii_overlap"]
        assert overlap["status"] == "ok"
        assert overlap["consensus_count"] >= 1
        assert overlap["common_count"] == 0
        assert overlap["missing_shareholding_count"] == overlap["consensus_count"]
