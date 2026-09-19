"""
consensus_signals.py — cross-fund agreement per stock for a given month.

``compute_consensus`` is the "MF side" of the common-holdings idea: how
many distinct schemes/AMCs added vs trimmed a given stock this month.
``join_shareholding_increase`` (Phase 1) joins that output against
quarterly FII/DII shareholding on ISIN to produce the full MF+FII
overlap signal: MF net buying plus an FII-or-DII quarterly increase,
with missing filings kept as no_data (never zero) and stale quarters
flagged via ``as_of_month``.
"""
import calendar
import sqlite3
from datetime import date

import pandas as pd


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of a table (empty set when unreadable)."""
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _delta_columns(conn: sqlite3.Connection) -> set[str]:
    """Column names of mf_holding_deltas (empty set when unreadable)."""
    return _table_columns(conn, "mf_holding_deltas")


def _quarantined_scheme_ids(conn: sqlite3.Connection, report_month: str) -> list[int]:
    """Scheme IDs quarantined for report_month (empty when table missing)."""
    try:
        rows = conn.execute(
            "SELECT scheme_id FROM scheme_month_status "
            "WHERE report_month = ? AND status = 'quarantined'",
            (report_month,),
        ).fetchall()
    except Exception:
        return []
    return [int(r[0]) for r in rows if r[0] is not None]


def _prev_universe_isins(conn: sqlite3.Connection, prev_months) -> set[str] | None:
    """ISINs any tracked scheme already held in the compared previous month(s).

    Returns None when no previous month is known, so callers report
    ``unknown`` instead of asserting every stock is a new listing.
    """
    months = sorted({str(m) for m in prev_months if m is not None and str(m) != "nan"})
    if not months:
        return None
    placeholders = ",".join("?" for _ in months)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT isin FROM mf_holdings_monthly "
            f"WHERE report_month IN ({placeholders})",
            months,
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    return {str(r[0]) for r in rows}


def compute_consensus(
    conn: sqlite3.Connection,
    report_month: str,
    instrument_type: str | None = "equity",
    active_equity_only: bool = True,
) -> pd.DataFrame:
    # price_effect_lakhs is added PRAGMA-guarded by Worker A; legacy DBs
    # lack it, so detect upfront and fall back to a price-less SELECT.
    has_price = "price_effect_lakhs" in _delta_columns(conn)

    def _build_sql(include_price: bool) -> str:
        price_select = "d.price_effect_lakhs, " if include_price else ""
        sql = (
            "SELECT d.isin, s.name AS stock_name, d.action, d.value_change_lakhs, "
            f"       d.flow_lakhs, {price_select}d.prev_month, sch.amc_name "
            "FROM mf_holding_deltas d "
            "JOIN stocks s ON s.isin = d.isin "
            "JOIN schemes sch ON sch.scheme_id = d.scheme_id "
            "WHERE d.report_month = ?"
        )
        if instrument_type is not None:
            sql += " AND s.instrument_type = ?"
        return sql

    params: list = [report_month]
    if instrument_type is not None:
        params.append(instrument_type)

    # Passive/debt schemes never count toward market-wide conviction.
    # Independent of, and additional to, the instrument_type filter.
    # Guarded for legacy DBs whose schemes table predates the column.
    has_active_col = "is_active_equity" in _table_columns(conn, "schemes")
    extra_sql = ""
    if active_equity_only and has_active_col:
        extra_sql += " AND sch.is_active_equity = 1"
    quarantined = _quarantined_scheme_ids(conn, report_month)
    if quarantined:
        placeholders = ",".join("?" for _ in quarantined)
        extra_sql += f" AND d.scheme_id NOT IN ({placeholders})"
        params.extend(quarantined)

    def _build_sql_guarded(include_price: bool) -> str:
        return _build_sql(include_price) + extra_sql

    try:
        deltas = pd.read_sql_query(_build_sql_guarded(has_price), conn, params=params)
    except sqlite3.OperationalError as exc:
        # Defensive retry for DBs whose PRAGMA advertised the column but
        # whose table actually predates it (or vice versa).
        if has_price and "price_effect" in str(exc).lower():
            has_price = False
            deltas = pd.read_sql_query(_build_sql_guarded(False), conn, params=params)
        else:
            raise

    if deltas.empty:
        return pd.DataFrame(columns=[
            "isin", "stock_name", "amcs_buying", "amcs_selling",
            "amcs_opening", "total_flow_lakhs", "new_position_flow_lakhs",
            "accumulation_flow_lakhs", "total_price_effect_lakhs",
            "net_amc_count", "buying_ratio", "universe_status",
            "is_new_to_universe",
        ])

    buying = deltas[deltas["action"].isin(["new", "added"])]
    selling = deltas[deltas["action"].isin(["trimmed", "exited"])]
    opening = deltas[deltas["action"] == "new"]

    buy_counts = buying.groupby("isin")["amc_name"].nunique().rename("amcs_buying")
    sell_counts = selling.groupby("isin")["amc_name"].nunique().rename("amcs_selling")
    open_counts = opening.groupby("isin")["amc_name"].nunique().rename("amcs_opening")
    names = deltas.drop_duplicates("isin").set_index("isin")["stock_name"]

    series_to_concat = [names, buy_counts, sell_counts, open_counts]
    if "flow_lakhs" in deltas.columns and deltas["flow_lakhs"].notna().any():
        flow_totals = deltas.groupby("isin")["flow_lakhs"].sum().rename("total_flow_lakhs")
        series_to_concat.append(flow_totals)
        # A new position books its entire market value as flow, so an IPO or
        # fresh listing every fund "bought" because it began existing outranks
        # real accumulation. Split the two rather than ranking on the sum.
        new_flow = (
            opening.groupby("isin")["flow_lakhs"].sum().rename("new_position_flow_lakhs")
        )
        series_to_concat.append(new_flow)
    if has_price and "price_effect_lakhs" in deltas.columns:
        price_totals = (
            deltas.groupby("isin")["price_effect_lakhs"].sum().rename("total_price_effect_lakhs")
        )
        series_to_concat.append(price_totals)

    out = pd.concat(series_to_concat, axis=1)
    # Ensure totals always exist so ranking is stable even when no flow or
    # price-effect data was recorded for this month (legacy DBs).
    if "total_flow_lakhs" not in out.columns:
        out["total_flow_lakhs"] = 0.0
    if "total_price_effect_lakhs" not in out.columns:
        out["total_price_effect_lakhs"] = 0.0
    if "new_position_flow_lakhs" not in out.columns:
        out["new_position_flow_lakhs"] = 0.0
    # fillna(0) would coerce stock_name NaNs to 0; fill numeric cols only,
    # then fill any missing names with empty string (should not happen).
    for col in ["amcs_buying", "amcs_selling", "amcs_opening", "total_flow_lakhs",
                "total_price_effect_lakhs", "new_position_flow_lakhs"]:
        if col in out.columns:
            out[col] = out[col].fillna(0)
    if "stock_name" in out.columns:
        out["stock_name"] = out["stock_name"].fillna("")
    out["amcs_buying"] = out["amcs_buying"].astype(int)
    out["amcs_selling"] = out["amcs_selling"].astype(int)
    out["amcs_opening"] = out["amcs_opening"].astype(int)
    # Capital moved into or out of positions that already existed last month.
    out["accumulation_flow_lakhs"] = (
        out["total_flow_lakhs"] - out["new_position_flow_lakhs"]
    )
    out["net_amc_count"] = out["amcs_buying"] - out["amcs_selling"]
    denom = (out["amcs_buying"] + out["amcs_selling"]).clip(lower=1)
    out["buying_ratio"] = out["amcs_buying"] / denom
    out = out.reset_index()
    # reset_index names the ISIN column "isin" when the index is named,
    # otherwise "index" — normalise to "isin" in both cases.
    if "index" in out.columns and "isin" not in out.columns:
        out = out.rename(columns={"index": "isin"})

    # Tri-state, like the FII/DII directions: a stock absent from every
    # tracked portfolio last month is a new listing, not a conviction buy.
    # With no previous month on record we say "unknown" rather than
    # branding the whole universe new.
    universe = _prev_universe_isins(
        conn, deltas["prev_month"].tolist() if "prev_month" in deltas.columns else []
    )
    if universe is None:
        out["universe_status"] = "unknown"
    else:
        out["universe_status"] = out["isin"].apply(
            lambda i: "established" if str(i) in universe else "new_listing"
        )
    out["is_new_to_universe"] = out["universe_status"] == "new_listing"

    # Consensus breadth still leads. Within one breadth level, established
    # names outrank fresh listings, and the tiebreak is accumulation flow —
    # never the entry value of a position that had nowhere to come from.
    return out.sort_values(
        ["net_amc_count", "is_new_to_universe", "accumulation_flow_lakhs"],
        ascending=[False, True, False],
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
