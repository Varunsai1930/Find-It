"""Re-run the validation gate over scheme-months already in a database.

The gate's rules change as real filings teach us what they should be. When
they do, the statuses stored in `scheme_month_status` are whatever the rules
said on the day the data was ingested -- so a database can carry quarantines
the current code would never produce, while narration eligibility and the
consensus filter still read those stale rows and withhold good data.

This re-runs `run_pipeline.run_validation_gate` against the holdings already
stored, with no CSVs and no network. Source hashes and drop provenance are
carried over from the existing status rows, so re-validating never invents
provenance the ingest did not record.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import db
import run_pipeline


def _scheme_months(conn: sqlite3.Connection, months=None, scheme_ids=None):
    pairs: set[tuple[int, str]] = set()
    for table in ("mf_holdings_monthly", "scheme_month_status"):
        try:
            rows = conn.execute(
                f"SELECT DISTINCT scheme_id, report_month FROM {table}").fetchall()
        except sqlite3.Error:
            continue
        pairs.update((int(a), str(b)) for a, b in rows if a is not None and b is not None)
    if months:
        wanted = {str(m) for m in months}
        pairs = {p for p in pairs if p[1] in wanted}
    if scheme_ids:
        wanted_ids = {int(s) for s in scheme_ids}
        pairs = {p for p in pairs if p[0] in wanted_ids}
    return sorted(pairs)


def rebuild_touched(conn: sqlite3.Connection, months=None, scheme_ids=None) -> dict:
    """Rebuild the gate's `touched` map from stored rows, not from CSVs."""
    touched: dict = {}
    for scheme_id, month in _scheme_months(conn, months, scheme_ids):
        entry = {"files": [], "hashes": [],
                 "dropped_non_isin_count": None, "dropped_non_isin_pct_nav": None}
        row = conn.execute(
            "SELECT source_data_hash, validation_report_json FROM scheme_month_status "
            "WHERE scheme_id = ? AND report_month = ?", (scheme_id, month)).fetchone()
        if row:
            if row[0]:
                entry["hashes"] = [h for h in str(row[0]).split("+") if h]
            try:
                report = json.loads(row[1]) if row[1] else {}
            except (TypeError, ValueError):
                report = {}
            for column in ("dropped_non_isin_count", "dropped_non_isin_pct_nav"):
                entry[column] = report.get(column)
        touched[(scheme_id, month)] = entry
    return touched


def _statuses(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT scheme_id, report_month, status FROM scheme_month_status").fetchall()
    except sqlite3.Error:
        return {}
    return {(int(a), str(b)): str(c) for a, b, c in rows}


def revalidate(db_path: str | Path, months=None, scheme_ids=None) -> dict:
    """Re-run the gate in place. Returns before/after counts and the changes."""
    conn = db.get_connection(str(db_path))
    try:
        before = _statuses(conn)
        touched = rebuild_touched(conn, months, scheme_ids)
        if not touched:
            return {"scheme_months": 0, "changes": [], "before": {}, "after": {}}
        report = run_pipeline.run_validation_gate(conn, touched)
        after = _statuses(conn)
        changes = []
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old != new:
                info = conn.execute(
                    "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?",
                    (key[0],)).fetchone()
                changes.append({
                    "scheme_id": key[0], "report_month": key[1],
                    "amc_name": info[0] if info else "?",
                    "scheme_name": info[1] if info else "?",
                    "from": old, "to": new,
                })
        counts: dict = {}
        for status in after.values():
            counts[status] = counts.get(status, 0) + 1
        return {"scheme_months": len(touched), "changes": changes,
                "before": _counts(before), "after": counts,
                "price_warnings": len(report.get("price_warnings", []))}
    finally:
        conn.close()


def _counts(statuses: dict) -> dict:
    out: dict = {}
    for status in statuses.values():
        out[status] = out.get(status, 0) + 1
    return out


def revalidate_dry_run(db_path: str | Path, months=None, scheme_ids=None) -> dict:
    """Same run, on a throwaway copy. The given database is never opened for writing."""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"database not found: {src}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "revalidate.copy.db"
        shutil.copyfile(src, copy)
        return revalidate(copy, months, scheme_ids)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="SQLite file to re-validate")
    ap.add_argument("--month", action="append", default=None, metavar="YYYY-MM",
                    help="Restrict to one month (repeatable)")
    ap.add_argument("--schemes", nargs="+", type=int, default=None,
                    help="Restrict to these scheme_ids")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report the changes from a temporary copy; never write --db")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = revalidate_dry_run if args.dry_run else revalidate
    result = runner(args.db, args.month, args.schemes)
    where = "(dry run, nothing written)" if args.dry_run else f"in {args.db}"
    print(f"Re-validated {result['scheme_months']} scheme-month(s) {where}")
    print(f"  before: {result['before'] or '(none)'}")
    print(f"  after:  {result['after'] or '(none)'}")
    if not result["changes"]:
        print("  no status changes -- stored statuses already match the current gate")
        return 0
    print(f"  {len(result['changes'])} status change(s):")
    for change in result["changes"]:
        print(f"    {change['amc_name']} | {change['scheme_name']} | "
              f"{change['report_month']}: {change['from']} -> {change['to']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
