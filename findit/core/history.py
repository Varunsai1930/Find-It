"""Monthly history over a named, fixed cohort; absent comparisons stay gaps."""
from __future__ import annotations

import sqlite3
from datetime import date

from findit.core.consensus_signals import comparison_rows
from findit.store import queries


def month_range(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start + "-01"), date.fromisoformat(end + "-01")
    a, b = first.year * 12 + first.month - 1, last.year * 12 + last.month - 1
    if b < a or b - a > 24:
        raise ValueError("history needs an ordered range of at most 25 snapshots")
    return [f"{n // 12:04d}-{n % 12 + 1:02d}" for n in range(a, b + 1)]


def stock_history(conn: sqlite3.Connection, isin: str, start: str, end: str,
                  active_only: bool = True, amc: str | None = None) -> dict:
    months = month_range(start, end)
    frames = {m: comparison_rows(conn, m, active_only) for m in months[1:]}
    cohorts = []
    for frame in frames.values():
        if amc and not frame.empty:
            frame = frame[frame["amc_name"] == amc]
        cohorts.append(set(int(sid) for sid in frame["scheme_id"]) if not frame.empty else set())
    cohort = set.intersection(*cohorts) if cohorts else set()
    source_backed = set()
    if queries.has_table(conn, "snapshot_sources"):
        source_backed = {sid for sid in cohort if all(conn.execute(
            "SELECT 1 FROM snapshot_sources WHERE scheme_id=? AND report_month=?",
            (sid, month)).fetchone() for month in months)}
    # Show a validated fixed cohort even on legacy data, naming provenance gaps.
    points = []
    for month in months:
        if not cohort:
            points.append({"month": month, "shares": None, "net_share_change": None,
                           "net_flow_lakhs": None, "status": "unavailable", "funds": 0})
            continue
        placeholders = ",".join("?" for _ in cohort)
        shares = conn.execute(
            f"SELECT SUM(quantity) FROM mf_holdings_monthly WHERE report_month=? AND isin=? "
            f"AND scheme_id IN ({placeholders})", (month, isin, *sorted(cohort))).fetchone()[0]
        # Every cohort snapshot exists and passed: an absent security means not
        # held by this cohort, rather than a missing month being filled with zero.
        point = {"month": month, "shares": shares or 0, "funds": len(cohort),
                 "status": "validated_fixed_cohort", "net_share_change": None,
                 "net_flow_lakhs": None, "houses_buying": None, "houses_selling": None,
                 "adjustments": []}
        if month in frames:
            frame = frames[month]
            subset = frame[(frame["scheme_id"].isin(cohort)) & (frame["isin"] == isin)]
            net_by_house = subset.groupby("amc_name")["qty_change"].sum()
            point.update(net_share_change=float(subset["qty_change"].sum()),
                         net_flow_lakhs=(float(subset["flow_lakhs"].sum())
                                         if subset["flow_lakhs"].notna().all() else None),
                         houses_buying=int((net_by_house > 1e-6).sum()),
                         houses_selling=int((net_by_house < -1e-6).sum()),
                         adjustments=sorted(set(subset.loc[
                             subset["adjustment_basis"] != "none", "adjustment_basis"])))
        points.append(point)
    return {"isin": isin, "start": start, "end": end, "amc": amc,
            "active_only": active_only, "cohort": sorted(cohort), "cohort_count": len(cohort),
            "source_backed_count": len(source_backed), "points": points,
            "scope": "Same validated individual funds at every snapshot and adjacent comparison in this range. Completeness of the wider fund universe remains unknown.",
            "limitation": ("No fixed cohort is available across the full range; all points remain gaps."
                           if not cohort else "History describes this cohort only. It does not establish investment performance.")}
