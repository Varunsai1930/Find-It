"""Auditable views of the existing adjusted fund-house calculation."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from urllib.parse import urlparse

from findit.core.consensus_signals import comparison_rows
from findit.core.coverage import house_coverage
from findit.store import queries
from findit.store.releases import RULE_VERSION, content_id


def public_url(value: str | None) -> str | None:
    """Only web links enter the public evidence contract, never file paths."""
    if value and urlparse(value).scheme in {"https", "http"} and urlparse(value).hostname:
        return value
    return None


def snapshot_evidence(conn: sqlite3.Connection, sid: int, month: str, isin: str) -> dict:
    statuses = queries.scheme_statuses(conn, month)
    result = {"month": month, "validation": statuses.get((sid, month), "not_validated"),
              "sources": [], "rows": [], "provenance_status": "unavailable"}
    if not queries.has_table(conn, "snapshot_sources"):
        return result
    sources = conn.execute(
        "SELECT s.source_sha256, d.workbook_name, d.source_url, d.retrieved_at, "
        "d.published_at, s.parser_version FROM snapshot_sources s "
        "JOIN disclosure_sources d ON d.sha256=s.source_sha256 "
        "WHERE s.scheme_id=? AND s.report_month=? ORDER BY s.source_sha256", (sid, month))
    for sha, book, url, retrieved, published, parser in sources:
        result["sources"].append({"sha256": sha, "workbook": book, "url": public_url(url),
                                  "retrieved_at": retrieved, "published_at": published,
                                  "publication_basis": "observed" if published else "unknown",
                                  "parser_version": parser})
        result["rows"].extend({"sha256": sha, "sheet": sheet, "row": row,
                               "raw": json.loads(raw) if raw else None,
                               "normalized": json.loads(normalized)}
                              for sheet, row, raw, normalized in conn.execute(
                                  "SELECT sheet,row_number,raw_json,normalized_json FROM holding_evidence "
                                  "WHERE scheme_id=? AND report_month=? AND isin=? "
                                  "AND source_sha256=? AND parser_version=? ORDER BY sheet,row_number",
                                  (sid, month, isin, sha, parser)))
    if result["sources"]:
        held = conn.execute("SELECT 1 FROM mf_holdings_monthly WHERE scheme_id=? AND report_month=? AND isin=?",
                            (sid, month, isin)).fetchone()
        result["provenance_status"] = ("available" if result["rows"] else
                                       "incomplete" if held else "absent_from_loaded_snapshot")
    return result


def stock_evidence(conn: sqlite3.Connection, isin: str, month: str,
                   active_only: bool = True, amc: str | None = None,
                   comparison=None, release_id: str | None = None,
                   include_sources: bool = True, coverage_cache: dict | None = None) -> dict:
    prev = (date.fromisoformat(month + "-01") - timedelta(days=1)).strftime("%Y-%m")
    rows = comparison if comparison is not None else comparison_rows(conn, month, active_only)
    cohort = rows
    if not rows.empty:
        rows = rows[rows["isin"] == isin]
        if amc:
            rows = rows[rows["amc_name"] == amc]
    funds = []
    for row in rows.to_dict("records"):
        sid = int(row["scheme_id"])
        label = conn.execute("SELECT scheme_name,scheme_title FROM schemes WHERE scheme_id=?", (sid,)).fetchone()
        fund = {**row, "scheme_name": label[1] or label[0]}
        if include_sources:
            fund.update(previous_source=snapshot_evidence(conn, sid, prev, isin),
                        current_source=snapshot_evidence(conn, sid, month, isin))
        funds.append(fund)
    houses = sorted({row["amc_name"] for row in funds} | ({amc} if amc else set()))
    # One report uses one comparison frame. Share house coverage within that
    # request, while source rows are only constructed for an evidence view.
    coverage_cache = coverage_cache if coverage_cache is not None else {}
    coverage = {}
    for house in houses:
        if house not in coverage_cache:
            ids = (set(int(v) for v in cohort.loc[cohort["amc_name"] == house, "scheme_id"])
                   if not cohort.empty else set())
            coverage_cache[house] = house_coverage(conn, month, house, ids, active_only)
        coverage[house] = coverage_cache[house]
    all_priced = bool(funds) and all(row["flow_lakhs"] is not None and
                                    row["flow_lakhs"] == row["flow_lakhs"] for row in funds)
    return {"isin": isin, "month": month, "prev_month": prev, "amc": amc,
            "release_id": release_id or content_id(conn), "rule_version": RULE_VERSION,
            "active_only": active_only, "funds": funds, "coverage": coverage,
            "raw_previous_shares": sum(row["raw_quantity_prev"] for row in funds) if funds else None,
            "adjusted_previous_shares": sum(row["quantity_prev"] for row in funds) if funds else None,
            "current_shares": sum(row["quantity_curr"] for row in funds) if funds else None,
            "net_share_change": sum(row["qty_change"] for row in funds) if funds else None,
            "gross_added_shares": sum(max(row["qty_change"], 0) for row in funds) if funds else None,
            "gross_reduced_shares": sum(max(-row["qty_change"], 0) for row in funds) if funds else None,
            "net_flow_lakhs": sum(row["flow_lakhs"] for row in funds) if all_priced else None,
            "review_status": "not_independently_reviewed",
            "price_convention": "Share change × current month-end price implied by the disclosure's market value / shares. New positions use current value; exits use previous value. This estimates trading value, not execution prices or investment returns.",
            "ownership_note": "These are shares held and changes versus previous fund holdings. They are not the percentage of the company owned.",
            "no_data": not funds}
