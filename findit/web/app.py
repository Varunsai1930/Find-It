"""Read-only web layer over precomputed holdings tables.

Every route opens SQLite in read-only mode and only reads tables that the
offline pipeline already filled. No route writes, and no route derives fresh
holdings signals at request time.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import consensus_signals
from findit.summary import get_summary

_WEB_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[2]


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




def _quarterly_predicate(conn: sqlite3.Connection, alias: str = "") -> str:
    """Quarterly-filing filter, shared with the consensus FII/DII join."""
    return consensus_signals.quarterly_filing_sql(
        _table_columns(conn, "shareholding_quarterly"), alias)


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


def _ranked_consensus(conn: sqlite3.Connection, month: str, equity_only: bool = True,
                      active_only: bool = True) -> list[dict[str, Any]]:
    """consensus_signals.ranked_consensus as JSON-ready rows.

    The same ranking the pipeline, report and backtests use, so the dashboard
    can never disagree with them, and an older month never shows filings
    published after it.
    """
    ranked = consensus_signals.ranked_consensus(
        conn, month, "equity" if equity_only else None, active_only)
    return json.loads(ranked.to_json(orient="records")) if not ranked.empty else []


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
            # Index/ETF/debt schemes track a benchmark rather than express a
            # view; active_only=0 opts back in to the wider view.
            active_filter = active_only == 1
            quarantined_schemes = sorted(
                {sid for (sid, m) in _quarantined_pairs(conn) if m == month}
            )
            ranked = _ranked_consensus(conn, month, equity_filter, active_filter)
            if not ranked:
                has_deltas = conn.execute(
                    "SELECT 1 FROM mf_holding_deltas WHERE report_month = ? LIMIT 1",
                    (month,)).fetchone() is not None
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
                            if has_deltas else
                            f"No holding-change data available for {month} yet "
                            "(data may not have been ingested for this month, "
                            "or this is the first month tracked)."
                        ),
                    }
                )
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
                    consensus_top = _ranked_consensus(conn, latest_month)[:15]
                except sqlite3.Error:
                    consensus_top = []
                if consensus_top:
                    consensus_message = ""
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
