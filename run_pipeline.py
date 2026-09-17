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
buying the same thing" signal), and prints a fallback summary for
every scheme that has data for --curr.

Nothing here calls an LLM. This is the fully rule-based baseline —
wire GLM 5.3 in afterwards to narrate on top of build_summary()'s
underlying numbers (build plan §7), not to replace this file.
"""
import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import db
import delta_calculator
import consensus_signals
import fallback_summary
from findit.store.validation_gate import (
    validate_holdings_month,
    validate_implied_price_cv,
)

# Hard NAV-sum bounds for the gate. validate_holdings_month's own NAV check
# is warn-only (partial sheets are real), but a sum this far outside a sane
# range means the numbers don't add up — quarantine, don't guess.
NAV_QUARANTINE_MIN = 50.0
NAV_QUARANTINE_MAX = 150.0


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


def run_validation_gate(conn, touched: dict) -> dict:
    """Validate each touched (scheme_id, month); persist statuses.

    touched: {(scheme_id, month): {"files": [...], "hashes": [...]}}.
    Returns the run-report dict (also persisted to ingest_runs)."""
    outcomes = []
    for (scheme_id, month) in sorted(touched):
        info = conn.execute(
            "SELECT amc_name, scheme_name FROM schemes WHERE scheme_id = ?",
            (scheme_id,),
        ).fetchone()
        amc, scheme = (info[0], info[1]) if info else ("?", "?")
        curr_df = _holdings_df(conn, scheme_id, month)
        prev_month = _prev_month(conn, scheme_id, month)
        prev_df = _holdings_df(conn, scheme_id, prev_month) if prev_month else None
        try:
            ca_rows = conn.execute(
                "SELECT isin FROM corporate_actions "
                "WHERE confirmed = 1 AND effective_month = ?",
                (month,),
            ).fetchall()
            ca_isins = {str(r[0]) for r in ca_rows}
        except Exception:
            ca_isins = set()
        try:
            result = validate_holdings_month(curr_df, prev_df, ca_isins)
            issues = list(result.get("issues", []))
            passed = bool(result.get("passed", True))
        except Exception as exc:
            issues = [{
                "code": "gate_crashed", "severity": "error",
                "message": f"validation gate raised {exc!r}",
            }]
            passed = False
        # Hard NAV-sum gate: far outside sane range -> quarantine.
        try:
            nav_sum = float(
                pd.to_numeric(curr_df["pct_nav"], errors="coerce").dropna().sum()
            )
        except Exception:
            nav_sum = float("nan")
        if nav_sum != nav_sum or not (
            NAV_QUARANTINE_MIN <= nav_sum <= NAV_QUARANTINE_MAX
        ):
            issues.append({
                "code": "nav_sum_quarantine", "severity": "error",
                "message": (
                    f"pct_nav sum {nav_sum:.2f} far outside sane range "
                    f"[{NAV_QUARANTINE_MIN:.0f}, {NAV_QUARANTINE_MAX:.0f}]"
                ),
                "nav_sum": nav_sum,
            })
            passed = False
        status = "ok" if passed else "quarantined"
        report_json = json.dumps({
            "issues": issues, "prev_month": prev_month,
            "nav_sum": nav_sum,
        })
        hashes = sorted(set(touched[(scheme_id, month)]["hashes"]))
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
        })
    conn.commit()

    # Cross-scheme implied-price check (warn-only, never quarantines):
    # same ISIN held by >=2 schemes should imply the same month-end price.
    price_warnings = []
    months = sorted({m for (_, m) in touched})
    for month in months:
        try:
            all_hold = pd.read_sql_query(
                "SELECT isin, quantity, market_value_lakhs "
                "FROM mf_holdings_monthly WHERE report_month = ?",
                conn, params=(month,),
            )
        except Exception:
            continue
        for isin, grp in all_hold.groupby("isin"):
            if len(grp) < 2:
                continue
            px = (grp["market_value_lakhs"] / grp["quantity"]).tolist()
            res = validate_implied_price_cv(
                prices=px, n_schemes=len(grp), label=f"{isin} {month}")
            for issue in res.get("issues", []):
                price_warnings.append({
                    "isin": str(isin), "report_month": month, **issue})
    return {"scheme_months": outcomes, "price_warnings": price_warnings}


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
            id_map = dict(conn.execute(
                "SELECT amc_name || '||' || scheme_name, scheme_id FROM schemes"
            ).fetchall())
            for _, row in df.iterrows():
                key = f"{row['amc_name']}||{row['scheme_name']}"
                sid = id_map.get(key)
                if sid is None:
                    continue
                entry = touched.setdefault(
                    (int(sid), str(row["report_month"])),
                    {"files": [], "hashes": []},
                )
                entry["files"].append(str(csv_path))
                entry["hashes"].append(file_hash)

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


if __name__ == "__main__":
    main()
