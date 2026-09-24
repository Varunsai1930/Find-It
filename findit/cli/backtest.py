"""Measure whether the consensus signal predicted anything, month by month.

The project's central claim is that stocks many AMCs bought -- especially
with FII/DII agreement -- do better than the rest. This scores each signal
group's forward return against the *tracked universe* (every equity any
tracked scheme held that month), which holds the selection process fixed
and varies only the signal.

No look-ahead: a month's portfolios are public only on the SEBI disclosure
deadline (month end + 10 days), so a position is entered at the close of
the first trading day after it and held until the next month's entry.
Using the month-end close would trade on portfolios nobody had seen.

Two partitions of the same stocks are scored:
  raw     -- net AMCs whose quantity went up (mf_and_fii / mf_only / ...)
  active  -- net AMCs that *chose* to raise the stock's weight beyond what
             inflows alone explain (findit.core.active_weight)

Across several signal months it reports a track record: each month's
excess return per group, and the mean excess with a t-statistic computed
across months. Months, not stocks, are the independent observations --
stocks in one month move together.

Offline except for the prices, which `findit.cli.prices` fetches separately.
Read-only: it computes and prints, and writes nothing.
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
from datetime import date, timedelta
from statistics import mean, median, stdev

import pandas as pd

import consensus_signals
from findit.core import publication

GROUPS = ("mf_and_fii", "mf_only", "mf_neutral", "mf_selling")
ACTIVE_GROUPS = ("active_and_fii", "active_only", "active_neutral", "active_selling")
# The first trading day on/after a target date is at most this far away.
MAX_TRADING_GAP_DAYS = 10


def classify(row) -> str:
    """One stock's signal group for the signal month."""
    net = row.get("net_amc_count", 0)
    if net > 0:
        return "mf_and_fii" if bool(row.get("is_common_with_fii_increase", False)) else "mf_only"
    return "mf_neutral" if net == 0 else "mf_selling"


def classify_active(row) -> str:
    """One stock's group by *discretionary* breadth (net_active_amc_count)."""
    net = row.get("net_active_amc_count", 0)
    if pd.isna(net):
        net = 0
    if net > 0:
        return ("active_and_fii" if bool(row.get("is_common_with_fii_increase", False))
                and row.get("net_amc_count", 0) > 0 else "active_only")
    return "active_neutral" if net == 0 else "active_selling"


def build_panel(consensus: pd.DataFrame, prices_signal: pd.DataFrame,
                prices_forward: pd.DataFrame) -> pd.DataFrame:
    """Join signal to forward return. Pure; unpriced names are dropped, not zeroed."""
    if consensus is None or consensus.empty:
        return pd.DataFrame(columns=["isin", "group", "forward_return"])
    panel = consensus.merge(
        prices_signal.rename(columns={"close_price": "px_signal"})[["isin", "px_signal"]],
        on="isin", how="inner")
    panel = panel.merge(
        prices_forward.rename(columns={"close_price": "px_forward"})[["isin", "px_forward"]],
        on="isin", how="inner")
    panel = panel[(panel["px_signal"] > 0) & (panel["px_forward"] > 0)]
    if panel.empty:
        return pd.DataFrame(columns=["isin", "group", "forward_return"])
    panel["forward_return"] = panel["px_forward"] / panel["px_signal"] - 1.0
    panel["group"] = panel.apply(classify, axis=1)
    if "net_active_amc_count" in panel.columns:
        panel["active_group"] = panel.apply(classify_active, axis=1)
    return panel


def _permutation_p(values: list[float], group_values: list[float],
                   iterations: int = 5000, seed: int = 0) -> float | None:
    """How often a random subset of the same size beats this group's mean.

    Ignores cross-sectional correlation (stocks move together), so it is
    optimistic. It is a sanity check, not a significance test.
    """
    n = len(group_values)
    if n == 0 or n >= len(values) or iterations <= 0:
        return None
    observed = mean(group_values)
    rng = random.Random(seed)
    hits = 0
    for _ in range(iterations):
        if mean(rng.sample(values, n)) >= observed:
            hits += 1
    return hits / iterations


def evaluate(panel: pd.DataFrame, iterations: int = 5000) -> dict:
    """Per-group forward-return stats against the whole tracked universe."""
    if panel.empty:
        return {"universe": {"n": 0}, "groups": {}}
    universe = panel["forward_return"].tolist()
    base = {"n": len(universe), "mean": mean(universe), "median": median(universe)}
    out = {}
    partitions = [("group", GROUPS)]
    if "active_group" in panel.columns:
        partitions.append(("active_group", ACTIVE_GROUPS))
    for column, name in ((c, n) for c, names in partitions for n in names):
        values = panel.loc[panel[column] == name, "forward_return"].tolist()
        if not values:
            out[name] = {"n": 0}
            continue
        out[name] = {
            "n": len(values), "mean": mean(values), "median": median(values),
            "excess_mean": mean(values) - base["mean"],
            "excess_median": median(values) - base["median"],
            "win_rate": sum(1 for v in values if v > 0) / len(values),
            "beat_universe_rate": sum(1 for v in values if v > base["median"]) / len(values),
            "p_vs_random": _permutation_p(universe, values, iterations),
        }
    if "is_new_to_universe" in panel.columns:
        flags = panel["is_new_to_universe"].fillna(False).astype(bool)
        new_listings = panel.loc[flags, "forward_return"]
    else:
        new_listings = panel.loc[[], "forward_return"]
    return {"universe": base, "groups": out,
            "new_listings": {"n": int(len(new_listings)),
                             "mean": float(new_listings.mean()) if len(new_listings) else None}}


def _close_on_or_after(conn: sqlite3.Connection, target: date,
                       latest_ok: bool = False) -> tuple[str, pd.DataFrame] | None:
    """(trade_date, closes) for the first stored trading day on/after target.

    ``latest_ok`` falls back to the latest stored day after ``target``'s
    window -- used only for a holding period that has not ended yet.
    """
    row = conn.execute(
        "SELECT MIN(trade_date) FROM security_prices_daily "
        "WHERE trade_date >= ? AND trade_date <= ?",
        (target.isoformat(), (target + timedelta(days=MAX_TRADING_GAP_DAYS)).isoformat()),
    ).fetchone()
    day = row[0] if row else None
    if day is None and latest_ok:
        day = conn.execute("SELECT MAX(trade_date) FROM security_prices_daily").fetchone()[0]
    if day is None:
        return None
    has_value = "traded_value" in consensus_signals._table_columns(conn, "security_prices_daily")
    return str(day), pd.read_sql_query(
        "SELECT isin, close_price, trade_date"
        + (", traded_value" if has_value else "")
        + " FROM security_prices_daily WHERE trade_date = ?", conn, params=(day,))


def _next_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{y + (m == 12)}-{1 if m == 12 else m + 1:02d}"


def holding_period(signal_month: str, today: date | None = None) -> dict:
    """Entry/exit target dates for one signal month (no prices involved)."""
    today = today or date.today()
    entry = publication.mf_signal_entry_date(signal_month)
    exit_ = publication.mf_signal_entry_date(_next_month(signal_month))
    return {"entry_target": entry, "exit_target": exit_,
            "exit_in_future": exit_ > today, "entry_in_future": entry > today}


def run(db_path: str, signal_month: str, iterations: int = 5000) -> dict:
    """Score one signal month, entering at publication and exiting at the next."""
    period = holding_period(signal_month)
    if period["entry_in_future"]:
        raise SystemExit(
            f"{signal_month}'s portfolios are not public until "
            f"{period['entry_target'] - timedelta(days=1)}; nothing to score yet.")
    conn = sqlite3.connect(db_path)
    try:
        has_daily = bool(consensus_signals._table_columns(conn, "security_prices_daily"))
        entry = _close_on_or_after(conn, period["entry_target"]) if has_daily else None
        exit_ = (_close_on_or_after(conn, period["exit_target"],
                                    latest_ok=period["exit_in_future"])
                 if has_daily else None)
        if exit_ is not None and entry is not None and exit_[0] <= entry[0]:
            exit_ = None
        if entry is None or exit_ is None:
            needed = [period["entry_target"]] if entry is None else []
            if exit_ is None:
                needed.append(date.today() if period["exit_in_future"]
                              else period["exit_target"])
            raise SystemExit(
                f"no stored close for {signal_month}'s holding period. Fetch it first:\n"
                f"  python3 -m findit.cli.prices --db {db_path} "
                + " ".join(f"--date {d.isoformat()}" for d in needed))
        consensus = consensus_signals.compute_consensus(conn, signal_month)
        consensus = consensus_signals.join_shareholding_increase(
            conn, consensus, as_of_month=signal_month)
        panel = build_panel(consensus, entry[1], exit_[1])
        result = evaluate(panel, iterations)
        result["signal_month"] = signal_month
        result["forward_month"] = _next_month(signal_month)
        result["price_dates"] = (entry[0], exit_[0])
        result["partial_period"] = bool(period["exit_in_future"])
        result["consensus_rows"] = int(len(consensus))
        result["unpriced"] = int(len(consensus)) - int(len(panel))
        return result
    finally:
        conn.close()


def pooled(results: list[dict], names: tuple = GROUPS + ACTIVE_GROUPS) -> dict:
    """Mean per-period excess per group, with a t-statistic across periods.

    Only complete holding periods are pooled; a partial current period is
    shown in the track record but never averaged with finished ones.
    """
    out = {}
    complete = [r for r in results if not r.get("partial_period")]
    for name in names:
        excess = [r["groups"][name]["excess_mean"] for r in complete
                  if r["groups"].get(name, {}).get("n")]
        stats = {"months": len(excess)}
        if excess:
            stats["mean_excess"] = mean(excess)
            stats["months_beating_universe"] = sum(1 for e in excess if e > 0)
        if len(excess) >= 2:
            sd = stdev(excess)
            stats["t_stat"] = (stats["mean_excess"] / (sd / math.sqrt(len(excess)))
                               if sd > 0 else None)
        out[name] = stats
    return out


def track_record(results: list[dict], names: tuple = GROUPS + ACTIVE_GROUPS,
                 period: str = "month", label_key: str = "signal_month") -> str:
    """Per-period excess returns per group, then the pooled line."""
    width = max(15, max(len(n) for n in names) + 1)
    lines = [f"=== Track record: excess return vs tracked universe, per signal {period} ===",
             f"{'signal':<11}{'held':<24}" + "".join(f"{n:>{width}}" for n in names)]
    for r in results:
        held = f"{r['price_dates'][0]}->{r['price_dates'][1]}" + (
            "*" if r.get("partial_period") else "")
        cells = []
        for n in names:
            g = r["groups"].get(n, {"n": 0})
            cells.append(f"{_pct(g['excess_mean'])} ({g['n']:>3})" if g.get("n") else "-")
        lines.append(f"{r[label_key]:<11}{held:<24}"
                     + "".join(f"{c:>{width}}" for c in cells))
    stats = pooled(results, names)
    mean_cells, t_cells = [], []
    for n in names:
        st = stats[n]
        mean_cells.append(f"{_pct(st.get('mean_excess'))} ({st['months']:>3})"
                          if st["months"] else "-")
        t = st.get("t_stat")
        t_cells.append(f"t={t:+.2f} {st['months_beating_universe']}/{st['months']}"
                       if t is not None else "-")
    lines.append(f"{'pooled':<11}{f'complete {period}s only':<24}"
                 + "".join(f"{c:>{width}}" for c in mean_cells))
    lines.append(f"{'':<11}{f't across {period}s, wins':<24}"
                 + "".join(f"{c:>{width}}" for c in t_cells))
    complete = len([r for r in results if not r.get("partial_period")])
    lines += [
        "",
        f"(n) = stocks in the group that {period}; pooled (n) = {period}s. * = holding period",
        "not finished yet: marked to the latest stored close and left out of the pooled line.",
        f"{complete} complete signal {period}(s). A t-statistic needs many {period}s before "
        "it means anything;",
        "with fewer than ~24, treat every number here as a description, not evidence.",
    ]
    return "\n".join(lines)


def run_many(db_path: str, signal_months: list[str], iterations: int = 2000) -> list[dict]:
    return [run(db_path, m, iterations=iterations) for m in signal_months]


def month_range(first: str, last: str) -> list[str]:
    months, current = [], first
    while current <= last:
        months.append(current)
        current = _next_month(current)
    return months


def _pct(value) -> str:
    return "     -" if value is None else f"{100.0 * value:>+6.2f}%"


def report(result: dict) -> str:
    base = result["universe"]
    partial = " (holding period not finished: latest stored close)" if result.get(
        "partial_period") else ""
    lines = [
        f"=== Signal {result['signal_month']} -> forward {result['forward_month']} ===",
        f"held: {result['price_dates'][0]} -> {result['price_dates'][1]}{partial}",
        f"tracked universe: {base['n']} priced equities "
        f"({result['unpriced']} of {result['consensus_rows']} consensus rows unpriced)",
    ]
    if base["n"]:
        lines.append(f"universe mean {_pct(base['mean'])}  median {_pct(base['median'])}")
    lines.append("")
    lines.append(f"{'group':<15}{'n':>5}{'mean':>9}{'median':>9}"
                 f"{'vs univ':>9}{'win':>7}{'p':>7}")
    names = GROUPS + (ACTIVE_GROUPS if any(n in result["groups"] for n in ACTIVE_GROUPS) else ())
    for name in names:
        if name == ACTIVE_GROUPS[0]:
            lines.append("-- by discretionary (active-weight) breadth --")
        g = result["groups"].get(name, {"n": 0})
        if not g["n"]:
            lines.append(f"{name:<15}{0:>5}{'   (no names)':>9}")
            continue
        p = g["p_vs_random"]
        lines.append(
            f"{name:<15}{g['n']:>5}{_pct(g['mean']):>9}{_pct(g['median']):>9}"
            f"{_pct(g['excess_mean']):>9}{100 * g['win_rate']:>6.0f}%"
            f"{('  -' if p is None else f'{p:>6.3f}')}")
    new = result.get("new_listings") or {}
    if new.get("n"):
        lines.append(f"\nof which new listings: {new['n']} names, mean {_pct(new['mean'])}")
    lines += [
        "",
        "READ THIS BEFORE BELIEVING ANY NUMBER ABOVE",
        "  One signal month is one event, not a sample. These figures describe "
        "what happened once;",
        "  they do not establish that the signal works. p is a permutation "
        "check against random",
        "  subsets of the same universe and ignores that stocks move together, "
        "so it is optimistic.",
        "  The comparison is cross-sectional within the tracked universe, so it "
        "is not a strategy",
        "  return: no costs, no liquidity limits, no position sizing.",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
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
        months += month_range(args.first, args.last)
    months = sorted(set(months))
    if not months:
        parser.error("give --signal-month (repeatable) or --from/--to")
    results = run_many(args.db, months, args.iterations)
    for result in results:
        print(report(result))
        print()
    print(track_record(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
