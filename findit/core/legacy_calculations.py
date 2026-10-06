"""Frozen readiness-2026-10-01 comparison arithmetic for safe pinned replay.

Copied from the verified aca7da8 baseline. New/default calculations must never
use this module. Callers must guard consumed legacy inputs before replay.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd

from findit.store import queries

QTY_TOLERANCE = 0.02
PRICE_TOLERANCE = 0.15
_HOLDING_COLUMNS = ("scheme_id", "isin", "quantity", "market_value_lakhs")
SPLIT_RATIOS = (1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 10.0, 0.5, 0.2, 0.1)

def _check(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    missing = set(_HOLDING_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} holdings are missing columns: {sorted(missing)}")
    out = frame.loc[:, list(_HOLDING_COLUMNS)].copy()
    out["quantity"] = pd.to_numeric(out["quantity"], errors="coerce")
    out["market_value_lakhs"] = pd.to_numeric(out["market_value_lakhs"], errors="coerce")
    return out[(out["quantity"] > 0) & (out["market_value_lakhs"] > 0)]

def infer_split_ratios(prev: pd.DataFrame, curr: pd.DataFrame,
                       confirmed: dict | None = None) -> dict:
    """{isin: share-count ratio} for splits/bonuses between the two months.

    A ratio is inferred only when every scheme holding the stock in both
    months shows the same quantity multiple k (within QTY_TOLERANCE) *and*
    the per-share price moved by about 1/k. Funds trading independently do
    not all land on the same exact multiple; a genuine price crash does not
    multiply share counts. ``confirmed`` (e.g. from corporate_actions)
    always wins.
    """
    prev, curr = _check(prev, "previous"), _check(curr, "current")
    both = prev.merge(curr, on=["scheme_id", "isin"], suffixes=("_prev", "_curr"))
    ratios: dict = {}
    for isin, grp in both.groupby("isin"):
        qty_ratio = grp["quantity_curr"] / grp["quantity_prev"]
        px_prev = grp["market_value_lakhs_prev"].sum() / grp["quantity_prev"].sum()
        px_curr = grp["market_value_lakhs_curr"].sum() / grp["quantity_curr"].sum()
        price_ratio = px_curr / px_prev
        for k in SPLIT_RATIOS:
            if ((qty_ratio - k).abs() / k <= QTY_TOLERANCE).all() and \
                    abs(price_ratio * k - 1.0) <= PRICE_TOLERANCE:
                ratios[str(isin)] = k
                break
    for isin, k in (confirmed or {}).items():
        ratios[str(isin)] = float(k)
    return ratios

def diff_holdings(prev: pd.DataFrame, curr: pd.DataFrame,
                  prev_month: str, curr_month: str) -> pd.DataFrame:
    """Deltas for every (scheme, isin) of the schemes present in *both* months.

    Pure: ``prev``/``curr`` carry scheme_id, isin, quantity,
    market_value_lakhs and pct_nav. Each row has flow_lakhs (trading flow),
    price_effect_lakhs (residual) and value_change_lakhs.

    A scheme with a snapshot in only one month is left out, not compared
    against nothing: a newly launched fund would otherwise "buy" its whole
    opening portfolio, and a month whose file was not loaded would "exit"
    everything. No data is never activity. ``unmatched_schemes`` names them.

    Month-end-price convention: flow is valued at px_curr; monthly
    snapshots cannot see intra-month execution, so execution-vs-close
    differences sit in the price-effect residual.
    """
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

def _eligible_deltas(conn: sqlite3.Connection, report_month: str,
                     instrument_type: str | None,
                     active_equity_only: bool) -> tuple[str, list]:
    """FROM/WHERE (aliases d, s, sch) and params for the delta rows that vote in a month.

    A delta row is a comparison between report_month and d.prev_month, so it
    is excluded when either side is quarantined (per row for the previous
    month, since schemes can carry different prev_months in one report_month).
    Legacy DBs with no scheme_month_status table exclude neither -- nothing is
    known to be quarantined. The rules themselves live in findit.store.queries.
    """
    sql = ("FROM mf_holding_deltas d "
           "JOIN stocks s ON s.isin = d.isin "
           "JOIN schemes sch ON sch.scheme_id = d.scheme_id "
           "WHERE d.report_month = ?")
    params: list = [report_month]
    if instrument_type is not None:
        sql += " AND s.instrument_type = ?"
        params.append(instrument_type)
    if active_equity_only:
        sql += queries.active_equity_sql(conn, "sch")
    if queries.has_status_table(conn):
        for month in ("d.report_month", "d.prev_month"):
            sql += f" AND NOT {queries.quarantined_sql('d.scheme_id', month)}"
    return sql, params

def comparison_rows(conn: sqlite3.Connection, report_month: str,
                    active_equity_only: bool = True) -> pd.DataFrame:
    """Exact adjusted contributions shared by overview, evidence and history."""
    prev_month = (date.fromisoformat(report_month + "-01") - timedelta(days=1)).strftime("%Y-%m")
    source, params = _eligible_deltas(conn, report_month, "equity", active_equity_only)
    if not queries.has_status_table(conn):
        return pd.DataFrame()
    for required_month in ("d.report_month", "d.prev_month"):
        source += (" AND EXISTS (SELECT 1 FROM scheme_month_status checked "
                   f"WHERE checked.scheme_id=d.scheme_id AND checked.report_month={required_month} "
                   "AND checked.status='ok')")
    rows = pd.read_sql_query(
        "WITH eligible AS (SELECT d.*, sch.amc_name, s.name AS stock_name "
        + source + " AND d.prev_month = ?) "
        "SELECT d.scheme_id, d.isin, d.amc_name, d.stock_name, d.flow_lakhs, "
        "COALESCE(p.quantity, 0) AS quantity_prev, "
        "COALESCE(c.quantity, 0) AS quantity_curr, "
        "COALESCE(p.market_value_lakhs, 0) AS market_value_lakhs_prev, "
        "COALESCE(c.market_value_lakhs, 0) AS market_value_lakhs_curr, "
        "COALESCE(p.pct_nav, 0) AS pct_nav_prev, COALESCE(c.pct_nav, 0) AS pct_nav_curr "
        "FROM eligible d LEFT JOIN mf_holdings_monthly p ON p.scheme_id = d.scheme_id "
        "AND p.isin = d.isin AND p.report_month = d.prev_month "
        "LEFT JOIN mf_holdings_monthly c ON c.scheme_id = d.scheme_id "
        "AND c.isin = d.isin AND c.report_month = d.report_month "
        "WHERE (p.scheme_id IS NOT NULL OR c.scheme_id IS NOT NULL) "
        "AND EXISTS (SELECT 1 FROM mf_holdings_monthly h WHERE h.scheme_id = d.scheme_id "
        "AND h.report_month = d.prev_month) "
        "AND EXISTS (SELECT 1 FROM mf_holdings_monthly h WHERE h.scheme_id = d.scheme_id "
        "AND h.report_month = d.report_month)", conn, params=[*params, prev_month])
    if rows.empty:
        return rows

    def holdings(suffix: str) -> pd.DataFrame:
        return rows[["scheme_id", "isin", *[key + suffix for key in
                    ("quantity", "market_value_lakhs", "pct_nav")]]].rename(
            columns={key + suffix: key for key in ("quantity", "market_value_lakhs", "pct_nav")})

    prev, curr = holdings("_prev"), holdings("_curr")
    confirmed = {}
    if queries.has_table(conn, "corporate_actions"):
        confirmed = {str(isin): float(ratio) for isin, ratio in conn.execute(
            "SELECT isin, ratio FROM corporate_actions "
            "WHERE confirmed = 1 AND effective_month = ? AND ratio > 0", (report_month,))}
    ratios = infer_split_ratios(prev, curr, confirmed)
    rows["raw_quantity_prev"] = rows["quantity_prev"]
    rows["split_ratio"] = rows["isin"].map(ratios).fillna(1.0)
    rows["adjustment_basis"] = rows["isin"].map(
        lambda isin: "confirmed" if isin in confirmed else "inferred" if isin in ratios else "none")
    rows["quantity_prev"] *= rows["isin"].map(ratios).fillna(1.0)
    rows["qty_change"] = rows["quantity_curr"] - rows["quantity_prev"]
    if ratios:
        prev["quantity"] *= prev["isin"].map(ratios).fillna(1.0)
        # Reuse the pipeline's flow convention for adjusted comparisons.
        adjusted = diff_holdings(prev, curr, prev_month, report_month)
        flows = adjusted.set_index(["scheme_id", "isin"])["flow_lakhs"]
        split_rows = rows["isin"].isin(ratios)
        rows.loc[split_rows, "flow_lakhs"] = [
            flows.at[(sid, isin)] for sid, isin in
            rows.loc[split_rows, ["scheme_id", "isin"]].itertuples(index=False, name=None)]

    return rows
