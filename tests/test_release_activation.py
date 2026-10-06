"""Monthly publication cannot bypass source, validation, or review gates."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from findit.cli.release import main as release_main
from findit.store.releases import activate_release, snapshot
from tests.test_web import _copy_db


def _monthly_source(tmp_path: Path) -> Path:
    """A preloaded, source-backed adjacent pair with a completed run report."""
    path = _copy_db(tmp_path)
    with sqlite3.connect(path) as conn:
        # These portfolios total 100% rather than the compact web fixture's 10% rows.
        conn.execute("UPDATE mf_holdings_monthly SET pct_nav=100.0 / (SELECT count(*) "
                     "FROM mf_holdings_monthly sibling WHERE sibling.scheme_id=mf_holdings_monthly.scheme_id "
                     "AND sibling.report_month=mf_holdings_monthly.report_month)")
        for sid, month in conn.execute("SELECT DISTINCT scheme_id,report_month FROM mf_holdings_monthly").fetchall():
            sha = hashlib.sha256(f"workbook-{sid}-{month}".encode()).hexdigest()
            conn.execute("INSERT INTO disclosure_sources VALUES(?,?,?,?,?)",
                         (sha, f"fund-{sid}-{month}.xlsx", "https://example.com/disclosure.xlsx",
                          "2026-09-01T00:00:00Z", None))
            conn.execute("INSERT INTO snapshot_sources VALUES(?,?,?,?)", (sid, month, sha, "fixture-v1"))
            for index, row in enumerate(conn.execute(
                    "SELECT isin,quantity,market_value_lakhs,pct_nav FROM mf_holdings_monthly "
                    "WHERE scheme_id=? AND report_month=?", (sid, month)).fetchall(), 1):
                normalized = dict(zip(("isin", "quantity", "market_value_lakhs", "pct_nav"), row))
                conn.execute("INSERT INTO holding_evidence VALUES(?,?,?,?,?,?,?,?,?)",
                             (sid, month, row[0], sha, "Portfolio", index,
                              json.dumps(normalized), json.dumps(normalized), "fixture-v1"))
            conn.execute("UPDATE scheme_month_status SET source_data_hash=? "
                         "WHERE scheme_id=? AND report_month=?", (sha, sid, month))
        conn.execute("INSERT INTO ingest_runs VALUES(?,?,?,?)",
                     ("monthly-fixture", "2026-09-01T00:00:00Z", "completed",
                      json.dumps({"prev": "2026-07", "curr": "2026-08", "files": []})))
    return path


def test_candidates_never_implicitly_activate(tmp_path):
    source = _copy_db(tmp_path)
    output = tmp_path / "releases"
    result = snapshot(source, output, "Generic archive")
    assert (output / f"{result['release_id']}.db").is_file()
    assert not (output / "current.json").exists()
    with pytest.raises(ValueError, match="report month"):
        snapshot(source, output, "Activate", activate=True, notes="reviewed")
    with pytest.raises(ValueError, match="review notes"):
        snapshot(source, output, "Activate", activate=True, report_month="2026-08")
    assert not (output / "current.json").exists()


def test_monthly_activation_records_receipt_without_rewriting_candidate_manifest(tmp_path):
    source = _monthly_source(tmp_path)
    output = tmp_path / "releases"
    candidate = snapshot(source, output, "Candidate", report_month="2026-08")
    manifest = output / f"{candidate['release_id']}.json"
    original = manifest.read_bytes()
    receipt = activate_release(output, candidate["release_id"], notes="Reviewed workbook and anomalies")
    pointer = json.loads((output / "current.json").read_text())
    saved = json.loads((output / pointer["activation_receipt"]).read_text())
    assert saved == receipt
    assert saved["report_month"] == "2026-08"
    assert saved["previous_month"] == "2026-07"
    assert saved["source_backed_compared_funds"] == [1, 2, 3]
    assert saved["coverage"]["market_completeness"] == "unknown"
    assert saved["coverage"]["houses"]
    assert manifest.read_bytes() == original


@pytest.mark.parametrize("change,error", [
    ("DELETE FROM ingest_runs", "completed monthly"),
    ("UPDATE ingest_runs SET status='completed_with_quarantine'", "did not complete"),
    ("UPDATE ingest_runs SET validation_report_json='{}'", "does not match"),
    ("DELETE FROM snapshot_sources WHERE report_month='2026-08'", "actual-month"),
    ("DELETE FROM holding_evidence WHERE report_month='2026-08'", "actual-month"),
    ("DELETE FROM snapshot_sources WHERE report_month='2026-07'", "source-backed adjacent"),
    ("UPDATE scheme_month_status SET status='quarantined' WHERE report_month='2026-07'", "source-backed adjacent"),
    ("DELETE FROM mf_holding_deltas WHERE report_month='2026-08'", "source-backed adjacent"),
])
def test_failed_monthly_guard_retains_last_good_pointer(tmp_path, change, error):
    source = _monthly_source(tmp_path)
    output = tmp_path / "releases"
    baseline = snapshot(source, output, "Good", activate=True, report_month="2026-08", notes="reviewed")
    pointer = (output / "current.json").read_bytes()
    with sqlite3.connect(source) as conn:
        conn.execute(change)
    with pytest.raises(ValueError, match=error):
        snapshot(source, output, "Rejected", activate=True, report_month="2026-08", notes="reviewed")
    assert (output / "current.json").read_bytes() == pointer
    assert json.loads(pointer)["release_id"] == baseline["release_id"]


def test_rollback_uses_same_guard_and_explicit_legacy_month(tmp_path):
    source = _monthly_source(tmp_path)
    output = tmp_path / "releases"
    from findit.core.rules import LEGACY_RULE_VERSION
    old = snapshot(source, output, "Legacy", rules_version=LEGACY_RULE_VERSION)
    manifest_path = output / f"{old['release_id']}.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("report_month")
    manifest_path.write_text(json.dumps(manifest))
    original = manifest_path.read_bytes()
    with pytest.raises(SystemExit):
        release_main(["--out", str(output), "--rollback", old["release_id"], "--notes", "reviewed"])
    assert not (output / "current.json").exists()
    assert release_main(["--out", str(output), "--rollback", old["release_id"],
                         "--month", "2026-08", "--notes", "Reviewed original month"]) == 0
    assert manifest_path.read_bytes() == original
    pointer = (output / "current.json").read_bytes()
    with pytest.raises(SystemExit):
        release_main(["--out", str(output), "--rollback", old["release_id"],
                      "--month", "2026-09", "--notes", "reviewed"])
    assert (output / "current.json").read_bytes() == pointer


def test_pointer_write_failure_preserves_current_release(tmp_path, monkeypatch):
    source = _monthly_source(tmp_path)
    output = tmp_path / "releases"
    good = snapshot(source, output, "Good", activate=True, report_month="2026-08", notes="reviewed")
    pointer = (output / "current.json").read_bytes()
    original_replace = Path.replace

    def fail_pointer(self, target):
        if Path(target) == output / "current.json":
            raise OSError("pointer write failed")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_pointer)
    with pytest.raises(OSError, match="pointer write failed"):
        activate_release(output, good["release_id"], notes="reviewed")
    assert (output / "current.json").read_bytes() == pointer
