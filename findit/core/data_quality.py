"""Read-time safety for holdings whose stored validation predates current rules."""
from __future__ import annotations

import sqlite3

from findit.store import queries


def bad_domestic_quantity_sql(scheme: str, month: str) -> str:
    """A predicate for unreadable domestic-equity quantities.

    Expressions are internal SQL aliases, never user-supplied strings. A foreign
    security's missing units must not withhold a usable domestic-equity sleeve.
    """
    return (
        f"COALESCE(({scheme}, {month}) IN ("
        "SELECT bad.scheme_id, bad.report_month FROM mf_holdings_monthly bad "
        "JOIN stocks quality_stock ON quality_stock.isin=bad.isin "
        "WHERE quality_stock.instrument_type='equity' AND "
        "(bad.quantity IS NULL OR typeof(bad.quantity) NOT IN ('integer','real') "
        "OR bad.quantity < 0 OR bad.quantity > 1.7976931348623157e308)), 0)"
    )


def bad_domestic_quantity_pairs(conn: sqlite3.Connection,
                                scheme_id: int | None = None) -> set[tuple[int, str]]:
    """Unsafe scheme-months, independent of their historical stored status."""
    if not queries.has_table(conn, "mf_holdings_monthly") or not queries.has_table(conn, "stocks"):
        return set()
    scope = " AND bad.scheme_id=?" if scheme_id is not None else ""
    return {(int(sid), str(month)) for sid, month in conn.execute(
        "SELECT DISTINCT bad.scheme_id,bad.report_month FROM mf_holdings_monthly bad "
        "JOIN stocks s ON s.isin=bad.isin WHERE s.instrument_type='equity' AND "
        "(bad.quantity IS NULL OR typeof(bad.quantity) NOT IN ('integer','real') "
        "OR bad.quantity < 0 OR bad.quantity > 1.7976931348623157e308)" + scope,
        (scheme_id,) if scheme_id is not None else ())}
