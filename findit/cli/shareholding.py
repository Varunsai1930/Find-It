"""Fetch quarterly shareholding filings (see findit.ingest.shareholding).

    python3 -m findit.cli.shareholding --db tracker.db
    python3 -m findit.cli.shareholding --db tracker.db --history 0   # every filing since 2015
"""
from __future__ import annotations

import argparse
from pathlib import Path

from findit.core import consensus_signals
from findit.ingest import shareholding
from findit.store import db


def _print_stats(stats: shareholding.FetchStats) -> None:
    print("\n=== BSE quarterly shareholding fetch ===")
    print(f"MF-holdings query ISINs: {stats.holdings_query_isins}")
    print(f"Listed-equity ISINs tried: {stats.attempted}/{stats.listed_equity_isins}")
    print(f"Resolved to BSE scrips: {stats.resolved}")
    print(f"Quarterly records loaded: {stats.loaded_records}")
    print(f"Quarterly records already cached: {stats.already_cached_records}")
    print(f"No BSE scrip mapping: {len(stats.unresolved_isins)}")
    print(f"Fewer than two usable BSE filings: {len(stats.no_filings_isins)}")
    print(f"Fetch/parse failures: {len(stats.failed_isins)}")
    if stats.failed_isins:
        # Group by reason (the message without the ISIN/quarter prefix or URL),
        # so a network outage cannot hide behind ten unrelated parse errors.
        reasons: dict[str, list[str]] = {}
        for failure in stats.failed_isins:
            where, _, message = failure.partition(": ")
            reason = message.split(" for url:")[0][:90]
            reasons.setdefault(reason, []).append(where)
        print("Failures by reason:")
        for reason, where in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
            print(f"  {len(where):>4}  {reason}  (e.g. {where[0]})")
    if stats.stopped_early:
        print(f"\nINCOMPLETE: {stats.stopped_early}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=shareholding.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="tracker.db", help="SQLite file to use")
    parser.add_argument(
        "--delay", type=float, default=0.35,
        help="Minimum seconds between NSE/BSE requests (default: 0.35)",
    )
    parser.add_argument(
        "--limit", type=int,
        help="Only process the first N eligible MF-held equities (for a smoke test)",
    )
    parser.add_argument(
        "--only-missing", action="store_true",
        help="Skip ISINs that already have two or more cached quarterly filings",
    )
    parser.add_argument(
        "--history", type=int, default=2, metavar="N",
        help="Newest N filings per stock (default 2; 0 = every filing back to 2015)",
    )
    parser.add_argument(
        "--source", choices=shareholding.SOURCES, default="bse",
        help="bse = BSE index + archive, history from 2016 (default); nse = NSE index + "
             "archive, from Sep 2021 -- use when BSE's API is unavailable",
    )
    parser.add_argument(
        "--universe", choices=("held", "nse"), default="held",
        help="held = ISINs tracked schemes hold (default); nse = every NSE-listed "
             "equity, the broader research universe for the quarterly backtest",
    )
    parser.add_argument(
        "--report-month",
        help="Also print MF + FII/DII common signals for this MF report month",
    )
    args = parser.parse_args(argv)
    if args.delay < 0:
        parser.error("--delay must not be negative")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.history < 0:
        parser.error("--history must not be negative")
    if args.only_missing and args.history == 0:
        parser.error("--only-missing cannot tell which stocks have *every* filing without "
                     "asking BSE; drop it -- stored filings are skipped anyway")

    db_path = Path(args.db)
    conn = db.get_connection(str(db_path))
    cache_dir = db_path.parent / ".shareholding_cache"
    stats = shareholding.fetch_shareholding(
        conn,
        shareholding.PoliteSession(args.delay, referer="https://www.nseindia.com/" if args.source == "nse"
                      else f"{shareholding.BSE_SITE_URL}/"),
        cache_dir, limit=args.limit, only_missing=args.only_missing,
        history=args.history or None, universe=args.universe, source=args.source,
    )
    _print_stats(stats)
    if stats.stopped_early:
        raise SystemExit(1)

    if args.report_month:
        joined = consensus_signals.join_shareholding_increase(
            conn, consensus_signals.compute_consensus(conn, args.report_month),
            as_of_month=args.report_month,
        )
        common = joined[joined["is_common_with_fii_increase"]]
        print(f"\n=== MF + FII/DII common increases, {args.report_month} ===")
        print("(none)" if common.empty else common.to_string(index=False))



if __name__ == "__main__":
    main()
