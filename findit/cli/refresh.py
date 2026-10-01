"""Stage a monthly refresh in a new database, retaining the last good release."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from findit.cli.coverage import audit, import_inventory
from findit.core.history import month_range
from findit.pipeline import file_sha256
from findit.store import db
from findit.store.releases import content_id, snapshot


def refresh(source: Path, staged: Path, previous: str, current: str,
            files: list[Path], inventories: list[Path], releases: Path | None = None,
            review_notes: str = "", parent: str | None = None) -> dict:
    """Reuse the pipeline in an isolated process; failed stages never promote."""
    if len(month_range(previous, current)) != 2:
        raise ValueError("refresh requires adjacent previous/current months")
    if not source.is_file() or staged.exists() or source.resolve() == staged.resolve():
        raise ValueError("use an existing source and a new, separate staged database")
    if releases and not review_notes.strip():
        raise ValueError("promotion requires recorded source/anomaly review notes")
    missing = [str(p) for p in files + inventories if not p.is_file()]
    if missing:
        raise ValueError("input files missing: " + ", ".join(missing))
    staged.parent.mkdir(parents=True, exist_ok=True)
    started, clock = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    report = {"started_at": started, "previous_month": previous, "current_month": current,
              "source_sha256": file_sha256(source), "status": "running",
              "inputs": [{"file": str(p), "sha256": file_sha256(p)} for p in files + inventories],
              "review_notes": review_notes, "cash_cost_inr": None, "founder_minutes": None,
              "support_minutes": None, "publication_delay_seconds": None}
    metrics_path = staged.with_suffix(".refresh.json")
    try:
        src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(staged)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        command = [sys.executable, "-m", "findit.cli.pipeline", "--db", str(staged),
                   "--prev", previous, "--curr", current]
        if files:
            command += ["--load", *map(str, files)]
        with staged.with_suffix(".pipeline.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        conn = db.get_connection(staged)
        try:
            for inventory in inventories:
                import_inventory(conn, json.loads(inventory.read_text()))
            report["coverage"] = audit(conn, [current])
            report["release_id"] = content_id(conn)
            latest = conn.execute("SELECT validation_report_json FROM ingest_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            report["validation"] = json.loads(latest[0]) if latest else None
        finally:
            conn.close()
        report["status"] = "staged_review_required"
        if releases:
            report["release"] = snapshot(staged, releases, f"Monthly research {current}", parent, review_notes)
            report["status"] = "released"
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        if staged.is_file():
            # Also prevent manual promotion of a stage that failed after the
            # pipeline (for example, an invalid official inventory import).
            with sqlite3.connect(staged) as failed:
                if failed.execute("SELECT 1 FROM sqlite_master WHERE name='ingest_runs'").fetchone():
                    row = failed.execute("SELECT run_id,validation_report_json FROM ingest_runs ORDER BY started_at DESC LIMIT 1").fetchone()
                    if row:
                        details = json.loads(row[1] or "{}")
                        details["refresh_error"] = report["error"]
                        failed.execute("UPDATE ingest_runs SET status='failed',validation_report_json=? WHERE run_id=?",
                                       (json.dumps(details), row[0]))
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["processing_seconds"] = time.perf_counter() - clock
        report["database_bytes"] = staged.stat().st_size if staged.exists() else None
        metrics_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-db", type=Path, required=True)
    ap.add_argument("--staged-db", type=Path, required=True)
    ap.add_argument("--prev", required=True)
    ap.add_argument("--curr", required=True)
    ap.add_argument("--load", type=Path, nargs="+", default=[])
    ap.add_argument("--inventory", type=Path, action="append", default=[])
    ap.add_argument("--publish-to", type=Path, help="Optional local release directory after source review")
    ap.add_argument("--review-notes", default="")
    ap.add_argument("--parent", help="Retained release ID for a corrected disclosure revision")
    args = ap.parse_args(argv)
    try:
        result = refresh(args.source_db, args.staged_db, args.prev, args.curr, args.load,
                         args.inventory, args.publish_to, args.review_notes, args.parent)
    except (ValueError, subprocess.CalledProcessError) as exc:
        ap.exit(1, f"Refresh failed: {exc}. Last good release retained.\n")
    print(json.dumps({k: result[k] for k in ("status", "release_id", "processing_seconds", "database_bytes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
