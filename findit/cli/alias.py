"""Inspect and repair scheme identity: list, alias, and merge scheme records.

A scheme's stored name is the AMC's Excel sheet name. When an AMC renames a
sheet, ingestion creates a second scheme_id and the deltas show a phantom full
exit plus a phantom new fund. Punctuation and spacing drift is absorbed
automatically by ``db.normalize_scheme_name``; a real rename ("SCRF" ->
"SBI Credit Risk Fund") is a judgement call and is recorded here by a human.

Offline: reads and writes only the given SQLite file, never the network.
"""
from __future__ import annotations

import argparse
import sqlite3

import db

# Every table that carries a scheme_id, with the columns that make a row
# unique within one scheme. A merge must move all of them or none.
_SCHEME_TABLES = (
    ("mf_holdings_monthly", ("isin", "report_month")),
    ("mf_holding_deltas", ("isin", "report_month")),
    ("scheme_month_status", ("report_month",)),
)


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def list_schemes(conn: sqlite3.Connection, amc: str | None = None) -> list[dict]:
    """Every scheme with its recorded aliases and month coverage."""
    db.backfill_scheme_identity(conn)
    sql = ("SELECT scheme_id, amc_name, scheme_name, scheme_key FROM schemes")
    params: list = []
    if amc:
        sql += " WHERE amc_name = ?"
        params.append(amc)
    out = []
    for scheme_id, amc_name, scheme_name, key in conn.execute(sql + " ORDER BY amc_name, scheme_name", params):
        aliases = [r[0] for r in conn.execute(
            "SELECT alias FROM scheme_aliases WHERE scheme_id = ? AND alias != ? "
            "ORDER BY alias", (scheme_id, scheme_name))]
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT report_month FROM mf_holdings_monthly "
            "WHERE scheme_id = ? ORDER BY report_month", (scheme_id,))]
        out.append({"scheme_id": scheme_id, "amc_name": amc_name,
                    "scheme_name": scheme_name, "scheme_key": key,
                    "aliases": aliases, "months": months})
    return out


def merge_schemes(conn: sqlite3.Connection, source_id: int, target_id: int) -> dict:
    """Move every row from source_id onto target_id, then drop the source.

    Refuses when the two schemes belong to different AMCs, or when both hold
    a row for the same key (a genuine data conflict, not a rename): silently
    dropping one side's holdings would be exactly the invisible wrong number
    this project refuses to produce.
    """
    db.backfill_scheme_identity(conn)
    if source_id == target_id:
        raise ValueError("--merge-from and --merge-into are the same scheme_id")
    rows = {r[0]: r for r in conn.execute(
        "SELECT scheme_id, amc_name, scheme_name FROM schemes "
        "WHERE scheme_id IN (?, ?)", (source_id, target_id))}
    for sid in (source_id, target_id):
        if sid not in rows:
            raise ValueError(f"No scheme with scheme_id={sid}")
    if rows[source_id][1] != rows[target_id][1]:
        raise ValueError(
            f"refusing to merge across AMCs: {rows[source_id][1]!r} -> "
            f"{rows[target_id][1]!r}")

    tables = _existing_tables(conn)
    conflicts: list[str] = []
    for table, key_cols in _SCHEME_TABLES:
        if table not in tables:
            continue
        cols = ", ".join(key_cols)
        joins = " AND ".join(f"a.{c} = b.{c}" for c in key_cols)
        found = conn.execute(
            f"SELECT {', '.join('a.' + c for c in key_cols)} FROM {table} a "
            f"JOIN {table} b ON {joins} "
            f"WHERE a.scheme_id = ? AND b.scheme_id = ? LIMIT 5",
            (source_id, target_id)).fetchall()
        for row in found:
            conflicts.append(f"{table}({cols})=" + "/".join(str(v) for v in row))
    if conflicts:
        raise ValueError(
            "refusing to merge: both schemes hold rows for the same key -- "
            + "; ".join(conflicts)
            + ". Inspect these before merging; no rows were changed.")

    moved: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, _key_cols in _SCHEME_TABLES:
            if table not in tables:
                continue
            cur = conn.execute(
                f"UPDATE {table} SET scheme_id = ? WHERE scheme_id = ?",
                (target_id, source_id))
            moved[table] = int(cur.rowcount or 0)
        # The source's names survive as aliases, so a later file using the
        # old sheet name still resolves to the surviving identity.
        source_name = rows[source_id][2]
        aliases = [r[0] for r in conn.execute(
            "SELECT alias FROM scheme_aliases WHERE scheme_id = ?", (source_id,))]
        conn.execute("DELETE FROM scheme_aliases WHERE scheme_id = ?", (source_id,))
        for alias in set(aliases) | {source_name}:
            conn.execute(
                "INSERT OR IGNORE INTO scheme_aliases (scheme_id, alias, "
                "alias_normalized, created_at, source) "
                "VALUES (?, ?, ?, datetime('now'), 'merge')",
                (target_id, alias, db.normalize_scheme_name(alias)))
        conn.execute("DELETE FROM schemes WHERE scheme_id = ?", (source_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"source_id": source_id, "target_id": target_id,
            "target_name": rows[target_id][2], "moved": moved}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="SQLite file to inspect or repair")
    ap.add_argument("--amc", default=None, help="Restrict --list to one AMC")
    ap.add_argument("--list", action="store_true", help="List schemes and aliases")
    ap.add_argument("--add-alias", default=None, metavar="RAW_SHEET_NAME",
                    help="Record a raw sheet name as an alias of --scheme-id")
    ap.add_argument("--scheme-id", type=int, default=None,
                    help="Target scheme_id for --add-alias")
    ap.add_argument("--merge-from", type=int, default=None,
                    help="scheme_id to merge away (its rows move, then it is deleted)")
    ap.add_argument("--merge-into", type=int, default=None,
                    help="scheme_id that survives the merge")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = sqlite3.connect(args.db)
    try:
        if args.add_alias is not None:
            if args.scheme_id is None:
                raise SystemExit("--add-alias requires --scheme-id")
            db.record_scheme_alias(conn, args.scheme_id, args.add_alias)
            print(f"Recorded alias {args.add_alias!r} -> scheme_id {args.scheme_id}")
        if args.merge_from is not None or args.merge_into is not None:
            if args.merge_from is None or args.merge_into is None:
                raise SystemExit("--merge-from requires --merge-into")
            result = merge_schemes(conn, args.merge_from, args.merge_into)
            detail = ", ".join(f"{t}={n}" for t, n in sorted(result["moved"].items()) if n)
            print(f"Merged scheme_id {result['source_id']} into "
                  f"{result['target_id']} ({result['target_name']}): "
                  f"{detail or 'no rows to move'}")
        if args.list or (args.add_alias is None and args.merge_from is None):
            for row in list_schemes(conn, args.amc):
                months = ",".join(row["months"]) or "-"
                print(f"{row['scheme_id']:>5}  {row['amc_name']} | "
                      f"{row['scheme_name']}  [{months}]")
                for alias in row["aliases"]:
                    print(f"         alias: {alias}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
