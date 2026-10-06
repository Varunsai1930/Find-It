"""The monthly run's steps: provenance, the validation gate, the FII/DII overlap.

``findit.cli.pipeline`` runs them in order and prints the results. Nothing
here calls an LLM; every number is computed from the stored holdings.
"""
import hashlib
import json
import math
from statistics import median
from pathlib import Path

import pandas as pd

from findit.store import db, queries
from findit.core import consensus_signals
from findit.core.corporate_actions import detect_candidate
from findit.store.validation_gate import (
    validate_holdings_month,
    validate_implied_price_cv,
)

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _holdings_df(conn, scheme_id: int, month: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT h.isin, h.quantity, h.market_value_lakhs, h.pct_nav, s.instrument_type "
        "FROM mf_holdings_monthly h LEFT JOIN stocks s ON s.isin=h.isin "
        "WHERE h.scheme_id = ? AND h.report_month = ?",
        conn, params=(scheme_id, month),
    )


def _prev_month(conn, scheme_id: int, month: str):
    row = conn.execute(
        "SELECT MAX(report_month) FROM mf_holdings_monthly "
        "WHERE scheme_id = ? AND report_month < ?",
        (scheme_id, month),
    ).fetchone()
    return row[0] if row and row[0] else None


def _peer_ratios(conn, prev_month: str, month: str) -> dict:
    """Quantity-ratio medians of the *other* schemes holding each ISIN.

    Build once per month pair, using matched, positive quantities only.
    Single-holder stocks have no peer evidence.
    """
    pairs = pd.read_sql_query(
        "SELECT c.scheme_id, c.isin, c.quantity / p.quantity AS ratio "
        "FROM mf_holdings_monthly c JOIN mf_holdings_monthly p "
        "ON p.scheme_id = c.scheme_id AND p.isin = c.isin "
        "WHERE c.report_month = ? AND p.report_month = ? "
        "AND c.quantity > 0 AND p.quantity > 0",
        conn, params=(month, prev_month),
    )
    medians = {}
    for isin, group in pairs.groupby("isin"):
        holders = [(int(sid), float(ratio)) for sid, ratio in
                   group[["scheme_id", "ratio"]].itertuples(index=False, name=None)
                   if math.isfinite(ratio)]
        if len(holders) < 2:
            continue
        for sid, _ in holders:
            medians.setdefault(sid, {})[str(isin)] = median(
                ratio for other_sid, ratio in holders if other_sid != sid
            )
    return medians


def _record_candidates(conn, issues, prev_df, curr_df, month: str) -> list[dict]:
    """Require both peer agreement and inverse price evidence; never confirm."""
    if prev_df is None or prev_df.empty or curr_df.empty:
        return []
    prev = prev_df.set_index("isin")
    curr = curr_df.set_index("isin")
    candidates = []
    for issue in issues:
        if issue["code"] != "qty_ratio_corporate_action_candidate":
            continue
        isin = issue["isin"]
        before, after = prev.loc[isin], curr.loc[isin]
        try:
            pq, pv, cq, cv = map(float, (
                before["quantity"], before["market_value_lakhs"],
                after["quantity"], after["market_value_lakhs"],
            ))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in (pq, pv, cq, cv)):
            continue
        candidate = detect_candidate(pq, pv / pq, cq, cv / cq)
        if candidate is None:
            continue
        # Preserve a prior reviewed/confirmed record and make reruns idempotent.
        conn.execute(
            "INSERT OR IGNORE INTO corporate_actions "
            "(isin, effective_month, kind, ratio, detected_by, confirmed) "
            "VALUES (?, ?, ?, ?, 'peer_ratio_agreement', 0)",
            (isin, month, candidate["type"], candidate["target_qty_ratio"]),
        )
        stored = conn.execute(
            "SELECT confirmed, detected_by FROM corporate_actions "
            "WHERE isin = ? AND effective_month = ?", (isin, month),
        ).fetchone()
        if stored[0] == 0 and stored[1] == "peer_ratio_agreement":
            candidates.append({
                "isin": isin, "effective_month": month, **candidate,
                "peer_median_ratio": issue.get("peer_median_ratio"),
                "confirmed": 0, "detected_by": "peer_ratio_agreement",
            })
    return candidates


def build_overlap_view(conn, consensus: pd.DataFrame, curr: str) -> dict:
    """Join MF consensus against quarterly FII/DII shareholding.

    Uses ``curr`` as a no-lookahead cutoff: only filings published by the
    day ``curr``'s MF portfolios became public are ranked. Missing filings stay
    ``no_data`` (never zero); stale quarters are flagged by the join.
    Never raises on missing/empty shareholding data — returns the
    consensus unchanged with an ``unavailable`` status instead.
    """
    if consensus is None or consensus.empty:
        return {"joined": consensus, "common": consensus, "status": "no_consensus"}
    try:
        joined = consensus_signals.join_shareholding_increase(
            conn, consensus, as_of_month=curr,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return {"joined": consensus, "common": consensus.iloc[0:0],
                "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    try:
        common = joined[joined["is_common_with_fii_increase"]]
    except (KeyError, TypeError) as exc:
        return {"joined": joined, "common": joined.iloc[0:0],
                "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {"joined": joined, "common": common, "status": "ok", "error": None}


def overlap_summary(joined, common, status: str, error: str | None = None) -> dict:
    """Auditable counts for the ingest_runs report (JSON-safe)."""
    if status != "ok" or joined is None or getattr(joined, "empty", True):
        summary = {"status": status, "common_count": 0}
        if error:
            summary["error"] = error
        return summary
    try:
        stale_count = int(joined["shareholding_stale"].fillna(True).sum())
    except (KeyError, TypeError, ValueError):
        stale_count = 0
    try:
        missing_count = int((joined["shareholding_quarter_end"].isna()).sum())
    except (KeyError, TypeError, AttributeError):
        missing_count = 0
    return {
        "status": status,
        "common_count": int(len(common)),
        "consensus_count": int(len(joined)),
        "stale_count": stale_count,
        "missing_shareholding_count": missing_count,
    }


def run_validation_gate(conn, touched: dict) -> dict:
    """Validate touched scheme-months and persist reports, including provenance.

    touched values contain files/hashes and optional dropped_non_isin_count
    and dropped_non_isin_pct_nav. Legacy CSVs leave provenance unknown.
    """
    outcomes = []
    peer_cache = {}
    candidates = {}
    for (scheme_id, month) in sorted(touched):
        info = conn.execute(
            "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?",
            (scheme_id,),
        ).fetchone()
        amc, scheme = (info[0], info[1]) if info else ("?", "?")
        curr_df = _holdings_df(conn, scheme_id, month)
        prev_month = _prev_month(conn, scheme_id, month)
        prev_df = _holdings_df(conn, scheme_id, prev_month) if prev_month else None
        ca_rows = conn.execute(
            "SELECT isin FROM corporate_actions "
            "WHERE confirmed = 1 AND effective_month = ?", (month,),
        ).fetchall()
        ca_isins = {str(r[0]) for r in ca_rows}
        peers = {}
        if prev_month:
            pair = (prev_month, month)
            if pair not in peer_cache:
                peer_cache[pair] = _peer_ratios(conn, prev_month, month)
            peers = peer_cache[pair].get(scheme_id, {})
        try:
            result = validate_holdings_month(
                curr_df, prev_df, ca_isins, peer_median_ratios=peers,
            )
            issues = list(result.get("issues", []))
            passed = bool(result.get("passed", True))
        except Exception as exc:
            issues = [{
                "code": "gate_crashed", "severity": "error",
                "message": f"validation gate raised {exc!r}",
            }]
            passed = False
        for candidate in _record_candidates(conn, issues, prev_df, curr_df, month):
            candidates[(candidate["isin"], month)] = candidate
        nav_sum = float(pd.to_numeric(curr_df["pct_nav"], errors="coerce").sum())
        source = touched[(scheme_id, month)]
        provenance = {
            "dropped_non_isin_count": source.get("dropped_non_isin_count"),
            "dropped_non_isin_pct_nav": source.get("dropped_non_isin_pct_nav"),
        }
        status = queries.STATUS_OK if passed else queries.STATUS_QUARANTINED
        report_json = json.dumps({
            "issues": issues, "prev_month": prev_month,
            "nav_sum": nav_sum, **provenance,
        })
        hashes = sorted(set(source["hashes"]))
        conn.execute(
            """INSERT OR REPLACE INTO scheme_month_status
               (scheme_id, report_month, status, validation_report_json, source_data_hash)
               VALUES (?, ?, ?, ?, ?)""",
            (scheme_id, month, status, report_json,
             "+".join(hashes) if hashes else None),
        )
        outcomes.append({
            "scheme_id": scheme_id, "amc_name": amc, "scheme_name": scheme,
            "report_month": month, "status": status, "issues": issues,
            "nav_sum": nav_sum, **provenance,
        })
    conn.commit()

    # Cross-scheme implied-price check remains warn-only.
    price_warnings = []
    months = sorted({m for (_, m) in touched})
    for month in months:
        all_hold = pd.read_sql_query(
            "SELECT isin, quantity, market_value_lakhs "
            "FROM mf_holdings_monthly WHERE report_month = ?",
            conn, params=(month,),
        )
        for isin, grp in all_hold.groupby("isin"):
            if len(grp) < 2:
                continue
            px = (grp["market_value_lakhs"] / grp["quantity"]).tolist()
            res = validate_implied_price_cv(
                prices=px, n_schemes=len(grp), label=f"{isin} {month}")
            for issue in res.get("issues", []):
                price_warnings.append({
                    "isin": str(isin), "report_month": month, **issue})
    return {
        "scheme_months": outcomes, "price_warnings": price_warnings,
        "corporate_action_candidates": [candidates[key] for key in sorted(candidates)],
    }


def track_source(touched: dict, conn, df: pd.DataFrame, path: Path, file_hash: str):
    """Collect repeated per-scheme CSV metadata once, not once per holding."""
    for (amc, scheme, month), group in df.groupby(
        ["amc_name", "scheme_name", "report_month"]
    ):
        # Same resolution the loader used: an aliased/re-punctuated sheet
        # name must land on the scheme_id its holdings were just written to.
        sid = db.resolve_scheme_id(conn, amc, scheme, create=False)
        if sid is None:
            raise RuntimeError(
                f"no scheme_id for {amc} | {scheme} | {month} after loading -- "
                "this shouldn't happen"
            )
        entry = touched.setdefault((int(sid), str(month)), {"files": [], "hashes": []})
        entry["files"].append(str(path))
        entry["hashes"].append(file_hash)
        for column in ("dropped_non_isin_count", "dropped_non_isin_pct_nav"):
            if column not in group:
                entry[column] = None
                continue
            values = pd.to_numeric(group[column], errors="coerce").dropna().unique()
            if len(values) > 1:
                raise ValueError(f"Inconsistent {column} for {amc} | {scheme} | {month}")
            value = float(values[0]) if len(values) else None
            if value is not None and not math.isfinite(value):
                raise ValueError(f"Non-finite {column} for {amc} | {scheme} | {month}")
            # A reloaded scheme snapshot replaces earlier provenance, not sums it.
            entry[column] = int(value) if column.endswith("count") and value is not None else value
