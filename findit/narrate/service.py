"""Explicit batch generation and read-only, freshness-checked summary access."""
from __future__ import annotations

from datetime import datetime, timezone
import re

from .facts import build_snapshot, render_plan

RULES_VERSION = "rules-v1"
_SUMMARY_SCHEMA = """CREATE TABLE IF NOT EXISTS fund_summaries (
    scheme_id INTEGER NOT NULL, report_month TEXT NOT NULL,
    summary_text TEXT, generated_by TEXT, generated_at TEXT,
    source_data_hash TEXT, model_version TEXT,
    PRIMARY KEY (scheme_id, report_month))"""


def validate_month(month):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must use YYYY-MM with a valid month")
    if int(month[:4]) < 1:
        raise ValueError("month must use a positive year")
    return month


def selected_scheme_ids(conn, month, scheme_ids=None):
    validate_month(month)
    if scheme_ids is None:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        sources = [name for name in ("mf_holdings_monthly", "mf_holding_deltas")
                   if name in tables]
        if not sources:
            return []
        # Include full exits even when the current snapshot has no ISIN rows.
        sql = " UNION ".join(
            f"SELECT scheme_id FROM {name} WHERE report_month=?" for name in sources)
        return [row[0] for row in conn.execute(sql + " ORDER BY scheme_id", [month] * len(sources))]
    ids = list(scheme_ids)
    if any(isinstance(sid, bool) or not isinstance(sid, int) or sid <= 0 for sid in ids):
        raise ValueError("scheme IDs must be positive integers")
    ids = sorted(set(ids))
    for sid in ids:
        if not conn.execute("SELECT 1 FROM schemes WHERE scheme_id=?", (sid,)).fetchone():
            raise ValueError(f"No scheme with scheme_id={sid}")
    return ids


def _cached(conn, snapshot):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='fund_summaries'").fetchone():
        return None
    row = conn.execute(
        "SELECT summary_text, generated_by, model_version, source_data_hash "
        "FROM fund_summaries WHERE scheme_id=? AND report_month=?",
        (snapshot.scheme_id, snapshot.report_month)).fetchone()
    if row is None or not snapshot.eligible or row[3] != snapshot.source_hash:
        return None
    if not isinstance(row[0], str) or not row[0].strip():
        return None
    recognized = ((row[1] == "rules" and row[2] == RULES_VERSION)
                  or (row[1] == "zai" and isinstance(row[2], str)
                      and re.fullmatch(r"zai:[A-Za-z0-9_.-]+:editorial-v1", row[2])))
    if not recognized:
        return None
    return {"text": row[0], "generated_by": row[1], "model_version": row[2],
            "cached": True, "source_data_hash": row[3]}


def _fallback(snapshot):
    return {"text": snapshot.fallback_text, "generated_by": "rules",
            "model_version": RULES_VERSION, "cached": False,
            "source_data_hash": snapshot.source_hash}


def get_summary(conn, scheme_id, month):
    """Read current cached text or deterministic fallback; never writes or calls AI."""
    selected_scheme_ids(conn, month, [scheme_id])
    snapshot = build_snapshot(conn, scheme_id, month)
    return {**(_cached(conn, snapshot) or _fallback(snapshot)),
            "eligible": snapshot.eligible, "reason": snapshot.reason}


def generate_batch(conn, month, scheme_ids=None, provider=None, force=False):
    """Generate eligible summaries, committing each only if its source stayed current."""
    ids = selected_scheme_ids(conn, month, scheme_ids)
    if conn.in_transaction:
        raise ValueError("finish the existing transaction before generating summaries")
    desired_by = "rules" if provider is None else "zai"
    desired_version = RULES_VERSION if provider is None else provider.model_version
    conn.execute(_SUMMARY_SCHEMA)
    conn.commit()
    reports = []
    for sid in ids:
        snapshot = build_snapshot(conn, sid, month)
        if not snapshot.eligible:
            reports.append({"scheme_id": sid, "status": "skipped",
                            "reason": snapshot.reason, **_fallback(snapshot)})
            continue
        cached = _cached(conn, snapshot)
        if (not force and cached and cached["generated_by"] == desired_by
                and cached["model_version"] == desired_version):
            reports.append({"scheme_id": sid, "status": "cached", "reason": None, **cached})
            continue
        result = _fallback(snapshot)
        status, reason = "generated", None
        if provider is not None:
            try:
                plan = provider.generate(snapshot.prompt)
                text = render_plan(snapshot, plan)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("empty narration")
                result.update(text=text, generated_by=desired_by, model_version=desired_version)
            except Exception:
                # Never persist/print raw exceptions: HTTP errors may contain secrets.
                status, reason = "fallback", "provider_or_plan_failed"
        # No transaction was open during the provider call. Lock before the
        # freshness recheck so ingestion cannot race between checking and saving.
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = build_snapshot(conn, sid, month)
            if not current.eligible or current.source_hash != snapshot.source_hash:
                conn.rollback()
                reports.append({"scheme_id": sid, "status": "skipped",
                                "reason": "source_changed", **_fallback(current)})
                continue
            conn.execute(
                "INSERT INTO fund_summaries "
                "(scheme_id, report_month, summary_text, generated_by, generated_at, "
                "source_data_hash, model_version) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scheme_id, report_month) DO UPDATE SET "
                "summary_text=excluded.summary_text, generated_by=excluded.generated_by, "
                "generated_at=excluded.generated_at, source_data_hash=excluded.source_data_hash, "
                "model_version=excluded.model_version",
                (sid, month, result["text"], result["generated_by"],
                 datetime.now(timezone.utc).isoformat(), snapshot.source_hash,
                 result["model_version"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        reports.append({"scheme_id": sid, "status": status, "reason": reason, **result})
    return reports
