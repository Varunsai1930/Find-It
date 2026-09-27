"""Running a month again -- the same files, corrected files, or after a crash --
must leave the database as if that run were the only one.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from findit.cli import pipeline as pipeline_cli
from findit.core import delta_calculator
from findit.store import db

RELIANCE, HDFC_BANK = "INE002A01018", "INE040A01034"


def _csv(tmp_path, name, month, holdings, scheme="Fund"):
    rows = [{"amc_name": "T AMC", "scheme_name": scheme, "report_month": month,
             "isin": isin, "instrument_name": isin, "industry": "X", "quantity": qty,
             "market_value_lakhs": value, "pct_nav": pct, "pct_nav_scale": "percent"}
            for isin, qty, value, pct in holdings]
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


JULY = [(RELIANCE, 10, 50.0, 50.0), (HDFC_BANK, 10, 50.0, 50.0)]
AUGUST = [(RELIANCE, 12, 60.0, 50.0), (HDFC_BANK, 12, 60.0, 50.0)]
AUGUST_CORRECTED = [(RELIANCE, 12, 120.0, 100.0)]


def _holdings(conn, month):
    return sorted(r[0] for r in conn.execute(
        "SELECT isin FROM mf_holdings_monthly WHERE report_month = ?", (month,)))


def _run(tmp_path, *files, prev="2026-07", curr="2026-08"):
    pipeline_cli.main(["--db", str(tmp_path / "t.db"), "--load", *map(str, files),
                       "--prev", prev, "--curr", curr])
    return sqlite3.connect(tmp_path / "t.db")


def test_a_corrected_file_replaces_the_month_instead_of_adding_to_it(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _csv(tmp_path, "a.csv", "2026-08", AUGUST))
    db.load_parsed_csv(conn, _csv(tmp_path, "b.csv", "2026-08", AUGUST_CORRECTED))
    # HDFC Bank is no longer in the file, so it is no longer held.
    assert _holdings(conn, "2026-08") == [RELIANCE]


def test_a_file_that_empties_a_scheme_empties_its_month(tmp_path):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _csv(tmp_path, "a.csv", "2026-08", AUGUST))
    cash_only = _csv(tmp_path, "b.csv", "2026-08", [(None, None, None, None)])
    frame = pd.read_csv(cash_only).assign(dropped_non_isin_count=3)
    frame.to_csv(cash_only, index=False)
    assert db.load_parsed_csv(conn, cash_only) == 0
    assert _holdings(conn, "2026-08") == []
    assert conn.execute("SELECT COUNT(*) FROM stocks WHERE isin IS NULL").fetchone()[0] == 0


def test_the_same_files_twice_give_the_same_database(tmp_path):
    july, august = (_csv(tmp_path, "j.csv", "2026-07", JULY),
                    _csv(tmp_path, "a.csv", "2026-08", AUGUST))
    tables = ("mf_holdings_monthly", "mf_holding_deltas", "scheme_month_status", "schemes")

    def snapshot(conn):
        return {t: sorted(map(repr, conn.execute(f"SELECT * FROM {t}"))) for t in tables}

    first = snapshot(_run(tmp_path, july, august))
    second = snapshot(_run(tmp_path, july, august))
    assert first == second


def test_changed_holdings_clear_what_was_derived_from_them(tmp_path, capsys):
    july, august = (_csv(tmp_path, "j.csv", "2026-07", JULY),
                    _csv(tmp_path, "a.csv", "2026-08", AUGUST))
    conn = _run(tmp_path, july, august)
    september = _csv(tmp_path, "s.csv", "2026-09", AUGUST)
    conn = _run(tmp_path, september, prev="2026-08", curr="2026-09")
    assert conn.execute("SELECT COUNT(*) FROM mf_holding_deltas "
                        "WHERE report_month = '2026-09'").fetchone()[0] == 2
    capsys.readouterr()

    # A corrected August, loaded on its own: the stored status and every
    # comparison built on the old August no longer describe what is stored.
    wconn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(wconn, _csv(tmp_path, "a2.csv", "2026-08", AUGUST_CORRECTED))
    assert "re-run the pipeline for 2026-09" in capsys.readouterr().out
    assert wconn.execute("SELECT COUNT(*) FROM scheme_month_status "
                         "WHERE report_month = '2026-08'").fetchone()[0] == 0
    assert wconn.execute("SELECT COUNT(*) FROM mf_holding_deltas WHERE report_month "
                         "IN ('2026-08', '2026-09')").fetchone()[0] == 0
    # July is untouched.
    assert wconn.execute("SELECT COUNT(*) FROM scheme_month_status "
                         "WHERE report_month = '2026-07'").fetchone()[0] == 1


def test_a_failure_part_way_through_a_file_writes_nothing(tmp_path, monkeypatch):
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _csv(tmp_path, "a.csv", "2026-08", AUGUST))

    def boom(isin):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "classify_isin", boom)
    new_scheme = _csv(tmp_path, "b.csv", "2026-08", AUGUST_CORRECTED, scheme="Other Fund")
    frame = pd.concat([pd.read_csv(new_scheme),
                       pd.read_csv(_csv(tmp_path, "c.csv", "2026-08", AUGUST_CORRECTED))])
    frame.to_csv(new_scheme, index=False)
    with pytest.raises(RuntimeError, match="disk full"):
        db.load_parsed_csv(conn, new_scheme)
    # Neither the new scheme nor the correction to the old one landed.
    assert [r[0] for r in conn.execute("SELECT scheme_name FROM schemes")] == ["Fund"]
    assert _holdings(conn, "2026-08") == [RELIANCE, HDFC_BANK]


def test_the_run_log_never_calls_an_unfinished_run_completed(tmp_path, monkeypatch):
    july, august = (_csv(tmp_path, "j.csv", "2026-07", JULY),
                    _csv(tmp_path, "a.csv", "2026-08", AUGUST))
    conn = _run(tmp_path, july, august)
    assert conn.execute("SELECT status FROM ingest_runs").fetchone()[0] == "completed"

    def crash(*args, **kwargs):
        raise RuntimeError("ranking crashed")

    monkeypatch.setattr(delta_calculator, "persist_deltas", crash)
    with pytest.raises(RuntimeError):
        _run(tmp_path, july, august)
    status, report = conn.execute(
        "SELECT status, validation_report_json FROM ingest_runs "
        "ORDER BY started_at DESC LIMIT 1").fetchone()
    assert status == "failed"
    report = json.loads(report)
    assert report["error"] == "RuntimeError: ranking crashed"
    assert report["ok_count"] == 2  # the gate had finished and says so


def test_an_isin_on_several_lines_is_one_holding(tmp_path):
    """Lots are summed, blank note lines never overwrite a real line, and
    zero-quantity lines (a written-off bond) are not added up."""
    lots = [(HDFC_BANK, 2857268, 21376.65, 5.63), (HDFC_BANK, 1164800, 8714.45, 2.29),
            (RELIANCE, 50, 5401.89, 4.5), (RELIANCE, None, None, None),
            ("INE975G08140", 0, 5965.0, 37.2), ("INE975G08140", 0, None, 18.8),
            ("INE975G08140", 0, None, 34.3)]
    conn = db.get_connection(str(tmp_path / "t.db"))
    db.load_parsed_csv(conn, _csv(tmp_path, "a.csv", "2026-08", lots))
    stored = {isin: (qty, value, pct) for isin, qty, value, pct in conn.execute(
        "SELECT isin, quantity, market_value_lakhs, pct_nav FROM mf_holdings_monthly")}
    assert stored[HDFC_BANK] == (2857268 + 1164800, 21376.65 + 8714.45, 5.63 + 2.29)
    assert stored[RELIANCE] == (50, 5401.89, 4.5)
    assert stored["INE975G08140"] == (0, None, 34.3)  # as before: the last line stands
