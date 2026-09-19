"""Re-running the validation gate over stored data. Temp DBs only, no network."""

import hashlib
import json

import pandas as pd

import db
from findit.cli import revalidate


def _csv(tmp_path, name, scheme, month, pct=99.0, qty=100.0):
    path = tmp_path / name
    pd.DataFrame([{
        "isin": "INE002A01018", "instrument_name": "Reliance Industries Ltd.",
        "industry": "Refineries", "quantity": qty, "market_value_lakhs": 500.0,
        "pct_nav": pct, "scheme_name": scheme, "amc_name": "HDFC AMC",
        "report_month": month,
    }]).to_csv(path, index=False)
    return path


def _loaded(tmp_path, pct=99.0):
    path_db = tmp_path / "t.db"
    conn = db.get_connection(str(path_db))
    db.load_parsed_csv(conn, _csv(tmp_path, "m.csv", "HDFC Mid Cap Fund", "2026-07", pct=pct))
    conn.commit()
    conn.close()
    return path_db


def _set_status(path_db, status, report=None, source_hash="abc123"):
    conn = db.get_connection(str(path_db))
    sid = conn.execute("SELECT scheme_id FROM schemes").fetchone()[0]
    conn.execute(
        "INSERT OR REPLACE INTO scheme_month_status (scheme_id, report_month, status, "
        "validation_report_json, source_data_hash) VALUES (?, '2026-07', ?, ?, ?)",
        (sid, status, json.dumps(report or {}), source_hash))
    conn.commit()
    conn.close()
    return sid


def test_stale_quarantine_is_cleared_when_the_data_passes(tmp_path):
    """The point of the command: a rule that has since become warn-only."""
    path_db = _loaded(tmp_path)
    _set_status(path_db, "quarantined", {"issues": [
        {"code": "qty_ratio_out_of_band", "severity": "error", "message": "old rule"}]})
    result = revalidate.revalidate(path_db)
    assert result["after"] == {"ok": 1}
    assert [c["to"] for c in result["changes"]] == ["ok"]


def test_genuinely_bad_data_stays_quarantined(tmp_path):
    path_db = _loaded(tmp_path, pct=126.5)
    result = revalidate.revalidate(path_db)
    assert result["after"] == {"quarantined": 1}
    conn = db.get_connection(str(path_db))
    report = json.loads(conn.execute(
        "SELECT validation_report_json FROM scheme_month_status").fetchone()[0])
    assert any(i["code"] == "nav_sum_quarantine" for i in report["issues"])
    conn.close()


def test_scheme_month_without_a_status_row_gets_validated(tmp_path):
    """Holdings that never went through the gate are unvalidated, not ok."""
    path_db = _loaded(tmp_path)
    conn = db.get_connection(str(path_db))
    assert conn.execute("SELECT COUNT(*) FROM scheme_month_status").fetchone()[0] == 0
    conn.close()
    result = revalidate.revalidate(path_db)
    assert result["scheme_months"] == 1
    assert result["after"] == {"ok": 1}


def test_dry_run_leaves_the_source_database_untouched(tmp_path):
    path_db = _loaded(tmp_path)
    _set_status(path_db, "quarantined")
    before = hashlib.sha256(path_db.read_bytes()).hexdigest()
    result = revalidate.revalidate_dry_run(path_db)
    assert result["after"] == {"ok": 1}
    assert hashlib.sha256(path_db.read_bytes()).hexdigest() == before
    conn = db.get_connection(str(path_db))
    assert conn.execute("SELECT status FROM scheme_month_status").fetchone()[0] == "quarantined"
    conn.close()


def test_existing_provenance_and_hashes_are_carried_over(tmp_path):
    """Re-validation must never invent provenance the ingest did not record."""
    path_db = _loaded(tmp_path)
    _set_status(path_db, "ok", {"dropped_non_isin_count": 4,
                                "dropped_non_isin_pct_nav": 1.5},
                source_hash="hash1+hash2")
    conn = db.get_connection(str(path_db))
    touched = revalidate.rebuild_touched(conn)
    conn.close()
    entry = list(touched.values())[0]
    assert entry["dropped_non_isin_count"] == 4
    assert entry["dropped_non_isin_pct_nav"] == 1.5
    assert entry["hashes"] == ["hash1", "hash2"]

    revalidate.revalidate(path_db)
    conn = db.get_connection(str(path_db))
    row = conn.execute("SELECT validation_report_json, source_data_hash "
                       "FROM scheme_month_status").fetchone()
    conn.close()
    report = json.loads(row[0])
    assert report["dropped_non_isin_count"] == 4
    assert row[1] == "hash1+hash2"


def test_month_filter_restricts_the_run(tmp_path):
    path_db = _loaded(tmp_path)
    conn = db.get_connection(str(path_db))
    db.load_parsed_csv(conn, _csv(tmp_path, "m2.csv", "HDFC Mid Cap Fund", "2026-08"))
    conn.close()
    result = revalidate.revalidate(path_db, months=["2026-08"])
    assert result["scheme_months"] == 1
    assert list(result["after"]) == ["ok"]


def test_cli_reports_no_change_when_statuses_already_match(tmp_path, capsys):
    path_db = _loaded(tmp_path)
    revalidate.main(["--db", str(path_db)])
    capsys.readouterr()
    assert revalidate.main(["--db", str(path_db)]) == 0
    assert "no status changes" in capsys.readouterr().out
