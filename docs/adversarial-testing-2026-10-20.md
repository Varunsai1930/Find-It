# Adversarial testing — 20 October 2026

This local exercise deliberately supplied malformed inputs and reordered
dashboard responses, reproduced failures, then added fixes and regression
tests. All test databases and workbooks were temporary and synthetic. The
existing portfolio database, original disclosures, retained releases and
pre-existing working changes were preserved.

## Reproduced failures and fixes

| Trigger | Observed failure | Fixed behavior |
| --- | --- | --- |
| A correction CSV with a missing or blank `amc_name` or `scheme_name` on one row | Missing keys were silently dropped during grouping. A two-stock portfolio was replaced by one stock, while another quantity changed from 100 to 999. | Reject the entire file before writing, preserving the previous snapshot. |
| A workbook containing valid-ISIN exposure notes after `Total Net Assets`, `Net Assets` or `Net Asset Value` | Notes were imported as holdings, adding an unrelated 900-share position. | Recognize these final portfolio totals and exclude later ISIN notes. Summary totals above the holdings remain supported. |
| A summary total above the holdings, followed by cash and a final total | The omission mask could suppress the cash provenance. | Use a final total following actual detail rows as the boundary, retaining omitted cash counts and weights. |
| Numeric cells containing `inf`, `-inf`, `Infinity` or `-Infinity` | Infinite quantities, values and NAV weights survived parsing without the existing numeric warning. | Treat them as unavailable numbers, emit the warning and retain their original raw evidence. |
| A 50,000-character stock lookup | Both lookup routes raised SQLite's `LIKE or GLOB pattern too complex` error. | Limit query text to 256 characters and return HTTP 422 for longer input. |
| A history request with a 301-digit `active_only` integer | Binding the raw integer to SQLite raised `OverflowError`; values such as `2` also made fund-house options disagree with the displayed filter. | Bind the same normalized boolean used by the history calculation. |
| Two ranking requests whose cancelled response arrives before or after the current response | Cancelled work enhanced the table again, duplicating sorting controls and listeners. | Enhance only the successfully rendered current fragment. |

The parser version is now `portfolio-2026-10-20-nav-boundary-finite-1`.
Existing stored snapshots are not rewritten by these source changes. Applying
the parser corrections to existing data requires the normal reviewed ingest
and release process.

## Verification

Before the changes: 602 Python tests and 38 dashboard tests passed. Existing
tests therefore did not expose these cases.

In the original working checkout after the changes, **632 Python tests and
40 dashboard tests passed**. The
32 new regression cases cover the triggers above, including response ordering,
unchanged database contents after rejected requests and corrections, summary
totals, and raw numeric evidence. Failing tests or direct synthetic probes
confirmed the original failures before fixes were applied. Independent review
of the loader and API changes found no actionable regression.

Before publishing, the fixes were isolated from unrelated local edits in a
clean checkout. That exact version passed **615 Python tests and 38 dashboard
tests**, plus lint and whitespace checks. The count differs because unrelated
local test additions were excluded from the commit.

Run the complete checks from the repository root:

```sh
.venv/bin/python -m pytest -q
node --test tests/web.test.cjs
.venv/bin/ruff check .
git diff --check
```

Both lint and whitespace checks passed. Regression tests are in
`tests/test_reruns.py`, `tests/test_ingest_adversarial.py`, `tests/test_web.py`
and `tests/web.test.cjs`.

## Scope and limits

This was a bounded local failure-injection pass, not exhaustive testing.
It covered workbook boundaries and numeric cells, destructive CSV correction
inputs, malformed API requests and asynchronous dashboard responses.
Malformed watchlist JSON and boundary history dates were also probed without
finding additional crashes. Hosted load, real network outages, process kills
during publication, disk exhaustion and a full browser interaction pass were
not exercised.
