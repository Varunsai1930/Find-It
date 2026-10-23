"""Profile local read-only dashboard requests. Excludes browser and network time."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


@contextmanager
def query_counter():
    from findit.web import app
    connect = app._ro_connect
    statements = []

    def traced(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    with patch.object(app, "_ro_connect", traced):
        yield statements


def benchmark(database: Path, month: str, rounds: int = 5) -> dict:
    from fastapi.testclient import TestClient
    from findit.web.app import create_app
    if rounds < 1:
        raise ValueError("rounds must be positive")
    samples = []
    cases = {"page": "/?month=" + month,
             "search": "/api/stocks/search?q=in&month=" + month,
             "filters": "/fragments/month/" + month + "?active_only=0&side=sell"}
    with query_counter() as statements, TestClient(create_app(database)) as client:
        for iteration in range(rounds + 1):
            for name, url in cases.items():
                statements.clear()
                started = time.perf_counter()
                response = client.get(url)
                response.raise_for_status()
                samples.append({"case": name, "phase": "cold" if iteration == 0 else "warm",
                                "seconds": time.perf_counter() - started,
                                "bytes": len(response.content), "sql_statements": len(statements)})
    metrics = {}
    for name in cases:
        warm = [s for s in samples if s["case"] == name and s["phase"] == "warm"]
        times = sorted(s["seconds"] for s in warm)
        metrics[name] = {"median_seconds": statistics.median(times),
                         "p95_seconds": times[math.ceil(.95 * len(times)) - 1],
                         "bytes": max(s["bytes"] for s in warm),
                         "sql_statements": max(s["sql_statements"] for s in warm)}
    return {"metrics": metrics, "samples": samples,
            "limitations": "Local ASGI timings exclude browser rendering, network and platform cold starts."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--month", required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.resolve() == args.db.resolve() or args.output.exists():
        parser.error("Choose a new output file separate from the database")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    result = benchmark(args.db, args.month, args.rounds)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
