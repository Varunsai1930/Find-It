"""The monthly command: load parsed CSVs, validate, compare, rank, summarise.

    python3 -m findit.cli.pipeline \
        --load hdfc_march.parsed.csv sbi_march.parsed.csv \
        --load hdfc_april.parsed.csv sbi_april.parsed.csv \
        --prev 2026-03 --curr 2026-04

Loads every CSV into the database, runs the validation gate, computes deltas
between --prev and --curr, and prints the consensus, the MF + FII/DII overlap,
the signal's track record and a summary for every compared scheme. The steps
themselves live in findit.pipeline; this module parses arguments and prints.
"""
import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from findit.core import consensus_signals, delta_calculator
from findit.pipeline import (
    build_overlap_view,
    file_sha256,
    overlap_summary,
    run_validation_gate,
    track_source,
)
from findit.research import backtest
from findit.store import db
from findit.summary import get_summary


def _format_overlap_row(row) -> str:
    isin = row.get("isin", "?")
    name = row.get("stock_name", "")
    mf_net = row.get("net_amc_count", 0)
    fii_dir = row.get("fii_direction", "no_data")
    dii_dir = row.get("dii_direction", "no_data")
    qend = row.get("shareholding_quarter_end")
    prev_qend = row.get("previous_shareholding_quarter_end")
    stale = bool(row.get("shareholding_stale", False))
    stale_mark = " [stale]" if stale else ""
    return (
        f"  {name} | {isin} | MF net {mf_net} | "
        f"FII {fii_dir} / DII {dii_dir} | "
        f"as-of {qend} (prev {prev_qend}){stale_mark}"
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--load", nargs="+", action="append", default=[],
        metavar="CSV",
        help="One or more parsed CSVs to load (repeat --load for each batch)",
    )
    ap.add_argument("--prev", required=True, help="Previous report month, e.g. 2026-03")
    ap.add_argument("--curr", required=True, help="Current report month, e.g. 2026-04")
    ap.add_argument("--db", default="tracker.db", help="SQLite file to use")
    args = ap.parse_args(argv)

    conn = db.get_connection(args.db)

    # Load + record which (scheme, month) each file touched.
    touched: dict = {}
    for batch in args.load:
        for csv_path in batch:
            path = Path(csv_path)
            n = db.load_parsed_csv(conn, path)
            print(f"Loaded {n} rows from {csv_path}")
            file_hash = file_sha256(path)
            df = pd.read_csv(path)
            track_source(touched, conn, df, path, file_hash)

    # Classify freshly loaded schemes: backfill only runs at connect time
    # (before these inserts), so without this every new scheme stays NULL
    # and the consensus filter (is_active_equity = 1) silently drops it.
    db.backfill_is_active_equity(conn)

    # Validation gate: the second gate after the parser's fail-loud column
    # check. A file that parses fine can still produce numbers that don't
    # add up — those months are quarantined, never silently published.
    report = run_validation_gate(conn, touched)
    ok = [o for o in report["scheme_months"] if o["status"] == "ok"]
    bad = [o for o in report["scheme_months"] if o["status"] != "ok"]
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()
    run_status = "completed_with_quarantine" if bad else "completed"
    conn.execute(
        "INSERT OR REPLACE INTO ingest_runs "
        "(run_id, started_at, status, validation_report_json) "
        "VALUES (?, ?, ?, ?)",
        (run_id, started_at, run_status, json.dumps({
            "prev": args.prev, "curr": args.curr,
            "files": sorted({f for v in touched.values() for f in v["files"]}),
            "ok_count": len(ok), "quarantined_count": len(bad),
            "scheme_months": report["scheme_months"],
            "price_warnings": report["price_warnings"],
            "corporate_action_candidates": report["corporate_action_candidates"],
        })),
    )
    conn.commit()

    print(f"\n=== Validation gate: {len(ok)} ok, {len(bad)} quarantined ===")
    if bad:
        print("QUARANTINED (excluded from consensus and summaries):")
        for o in bad:
            errs = [i.get("message", i.get("code", "?")) for i in o["issues"]
                    if i.get("severity") == "error"] or ["failed checks"]
            print(f"  - {o['amc_name']} | {o['scheme_name']} | "
                  f"{o['report_month']}: {'; '.join(errs)}")
    else:
        print("All loaded scheme-months passed.")

    deltas = delta_calculator.compute_deltas(conn, args.prev, args.curr)
    delta_calculator.persist_deltas(conn, deltas, report_month=args.curr)
    print(f"\nComputed {len(deltas)} deltas for {args.prev} -> {args.curr}")
    unmatched = delta_calculator.unmatched_schemes(conn, args.prev, args.curr)
    for side, month in (("only_curr", args.curr), ("only_prev", args.prev)):
        for sid in unmatched[side]:
            amc, scheme = conn.execute(
                "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?", (sid,)
            ).fetchone()
            print(f"  [no comparison] {amc} | {scheme}: holdings only in {month}; "
                  "not counted as buying or selling")

    print(f"\n=== Cross-fund consensus, {args.curr} (active equity only) ===")
    print("Ranked by raw breadth (net_amc_count). net_active_amc_count counts only AMCs that\n"
          "raised a stock's weight beyond what inflows explain; it is shown alongside, not\n"
          "used for ranking, until the track record below shows which one earns it.")
    consensus = consensus_signals.compute_consensus(conn, args.curr)
    if consensus.empty:
        print("(no signals yet)")
    else:
        print(consensus.to_string(index=False))

    # Phase 1: MF+FII/DII overlap on ISIN. Missing filings stay no_data
    # (never zero); quarterly filings may be stale relative to the MF month.
    print(f"\n=== MF + FII/DII overlap, {args.curr} ===")
    overlap = build_overlap_view(conn, consensus, args.curr)
    joined, common, overlap_status = (
        overlap["joined"], overlap["common"], overlap["status"],
    )
    overlap_error = overlap.get("error")
    if overlap_status == "no_consensus":
        print("(no MF signals, so no overlap to report)")
    elif overlap_status == "unavailable":
        print("(shareholding data unavailable — showing MF-only consensus above)")
        if overlap_error:
            print(f"  reason: {overlap_error}")
    elif common.empty:
        print("(no MF+FII/DII common increases this month)")
    else:
        print("(MF net buying + FII-or-DII quarterly increase; stale quarters flagged)")
        for _, row in common.iterrows():
            print(_format_overlap_row(row))
    # The run report is the audit trail for this month. If it cannot be
    # written, say so loudly rather than leaving a silently incomplete record.
    try:
        current_report = json.loads(conn.execute(
            "SELECT validation_report_json FROM ingest_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0])
        current_report["mf_fii_overlap"] = overlap_summary(
            joined, common, overlap_status, overlap_error)
        conn.execute(
            "UPDATE ingest_runs SET validation_report_json = ? WHERE run_id = ?",
            (json.dumps(current_report), run_id),
        )
        conn.commit()
    except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
        print(
            f"  [warn] could not record the MF+FII overlap in ingest_runs "
            f"{run_id}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    try:
        passive = conn.execute(
            "SELECT DISTINCT s.amc_name, s.scheme_name FROM schemes s "
            "JOIN mf_holding_deltas d ON d.scheme_id = s.scheme_id "
            "WHERE d.report_month = ? AND s.is_active_equity = 0 "
            "ORDER BY 1, 2",
            (args.curr,),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        # The consensus filter already excluded these; not being able to
        # list them makes the exclusion unauditable, so it must be visible.
        passive = []
        print(f"  [warn] could not list excluded passive schemes: {exc}",
              file=sys.stderr)
    if passive:
        print(f"\nExcluded {len(passive)} passive/hedged/FoF/debt scheme(s) from consensus "
              f"(by full scheme name where the sheet has one; still shown in own summaries):")
        for amc, scheme in passive:
            print(f"  - {amc} | {scheme}")

    print("\n=== Signal track record (entry after publication; see findit.cli.backtest) ===")
    try:
        print(backtest.db_track_record(args.db))
    except (sqlite3.DatabaseError, ValueError) as exc:
        print(f"  [warn] track record unavailable: {type(exc).__name__}: {exc}",
              file=sys.stderr)

    print(f"\n=== Monthly summaries, {args.curr} ===")
    scheme_ids = conn.execute("SELECT DISTINCT scheme_id FROM mf_holding_deltas WHERE report_month = ?", (args.curr,)).fetchall()
    for (scheme_id,) in scheme_ids:
        print()
        # get_summary also withholds a delta whose *previous* month is
        # quarantined -- the same check the dashboard and digest apply.
        print(get_summary(conn, scheme_id, args.curr)["text"])

    print("\n=== Corporate-action candidates (unconfirmed; no flow adjustments) ===")
    if not report["corporate_action_candidates"]:
        print("(none passed both peer-agreement and inverse-price checks)")
    for candidate in report["corporate_action_candidates"]:
        print(
            f"  {candidate['isin']} | {candidate['effective_month']} | "
            f"quantity {candidate['qty_ratio']:.4f}x | "
            f"price {candidate['price_ratio']:.4f}x | "
            f"target {candidate['target_qty_ratio']:g}x | confirmed=0"
        )



if __name__ == "__main__":
    main()
