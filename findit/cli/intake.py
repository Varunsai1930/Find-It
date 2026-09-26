"""Check and parse a month of downloaded AMC disclosures, ready to load.

Download each AMC's monthly portfolio from its disclosure page into one
folder per fund house, named as the database names it (``--init`` creates
the folders for AMCs already in the database):

    python3 -m findit.cli.intake --month 2026-08 --init
    python3 -m findit.cli.intake --month 2026-08

Accepted AMCs get a parsed CSV under ``<inbox>/<month>/_parsed/`` and the
report prints the ``run_pipeline.py`` command that loads them. Nothing is
downloaded and the database is never written: loading is that separate step.
Exit status is 1 when anything was refused, unknown or misplaced.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from findit.ingest import intake


def _tracked_amcs(db_path: Path) -> list[str]:
    """AMC names already in the database (read-only), or [] without one."""
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [str(r[0]) for r in conn.execute("SELECT DISTINCT amc_name FROM schemes")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def init_folders(month_dir: Path, db_path: Path) -> list[Path]:
    """Create month_dir and a folder for each tracked AMC that AMFI lists."""
    listed = {a.amc for a in intake.load_registry()}
    month_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for amc in sorted(set(_tracked_amcs(db_path)) & listed):
        folder = month_dir / amc
        if not folder.exists():
            folder.mkdir()
            created.append(folder)
    return created


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, help="Report month, YYYY-MM")
    ap.add_argument("--inbox", type=Path, default=Path("real_data/inbox"),
                    help="Folder holding one sub-folder per month (default real_data/inbox)")
    ap.add_argument("--db", type=Path, default=Path("tracker.db"),
                    help="Database the load command targets; --init reads it, read-only")
    ap.add_argument("--init", action="store_true",
                    help="Create the month's folders for AMCs already in the database, then stop")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    month_dir = args.inbox / args.month
    if args.init:
        created = init_folders(month_dir, args.db)
        print(f"{month_dir}: created {len(created)} folder(s)")
        for folder in created:
            print(f"  {folder}")
        print("Add a folder per extra fund house, named exactly as listed in "
              "findit/ingest/amfi_amcs.json ('amc').")
        return 0
    try:
        report = intake.prepare(month_dir, args.month)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(intake.render(report, str(args.db)))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
