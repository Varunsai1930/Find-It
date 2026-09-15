"""
run_pipeline.py — the monthly driver script. Once you have real AMFI
files, this is the one command you actually run each month.

USAGE (after parsing each AMC's file with amfi_mf_parser.py):
  python run_pipeline.py \
      --load hdfc_march.parsed.csv sbi_march.parsed.csv \
      --load hdfc_april.parsed.csv sbi_april.parsed.csv \
      --prev 2026-03 --curr 2026-04

This loads every given CSV into tracker.db, computes deltas between
--prev and --curr, prints the cross-fund consensus table (the "who's
buying the same thing" signal), and prints a fallback summary for
every scheme that has data for --curr.

Nothing here calls an LLM. This is the fully rule-based baseline —
wire GLM 5.3 in afterwards to narrate on top of build_summary()'s
underlying numbers (build plan §7), not to replace this file.
"""
import argparse
from pathlib import Path

import db
import delta_calculator
import consensus_signals
import fallback_summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--load", nargs="+", action="append", default=[],
        metavar="CSV",
        help="One or more parsed CSVs to load (repeat --load for each batch)",
    )
    ap.add_argument("--prev", required=True, help="Previous report month, e.g. 2026-03")
    ap.add_argument("--curr", required=True, help="Current report month, e.g. 2026-04")
    ap.add_argument("--db", default="tracker.db", help="SQLite file to use")
    args = ap.parse_args()

    conn = db.get_connection(args.db)

    for batch in args.load:
        for csv_path in batch:
            n = db.load_parsed_csv(conn, Path(csv_path))
            print(f"Loaded {n} rows from {csv_path}")

    deltas = delta_calculator.compute_deltas(conn, args.prev, args.curr)
    delta_calculator.persist_deltas(conn, deltas)
    print(f"\nComputed {len(deltas)} deltas for {args.prev} -> {args.curr}")

    print(f"\n=== Cross-fund consensus, {args.curr} ===")
    consensus = consensus_signals.compute_consensus(conn, args.curr)
    if consensus.empty:
        print("(no signals yet)")
    else:
        print(consensus.to_string(index=False))

    print(f"\n=== Monthly summaries, {args.curr} ===")
    scheme_ids = conn.execute("SELECT DISTINCT scheme_id FROM mf_holding_deltas WHERE report_month = ?", (args.curr,)).fetchall()
    for (scheme_id,) in scheme_ids:
        print()
        print(fallback_summary.build_summary(conn, scheme_id, args.curr))


if __name__ == "__main__":
    main()
