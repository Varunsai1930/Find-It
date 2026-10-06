"""Read-only web layer over precomputed holdings tables.

Every route opens SQLite in read-only mode and only reads tables that the
offline pipeline already filled. The web layer ranks and summarizes those
stored monthly comparisons; no route writes or ingests new disclosures.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from urllib.parse import urlencode
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path as PathParam, Request
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd

from findit.core import consensus_signals, publication
from findit.core.coverage import house_coverage, month_coverage
from findit.core.evidence import stock_evidence
from findit.core.history import stock_history, history_evidence
from findit.core.replay import comparison_for_rules, LegacyReplayError, previous_month
from findit.core.data_quality import bad_domestic_quantity_pairs
from findit.store.releases import ReleaseStore, RULE_VERSION
from findit.core.watchlist import monthly_report, parse_stocks, render_report
from findit.store import queries
from findit.summary import get_summary
from findit.web.fund_houses import AUM_PERIOD, AUM_SOURCE, selected_groups

_WEB_DIR = Path(__file__).resolve().parent

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
    path = db_path if db_path is not None else os.environ.get("FINDIT_DB", "tracker.db")
    return str(Path(path).expanduser().resolve())


def _stock_label(name: str | None) -> str:
    """Remove the disclosure's equity marker, preserving the company's name."""
    return re.sub(r"^EQ\s*-\s*", "", name or "", flags=re.IGNORECASE).strip()


def _ro_connect(db_path: str, release_store: ReleaseStore | None = None) -> sqlite3.Connection:
    # Encode URI-sensitive filename characters, and never create a missing DB.
    if not Path(db_path).is_file():
        raise HTTPException(status_code=503, detail=(
            f"No database at {db_path}. Build one with `python3 -m findit.cli.pipeline` "
            "(see the README), or set FINDIT_DB to your existing database."))
    conn = None
    try:
        if release_store is not None and re.fullmatch(r"[a-f0-9]{64}", Path(db_path).stem):
            try:
                conn, _ = release_store.connect()
            except (ValueError, OSError, sqlite3.Error) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            conn = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True,
                                   check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON;")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"schemes", "stocks", "mf_holdings_monthly", "mf_holding_deltas"} <= tables:
            raise sqlite3.DatabaseError("missing tracker tables")
        conn.create_function("stock_label", 1, _stock_label, deterministic=True)
        return conn
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        raise HTTPException(status_code=503, detail=(
            "The holdings database could not be read. Check FINDIT_DB points to a FindIt "
            "database built by the pipeline (see the README).")) from exc


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


def _shareholding_rows(conn: sqlite3.Connection, isin: str) -> list[dict[str, Any]]:
    try:
        columns = queries.table_columns(conn, "shareholding_quarterly")
        pred = queries.quarterly_filing_sql(columns)
        published = "published_at" if "published_at" in columns else "NULL AS published_at"
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
    key = (db_path, _file_signature(db_path), RULE_VERSION, month, equity_only, active_only)
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


def _month_overview(conn, month, active_only):
    activity = consensus_signals.fund_house_activity(conn, month, active_only)
    fund_groups = selected_groups(activity)
    for group in fund_groups:
        for result in group["funds"]:
            result.update(house_coverage(conn, month, result["amc"],
                                         set(result.get("compared_ids", [])), active_only))
    return {"fund_groups": fund_groups, "summary_prev_month": activity["prev_month"]}


def _month_view(conn: sqlite3.Connection, view: dict[str, Any], *,
                overview=None, coverage=None) -> dict[str, Any]:
    """One month's overview and consensus; shared by pages and fragments."""
    month = view["month"]
    overview = overview if overview is not None else _month_overview(conn, month, bool(view["active_only"]))
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
        "coverage": coverage if coverage is not None else month_coverage(
            conn, month, bool(view["equity_only"]), bool(view["active_only"])),
        "filings": filings,
        **overview,
        "aum_period": AUM_PERIOD,
        "aum_source": AUM_SOURCE,
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


def _compact_shares(value: Any) -> str:
    """Rounded display only; exact quantities remain in the expanded card."""
    if value is None or not math.isfinite(float(value)):
        return "–"
    number = float(value)
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= scale or round(abs(number) / (scale / 1000), 1) >= 1000:
            text = f"{abs(number) / scale:.1f}".removesuffix(".0")
            return ("+" if number > 0 else "−") + text + suffix
    return _signed(number)


def _month_label(iso: Any, with_day: bool = False) -> str:
    if not iso:
        return "–"
    text = str(iso)
    d = publication.month_end(text) if len(text) == 7 else publication.to_date(text[:10])
    return f"{d.day} {d:%b %Y}" if with_day else f"{d:%b %Y}"


def _scheme_title_sql(conn: sqlite3.Connection, alias: str = "sch") -> str:
    """The workbook's own scheme title where recorded (legacy DBs lack the column)."""
    if "scheme_title" in queries.table_columns(conn, "schemes"):
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
    validated = queries.has_status_table(conn)
    if holdings_month is not None:
        # Legacy DBs have no status table; every row is then "not_run".
        status_sql = (
            "COALESCE(m.status, 'not_validated')"
            if validated else "'not_run'")
        status_join = (
            " LEFT JOIN scheme_month_status m"
            " ON m.scheme_id = h.scheme_id AND m.report_month = h.report_month"
            if validated else "")
        if validated:
            status_join += (" LEFT JOIN scheme_month_status prior"
                            " ON prior.scheme_id=h.scheme_id AND prior.report_month=d.prev_month")
        prior_status = "COALESCE(prior.status, 'not_validated')" if validated else "'not_run'"
        try:
            cur = conn.execute(
                f"""SELECT h.scheme_id, sch.amc_name, sch.scheme_name, {_scheme_title_sql(conn)},
                          h.quantity, h.market_value_lakhs, h.pct_nav,
                          d.action, d.qty_change, d.flow_lakhs,
                          d.value_change_lakhs,
                          {status_sql} AS validation_status,
                          {prior_status} AS previous_validation_status, d.prev_month, p.scheme_id AS previous_holding_scheme_id,
                          p.market_value_lakhs AS previous_market_value_lakhs
                   FROM mf_holdings_monthly h
                   JOIN schemes sch ON sch.scheme_id = h.scheme_id
                   LEFT JOIN mf_holding_deltas d
                     ON d.scheme_id = h.scheme_id
                    AND d.isin = h.isin
                    AND d.report_month = h.report_month{status_join}
                   LEFT JOIN mf_holdings_monthly p ON p.scheme_id=h.scheme_id
                    AND p.isin=h.isin AND p.report_month=d.prev_month
                   WHERE h.isin = ? AND h.report_month = ?
                   ORDER BY h.market_value_lakhs DESC""",
                (stock["isin"], holdings_month),
            )
            holdings = [_sanitize(dict(r)) for r in cur.fetchall()]
            bad_pairs = bad_domestic_quantity_pairs(conn)
            expected_previous = previous_month(holdings_month)
            for holding in holdings:
                sid, previous = holding["scheme_id"], holding["prev_month"]
                statuses = (holding["validation_status"], holding["previous_validation_status"])
                reason = None
                state = "available"
                if "quarantined" in statuses:
                    state, reason = "withheld", "Either snapshot failed validation."
                elif (sid, holdings_month) in bad_pairs or (sid, previous) in bad_pairs:
                    state, reason = "withheld", "Domestic share quantities are unavailable or invalid."
                elif previous != expected_previous:
                    state, reason = "unavailable", "No adjacent previous-month comparison is available."
                elif not conn.execute("SELECT 1 FROM mf_holdings_monthly WHERE scheme_id=? AND report_month=? LIMIT 1", (sid, previous)).fetchone():
                    state, reason = "unavailable", "The previous snapshot is unavailable."
                elif validated and any(v != "ok" for v in statuses):
                    state, reason = "not_validated", "Both snapshots must pass validation."
                elif holding["action"] is None:
                    state, reason = "unavailable", "No previous-month comparison is available."
                elif not validated:
                    state = "not_run"
                values = [holding["market_value_lakhs"]]
                if holding["previous_holding_scheme_id"] is not None:
                    values.append(holding["previous_market_value_lakhs"])
                if any(v is None or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
                    holding["flow_lakhs"] = holding["value_change_lakhs"] = None
                del holding["previous_holding_scheme_id"]
                del holding["previous_market_value_lakhs"]
                holding.update(comparison_status=state, comparison_reason=reason)
                if state in {"withheld", "not_validated", "unavailable"}:
                    for field in ("action", "qty_change", "flow_lakhs", "value_change_lakhs"):
                        holding[field] = None
        except sqlite3.Error:
            holdings = []
    quarters = _shareholding_rows(conn, str(stock["isin"]))
    status = "available" if quarters else "missing"
    payload: dict[str, Any] = {
        "isin": stock["isin"],
        "name": _stock_label(stock["name"]),
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
        "withheld_count": sum(h["comparison_status"] == "withheld" for h in holdings),
        "not_validated_count": sum(h["comparison_status"] == "not_validated" for h in holdings),
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

    Names starting with the query come first (ignoring disclosure equity markers),
    with equities before other instruments, then the most widely held:
    schemes holding it in month when given, else in any month. Fewer than two
    searchable characters match nothing rather than everything.
    """
    text = query.strip().replace("%", "").replace("_", "")
    if len(text) < 2:
        return []
    in_month = " WHERE report_month = ?" if month else ""
    rows = conn.execute(
        "SELECT s.isin, stock_label(s.name) AS name, s.industry,"
        " COALESCE(h.n, 0) AS schemes_holding FROM stocks s"
        " LEFT JOIN (SELECT isin, COUNT(DISTINCT scheme_id) AS n FROM mf_holdings_monthly"
        f"{in_month} GROUP BY isin) h USING (isin)"
        " WHERE s.name LIKE ? OR s.isin LIKE ?"
        " ORDER BY stock_label(s.name) LIKE ? DESC, s.instrument_type = 'equity' DESC,"
        " schemes_holding DESC, s.name LIMIT ?",
        (*([month] if month else []), f"%{text}%", f"{text}%", f"{text}%", limit)).fetchall()
    return [dict(r) for r in rows]


class WatchlistRequest(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    stocks: list[Annotated[str, Field(min_length=12, max_length=12)]] = Field(default_factory=list, max_length=100)
    active_only: bool = True
    release: str | None = None
    rules: str | None = None


def create_app(db_path: str | Path | None = None, release_dir: Path | None = None, *,
               allowed_hosts: tuple[str, ...] | None = None, hosted: bool = False,
               expected_month: str | None = None) -> FastAPI:
    resolved_db = _resolve_db_path(db_path)
    releases = ReleaseStore(Path(resolved_db), release_dir)
    # Verify a retained launch release before accepting requests. Later access
    # checks file signatures and re-verifies if either DB or manifest changed.
    if re.fullmatch(r"[a-f0-9]{64}", Path(resolved_db).stem):
        releases.retained(Path(resolved_db).stem)
    report_comparisons = {}
    report_lock = threading.Lock()
    overview_cache = {}
    coverage_cache = {}
    overview_lock = threading.Lock()

    def month_context(conn, view):
        path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        verified = releases._verified.get(path.stem)
        if path != releases.directory / f"{path.stem}.db" or verified is None:
            return _month_view(conn, view)
        # _ro_connect verifies the file signatures before every request. Include
        # those signatures and the calculation version to isolate all scopes.
        key = (path.stem, verified[0], RULE_VERSION, view["month"], bool(view["active_only"]))
        coverage_key = (*key, bool(view["equity_only"]))
        with overview_lock:
            if key not in overview_cache:
                if len(overview_cache) >= 16:
                    overview_cache.pop(next(iter(overview_cache)))
                overview_cache[key] = _month_overview(conn, view["month"], bool(view["active_only"]))
            if coverage_key not in coverage_cache:
                if len(coverage_cache) >= 32:
                    coverage_cache.pop(next(iter(coverage_cache)))
                coverage_cache[coverage_key] = month_coverage(
                    conn, view["month"], bool(view["equity_only"]), bool(view["active_only"]))
            overview = copy.deepcopy(overview_cache[key])
            coverage = copy.deepcopy(coverage_cache[coverage_key])
        return _month_view(conn, view, overview=overview, coverage=coverage)

    def report_for(conn, month, stocks, active_only, identity, effective_rules):
        path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        immutable = path == releases.directory / f"{identity}.db" and identity in releases._verified
        comparison = None
        if stocks and immutable:
            key = (identity, effective_rules, month, active_only)
            with report_lock:
                if key not in report_comparisons:
                    if len(report_comparisons) >= 4:
                        report_comparisons.pop(next(iter(report_comparisons)))
                    report_comparisons[key] = comparison_for_rules(conn, month, active_only, effective_rules)
                comparison = report_comparisons[key]
        return monthly_report(conn, month, stocks, active_only, release_id=identity, comparison=comparison, rules_version=effective_rules)

    def release_connection(release=None, rules=None):
        try:
            return releases.connect(release, rules)
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def scoped_evidence(conn, isin, month, active_only, amc, identity, start, end, cohort, effective_rules):
        if any((start, end, cohort)):
            if not all((start, end, cohort)):
                raise HTTPException(status_code=400, detail="historical evidence requires start, end and cohort")
            try:
                return history_evidence(conn, isin.upper(), month, start, end, active_only == 1,
                                        amc, identity, cohort, effective_rules)
            except LegacyReplayError:
                raise
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if month not in _known_months(conn):
            raise HTTPException(status_code=404, detail="Unknown month")
        return stock_evidence(conn, isin.upper(), month, active_only == 1, amc, release_id=identity, rules_version=effective_rules)

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    # Fingerprints prevent cached assets from outliving a deployed UI change.
    assets = {name: hashlib.sha256((_WEB_DIR / "static" / name).read_bytes()).hexdigest()[:12]
              for name in ("style.css", "dashboard.js")}
    templates.env.globals["asset_url"] = lambda name: f"/static/{name}?v={assets[name]}"
    templates.env.filters.update(crore=_crore, signed=_signed, compact_shares=_compact_shares,
                                 month_label=_month_label,
                                 scheme_label=_scheme_label, stock_label=_stock_label)

    app = FastAPI(title="FindIt holdings dashboard (read-only)")

    if hosted and (not allowed_hosts or not expected_month):
        raise ValueError("Hosted serving requires exact allowed hosts and an expected month")
    if allowed_hosts:
        if any(not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or "*" in host for host in allowed_hosts):
            raise ValueError("Allowed hosts must be exact hostnames")
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts), www_redirect=False)

    def replay_conflict(exc, identity, path, params):
        successor = None
        # The current corrected release is preferred; archived siblings are
        # eligible only when their manifest explicitly names this parent.
        current_manifest = Path(resolved_db).with_suffix(".json")
        candidates = [current_manifest, *sorted(releases.directory.glob("*.json"))]
        for manifest in candidates:
            if not re.fullmatch(r"[a-f0-9]{64}", manifest.stem):
                continue
            try:
                _, metadata = releases.retained(manifest.stem)
            except (OSError, ValueError, sqlite3.Error):
                continue
            if metadata.get("rule_version") == RULE_VERSION and (
                    metadata.get("parent_release") == identity or manifest == current_manifest):
                successor = metadata["release_id"]
                break
        params = {k: v for k, v in params.items() if v is not None}
        params["rules"] = RULE_VERSION
        if successor:
            params["release"] = successor
        return HTTPException(status_code=409, detail={
            "message": str(exc), "successor_url": path + "?" + urlencode(params),
            "successor_available": bool(successor)})

    @app.middleware("http")
    async def response_policy(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {"/api/watchlist", "/watchlist/report"}:
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health/live")
    def health_live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def health_ready():
        conn = None
        try:
            if not re.fullmatch(r"[a-f0-9]{64}", Path(resolved_db).stem):
                raise ValueError("A verified retained release is required")
            context = releases.connect()
            conn, identity = context
            month = expected_month or _latest_holdings_month(conn)
            if not month or month != _latest_holdings_month(conn):
                raise ValueError("Expected release month is unavailable")
            if comparison_for_rules(conn, month, True, context.rules_version).empty:
                raise ValueError("No validated domestic comparison is available")
            return {"status": "ready", "release_id": identity,
                    "rule_version": context.rules_version, "month": month}
        except (ValueError, OSError, sqlite3.Error):
            raise HTTPException(status_code=503, detail="Retained release is not ready") from None
        finally:
            if conn is not None:
                conn.close()

    app.mount(
        "/static",
        StaticFiles(directory=str(_WEB_DIR / "static")),
        name="static",
    )

    # -- coverage ---------------------------------------------------------
    @app.get("/api/coverage")
    def api_coverage() -> Any:
        conn = _ro_connect(resolved_db, releases)
        try:
            try:
                counts = conn.execute(
                    "SELECT report_month, COUNT(DISTINCT scheme_id), COUNT(*) "
                    "FROM mf_holdings_monthly WHERE report_month IS NOT NULL "
                    "GROUP BY report_month ORDER BY report_month"
                ).fetchall()
            except sqlite3.Error:
                counts = []
            months = [str(r[0]) for r in counts]
            scheme_counts = {str(r[0]): int(r[1]) for r in counts}
            holdings_counts = {str(r[0]): int(r[2]) for r in counts}
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
            delta_month_set = set(delta_months)
            per_month = [
                {
                    "month": m,
                    "schemes": scheme_counts.get(m, 0),
                    "holdings": holdings_counts.get(m, 0),
                    "has_deltas": m in delta_month_set,
                }
                for m in months
            ]
            quarantined = [
                {"scheme_id": sid, "report_month": m}
                for (sid, m) in sorted(queries.quarantined(conn))
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
                    "validated" if queries.has_status_table(conn) else "unvalidated"
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
        conn = _ro_connect(resolved_db, releases)
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
        conn = _ro_connect(resolved_db, releases)
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            equity_filter = equity_only == 1
            # Index/ETF/debt schemes track a benchmark rather than express a
            # view; active_only=0 opts back in to the wider view.
            active_filter = active_only == 1
            quarantined_schemes = sorted(sid for sid, _ in queries.quarantined(conn, month))
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
    def api_summary(scheme_id: Annotated[int, PathParam(ge=1, le=9223372036854775807)], month: str) -> Any:
        conn = _ro_connect(resolved_db, releases)
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
            # get_summary owns the withhold rule, for the current month and the
            # previous month the comparison is made against.
            summary_result = get_summary(conn, scheme_id, month)
            text = summary_result["text"]
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
                    "quarantined": summary_result["reason"] == "quarantined",
                }
            )
        finally:
            conn.close()

    # -- stock ------------------------------------------------------------
    def watchlist_result(month, selected, active_only, release=None, rules=None):
        if not selected and not release and not rules and not re.fullmatch(r"[a-f0-9]{64}", Path(resolved_db).stem):
            conn = _ro_connect(resolved_db, releases)
            try:
                if month not in _known_months(conn):
                    raise HTTPException(status_code=404, detail="Unknown month")
                return {"month": month, "stocks": [], "release_id": None,
                        "rule_version": RULE_VERSION, "status": "empty_watchlist"}
            finally:
                conn.close()
        context = release_connection(release, rules)
        conn, identity = context
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail="Unknown month")
            return _sanitize(report_for(conn, month, selected, active_only, identity, context.rules_version))
        except LegacyReplayError as exc:
            raise replay_conflict(exc, identity, "/", {"month": month}) from exc
        finally:
            conn.close()

    def selected_stocks(stocks):
        try:
            return parse_stocks(stocks)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/watchlist")
    def api_watchlist(month: str, stocks: str = "", active_only: int = 1, release: str | None = None, rules: str | None = None) -> Any:
        return watchlist_result(month, selected_stocks(stocks), active_only == 1, release, rules)

    @app.post("/api/watchlist")
    def api_watchlist_post(body: WatchlistRequest) -> Any:
        return watchlist_result(body.month, selected_stocks(",".join(body.stocks)), body.active_only, body.release, body.rules)

    def download_report(request, month, selected, active_only, release=None, rules=None):
        context = release_connection(release, rules)
        conn, identity = context
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail="Unknown month")
            report = report_for(conn, month, selected, active_only, identity, context.rules_version)
            origin = ("https://" + request.url.hostname + "/") if hosted else str(request.base_url)
            return Response(render_report(report, origin, hosted=hosted), media_type="text/markdown",
                            headers={"Content-Disposition": f'attachment; filename="findit-{month}-{identity[:12]}.md"'})
        except LegacyReplayError as exc:
            raise replay_conflict(exc, identity, "/", {"month": month}) from exc
        finally:
            conn.close()

    @app.get("/watchlist/report")
    def watchlist_download(request: Request, month: str, stocks: str = "", active_only: int = 1, release: str | None = None, rules: str | None = None) -> Any:
        return download_report(request, month, selected_stocks(stocks), active_only == 1, release, rules)

    @app.post("/watchlist/report")
    def watchlist_download_post(request: Request, body: WatchlistRequest) -> Any:
        return download_report(request, body.month, selected_stocks(",".join(body.stocks)), body.active_only, body.release, body.rules)

    @app.get("/api/history/{isin}")
    def api_history(isin: str, start: str, end: str, active_only: int = 1,
                    amc: str | None = None, release: str | None = None, rules: str | None = None) -> Any:
        context = release_connection(release, rules)
        conn, identity = context
        effective_rules = context.rules_version
        try:
            try:
                return _sanitize(stock_history(conn, isin.upper(), start, end, active_only == 1, amc, release_id=identity, rules_version=effective_rules))
            except LegacyReplayError as exc:
                raise replay_conflict(exc, identity, "/history/" + isin.upper(),
                                      {"start": start, "end": end, "active_only": active_only, "amc": amc}) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            conn.close()

    @app.get("/history/{isin}")
    def history_page(request: Request, isin: str, start: str, end: str,
                     active_only: int = 1, amc: str | None = None, release: str | None = None, rules: str | None = None) -> Any:
        context = release_connection(release, rules)
        conn, identity = context
        effective_rules = context.rules_version
        try:
            try:
                result = stock_history(conn, isin.upper(), start, end, active_only == 1, amc, release_id=identity, rules_version=effective_rules)
            except LegacyReplayError as exc:
                raise replay_conflict(exc, identity, "/history/" + isin.upper(),
                                      {"start": start, "end": end, "active_only": active_only, "amc": amc}) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            houses = [row[0] for row in conn.execute(
                "SELECT DISTINCT s.amc_name FROM schemes s JOIN mf_holdings_monthly h USING(scheme_id) "
                "WHERE h.report_month BETWEEN ? AND ? "
                "AND (?=0 OR s.is_active_equity=1) ORDER BY s.amc_name",
                (start, end, active_only))]
            if amc and amc not in houses:
                houses.append(amc)
            return templates.TemplateResponse(request, "history.html",
                                              {"h": _sanitize(result), "houses": houses})
        finally:
            conn.close()

    @app.get("/api/evidence/{isin}")
    def api_evidence(isin: str, month: str, active_only: int = 1, amc: str | None = None,
                     release: str | None = None, rules: str | None = None,
                     start: str | None = None, end: str | None = None, cohort: str | None = None) -> Any:
        if cohort and not release:
            raise HTTPException(status_code=400, detail="historical evidence requires a release")
        context = release_connection(release, rules)
        conn, identity = context
        effective_rules = context.rules_version
        try:
            return _sanitize(scoped_evidence(conn, isin, month, active_only, amc, identity, start, end, cohort, effective_rules))
        except LegacyReplayError as exc:
            raise replay_conflict(exc, identity, "/evidence/" + isin.upper(),
                                  {"month": month, "active_only": active_only, "amc": amc}) from exc
        finally:
            conn.close()

    @app.get("/evidence/{isin}")
    def evidence_page(request: Request, isin: str, month: str, active_only: int = 1,
                      amc: str | None = None, release: str | None = None, rules: str | None = None,
                      start: str | None = None, end: str | None = None, cohort: str | None = None) -> Any:
        if cohort and not release:
            raise HTTPException(status_code=400, detail="historical evidence requires a release")
        context = release_connection(release, rules)
        conn, identity = context
        effective_rules = context.rules_version
        try:
            evidence = scoped_evidence(conn, isin, month, active_only, amc, identity, start, end, cohort, effective_rules)
            evidence["history_url"] = evidence.get("history_url") or ("/history/" + isin.upper() + "?" +
                urlencode({
                    "start": min(_known_months(conn)), "end": month, "active_only": active_only,
                    "amc": amc or "", "release": identity, "rules": effective_rules}))
            return templates.TemplateResponse(request, "evidence.html", {"e": _sanitize(evidence)})
        except LegacyReplayError as exc:
            raise replay_conflict(exc, identity, "/evidence/" + isin.upper(),
                                  {"month": month, "active_only": active_only, "amc": amc}) from exc
        finally:
            conn.close()

    @app.get("/api/stock/{isin}")
    def api_stock(isin: str, month: str | None = None) -> Any:
        conn = _ro_connect(resolved_db, releases)
        try:
            return _stock_payload(conn, isin, month)
        finally:
            conn.close()

    @app.get("/api/stocks/search")
    def api_stock_search(q: str = "", month: str | None = None, limit: int = 8) -> Any:
        """Name or ISIN-prefix suggestions for the stock lookup, as JSON."""
        conn = _ro_connect(resolved_db, releases)
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
        conn = _ro_connect(resolved_db, releases)
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
                ctx["history_start"] = min(_known_months(conn))
                ranked = _ranked(conn, stock["month"], bool(view["equity_only"]),
                                 bool(view["active_only"]))
                hits = _records(ranked[ranked["isin"] == stock["isin"]]) if not ranked.empty else []
                ctx["activity"] = hits[0] if hits else None
            return templates.TemplateResponse(request, "_stock.html", _sanitize(ctx))
        finally:
            conn.close()

    # -- month view fragment ---------------------------------------------
    @app.get("/coverage")
    def coverage_page(request: Request, month: str, amc: str | None = None,
                      equity_only: int = 1, active_only: int = 1, side: str = "buy",
                      limit: int = _DEFAULT_LIMIT, cols: str = "core") -> Any:
        conn = _ro_connect(resolved_db, releases)
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            view = _view(month, equity_only, active_only, side, limit, cols)
            ctx = month_context(conn, view)
            funds = [f for group in ctx["fund_groups"] for f in group["funds"]]
            if amc is not None:
                funds = [f for f in funds if f["amc"] == amc]
                if not funds:
                    raise HTTPException(status_code=404, detail="Unknown featured fund house")
            ctx["funds"] = funds
            return templates.TemplateResponse(request, "coverage.html", _sanitize(ctx))
        finally:
            conn.close()

    @app.get("/fragments/month/{month}")
    def month_fragment(request: Request, month: str, equity_only: int = 1,
                       active_only: int = 1, side: str = "buy",
                       limit: int = _DEFAULT_LIMIT, cols: str = "core") -> Any:
        conn = _ro_connect(resolved_db, releases)
        try:
            if month not in _known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            view = _view(month, equity_only, active_only, side, limit, cols)
            ctx = _sanitize(month_context(conn, view))
            return templates.TemplateResponse(request, "_month_view.html", ctx)
        finally:
            conn.close()

    # -- dashboard --------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request, month: str | None = None, equity_only: int = 1,
                  active_only: int = 1, side: str = "buy",
                  limit: int = _DEFAULT_LIMIT, cols: str = "core") -> Any:
        try:
            conn = _ro_connect(resolved_db, releases)
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
                ctx.update(month_context(conn, view))
            return templates.TemplateResponse(request, "dashboard.html", _sanitize(ctx))
        finally:
            conn.close()

    return app
