"""Read-only web layer over precomputed holdings tables.

Every route opens SQLite in read-only mode and only reads tables that the
offline pipeline already filled. No route writes, and no route derives fresh
holdings signals at request time.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd

import consensus_signals
from findit.core import publication
from findit.summary import get_summary

_WEB_DIR = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Consensus rows per page; 0 shows every ranked stock.
_ROW_LIMITS = (25, 50, 0)
_DEFAULT_LIMIT = _ROW_LIMITS[0]
# Ranked months kept in memory; each is keyed on the DB file's signature.
_RANKED_CACHE_SIZE = 16
_ranked_cache: dict[tuple, pd.DataFrame] = {}
_ranked_lock = threading.Lock()


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
    if not Path(db_path).is_file():
        # A fresh clone has no database; say so rather than fail with a 500.
        raise HTTPException(status_code=503, detail=(
            f"No database at {db_path}. Build one with run_pipeline.py (see the README)."))
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
        published = ("published_at" if "published_at" in _table_columns(conn, "shareholding_quarterly")
                     else "NULL AS published_at")
        cur = conn.execute(
            "SELECT quarter_end, promoter_pct, fii_pct, dii_pct, public_pct, source,"
            f" {published} FROM shareholding_quarterly WHERE isin = ? AND {pred}"
            " ORDER BY quarter_end DESC",
            (isin,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    for row in rows:
        # The same rule the consensus join uses: observed broadcast, else the deadline.
        published_on, basis = publication.shareholding_published(
            row["quarter_end"], row["published_at"])
        row["published_on"], row["published_basis"] = published_on.isoformat(), basis
    return _sanitize(rows)


def _file_signature(db_path: str) -> tuple:
    """(mtime, size) of the DB and its WAL: any committed write changes it."""
    signature = []
    for path in (db_path, db_path + "-wal"):
        try:
            st = os.stat(path)
        except OSError:
            signature.append(None)
        else:
            signature.append((st.st_mtime_ns, st.st_size))
    return tuple(signature)


def _ranked(conn: sqlite3.Connection, month: str, equity_only: bool,
            active_only: bool) -> pd.DataFrame:
    """consensus_signals.ranked_consensus, cached until the DB file changes.

    The same ranking the pipeline, report and backtests use, so the dashboard
    can never disagree with them, and an older month never shows filings
    published after it. Callers must not modify the returned frame.
    """
    db_path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    key = (db_path, _file_signature(db_path), month, equity_only, active_only)
    with _ranked_lock:
        ranked = _ranked_cache.get(key)
    if ranked is None:
        ranked = consensus_signals.ranked_consensus(
            conn, month, "equity" if equity_only else None, active_only)
        with _ranked_lock:
            while len(_ranked_cache) >= _RANKED_CACHE_SIZE:
                _ranked_cache.pop(next(iter(_ranked_cache)))
            _ranked_cache[key] = ranked
    return ranked


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records")) if not frame.empty else []


def _no_signal_message(conn: sqlite3.Connection, month: str) -> str:
    """Why a month has no ranked rows: nothing moved, or nothing was compared."""
    has_deltas = conn.execute(
        "SELECT 1 FROM mf_holding_deltas WHERE report_month = ? LIMIT 1",
        (month,)).fetchone() is not None
    if has_deltas:
        return (f"No MF buying or selling signals for {month} under the current filter; "
                "stocks with no monthly change are not ranked as buys.")
    return (f"No holding-change data available for {month} yet (data may not have been "
            "ingested for this month, or this is the first month tracked).")


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else row[0]


def _month_schemes(conn: sqlite3.Connection, table: str, month: str,
                   active_only: bool) -> dict[int, str]:
    """scheme_id -> AMC for schemes with rows in table for month."""
    active = (" AND s.is_active_equity = 1"
              if active_only and "is_active_equity" in _table_columns(conn, "schemes") else "")
    try:
        rows = conn.execute(
            f"SELECT DISTINCT t.scheme_id, s.amc_name FROM {table} t"
            f" JOIN schemes s USING (scheme_id) WHERE t.report_month = ?{active}",
            (month,)).fetchall()
    except sqlite3.Error:
        return {}
    return {int(r[0]): str(r[1]) for r in rows}


def _month_statuses(conn: sqlite3.Connection, month: str) -> dict[int, str]:
    try:
        rows = conn.execute("SELECT scheme_id, status FROM scheme_month_status"
                            " WHERE report_month = ?", (month,)).fetchall()
    except sqlite3.Error:
        return {}
    return {int(r[0]): str(r[1]) for r in rows if r[0] is not None}


def _month_coverage(conn: sqlite3.Connection, month: str, equity_only: bool,
                    active_only: bool) -> dict[str, Any]:
    """Who is in month's comparison, what was withheld, and how fresh the inputs are.

    Counts cover the schemes the active filter keeps. "compared" is
    consensus_signals.voting_schemes, the exact set the ranking counts, so a
    scheme loaded without a previous month, or quarantined, is never in it.
    The ingest log does not record every load, so the last ingest is
    database-wide, never presented as this month's.
    """
    in_scope = _month_schemes(conn, "mf_holdings_monthly", month, active_only)
    validated = _has_status_table(conn)
    statuses = _month_statuses(conn, month)
    withheld = {sid for sid in in_scope if statuses.get(sid) == "quarantined"}
    compared = consensus_signals.voting_schemes(
        conn, month, "equity" if equity_only else None, active_only)
    with_deltas = _month_schemes(conn, "mf_holding_deltas", month, active_only).keys()
    no_previous = in_scope.keys() - with_deltas - withheld
    return {
        "compared": len(compared),
        "compared_amcs": len(set(compared.values())),
        "withheld": len(withheld),
        "no_previous": len(no_previous),
        # Compared, but only on holdings the equity filter leaves out.
        "no_equity": len(in_scope.keys() - compared.keys() - withheld - no_previous),
        "in_scope": len(in_scope),
        "loaded": len(_month_schemes(conn, "mf_holdings_monthly", month, False)),
        "validated": validated,
        "passed": sum(statuses.get(sid) == "ok" for sid in in_scope),
        "not_validated": sum(sid not in statuses for sid in in_scope),
        "prev_month": _scalar(conn, "SELECT MAX(prev_month) FROM mf_holding_deltas"
                                    " WHERE report_month = ?", (month,)),
        "public_on": publication.mf_disclosure_deadline(month).isoformat(),
        "last_ingest": _scalar(conn, "SELECT MAX(started_at) FROM ingest_runs"),
    }


def _month_view(conn: sqlite3.Connection, view: dict[str, Any]) -> dict[str, Any]:
    """Template context for one month: coverage facts plus the consensus table.

    The page renders it on first load and /fragments/month re-renders it when
    a filter changes, so the table markup has one implementation.
    """
    month = view["month"]
    ranked = _ranked(conn, month, bool(view["equity_only"]), bool(view["active_only"]))
    ordered = consensus_signals.broadest_selling(ranked) if view["side"] == "sell" else ranked
    rows = _records(ordered if view["limit"] == 0 else ordered.head(view["limit"]))
    status = ranked["shareholding_status"] if not ranked.empty else None
    filings = {
        "latest_quarter": None if ranked.empty else ranked["shareholding_quarter_end"].max(),
        "missing": 0 if status is None else int((status == "missing").sum()),
        "single_quarter": 0 if status is None else int((status == "single_quarter").sum()),
        "stale": 0 if ranked.empty else int(
            (ranked["shareholding_stale"] & (status != "missing")).sum()),
        "deadline_basis": 0 if ranked.empty else int(
            (ranked["shareholding_published_basis"] == publication.BASIS_DEADLINE).sum()),
    }
    return {
        **view,
        "view": view,
        "row_limits": _ROW_LIMITS,
        "total": len(ranked),
        "rows": rows,
        "message": "" if rows else _no_signal_message(conn, month),
        "coverage": _month_coverage(conn, month, bool(view["equity_only"]),
                                    bool(view["active_only"])),
        "filings": filings,
    }


def _view(month: str | None, equity_only: int, active_only: int, side: str, limit: int,
          cols: str) -> dict[str, Any]:
    """Normalised view parameters, shared by the page, the fragment and their links."""
    return {
        "month": month,
        "side": "sell" if side == "sell" else "buy",
        "limit": limit if limit in _ROW_LIMITS else _DEFAULT_LIMIT,
        # "core" is the scannable table; "all" adds discretionary net,
        # new-position flow and the FII/DII changes.
        "cols": "all" if cols == "all" else "core",
        "equity_only": int(equity_only == 1),
        "active_only": int(active_only == 1),
    }


def _crore(lakhs: Any) -> str:
    """Rs lakh -> Rs crore for display, with a typographic minus."""
    if lakhs is None:
        return "–"
    crore = float(lakhs) / 100
    text = f"{crore:,.0f}" if abs(crore) >= 10 else f"{crore:,.1f}"
    return text.replace("-", "−")


def _signed(value: Any, digits: int = 0) -> str:
    if value is None:
        return "–"
    rounded = round(float(value), digits)
    if rounded == 0:
        return f"{0:.{digits}f}"
    return f"{rounded:+,.{digits}f}".replace("-", "−")


def _month_label(iso: Any, with_day: bool = False) -> str:
    if not iso:
        return "–"
    text = str(iso)
    d = publication.month_end(text) if len(text) == 7 else publication.to_date(text[:10])
    return f"{d.day} {d:%b %Y}" if with_day else f"{d:%b %Y}"


def _scheme_title_sql(conn: sqlite3.Connection, alias: str = "sch") -> str:
    """The workbook's own scheme title where recorded (legacy DBs lack the column)."""
    if "scheme_title" in _table_columns(conn, "schemes"):
        return f"{alias}.scheme_title"
    return "NULL AS scheme_title"


def _scheme_label(scheme: dict[str, Any]) -> str:
    """'HDFC Large Cap Fund (An open ended ...)' -> 'HDFC Large Cap Fund'; else the sheet name."""
    title = (scheme.get("scheme_title") or "").split(" (")[0].strip()
    return title or str(scheme.get("scheme_name"))


def _stock_payload(conn: sqlite3.Connection, isin: str, month: str | None) -> dict[str, Any]:
    """One stock's holdings in month (default: latest) and its quarterly filings."""
    key = str(isin).strip().upper()
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
    validated = _has_status_table(conn)
    if holdings_month is not None:
        # Legacy DBs have no status table; every row is then "not_run".
        status_sql = (
            "COALESCE(m.status, 'not_validated')"
            if validated else "'not_run'")
        status_join = (
            " LEFT JOIN scheme_month_status m"
            " ON m.scheme_id = h.scheme_id AND m.report_month = h.report_month"
            if validated else "")
        try:
            cur = conn.execute(
                f"""SELECT h.scheme_id, sch.amc_name, sch.scheme_name, {_scheme_title_sql(conn)},
                          h.quantity, h.market_value_lakhs, h.pct_nav,
                          d.action, d.qty_change, d.flow_lakhs,
                          d.value_change_lakhs,
                          {status_sql} AS validation_status
                   FROM mf_holdings_monthly h
                   JOIN schemes sch ON sch.scheme_id = h.scheme_id
                   LEFT JOIN mf_holding_deltas d
                     ON d.scheme_id = h.scheme_id
                    AND d.isin = h.isin
                    AND d.report_month = h.report_month{status_join}
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
        "portfolios_public_on": (
            publication.mf_disclosure_deadline(holdings_month).isoformat()
            if holdings_month else None),
        "holdings": holdings,
        "holdings_count": len(holdings),
        # Rows the ranking leaves out: quarantined scheme-months are shown for
        # inspection only, and their actions are never validated activity.
        "withheld_count": sum(h["validation_status"] == "quarantined" for h in holdings),
        "not_validated_count": sum(h["validation_status"] == "not_validated" for h in holdings),
        "validation": "checked" if validated else "not_run",
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


def _find_stocks(conn: sqlite3.Connection, query: str, month: str | None = None,
                 limit: int = 12) -> list[dict[str, Any]]:
    """Stocks whose name contains query or whose ISIN starts with it.

    Names starting with the query come first, then the most widely held:
    schemes holding it in month when given, else in any month. Fewer than two
    searchable characters match nothing rather than everything.
    """
    text = query.strip().replace("%", "").replace("_", "")
    if len(text) < 2:
        return []
    in_month = " WHERE report_month = ?" if month else ""
    rows = conn.execute(
        "SELECT s.isin, s.name, s.industry, COALESCE(h.n, 0) AS schemes_holding FROM stocks s"
        " LEFT JOIN (SELECT isin, COUNT(DISTINCT scheme_id) AS n FROM mf_holdings_monthly"
        f"{in_month} GROUP BY isin) h USING (isin)"
        " WHERE s.name LIKE ? OR s.isin LIKE ?"
        " ORDER BY s.name LIKE ? DESC, schemes_holding DESC, s.name LIMIT ?",
        (*([month] if month else []), f"%{text}%", f"{text}%", f"{text}%", limit)).fetchall()
    return [dict(r) for r in rows]


def create_app(db_path: str | Path | None = None) -> FastAPI:
    resolved_db = _resolve_db_path(db_path)
    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    templates.env.filters.update(crore=_crore, signed=_signed, month_label=_month_label,
                                 scheme_label=_scheme_label)

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
            ranked = _records(_ranked(conn, month, equity_filter, active_filter))
            if not ranked:
                return _sanitize(
                    {
                        "month": month,
                        "equity_only": bool(equity_filter),
                        "active_equity_only": bool(active_filter),
                        "count": 0,
                        "results": [],
                        "message": _no_signal_message(conn, month),
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
        conn = _ro_connect(resolved_db)
        try:
            return _stock_payload(conn, isin, month)
        finally:
            conn.close()

    @app.get("/api/stocks/search")
    def api_stock_search(q: str = "", month: str | None = None, limit: int = 8) -> Any:
        """Name or ISIN-prefix suggestions for the stock lookup, as JSON."""
        conn = _ro_connect(resolved_db)
        try:
            if month is not None and month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            results = _find_stocks(conn, q, month, min(max(limit, 1), 25))
            return _sanitize({"query": q.strip(), "month": month, "count": len(results),
                              "results": results})
        finally:
            conn.close()

    @app.get("/fragments/stock")
    def stock_fragment(request: Request, q: str, month: str | None = None,
                       equity_only: int = 1, active_only: int = 1) -> Any:
        """A stock's detail by ISIN, or by name when q is not a known ISIN.

        The detail carries the stock's row from the month's ranking under the
        same filters as the table, so it adds the columns the table hides.
        """
        conn = _ro_connect(resolved_db)
        try:
            view = _view(month, equity_only, active_only, "buy", _DEFAULT_LIMIT, "core")
            ctx: dict[str, Any] = {"query": q.strip(), "view": view}
            try:
                stock = _stock_payload(conn, q, month)
            except HTTPException as exc:
                if not str(exc.detail).startswith("Unknown isin"):
                    raise
                matches = _find_stocks(conn, q, month)
                if len(matches) != 1:
                    ctx["matches"] = matches
                    return templates.TemplateResponse(request, "_stock.html", ctx)
                stock = _stock_payload(conn, matches[0]["isin"], month)
            ctx["stock"] = stock
            if stock["month"] is not None:
                ranked = _ranked(conn, stock["month"], bool(view["equity_only"]),
                                 bool(view["active_only"]))
                hits = _records(ranked[ranked["isin"] == stock["isin"]]) if not ranked.empty else []
                ctx["activity"] = hits[0] if hits else None
            return templates.TemplateResponse(request, "_stock.html", _sanitize(ctx))
        finally:
            conn.close()

    # -- month view fragment ---------------------------------------------
    @app.get("/fragments/month/{month}")
    def month_fragment(request: Request, month: str, equity_only: int = 1,
                       active_only: int = 1, side: str = "buy",
                       limit: int = _DEFAULT_LIMIT, cols: str = "core") -> Any:
        conn = _ro_connect(resolved_db)
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            view = _view(month, equity_only, active_only, side, limit, cols)
            ctx = _sanitize(_month_view(conn, view))
            return templates.TemplateResponse(request, "_month_view.html", ctx)
        finally:
            conn.close()

    # -- dashboard --------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request, month: str | None = None, equity_only: int = 1,
                  active_only: int = 1, side: str = "buy",
                  limit: int = _DEFAULT_LIMIT, cols: str = "core") -> Any:
        try:
            conn = _ro_connect(resolved_db)
        except HTTPException as exc:
            return templates.TemplateResponse(
                request, "dashboard.html", {"months": [], "missing_db": exc.detail},
                status_code=exc.status_code)
        try:
            try:
                months = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT DISTINCT report_month FROM mf_holdings_monthly "
                        "ORDER BY report_month DESC"
                    ).fetchall()
                    if r[0] is not None
                ]
            except sqlite3.Error:
                months = []
            if month not in months:
                month = months[0] if months else None
            try:
                schemes = [
                    dict(r) for r in conn.execute(
                        "SELECT scheme_id, amc_name, scheme_name, is_active_equity,"
                        f" {_scheme_title_sql(conn, 'schemes')} FROM schemes"
                    ).fetchall()
                ]
                schemes.sort(key=lambda sc: (sc["amc_name"], _scheme_label(sc).lower()))
            except sqlite3.Error:
                schemes = []
            # The scheme picker filters this list in the browser.
            scheme_options = [{"id": sc["scheme_id"], "label": _scheme_label(sc),
                               "amc": sc["amc_name"], "votes": sc["is_active_equity"] != 0}
                              for sc in schemes]
            view = _view(month, equity_only, active_only, side, limit, cols)
            ctx: dict[str, Any] = {**view, "view": view, "months": months,
                                   "scheme_options": scheme_options}
            if month is not None:
                ctx.update(_month_view(conn, view))
            return templates.TemplateResponse(request, "dashboard.html", _sanitize(ctx))
        finally:
            conn.close()

    return app
