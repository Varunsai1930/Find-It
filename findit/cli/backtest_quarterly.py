"""Test the core idea on ten years of data before building on it.

Monthly AMC portfolios exist here for only a few months, which can never
say whether "stocks mutual funds are buying beat comparable stocks". Every
listed company's quarterly shareholding pattern can: one standard format,
every company, back to 2015, with mutual-fund and FPI ownership as separate
lines. This scores the change in MF ownership (and FII agreement) quarter by
quarter against the rest of the same universe.

No look-ahead, by construction:
  - decision date D = quarter end + ``--decision-lag-days`` (default 30);
  - a stock's filing is used only if it was *published* by D -- BSE's
    observed broadcast time, or the 21-day filing deadline when that was
    never recorded (reported as a count, and ``--require-observed`` drops
    them). A late filer is left out of that quarter, never back-filled;
  - entry at the close of the first trading day after D, exit at the next
    quarter's entry.

Signal per stock: dMF = MF % of shares this quarter - last quarter (and the
same for FII/FPI). Groups:
  mf_up_fii_up   dMF >= +threshold and dFII >= +threshold
  mf_up_only     dMF >= +threshold, FII not up
  mf_flat        |dMF| < threshold
  mf_down        dMF <= -threshold
  mf_top_q / mf_bottom_q   top / bottom fifth of the universe by dMF

Known bias, printed with every run: the universe is companies with filings
on record today, so firms that delisted since are missing (survivorship).
It affects every group; it is not a reason to trust small differences.

Read-only. Prices come from ``findit.cli.prices --date``; missing ones are
listed as one command to run.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from statistics import mean

import pandas as pd

from findit.cli.backtest import _close_on_or_after, _pct, track_record
from findit.core import publication

GROUPS = ("mf_up_fii_up", "mf_up_only", "mf_flat", "mf_down", "mf_top_q", "mf_bottom_q")
DEFAULT_LAG_DAYS = 30
DEFAULT_THRESHOLD_PP = 0.25
# Size control: each quarter's universe splits into this many turnover bands,
# and needs at least this many stocks with a turnover before it is applied.
SIZE_BANDS = 3
MIN_SIZED = 15


def previous_quarter_end(quarter_end: str) -> str:
    y, m = int(quarter_end[:4]), int(quarter_end[5:7])
    return {3: f"{y - 1}-12-31", 6: f"{y}-03-31", 9: f"{y}-06-30", 12: f"{y}-09-30"}[m]


def next_quarter_end(quarter_end: str) -> str:
    y, m = int(quarter_end[:4]), int(quarter_end[5:7])
    return {3: f"{y}-06-30", 6: f"{y}-09-30", 9: f"{y}-12-31", 12: f"{y + 1}-03-31"}[m]


def decision_date(quarter_end: str, lag_days: int = DEFAULT_LAG_DAYS) -> date:
    return date.fromisoformat(quarter_end) + timedelta(days=lag_days)


def load_filings(conn: sqlite3.Connection) -> pd.DataFrame:
    """Quarterly filings with the effective publication date and its basis."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shareholding_quarterly)")}
    missing = {"mf_pct", "published_at"} - cols
    if missing:
        raise SystemExit(
            f"shareholding_quarterly lacks {sorted(missing)}; fetch filings with this "
            "version first:\n  python3 fetch_shareholding.py --db <db> --history 0")
    frame = pd.read_sql_query(
        "SELECT isin, quarter_end, mf_pct, fii_pct, published_at FROM shareholding_quarterly "
        f"WHERE substr(quarter_end, 6, 5) IN ({','.join('?' for _ in publication.QUARTER_END_MONTH_DAYS)})",
        conn, params=publication.QUARTER_END_MONTH_DAYS)
    published, basis = [], []
    for quarter_end, published_at in zip(frame["quarter_end"], frame["published_at"]):
        day, how = publication.shareholding_published(quarter_end, published_at)
        published.append(day)
        basis.append(how)
    frame["published"] = published
    frame["basis"] = basis
    return frame


def signal_panel(filings: pd.DataFrame, quarter_end: str, decision: date,
                 threshold_pp: float = DEFAULT_THRESHOLD_PP,
                 require_observed: bool = False) -> tuple[pd.DataFrame, dict]:
    """Per-stock dMF/dFII and group for one quarter, using only what was public."""
    usable = filings[filings["published"] <= decision]
    if require_observed:
        usable = usable[usable["basis"] == publication.BASIS_OBSERVED]
    curr = usable[usable["quarter_end"] == quarter_end].set_index("isin")
    prev = usable[usable["quarter_end"] == previous_quarter_end(quarter_end)].set_index("isin")
    filed_this_quarter = filings[filings["quarter_end"] == quarter_end]
    audit = {
        "filed": int(len(filed_this_quarter)),
        "public_by_decision": int(len(curr)),
        "late_excluded": int(len(filed_this_quarter) - len(
            filed_this_quarter[filed_this_quarter["published"] <= decision])),
        "deadline_basis": int((curr["basis"] == publication.BASIS_DEADLINE).sum()),
    }
    both = curr.join(prev, lsuffix="", rsuffix="_prev", how="inner")
    both = both[both["mf_pct"].notna() & both["mf_pct_prev"].notna()]
    if both.empty:
        return pd.DataFrame(columns=["isin", "d_mf", "d_fii", "group"]), audit
    panel = pd.DataFrame({
        "isin": both.index,
        "d_mf": (both["mf_pct"] - both["mf_pct_prev"]).values,
        "d_fii": (both["fii_pct"] - both["fii_pct_prev"]).values,
    })

    def _group(row) -> str:
        if row["d_mf"] >= threshold_pp:
            return "mf_up_fii_up" if row["d_fii"] >= threshold_pp else "mf_up_only"
        return "mf_down" if row["d_mf"] <= -threshold_pp else "mf_flat"

    panel["group"] = panel.apply(_group, axis=1)
    return panel, audit


def score_quarter(panel: pd.DataFrame, entry: pd.DataFrame, exit_: pd.DataFrame) -> dict:
    """Per-group mean return and excess vs the universe for one quarter.

    With entry-day turnover available, each group also gets ``excess_size``:
    its mean return minus that of stocks in the same turnover band -- the
    comparison that removes a size effect (MF ownership swings are larger in
    smaller, riskier stocks, so a raw difference can be size, not signal).
    """
    entry_cols = ["isin", "close_price"] + (["traded_value"] if "traded_value" in entry else [])
    priced = panel.merge(entry[entry_cols].rename(columns={"close_price": "px_in"}), on="isin")
    priced = priced.merge(exit_[["isin", "close_price"]].rename(
        columns={"close_price": "px_out"}), on="isin")
    priced = priced[(priced["px_in"] > 0) & (priced["px_out"] > 0)].copy()
    out = {"universe": {"n": int(len(priced))}, "groups": {}, "unpriced": len(panel) - len(priced)}
    if priced.empty:
        return out
    priced["ret"] = priced["px_out"] / priced["px_in"] - 1.0
    base = float(priced["ret"].mean())
    out["universe"]["mean"] = base
    # Fifths need at least one stock each; below that the two groups stay empty.
    priced["quintile"] = (pd.qcut(priced["d_mf"].rank(method="first"), 5, labels=False)
                          if len(priced) >= 5 else -1)
    members = {"mf_top_q": priced["quintile"] == 4, "mf_bottom_q": priced["quintile"] == 0}
    if "traded_value" in priced:
        sized = priced["traded_value"] > 0
    else:
        sized = pd.Series(False, index=priced.index)
    if sized.sum() >= MIN_SIZED:
        bands = pd.qcut(priced.loc[sized, "traded_value"].rank(method="first"),
                        SIZE_BANDS, labels=False)
        priced.loc[sized, "size_band"] = bands
        priced["ret_size_adj"] = priced["ret"] - priced.groupby("size_band")["ret"].transform("mean")
    for name in GROUPS:
        mask = members.get(name, priced["group"] == name)
        values = priced.loc[mask, "ret"].tolist()
        group = ({"n": len(values), "mean": mean(values), "excess_mean": mean(values) - base}
                 if values else {"n": 0})
        if "ret_size_adj" in priced:
            adjusted = priced.loc[mask, "ret_size_adj"].dropna()
            if len(adjusted):
                group.update(n_size=int(len(adjusted)), excess_size=float(adjusted.mean()))
        out["groups"][name] = group
    return out


def size_neutral(results: list[dict]) -> list[dict]:
    """The same results with each group's excess replaced by its size-adjusted one."""
    return [{**r, "groups": {
        name: ({"n": g["n_size"], "excess_mean": g["excess_size"]} if "excess_size" in g
               else {"n": 0})
        for name, g in r["groups"].items()}} for r in results]


def run(db_path: str, quarters: list[str] | None = None, lag_days: int = DEFAULT_LAG_DAYS,
        threshold_pp: float = DEFAULT_THRESHOLD_PP, require_observed: bool = False) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        filings = load_filings(conn)
        on_file = sorted(set(filings["quarter_end"]))
        wanted = quarters or on_file
        results, missing_dates, skipped = [], set(), []
        today = date.today()
        for quarter_end in wanted:
            decision = decision_date(quarter_end, lag_days)
            nxt = decision_date(next_quarter_end(quarter_end), lag_days)
            if decision >= today:
                skipped.append(f"{quarter_end}: decision date {decision} not reached")
                continue
            panel, audit = signal_panel(filings, quarter_end, decision, threshold_pp,
                                        require_observed)
            if panel.empty:
                skipped.append(f"{quarter_end}: no stock has two consecutive public filings")
                continue
            entry_target = publication.first_tradable_date(decision)
            exit_target = publication.first_tradable_date(nxt)
            partial = exit_target > today
            entry = _close_on_or_after(conn, entry_target)
            exit_ = _close_on_or_after(conn, exit_target, latest_ok=partial)
            if entry is not None and exit_ is not None and exit_[0] <= entry[0]:
                exit_ = None
            if entry is None:
                missing_dates.add(entry_target)
            if exit_ is None:
                missing_dates.add(today - timedelta(days=1) if partial else exit_target)
            if entry is None or exit_ is None:
                continue
            scored = score_quarter(panel, entry[1], exit_[1])
            scored.update(signal_quarter=quarter_end, price_dates=(entry[0], exit_[0]),
                          partial_period=partial, audit=audit)
            results.append(scored)
        return {"results": results, "missing_dates": sorted(missing_dates),
                "skipped": skipped, "quarters_on_file": on_file}
    finally:
        conn.close()


def report(outcome: dict, db_path: str, lag_days: int, threshold_pp: float) -> str:
    lines = []
    if outcome["missing_dates"]:
        lines += [f"{len(outcome['missing_dates'])} entry/exit close(s) are not stored yet. "
                  "Fetch them, then re-run:",
                  f"  python3 -m findit.cli.prices --db {db_path} "
                  + " ".join(f"--date {d.isoformat()}" for d in outcome["missing_dates"]), ""]
    for note in outcome["skipped"]:
        lines.append(f"skipped {note}")
    results = outcome["results"]
    if not results:
        lines.append("No quarter could be scored yet.")
        return "\n".join(lines)
    lines += ["", f"decision = quarter end + {lag_days}d; groups at +/-{threshold_pp}pp of shares",
              f"{'quarter':<11}{'filed':>7}{'public':>8}{'late':>6}{'deadline':>9}"
              f"{'scored':>8}{'universe':>10}"]
    for r in results:
        a = r["audit"]
        lines.append(f"{r['signal_quarter']:<11}{a['filed']:>7}{a['public_by_decision']:>8}"
                     f"{a['late_excluded']:>6}{a['deadline_basis']:>9}"
                     f"{r['universe']['n']:>8}{_pct(r['universe'].get('mean')):>10}")
    lines += ["", track_record(results, GROUPS, period="quarter", label_key="signal_quarter")]
    adjusted = size_neutral(results)
    if any(g.get("n") for r in adjusted for g in r["groups"].values()):
        lines += ["", "SIZE-NEUTRAL: each stock measured against stocks in the same third of that",
                  "quarter's universe by entry-day rupee turnover (a size proxy)",
                  track_record(adjusted, GROUPS, period="quarter", label_key="signal_quarter")]
    else:
        lines += ["", "(no size-neutral view: entry closes carry no turnover -- re-run "
                  "findit.cli.prices for those --date values to store it)"]
    lines += ["",
              "READ THIS BEFORE BELIEVING ANY NUMBER ABOVE",
              "  Universe = companies with filings on record today: firms delisted since are",
              "  missing (survivorship bias). The universe grows from Sep 2021, where NSE's",
              "  filings (which begin then) fill stocks BSE did not serve. Size is proxied by",
              "  one day's turnover and there is no sector matching; six groups are tested,",
              "  so one |t| near 2 is expected by chance.",
              "  'deadline' counts filings whose publication date was never observed and was",
              "  assumed to be the 21-day deadline; --require-observed drops them.",
              "  Cross-sectional comparison, not a strategy: no costs, liquidity or sizing."]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--quarter", action="append", default=[], metavar="YYYY-MM-DD",
                    help="Quarter end to score (repeatable; default every quarter on file)")
    ap.add_argument("--decision-lag-days", type=int, default=DEFAULT_LAG_DAYS)
    ap.add_argument("--threshold-pp", type=float, default=DEFAULT_THRESHOLD_PP,
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
    outcome = run(args.db, sorted(args.quarter) or None, args.decision_lag_days,
                  args.threshold_pp, args.require_observed)
    print(report(outcome, args.db, args.decision_lag_days, args.threshold_pp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
