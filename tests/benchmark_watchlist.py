"""Run with PYTHONPATH=. python tests/benchmark_watchlist.py DB OUTPUT_PREFIX.

Reads the database only. Reports contain its public stock results; choose a local
ignored output directory. Timings include TestClient dispatch and JSON encoding.
"""

import json
import time
import statistics
import sqlite3
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from findit.web.app import create_app

p = Path(sys.argv[1])
output = Path(sys.argv[2])
output.parent.mkdir(parents=True, exist_ok=True)
c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
stocks = [
    r[0]
    for r in c.execute(
        "select h.isin from mf_holdings_monthly h join stocks s using(isin) where h.report_month='2026-08' and s.instrument_type='equity' group by h.isin order by count(*) desc,h.isin limit 100"
    )
]
c.close()
started = time.perf_counter()
client = TestClient(create_app(p))
startup = time.perf_counter() - started
results = {"startup_seconds": startup}
for n in (0, 1, 25, 100):
    times = []
    for run in range(3):
        start = time.perf_counter()
        r = client.get(
            "/api/watchlist", params={"month": "2026-08", "stocks": ",".join(stocks[:n])}
        )
        times.append(time.perf_counter() - start)
        assert r.status_code == 200, r.text
    results[n] = {"seconds": times, "median": statistics.median(times), "stocks": stocks[:n]}
    Path(f"{output}-{n}-report.json").write_text(json.dumps(r.json(), sort_keys=True))
    print(n, times, flush=True)
Path(f"{output}-timings.json").write_text(json.dumps(results, indent=2))
