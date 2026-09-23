"""Discretionary vs flow-driven buying. Pure; no DB/IO.

When a fund takes in money it buys more of almost everything it holds, so
"quantity went up" mostly measures investor inflows, not a manager's view.
Research on fund trades (Alexander, Cici & Gibson 2007) finds the trades a
manager chooses carry information and the flow-forced ones do not.

The measure: compare a stock's weight in the portfolio now with the weight
it would have had if the manager had not traded at all -- last month's
shares, valued at this month's prices ("drift weight"):

    drift_value_i  = qty_prev_i (split-adjusted) * price_curr_i
    w_drift_i      = drift_value_i / sum(drift_value)
    w_curr_i       = value_curr_i  / sum(value_curr)
    active_change  = w_curr_i - w_drift_i            (percentage points)

A fund that grew 10% and bought 10% more of every holding scores zero on
every stock. A fund that tilted towards a stock scores positive on it and
negative on what it funded that from. Weights are within the scheme's
equity sleeve, so a hybrid fund moving between equity and debt is not
mistaken for a view on individual stocks.

Stock splits and bonuses change quantities without any trade. Without an
adjustment a 1:2 split reads as every fund doubling its position, so
``infer_split_ratios`` detects them from the holdings themselves.
"""
from __future__ import annotations

import pandas as pd

from findit.core.corporate_actions import SPLIT_RATIOS

# Holders' quantity ratios must cluster this tightly on the split ratio...
QTY_TOLERANCE = 0.02
# ...and the per-share price must move by roughly its inverse.
PRICE_TOLERANCE = 0.15
# Scheme-level changes smaller than this are rounding noise, not a decision.
NOISE_FLOOR_PP = 0.05

_HOLDING_COLUMNS = ("scheme_id", "isin", "quantity", "market_value_lakhs")


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


def active_weight_changes(prev: pd.DataFrame, curr: pd.DataFrame,
                          fallback_prices: dict | None = None,
                          split_ratios: dict | None = None) -> pd.DataFrame:
    """Per (scheme, stock) active weight change between two months.

    ``prev``/``curr``: one scheme-month of equity holdings per scheme
    (scheme_id, isin, quantity, market_value_lakhs). Only schemes
    present in both are scored -- a scheme with no previous month has no
    drift to compare against.

    Current per-share prices come from the scheme's own holding when it
    still holds the stock, else the cross-scheme implied price this month,
    else ``fallback_prices`` (lakhs per share, e.g. an exchange close).
    With none of those the stock is priced flat (zero return) and the row
    is marked ``price_basis='assumed_flat'`` so it can be audited.
    """
    prev, curr = _check(prev, "previous"), _check(curr, "current")
    columns = ["scheme_id", "isin", "w_drift_pp", "w_curr_pp",
               "active_weight_change_pp", "discretionary_flow_lakhs", "price_basis"]
    shared = sorted(set(prev["scheme_id"]) & set(curr["scheme_id"]))
    if not shared:
        return pd.DataFrame(columns=columns)
    prev = prev[prev["scheme_id"].isin(shared)]
    curr = curr[curr["scheme_id"].isin(shared)]
    splits = split_ratios or {}
    fallback = fallback_prices or {}

    market_px = (curr.groupby("isin")["market_value_lakhs"].sum()
                 / curr.groupby("isin")["quantity"].sum()).to_dict()

    rows = []
    for scheme_id in shared:
        p = prev[prev["scheme_id"] == scheme_id].set_index("isin")
        c = curr[curr["scheme_id"] == scheme_id].set_index("isin")
        isins = sorted(set(p.index) | set(c.index))
        drift, basis = {}, {}
        for isin in isins:
            if isin not in p.index:
                drift[isin] = 0.0
                basis[isin] = "new_position"
                continue
            qty_prev = float(p.at[isin, "quantity"]) * float(splits.get(isin, 1.0))
            if isin in c.index:
                px, b = float(c.at[isin, "market_value_lakhs"]) / float(c.at[isin, "quantity"]), "own"
            elif isin in market_px:
                px, b = float(market_px[isin]), "cross_scheme"
            elif isin in fallback and fallback[isin] and fallback[isin] > 0:
                px, b = float(fallback[isin]), "exchange_close"
            else:
                px = float(p.at[isin, "market_value_lakhs"]) / float(p.at[isin, "quantity"])
                px /= float(splits.get(isin, 1.0))
                b = "assumed_flat"
            drift[isin] = qty_prev * px
            basis[isin] = b
        total_drift = sum(drift.values())
        total_curr = float(c["market_value_lakhs"].sum())
        if total_drift <= 0 or total_curr <= 0:
            continue
        for isin in isins:
            w_drift = drift[isin] / total_drift
            w_curr = float(c.at[isin, "market_value_lakhs"]) / total_curr if isin in c.index else 0.0
            change = w_curr - w_drift
            rows.append({
                "scheme_id": scheme_id, "isin": isin,
                "w_drift_pp": 100.0 * w_drift, "w_curr_pp": 100.0 * w_curr,
                "active_weight_change_pp": 100.0 * change,
                "discretionary_flow_lakhs": change * total_curr,
                "price_basis": basis[isin],
            })
    return pd.DataFrame(rows, columns=columns)


def aggregate_by_stock(changes: pd.DataFrame, scheme_amc: dict,
                       noise_floor_pp: float = NOISE_FLOOR_PP) -> pd.DataFrame:
    """Per-stock breadth of *discretionary* buying across AMCs.

    A scheme counts as an active buyer (seller) of a stock when its active
    weight change is at least ``noise_floor_pp`` above (below) zero. An AMC
    counts once, in the direction of its schemes' net discretionary flow,
    because one AMC's schemes share a research desk: twelve schemes buying
    the same stock are one opinion, not twelve.
    """
    columns = ["isin", "active_amcs_buying", "active_amcs_selling",
               "net_active_amc_count", "discretionary_flow_lakhs",
               "active_weight_change_pp_sum"]
    if changes is None or changes.empty:
        return pd.DataFrame(columns=columns)
    work = changes.copy()
    work["amc_name"] = work["scheme_id"].map(scheme_amc)
    if work["amc_name"].isna().any():
        missing = sorted(work.loc[work["amc_name"].isna(), "scheme_id"].unique())
        raise ValueError(f"no AMC recorded for scheme_id(s) {missing}")
    work["vote"] = 0
    work.loc[work["active_weight_change_pp"] >= noise_floor_pp, "vote"] = 1
    work.loc[work["active_weight_change_pp"] <= -noise_floor_pp, "vote"] = -1
    decided = work[work["vote"] != 0]
    amc_flow = decided.groupby(["isin", "amc_name"])["discretionary_flow_lakhs"].sum()
    amc_dir = amc_flow.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0)).rename("dir")
    amc_dir = amc_dir.reset_index()
    buying = amc_dir[amc_dir["dir"] > 0].groupby("isin").size()
    selling = amc_dir[amc_dir["dir"] < 0].groupby("isin").size()
    out = pd.DataFrame({
        "active_amcs_buying": buying,
        "active_amcs_selling": selling,
        "discretionary_flow_lakhs": work.groupby("isin")["discretionary_flow_lakhs"].sum(),
        "active_weight_change_pp_sum": work.groupby("isin")["active_weight_change_pp"].sum(),
    })
    out[["active_amcs_buying", "active_amcs_selling"]] = (
        out[["active_amcs_buying", "active_amcs_selling"]].fillna(0).astype(int))
    out["net_active_amc_count"] = out["active_amcs_buying"] - out["active_amcs_selling"]
    out.index.name = "isin"
    return out.reset_index().loc[:, columns]
