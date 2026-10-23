"""Local recovery keeps the source database and original disclosures intact."""
import hashlib
import json
import sqlite3

import openpyxl
import pytest

from findit.store import db
from tools.recover_local_data import copy_originals, recover, rebuild_later_comparisons


def _workbook(path, month):
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "HDFCTEST"
    sheet.append(["HDFC Test Equity Fund"])
    sheet.append([f"Portfolio as on {month} 31, 2026"])
    sheet.append(["Name of the Instrument", "ISIN", "Quantity", "Market Value", "% to NAV"])
    sheet.append(["Coforge Ltd", "INE591G01017", 100, 100, 100])
    workbook.save(path)


def test_recovery_reparses_sources_on_a_copy_and_retains_unknown_dates(tmp_path):
    source = tmp_path / "source.db"
    db.get_connection(source).close()
    source_bytes = source.read_bytes()
    inbox = tmp_path / "originals"
    originals = []
    for key, name in (("2026-07", "July"), ("2026-08", "August")):
        workbook = inbox / key / "HDFC AMC" / "portfolio.xlsx"
        _workbook(workbook, name)
        originals.append((workbook, workbook.read_bytes()))
    report = recover(source, [inbox], tmp_path / "recovered", "2026-07", "2026-08")
    assert report["status"] == "candidate_ready_for_review"
    assert report["checks"]["compared_active_funds"] == 1
    assert report["checks"]["source_backed_active_funds"] == 1
    assert source.read_bytes() == source_bytes
    assert all(path.read_bytes() == contents for path, contents in originals)
    with sqlite3.connect(report["candidate_database"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM holding_evidence").fetchone()[0] == 2
        assert conn.execute("SELECT source_url,retrieved_at,published_at FROM disclosure_sources").fetchall() == [(None, None, None), (None, None, None)]


def test_later_inbox_replaces_the_whole_amc_folder_without_touching_originals(tmp_path):
    first, later = tmp_path / "first", tmp_path / "later"
    for root, filename in ((first, "stale.xlsx"), (later, "corrected.xlsx")):
        target = root / "2026-08" / "HDFC AMC" / filename
        _workbook(target, "August")
    source = later / "2026-08" / "HDFC AMC"
    (source / "_unzipped").mkdir()
    (source / "_unzipped" / "stale.xlsx").write_text("do not copy")
    target = tmp_path / "copy"
    report = copy_originals([first, later], target, ["2026-08"])
    assert [p.name for p in (target / "2026-08" / "HDFC AMC").iterdir()] == ["corrected.xlsx"]
    assert len(report) == 1
    assert report[0]["files"][0]["sha256"] == hashlib.sha256((source / "corrected.xlsx").read_bytes()).hexdigest()
    assert (source / "_unzipped" / "stale.xlsx").is_file()


def test_bad_workbook_cannot_produce_a_candidate(tmp_path):
    source = tmp_path / "source.db"
    db.get_connection(source).close()
    originals = source.read_bytes()
    root = tmp_path / "originals"
    bad = root / "2026-07" / "HDFC AMC" / "broken.xlsx"
    bad.parent.mkdir(parents=True)
    bad.write_text("not a workbook")
    output = tmp_path / "recovered"
    with pytest.raises(ValueError, match="intake refused"):
        recover(source, [root], output, "2026-07", "2026-08")
    assert source.read_bytes() == originals
    assert not (output / "candidate.db").exists()
    assert json.loads((output / "recovery.json").read_text())["status"] == "failed"


def test_unsuccessful_refresh_is_never_marked_ready(tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    db.get_connection(source).close()
    root = tmp_path / "originals"
    for month, name in (("2026-07", "July"), ("2026-08", "August")):
        _workbook(root / month / "HDFC AMC" / "portfolio.xlsx", name)
    monkeypatch.setattr("tools.recover_local_data.refresh", lambda *a, **kw: {"status": "failed"})
    output = tmp_path / "recovered"
    with pytest.raises(ValueError, match="reviewable candidate"):
        recover(source, [root], output, "2026-07", "2026-08")
    assert json.loads((output / "recovery.json").read_text())["status"] == "failed"


def test_following_month_is_recomputed_after_recovering_its_baseline(tmp_path):
    source = tmp_path / "candidate.db"
    conn = db.get_connection(source)
    conn.execute("INSERT INTO schemes (scheme_id,amc_name,scheme_name,is_active_equity) VALUES (1,'HDFC AMC','TEST',1)")
    conn.execute("INSERT INTO stocks (isin,name,instrument_type) VALUES ('INE591G01017','Coforge','equity')")
    for month, quantity in (("2026-08", 100), ("2026-09", 300)):
        conn.execute("INSERT INTO mf_holdings_monthly (scheme_id,isin,report_month,quantity,market_value_lakhs,pct_nav) VALUES (1,'INE591G01017',?,?,100,100)", (month, quantity))
        conn.execute("INSERT INTO scheme_month_status (scheme_id,report_month,status) VALUES (1,?,'ok')", (month,))
    conn.commit()
    conn.close()
    assert rebuild_later_comparisons(source, "2026-08") == ["2026-09"]
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT qty_change,prev_month FROM mf_holding_deltas WHERE report_month='2026-09'").fetchone() == (200, "2026-08")
