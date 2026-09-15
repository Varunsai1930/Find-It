"""
consensus_signals.py — cross-fund agreement per stock for a given month.

This is the "MF side" of the common-holdings idea: how many distinct
schemes/AMCs added vs trimmed a given stock this month. Once FII/DII
quarterly data is loaded (phase 1, not built yet — see build plan §2),
this joins against that on ISIN to produce the full MF+FII overlap signal.
For now this proves the MF-only consensus logic end to end.
"""
import sqlite3
import pandas as pd


def compute_consensus(conn: sqlite3.Connection, report_month: str) -> pd.DataFrame:
    deltas = pd.read_sql_query(
        "SELECT d.isin, s.name AS stock_name, d.action, d.value_change_lakhs, "
        "       sch.amc_name "
        "FROM mf_holding_deltas d "
        "JOIN stocks s ON s.isin = d.isin "
        "JOIN schemes sch ON sch.scheme_id = d.scheme_id "
        "WHERE d.report_month = ?",
        conn, params=(report_month,),
    )

    if deltas.empty:
        return pd.DataFrame(columns=[
            "isin", "stock_name", "amcs_buying", "amcs_selling",
            "net_amc_count", "total_value_change_lakhs",
        ])

    buying = deltas[deltas["action"].isin(["new", "added"])]
    selling = deltas[deltas["action"].isin(["trimmed", "exited"])]

    buy_counts = buying.groupby("isin")["amc_name"].nunique().rename("amcs_buying")
    sell_counts = selling.groupby("isin")["amc_name"].nunique().rename("amcs_selling")
    value_totals = deltas.groupby("isin")["value_change_lakhs"].sum().rename("total_value_change_lakhs")
    names = deltas.drop_duplicates("isin").set_index("isin")["stock_name"]

    out = pd.concat([names, buy_counts, sell_counts, value_totals], axis=1).fillna(0)
    out["amcs_buying"] = out["amcs_buying"].astype(int)
    out["amcs_selling"] = out["amcs_selling"].astype(int)
    out["net_amc_count"] = out["amcs_buying"] - out["amcs_selling"]
    out = out.reset_index().rename(columns={"index": "isin", "stock_name": "stock_name"})
    return out.sort_values("net_amc_count", ascending=False)


def join_shareholding_increase(
    conn: sqlite3.Connection, consensus: pd.DataFrame,
) -> pd.DataFrame:
    """Add the latest available FII/DII quarter-over-quarter signal to MF consensus.

    A common signal means the MF consensus is positive (more AMCs bought than
    sold) and either FII or the deliberately-conservative DII aggregate rose
    between the two most recent filed quarters.  A left join preserves all MF
    consensus rows; missing shareholding filings are never interpreted as zero.
    """
    signal_columns = [
        "shareholding_quarter_end", "previous_shareholding_quarter_end",
        "fii_pct", "previous_fii_pct", "fii_pct_change", "dii_pct",
        "previous_dii_pct", "dii_pct_change", "mf_increased",
        "fii_increased", "dii_increased", "fii_or_dii_increased",
        "is_common_with_fii_increase",
    ]
    if consensus.empty:
        out = consensus.copy()
        for column in signal_columns:
            out[column] = pd.Series(dtype="bool" if column.endswith("increased") else "object")
        return out

    shareholding = pd.read_sql_query(
        """WITH ranked AS (
               SELECT isin, quarter_end, fii_pct, dii_pct,
                      ROW_NUMBER() OVER (
                          PARTITION BY isin ORDER BY quarter_end DESC
                      ) AS quarter_rank
               FROM shareholding_quarterly
           )
           SELECT latest.isin,
                  latest.quarter_end AS shareholding_quarter_end,
                  previous.quarter_end AS previous_shareholding_quarter_end,
                  latest.fii_pct,
                  previous.fii_pct AS previous_fii_pct,
                  latest.fii_pct - previous.fii_pct AS fii_pct_change,
                  latest.dii_pct,
                  previous.dii_pct AS previous_dii_pct,
                  latest.dii_pct - previous.dii_pct AS dii_pct_change
           FROM ranked AS latest
           JOIN ranked AS previous
             ON previous.isin = latest.isin
            AND previous.quarter_rank = 2
           WHERE latest.quarter_rank = 1""",
        conn,
    )

    out = consensus.merge(shareholding, on="isin", how="left")
    out["mf_increased"] = out["net_amc_count"].gt(0)
    out["fii_increased"] = out["fii_pct_change"].gt(0).fillna(False)
    out["dii_increased"] = out["dii_pct_change"].gt(0).fillna(False)
    out["fii_or_dii_increased"] = out["fii_increased"] | out["dii_increased"]
    out["is_common_with_fii_increase"] = (
        out["mf_increased"] & out["fii_or_dii_increased"]
    )
    return out.sort_values(
        ["is_common_with_fii_increase", "net_amc_count"],
        ascending=[False, False],
    )
