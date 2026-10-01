"""Who is in a month's comparison, who is left out and why.

The dashboard and the monthly report both state these counts, so both read
them from here. "compared" is consensus_signals.voting_schemes -- the exact
set the ranking counts -- so a coverage line can never disagree with the
table under it.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from findit.core import consensus_signals, publication
from findit.store import queries


def house_coverage(conn: sqlite3.Connection, month: str, amc: str,
                   compared_ids: set[int], active_only: bool = True) -> dict[str, Any]:
    """Reconcile an official inventory with snapshots; never infer its denominator.

    The caller supplies the exact cohort used by the house calculation. An
    inventory must account for both months before any complete claim is made.
    """
    prev = (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")
    loaded = {sid for sid, house in _month_schemes(
        conn, "mf_holdings_monthly", month, active_only).items() if house == amc}
    previous = set(_month_schemes(conn, "mf_holdings_monthly", prev, False))
    statuses = queries.scheme_statuses(conn)
    passed = {sid for sid in loaded if statuses.get((sid, month)) == queries.STATUS_OK}
    expected_rows = []
    inventories = []
    if queries.has_table(conn, "coverage_inventories"):
        inventories = conn.execute(
            "SELECT report_month, exhaustive FROM coverage_inventories "
            "WHERE amc_name = ? AND report_month IN (?, ?)", (amc, prev, month)).fetchall()
        expected_rows = conn.execute(
            "SELECT official_key, official_name, scheme_id, active_eligible, equity_eligible, "
            "exclusion_reason FROM expected_funds WHERE amc_name = ? AND report_month = ? "
            "ORDER BY official_key", (amc, month)).fetchall()
    known = {str(m) for m, complete in inventories if complete == 1} == {prev, month}
    eligible = [r for r in expected_rows if r[4] and (not active_only or r[3])]
    details = []
    for key, name, sid, _, _, reason in eligible:
        if sid is None:
            state = "identity_unresolved"
        elif sid not in loaded:
            state = "missing_current"
        elif sid not in previous:
            state = "missing_previous"
        elif any(statuses.get((sid, m)) == queries.STATUS_QUARANTINED for m in (prev, month)):
            state = "withheld"
        elif any(statuses.get((sid, m)) != queries.STATUS_OK for m in (prev, month)):
            state = "not_validated"
        elif sid not in compared_ids:
            state = "no_usable_comparison"
        else:
            state = "compared"
        details.append({"key": key, "name": name, "scheme_id": sid,
                        "state": state, "reason": reason})
    expected_ids = {r[2] for r in eligible if r[2] is not None}
    extras = compared_ids - expected_ids if known else set()
    state = ("unavailable" if not compared_ids else
             "unknown_completeness" if not known else
             "complete" if details and all(r["state"] == "compared" for r in details)
             and not extras else "partial")
    return {"coverage_state": state, "inventory_known": known,
            "expected": len(eligible) if known else None, "loaded": len(loaded),
            "validated_count": len(passed), "compared": len(compared_ids),
            "expected_details": details,
            "outside_inventory": len(extras),
            "out_of_scope": len(expected_rows) - len(eligible),
            "missing_previous": len(loaded - previous),
            "withheld_count": sum(any(statuses.get((sid, m)) == queries.STATUS_QUARANTINED
                                      for m in (prev, month)) for sid in loaded),
            "not_validated_count": len(loaded - passed),
            "inventory_source": _scalar(conn, "SELECT source_url FROM coverage_inventories "
                                        "WHERE amc_name = ? AND report_month = ?", (amc, month))}


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
        # The market-wide ranking includes houses outside the selected eight.
        "coverage_state": "unknown_completeness" if compared else "unavailable",
        "expected": None,
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
