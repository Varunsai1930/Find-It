"""Deterministic stock reports, composed from the existing evidence calculation."""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlencode

from findit.core.evidence import stock_evidence
from findit.core.consensus_signals import comparison_rows
from findit.store.releases import RULE_VERSION, content_id


def parse_stocks(value: str) -> list[str]:
    stocks = sorted(set(s.strip().upper() for s in value.split(",") if s.strip()))
    if len(stocks) > 100 or any(not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", s) for s in stocks):
        raise ValueError("use up to 100 valid ISINs")
    return stocks


def monthly_report(conn: sqlite3.Connection, month: str, stocks: list[str],
                   active_only: bool = True) -> dict:
    items = []
    comparison = comparison_rows(conn, month, active_only)
    release_id = content_id(conn)
    for isin in sorted(set(stocks)):
        stock = conn.execute("SELECT name,instrument_type FROM stocks WHERE isin=?", (isin,)).fetchone()
        e = stock_evidence(conn, isin, month, active_only, comparison=comparison, release_id=release_id)
        by_house = {}
        for fund in e["funds"]:
            by_house[fund["amc_name"]] = by_house.get(fund["amc_name"], 0) + fund["qty_change"]
        items.append({"isin": isin, "name": stock[0] if stock else isin,
                      "status": "unknown_stock" if stock is None else "unavailable" if e["no_data"] else
                                "net_unchanged_with_opposing_changes" if abs(e["net_share_change"]) <= 1e-6 and
                                e["gross_added_shares"] + e["gross_reduced_shares"] > 1e-6 else
                                "unchanged_in_compared_funds" if abs(e["net_share_change"]) <= 1e-6 else "changed",
                      "houses_buying": None if e["no_data"] else sum(v > 1e-6 for v in by_house.values()),
                      "houses_selling": None if e["no_data"] else sum(v < -1e-6 for v in by_house.values()),
                      "net_share_change": None if e["no_data"] else e["net_share_change"],
                      "net_flow_lakhs": e["net_flow_lakhs"], "coverage": e["coverage"],
                      "additions": None if e["no_data"] else sum(f["quantity_prev"] == 0 and f["quantity_curr"] > 0 for f in e["funds"]),
                      "exits": None if e["no_data"] else sum(f["quantity_prev"] > 0 and f["quantity_curr"] == 0 for f in e["funds"]),
                      "largest_compared_changes": [{"fund": f["scheme_name"], "shares": f["qty_change"],
                                                     "flow_lakhs": f["flow_lakhs"]} for f in sorted(
                          e["funds"], key=lambda f: abs(f["qty_change"]), reverse=True)[:3]],
                      "evidence_url": f"/evidence/{isin}?" + urlencode({"month": month, "active_only": int(active_only)}),
                      "review_status": e["review_status"]})
    return {"month": month, "release_id": release_id, "rule_version": RULE_VERSION,
            "scope": "Domestic equity; " + ("active stock-pickers" if active_only else "all fund types"),
            "market_completeness": "unknown", "stocks": items,
            "claims": "Research utility only. No established investment-performance claim."}


def render_report(report: dict) -> str:
    def available(value):
        return value if value is not None else "unavailable"
    lines = [f"# Changes in my stocks — {report['month']}", "",
             f"Data release: {report['release_id']}", f"Rules: {report['rule_version']}",
             f"Scope: {report['scope']}. All-market completeness: unknown.", "",
             report["claims"], ""]
    for stock in report["stocks"]:
        lines += [f"## {stock['name']} ({stock['isin']})", f"Status: {stock['status']}",
                  f"Fund houses buying/selling: {available(stock['houses_buying'])}/{available(stock['houses_selling'])}",
                  f"Net share change: {stock['net_share_change'] if stock['net_share_change'] is not None else 'unavailable'}",
                  f"Estimated net trading value (₹ lakh): {stock['net_flow_lakhs'] if stock['net_flow_lakhs'] is not None else 'unavailable'}",
                  f"Individual-fund additions/exits: {available(stock['additions'])}/{available(stock['exits'])}",
                  f"Evidence: {stock['evidence_url']}", f"Review: {stock['review_status']}"]
        for change in stock["largest_compared_changes"]:
            lines.append(f"- Largest compared change: {change['fund']}; {change['shares']} shares; ₹ lakh {available(change['flow_lakhs'])}")
        for house, c in stock["coverage"].items():
            lines.append(f"- {house}: {c['coverage_state']}; expected {c['expected'] if c['expected'] is not None else 'unknown'}, loaded {c['loaded']}, validated {c['validated_count']}, compared {c['compared']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
