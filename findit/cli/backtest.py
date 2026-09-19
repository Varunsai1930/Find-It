"""Measure whether the consensus signal predicted anything.

The project's central claim is that stocks many AMCs bought -- especially
with FII/DII agreement -- do better than the rest. Nothing measured it.
This scores each signal group's forward return against the *tracked
universe* (every equity any tracked scheme held that month), which is the
only honest baseline: it holds the selection process fixed and varies only
the signal.

Offline except for the prices, which `findit.cli.prices` fetches separately.
Read-only: it computes and prints, and writes nothing.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from statistics import mean, median

import pandas as pd

import consensus_signals

GROUPS = ("mf_and_fii", "mf_only", "mf_neutral", "mf_selling")


def _prices(conn: sqlite3.Connection, month: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT isin, close_price, trade_date FROM security_prices_monthly "
        "WHERE report_month = ?", conn, params=(month,))


def classify(row) -> str:
    """One stock's signal group for the signal month."""
    net = row.get("net_amc_count", 0)
    if net > 0:
        return "mf_and_fii" if bool(row.get("is_common_with_fii_increase", False)) else "mf_only"
    return "mf_neutral" if net == 0 else "mf_selling"


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
    return panel


def _permutation_p(values: list[float], group_values: list[float],
                   iterations: int = 5000, seed: int = 0) -> float | None:
    """How often a random subset of the same size beats this group's mean.

    Ignores cross-sectional correlation (stocks move together), so it is
    optimistic. It is a sanity check, not a significance test.
    """
    n = len(group_values)
    if n == 0 or n >= len(values):
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
    for name in GROUPS:
        values = panel.loc[panel["group"] == name, "forward_return"].tolist()
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


def run(db_path: str, signal_month: str, forward_month: str,
        iterations: int = 5000) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        prices_signal = _prices(conn, signal_month)
        prices_forward = _prices(conn, forward_month)
        if prices_signal.empty or prices_forward.empty:
            missing = [m for m, p in ((signal_month, prices_signal),
                                      (forward_month, prices_forward)) if p.empty]
            raise SystemExit(
                f"no prices for {', '.join(missing)}. Fetch them first:\n"
                f"  python3 -m findit.cli.prices --db {db_path} "
                + " ".join(f"--month {m}" for m in missing))
        consensus = consensus_signals.compute_consensus(conn, signal_month)
        consensus = consensus_signals.join_shareholding_increase(
            conn, consensus, as_of_month=signal_month)
        panel = build_panel(consensus, prices_signal, prices_forward)
        result = evaluate(panel, iterations)
        result["signal_month"] = signal_month
        result["forward_month"] = forward_month
        result["price_dates"] = (
            str(prices_signal["trade_date"].iloc[0]),
            str(prices_forward["trade_date"].iloc[0]))
        result["consensus_rows"] = int(len(consensus))
        result["unpriced"] = int(len(consensus)) - int(len(panel))
        return result
    finally:
        conn.close()


def _pct(value) -> str:
    return "     -" if value is None else f"{100.0 * value:>+6.2f}%"


def report(result: dict) -> str:
    base = result["universe"]
    lines = [
        f"=== Signal {result['signal_month']} -> forward {result['forward_month']} ===",
        f"prices: {result['price_dates'][0]} -> {result['price_dates'][1]}",
        f"tracked universe: {base['n']} priced equities "
        f"({result['unpriced']} of {result['consensus_rows']} consensus rows unpriced)",
    ]
    if base["n"]:
        lines.append(f"universe mean {_pct(base['mean'])}  median {_pct(base['median'])}")
    lines.append("")
    lines.append(f"{'group':<12}{'n':>5}{'mean':>9}{'median':>9}"
                 f"{'vs univ':>9}{'win':>7}{'p':>7}")
    for name in GROUPS:
        g = result["groups"].get(name, {"n": 0})
        if not g["n"]:
            lines.append(f"{name:<12}{0:>5}{'   (no names)':>9}")
            continue
        p = g["p_vs_random"]
        lines.append(
            f"{name:<12}{g['n']:>5}{_pct(g['mean']):>9}{_pct(g['median']):>9}"
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--signal-month", required=True, metavar="YYYY-MM")
    ap.add_argument("--forward-month", required=True, metavar="YYYY-MM")
    ap.add_argument("--iterations", type=int, default=5000,
                    help="Permutation iterations (0 disables)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(report(run(args.db, args.signal_month, args.forward_month, args.iterations)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
