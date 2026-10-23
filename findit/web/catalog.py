"""Read-only fund catalog and stock autocomplete routes."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import HTTPException, Query

MAX_STOCK_QUERY_LENGTH = 256


def register_catalog_routes(app, *, connect, known_months, find_stocks, scheme_label, sanitize):
    """Register lookup routes with the application's read-only connection policy."""
    # -- schemes ----------------------------------------------------------
    @app.get("/api/schemes")
    def api_schemes(month: str | None = None) -> Any:
        conn = connect()
        try:
            if month is not None:
                if month not in known_months(conn):
                    raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
                rows = conn.execute(
                    """SELECT DISTINCT s.*
                       FROM schemes s
                       JOIN mf_holdings_monthly h ON h.scheme_id = s.scheme_id
                       WHERE h.report_month = ?
                       ORDER BY s.amc_name, s.scheme_name""",
                    (month,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schemes "
                    "ORDER BY amc_name, scheme_name"
                ).fetchall()
            schemes = [
                {
                    "scheme_id": int(r["scheme_id"]),
                    "amc_name": r["amc_name"],
                    "scheme_name": r["scheme_name"],
                    "label": scheme_label(dict(r)),
                    "votes": dict(r).get("is_active_equity") != 0,
                }
                for r in rows
            ]
            return sanitize({"month": month, "count": len(schemes), "schemes": schemes})
        finally:
            conn.close()

    @app.get("/api/stocks/search")
    def api_stock_search(q: Annotated[str, Query(max_length=MAX_STOCK_QUERY_LENGTH)] = "",
                         month: str | None = None, limit: int = 8) -> Any:
        """Name or ISIN-prefix suggestions for the stock lookup, as JSON."""
        conn = connect()
        try:
            if month is not None and month not in known_months(conn):
                raise HTTPException(status_code=404, detail=f"Unknown month: {month}")
            results = find_stocks(conn, q, month, min(max(limit, 1), 25))
            return sanitize({"query": q.strip(), "month": month, "count": len(results),
                              "results": results})
        finally:
            conn.close()
