"""Offline delta rebuild on a DB copy only. Never writes the original DB."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import db
import delta_calculator


def rebuild_copy(src_db: str | Path, dst_copy: str | Path, prev: str, curr: str) -> int:
    """Copy src_db to dst_copy, then compute+persists deltas on the copy only.

    Returns the number of delta rows written to the copy. Never opens the
    original for writing; the original is only read via shutil.copyfile.
    """
    src = Path(src_db)
    dst = Path(dst_copy)
    if not src.exists():
        raise FileNotFoundError(f"source DB not found: {src}")
    try:
        same = src.resolve() == dst.resolve()
    except OSError:
        same = str(src) == str(dst)
    if same:
        raise ValueError("refusing to rebuild: --db and --db-copy are the same file")
    if dst.parent != Path(".") and str(dst.parent) not in ("", "."):
        dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    conn = db.get_connection(str(dst))
    try:
        deltas = delta_calculator.compute_deltas(conn, prev, curr)
        return int(delta_calculator.persist_deltas(conn, deltas))
    finally:
        conn.close()


def rebuild_copy_in_place(copy_db: str | Path, prev: str, curr: str) -> int:
    """Recompute deltas directly on copy_db (which must already be a copy)."""
    conn = db.get_connection(str(copy_db))
    try:
        deltas = delta_calculator.compute_deltas(conn, prev, curr)
        return int(delta_calculator.persist_deltas(conn, deltas))
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="Source (original) DB; copied, never written")
    ap.add_argument("--db-copy", required=True, help="Destination copy DB to rebuild")
    ap.add_argument("--prev", required=True, help="Previous report month, e.g. 2026-03")
    ap.add_argument("--curr", required=True, help="Current report month, e.g. 2026-04")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.db:
        n = rebuild_copy(args.db, args.db_copy, args.prev, args.curr)
        print(f"Rebuilt {n} deltas for {args.prev} -> {args.curr} in {args.db_copy}")
    else:
        n = rebuild_copy_in_place(args.db_copy, args.prev, args.curr)
        print(f"Rebuilt {n} deltas for {args.prev} -> {args.curr} in {args.db_copy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
