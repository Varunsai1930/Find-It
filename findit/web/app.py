"""Read-only web layer over precomputed holdings tables.

Every route opens SQLite in read-only mode and only reads tables that the
offline pipeline already filled. No route writes, and no route derives fresh
holdings signals at request time.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from findit.summary import get_summary

_WEB_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[2]
BUY_ACTIONS = ("new", "added")
SELL_ACTIONS = ("trimmed", "exited")


def _sanitize(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def _resolve_db_path(db_path: str | Path | None) -> str:
    if db_path is None:
        return str((_REPO_ROOT / "tracker.db").resolve())
    return str(Path(str(db_path)).resolve())


def _ro_connect(db_path: str) -> sqlite3.Connection:
    # Read-only open: URI with mode=ro plus query_only enforcement.
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    return conn


def _known_months(conn: sqlite3.Connection) -> set[str]:
    months: set[str] = set()
    for table in ("mf_holdings_monthly", "mf_holding_deltas"):
        try:
            for (m,) in conn.execute(f"SELECT DISTINCT report_month FROM {table}"):
                if m is not None:
                    months.add(str(m))
        except sqlite3.Error:
            continue
    return months


def _latest_holdings_month(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT MAX(report_month) FROM mf_holdings_monthly").fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _prev_universe_isins(conn: sqlite3.Connection, prev_months) -> set[str] | None:
    """ISINs any tracked scheme held in the compared previous month(s).

    None when no previous month is on record, so the caller reports
    ``unknown`` instead of calling every stock a new listing.
    """
    months = sorted({str(m) for m in prev_months if m is not None})
    if not months:
        return None
    placeholders = ",".join("?" for _ in months)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT isin FROM mf_holdings_monthly "
            f"WHERE report_month IN ({placeholders})",
            months,
        ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    return {str(r[0]) for r in rows}


def _quarterly_predicate(conn: sqlite3.Connection, alias: str = "") -> str:
    """SQL fragment restricting shareholding rows to quarterly filings.

    NULL-safe for legacy rows predating filing_type: those fall back to
    calendar quarter-end matching. Interim filings are always excluded.
    """
    prefix = f"{alias}." if alias else ""
    if "filing_type" in _table_columns(conn, "shareholding_quarterly"):
        return (
            f"({prefix}filing_type = 'quarterly' OR ({prefix}filing_type IS NULL AND "
            f"substr({prefix}quarter_end,6,5) IN ('03-31','06-30','09-30','12-31')))"
        )
    return f"substr({prefix}quarter_end,6,5) IN ('03-31','06-30','09-30','12-31')"


def _quarantined_pairs(conn: sqlite3.Connection) -> set[tuple[int, str]]:
    """(scheme_id, report_month) pairs withheld pending validation.

    Empty when the v2 status table is absent (legacy DBs): nothing is
    withheld, and coverage labels the month unvalidated rather than valid.
    """
    try:
        rows = conn.execute(
            "SELECT scheme_id, report_month FROM scheme_month_status WHERE status = 'quarantined'"
        ).fetchall()
    except sqlite3.Error:
        return set()
    return {(int(r[0]), str(r[1])) for r in rows if r[0] is not None and r[1] is not None}


def _has_status_table(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1 FROM scheme_month_status LIMIT 1").fetchall()
    except sqlite3.Error:
        return False
    return True


def _direction(change: Any) -> str:
    try:
        if change is None or (isinstance(change, float) and not math.isfinite(change)):
            return "no_data"
        v = float(change)
    except (TypeError, ValueError):
        return "no_data"
    if math.isnan(v):
        return "no_data"
    return "increased" if v > 0 else "decreased"


def _shareholding_rows(conn: sqlite3.Connection, isin: str) -> list[dict[str, Any]]:
    try:
        pred = _quarterly_predicate(conn)
        cur = conn.execute(
            "SELECT quarter_end, promoter_pct, fii_pct, dii_pct, public_pct, source"
            f" FROM shareholding_quarterly WHERE isin = ? AND {pred}"
            " ORDER BY quarter_end DESC",
            (isin,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    return _sanitize(rows)


def _shareholding_summary(conn: sqlite3.Connection, isins: list[str]) -> dict[str, dict[str, Any]]:
    if not isins:
        return {}
    placeholders = ",".join("?" for _ in isins)
    try:
        pred = _quarterly_predicate(conn)
        cur = conn.execute(
            f"""SELECT isin, quarter_end, fii_pct, dii_pct
                FROM shareholding_quarterly WHERE isin IN ({placeholders})
                AND {pred}
                ORDER BY isin, quarter_end DESC""",
            isins,
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in cur.fetchall():
            grouped.setdefault(str(r["isin"]), []).append(dict(r))
    except sqlite3.Error:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for isin in isins:
        quarters = grouped.get(isin, [])
        if not quarters:
            out[isin] = {
                "status": "missing",
                "latest_quarter": None,
                "previous_quarter": None,
                "fii_pct": None,
                "dii_pct": None,
                "fii_pct_change": None,
                "dii_pct_change": None,
                "fii_direction": "no_data",
                "dii_direction": "no_data",
                "fii_increased": False,
                "dii_increased": False,
            }
        elif len(quarters) == 1:
            q = quarters[0]
            out[isin] = {
                "status": "single_quarter",
                "latest_quarter": q["quarter_end"],
                "previous_quarter": None,
                "fii_pct": q["fii_pct"],
                "dii_pct": q["dii_pct"],
                "fii_pct_change": None,
                "dii_pct_change": None,
                "fii_direction": "no_data",
                "dii_direction": "no_data",
                "fii_increased": False,
                "dii_increased": False,
            }
        else:
            latest, prev = quarters[0], quarters[1]
            fii_ch = None
            dii_ch = None
            try:
                if latest["fii_pct"] is not None and prev["fii_pct"] is not None:
                    fii_ch = float(latest["fii_pct"]) - float(prev["fii_pct"])
                if latest["dii_pct"] is not None and prev["dii_pct"] is not None:
                    dii_ch = float(latest["dii_pct"]) - float(prev["dii_pct"])
            except (TypeError, ValueError):
                pass
            out[isin] = {
                "status": "available",
                "latest_quarter": latest["quarter_end"],
                "previous_quarter": prev["quarter_end"],
                "fii_pct": latest["fii_pct"],
                "dii_pct": latest["dii_pct"],
                "fii_pct_change": fii_ch,
                "dii_pct_change": dii_ch,
                "fii_direction": _direction(fii_ch),
                "dii_direction": _direction(dii_ch),
                "fii_increased": bool(fii_ch is not None and fii_ch > 0),
                "dii_increased": bool(dii_ch is not None and dii_ch > 0),
            }
    return _sanitize(out)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    resolved_db = _resolve_db_path(db_path)
    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

    app = FastAPI(title="FindIt holdings dashboard (read-only)")

    app.mount(
        "/static",
        StaticFiles(directory=str(_WEB_DIR / "static")),
        name="static",
    )

    # -- coverage ---------------------------------------------------------
    @app.get("/api/coverage")
    def api_coverage() -> Any:
        conn = _ro_connect(resolved_db)
        try:
            try:
                months = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT report_month FROM mf_holdings_monthly "
                        "ORDER BY report_month"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                months = []
            scheme_counts: dict[str, int] = {}
            holdings_counts: dict[str, int] = {}
            for m in months:
                try:
                    sc = conn.execute(
                        "SELECT COUNT(DISTINCT scheme_id) FROM mf_holdings_monthly "
                        "WHERE report_month = ?",
                        (m,),
                    ).fetchone()
                    hc = conn.execute(
                        "SELECT COUNT(*) FROM mf_holdings_monthly WHERE report_month = ?",
                        (m,),
                    ).fetchone()
                except sqlite3.Error:
                    continue
                scheme_counts[m] = int(sc[0] or 0)
                holdings_counts[m] = int(hc[0] or 0)
            try:
                delta_months = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT report_month FROM mf_holding_deltas "
                        "ORDER BY report_month"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                delta_months = []
            try:
                quarters = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT quarter_end FROM shareholding_quarterly "
                        "ORDER BY quarter_end"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                quarters = []
            latest_month = months[-1] if months else None
            latest_quarter = quarters[-1] if quarters else None
            per_month = [
                {
                    "month": m,
                    "schemes": scheme_counts.get(m, 0),
                    "holdings": holdings_counts.get(m, 0),
                    "has_deltas": m in set(delta_months),
                }
                for m in months
            ]
            quarantined = [
                {"scheme_id": sid, "report_month": m}
                for (sid, m) in sorted(_quarantined_pairs(conn))
            ]
            payload = {
                "months": months,
                "latest_month": latest_month,
                "scheme_counts": scheme_counts,
                "holdings_counts": holdings_counts,
                "per_month": per_month,
                "delta_months": delta_months,
                "shareholding_quarters": quarters,
                "latest_quarter": latest_quarter,
                "quarantined": quarantined,
                "validation_coverage": (
                    "validated" if _has_status_table(conn) else "unvalidated"
                ),
            }
            if not months:
                payload["message"] = "No coverage data available yet."
            return _sanitize(payload)
        finally:
            conn.close()

    # -- schemes ----------------------------------------------------------
    @app.get("/api/schemes")
    def api_schemes(month: str | None = None) -> Any:
        conn = _ro_connect(resolved_db)
        try:
            if month is not None:
                if month not in _known_months(conn):
                    raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
                rows = conn.execute(
                    """SELECT DISTINCT s.scheme_id, s.amc_name, s.scheme_name
                       FROM schemes s
                       JOIN mf_holdings_monthly h ON h.scheme_id = s.scheme_id
                       WHERE h.report_month = ?
                       ORDER BY s.amc_name, s.scheme_name""",
                    (month,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT scheme_id, amc_name, scheme_name FROM schemes "
                    "ORDER BY amc_name, scheme_name"
                ).fetchall()
            schemes = [
                {
                    "scheme_id": int(r["scheme_id"]),
                    "amc_name": r["amc_name"],
                    "scheme_name": r["scheme_name"],
                }
                for r in rows
            ]
            return _sanitize({"month": month, "count": len(schemes), "schemes": schemes})
        finally:
            conn.close()

    # -- consensus --------------------------------------------------------
    @app.get("/api/consensus/{month}")
    def api_consensus(month: str, equity_only: int = 1, active_only: int = 1) -> Any:
        conn = _ro_connect(resolved_db)
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            equity_filter = equity_only == 1
            # Match compute_consensus: index/ETF/debt schemes track a
            # benchmark rather than express a view, so counting them as
            # conviction made the dashboard and the CLI disagree about the
            # same month. active_only=0 opts back in to the wider view.
            active_filter = active_only == 1
            quarantined_schemes = sorted(
                {sid for (sid, m) in _quarantined_pairs(conn) if m == month}
            )
            # price_effect_lakhs is added PRAGMA-guarded by Worker A; legacy
            # DBs lack it, so detect upfront and fall back to a price-less
            # SELECT rather than 500ing.
            has_price = "price_effect_lakhs" in _table_columns(conn, "mf_holding_deltas")

            def _consensus_sql(include_price: bool) -> tuple[str, list[Any]]:
                price_select = (
                    "d.price_effect_lakhs AS price_effect_lakhs, "
                    if include_price
                    else ""
                )
                sql = (
                    "SELECT d.isin AS isin, s.name AS stock_name, s.industry AS industry, "
                    "s.instrument_type AS instrument_type, d.scheme_id AS scheme_id, "
                    "sch.amc_name AS amc_name, d.action AS action, "
                    "d.qty_change AS qty_change, d.value_change_lakhs AS value_change_lakhs, "
                    f"{price_select}"
                    "d.flow_lakhs AS flow_lakhs, d.prev_month AS prev_month "
                    "FROM mf_holding_deltas d "
                    "JOIN stocks s ON s.isin = d.isin "
                    "JOIN schemes sch ON sch.scheme_id = d.scheme_id "
                    "WHERE d.report_month = ?"
                )
                params: list[Any] = [month]
                if equity_filter:
                    sql += " AND s.instrument_type = ?"
                    params.append("equity")
                if active_filter and "is_active_equity" in _table_columns(conn, "schemes"):
                    sql += " AND sch.is_active_equity = 1"
                if quarantined_schemes:
                    placeholders = ",".join("?" for _ in quarantined_schemes)
                    sql += f" AND d.scheme_id NOT IN ({placeholders})"
                    params.extend(quarantined_schemes)
                return sql, params

            sql, params = _consensus_sql(has_price)
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                if has_price and "price_effect" in str(exc).lower():
                    has_price = False
                    sql, params = _consensus_sql(False)
                    rows = conn.execute(sql, params).fetchall()
                else:
                    raise
            if not rows:
                return _sanitize(
                    {
                        "month": month,
                        "equity_only": bool(equity_filter),
                        "active_equity_only": bool(active_filter),
                        "count": 0,
                        "results": [],
                        "message": (
                            f"No holding-change data available for {month} yet "
                            "(data may not have been ingested for this month, "
                            "or this is the first month tracked)."
                        ),
                    }
                )
            grouped: dict[str, dict[str, Any]] = {}
            for r in rows:
                isin = str(r["isin"])
                g = grouped.setdefault(
                    isin,
                    {
                        "isin": isin,
                        "stock_name": r["stock_name"],
                        "industry": r["industry"],
                        "instrument_type": r["instrument_type"],
                        "buy_schemes": set(),
                        "sell_schemes": set(),
                        "buy_amcs": set(),
                        "sell_amcs": set(),
                        "total_flow_lakhs": 0.0,
                        "new_position_flow_lakhs": 0.0,
                        "total_price_effect_lakhs": 0.0,
                        "open_amcs": set(),
                    },
                )
                action = str(r["action"])
                try:
                    flow = float(r["flow_lakhs"]) if r["flow_lakhs"] is not None else 0.0
                except (TypeError, ValueError):
                    flow = 0.0
                try:
                    price = (
                        float(r["price_effect_lakhs"])
                        if (has_price and r["price_effect_lakhs"] is not None)
                        else 0.0
                    )
                except (TypeError, ValueError, IndexError, KeyError):
                    price = 0.0
                if not math.isfinite(flow):
                    flow = 0.0
                if not math.isfinite(price):
                    price = 0.0
                if action in BUY_ACTIONS:
                    g["buy_schemes"].add(int(r["scheme_id"]))
                    g["buy_amcs"].add(str(r["amc_name"]))
                    g["total_flow_lakhs"] += flow
                    g["total_price_effect_lakhs"] += price
                    # A new position books its whole market value as flow;
                    # keep it separate so entry value never reads as buying
                    # pressure (mirrors consensus_signals.compute_consensus).
                    if action == "new":
                        g["open_amcs"].add(str(r["amc_name"]))
                        g["new_position_flow_lakhs"] += flow
                elif action in SELL_ACTIONS:
                    g["sell_schemes"].add(int(r["scheme_id"]))
                    g["sell_amcs"].add(str(r["amc_name"]))
                    g["total_flow_lakhs"] += flow
                    g["total_price_effect_lakhs"] += price
                # 'unchanged' rows contribute no signal and are skipped.
            # Exclude no-signal names: only names with at least one buy or sell.
            ranked: list[dict[str, Any]] = []
            for g in grouped.values():
                nb = len(g["buy_schemes"])
                ns = len(g["sell_schemes"])
                if nb + ns == 0:
                    continue
                ab = len(g["buy_amcs"])
                as_ = len(g["sell_amcs"])
                denom = nb + ns
                buying_ratio = (nb / denom) if denom else 0.0
                ranked.append(
                    {
                        "isin": g["isin"],
                        "stock_name": g["stock_name"],
                        "industry": g["industry"],
                        "instrument_type": g["instrument_type"],
                        "schemes_buying": nb,
                        "schemes_selling": ns,
                        "amcs_buying": ab,
                        "amcs_selling": as_,
                        "net_scheme_count": nb - ns,
                        "net_amc_count": ab - as_,
                        "buying_ratio": buying_ratio,
                        "total_flow_lakhs": g["total_flow_lakhs"],
                        "new_position_flow_lakhs": g["new_position_flow_lakhs"],
                        "accumulation_flow_lakhs": (
                            g["total_flow_lakhs"] - g["new_position_flow_lakhs"]
                        ),
                        "amcs_opening": len(g["open_amcs"]),
                        "total_price_effect_lakhs": g["total_price_effect_lakhs"],
                    }
                )
            if not ranked:
                return _sanitize(
                    {
                        "month": month,
                        "equity_only": bool(equity_filter),
                        "active_equity_only": bool(active_filter),
                        "count": 0,
                        "results": [],
                        "message": (
                            f"No MF buying or selling signals for {month} "
                            "under the current filter; stocks with no "
                            "monthly change are not ranked as buys."
                        ),
                    }
                )
            universe = _prev_universe_isins(
                conn, [r["prev_month"] for r in rows]
            )
            for r in ranked:
                if universe is None:
                    r["universe_status"] = "unknown"
                else:
                    r["universe_status"] = (
                        "established" if r["isin"] in universe else "new_listing"
                    )
                r["is_new_to_universe"] = r["universe_status"] == "new_listing"
            # Breadth first; then established names ahead of fresh listings;
            # then accumulation flow, never a new position's entry value.
            ranked.sort(
                key=lambda x: (
                    x["net_amc_count"],
                    x["net_scheme_count"],
                    not x["is_new_to_universe"],
                    x["accumulation_flow_lakhs"],
                ),
                reverse=True,
            )
            summaries = _shareholding_summary(conn, [r["isin"] for r in ranked])
            for r in ranked:
                s = summaries.get(r["isin"], {})
                r["shareholding_status"] = s.get("status", "missing")
                r["shareholding_quarter_end"] = s.get("latest_quarter")
                r["previous_shareholding_quarter_end"] = s.get("previous_quarter")
                r["fii_pct"] = s.get("fii_pct")
                r["dii_pct"] = s.get("dii_pct")
                r["fii_direction"] = s.get("fii_direction", "no_data")
                r["dii_direction"] = s.get("dii_direction", "no_data")
                r["fii_pct_change"] = s.get("fii_pct_change")
                r["dii_pct_change"] = s.get("dii_pct_change")
                r["fii_increased"] = bool(s.get("fii_increased", False))
                r["dii_increased"] = bool(s.get("dii_increased", False))
            payload: dict[str, Any] = {
                "month": month,
                "equity_only": bool(equity_filter),
                "active_equity_only": bool(active_filter),
                "count": len(ranked),
                "results": ranked,
            }
            if quarantined_schemes:
                payload["quarantined_schemes"] = quarantined_schemes
                payload["message"] = (
                    f"{len(quarantined_schemes)} scheme(s) withheld pending"
                    " validation (not zero)."
                )
            return _sanitize(payload)
        finally:
            conn.close()

    # -- summary ----------------------------------------------------------
    @app.get("/api/summary/{scheme_id}/{month}")
    def api_summary(scheme_id: int, month: str) -> Any:
        conn = _ro_connect(resolved_db)
        try:
            row = conn.execute(
                "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?",
                (scheme_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown scheme_id: {scheme_id}"
                )
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            if (int(scheme_id), month) in _quarantined_pairs(conn):
                return _sanitize(
                    {
                        "scheme_id": int(scheme_id),
                        "report_month": month,
                        "amc_name": row["amc_name"],
                        "scheme_name": row["scheme_name"],
                        "summary": (
                            f"Data for {row['scheme_name']} in {month} withheld"
                            " pending validation (not zero)."
                        ),
                        "has_data": False,
                        "quarantined": True,
                    }
                )
            try:
                summary_result = get_summary(conn, scheme_id, month)
                text = summary_result["text"]
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            blocked = summary_result["reason"] in {"quarantined", "nonfinite_or_invalid_data"}
            has_data = not blocked and not str(text).startswith("No holding-change data available")
            return _sanitize(
                {
                    "scheme_id": int(scheme_id),
                    "report_month": month,
                    "amc_name": row["amc_name"],
                    "scheme_name": row["scheme_name"],
                    "summary": str(text),
                    "has_data": bool(has_data),
                    "generated_by": summary_result["generated_by"],
                    "model_version": summary_result["model_version"],
                    "cached": summary_result["cached"],
                    "summary_status": summary_result["reason"] or "available",
                }
            )
        finally:
            conn.close()

    # -- stock ------------------------------------------------------------
    @app.get("/api/stock/{isin}")
    def api_stock(isin: str, month: str | None = None) -> Any:
        key = str(isin).strip().upper()
        conn = _ro_connect(resolved_db)
        try:
            stock = conn.execute(
                "SELECT isin, name, industry, instrument_type FROM stocks WHERE isin = ?",
                (key,),
            ).fetchone()
            if stock is None:
                # Retry case-insensitive for robustness.
                stock = conn.execute(
                    "SELECT isin, name, industry, instrument_type FROM stocks "
                    "WHERE UPPER(isin) = ?",
                    (key,),
                ).fetchone()
            if stock is None:
                raise HTTPException(status_code=404, detail=f"Unknown isin: {isin}")
            if month is not None and month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            holdings_month = month or _latest_holdings_month(conn)
            holdings: list[dict[str, Any]] = []
            if holdings_month is not None:
                try:
                    cur = conn.execute(
                        """SELECT h.scheme_id, sch.amc_name, sch.scheme_name,
                                  h.quantity, h.market_value_lakhs, h.pct_nav,
                                  d.action, d.qty_change, d.flow_lakhs,
                                  d.value_change_lakhs
                           FROM mf_holdings_monthly h
                           JOIN schemes sch ON sch.scheme_id = h.scheme_id
                           LEFT JOIN mf_holding_deltas d
                             ON d.scheme_id = h.scheme_id
                            AND d.isin = h.isin
                            AND d.report_month = h.report_month
                           WHERE h.isin = ? AND h.report_month = ?
                           ORDER BY h.market_value_lakhs DESC""",
                        (stock["isin"], holdings_month),
                    )
                    holdings = [_sanitize(dict(r)) for r in cur.fetchall()]
                except sqlite3.Error:
                    holdings = []
            quarters = _shareholding_rows(conn, str(stock["isin"]))
            status = "available" if quarters else "missing"
            payload: dict[str, Any] = {
                "isin": stock["isin"],
                "name": stock["name"],
                "industry": stock["industry"],
                "instrument_type": stock["instrument_type"],
                "month": holdings_month,
                "holdings": holdings,
                "holdings_count": len(holdings),
                "shareholding": quarters,
                "shareholding_status": status,
            }
            if not holdings:
                payload["holdings_message"] = (
                    f"No holdings found for {stock['isin']} in {holdings_month}."
                )
            if not quarters:
                payload["shareholding_message"] = (
                    "No shareholding filing available for this stock "
                    "(missing filing, not zero)."
                )
            return _sanitize(payload)
        finally:
            conn.close()

    # -- dashboard --------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request) -> Any:
        conn = _ro_connect(resolved_db)
        try:
            try:
                months = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT report_month FROM mf_holdings_monthly "
                        "ORDER BY report_month"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                months = []
            latest_month = months[-1] if months else None
            scheme_counts: dict[str, int] = {}
            for m in months:
                try:
                    sc = conn.execute(
                        "SELECT COUNT(DISTINCT scheme_id) FROM mf_holdings_monthly "
                        "WHERE report_month = ?",
                        (m,),
                    ).fetchone()
                    scheme_counts[m] = int(sc[0] or 0)
                except sqlite3.Error:
                    scheme_counts[m] = 0
            try:
                delta_months = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT report_month FROM mf_holding_deltas "
                        "ORDER BY report_month"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                delta_months = []
            try:
                quarters = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT quarter_end FROM shareholding_quarterly "
                        "ORDER BY quarter_end"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                quarters = []
            latest_quarter = quarters[-1] if quarters else None
            try:
                schemes = [
                    {
                        "scheme_id": int(r["scheme_id"]),
                        "amc_name": r["amc_name"],
                        "scheme_name": r["scheme_name"],
                    }
                    for r in conn.execute(
                        "SELECT scheme_id, amc_name, scheme_name FROM schemes "
                        "ORDER BY amc_name, scheme_name LIMIT 500"
                    ).fetchall()
                ]
            except sqlite3.Error:
                schemes = []
            consensus_top: list[dict[str, Any]] = []
            consensus_message = "No consensus data available yet."
            if latest_month is not None:
                try:
                    has_price = "price_effect_lakhs" in _table_columns(
                        conn, "mf_holding_deltas"
                    )
                    price_select = (
                        ", d.price_effect_lakhs AS price_effect_lakhs"
                        if has_price
                        else ""
                    )
                    dashboard_sql = (
                        "SELECT d.isin AS isin, s.name AS stock_name, "
                        "d.scheme_id AS scheme_id, sch.amc_name AS amc_name, "
                        "d.action AS action, "
                        f"d.flow_lakhs AS flow_lakhs{price_select}, "
                        "d.value_change_lakhs AS value_change_lakhs "
                        "FROM mf_holding_deltas d "
                        "JOIN stocks s ON s.isin = d.isin "
                        "JOIN schemes sch ON sch.scheme_id = d.scheme_id "
                        "WHERE d.report_month = ? AND s.instrument_type = 'equity'"
                    )
                    try:
                        rows = conn.execute(
                            dashboard_sql, (latest_month,)
                        ).fetchall()
                    except sqlite3.Error as exc:
                        # Legacy DBs predate price_effect_lakhs: retry without it.
                        if has_price and "price_effect" in str(exc).lower():
                            has_price = False
                            dashboard_sql = dashboard_sql.replace(
                                ", d.price_effect_lakhs AS price_effect_lakhs", ""
                            )
                            rows = conn.execute(
                                dashboard_sql, (latest_month,)
                            ).fetchall()
                        else:
                            raise
                    agg: dict[str, dict[str, Any]] = {}
                    for r in rows:
                        isin = str(r["isin"])
                        g = agg.setdefault(
                            isin,
                            {
                                "isin": isin,
                                "stock_name": r["stock_name"],
                                "buy_s": set(),
                                "sell_s": set(),
                                "buy_a": set(),
                                "sell_a": set(),
                                "flow": 0.0,
                                "price": 0.0,
                            },
                        )
                        act = str(r["action"])
                        try:
                            fl = (
                                float(r["flow_lakhs"])
                                if r["flow_lakhs"] is not None
                                else 0.0
                            )
                        except (TypeError, ValueError):
                            fl = 0.0
                        if not math.isfinite(fl):
                            fl = 0.0
                        try:
                            pr = (
                                float(r["price_effect_lakhs"])
                                if (has_price and r["price_effect_lakhs"] is not None)
                                else 0.0
                            )
                        except (TypeError, ValueError, IndexError, KeyError):
                            pr = 0.0
                        if not math.isfinite(pr):
                            pr = 0.0
                        if act in BUY_ACTIONS:
                            g["buy_s"].add(int(r["scheme_id"]))
                            g["buy_a"].add(str(r["amc_name"]))
                            g["flow"] += fl
                            g["price"] += pr
                        elif act in SELL_ACTIONS:
                            g["sell_s"].add(int(r["scheme_id"]))
                            g["sell_a"].add(str(r["amc_name"]))
                            g["flow"] += fl
                            g["price"] += pr
                    ranked_all = []
                    for g in agg.values():
                        nb, ns = len(g["buy_s"]), len(g["sell_s"])
                        if nb + ns == 0:
                            continue
                        ab, as_ = len(g["buy_a"]), len(g["sell_a"])
                        denom = nb + ns
                        ranked_all.append(
                            {
                                "isin": g["isin"],
                                "stock_name": g["stock_name"],
                                "schemes_buying": nb,
                                "schemes_selling": ns,
                                "amcs_buying": ab,
                                "amcs_selling": as_,
                                "net_amc_count": ab - as_,
                                "net_scheme_count": nb - ns,
                                "buying_ratio": (nb / denom) if denom else 0.0,
                                "total_flow_lakhs": g["flow"],
                                "total_price_effect_lakhs": g["price"],
                            }
                        )
                    ranked_all.sort(
                        key=lambda x: (
                            x["net_amc_count"],
                            x["net_scheme_count"],
                            x["total_flow_lakhs"],
                        ),
                        reverse=True,
                    )
                    consensus_top = _sanitize(ranked_all[:15])
                    if consensus_top:
                        sh_top = _shareholding_summary(
                            conn, [r["isin"] for r in consensus_top]
                        )
                        for r in consensus_top:
                            s = sh_top.get(r["isin"], {})
                            r["shareholding_status"] = s.get("status", "missing")
                            r["shareholding_quarter_end"] = s.get("latest_quarter")
                            r["fii_direction"] = s.get("fii_direction", "no_data")
                            r["dii_direction"] = s.get("dii_direction", "no_data")
                        consensus_message = ""
                except sqlite3.Error:
                    consensus_top = []
            ctx = _sanitize(
                {
                    "months": months,
                    "latest_month": latest_month,
                    "scheme_counts": scheme_counts,
                    "delta_months": delta_months,
                    "latest_quarter": latest_quarter,
                    "quarters": quarters,
                    "schemes": schemes,
                    "consensus_top": consensus_top,
                    "consensus_message": consensus_message,
                }
            )
            return templates.TemplateResponse(request, "dashboard.html", ctx)
        finally:
            conn.close()

    return app
