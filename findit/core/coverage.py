"""Who is in a month's comparison, who is left out and why.

The dashboard and the monthly report both state these counts, so both read
them from here. "compared" is consensus_signals.voting_schemes -- the exact
set the ranking counts -- so a coverage line can never disagree with the
table under it.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from findit.core import consensus_signals, publication
from findit.store import queries


def _month_schemes(conn: sqlite3.Connection, table: str, month: str,
                   active_only: bool) -> dict[int, str]:
    """scheme_id -> AMC for schemes with rows in table for month."""
    active = queries.active_equity_sql(conn, "s") if active_only else ""
    rows = conn.execute(
        f"SELECT DISTINCT t.scheme_id, s.amc_name FROM {table} t"
        f" JOIN schemes s USING (scheme_id) WHERE t.report_month = ?{active}",
        (month,)).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def _prev_withheld_schemes(conn: sqlite3.Connection, month: str,
                           active_only: bool) -> set[int]:
    """In-scope schemes whose delta row this month compares against a quarantined prev_month.

    voting_schemes drops these, so without this count they would silently
    fall into "no_equity" even though the equity filter never touched them.
    """
    if not queries.has_status_table(conn):
        return set()
    active = queries.active_equity_sql(conn, "s") if active_only else ""
    rows = conn.execute(
        "SELECT DISTINCT d.scheme_id FROM mf_holding_deltas d "
        "JOIN schemes s ON s.scheme_id = d.scheme_id "
        f"WHERE d.report_month = ? AND {queries.quarantined_sql('d.scheme_id', 'd.prev_month')}{active}",
        (month,)).fetchall()
    return {int(r[0]) for r in rows}


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else row[0]


def month_coverage(conn: sqlite3.Connection, month: str, equity_only: bool = True,
                   active_only: bool = True) -> dict[str, Any]:
    """Coverage of one month's comparison, under the ranking's filters.

    Counts cover the schemes the active filter keeps: a scheme loaded without
    a previous month, quarantined itself, or compared against a previous
    month that was quarantined is never "compared". ``dropped_out`` counts
    schemes held in the previous month and missing from this one -- a file
    not loaded reads as a fund that vanished, never as a fund that sold. The
    ingest log does not record every load, so the last ingest is
    database-wide, never presented as this month's.
    """
    in_scope = _month_schemes(conn, "mf_holdings_monthly", month, active_only)
    validated = queries.has_status_table(conn)
    statuses = {sid: status for (sid, _), status in queries.scheme_statuses(conn, month).items()}
    withheld = {sid for sid in in_scope if statuses.get(sid) == queries.STATUS_QUARANTINED}
    compared = consensus_signals.voting_schemes(
        conn, month, "equity" if equity_only else None, active_only)
    with_deltas = _month_schemes(conn, "mf_holding_deltas", month, active_only).keys()
    no_previous = in_scope.keys() - with_deltas - withheld
    # Has a previous month, not withheld for this month, but that previous
    # month itself failed validation -- excluded from "compared" for that
    # reason, not because the equity filter dropped its holdings.
    prev_withheld = (_prev_withheld_schemes(conn, month, active_only)
                     & in_scope.keys()) - withheld
    prev_month = _scalar(conn, "SELECT MAX(prev_month) FROM mf_holding_deltas"
                               " WHERE report_month = ?", (month,))
    dropped_out = (_month_schemes(conn, "mf_holdings_monthly", prev_month, active_only).keys()
                   - in_scope.keys()) if prev_month else set()
    return {
        "compared": len(compared),
        "compared_amcs": len(set(compared.values())),
        "withheld": len(withheld),
        "no_previous": len(no_previous),
        "prev_withheld": len(prev_withheld),
        # Compared, but only on holdings the equity filter leaves out.
        "no_equity": len(in_scope.keys() - compared.keys() - withheld
                         - no_previous - prev_withheld),
        "dropped_out": len(dropped_out),
        "in_scope": len(in_scope),
        "loaded": len(_month_schemes(conn, "mf_holdings_monthly", month, False)),
        "validated": validated,
        "passed": sum(statuses.get(sid) == queries.STATUS_OK for sid in in_scope),
        "not_validated": sum(sid not in statuses for sid in in_scope),
        "prev_month": prev_month,
        "public_on": publication.mf_disclosure_deadline(month).isoformat(),
        "last_ingest": _scalar(conn, "SELECT MAX(started_at) FROM ingest_runs"),
    }
