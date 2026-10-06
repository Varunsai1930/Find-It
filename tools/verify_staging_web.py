"""Verify a pinned staging pair locally, without network or data writes.

Run with the deployment Python environment. This measures the local ASGI app,
not Vercel network latency or platform cold starts. Application caches begin
empty; the operating-system filesystem cache is outside this test's control.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
CONCURRENCY = 5
LIMITS = {"warm_core_p95_seconds": 5, "history_max_seconds": 8,
          "cold_max_seconds": 15, "response_bytes": 4_000_000}


@dataclass(frozen=True)
class Case:
    name: str
    path: str
    params: dict | None = None
    body: dict | None = None
    history: bool = False


def percentile95(values: list[float]) -> float:
    """Conservative nearest-rank p95; small samples never hide their maximum."""
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def protected_files(descriptor: dict, directory: Path, baseline: Path) -> list[Path]:
    paths = {directory / (item["release_id"] + suffix)
             for item in descriptor["releases"] for suffix in (".db", ".json")}
    if baseline.exists():
        data = json.loads(baseline.read_text())
        paths.update(ROOT / name for name in data["protected_files"])
    return sorted(paths)


def signatures(paths: list[Path]) -> dict[str, str]:
    from tools.prepare_vercel_release import file_sha256
    return {str(path): file_sha256(path) for path in paths}


def journals(paths: list[Path]) -> list[str]:
    return sorted(str(Path(str(path) + suffix)) for path in paths if path.suffix == ".db"
                  for suffix in ("-wal", "-shm", "-journal")
                  if Path(str(path) + suffix).exists())


def validate_pair(descriptor: dict, directory: Path) -> None:
    """A retained directory can contain other releases; only the pinned pair is read."""
    from tools.prepare_vercel_release import file_sha256, validate_release_contents
    for item in descriptor["releases"]:
        database = directory / (item["release_id"] + ".db")
        manifest = database.with_suffix(".json")
        if any(path.is_symlink() or not path.is_file() for path in (database, manifest)):
            raise ValueError("Pinned artifacts must be ordinary files")
        if (database.stat().st_size != item["database_bytes"] or
                file_sha256(database) != item["database_sha256"] or
                file_sha256(manifest) != item["manifest_sha256"]):
            raise ValueError("Pinned artifact byte verification failed")
    validate_release_contents(descriptor, directory)


def choose_inputs(database: Path, month: str, house: str | None) -> tuple[list[str], str, str]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        stocks = [row[0] for row in conn.execute(
            "SELECT h.isin FROM mf_holdings_monthly h JOIN stocks s USING(isin) "
            "WHERE h.report_month=? AND s.instrument_type='equity' "
            "GROUP BY h.isin ORDER BY COUNT(*) DESC,h.isin LIMIT 100", (month,))]
        if len(stocks) != 100:
            raise ValueError("The maximum 100-stock response gate requires 100 domestic equities")
        if house is None:
            row = conn.execute(
                "SELECT s.amc_name FROM mf_holdings_monthly h JOIN schemes s USING(scheme_id) "
                "WHERE h.report_month BETWEEN '2026-02' AND ? AND s.is_active_equity=1 "
                "GROUP BY s.amc_name ORDER BY COUNT(DISTINCT h.report_month) DESC,COUNT(*) DESC,s.amc_name LIMIT 1",
                (month,)).fetchone()
            if row is None:
                raise ValueError("No selected-house history is available")
            house = row[0]
        row = conn.execute(
            "SELECT h.isin FROM mf_holdings_monthly h JOIN schemes sch USING(scheme_id) "
            "JOIN stocks s USING(isin) WHERE sch.amc_name=? AND sch.is_active_equity=1 "
            "AND s.instrument_type='equity' AND h.report_month BETWEEN '2026-02' AND ? "
            "GROUP BY h.isin ORDER BY COUNT(DISTINCT h.report_month) DESC,COUNT(*) DESC,h.isin LIMIT 1",
            (house, month)).fetchone()
        if row is None:
            raise ValueError("The selected house has no domestic-equity history")
        return stocks, house, row[0]


def request_cases(descriptor: dict, stocks: list[str], house: str, history_stock: str) -> list[Case]:
    month, release = descriptor["month"], descriptor["current_release"]
    current = next(item for item in descriptor["releases"] if item["release_id"] == release)
    pin = {"release": release, "rules": current["rule_version"]}
    cases = [Case("readiness", "/health/ready"), Case("coverage_api", "/api/coverage"),
             Case("coverage_page", "/coverage", {"month": month}), Case("dashboard_default", "/")]
    for equity in (1, 0):
        for active in (1, 0):
            filters = {"equity_only": equity, "active_only": active}
            label = f"equity{equity}_active{active}"
            cases.append(Case("consensus_" + label, "/api/consensus/" + month, filters))
            for side in ("buy", "sell"):
                cases.append(Case("month_" + label + "_" + side, "/fragments/month/" + month,
                                  {**filters, "side": side, "limit": 0, "cols": "all"}))
    cases.extend([
        Case("stock_api", "/api/stock/" + stocks[0], {"month": month}),
        Case("stock_fragment", "/fragments/stock", {"q": stocks[0], "month": month}),
        Case("evidence_api", "/api/evidence/" + stocks[0], {"month": month, **pin}),
        Case("evidence_page", "/evidence/" + stocks[0], {"month": month, **pin}),
        Case("watchlist_100", "/api/watchlist", body={"month": month, "stocks": stocks,
                                                      "active_only": True, **pin}),
        Case("report_100", "/watchlist/report", body={"month": month, "stocks": stocks,
                                                      "active_only": True, **pin}),
        Case("history_api", "/api/history/" + history_stock,
             {"start": "2026-02", "end": month, "amc": house, **pin}, history=True),
        Case("history_page", "/history/" + history_stock,
             {"start": "2026-02", "end": month, "amc": house, **pin}, history=True),
    ])
    return cases


def size_only_cases(database: Path) -> list[Case]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        months = [row[0] for row in conn.execute(
            "SELECT DISTINCT report_month FROM mf_holdings_monthly ORDER BY report_month")]
    cases = []
    for month in months:
        for side in ("buy", "sell"):
            params = {"month": month, "equity_only": 0, "active_only": 0,
                      "cols": "all", "limit": 0, "side": side}
            cases.append(Case("full_dashboard_" + month + "_" + side, "/", params))
            cases.append(Case("full_fragment_" + month + "_" + side,
                              "/fragments/month/" + month, params))
    return cases


def sample(client, case: Case, phase: str, host: str, barrier=None) -> dict:
    if barrier is not None:
        barrier.wait()
    started = time.perf_counter()
    try:
        response = client.request("POST" if case.body else "GET", case.path,
                                  params=case.params, json=case.body)
        result = {"case": case.name, "phase": phase, "seconds": time.perf_counter() - started,
                  "status": response.status_code, "bytes": len(response.content),
                  "history": case.history, "checks": []}
        if case.body:
            result["checks"].append(response.headers.get("cache-control") == "private, no-store")
        if case.name == "report_100":
            result["checks"].append("https://" + host + "/evidence/" in response.text)
            result["checks"].append("Start your local" not in response.text)
            result["checks"].append(case.body["release"] in response.text)
            result["checks"].append(case.body["rules"] in response.text)
        if response.status_code == 200 and case.name in {"watchlist_100", "evidence_api", "history_api"}:
            expected = case.body if case.body else case.params
            payload = response.json()
            result["checks"].append(payload["release_id"] == expected["release"])
            result["checks"].append(payload["rule_version"] == expected["rules"])
            if case.name == "watchlist_100":
                result["checks"].append(len(payload["stocks"]) == 100)
            if case.name == "history_api":
                result["checks"].append(payload["cohort_count"] > 0 and len(payload["points"]) == 7)
        if response.status_code != 200:
            result["error"] = response.text[:1000]
        return result
    except Exception as exc:
        return {"case": case.name, "phase": phase, "seconds": time.perf_counter() - started,
                "status": None, "bytes": 0, "history": case.history, "checks": [False],
                "error": type(exc).__name__ + ": " + str(exc)[:500]}


def concurrent_samples(client, cases: list[Case], phase: str, host: str) -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        for offset in range(0, len(cases), CONCURRENCY):
            batch = cases[offset:offset + CONCURRENCY]
            barrier = threading.Barrier(len(batch))
            futures = [executor.submit(sample, client, case, phase, host, barrier) for case in batch]
            results.extend(future.result() for future in futures)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, default=ROOT / "deployment/releases.json")
    parser.add_argument("--release-dir", type=Path, default=ROOT / "deploy-data")
    parser.add_argument("--host", default=os.environ.get("VERCEL_URL", "findit-preview.vercel.app"))
    parser.add_argument("--house", help="Default: the active house with the longest available history")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--baseline", type=Path, default=ROOT / "real_data/readiness/vercel-staging/baseline.json")
    parser.add_argument("--output", type=Path, default=ROOT / "real_data/readiness/vercel-staging/web-verification.json")
    args = parser.parse_args(argv)
    if args.rounds < 3 or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*\.vercel\.app", args.host):
        parser.error("Use at least three warm rounds and an exact Vercel preview hostname")
    sys.path.insert(0, str(ROOT))
    from tools.prepare_vercel_release import load_descriptor
    descriptor = load_descriptor(args.descriptor)
    if descriptor["month"] != "2026-08":
        raise ValueError("This staging gate covers the approved February–August 2026 period")
    paths = protected_files(descriptor, args.release_dir, args.baseline)
    before = signatures(paths)
    before_journals = journals(paths)
    validate_pair(descriptor, args.release_dir)
    database = args.release_dir / (descriptor["current_release"] + ".db")
    stocks, house, history_stock = choose_inputs(database, descriptor["month"], args.house)
    cases = request_cases(descriptor, stocks, house, history_stock)
    cold_started = time.perf_counter()
    from fastapi.testclient import TestClient
    from findit.web.app import create_app
    app = create_app(database, args.release_dir, hosted=True, expected_month=descriptor["month"],
                     allowed_hosts=(args.host,))
    samples = []
    with TestClient(app, base_url="https://" + args.host, raise_server_exceptions=False) as client:
        startup_seconds = time.perf_counter() - cold_started
        cold_names = {"readiness", "dashboard_default", "consensus_equity0_active0", "evidence_api", "watchlist_100"}
        samples.extend(concurrent_samples(client, [case for case in cases if case.name in cold_names], "cold", args.host))
        print("Cold batch complete.", flush=True)
        for case in cases:
            result = sample(client, case, "smoke", args.host)
            samples.append(result)
            print(f"Smoke {case.name}: {result['status']}, {result['seconds']:.3f}s, {result['bytes']} bytes", flush=True)
        for number in range(args.rounds):
            samples.extend(concurrent_samples(client, cases, "warm", args.host))
            print(f"Warm round {number + 1}/{args.rounds} complete.", flush=True)
        for case in size_only_cases(database):
            result = sample(client, case, "size_only", args.host)
            samples.append(result)
            print(f"Size {case.name}: {result['status']}, {result['bytes']} bytes", flush=True)
    after = signatures(paths)
    after_journals = journals(paths)
    core = [item["seconds"] for item in samples if item["phase"] == "warm" and not item["history"]]
    history = [item["seconds"] for item in samples if item["history"]]
    cold = [startup_seconds + item["seconds"] for item in samples if item["phase"] == "cold"]
    endpoint_p95 = {case.name: percentile95([item["seconds"] for item in samples
                                            if item["case"] == case.name and item["phase"] == "warm"])
                    for case in cases if not case.history}
    gates = {"warm_core_p95": percentile95(core) <= LIMITS["warm_core_p95_seconds"],
             "each_core_endpoint_p95": max(endpoint_p95.values()) <= LIMITS["warm_core_p95_seconds"],
             "history_max": max(history) <= LIMITS["history_max_seconds"],
             "cold_max": max(cold) <= LIMITS["cold_max_seconds"],
             "response_size": max(item["bytes"] for item in samples) <= LIMITS["response_bytes"],
             "no_5xx": all(item["status"] is not None and item["status"] < 500 for item in samples),
             "successful_expected_responses": all(item["status"] == 200 and all(item["checks"]) for item in samples),
             "protected_bytes_unchanged": before == after,
             "no_journals": not before_journals and not after_journals}
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "release_id": descriptor["current_release"],
              "pinned_releases": [item["release_id"] for item in descriptor["releases"]],
              "month": descriptor["month"], "host": args.host, "house": house,
              "history_stock": history_stock, "watchlist_stocks": stocks, "concurrency": CONCURRENCY,
              "warm_rounds": args.rounds, "limits": LIMITS, "passed": all(gates.values()), "gates": gates,
              "metrics": {"startup_seconds": startup_seconds, "warm_core_p95_seconds": percentile95(core),
                          "core_endpoint_p95_seconds": endpoint_p95, "history_max_seconds": max(history),
                          "cold_max_seconds": max(cold), "max_response_bytes": max(item["bytes"] for item in samples)},
              "samples": samples, "protected_files": len(paths), "journals": after_journals,
              "limitations": ["Local ASGI timings exclude Vercel network latency and platform cold starts.",
                              "Application caches begin empty; operating-system cache state is uncontrolled.",
                              "Concurrent traffic uses five request threads on this machine."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "gates": gates, "metrics": report["metrics"]}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
