"""Monthly history over a named, fixed cohort; absent comparisons stay gaps."""
from __future__ import annotations

import sqlite3
import hashlib
import json
from urllib.parse import urlencode
from datetime import date

from findit.core.consensus_signals import comparison_rows
from findit.store import queries
from findit.store.releases import RULE_VERSION, content_id


def month_range(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start + "-01"), date.fromisoformat(end + "-01")
    a, b = first.year * 12 + first.month - 1, last.year * 12 + last.month - 1
    if b < a or b - a > 24:
        raise ValueError("history needs an ordered range of at most 25 snapshots")
    return [f"{n // 12:04d}-{n % 12 + 1:02d}" for n in range(a, b + 1)]


def history_scope(conn, start, end, active_only=True, amc=None):
    months = month_range(start, end)
    frames = {m: comparison_rows(conn, m, active_only) for m in months[1:]}
    cohorts = []
    for frame in frames.values():
        if amc and not frame.empty:
            frame = frame[frame["amc_name"] == amc]
        cohorts.append(set(int(sid) for sid in frame["scheme_id"]) if not frame.empty else set())
    cohort = set.intersection(*cohorts) if cohorts else set()
    return months, frames, cohort


def scope_id(release_id, start, end, active_only, amc, cohort):
    return hashlib.sha256(json.dumps([release_id, start, end, active_only,
                                     amc or None, sorted(cohort)]).encode()).hexdigest()


def stock_history(conn: sqlite3.Connection, isin: str, start: str, end: str,
                  active_only: bool = True, amc: str | None = None,
                  release_id: str | None = None) -> dict:
    months, frames, cohort = history_scope(conn, start, end, active_only, amc)
    release_id = release_id or content_id(conn)
    token = scope_id(release_id, start, end, active_only, amc, cohort)
    stock = conn.execute("SELECT instrument_type FROM stocks WHERE isin=?", (isin,)).fetchone()
    known_stock = stock is not None
    in_scope = known_stock and stock[0] == "equity"
    source_backed = set()
    if queries.has_table(conn, "snapshot_sources"):
        source_backed = {sid for sid in cohort if all(conn.execute(
            "SELECT 1 FROM snapshot_sources WHERE scheme_id=? AND report_month=?",
            (sid, month)).fetchone() for month in months)}
    # Show a validated fixed cohort even on legacy data, naming provenance gaps.
    points = []
    for month in months:
        if not cohort or not in_scope:
            points.append({"month": month, "shares": None, "net_share_change": None,
                           "net_flow_lakhs": None, "status": "unavailable", "funds": 0,
                           "houses_buying": None, "houses_selling": None,
                           "adjustments": [], "source_revision": False})
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
        point["source_revision"] = bool(queries.has_table(conn, "holding_evidence") and conn.execute(
            f"SELECT 1 FROM holding_evidence e WHERE e.report_month=? AND e.isin=? AND e.scheme_id IN ({placeholders}) "
            "AND NOT EXISTS (SELECT 1 FROM snapshot_sources s WHERE s.scheme_id=e.scheme_id "
            "AND s.report_month=e.report_month AND s.source_sha256=e.source_sha256) LIMIT 1",
            (month, isin, *sorted(cohort))).fetchone())
        points.append(point)
    for point in points:
        point["evidence_url"] = ("/evidence/" + isin + "?" + urlencode({
            "month": point["month"], "start": start, "end": end,
            "active_only": int(active_only), "amc": amc or "", "cohort": token,
            "release": release_id, "rules": RULE_VERSION})
            if point["status"] != "unavailable" else None)
    maximum = max((p["shares"] or 0 for p in points), default=0)
    for point in points:
        point["bar_percent"] = (100 * point["shares"] / maximum if maximum and point["shares"] is not None else 0)
    return {"isin": isin, "start": start, "end": end, "amc": amc,
            "release_id": release_id, "rule_version": RULE_VERSION,
            "active_only": active_only, "cohort": sorted(cohort), "cohort_count": len(cohort),
            "source_backed_count": len(source_backed), "points": points,
            "scope": "Same validated individual funds at every snapshot and adjacent comparison in this range. Completeness of the wider fund universe remains unknown.",
            "limitation": ("Unknown stock in this data release; all points remain unavailable." if not known_stock else
                           "History is limited to domestic equity; this security is outside that scope."
                           if not in_scope else
                           "No fixed cohort is available across the full range; all points remain gaps."
                           if not cohort else "History describes this cohort only. It does not establish investment performance.")}


def history_evidence(conn, isin, month, start, end, active_only, amc, release_id, token):
    """Recompute and validate a named historical cohort, never accept arbitrary IDs."""
    from findit.core.evidence import stock_evidence, snapshot_evidence
    months, frames, cohort = history_scope(conn, start, end, active_only, amc)
    if month not in months or token != scope_id(release_id, start, end, active_only, amc, cohort):
        raise ValueError("historical evidence scope does not match its period, filters, cohort or release")
    stock = conn.execute("SELECT instrument_type FROM stocks WHERE isin=?", (isin,)).fetchone()
    if not cohort or stock is None:
        raise ValueError("historical evidence is unavailable for this cohort")
    if stock[0] != "equity":
        raise ValueError("historical evidence is limited to domestic equity")
    baseline = month == start
    frame = frames[months[1]] if baseline else frames[month]
    frame = frame[frame["scheme_id"].isin(cohort)]
    e = stock_evidence(conn, isin, month, active_only, amc, comparison=frame.iloc[0:0] if baseline else frame,
                       release_id=release_id)
    if baseline:
        placeholders = ",".join("?" for _ in cohort)
        rows = conn.execute(
            "SELECT h.scheme_id, s.amc_name, COALESCE(s.scheme_title,s.scheme_name), h.quantity "
            "FROM mf_holdings_monthly h JOIN schemes s USING(scheme_id) "
            f"WHERE h.report_month=? AND h.isin=? AND h.scheme_id IN ({placeholders}) ORDER BY h.scheme_id",
            (month, isin, *sorted(cohort)))
        e["funds"] = [{"scheme_id": sid, "amc_name": house, "scheme_name": name,
                       "quantity_curr": quantity, "current_source": snapshot_evidence(conn, sid, month, isin)}
                      for sid, house, name, quantity in rows]
        e["current_shares"] = sum(f["quantity_curr"] for f in e["funds"])
        e["prev_month"] = None
    elif not e["funds"]:
        # Validated snapshots with no holding in either month mean zero for
        # this known security and cohort, not an unavailable comparison.
        for key in ("raw_previous_shares", "adjusted_previous_shares", "current_shares",
                    "net_share_change", "gross_added_shares", "gross_reduced_shares", "net_flow_lakhs"):
            e[key] = 0
    e.update(no_data=False, baseline=baseline, cohort=sorted(cohort), cohort_count=len(cohort),
             history_start=start, history_end=end, coverage={},
             history_url="/history/" + isin + "?" + urlencode({"start": start, "end": end,
                 "active_only": int(active_only), "amc": amc or "", "release": release_id, "rules": RULE_VERSION}))
    return e
