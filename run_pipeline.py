"""
run_pipeline.py — the monthly driver script. Once you have real AMFI
files, this is the one command you actually run each month.

USAGE (after parsing each AMC's file with amfi_mf_parser.py):
  python run_pipeline.py \
      --load hdfc_march.parsed.csv sbi_march.parsed.csv \
      --load hdfc_april.parsed.csv sbi_april.parsed.csv \
      --prev 2026-03 --curr 2026-04

This loads every given CSV into tracker.db, computes deltas between
--prev and --curr, prints the cross-fund consensus table (the "who's
buying the same thing" signal), joins it against quarterly FII/DII
shareholding on ISIN for the full MF+FII overlap view (Phase 1), and
prints a fallback summary for every scheme that has data for --curr.

Nothing here calls an LLM. This is the fully rule-based baseline —
wire GLM 5.3 in afterwards to narrate on top of build_summary()'s
underlying numbers (build plan §7), not to replace this file.
"""
import argparse
import hashlib
import json
import math
from statistics import median
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import db
import delta_calculator
import consensus_signals
import fallback_summary
from findit.core.corporate_actions import detect_candidate
from findit.store.validation_gate import (
    validate_holdings_month,
    validate_implied_price_cv,
)

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _holdings_df(conn, scheme_id: int, month: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT isin, quantity, market_value_lakhs, pct_nav "
        "FROM mf_holdings_monthly WHERE scheme_id = ? AND report_month = ?",
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
    """Join MF consensus against quarterly FII/DII shareholding (Phase 1).

    Uses ``curr`` as a no-lookahead cutoff so only quarters with
    ``quarter_end <= <curr month-end>`` are ranked. Missing filings stay
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
    except Exception:
        return {"joined": consensus, "common": consensus.iloc[0:0], "status": "unavailable"}
    try:
        common = joined[joined["is_common_with_fii_increase"]]
    except (KeyError, TypeError):
        return {"joined": joined, "common": joined.iloc[0:0], "status": "unavailable"}
    return {"joined": joined, "common": common, "status": "ok"}


def _format_overlap_row(row) -> str:
    isin = row.get("isin", "?")
    name = row.get("stock_name", "")
    mf_net = row.get("net_amc_count", 0)
    fii_dir = row.get("fii_direction", "no_data")
    dii_dir = row.get("dii_direction", "no_data")
    qend = row.get("shareholding_quarter_end")
    prev_qend = row.get("previous_shareholding_quarter_end")
    stale = bool(row.get("shareholding_stale", False))
    stale_mark = " [stale]" if stale else ""
    return (
        f"  {name} | {isin} | MF net {mf_net} | "
        f"FII {fii_dir} / DII {dii_dir} | "
        f"as-of {qend} (prev {prev_qend}){stale_mark}"
    )


def _overlap_summary(joined, common, status: str) -> dict:
    """Auditable counts for the ingest_runs report (JSON-safe)."""
    if status != "ok" or joined is None or getattr(joined, "empty", True):
        return {"status": status, "common_count": 0}
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
        status = "ok" if passed else "quarantined"
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


def _track_source(touched: dict, conn, df: pd.DataFrame, path: Path, file_hash: str):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--load", nargs="+", action="append", default=[],
        metavar="CSV",
        help="One or more parsed CSVs to load (repeat --load for each batch)",
    )
    ap.add_argument("--prev", required=True, help="Previous report month, e.g. 2026-03")
    ap.add_argument("--curr", required=True, help="Current report month, e.g. 2026-04")
    ap.add_argument("--db", default="tracker.db", help="SQLite file to use")
    args = ap.parse_args()

    conn = db.get_connection(args.db)

    # Load + record which (scheme, month) each file touched.
    touched: dict = {}
    for batch in args.load:
        for csv_path in batch:
            path = Path(csv_path)
            n = db.load_parsed_csv(conn, path)
            print(f"Loaded {n} rows from {csv_path}")
            file_hash = _file_sha256(path)
            df = pd.read_csv(path)
            _track_source(touched, conn, df, path, file_hash)

    # Classify freshly loaded schemes: backfill only runs at connect time
    # (before these inserts), so without this every new scheme stays NULL
    # and the consensus filter (is_active_equity = 1) silently drops it.
    db.backfill_is_active_equity(conn)

    # Validation gate: the second gate after the parser's fail-loud column
    # check. A file that parses fine can still produce numbers that don't
    # add up — those months are quarantined, never silently published.
    report = run_validation_gate(conn, touched)
    ok = [o for o in report["scheme_months"] if o["status"] == "ok"]
    bad = [o for o in report["scheme_months"] if o["status"] != "ok"]
    run_id = uuid.uuid4().hex[:12]
    started_at = datetime.now(timezone.utc).isoformat()
    run_status = "completed_with_quarantine" if bad else "completed"
    conn.execute(
        "INSERT OR REPLACE INTO ingest_runs "
        "(run_id, started_at, status, validation_report_json) "
        "VALUES (?, ?, ?, ?)",
        (run_id, started_at, run_status, json.dumps({
            "prev": args.prev, "curr": args.curr,
            "files": sorted({f for v in touched.values() for f in v["files"]}),
            "ok_count": len(ok), "quarantined_count": len(bad),
            "scheme_months": report["scheme_months"],
            "price_warnings": report["price_warnings"],
            "corporate_action_candidates": report["corporate_action_candidates"],
        })),
    )
    conn.commit()

    print(f"\n=== Validation gate: {len(ok)} ok, {len(bad)} quarantined ===")
    if bad:
        print("QUARANTINED (excluded from consensus and summaries):")
        for o in bad:
            errs = [i.get("message", i.get("code", "?")) for i in o["issues"]
                    if i.get("severity") == "error"] or ["failed checks"]
            print(f"  - {o['amc_name']} | {o['scheme_name']} | "
                  f"{o['report_month']}: {'; '.join(errs)}")
    else:
        print("All loaded scheme-months passed.")

    deltas = delta_calculator.compute_deltas(conn, args.prev, args.curr)
    delta_calculator.persist_deltas(conn, deltas)
    print(f"\nComputed {len(deltas)} deltas for {args.prev} -> {args.curr}")

    print(f"\n=== Cross-fund consensus, {args.curr} (active equity only) ===")
    consensus = consensus_signals.compute_consensus(conn, args.curr)
    if consensus.empty:
        print("(no signals yet)")
    else:
        print(consensus.to_string(index=False))

    # Phase 1: MF+FII/DII overlap on ISIN. Missing filings stay no_data
    # (never zero); quarterly filings may be stale relative to the MF month.
    print(f"\n=== MF + FII/DII overlap, {args.curr} ===")
    overlap = build_overlap_view(conn, consensus, args.curr)
    joined, common, overlap_status = (
        overlap["joined"], overlap["common"], overlap["status"],
    )
    if overlap_status == "no_consensus":
        print("(no MF signals, so no overlap to report)")
    elif overlap_status == "unavailable":
        print("(shareholding data unavailable — showing MF-only consensus above)")
    elif common.empty:
        print("(no MF+FII/DII common increases this month)")
    else:
        print("(MF net buying + FII-or-DII quarterly increase; stale quarters flagged)")
        for _, row in common.iterrows():
            print(_format_overlap_row(row))
    try:
        current_report = json.loads(conn.execute(
            "SELECT validation_report_json FROM ingest_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0])
        current_report["mf_fii_overlap"] = _overlap_summary(joined, common, overlap_status)
        conn.execute(
            "UPDATE ingest_runs SET validation_report_json = ? WHERE run_id = ?",
            (json.dumps(current_report), run_id),
        )
        conn.commit()
    except Exception:
        pass
    try:
        passive = conn.execute(
            "SELECT DISTINCT s.amc_name, s.scheme_name FROM schemes s "
            "JOIN mf_holding_deltas d ON d.scheme_id = s.scheme_id "
            "WHERE d.report_month = ? AND s.is_active_equity = 0 "
            "ORDER BY 1, 2",
            (args.curr,),
        ).fetchall()
    except Exception:
        passive = []
    if passive:
        print(f"\nExcluded {len(passive)} passive/debt scheme(s) from consensus "
              f"(pattern heuristic, still shown in own summaries):")
        for amc, scheme in passive:
            print(f"  - {amc} | {scheme}")

    print(f"\n=== Monthly summaries, {args.curr} ===")
    scheme_ids = conn.execute("SELECT DISTINCT scheme_id FROM mf_holding_deltas WHERE report_month = ?", (args.curr,)).fetchall()
    for (scheme_id,) in scheme_ids:
        print()
        print(fallback_summary.build_summary(conn, scheme_id, args.curr))

    print("\n=== Corporate-action candidates (unconfirmed; no flow adjustments) ===")
    if not report["corporate_action_candidates"]:
        print("(none passed both peer-agreement and inverse-price checks)")
    for candidate in report["corporate_action_candidates"]:
        print(
            f"  {candidate['isin']} | {candidate['effective_month']} | "
            f"quantity {candidate['qty_ratio']:.4f}x | "
            f"price {candidate['price_ratio']:.4f}x | "
            f"target {candidate['target_qty_ratio']:g}x | confirmed=0"
        )


if __name__ == "__main__":
    main()
