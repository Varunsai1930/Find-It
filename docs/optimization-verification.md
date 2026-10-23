# Optimization verification

Each improvement is checked before its commit and checked again from the
committed source before starting the next improvement. Existing local edits
are preserved separately from these commits.

## Measure locally

Run `python tools/benchmark_web.py --db /path/to/copy.db --month 2026-08
--rounds 5 --output /tmp/findit-performance.json` from the locked environment.
It measures page, stock search and filter responses, including warm median,
p95, response bytes and SQLite statement counts. It reads the database only.
Use the existing staging verification tool for pinned releases and concurrency.

Browser developer tools show application duration in the Server-Timing header,
alongside network transfer and browser rendering. The local benchmark does
not measure browser rendering or hosted cold starts. Compare identical data,
filters, runtime and machine when judging performance improvements.
