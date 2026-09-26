"""Record full scheme names from disclosure workbooks and re-derive the
passive/arbitrage flag from them.

Sheets are often named by code ("SAOF", "NIF30DEX"), which the sheet-name
heuristic cannot read, so index and arbitrage funds were counted as active
managers. The workbook's own title rows carry the real name. This reads
only those rows -- no holdings are loaded or changed -- and prints every
scheme whose ``is_active_equity`` flag changes.

    python3 -m findit.cli.scheme_titles --db tracker.db --amc "SBI AMC" \\
        real_data/sbi_aug2026.xlsx
    python3 -m findit.cli.scheme_titles --db tracker.db --amc "ICICI Prudential AMC" \\
        real_data/icici_aug2026/*.xlsx --dry-run

A scheme's title is stored once and does not change month to month, but a
*classifier rule* fix (e.g. teaching it to recognise a debt "Savings Fund")
does not retroactively touch flags already written to an existing DB, and
the disclosure workbook a title first came from is not always still on
disk for every scheme. ``--from-db`` re-runs the current classifier against
every scheme's already-stored ``scheme_title`` with no workbook needed:

    python3 -m findit.cli.scheme_titles --db tracker.db --from-db --dry-run
"""
from __future__ import annotations

import argparse
from pathlib import Path

import amfi_mf_parser
import db


def apply_titles(conn, amc: str, files: list[Path], dry_run: bool = False) -> dict:
    changes, unknown, untitled, recorded = [], [], [], 0
    for path in files:
        for sheet, title in amfi_mf_parser.read_scheme_titles(path).items():
            scheme_id = db.resolve_scheme_id(conn, amc, sheet, create=False)
            if scheme_id is None:
                unknown.append(f"{path.name}: {sheet}")
                continue
            if not title:
                untitled.append(f"{path.name}: {sheet}")
                continue
            if dry_run:
                row = conn.execute(
                    "SELECT scheme_name, is_active_equity FROM schemes WHERE scheme_id = ?",
                    (scheme_id,)).fetchone()
                new_flag = db.classify_scheme_title(title)
                if row[1] is not None and int(row[1]) != new_flag:
                    changes.append((amc, row[0], title, int(row[1]), new_flag))
            else:
                change = db.record_scheme_title(conn, scheme_id, title)
                if change:
                    changes.append(change)
            recorded += 1
    return {"recorded": recorded, "changes": changes, "unknown": unknown,
            "untitled": untitled}


def reclassify_from_stored_titles(conn, dry_run: bool = False) -> dict:
    """Re-run classify_scheme_title against every already-stored scheme_title.

    For applying a classifier *rule* fix to an existing DB when the source
    workbook is not at hand -- no file I/O, just the DB's own scheme_title
    column. Same ``changes`` shape as apply_titles, so callers/printing can
    treat the two the same way.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(schemes)").fetchall()]
    if "scheme_title" not in cols:
        return {"recorded": 0, "changes": []}
    rows = conn.execute(
        "SELECT scheme_id, amc_name, scheme_name, scheme_title, is_active_equity "
        "FROM schemes WHERE scheme_title IS NOT NULL"
    ).fetchall()
    changes = []
    for scheme_id, amc, scheme_name, scheme_title, old_flag in rows:
        new_flag = db.classify_scheme_title(scheme_title)
        old = None if old_flag is None else int(old_flag)
        if old == new_flag:
            continue
        # An unset (NULL) flag is reported too, so a dry run lists every write.
        changes.append((amc, scheme_name, scheme_title, old, new_flag))
        if not dry_run:
            conn.execute("UPDATE schemes SET is_active_equity = ? WHERE scheme_id = ?",
                         (new_flag, scheme_id))
    if not dry_run:
        conn.commit()
    return {"recorded": len(rows), "changes": changes}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--amc", help="AMC name exactly as stored, e.g. 'SBI AMC' "
                                  "(not used with --from-db)")
    ap.add_argument("files", nargs="*", type=Path,
                     help="Disclosure .xlsx workbook(s) (omit with --from-db)")
    ap.add_argument("--dry-run", action="store_true", help="Report changes, write nothing")
    ap.add_argument("--from-db", action="store_true",
                     help="Re-derive is_active_equity from each scheme's already-stored "
                          "scheme_title instead of re-reading workbooks")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.from_db:
        if args.amc or args.files:
            parser.error("--from-db does not take --amc or file arguments")
    elif not args.amc or not args.files:
        parser.error("--amc and at least one file are required (or pass --from-db)")

    conn = db.get_connection(args.db)
    try:
        if args.from_db:
            result = reclassify_from_stored_titles(conn, args.dry_run)
        else:
            result = apply_titles(conn, args.amc, args.files, args.dry_run)
    finally:
        conn.close()
    verb = "would change" if args.dry_run else "changed"
    print(f"{result['recorded']} scheme title(s) read; {len(result['changes'])} flag(s) {verb}")
    for amc, scheme, title, old, new in result["changes"]:
        label = "active" if new else "excluded (passive/hedged/FoF/debt)"
        print(f"  {old}->{new} {amc} | {scheme} | {title} -> {label}")
    if result.get("unknown"):
        print(f"{len(result['unknown'])} sheet(s) with no scheme in the DB (skipped):")
        for item in result["unknown"][:20]:
            print(f"  - {item}")
    if result.get("untitled"):
        print(f"{len(result['untitled'])} sheet(s) with no title row (kept as is):")
        for item in result["untitled"]:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
