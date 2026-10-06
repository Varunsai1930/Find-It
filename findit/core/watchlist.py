"""Deterministic stock reports, composed from the existing evidence calculation."""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlencode

from findit.core.evidence import stock_evidence
from findit.core.replay import comparison_for_rules
from findit.store.releases import RULE_VERSION, content_id


def parse_stocks(value: str) -> list[str]:
    stocks = sorted(set(s.strip().upper() for s in value.split(",") if s.strip()))
    if len(stocks) > 100 or any(not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", s) for s in stocks):
        raise ValueError("use up to 100 valid ISINs")
    return stocks


def monthly_report(conn: sqlite3.Connection, month: str, stocks: list[str],
                   active_only: bool = True, release_id: str | None = None, comparison=None,
                   rules_version: str = RULE_VERSION) -> dict:
    items = []
    if stocks and comparison is None:
        comparison = comparison_for_rules(conn, month, active_only, rules_version)
    coverage_cache = {}
    release_id = release_id or content_id(conn, rules_version)
    for isin in sorted(set(stocks)):
        stock = conn.execute("SELECT name,instrument_type FROM stocks WHERE isin=?", (isin,)).fetchone()
        e = stock_evidence(conn, isin, month, active_only, comparison=comparison, release_id=release_id,
                           include_sources=False, coverage_cache=coverage_cache, rules_version=rules_version)
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
                      "evidence_url": f"/evidence/{isin}?" + urlencode({"month": month, "active_only": int(active_only), "release": release_id, "rules": rules_version}),
                      "review_status": e["review_status"]})
    return {"month": month, "release_id": release_id, "rule_version": rules_version,
            "scope": "Domestic equity; " + ("active stock-pickers" if active_only else "all fund types"),
            "market_completeness": "unknown", "stocks": items,
            "claims": "Research utility only. No established investment-performance claim."}


def render_report(report: dict, evidence_base_url: str = "http://127.0.0.1:65100", *, hosted: bool = False) -> str:
    evidence_base_url = evidence_base_url.rstrip("/")
    def available(value):
        return value if value is not None else "unavailable"
    lines = [f"# Changes in my stocks — {report['month']}", "",
             f"Data release: {report['release_id']}", f"Rules: {report['rule_version']}",
             f"Scope: {report['scope']}. All-market completeness: unknown.", "",
             report["claims"], "",
             "House directions net the compared individual funds’ share changes within each house. Stock detail counts houses with any buying or selling fund; a house can appear on both sides there.", "",
             "Reopen this report: open the downloaded .md file in a browser or text editor.", "",
             "1. Start your local FindIt server with the retained release database and matching .json manifest. Keep the matching application/rules version.",
             "2. Copy the complete Evidence URL below into your browser address bar (or click it in a Markdown viewer).",
             f"3. These links use the exporting server address {evidence_base_url}. If reopening on another local address, replace only that address; keep the entire path and query unchanged.",
             "4. Check the evidence page shows the same release, rules, month and scope as this report. Missing or mismatched releases are rejected, never replaced with current data.", ""]
    if hosted:
        begin = lines.index("1. Start your local FindIt server with the retained release database and matching .json manifest. Keep the matching application/rules version.")
        lines[begin:begin + 4] = [
            "1. Open the complete Evidence URL below in your browser.",
            f"2. These links reopen the exporting deployment at {evidence_base_url}. Sign in if deployment protection requires it.",
            "3. Check the evidence page shows the same release, rules, month and scope as this report. Missing or mismatched releases are rejected, never replaced with current data."]
    for stock in report["stocks"]:
        lines += [f"## {stock['name']} ({stock['isin']})", f"Status: {stock['status']}",
                  f"Fund houses net adding/reducing shares: {available(stock['houses_buying'])}/{available(stock['houses_selling'])}",
                  f"Net share change: {stock['net_share_change'] if stock['net_share_change'] is not None else 'unavailable'}",
                  f"Estimated net trading value (₹ lakh): {stock['net_flow_lakhs'] if stock['net_flow_lakhs'] is not None else 'unavailable'}",
                  f"Individual-fund additions/exits: {available(stock['additions'])}/{available(stock['exits'])}",
                  f"Evidence: [Open matching-release evidence]({evidence_base_url}{stock['evidence_url']})", f"Review: {stock['review_status']}"]
        for change in stock["largest_compared_changes"]:
            lines.append(f"- Largest compared change: {change['fund']}; {change['shares']} shares; ₹ lakh {available(change['flow_lakhs'])}")
        for house, c in stock["coverage"].items():
            lines.append(f"- {house}: {c['coverage_state']}; expected {c['expected'] if c['expected'] is not None else 'unknown'}, loaded {c['loaded']}, validated {c['validated_count']}, compared {c['compared']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
