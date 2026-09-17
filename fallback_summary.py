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


def _fmt_cr(lakhs) -> str:
    """Sign-aware Cr formatting: '+₹X.X Cr' / '-₹X.X Cr'. Never '+₹-X'."""
    cr = float(lakhs) / 100.0
    sign = "+" if cr >= 0 else "-"
    return f"{sign}₹{abs(cr):.1f} Cr"


def _flow_value_detail(row) -> str:
    """'(+₹X Cr flow, -₹Y Cr value change)' or value-only fallback."""
    has_flow = "flow_lakhs" in row and pd.notna(row["flow_lakhs"])
    has_val = "value_change_lakhs" in row and pd.notna(row["value_change_lakhs"])
    if has_flow and has_val:
        return f"({_fmt_cr(row['flow_lakhs'])} flow, {_fmt_cr(row['value_change_lakhs'])} value change)"
    if has_val:
        return f"({_fmt_cr(row['value_change_lakhs'])})"
    if has_flow:
        return f"({_fmt_cr(row['flow_lakhs'])} flow)"
    return ""


def _sort_col(df: pd.DataFrame) -> str:
    if "flow_lakhs" in df.columns and df["flow_lakhs"].notna().any():
        return "flow_lakhs"
    return "value_change_lakhs"


def _format_exits(exited: pd.DataFrame, prefix: str = "Fully exited") -> str:
    """Dedupe exits by ISIN, cap names at 5 + 'and N more'."""
    deduped = exited.drop_duplicates(subset=["isin"]) if "isin" in exited.columns else exited
    if "value_change_lakhs" in deduped.columns:
        deduped = deduped.sort_values("value_change_lakhs", ascending=True)
    names = deduped["stock_name"].tolist()
    if len(names) > 5:
        display = ", ".join(names[:5]) + f", and {len(names) - 5} more"
    else:
        display = ", ".join(names)
    return f"- {prefix}: {display}."


def _append_action_lines(deltas: pd.DataFrame, lines: list) -> None:
    new = deltas[deltas["action"] == "new"]
    if not new.empty:
        new = new.sort_values("value_change_lakhs", ascending=False)
    added = deltas[deltas["action"] == "added"]
    trimmed = deltas[deltas["action"] == "trimmed"]
    exited = deltas[deltas["action"] == "exited"]

    if not new.empty:
        top = new.iloc[0]
        detail = _flow_value_detail(top)
        # Keep the value-position + NAV% phrasing (BSE 0.66% case) and
        # additionally show BOTH flow and value change.
        lines.append(
            f"- Opened {len(new)} new position(s). The largest new entry was "
            f"{top['stock_name']}, a ₹{top['value_change_lakhs']/100:.1f} Cr position "
            f"({top['pct_nav_change']:.2f}% of NAV) {detail}."
        )
    if not added.empty:
        top = added.sort_values(_sort_col(added), ascending=False).iloc[0]
        flow_detail = _flow_value_detail(top)
        lines.append(
            f"- Added to {len(added)} existing position(s). The biggest addition was "
            f"{top['stock_name']}, up {top['qty_change']:,.0f} shares "
            f"{flow_detail}."
        )
    if not trimmed.empty:
        top = trimmed.sort_values(_sort_col(trimmed), ascending=True).iloc[0]
        flow_detail = _flow_value_detail(top)
        lines.append(
            f"- Trimmed {len(trimmed)} position(s). The largest cut was "
            f"{top['stock_name']}, down {abs(top['qty_change']):,.0f} shares "
            f"{flow_detail}."
        )
    if not exited.empty:
        lines.append(_format_exits(exited))


def build_summary(
    conn: sqlite3.Connection,
    scheme_id: int,
    report_month: str,
    instrument_type: str | None = "equity",
) -> str:
    scheme = conn.execute(
        "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?", (scheme_id,)
    ).fetchone()
    if scheme is None:
        raise ValueError(f"No scheme with scheme_id={scheme_id}")
    amc_name, scheme_name = scheme

    try:
        stock_cols = [r[1] for r in conn.execute("PRAGMA table_info(stocks)").fetchall()]
    except Exception:
        stock_cols = []
    has_instrument = "instrument_type" in stock_cols

    if has_instrument:
        select_extra = ", s.instrument_type AS instrument_type"
    else:
        select_extra = ""
    sql = (
        f"SELECT d.*, s.name AS stock_name{select_extra} FROM mf_holding_deltas d "
        "JOIN stocks s ON s.isin = d.isin "
        "WHERE d.scheme_id = ? AND d.report_month = ?"
    )
    params = [scheme_id, report_month]
    if instrument_type is not None and has_instrument:
        sql += " AND s.instrument_type = ?"
        params.append(instrument_type)

    deltas = pd.read_sql_query(sql, conn, params=params)

    if deltas.empty:
        return (
            f"No holding-change data available for {scheme_name} in "
            f"{report_month} yet (data may not have been ingested for "
            f"this month, or this is the first month tracked)."
        )

    lines = [f"{scheme_name} ({amc_name}) — {report_month} portfolio changes:"]

    # When no instrument filter is requested but the column exists, keep
    # equity calls separate from money-market/bond maturities so a CD
    # maturity is never narrated as an equity exit.
    if instrument_type is None and has_instrument and "instrument_type" in deltas.columns:
        equity = deltas[deltas["instrument_type"] == "equity"]
        non_equity = deltas[deltas["instrument_type"] != "equity"]
        if not equity.empty:
            _append_action_lines(equity, lines)
        else:
            lines.append("- No material equity changes versus the previous month.")
        if not non_equity.empty:
            ne_new = (non_equity["action"] == "new").sum()
            ne_added = (non_equity["action"] == "added").sum()
            ne_trimmed = (non_equity["action"] == "trimmed").sum()
            ne_exited = non_equity[non_equity["action"] == "exited"]
            lines.append(
                f"- Non-equity holdings (money-market/bond maturities, not equity calls): "
                f"{len(non_equity)} change(s) "
                f"({int(ne_new)} new, {int(ne_added)} added, "
                f"{int(ne_trimmed)} trimmed, {len(ne_exited.drop_duplicates(subset=['isin']) if 'isin' in ne_exited.columns else ne_exited)} exited)."
            )
            if not ne_exited.empty:
                lines.append(
                    _format_exits(ne_exited, prefix="Non-equity exits (maturities, not equity exits)")
                )
        if len(lines) == 1:
            lines.append("- No material changes versus the previous month.")
        return "\n".join(lines)

    _append_action_lines(deltas, lines)

    if len(lines) == 1:
        lines.append("- No material changes versus the previous month.")

    return "\n".join(lines)
