"""
delta_calculator.py — computes month-over-month holding changes per
(scheme, stock), and persists them to mf_holding_deltas.

Decomposes month-over-month holding changes into:
- flow_lakhs = qty_change * implied_px_curr (actual capital deployed / withdrawn)
- value_change_lakhs = market_value_curr - market_value_prev (flow + price effect)
"""
import sqlite3
import pandas as pd
import numpy as np


def _fetch_month(conn: sqlite3.Connection, report_month: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT scheme_id, isin, quantity, market_value_lakhs, pct_nav "
        "FROM mf_holdings_monthly WHERE report_month = ?",
        conn, params=(report_month,),
    )


def compute_deltas(conn: sqlite3.Connection, prev_month: str, curr_month: str) -> pd.DataFrame:
    """Returns a DataFrame of deltas for every (scheme, isin) present in
    either month, including flow_lakhs (trading flow) and value_change_lakhs."""
    prev = _fetch_month(conn, prev_month)
    curr = _fetch_month(conn, curr_month)

    if prev.empty and curr.empty:
        return pd.DataFrame(columns=[
            "scheme_id", "isin", "report_month", "prev_month",
            "qty_change", "value_change_lakhs", "flow_lakhs",
            "pct_nav_change", "action",
        ])

    m = prev.merge(
        curr, on=["scheme_id", "isin"], how="outer",
        suffixes=("_prev", "_curr"), indicator=True,
    )

    m["quantity_prev"] = m["quantity_prev"].fillna(0.0)
    m["quantity_curr"] = m["quantity_curr"].fillna(0.0)
    m["market_value_lakhs_prev"] = m["market_value_lakhs_prev"].fillna(0.0)
    m["market_value_lakhs_curr"] = m["market_value_lakhs_curr"].fillna(0.0)
    m["pct_nav_prev"] = m["pct_nav_prev"].fillna(0.0)
    m["pct_nav_curr"] = m["pct_nav_curr"].fillna(0.0)

    m["qty_change"] = m["quantity_curr"] - m["quantity_prev"]
    m["value_change_lakhs"] = m["market_value_lakhs_curr"] - m["market_value_lakhs_prev"]
    m["pct_nav_change"] = m["pct_nav_curr"] - m["pct_nav_prev"]
    m["report_month"] = curr_month
    m["prev_month"] = prev_month

    # Action classification
    conditions = [
        m["_merge"] == "right_only",
        m["_merge"] == "left_only",
        m["qty_change"] > 0,
        m["qty_change"] < 0,
    ]
    choices = ["new", "exited", "added", "trimmed"]
    m["action"] = np.select(conditions, choices, default="unchanged")

    # flow_lakhs: qty_change * implied_px_curr (or full entry / exit values)
    implied_px_curr = np.where(
        m["quantity_curr"] > 0,
        m["market_value_lakhs_curr"] / m["quantity_curr"],
        0.0,
    )
    flow_both = m["qty_change"] * implied_px_curr
    flow_conditions = [
        m["_merge"] == "right_only",
        m["_merge"] == "left_only",
    ]
    flow_choices = [
        m["market_value_lakhs_curr"],
        -m["market_value_lakhs_prev"],
    ]
    m["flow_lakhs"] = np.select(flow_conditions, flow_choices, default=flow_both)

    cols = [
        "scheme_id", "isin", "report_month", "prev_month",
        "qty_change", "value_change_lakhs", "flow_lakhs",
        "pct_nav_change", "action",
    ]
    return m[cols]


def persist_deltas(conn: sqlite3.Connection, deltas: pd.DataFrame) -> int:
    if deltas.empty:
        return 0
    cur = conn.cursor()
    has_flow = "flow_lakhs" in deltas.columns
    rows = [
        (
            int(r.scheme_id), str(r.isin), str(r.report_month),
            str(r.prev_month) if pd.notna(r.prev_month) else None,
            float(r.qty_change), float(r.value_change_lakhs),
            float(r.flow_lakhs) if has_flow and pd.notna(r.flow_lakhs) else None,
            float(r.pct_nav_change), str(r.action),
        )
        for r in deltas.itertuples(index=False)
    ]
    cur.executemany(
        """INSERT OR REPLACE INTO mf_holding_deltas
           (scheme_id, isin, report_month, prev_month, qty_change,
            value_change_lakhs, flow_lakhs, pct_nav_change, action)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(deltas)
