"""Test the core idea on ten years of quarterly filings (see findit.research.backtest_quarterly).

    python3 -m findit.cli.backtest_quarterly --db tracker.db
"""
from __future__ import annotations

import argparse

from findit.core import publication
from findit.research import backtest_quarterly as lib


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=lib.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--quarter", action="append", default=[], metavar="YYYY-MM-DD",
                    help="Quarter end to score (repeatable; default every quarter on file)")
    ap.add_argument("--decision-lag-days", type=int, default=lib.DEFAULT_LAG_DAYS)
    ap.add_argument("--threshold-pp", type=float, default=lib.DEFAULT_THRESHOLD_PP,
                    help="Ownership change (percentage points of shares) that counts as a move")
    ap.add_argument("--require-observed", action="store_true",
                    help="Use only filings with an observed BSE publication time")
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for q in args.quarter:
        if q[5:] not in publication.QUARTER_END_MONTH_DAYS:
            parser.error(f"--quarter must be a quarter end (YYYY-03-31 etc.), got {q}")
    if args.decision_lag_days < publication.SHAREHOLDING_FILING_DAYS:
        print(f"note: a decision lag under {publication.SHAREHOLDING_FILING_DAYS} days "
              "excludes most on-time filers")
    outcome = lib.run(args.db, sorted(args.quarter) or None, args.decision_lag_days,
                      args.threshold_pp, args.require_observed)
    print(lib.report(outcome, args.db, args.decision_lag_days, args.threshold_pp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
