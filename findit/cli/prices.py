"""Fetch NSE month-end closing prices into security_prices_monthly.

The only command in this project that downloads prices. It writes one table
and touches nothing else: no holdings, deltas, validation or consensus.
"""
from __future__ import annotations

import argparse

import db
from findit.ingest.prices import PriceFetchError, fetch_bhavcopy, load_prices


def fetch_months(db_path: str, months: list[str], cache_dir=None) -> dict:
    results: dict = {}
    conn = db.get_connection(db_path)
    try:
        for month in months:
            try:
                prices = fetch_bhavcopy(
                    month, **({"cache_dir": cache_dir} if cache_dir else {}))
            except (PriceFetchError, ValueError) as exc:
                results[month] = {"status": "failed", "error": str(exc), "rows": 0}
                continue
            written = load_prices(conn, prices)
            results[month] = {
                "status": "ok", "rows": written,
                "trade_date": str(prices["trade_date"].iloc[0]),
            }
    finally:
        conn.close()
    return results


def coverage(db_path: str, month: str) -> dict:
    """How much of the month's tracked equity universe now has a price."""
    conn = db.get_connection(db_path)
    try:
        held = {r[0] for r in conn.execute(
            "SELECT DISTINCT h.isin FROM mf_holdings_monthly h "
            "JOIN stocks s ON s.isin = h.isin "
            "WHERE h.report_month = ? AND s.instrument_type = 'equity'", (month,))}
        priced = {r[0] for r in conn.execute(
            "SELECT isin FROM security_prices_monthly WHERE report_month = ?", (month,))}
    finally:
        conn.close()
    matched = held & priced
    return {"held_equities": len(held), "priced": len(matched),
            "unpriced": len(held - priced),
            "pct": (100.0 * len(matched) / len(held)) if held else 0.0}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="SQLite file to write prices into")
    ap.add_argument("--month", action="append", required=True, metavar="YYYY-MM",
                    help="Month to fetch the last trading day's closes for (repeatable)")
    ap.add_argument("--cache-dir", default=None, help="Where to cache downloaded zips")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = fetch_months(args.db, args.month, args.cache_dir)
    failed = 0
    for month, result in results.items():
        if result["status"] == "ok":
            cov = coverage(args.db, month)
            print(f"{month}: {result['rows']} closes as of {result['trade_date']} "
                  f"| {cov['priced']}/{cov['held_equities']} tracked equities priced "
                  f"({cov['pct']:.1f}%), {cov['unpriced']} unpriced")
        else:
            failed += 1
            print(f"{month}: FAILED -- {result['error']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
