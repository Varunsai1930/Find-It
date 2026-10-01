import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from findit.cli.release import main as release_main
from findit.core.watchlist import monthly_report, parse_stocks, render_report
from findit.store.releases import snapshot
from findit.web.app import create_app
from tests.test_web import _copy_db


def test_report_reproducible_order_and_release_change(tmp_path):
    path = _copy_db(tmp_path)
    c = sqlite3.connect(path)
    stocks = ["INE002A01018", "INE009A01021"]
    first = monthly_report(c, "2026-08", stocks)
    assert render_report(first) == render_report(monthly_report(c, "2026-08", stocks[::-1]))
    c.execute("UPDATE mf_holdings_monthly SET quantity=quantity+1 WHERE scheme_id=1 AND report_month='2026-08'")
    second = monthly_report(c, "2026-08", stocks)
    assert first["release_id"] != second["release_id"]
    unknown = monthly_report(c, "2026-08", ["INE123A01012"])["stocks"][0]
    assert unknown["status"] == "unknown_stock" and unknown["net_share_change"] is None
    assert unknown["houses_buying"] is None and unknown["exits"] is None


def test_report_api_validation_and_download(tmp_path):
    client = TestClient(create_app(_copy_db(tmp_path)))
    assert client.get("/api/watchlist", params={"month": "2026-08", "stocks": "bad"}).status_code == 400
    report = client.get("/watchlist/report", params={"month": "2026-08", "stocks": "INE002A01018"})
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    assert "Data release:" in report.text and "All-market completeness: unknown" in report.text
    assert parse_stocks("ine002a01018,INE002A01018") == ["INE002A01018"]


def test_net_unchanged_can_still_contain_opposing_fund_changes(tmp_path):
    c = sqlite3.connect(_copy_db(tmp_path))
    # Existing fixture: Infosys -20 in fund 1, +30 in fund 3.
    # Make the positive change +20, so gross activity still exists at net zero.
    c.execute("UPDATE mf_holdings_monthly SET quantity=quantity-10 WHERE scheme_id=3 AND isin='INE009A01021' AND report_month='2026-08'")
    stock = monthly_report(c, "2026-08", ["INE009A01021"])["stocks"][0]
    assert stock["status"] == "net_unchanged_with_opposing_changes"
    assert stock["net_share_change"] == 0
    assert stock["houses_buying"] == stock["houses_selling"] == 1


def test_release_preserves_original_and_failed_refresh_retains_pointer(tmp_path):
    path = _copy_db(tmp_path)
    before = path.read_bytes()
    folder = tmp_path / "releases"
    first = snapshot(path, folder, "first")
    assert path.read_bytes() == before
    assert snapshot(path, folder, "same")["release_id"] == first["release_id"]
    pointer = (folder / "current.json").read_bytes()
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO ingest_runs(run_id,started_at,status) VALUES('failure','2026-10-01','failed')")
    with pytest.raises(ValueError, match="did not complete"):
        snapshot(path, folder, "bad")
    assert (folder / "current.json").read_bytes() == pointer
    assert release_main(["--out", str(folder), "--rollback", first["release_id"]]) == 0
    assert json.loads((folder / "current.json").read_text())["release_id"] == first["release_id"]
    (folder / f"{first['release_id']}.db").write_bytes(b"corrupt")
    with pytest.raises(SystemExit):
        release_main(["--out", str(folder), "--rollback", first["release_id"]])
    assert (folder / "current.json").read_bytes() == pointer
