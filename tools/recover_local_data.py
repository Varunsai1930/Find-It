"""Reparse retained monthly originals into a separate, auditable database copy.

Later --inbox roots replace earlier roots for the same AMC/month. Source folders
are copied before intake runs, so neither the original database nor disclosures
are modified. The candidate is never published or activated by this tool.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from findit.cli.refresh import refresh
from findit.core.history import stock_history
from findit.ingest.intake import PARSED_DIR, UNZIPPED_DIR, load_registry, prepare, previous_month
from findit.pipeline import file_sha256
from findit.store import db


def copy_originals(roots: list[Path], destination: Path, months: list[str]) -> list[dict]:
    """Select whole AMC folders explicitly; never combine overlapping workbooks."""
    known = {amc.amc for amc in load_registry()}
    selected: dict[tuple[str, str], Path] = {}
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"missing inbox root: {root}")
        for month in months:
            folder = root / month
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                if entry.is_dir() and entry.name in known:
                    selected[(month, entry.name)] = entry
    if not selected:
        raise ValueError("no registered AMC source folders found for requested months")
    records = []
    for (month, amc), source in sorted(selected.items()):
        target = destination / month / amc
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(PARSED_DIR, UNZIPPED_DIR))
        files = []
        for original in sorted(p for p in source.rglob("*") if p.is_file()):
            relative = original.relative_to(source)
            if any(part in (PARSED_DIR, UNZIPPED_DIR) for part in relative.parts):
                continue
            digest = file_sha256(original)
            if file_sha256(target / relative) != digest:
                raise ValueError(f"copied disclosure differs from original: {original}")
            files.append({"file": str(original), "sha256": digest})
        records.append({"month": month, "amc": amc, "original_directory": str(source),
                        "files": files})
    return records


def candidate_checks(path: Path, previous: str, current: str) -> dict:
    conn = db.connect_read_only(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
        example = conn.execute(
            "SELECT isin FROM stocks WHERE name LIKE '%Coforge%' ORDER BY isin LIMIT 1"
        ).fetchone()
        # Cohort membership is security independent; this example also records
        # one real stock's history for manual evidence reconciliation.
        isin = example[0] if example else "INE002A01018"
        history = stock_history(conn, isin, previous, current)
        hdfc = stock_history(conn, isin, previous, current, amc="HDFC AMC")
        snapshots = [dict(zip(("month", "funds", "holdings"), row)) for row in conn.execute(
            "SELECT report_month,COUNT(DISTINCT scheme_id),COUNT(*) "
            "FROM mf_holdings_monthly GROUP BY report_month ORDER BY report_month"
        )]
        return {"integrity_check": integrity, "foreign_key_violations": foreign_keys,
                "snapshots": snapshots, "compared_active_funds": history["cohort_count"],
                "source_backed_active_funds": history["source_backed_count"],
                "hdfc_compared_active_funds": hdfc["cohort_count"],
                "hdfc_source_backed_active_funds": hdfc["source_backed_count"],
                "example_history": history,
                "quarantined_snapshots": conn.execute(
                    "SELECT COUNT(*) FROM scheme_month_status WHERE status='quarantined'"
                ).fetchone()[0]}
    finally:
        conn.close()


def rebuild_later_comparisons(path: Path, current: str) -> list[str]:
    """A changed baseline invalidates the following month's stored deltas."""
    conn = db.connect_read_only(path)
    try:
        months = [row[0] for row in conn.execute(
            "SELECT DISTINCT report_month FROM mf_holdings_monthly "
            "WHERE report_month>? ORDER BY report_month", (current,))]
    finally:
        conn.close()
    for month in months:
        command = [sys.executable, "-m", "findit.cli.pipeline", "--db", str(path),
                   "--prev", previous_month(month), "--curr", month]
        with path.with_name(f"rebuild-{month}.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    return months


def recover(source: Path, roots: list[Path], output: Path, previous: str, current: str) -> dict:
    if not source.is_file() or output.exists():
        raise ValueError("use an existing source database and a new output directory")
    output.mkdir(parents=True)
    report = {"source_database": str(source), "source_sha256": file_sha256(source),
              "status": "preparing", "previous": previous, "current": current}
    report_path = output / "recovery.json"
    try:
        report["originals"] = copy_originals(roots, output / "inbox", [previous, current])
        parsed = []
        intake_results = []
        for month in (previous, current):
            intake = prepare(output / "inbox" / month, month)
            intake_results.extend({"month": month, "amc": r.amc, "schemes": r.schemes,
                                   "rows": r.rows, "problems": r.problems,
                                   "warnings": r.warnings, "skipped": r.skipped}
                                  for r in intake.results)
            if not intake.ok:
                raise ValueError(f"intake refused sources for {month}: " + json.dumps(
                    {r.amc: r.problems for r in intake.results if r.problems}))
            parsed.extend(r.csv for r in intake.results if r.csv)
        report["intake"] = intake_results
        staged = output / "candidate.db"
        report["refresh"] = refresh(source, staged, previous, current, parsed, [],
                                    review_notes="Reparsed retained originals; local candidate only")
        if report["refresh"].get("status") != "staged_review_required":
            raise ValueError("monthly refresh did not produce a reviewable candidate")
        report["rebuilt_later_comparisons"] = rebuild_later_comparisons(staged, current)
        report["checks"] = candidate_checks(staged, previous, current)
        report["source_unchanged"] = file_sha256(source) == report["source_sha256"]
        checks = report["checks"]
        if not report["source_unchanged"] or checks["integrity_check"] != "ok" or checks["foreign_key_violations"]:
            raise ValueError("candidate integrity or original preservation check failed")
        report["candidate_database"] = str(staged)
        report["candidate_sha256"] = file_sha256(staged)
        report["status"] = "candidate_ready_for_review"
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--inbox", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prev", required=True)
    parser.add_argument("--curr", required=True)
    args = parser.parse_args()
    report = recover(args.source_db, args.inbox, args.output, args.prev, args.curr)
    print(json.dumps({"status": report["status"], "database": report["candidate_database"],
                      "checks": report["checks"]}, indent=2))


if __name__ == "__main__":
    main()
