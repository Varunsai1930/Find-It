import pytest

from findit.cli.coverage import import_inventory
from findit.core.coverage import house_coverage
from findit.store.db import get_connection


def setup_inventory(tmp_path):
    c = get_connection(tmp_path / "working.db")
    c.execute("INSERT INTO schemes(scheme_id,amc_name,scheme_name,is_active_equity) VALUES(1,'House','Fund',1)")
    for m in ("2026-07", "2026-08"):
        c.execute("INSERT INTO mf_holdings_monthly(scheme_id,isin,report_month,quantity) VALUES(1,'INE123A01012',?,10)", (m,))
        c.execute("INSERT INTO scheme_month_status(scheme_id,report_month,status) VALUES(1,?,'ok')", (m,))
    c.commit()
    doc = {"amc_name": "House", "source_url": "https://official.example/portfolio",
           "source_sha256": "a" * 64, "reviewed_at": "2026-10-01", "exhaustive": True,
           "funds": [{"official_key": "Fund", "official_name": "Fund", "scheme_id": 1,
                      "active_eligible": True, "equity_eligible": True}]}
    return c, doc


def test_complete_needs_both_official_inventories_and_validation(tmp_path):
    c, doc = setup_inventory(tmp_path)
    assert house_coverage(c, "2026-08", "House", {1})["coverage_state"] == "unknown_completeness"
    for m in ("2026-07", "2026-08"):
        import_inventory(c, dict(doc, report_month=m))
    assert house_coverage(c, "2026-08", "House", {1})["coverage_state"] == "complete"
    c.execute("DELETE FROM scheme_month_status WHERE report_month='2026-07'")
    result = house_coverage(c, "2026-08", "House", {1})
    assert result["coverage_state"] == "partial"
    assert result["expected_details"][0]["state"] == "not_validated"


def test_missing_expected_fund_and_unexpected_comparison_preclude_complete(tmp_path):
    c, doc = setup_inventory(tmp_path)
    doc["funds"].append(dict(doc["funds"][0], official_key="Missing", official_name="Missing", scheme_id=None))
    for m in ("2026-07", "2026-08"):
        import_inventory(c, dict(doc, report_month=m))
    result = house_coverage(c, "2026-08", "House", {1})
    assert result["expected"] == 2 and result["coverage_state"] == "partial"
    assert result["expected_details"][1]["state"] == "identity_unresolved"
    assert house_coverage(c, "2026-08", "House", set())["coverage_state"] == "unavailable"


def test_inventory_rejects_duplicate_identity_atomically(tmp_path):
    c, doc = setup_inventory(tmp_path)
    import_inventory(c, dict(doc, report_month="2026-08"))
    doc["funds"].append(dict(doc["funds"][0], official_key="Duplicate"))
    with pytest.raises(ValueError, match="same scheme"):
        import_inventory(c, dict(doc, report_month="2026-08"))
    assert c.execute("SELECT count(*) FROM expected_funds").fetchone()[0] == 1


def test_read_only_audit_does_not_migrate_original(tmp_path):
    from findit.cli.coverage import main
    path = tmp_path / "original.db"
    c = get_connection(path)
    c.close()
    before = path.read_bytes()
    assert main(["--db", str(path), "--month", "2026-08", "--out", str(tmp_path / "audit.json")]) == 0
    assert path.read_bytes() == before


def test_new_official_portfolio_and_known_current_inventory(tmp_path):
    c, doc = setup_inventory(tmp_path)
    import_inventory(c, dict(doc, report_month="2026-08"))
    result = house_coverage(c, "2026-08", "House", {1})
    assert result["expected"] is None and result["expected_current"] == 1
    c.execute("INSERT INTO schemes(scheme_id,amc_name,scheme_name,is_active_equity) VALUES(2,'House','New',1)")
    c.execute("INSERT INTO mf_holdings_monthly(scheme_id,isin,report_month,quantity) VALUES(2,'INE123A01012','2026-08',10)")
    import_inventory(c, dict(doc, report_month="2026-07"))
    new = dict(doc["funds"][0], official_key="New", official_name="New", scheme_id=2)
    import_inventory(c, dict(doc, report_month="2026-08", funds=doc["funds"] + [new]))
    result = house_coverage(c, "2026-08", "House", {1})
    assert result["coverage_state"] == "partial" and result["expected"] == 2
    assert result["expected_details"][1]["state"] == "not_in_previous_official_inventory"
