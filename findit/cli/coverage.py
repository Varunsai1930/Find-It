"""Import reviewed official inventories and export a fund-by-month coverage audit."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from findit.core.coverage import house_coverage
from findit.core.consensus_signals import fund_house_activity
from findit.store import db, queries
from findit.ingest.intake import load_registry


def import_inventory(conn: sqlite3.Connection, document: dict) -> None:
    """Replace one reviewed inventory atomically; identities must be explicit.

    A downloaded file is not an exhaustive inventory by itself. The reviewer
    supplies the official list, scope decisions, hash, and completeness claim.
    """
    amc, month = document["amc_name"], document["report_month"]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("report_month must be YYYY-MM")
    url = document["source_url"]
    if urlparse(url).scheme != "https" or not urlparse(url).hostname:
        raise ValueError("inventory needs an official HTTPS source URL")
    if not re.fullmatch(r"[a-f0-9]{64}", document["source_sha256"]):
        raise ValueError("inventory needs the original source SHA-256")
    if type(document["exhaustive"]) is not bool or not document["reviewed_at"]:
        raise ValueError("inventory needs an explicit review and exhaustive flag")
    funds = document["funds"]
    if not funds or len({f["official_key"] for f in funds}) != len(funds):
        raise ValueError("inventory needs unique official fund keys")
    mapped = [f["scheme_id"] for f in funds if f.get("scheme_id") is not None]
    if len(set(mapped)) != len(mapped):
        raise ValueError("multiple official portfolios map to the same scheme")
    values = []
    for fund in funds:
        sid = fund.get("scheme_id")
        if sid is not None:
            owner = conn.execute("SELECT amc_name FROM schemes WHERE scheme_id = ?", (sid,)).fetchone()
            if owner is None or owner[0] != amc:
                raise ValueError(f"scheme {sid} does not belong to {amc}")
        if any(type(fund[k]) is not bool for k in ("active_eligible", "equity_eligible")):
            raise ValueError("eligibility must be explicit booleans")
        if not fund["equity_eligible"] and not fund.get("exclusion_reason"):
            raise ValueError("out-of-scope portfolios need an exclusion reason")
        values.append((amc, month, fund["official_key"], fund["official_name"], sid,
                       int(fund["active_eligible"]), int(fund["equity_eligible"]),
                       fund.get("exclusion_reason")))
    with conn:
        conn.execute("INSERT OR REPLACE INTO coverage_inventories VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (amc, month, url, document["source_sha256"], document["reviewed_at"],
                      int(document["exhaustive"]), document.get("notes")))
        conn.execute("DELETE FROM expected_funds WHERE amc_name = ? AND report_month = ?",
                     (amc, month))
        conn.executemany("INSERT INTO expected_funds VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)


def audit(conn: sqlite3.Connection, months: list[str]) -> dict:
    """Registered houses, loaded portfolios and reviewed inventories; no guessed gaps."""
    houses = {a.amc for a in load_registry()}
    houses.update(str(r[0]) for r in conn.execute("SELECT DISTINCT amc_name FROM schemes"))
    if queries.has_table(conn, "coverage_inventories"):
        houses.update(str(r[0]) for r in conn.execute("SELECT DISTINCT amc_name FROM coverage_inventories"))
    statuses = queries.scheme_statuses(conn)
    results = []
    for month in months:
        activity = fund_house_activity(conn, month)
        for amc in sorted(houses):
            compared = set(activity["houses"].get(amc, {}).get("compared_ids", []))
            result = house_coverage(conn, month, amc, compared)
            snapshots = conn.execute(
                "SELECT DISTINCT s.scheme_id, s.scheme_name, s.scheme_title, s.is_active_equity "
                "FROM schemes s JOIN mf_holdings_monthly h USING(scheme_id) "
                "WHERE s.amc_name = ? AND h.report_month = ? ORDER BY s.scheme_id", (amc, month))
            result["loaded_funds"] = [
                {"scheme_id": int(sid), "sheet": name, "name": title, "active": bool(active),
                 "validation": statuses.get((sid, month), "not_validated"),
                 "compared": sid in compared} for sid, name, title, active in snapshots]
            results.append({"amc": amc, "month": month, **result})
    return {"scope": "active stock-pickers, domestic equity; registered-house audit, unknown denominators explicit",
            "market_completeness": "unknown", "houses": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, required=True, help="Separate working database")
    ap.add_argument("--import-inventory", type=Path, action="append", default=[])
    ap.add_argument("--month", action="append", default=[])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        ap.error("database does not exist")
    conn = (db.get_connection(args.db) if args.import_inventory else
            sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True))
    try:
        for path in args.import_inventory:
            import_inventory(conn, json.loads(path.read_text()))
        months = args.month or [str(r[0]) for r in conn.execute(
            "SELECT DISTINCT report_month FROM mf_holdings_monthly ORDER BY report_month")]
        text = json.dumps(audit(conn, months), indent=2) + "\n"
        if args.out:
            args.out.write_text(text)
        else:
            print(text, end="")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
