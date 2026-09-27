"""Score the monthly consensus signal after publication (see findit.research.backtest).

    python3 -m findit.cli.backtest --db tracker.db --from 2026-03 --to 2026-07
"""
from __future__ import annotations

import argparse

from findit.research import backtest as lib


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=lib.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--signal-month", action="append", default=[], metavar="YYYY-MM",
                    help="Signal month to score (repeatable)")
    ap.add_argument("--from", dest="first", metavar="YYYY-MM",
                    help="First signal month of a range (with --to)")
    ap.add_argument("--to", dest="last", metavar="YYYY-MM", help="Last signal month of a range")
    ap.add_argument("--iterations", type=int, default=2000,
                    help="Permutation iterations per month (0 disables)")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    months = list(args.signal_month)
    if args.first or args.last:
        if not (args.first and args.last):
            parser.error("--from and --to go together")
        months += lib.month_range(args.first, args.last)
    months = sorted(set(months))
    if not months:
        parser.error("give --signal-month (repeatable) or --from/--to")
    results = lib.run_many(args.db, months, args.iterations)
    for result in results:
        print(lib.report(result))
        print()
    print(lib.track_record(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
