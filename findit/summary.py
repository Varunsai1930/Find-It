"""Rule-based monthly fund summaries, with the data-safety checks kept.

This replaces the constrained-GLM narration layer. That layer's only real
authority was reordering pre-written sentences and swapping a handful of
synonyms, which is not worth ~1,000 lines, a provider adapter and a cache.

What *was* worth keeping is the eligibility reasoning it carried: a summary
must not be published for a month whose data is quarantined, unvalidated, or
numerically broken, and "no data" must never be rendered as "no activity".
`get_summary` applies it; `build_summary` only renders the numbers.

Read-only: no writes, no network, no model.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

import pandas as pd

from findit.core.data_quality import bad_domestic_quantity_pairs
from findit.store import queries

RULES_VERSION = "rules-v1"
VALID_ACTIONS = frozenset({"new", "added", "trimmed", "exited", "unchanged"})
_REQUIRED_NUMERIC = ("qty_change",)
_OPTIONAL_NUMERIC = ("value_change_lakhs", "pct_nav_change", "flow_lakhs", "price_effect_lakhs")


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


def _has_material_price_effect(row) -> bool:
    """True when a price_effect_lakhs leg is present and worth narrating.

    Legacy rows predate the column (or store NULL): never material then.
    The threshold (|price| >= 0.05 Cr, i.e. 5 lakhs) keeps dust from
    rendering as a spurious '(+₹0.0 Cr price effect)' leg.
    """
    if "price_effect_lakhs" not in row:
        return False
    try:
        val = row["price_effect_lakhs"]
    except (KeyError, IndexError, TypeError):
        return False
    if not pd.notna(val):
        return False
    try:
        cr = float(val) / 100.0
    except (TypeError, ValueError):
        return False
    return abs(cr) >= 0.05


def _flow_price_value_detail(row) -> str:
    """'(+₹X Cr flow, -₹Y Cr price effect, -₹Z Cr value change)' when the
    price leg is present and material; otherwise same as _flow_value_detail.

    Used for added/trimmed tops where flow and value can diverge (sign
    flips). New/exited rows keep the flow+value form since flow == value
    there by convention (price effect is 0).
    """
    has_flow = "flow_lakhs" in row and pd.notna(row["flow_lakhs"])
    has_val = "value_change_lakhs" in row and pd.notna(row["value_change_lakhs"])
    if has_flow and has_val and _has_material_price_effect(row):
        return (
            f"({_fmt_cr(row['flow_lakhs'])} flow, "
            f"{_fmt_cr(row['price_effect_lakhs'])} price effect, "
            f"{_fmt_cr(row['value_change_lakhs'])} value change)"
        )
    return _flow_value_detail(row)


def _headline_lakhs(row) -> float | None:
    """Flow is the headline rupee figure; value change only as fallback."""
    if "flow_lakhs" in row and pd.notna(row["flow_lakhs"]):
        try:
            return float(row["flow_lakhs"])
        except (TypeError, ValueError):
            pass
    return float(row["value_change_lakhs"]) if pd.notna(row["value_change_lakhs"]) else None


def _sort_col(df: pd.DataFrame) -> str:
    if "flow_lakhs" in df.columns and df["flow_lakhs"].notna().any():
        return "flow_lakhs"
    return "value_change_lakhs"


def _format_exits(exited: pd.DataFrame, prefix: str = "Fully exited") -> str:
    """Dedupe exits by ISIN, cap names at 5 + 'and N more'."""
    deduped = exited.drop_duplicates(subset=["isin"]) if "isin" in exited.columns else exited
    # Rank by flow (value-change fallback when flow is all-NaN), same as
    # added/trimmed: the largest exit is the most negative flow.
    sort_col = _sort_col(deduped) if not deduped.empty else "value_change_lakhs"
    if sort_col in deduped.columns:
        deduped = deduped.sort_values(sort_col, ascending=True)
    names = deduped["stock_name"].tolist()
    if len(names) > 5:
        display = ", ".join(names[:5]) + f", and {len(names) - 5} more"
    else:
        display = ", ".join(names)
    return f"- {prefix}: {display}."


def _append_action_lines(deltas: pd.DataFrame, lines: list) -> None:
    new = deltas[deltas["action"] == "new"]
    if not new.empty:
        sort_col = _sort_col(new)
        if sort_col in new.columns:
            new = new.sort_values(sort_col, ascending=False)
    added = deltas[deltas["action"] == "added"]
    trimmed = deltas[deltas["action"] == "trimmed"]
    exited = deltas[deltas["action"] == "exited"]

    if not new.empty:
        top = new.iloc[0]
        detail = _flow_value_detail(top)
        # Keep the value-position + NAV% phrasing (BSE 0.66% case); the
        # headline rupee figure is flow (== value for new positions).
        value = _headline_lakhs(top)
        nav = top["pct_nav_change"]
        value_text = (f"a ₹{value / 100:.1f} Cr position" if value is not None
                      else f"{top['qty_change']:,.0f} shares; value unavailable")
        nav_text = f"{nav:.2f}% of NAV" if pd.notna(nav) else "NAV weight unavailable"
        entry = ("The largest new entry was" if new[_sort_col(new)].notna().all()
                 else "One new entry was")
        lines.append(
            f"- Opened {len(new)} new position(s). {entry} "
            f"{top['stock_name']}, {value_text} ({nav_text}) {detail}."
        )
    if not added.empty:
        top = added.sort_values(_sort_col(added), ascending=False).iloc[0]
        flow_detail = _flow_price_value_detail(top)
        addition = ("The biggest addition was" if added[_sort_col(added)].notna().all()
                    else "One addition was")
        lines.append(
            f"- Added to {len(added)} existing position(s). {addition} "
            f"{top['stock_name']}, up {top['qty_change']:,.0f} shares "
            f"{flow_detail}."
        )
    if not trimmed.empty:
        top = trimmed.sort_values(_sort_col(trimmed), ascending=True).iloc[0]
        flow_detail = _flow_price_value_detail(top)
        cut = ("The largest cut was" if trimmed[_sort_col(trimmed)].notna().all()
               else "One cut was")
        lines.append(
            f"- Trimmed {len(trimmed)} position(s). {cut} "
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
    """One scheme-month's changes as plain English. Renders only.

    It does not decide whether the month may be shown: `get_summary` does,
    withholding quarantined or broken data. Call that, not this, to publish.
    """
    scheme = conn.execute(
        "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?", (scheme_id,)
    ).fetchone()
    if scheme is None:
        raise ValueError(f"No scheme with scheme_id={scheme_id}")
    amc_name, scheme_name = scheme

    has_instrument = "instrument_type" in queries.table_columns(conn, "stocks")

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
    # With no instrument filter, equity and non-equity changes are told apart.
    split = instrument_type is None and has_instrument
    return render_summary(scheme_name, amc_name, report_month, deltas, split)


def render_summary(scheme_name: str, amc_name: str, report_month: str,
                   deltas: pd.DataFrame, split_non_equity: bool = False) -> str:
    """A scheme-month's delta rows as plain English. Pure.

    ``deltas`` carries mf_holding_deltas columns plus stock_name (and
    instrument_type when ``split_non_equity``).
    """
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
    if split_non_equity:
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



def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def assess(conn: sqlite3.Connection, scheme_id: int, report_month: str) -> dict:
    """Why this scheme-month may or may not be summarised.

    Returns {"reason": str | None, "scheme": row, "quarantined": bool}.
    `reason is None` means the numbers are safe to describe.
    """
    scheme_rows = _rows(conn, "SELECT * FROM schemes WHERE scheme_id = ?", (scheme_id,))
    if not scheme_rows:
        raise ValueError(f"No scheme with scheme_id={scheme_id}")
    scheme = scheme_rows[0]
    tables = _tables(conn)

    deltas = []
    if "mf_holding_deltas" in tables:
        deltas = _rows(conn, "SELECT * FROM mf_holding_deltas "
                       "WHERE scheme_id = ? AND report_month = ?",
                       (scheme_id, report_month))
    stocks: dict = {}
    if deltas and "stocks" in tables:
        isins = sorted({row["isin"] for row in deltas})
        placeholders = ",".join("?" for _ in isins)
        stocks = {row["isin"]: row for row in _rows(
            conn, f"SELECT * FROM stocks WHERE isin IN ({placeholders})", tuple(isins))}

    # The previous month matters too: a delta is a comparison, so bad data on
    # either side makes the comparison unsafe to describe.
    months = {report_month}
    months.update(row["prev_month"] for row in deltas if row.get("prev_month"))
    statuses: list[dict] = []
    if "scheme_month_status" in tables:
        for month in sorted(months):
            statuses.extend(_rows(conn, "SELECT * FROM scheme_month_status "
                                  "WHERE scheme_id = ? AND report_month = ?",
                                  (scheme_id, month)))

    quarantined = any(row.get("status") == queries.STATUS_QUARANTINED for row in statuses)
    unsafe = bad_domestic_quantity_pairs(conn, scheme_id)
    quarantined = quarantined or any((scheme_id, month) in unsafe for month in months)
    current_status = next((row.get("status") for row in statuses
                           if row["report_month"] == report_month), None)
    equity = [row for row in deltas
              if stocks.get(row["isin"], {}).get("instrument_type") == "equity"]
    unknown_type = any(stocks.get(row["isin"], {}).get("instrument_type") is None
                       for row in deltas)
    invalid = any(
        any(not _finite(row.get(column)) for column in _REQUIRED_NUMERIC)
        or any(row.get(column) is not None and not _finite(row[column])
               for column in _OPTIONAL_NUMERIC)
        or row.get("action") not in VALID_ACTIONS
        for row in equity)

    if quarantined:
        reason = "quarantined"
    elif invalid:
        reason = "nonfinite_or_invalid_data"
    elif current_status != queries.STATUS_OK:
        reason = "unvalidated"
    elif unknown_type:
        reason = "unknown_instrument_type"
    elif not equity:
        reason = "no_data"
    else:
        reason = None
    return {"reason": reason, "scheme": scheme, "quarantined": quarantined,
            "invalid": invalid, "tables": tables}


def get_summary(conn: sqlite3.Connection, scheme_id: int, report_month: str) -> dict:
    """One scheme-month's summary text plus why it is what it is."""
    state = assess(conn, scheme_id, report_month)
    scheme, tables = state["scheme"], state["tables"]

    if state["quarantined"] or state["invalid"]:
        explanation = (
            "the current or referenced previous month is quarantined"
            if state["quarantined"]
            else "holding-change data contains invalid numeric values or actions")
        text = (f"{scheme['scheme_name']} ({scheme['amc_name']}) — {report_month} "
                f"data withheld: {explanation}. Not zero activity — "
                "the numbers did not pass checks.")
    elif "mf_holding_deltas" in tables and "stocks" in tables:
        text = build_summary(conn, scheme_id, report_month)
    else:
        text = (f"No holding-change data available for {scheme['scheme_name']} in "
                f"{report_month} yet (data may not have been ingested for "
                "this month, or this is the first month tracked).")

    return {"text": text, "generated_by": "rules", "model_version": RULES_VERSION,
            "cached": False, "eligible": state["reason"] is None,
            "reason": state["reason"]}
