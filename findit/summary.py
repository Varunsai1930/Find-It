"""Rule-based monthly fund summaries, with the data-safety checks kept.

This replaces the constrained-GLM narration layer. That layer's only real
authority was reordering pre-written sentences and swapping a handful of
synonyms, which is not worth ~1,000 lines, a provider adapter and a cache.

What *was* worth keeping is the eligibility reasoning it carried: a summary
must not be published for a month whose data is quarantined, unvalidated, or
numerically broken, and "no data" must never be rendered as "no activity".
That logic lives here now, over `fallback_summary.build_summary`.

Read-only: no writes, no network, no model.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Any

import fallback_summary

RULES_VERSION = "rules-v1"
VALID_ACTIONS = frozenset({"new", "added", "trimmed", "exited", "unchanged"})
_REQUIRED_NUMERIC = ("qty_change", "value_change_lakhs", "pct_nav_change")
_OPTIONAL_NUMERIC = ("flow_lakhs", "price_effect_lakhs")


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

    quarantined = any(row.get("status") == "quarantined" for row in statuses)
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
    elif current_status not in {"ok", "validated"}:
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
        text = fallback_summary.build_summary(conn, scheme_id, report_month)
    else:
        text = (f"No holding-change data available for {scheme['scheme_name']} in "
                f"{report_month} yet (data may not have been ingested for "
                "this month, or this is the first month tracked).")

    return {"text": text, "generated_by": "rules", "model_version": RULES_VERSION,
            "cached": False, "eligible": state["reason"] is None,
            "reason": state["reason"]}
