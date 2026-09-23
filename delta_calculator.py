"""
delta_calculator.py — computes month-over-month holding changes per
(scheme, stock), and persists them to mf_holding_deltas.

Decomposes month-over-month holding changes into:
- flow_lakhs = qty_change * implied_px_curr (actual capital deployed / withdrawn)
- value_change_lakhs = market_value_curr - market_value_prev (flow + price effect)
- price_effect_lakhs = value_change_lakhs - flow_lakhs (residual)

Month-end-price convention: flow is valued at the current month-end
implied price (px_curr = market_value_curr / quantity_curr). Monthly
snapshots cannot see intra-month execution prices, so any difference
between the traded price and the month-end price lands in the
price-effect residual by construction.
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


def unmatched_schemes(conn: sqlite3.Connection, prev_month: str, curr_month: str) -> dict:
    """{"only_prev": [...], "only_curr": [...]} scheme_ids held in just one month."""
    prev = set(_fetch_month(conn, prev_month)["scheme_id"])
    curr = set(_fetch_month(conn, curr_month)["scheme_id"])
    return {"only_prev": sorted(prev - curr), "only_curr": sorted(curr - prev)}


def compute_deltas(conn: sqlite3.Connection, prev_month: str, curr_month: str) -> pd.DataFrame:
    """Returns a DataFrame of deltas for every (scheme, isin) of the schemes
    present in *both* months, including flow_lakhs (trading flow),
    price_effect_lakhs (residual) and value_change_lakhs.

    A scheme with a snapshot in only one month is left out, not compared
    against nothing: a newly launched fund would otherwise "buy" its whole
    opening portfolio, and a month whose file was not loaded would "exit"
    everything. No data is never activity. ``unmatched_schemes`` names them.

    Month-end-price convention: flow is valued at px_curr; monthly
    snapshots cannot see intra-month execution, so execution-vs-close
    differences sit in the price-effect residual.
    """
    prev = _fetch_month(conn, prev_month)
    curr = _fetch_month(conn, curr_month)
    both = set(prev["scheme_id"]) & set(curr["scheme_id"])
    prev = prev[prev["scheme_id"].isin(both)]
    curr = curr[curr["scheme_id"].isin(both)]

    if prev.empty and curr.empty:
        return pd.DataFrame(columns=[
            "scheme_id", "isin", "report_month", "prev_month",
            "qty_change", "value_change_lakhs", "flow_lakhs",
            "price_effect_lakhs",
            "pct_nav_change", "action",
        ])

    m = prev.merge(
        curr, on=["scheme_id", "isin"], how="outer",
        suffixes=("_prev", "_curr"), indicator=True,
    )

    m["quantity_prev"] = pd.to_numeric(m["quantity_prev"], errors="coerce").fillna(0.0)
    m["quantity_curr"] = pd.to_numeric(m["quantity_curr"], errors="coerce").fillna(0.0)
    m["market_value_lakhs_prev"] = pd.to_numeric(
        m["market_value_lakhs_prev"], errors="coerce"
    ).fillna(0.0)
    m["market_value_lakhs_curr"] = pd.to_numeric(
        m["market_value_lakhs_curr"], errors="coerce"
    ).fillna(0.0)
    m["pct_nav_prev"] = pd.to_numeric(m["pct_nav_prev"], errors="coerce").fillna(0.0)
    m["pct_nav_curr"] = pd.to_numeric(m["pct_nav_curr"], errors="coerce").fillna(0.0)

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
    _qty_curr = m["quantity_curr"].to_numpy(dtype=float)
    _mv_curr = m["market_value_lakhs_curr"].to_numpy(dtype=float)
    implied_px_curr = np.divide(
        _mv_curr, _qty_curr,
        out=np.zeros_like(_mv_curr, dtype=float), where=_qty_curr > 0,
    )
    flow_both = m["qty_change"] * implied_px_curr
    both_zero = (m["quantity_prev"] <= 0) & (m["quantity_curr"] <= 0)
    exited = (m["_merge"] == "left_only") | (m["quantity_curr"] <= 0)
    flow_conditions = [
        m["_merge"] == "right_only",
        both_zero,
        exited,
    ]
    flow_choices = [
        m["market_value_lakhs_curr"],
        0.0,
        -m["market_value_lakhs_prev"],
    ]
    m["flow_lakhs"] = np.select(flow_conditions, flow_choices, default=flow_both)

    # price_effect_lakhs as residual: preserves flow + price == value to the paisa.
    price_residual = m["value_change_lakhs"] - m["flow_lakhs"]
    clean_exit = (m["quantity_curr"] <= 0) & (m["market_value_lakhs_curr"] == 0)
    m["price_effect_lakhs"] = np.where(clean_exit, 0.0, price_residual)

    cols = [
        "scheme_id", "isin", "report_month", "prev_month",
        "qty_change", "value_change_lakhs", "flow_lakhs",
        "price_effect_lakhs",
        "pct_nav_change", "action",
    ]
    return m[cols]


def persist_deltas(conn: sqlite3.Connection, deltas: pd.DataFrame) -> int:
    if deltas.empty:
        return 0
    # PRAGMA-guarded ALTER fallback for legacy DBs lacking the column.
    try:
        existing = [r[1] for r in conn.execute("PRAGMA table_info(mf_holding_deltas)").fetchall()]
        if "price_effect_lakhs" not in existing:
            conn.execute("ALTER TABLE mf_holding_deltas ADD COLUMN price_effect_lakhs REAL")
    except sqlite3.DatabaseError:
        pass
    cur = conn.cursor()
    has_flow = "flow_lakhs" in deltas.columns
    has_price = "price_effect_lakhs" in deltas.columns
    rows = [
        (
            int(r.scheme_id), str(r.isin), str(r.report_month),
            str(r.prev_month) if pd.notna(r.prev_month) else None,
            float(r.qty_change), float(r.value_change_lakhs),
            float(r.flow_lakhs) if has_flow and pd.notna(r.flow_lakhs) else None,
            float(r.price_effect_lakhs) if has_price and pd.notna(r.price_effect_lakhs) else None,
            float(r.pct_nav_change), str(r.action),
        )
        for r in deltas.itertuples(index=False)
    ]
    cur.executemany(
        """INSERT OR REPLACE INTO mf_holding_deltas
           (scheme_id, isin, report_month, prev_month, qty_change,
            value_change_lakhs, flow_lakhs, price_effect_lakhs, pct_nav_change, action)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(deltas)
