"""
consensus_signals.py — cross-fund agreement per stock for a given month.

This is the "MF side" of the common-holdings idea: how many distinct
schemes/AMCs added vs trimmed a given stock this month. Once FII/DII
quarterly data is loaded (phase 1, not built yet — see build plan §2),
this joins against that on ISIN to produce the full MF+FII overlap signal.
For now this proves the MF-only consensus logic end to end.
"""
import calendar
import sqlite3
from datetime import date

import pandas as pd


def compute_consensus(
    conn: sqlite3.Connection,
    report_month: str,
    instrument_type: str | None = "equity",
) -> pd.DataFrame:
    sql = (
        "SELECT d.isin, s.name AS stock_name, d.action, d.value_change_lakhs, "
        "       d.flow_lakhs, sch.amc_name "
        "FROM mf_holding_deltas d "
        "JOIN stocks s ON s.isin = d.isin "
        "JOIN schemes sch ON sch.scheme_id = d.scheme_id "
        "WHERE d.report_month = ?"
    )
    params = [report_month]
    if instrument_type is not None:
        sql += " AND s.instrument_type = ?"
        params.append(instrument_type)

    deltas = pd.read_sql_query(sql, conn, params=params)

    if deltas.empty:
        return pd.DataFrame(columns=[
            "isin", "stock_name", "amcs_buying", "amcs_selling",
            "total_flow_lakhs", "total_value_change_lakhs", "net_amc_count",
            "buying_ratio",
        ])

    buying = deltas[deltas["action"].isin(["new", "added"])]
    selling = deltas[deltas["action"].isin(["trimmed", "exited"])]

    buy_counts = buying.groupby("isin")["amc_name"].nunique().rename("amcs_buying")
    sell_counts = selling.groupby("isin")["amc_name"].nunique().rename("amcs_selling")
    value_totals = deltas.groupby("isin")["value_change_lakhs"].sum().rename("total_value_change_lakhs")
    names = deltas.drop_duplicates("isin").set_index("isin")["stock_name"]

    series_to_concat = [names, buy_counts, sell_counts]
    if "flow_lakhs" in deltas.columns and deltas["flow_lakhs"].notna().any():
        flow_totals = deltas.groupby("isin")["flow_lakhs"].sum().rename("total_flow_lakhs")
        series_to_concat.append(flow_totals)
    series_to_concat.append(value_totals)

    out = pd.concat(series_to_concat, axis=1)
    # Ensure total_flow_lakhs always exists so ranking is stable even when
    # no flow data was recorded for this month.
    if "total_flow_lakhs" not in out.columns:
        out["total_flow_lakhs"] = 0.0
    # fillna(0) would coerce stock_name NaNs to 0; fill numeric cols only,
    # then fill any missing names with empty string (should not happen).
    for col in ["amcs_buying", "amcs_selling", "total_flow_lakhs", "total_value_change_lakhs"]:
        if col in out.columns:
            out[col] = out[col].fillna(0)
    if "stock_name" in out.columns:
        out["stock_name"] = out["stock_name"].fillna("")
    out["amcs_buying"] = out["amcs_buying"].astype(int)
    out["amcs_selling"] = out["amcs_selling"].astype(int)
    out["net_amc_count"] = out["amcs_buying"] - out["amcs_selling"]
    denom = (out["amcs_buying"] + out["amcs_selling"]).clip(lower=1)
    out["buying_ratio"] = out["amcs_buying"] / denom
    out = out.reset_index()
    # reset_index names the ISIN column "isin" when the index is named,
    # otherwise "index" — normalise to "isin" in both cases.
    if "index" in out.columns and "isin" not in out.columns:
        out = out.rename(columns={"index": "isin"})
    return out.sort_values(
        ["net_amc_count", "total_flow_lakhs"], ascending=[False, False]
    ).reset_index(drop=True)


def _month_end_iso(as_of_month: str) -> str:
    """Return the YYYY-MM-DD month-end for a YYYY-MM string (raises ValueError)."""
    try:
        parts = str(as_of_month).split("-")
        if len(parts) != 2:
            raise ValueError
        y, m = int(parts[0]), int(parts[1])
        last_day = calendar.monthrange(y, m)[1]
        return date(y, m, last_day).isoformat()
    except Exception as exc:
        raise ValueError(f"as_of_month must be YYYY-MM, got {as_of_month!r}") from exc


def _reference_date(as_of_month: str | None) -> date:
    if as_of_month is None:
        return date.today()
    return date.fromisoformat(_month_end_iso(as_of_month))


def join_shareholding_increase(
    conn: sqlite3.Connection,
    consensus: pd.DataFrame,
    as_of_month: str | None = None,
    stale_after_days: int = 200,
) -> pd.DataFrame:
    """Add the latest available FII/DII quarter-over-quarter signal to MF consensus.

    A common signal means the MF consensus is positive (more AMCs bought than
    sold) and either FII or the deliberately-conservative DII aggregate rose
    between the two most recent filed quarters.  A left join preserves all MF
    consensus rows; missing shareholding filings are never interpreted as zero.

    Quarterly filtering:
      - when ``shareholding_quarterly`` has a ``filing_type`` column, only
        ``filing_type='quarterly'`` rows are considered;
      - otherwise (legacy DBs) only rows whose quarter_end month-day is a
        standard quarter end (03-31/06-30/09-30/12-31) are considered, so
        interim filings never displace true quarterlies.

    ``as_of_month`` (YYYY-MM, optional) is a no-lookahead cutoff: only
    quarters with ``quarter_end <= <month-end>`` are ranked.  ``staleness_days``
    is ``<reference month-end or today> - shareholding_quarter_end`` in days;
    ``shareholding_stale`` is True when the quarter is missing or older than
    ``stale_after_days`` (default 200).

    Directions are tri-state: ``increased`` (change > 0), ``decreased``
    (change <= 0 with data present), ``no_data`` (change is NaN — e.g. only
    one quarterly filing exists).  ``fii_increased``/``dii_increased`` booleans
    are kept for compat and derived from the directions; ``is_common`` (alias
    ``is_common_with_fii_increase``) requires ``mf_increased`` AND an
    ``increased`` direction.
    """
    signal_columns = [
        "shareholding_quarter_end", "previous_shareholding_quarter_end",
        "fii_pct", "previous_fii_pct", "fii_pct_change", "dii_pct",
        "previous_dii_pct", "dii_pct_change", "mf_increased",
        "fii_increased", "dii_increased", "fii_or_dii_increased",
        "is_common_with_fii_increase", "is_common",
        "fii_direction", "dii_direction",
        "staleness_days", "shareholding_stale",
    ]
    if consensus.empty:
        out = consensus.copy()
        for column in signal_columns:
            if column in ("mf_increased", "fii_increased", "dii_increased",
                           "fii_or_dii_increased", "is_common_with_fii_increase",
                           "is_common", "shareholding_stale"):
                out[column] = pd.Series(dtype="bool")
            elif column in ("fii_direction", "dii_direction"):
                out[column] = pd.Series(dtype="object")
            elif column in ("staleness_days", "fii_pct", "previous_fii_pct",
                            "fii_pct_change", "dii_pct", "previous_dii_pct",
                            "dii_pct_change"):
                out[column] = pd.Series(dtype="float")
            else:
                out[column] = pd.Series(dtype="object")
        return out

    try:
        sh_cols = [r[1] for r in conn.execute("PRAGMA table_info(shareholding_quarterly)").fetchall()]
    except Exception:
        sh_cols = []

    where_clauses: list[str] = []
    params: list = []
    if "filing_type" in sh_cols:
        # NULL-safe: legacy rows predate filing_type; infer quarterly for them.
        where_clauses.append(
            "(filing_type = 'quarterly' OR (filing_type IS NULL AND "
            "substr(quarter_end,6,5) IN ('03-31','06-30','09-30','12-31')))"
        )
    elif "quarter_end" in sh_cols or not sh_cols:
        # Fallback for DBs without filing_type: only standard quarter ends.
        where_clauses.append("substr(quarter_end,6,5) IN ('03-31','06-30','09-30','12-31')")
    # else: unknown schema — legacy behavior (no quarterly filter).

    month_end_iso: str | None = None
    if as_of_month is not None:
        month_end_iso = _month_end_iso(as_of_month)
        where_clauses.append("quarter_end <= ?")
        params.append(month_end_iso)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    shareholding = pd.read_sql_query(
        f"""WITH filtered AS (
               SELECT isin, quarter_end, fii_pct, dii_pct
               FROM shareholding_quarterly
               {where_sql}
            ),
            ranked AS (
               SELECT isin, quarter_end, fii_pct, dii_pct,
                      ROW_NUMBER() OVER (
                          PARTITION BY isin ORDER BY quarter_end DESC
                      ) AS quarter_rank
               FROM filtered
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
            LEFT JOIN ranked AS previous
              ON previous.isin = latest.isin
             AND previous.quarter_rank = 2
            WHERE latest.quarter_rank = 1""",
        conn,
        params=params,
    )

    out = consensus.merge(shareholding, on="isin", how="left")
    out["mf_increased"] = out["net_amc_count"].gt(0).fillna(False)

    def _direction(series: pd.Series) -> pd.Series:
        # NaN change -> no_data (never False-as-decreased); >0 -> increased.
        return series.apply(
            lambda x: "no_data" if pd.isna(x) else ("increased" if x > 0 else "decreased")
        )

    out["fii_direction"] = _direction(out["fii_pct_change"])
    out["dii_direction"] = _direction(out["dii_pct_change"])
    out["fii_increased"] = out["fii_direction"] == "increased"
    out["dii_increased"] = out["dii_direction"] == "increased"
    out["fii_or_dii_increased"] = out["fii_increased"] | out["dii_increased"]
    out["is_common_with_fii_increase"] = (
        out["mf_increased"] & out["fii_or_dii_increased"]
    )
    out["is_common"] = out["is_common_with_fii_increase"]

    ref = _reference_date(as_of_month)
    ref_ts = pd.Timestamp(ref.isoformat())
    q_ts = pd.to_datetime(out["shareholding_quarter_end"], errors="coerce")
    out["staleness_days"] = (ref_ts - q_ts).dt.days.astype("float")
    out["shareholding_stale"] = out["staleness_days"].isna() | (
        out["staleness_days"] > stale_after_days
    )
    return out.sort_values(
        ["is_common_with_fii_increase", "net_amc_count"],
        ascending=[False, False],
    ).reset_index(drop=True)
