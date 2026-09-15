"""
fallback_summary.py — rule-based plain-English monthly summary, with NO
LLM involved. This is the baseline the AI narration layer sits on top of
(build plan §4/§7): build and trust this first, since the app must never
depend on the LLM call succeeding. When GLM 5.3 is wired in later, it
narrates exactly these same pre-computed numbers — it never computes
them itself.
"""
import sqlite3
import pandas as pd


def build_summary(conn: sqlite3.Connection, scheme_id: int, report_month: str) -> str:
    scheme = conn.execute(
        "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?", (scheme_id,)
    ).fetchone()
    if scheme is None:
        raise ValueError(f"No scheme with scheme_id={scheme_id}")
    amc_name, scheme_name = scheme

    deltas = pd.read_sql_query(
        "SELECT d.*, s.name AS stock_name FROM mf_holding_deltas d "
        "JOIN stocks s ON s.isin = d.isin "
        "WHERE d.scheme_id = ? AND d.report_month = ?",
        conn, params=(scheme_id, report_month),
    )

    if deltas.empty:
        return (
            f"No holding-change data available for {scheme_name} in "
            f"{report_month} yet (data may not have been ingested for "
            f"this month, or this is the first month tracked)."
        )

    new = deltas[deltas["action"] == "new"].sort_values("value_change_lakhs", ascending=False)
    added = deltas[deltas["action"] == "added"].sort_values("value_change_lakhs", ascending=False)
    trimmed = deltas[deltas["action"] == "trimmed"].sort_values("value_change_lakhs")
    exited = deltas[deltas["action"] == "exited"]

    lines = [f"{scheme_name} ({amc_name}) — {report_month} portfolio changes:"]

    if not new.empty:
        top = new.iloc[0]
        lines.append(
            f"- Opened {len(new)} new position(s). The largest new entry was "
            f"{top['stock_name']}, a ₹{top['value_change_lakhs']/100:.1f} Cr position "
            f"({top['pct_nav_change']:.2f}% of NAV)."
        )
    if not added.empty:
        top = added.iloc[0]
        lines.append(
            f"- Added to {len(added)} existing position(s). The biggest addition was "
            f"{top['stock_name']}, up {top['qty_change']:,.0f} shares "
            f"(+₹{top['value_change_lakhs']/100:.1f} Cr)."
        )
    if not trimmed.empty:
        top = trimmed.iloc[0]
        lines.append(
            f"- Trimmed {len(trimmed)} position(s). The largest cut was "
            f"{top['stock_name']}, down {abs(top['qty_change']):,.0f} shares "
            f"(-₹{abs(top['value_change_lakhs'])/100:.1f} Cr)."
        )
    if not exited.empty:
        names = ", ".join(exited["stock_name"].tolist())
        lines.append(f"- Fully exited: {names}.")

    if len(lines) == 1:
        lines.append("- No material changes versus the previous month.")

    return "\n".join(lines)
