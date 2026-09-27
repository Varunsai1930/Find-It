"""Shared reads of the tracker database.

Rules that decide what may be counted -- which scheme-months the validation
gate withheld, which schemes vote, which closes a backtest trades at -- are
read here once, so the pipeline, report, dashboard and backtests cannot
drift apart on them.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd

from findit.core import publication

# The first trading day on/after a target date is at most this far away.
MAX_TRADING_GAP_DAYS = 10
# scheme_month_status.status written by the validation gate.
STATUS_OK = "ok"
STATUS_QUARANTINED = "quarantined"


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of a table (empty set when the table does not exist)."""
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def has_table(conn: sqlite3.Connection, table: str) -> bool:
    """Whether a table exists. Errors propagate: unreadable is not absent."""
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,)).fetchone() is not None


# -- validation status ---------------------------------------------------------
#
# Only a database the gate never ran on (no scheme_month_status table) reads
# as "nothing quarantined". Any other error propagates: answering "none" on a
# broken table would publish quarantined schemes as though they had passed.

def has_status_table(conn: sqlite3.Connection) -> bool:
    """Whether the validation gate has ever run on this database."""
    return has_table(conn, "scheme_month_status")


def scheme_statuses(conn: sqlite3.Connection,
                    report_month: str | None = None) -> dict[tuple[int, str], str]:
    """Gate status per (scheme_id, report_month), for one month or all of them."""
    if not has_status_table(conn):
        return {}
    sql = "SELECT scheme_id, report_month, status FROM scheme_month_status"
    params: tuple = ()
    if report_month is not None:
        sql += " WHERE report_month = ?"
        params = (report_month,)
    return {(int(sid), str(month)): str(status)
            for sid, month, status in conn.execute(sql, params).fetchall()
            if sid is not None and month is not None}


def quarantined(conn: sqlite3.Connection,
                report_month: str | None = None) -> set[tuple[int, str]]:
    """(scheme_id, report_month) pairs the validation gate withheld."""
    return {key for key, status in scheme_statuses(conn, report_month).items()
            if status == STATUS_QUARANTINED}


def quarantined_sql(scheme: str, month: str) -> str:
    """SQL condition: the gate quarantined scheme-month (``scheme``, ``month``).

    Takes column expressions, e.g. ("d.scheme_id", "d.prev_month"). A delta is
    a comparison, so callers test both of its months. Only valid where the
    status table exists (see has_status_table).
    """
    return (f"EXISTS (SELECT 1 FROM scheme_month_status q WHERE q.scheme_id = {scheme} "
            f"AND q.report_month = {month} AND q.status = '{STATUS_QUARANTINED}')")


# -- which schemes vote ----------------------------------------------------------

def active_equity_sql(conn: sqlite3.Connection, alias: str) -> str:
    """' AND <alias>.is_active_equity = 1' -- index, ETF, arbitrage, FoF and debt
    schemes track or hedge rather than choose, so they never vote.

    Empty on a legacy DB whose schemes table predates the flag.
    """
    if "is_active_equity" not in table_columns(conn, "schemes"):
        return ""
    return f" AND {alias}.is_active_equity = 1"


# -- shareholding filings --------------------------------------------------------

def quarterly_filing_sql(columns: set[str], alias: str = "") -> str:
    """SQL predicate keeping quarterly shareholding filings, dropping interim ones.

    With a filing_type column, legacy NULL rows fall back to the date test.
    """
    prefix = f"{alias}." if alias else ""
    month_days = ",".join(f"'{md}'" for md in publication.QUARTER_END_MONTH_DAYS)
    by_date = f"substr({prefix}quarter_end, 6, 5) IN ({month_days})"
    if "filing_type" in columns:
        return (f"({prefix}filing_type = 'quarterly' OR "
                f"({prefix}filing_type IS NULL AND {by_date}))")
    return by_date


# -- prices -----------------------------------------------------------------------


def close_on_or_after(conn: sqlite3.Connection, target: date,
                      latest_ok: bool = False) -> tuple[str, pd.DataFrame] | None:
    """(trade_date, closes) for the first stored trading day on/after target.

    ``latest_ok`` falls back to the latest stored day after ``target``'s
    window -- used only for a holding period that has not ended yet.
    """
    row = conn.execute(
        "SELECT MIN(trade_date) FROM security_prices_daily "
        "WHERE trade_date >= ? AND trade_date <= ?",
        (target.isoformat(), (target + timedelta(days=MAX_TRADING_GAP_DAYS)).isoformat()),
    ).fetchone()
    day = row[0] if row else None
    if day is None and latest_ok:
        day = conn.execute("SELECT MAX(trade_date) FROM security_prices_daily").fetchone()[0]
    if day is None:
        return None
    has_value = "traded_value" in table_columns(conn, "security_prices_daily")
    return str(day), pd.read_sql_query(
        "SELECT isin, close_price, trade_date"
        + (", traded_value" if has_value else "")
        + " FROM security_prices_daily WHERE trade_date = ?", conn, params=(day,))
