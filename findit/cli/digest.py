"""Preview-only digest from fresh persisted summaries or rule fallback. No sending."""
from __future__ import annotations

import argparse
import sqlite3

from findit.narrate.service import get_summary


def _cached_summary(conn: sqlite3.Connection, scheme_id: int, month: str) -> str:
    # Freshness is checked on every read; this never calls the model or writes.
    return get_summary(conn, int(scheme_id), month)["text"]


def build_digest(conn: sqlite3.Connection, scheme_ids, month: str) -> str:
    """Build a deterministic preview digest for scheme_ids in month.

    Uses current persisted summaries or rule fallback; no LLM calls, no network,
    no sending. Output is sorted by scheme_id for determinism.
    """
    ids = sorted({int(s) for s in scheme_ids})
    header = f"Monthly digest — {month} ({len(ids)} scheme(s))"
    if not ids:
        return header + "\n"
    parts = [header]
    for sid in ids:
        parts.append("")
        parts.append(_cached_summary(conn, sid, month))
    return "\n".join(parts).rstrip() + "\n"


def parse_scheme_ids(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    out: list[int] = []
    for v in values:
        for tok in str(v).split(","):
            tok = tok.strip()
            if tok:
                out.append(int(tok))
    return sorted(set(out))


def resolve_scheme_ids(conn: sqlite3.Connection, month: str, explicit) -> list[int]:
    if explicit:
        return sorted(set(int(s) for s in explicit))
    rows = conn.execute(
        "SELECT DISTINCT scheme_id FROM mf_holding_deltas WHERE report_month = ? "
        "ORDER BY scheme_id",
        (month,),
    ).fetchall()
    if rows:
        return [int(r[0]) for r in rows]
    rows = conn.execute("SELECT scheme_id FROM schemes ORDER BY scheme_id").fetchall()
    return [int(r[0]) for r in rows]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="SQLite DB file to read (never written)")
    ap.add_argument("--month", required=True, help="Report month, e.g. 2026-04")
    ap.add_argument("--schemes", nargs="*", default=None, help="Scheme ids, space/comma separated")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Read-only open: preview must never migrate or rewrite the DB.
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        explicit = parse_scheme_ids(args.schemes)
        ids = resolve_scheme_ids(conn, args.month, explicit)
        print(build_digest(conn, ids, args.month), end="")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
