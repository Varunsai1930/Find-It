"""
consensus_signals.py — cross-fund agreement per stock for a given month.

``compute_consensus`` is the "MF side" of the common-holdings idea: how
many distinct schemes/AMCs added vs trimmed a given stock this month.
``join_shareholding_increase`` joins that output against
quarterly FII/DII shareholding on ISIN to produce the full MF+FII
overlap signal: MF net buying plus an FII-or-DII quarterly increase,
with missing filings kept as no_data (never zero) and stale quarters
flagged via ``as_of_month``.
"""
import sqlite3
from datetime import date, timedelta

import pandas as pd

from findit.core import active_weight, delta_calculator, publication
from findit.store import queries


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
    except sqlite3.DatabaseError:
        return None
    if not rows:
        return None
    return {str(r[0]) for r in rows}


ACTIVE_COLUMNS = [
    "active_amcs_buying", "active_amcs_selling", "net_active_amc_count",
    "discretionary_flow_lakhs", "active_weight_change_pp_sum",
]


def compute_active_consensus(
    conn: sqlite3.Connection,
    report_month: str,
    instrument_type: str | None = "equity",
    active_equity_only: bool = True,
) -> pd.DataFrame:
    """Per-stock breadth of *discretionary* buying (see findit.core.active_weight).

    The same schemes vote as in compute_consensus (``_eligible_deltas``):
    active only, and never a comparison with a quarantined month on either
    side -- the drift weight is built from the previous month, so bad data
    there would make it unsafe.
    """
    empty = pd.DataFrame(columns=["isin"] + ACTIVE_COLUMNS)
    # Deltas-only DBs (legacy, or built for a test) carry no holdings to
    # measure drift from: report no discretionary data rather than failing.
    if not queries.table_columns(conn, "mf_holding_deltas") or not queries.table_columns(
            conn, "mf_holdings_monthly"):
        return empty
    # Every stock of a compared scheme has a delta row, even when unchanged,
    # so the instrument filter is applied to the holdings below instead.
    source, params = _eligible_deltas(conn, report_month, None, active_equity_only)
    prev_months = {int(sid): str(prev) for sid, prev in conn.execute(
        f"SELECT DISTINCT d.scheme_id, d.prev_month {source} AND d.prev_month IS NOT NULL",
        params).fetchall()}
    if not prev_months:
        return empty

    type_sql = " AND s.instrument_type = ?" if instrument_type is not None else ""

    def _holdings(scheme_month: list[tuple[int, str]]) -> pd.DataFrame:
        frames = []
        for month in sorted({m for _, m in scheme_month}):
            ids = [sid for sid, m in scheme_month if m == month]
            marks = ",".join("?" for _ in ids)
            params = [month, *ids] + ([instrument_type] if instrument_type is not None else [])
            frames.append(pd.read_sql_query(
                "SELECT h.scheme_id, h.isin, h.quantity, h.market_value_lakhs "
                "FROM mf_holdings_monthly h JOIN stocks s ON s.isin = h.isin "
                f"WHERE h.report_month = ? AND h.scheme_id IN ({marks}){type_sql}",
                conn, params=params))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["scheme_id", "isin", "quantity", "market_value_lakhs"])

    prev = _holdings(list(prev_months.items()))
    curr = _holdings([(sid, report_month) for sid in prev_months])

    confirmed = {}
    if queries.table_columns(conn, "corporate_actions"):
        confirmed = {str(r[0]): float(r[1]) for r in conn.execute(
            "SELECT isin, ratio FROM corporate_actions "
            "WHERE confirmed = 1 AND effective_month = ? AND ratio > 0", (report_month,))}
    fallback = {}
    if queries.table_columns(conn, "security_prices_monthly"):
        # Exchange closes are rupees per share; holdings are lakhs.
        fallback = {str(r[0]): float(r[1]) / 1e5 for r in conn.execute(
            "SELECT isin, close_price FROM security_prices_monthly "
            "WHERE report_month = ? AND close_price > 0", (report_month,))}

    splits = active_weight.infer_split_ratios(prev, curr, confirmed)
    changes = active_weight.active_weight_changes(prev, curr, fallback, splits)
    scheme_amc = {int(r[0]): str(r[1]) for r in conn.execute(
        "SELECT scheme_id, amc_name FROM schemes")}
    return active_weight.aggregate_by_stock(changes, scheme_amc)


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


def voting_schemes(
    conn: sqlite3.Connection,
    report_month: str,
    instrument_type: str | None = "equity",
    active_equity_only: bool = True,
) -> dict[int, str]:
    """scheme_id -> AMC for every scheme compute_consensus counts in report_month.

    A scheme is in the comparison when it has a delta row (it was compared
    with a previous month) on an in-scope stock and is neither filtered out
    nor quarantined -- for report_month or for the previous month that row
    compares against. Coverage counts come from here so they cannot drift
    from the ranking.
    """
    source, params = _eligible_deltas(conn, report_month, instrument_type, active_equity_only)
    rows = conn.execute(f"SELECT DISTINCT d.scheme_id, sch.amc_name {source}", params).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def fund_house_activity(conn: sqlite3.Connection, report_month: str,
                        active_equity_only: bool = True) -> dict:
    """Net equity share changes per AMC, across comparable schemes only.

    The monthly overview uses the same validation gates as the activity
    table, requires the immediately preceding month, and nets purchases
    against sales within each fund house before counting stocks. Splits and
    bonuses adjust the previous share count, never becoming purchases.
    """
    prev_month = (date.fromisoformat(report_month + "-01") - timedelta(days=1)).strftime("%Y-%m")
    scope = queries.active_equity_sql(conn, "sch") if active_equity_only else ""
    loaded = conn.execute(
        "SELECT sch.amc_name, COUNT(DISTINCT h.scheme_id) "
        "FROM mf_holdings_monthly h JOIN schemes sch ON sch.scheme_id = h.scheme_id "
        f"WHERE h.report_month = ?{scope} AND EXISTS ("
        "SELECT 1 FROM mf_holdings_monthly eq JOIN stocks s ON s.isin = eq.isin "
        "WHERE eq.scheme_id = h.scheme_id AND eq.report_month IN (?, ?) "
        "AND s.instrument_type = 'equity') "
        "GROUP BY sch.amc_name", (report_month, report_month, prev_month)).fetchall()
    houses = {str(amc): {"loaded": int(count), "compared": 0, "increased": 0,
                         "reduced": 0, "biggest": None} for amc, count in loaded}
    source, params = _eligible_deltas(conn, report_month, "equity", active_equity_only)
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
        return {"prev_month": prev_month, "houses": houses}

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
    ratios = active_weight.infer_split_ratios(prev, curr, confirmed)
    rows["quantity_prev"] *= rows["isin"].map(ratios).fillna(1.0)
    rows["qty_change"] = rows["quantity_curr"] - rows["quantity_prev"]
    if ratios:
        prev["quantity"] *= prev["isin"].map(ratios).fillna(1.0)
        # Reuse the pipeline's flow convention for adjusted comparisons.
        adjusted = delta_calculator.diff_holdings(prev, curr, prev_month, report_month)
        flows = adjusted.set_index(["scheme_id", "isin"])["flow_lakhs"]
        split_rows = rows["isin"].isin(ratios)
        rows.loc[split_rows, "flow_lakhs"] = [
            flows.at[(sid, isin)] for sid, isin in
            rows.loc[split_rows, ["scheme_id", "isin"]].itertuples(index=False, name=None)]

    for amc, group in rows.groupby("amc_name", sort=True):
        house = houses.setdefault(str(amc), {"loaded": 0})
        stocks = group.groupby("isin", sort=True).agg(
            stock_name=("stock_name", "first"), quantity_prev=("quantity_prev", "sum"),
            quantity_curr=("quantity_curr", "sum"), qty_change=("qty_change", "sum"),
            flow_lakhs=("flow_lakhs", lambda values: values.sum(min_count=len(values))))
        changed = stocks[stocks["qty_change"].abs() > 1e-6]
        priced = changed[changed["flow_lakhs"].notna()].copy()
        biggest = None
        if not priced.empty:
            priced["magnitude"] = priced["flow_lakhs"].abs()
            move = priced.sort_values("magnitude", ascending=False, kind="stable").iloc[0]
            biggest = {"isin": str(move.name), "stock_name": str(move.stock_name),
                       "qty_change": float(move.qty_change), "flow_lakhs": float(move.flow_lakhs),
                       "change_pct": (100 * float(move.qty_change / move.quantity_prev)
                                      if move.quantity_prev > 0 else None)}
        house.update(compared=int(group["scheme_id"].nunique()),
                     increased=int((changed["qty_change"] > 0).sum()),
                     reduced=int((changed["qty_change"] < 0).sum()), biggest=biggest,
                     unpriced=int(changed["flow_lakhs"].isna().sum()))
    return {"prev_month": prev_month, "houses": houses}


def compute_consensus(
    conn: sqlite3.Connection,
    report_month: str,
    instrument_type: str | None = "equity",
    active_equity_only: bool = True,
) -> pd.DataFrame:
    """Per-stock cross-fund buying vs selling for one month, best-supported first.

    The one implementation of the consensus signal: the pipeline, the
    backtests and the web dashboard all rank with it. Only active equity
    schemes vote (``active_equity_only``) and quarantined scheme-months never
    do -- including a scheme whose delta compares report_month against a
    previous month that was itself quarantined, since the comparison is only
    as good as its worse side. AMC counts lead; scheme counts are carried for
    display.
    """
    # Legacy DBs predate price_effect_lakhs; PRAGMA is authoritative.
    has_price = "price_effect_lakhs" in queries.table_columns(conn, "mf_holding_deltas")
    price_select = "d.price_effect_lakhs, " if has_price else ""
    source, params = _eligible_deltas(conn, report_month, instrument_type, active_equity_only)
    sql = (
        "SELECT d.isin, d.scheme_id, s.name AS stock_name, s.industry, "
        "       s.instrument_type, d.action, d.value_change_lakhs, "
        f"       d.flow_lakhs, {price_select}d.prev_month, sch.amc_name {source}"
    )
    deltas = pd.read_sql_query(sql, conn, params=params)
    if deltas.empty:
        return rank_consensus(deltas, None, pd.DataFrame(columns=["isin"] + ACTIVE_COLUMNS))
    universe = _prev_universe_isins(conn, deltas["prev_month"].tolist())
    active = compute_active_consensus(conn, report_month, instrument_type, active_equity_only)
    return rank_consensus(deltas, universe, active)


def rank_consensus(deltas: pd.DataFrame, prev_universe: set[str] | None,
                   active: pd.DataFrame) -> pd.DataFrame:
    """The consensus ranking from eligible delta rows. Pure.

    ``deltas``: one row per voting (scheme, stock) with isin, scheme_id,
    amc_name, action, stock_name, industry, instrument_type, flow_lakhs and
    optionally price_effect_lakhs. ``prev_universe``: ISINs any tracked
    scheme held in the previous month, or None when that is unknown.
    ``active``: compute_active_consensus's per-stock discretionary counts.
    """
    if deltas.empty:
        return pd.DataFrame(columns=[
            "isin", "stock_name", "industry", "instrument_type",
            "schemes_buying", "schemes_selling", "net_scheme_count",
            "amcs_buying", "amcs_selling",
            "amcs_opening", "total_flow_lakhs", "new_position_flow_lakhs",
            "accumulation_flow_lakhs", "total_price_effect_lakhs",
            "net_amc_count", "buying_ratio", "universe_status",
            "is_new_to_universe", *ACTIVE_COLUMNS,
        ])

    buying = deltas[deltas["action"].isin(["new", "added"])]
    selling = deltas[deltas["action"].isin(["trimmed", "exited"])]
    opening = deltas[deltas["action"] == "new"]

    buy_counts = buying.groupby("isin")["amc_name"].nunique().rename("amcs_buying")
    sell_counts = selling.groupby("isin")["amc_name"].nunique().rename("amcs_selling")
    open_counts = opening.groupby("isin")["amc_name"].nunique().rename("amcs_opening")
    scheme_buys = buying.groupby("isin")["scheme_id"].nunique().rename("schemes_buying")
    scheme_sells = selling.groupby("isin")["scheme_id"].nunique().rename("schemes_selling")
    stock_info = deltas.drop_duplicates("isin").set_index("isin")[
        ["stock_name", "industry", "instrument_type"]]

    series_to_concat = [stock_info, buy_counts, sell_counts, open_counts,
                        scheme_buys, scheme_sells]
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
    if "price_effect_lakhs" in deltas.columns:
        price_totals = (
            deltas.groupby("isin")["price_effect_lakhs"].sum().rename("total_price_effect_lakhs")
        )
        series_to_concat.append(price_totals)

    out = pd.concat(series_to_concat, axis=1)
    # Totals always exist so ranking is stable when a legacy month recorded
    # no flow or price effect; counts are 0 where nobody bought or sold.
    for col in ("total_flow_lakhs", "total_price_effect_lakhs", "new_position_flow_lakhs"):
        out[col] = out[col].fillna(0.0) if col in out.columns else 0.0
    for col in ("amcs_buying", "amcs_selling", "amcs_opening", "schemes_buying",
                "schemes_selling"):
        out[col] = out[col].fillna(0).astype(int)
    out["stock_name"] = out["stock_name"].fillna("")
    out["net_scheme_count"] = out["schemes_buying"] - out["schemes_selling"]
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
    if prev_universe is None:
        out["universe_status"] = "unknown"
    else:
        out["universe_status"] = out["isin"].apply(
            lambda i: "established" if str(i) in prev_universe else "new_listing"
        )
    out["is_new_to_universe"] = out["universe_status"] == "new_listing"

    # Discretionary breadth alongside the raw counts. It does not change the
    # ranking below: whether it should is a question for the backtest.
    out = out.merge(active, on="isin", how="left")
    for column in ("active_amcs_buying", "active_amcs_selling", "net_active_amc_count"):
        out[column] = out[column].fillna(0).astype(int)

    # Consensus breadth still leads. Within one breadth level, established
    # names outrank fresh listings, and the tiebreak is accumulation flow —
    # never the entry value of a position that had nowhere to come from.
    return out.sort_values(
        ["net_amc_count", "is_new_to_universe", "accumulation_flow_lakhs"],
        ascending=[False, True, False],
    ).reset_index(drop=True)


def _cutoff_date(as_of_month: str | None, as_of_date) -> date | None:
    """The day whose knowledge the join may use (None = everything on file).

    An MF month is only known once its portfolios are published, so
    ``as_of_month`` means "as of that month's MF disclosure deadline" -- not
    its month end, which would admit filings nobody had seen yet.
    """
    if as_of_date is not None:
        return publication.to_date(as_of_date)
    if as_of_month is not None:
        return publication.mf_disclosure_deadline(as_of_month)
    return None


def join_shareholding_increase(
    conn: sqlite3.Connection,
    consensus: pd.DataFrame,
    as_of_month: str | None = None,
    stale_after_days: int = 200,
    as_of_date=None,
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

    No-lookahead cutoff: only filings *published* on or before the cutoff
    are ranked. The cutoff is ``as_of_date`` when given, else the MF
    disclosure deadline of ``as_of_month`` (the day that month's consensus
    became knowable). A filing's publication date is BSE's observed
    broadcast time when recorded, else its 21-day filing deadline;
    ``shareholding_published_basis`` says which.  ``staleness_days`` is
    ``<cutoff or today> - shareholding_quarter_end`` in days;
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
        "staleness_days", "shareholding_stale", "shareholding_published_basis",
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

    sh_cols = queries.table_columns(conn, "shareholding_quarterly")

    where_clauses: list[str] = [queries.quarterly_filing_sql(sh_cols)]
    params: list = []

    # When a filing became public: observed broadcast date, else the
    # regulatory deadline (legacy rows and DBs without the column).
    published_sql, basis_sql = publication.shareholding_published_sql("published_at" in sh_cols)

    cutoff = _cutoff_date(as_of_month, as_of_date)
    if cutoff is not None:
        where_clauses.append(f"{published_sql} <= ?")
        params.append(cutoff.isoformat())

    where_sql = f"WHERE {' AND '.join(where_clauses)}"
    shareholding = pd.read_sql_query(
        f"""WITH filtered AS (
               SELECT isin, quarter_end, fii_pct, dii_pct,
                      {basis_sql} AS published_basis
               FROM shareholding_quarterly
               {where_sql}
            ),
            ranked AS (
               SELECT isin, quarter_end, fii_pct, dii_pct, published_basis,
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
                   latest.dii_pct - previous.dii_pct AS dii_pct_change,
                   latest.published_basis AS shareholding_published_basis
            FROM ranked AS latest
            LEFT JOIN ranked AS previous
              ON previous.isin = latest.isin
             AND previous.quarter_rank = 2
            WHERE latest.quarter_rank = 1""",
        conn,
        params=params,
    )
    return add_shareholding_signal(consensus, shareholding,
                                   cutoff if cutoff is not None else date.today(),
                                   stale_after_days)


def add_shareholding_signal(consensus: pd.DataFrame, shareholding: pd.DataFrame,
                            reference: date, stale_after_days: int = 200) -> pd.DataFrame:
    """Join each stock's two latest public filings to the consensus. Pure.

    ``shareholding``: one row per ISIN with the latest and previous quarter's
    FII/DII (the query in join_shareholding_increase). ``reference`` is the
    day staleness is measured from. A stock with no filing keeps its row,
    with directions ``no_data`` -- never zero.
    """
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

    ref_ts = pd.Timestamp(reference.isoformat())
    q_ts = pd.to_datetime(out["shareholding_quarter_end"], errors="coerce")
    out["staleness_days"] = (ref_ts - q_ts).dt.days.astype("float")
    out["shareholding_stale"] = out["staleness_days"].isna() | (
        out["staleness_days"] > stale_after_days
    )
    return out.sort_values(
        ["is_common_with_fii_increase", "net_amc_count"],
        ascending=[False, False],
    ).reset_index(drop=True)


def ranked_consensus(
    conn: sqlite3.Connection,
    report_month: str,
    instrument_type: str | None = "equity",
    active_equity_only: bool = True,
) -> pd.DataFrame:
    """The month's ranking as shown to people: consensus plus FII/DII, in rank order.

    Stocks no fund bought or sold carry no signal and are dropped. FII/DII
    directions use only filings public when the month's portfolios were
    (see join_shareholding_increase); the join re-sorts, so rank order is
    restored. The dashboard and the monthly report both use this.
    """
    consensus = compute_consensus(conn, report_month, instrument_type, active_equity_only)
    consensus = consensus[consensus["schemes_buying"] + consensus["schemes_selling"] > 0]
    if consensus.empty:
        return consensus
    joined = join_shareholding_increase(conn, consensus, as_of_month=report_month)
    joined = joined.set_index("isin").loc[consensus["isin"]].reset_index()
    joined["shareholding_status"] = "available"
    joined.loc[joined["previous_shareholding_quarter_end"].isna(),
               "shareholding_status"] = "single_quarter"
    joined.loc[joined["shareholding_quarter_end"].isna(), "shareholding_status"] = "missing"
    return joined


def broadest_selling(ranked: pd.DataFrame) -> pd.DataFrame:
    """ranked_consensus rows in selling order: most AMCs net selling first.

    Ties go to the largest outflow from existing positions. The dashboard and
    the monthly report both use this, so their selling lists cannot differ.
    """
    return ranked.sort_values(["net_amc_count", "accumulation_flow_lakhs"])
