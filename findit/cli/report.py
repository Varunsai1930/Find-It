"""The monthly one-page report: what active funds did, and how much to trust it.

    python3 -m findit.cli.report --db tracker.db --month 2026-08 --out report_2026-08.md

Sections, all computed by the same functions the pipeline, dashboard and
backtests use (nothing here re-derives a number):

  1. Coverage -- which schemes voted and which were left out, and why.
  2. Broadest buying and selling -- ranked by AMCs (raw breadth), with the
     discretionary count (buying beyond what inflows explain) and the
     FII/DII direction known when the month's portfolios became public.
  3. Evidence -- the ten-year quarterly test of the idea itself, raw and
     size-neutral. The report states what it found, whatever it found.
  4. Track record -- how each past month's groups did after publication.

Read-only: it writes only the --out file (or prints to stdout).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

import consensus_signals
import delta_calculator
from findit.cli import backtest, backtest_quarterly
from findit.core import publication

TOP_N = 15
# Groups whose quarterly result the evidence section states.
EVIDENCE_GROUPS = ("mf_up_fii_up", "mf_up_only", "mf_down")
EVIDENCE_LABELS = {"mf_up_fii_up": "MF ownership up, FII also up",
                   "mf_up_only": "MF ownership up, FII not",
                   "mf_down": "MF ownership down"}
# |t| across quarters above which a pooled result is called a finding.
T_FINDING = 2.0


def _crore(lakhs) -> str:
    return "-" if pd.isna(lakhs) else f"{lakhs / 100:,.0f}"


def _direction(value) -> str:
    return "no filing" if value == "no_data" or pd.isna(value) else str(value)


def _table(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return ["_(none)_"]
    lines = ["| Stock | AMCs buying / selling | Discretionary net | "
             "Existing-position flow (₹ cr) | FII | DII |",
             "|---|---|---|---|---|---|"]
    for row in frame.itertuples(index=False):
        lines.append(
            f"| {row.stock_name} | {row.amcs_buying} / {row.amcs_selling} | "
            f"{row.net_active_amc_count:+d} | {_crore(row.accumulation_flow_lakhs)} | "
            f"{_direction(row.fii_direction)} | {_direction(row.dii_direction)} |")
    return lines


def coverage(conn: sqlite3.Connection, month: str, prev: str | None) -> list[str]:
    schemes = pd.read_sql_query(
        "SELECT s.scheme_id, s.amc_name, s.is_active_equity, "
        "COALESCE(m.status, 'unvalidated') AS status FROM schemes s "
        "JOIN (SELECT DISTINCT scheme_id FROM mf_holdings_monthly WHERE report_month = ?) h "
        "USING (scheme_id) LEFT JOIN scheme_month_status m "
        "ON m.scheme_id = s.scheme_id AND m.report_month = ?", conn, params=(month, month))
    active = schemes[schemes["is_active_equity"] == 1]
    lines = [f"- **{len(active)}** active equity schemes from **{active['amc_name'].nunique()}** "
             f"AMCs vote; {len(schemes) - len(active)} index/ETF/arbitrage/FoF/debt schemes "
             "do not.",
             f"- Quarantined by the validation gate: {int((schemes['status'] == 'quarantined').sum())}."]
    if prev:
        unmatched = delta_calculator.unmatched_schemes(conn, prev, month)
        lines.append(f"- Held in only one of {prev} / {month} (not compared, so not counted as "
                     f"buying or selling): {len(unmatched['only_curr']) + len(unmatched['only_prev'])}.")
    return lines


def evidence(db_path: str) -> list[str]:
    outcome = backtest_quarterly.run(db_path)
    results = outcome["results"]
    if not results:
        return ["_No quarterly history is stored yet; run `fetch_shareholding.py --history 0` "
                "and `findit.cli.backtest_quarterly` first._"]
    raw = backtest.pooled(results, backtest_quarterly.GROUPS)
    sized = backtest.pooled(backtest_quarterly.size_neutral(results), backtest_quarterly.GROUPS)
    quarters = sum(1 for r in results if not r.get("partial_period"))
    lines = [f"Ten-year test on quarterly shareholding filings ({quarters} complete quarters, "
             "entry after publication). Average excess return per quarter against the same "
             "universe; t is across quarters.", "",
             "| Group | Raw | Size-neutral |", "|---|---|---|"]
    findings = []
    for name in EVIDENCE_GROUPS:
        cells = []
        for stats in (raw[name], sized[name]):
            t = stats.get("t_stat")
            cells.append("-" if t is None else f"{100 * stats['mean_excess']:+.2f}% (t={t:+.2f})")
        lines.append(f"| {EVIDENCE_LABELS[name]} | {cells[0]} | {cells[1]} |")
        t_sized = sized[name].get("t_stat")
        if t_sized is not None and abs(t_sized) >= T_FINDING:
            direction = "beats" if t_sized > 0 else "lags"
            findings.append(f"{EVIDENCE_LABELS[name]} {direction} comparable stocks")
    lines.append("")
    if findings:
        lines.append(f"**Size-neutral |t| ≥ {T_FINDING:g}: {'; '.join(findings)}.** "
                     "Still one test of several; confirm on more quarters before acting.")
    else:
        lines.append("**No group beats comparable stocks reliably once size is controlled.** "
                     "Read the lists above as a record of what funds did, not as a buy list.")
    return lines


def build(db_path: str, month: str) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        prev = conn.execute("SELECT MAX(prev_month) FROM mf_holding_deltas WHERE report_month = ?",
                            (month,)).fetchone()[0]
        if prev is None:
            raise SystemExit(f"no holding changes stored for {month}; run the pipeline first")
        ranked = consensus_signals.ranked_consensus(conn, month)
        cover = coverage(conn, month, prev)
    finally:
        conn.close()
    selling = consensus_signals.broadest_selling(ranked).head(TOP_N)
    public_on = publication.mf_disclosure_deadline(month)
    lines = [
        f"# Mutual fund activity report — {month}",
        "",
        f"Changes {prev} → {month}. Portfolios public by {public_on:%d %b %Y}; FII/DII "
        "directions use only filings published by then.",
        "",
        "## Coverage", *cover,
        "",
        f"## Broadest buying (top {TOP_N})",
        "Ranked by net AMCs buying. *Discretionary net* counts only AMCs that raised the "
        "stock's weight beyond what their inflows explain.", "",
        *_table(ranked.head(TOP_N)),
        "",
        f"## Broadest selling (top {TOP_N})", "",
        *_table(selling),
        "",
        "## Evidence: does this predict returns?", *evidence(db_path),
        "",
        "## Track record of this monthly signal",
        "```", backtest.db_track_record(db_path), "```",
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--month", metavar="YYYY-MM",
                    help="Report month (default: the latest month with holding changes)")
    ap.add_argument("--out", type=Path, help="Write Markdown here instead of stdout")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    month = args.month
    if month is None:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        try:
            month = conn.execute("SELECT MAX(report_month) FROM mf_holding_deltas").fetchone()[0]
        finally:
            conn.close()
        if month is None:
            raise SystemExit("no holding changes stored yet; run the pipeline first")
    text = build(args.db, month)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
