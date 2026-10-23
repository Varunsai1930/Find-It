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


Large responses use negotiated gzip compression. Exact current CSS/JavaScript
fingerprints receive a one-year immutable cache policy; missing or mismatched
fingerprints must revalidate. Private watchlist and report responses retain
`private, no-store`. Check these headers on the deployed origin too when its
CDN serves static files directly; local verification covers the ASGI server.

“All” stock rankings use server pages of 50 rows. Page links preserve filters, absolute ranks and column selection; changing a filter returns to page one. Column sorting applies to the displayed rows.

## Regression checks

Run the existing Python, Ruff and Node checks from CONTRIBUTING.md. Browser
coverage uses the already pinned download extra and Chromium:

```sh
uv run --no-sync python tools/verify_browser.py --output /tmp/findit-browser-new
```

Use a new output directory for each run. The tool creates temporary synthetic
holdings and a retained release, then fulfills browser requests through the
local app. It covers concurrent request consistency, search and fund summaries,
pagination, filter resets, failed refresh and retry, watchlist persistence and
report release pins, keyboard focus, accessible status updates, theme palette
contrast and four responsive widths. It also checks that retained database
bytes did not change. CI runs it in a dedicated Chromium job.

The browser check reports its limitations: synthetic local data does not
measure hosted latency, and keyboard/accessibility-tree checks do not replace
manual screen-reader testing. Its contrast check covers the shared text palette,
not every possible composited or animated pixel.

## Local measurements

On the same small synthetic database, warm page median fell from about 24 ms
to 6 ms after month-cache reuse; SQLite statement count fell from 143 to 7
(before the later availability display added two small lookups). A targeted
search over 240,000 synthetic holdings fell from 12.5 ms to 0.21 ms with the
new indexes. The original grouped query remains available for older immutable
releases without those indexes. A representative page transfer fell from about
47 KB to 9 KB with gzip. These are local measurements, not hosted guarantees.

Every optimization has its own commit. The Python, Ruff and Node checks run
before the commit and again in an archive of the committed source, excluding
pre-existing local edits. The existing local edits remain in the working tree.
