"""Read-only, deterministic facts for constrained fund-summary narration.

The model may order facts and select trusted phrasings. It cannot supply prose,
numbers, names, or directions that get published.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import sqlite3
from typing import Any

import fallback_summary


POLICY_VERSION = "trusted-summary-facts-v1"


@dataclass(frozen=True)
class SummarySnapshot:
    scheme_id: int
    report_month: str
    source_hash: str
    fallback_text: str
    eligible: bool
    reason: str | None
    prompt: dict


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _canonical(value: Any) -> Any:
    """Make bad numeric inputs hashable without allowing non-JSON numbers."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite_float": repr(value)}
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(_canonical(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _sorted(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=_json)


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _variants(sentence: str) -> list[str]:
    # Anchored replacements never rewrite a stock name or any computed figure.
    for prefix, alternate in (
        ("Opened ", "Started "),
        ("Added to ", "Increased holdings in "),
        ("Trimmed ", "Reduced holdings in "),
        ("Fully exited: ", "Closed positions in: "),
        ("No material changes versus the previous month.",
         "There were no material changes versus the previous month."),
    ):
        if sentence.startswith(prefix):
            return [sentence, alternate + sentence[len(prefix):]]
    return [sentence]


def build_snapshot(conn: sqlite3.Connection, scheme_id: int,
                   report_month: str) -> SummarySnapshot:
    """Capture source data and trusted wording without migrations or writes."""
    scheme_rows = _rows(conn, "SELECT * FROM schemes WHERE scheme_id = ?", (scheme_id,))
    if not scheme_rows:
        raise ValueError(f"No scheme with scheme_id={scheme_id}")
    scheme = scheme_rows[0]
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    )}
    stocks = {}
    deltas = []
    if "mf_holding_deltas" in tables:
        deltas = _rows(conn, "SELECT * FROM mf_holding_deltas "
                       "WHERE scheme_id = ? AND report_month = ?",
                       (scheme_id, report_month))
    if deltas and "stocks" in tables:
        isins = sorted({row["isin"] for row in deltas})
        placeholders = ",".join("?" for _ in isins)
        stocks = {row["isin"]: row for row in _rows(
            conn, f"SELECT * FROM stocks WHERE isin IN ({placeholders})", tuple(isins)
        )}
    months = {report_month}
    months.update(row["prev_month"] for row in deltas if row.get("prev_month"))
    holdings, statuses = [], []
    for month in sorted(months):
        if "mf_holdings_monthly" in tables:
            holdings.extend(_rows(conn, "SELECT * FROM mf_holdings_monthly "
                                  "WHERE scheme_id = ? AND report_month = ?",
                                  (scheme_id, month)))
        if "scheme_month_status" in tables:
            statuses.extend(_rows(conn, "SELECT * FROM scheme_month_status "
                                  "WHERE scheme_id = ? AND report_month = ?",
                                  (scheme_id, month)))
    payload = {
        "policy_version": POLICY_VERSION,
        "scheme_id": scheme_id, "report_month": report_month,
        "scheme": scheme, "deltas": _sorted(deltas),
        "stocks": _sorted(list(stocks.values())),
        "holdings": _sorted(holdings), "statuses": _sorted(statuses),
    }
    header = f"{scheme['scheme_name']} ({scheme['amc_name']}) — {report_month} portfolio changes:"
    current_status = next((row.get("status") for row in statuses
                           if row["report_month"] == report_month), None)
    quarantined = any(row.get("status") == "quarantined" for row in statuses)
    equity = [row for row in deltas
              if stocks.get(row["isin"], {}).get("instrument_type") == "equity"]
    unknown_type = any(stocks.get(row["isin"], {}).get("instrument_type") is None
                       for row in deltas)
    required = ("qty_change", "value_change_lakhs", "pct_nav_change")
    optional = ("flow_lakhs", "price_effect_lakhs")
    invalid = any(
        any(not _finite(row.get(column)) for column in required)
        or any(row.get(column) is not None and not _finite(row[column])
               for column in optional)
        or row.get("action") not in {"new", "added", "trimmed", "exited", "unchanged"}
        for row in equity
    )
    if quarantined:
        reason = "quarantined"
    elif invalid:
        reason = "nonfinite_or_invalid_data"
    elif current_status not in {"ok", "validated"}:
        reason = "unvalidated"
    elif unknown_type:
        reason = "unknown_instrument_type"
    elif not equity:
        reason = "no_data"
    else:
        reason = None

    if quarantined or invalid:
        explanation = ("the current or referenced previous month is quarantined"
                       if quarantined else "holding-change data contains invalid numeric values or actions")
        fallback = (f"{scheme['scheme_name']} ({scheme['amc_name']}) — {report_month} "
                    f"data withheld: {explanation}. Not zero activity — "
                    "the numbers did not pass checks.")
    elif "mf_holding_deltas" in tables and "stocks" in tables:
        fallback = fallback_summary.build_summary(conn, scheme_id, report_month)
    else:
        fallback = (f"No holding-change data available for {scheme['scheme_name']} in "
                    f"{report_month} yet (data may not have been ingested for "
                    "this month, or this is the first month tracked).")
    facts = []
    if reason is None:
        lines = fallback.splitlines()
        header = lines[0]
        for line in lines[1:]:
            sentence = line.removeprefix("- ").strip()
            if sentence:
                facts.append({"id": f"f{len(facts)}", "variants": _variants(sentence)})
    # Wording is material too: a change to canonical templates must invalidate
    # cached summaries even if the underlying holdings are identical.
    payload.update(fallback_text=fallback, header=header, facts=facts)
    source_hash = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
    return SummarySnapshot(scheme_id, report_month, source_hash, fallback,
                           reason is None, reason, {"header": header, "facts": facts})


def render_plan(snapshot: SummarySnapshot, plan: dict) -> str:
    """Accept exactly one trusted variant of every fact, in a model-chosen order."""
    if not snapshot.eligible:
        raise ValueError("Snapshot is not eligible for AI narration")
    if type(plan) is not dict or set(plan) != {"sentences"}:
        raise ValueError("Plan must contain only sentences")
    sentences = plan["sentences"]
    if type(sentences) is not list:
        raise ValueError("sentences must be a list")
    facts = {fact["id"]: fact["variants"] for fact in snapshot.prompt["facts"]}
    seen, rendered = set(), []
    for entry in sentences:
        if type(entry) is not dict or set(entry) != {"fact_id", "variant"}:
            raise ValueError("Each sentence must contain only fact_id and variant")
        fact_id, variant = entry["fact_id"], entry["variant"]
        if type(fact_id) is not str or fact_id not in facts or fact_id in seen:
            raise ValueError("Unknown or repeated fact_id")
        if type(variant) is not int or not 0 <= variant < len(facts[fact_id]):
            raise ValueError("Invalid variant index")
        seen.add(fact_id)
        rendered.append(facts[fact_id][variant])
    if seen != set(facts):
        raise ValueError("Every fact must appear exactly once")
    return snapshot.prompt["header"] + "\n\n" + " ".join(rendered)
