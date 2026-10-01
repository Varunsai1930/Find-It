import json
import sqlite3

from fastapi.testclient import TestClient

from findit.core.evidence import stock_evidence
from findit.ingest.amfi_mf_parser import parse_workbook
from findit.store.db import get_connection, load_parsed_csv
from findit.web.app import create_app
from tests.test_parser_provenance import workbook
from tests.test_web import _copy_db


def test_evidence_reconciles_opposing_trades_and_preserves_scope(tmp_path):
    path = _copy_db(tmp_path)
    c = sqlite3.connect(path)
    # Infosys: scheme 1 reduces 20, scheme 2 unchanged, scheme 3 adds 30.
    e = stock_evidence(c, "INE009A01021", "2026-08")
    assert e["gross_added_shares"] == 30 and e["gross_reduced_shares"] == 20
    assert e["net_share_change"] == 10
    scoped = stock_evidence(c, "INE009A01021", "2026-08", amc="A AMC")
    assert scoped["net_share_change"] == -20
    assert scoped["coverage"]["A AMC"]["compared"] == 2
    assert all(f["current_source"]["provenance_status"] == "unavailable" for f in e["funds"])
    c.execute("DELETE FROM scheme_month_status WHERE scheme_id=1 AND report_month='2026-07'")
    assert stock_evidence(c, "INE009A01021", "2026-08", amc="A AMC")["net_share_change"] == 0


def test_parser_and_loader_keep_original_rows_and_revisions(tmp_path):
    path = workbook(tmp_path, [["Example", "INE123A01012", 100, 60, 0.6],
                              ["TREPS", None, 10, 40, 0.4], ["Total", None, None, 100, 1]])
    (tmp_path / ".findit-download.json").write_text(json.dumps({"files": []}))
    frame = parse_workbook(path, "AMC", "2026-08")
    assert frame.iloc[0]["source_row"] == 2
    assert json.loads(frame.iloc[0]["source_raw_json"])["pct_nav"] == "0.6"
    csv = tmp_path / "parsed.csv"
    frame.to_csv(csv, index=False)
    c = get_connection(tmp_path / "working.db")
    load_parsed_csv(c, csv)
    load_parsed_csv(c, csv)
    assert c.execute("SELECT count(*) FROM holding_evidence").fetchone()[0] == 1
    old_hash = frame.iloc[0]["source_sha256"]
    frame["source_sha256"] = "b" * 64
    frame["quantity"] = 120
    frame.to_csv(csv, index=False)
    load_parsed_csv(c, csv)
    assert c.execute("SELECT count(*) FROM holding_evidence").fetchone()[0] == 2
    assert c.execute("SELECT source_sha256 FROM snapshot_sources").fetchone()[0] != old_hash


def test_evidence_links_never_expose_filesystem_paths(tmp_path):
    path = _copy_db(tmp_path)
    client = TestClient(create_app(path))
    response = client.get("/evidence/INE002A01018", params={"month": "2026-08", "amc": "A AMC"})
    assert response.status_code == 200 and "Official" not in response.text
    assert str(tmp_path) not in response.text and "provenance is unavailable" in response.text
    assert client.get("/api/evidence/INE002A01018", params={"month": "2026-01"}).status_code == 404
