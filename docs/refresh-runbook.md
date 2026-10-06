# Monthly refresh and revision runbook

Use the locked local environment from the repository root: `uv sync --locked --extra dev --python 3.12`. Original databases and disclosures are reference material. Write every new acquisition, parsed result, stage and release under ignored `real_data/`. The web application performs no acquisition. These examples are local research operations; protected hosting follows the separate [staging runbook](vercel-staging.md).

## Acquire and inspect

```bash
.venv/bin/python -m findit.cli download --help
.venv/bin/python -m findit.cli intake --help
```

Use each house's official dated monthly disclosure list. Save originals with the downloader's checksum/URL/retrieval manifest; publication dates stay unknown unless the official source supplies them. Do not substitute factsheets containing only portfolio percentages for full quantities. Unsupported acquisition can use manually downloaded originals in the registry's house folder. CAPTCHA or source outages are access limitations, not absent portfolios.

Prepare the two adjacent month folders with the existing intake command. Resolve wrong-month files, duplicate portfolios, source-hash mismatches and identity issues before loading. A reviewed inventory JSON records the official portfolio keys, exact mapped scheme IDs, eligibility/exclusion reasons, original checksum, HTTPS URL, review date and exhaustive flag. The downloaded/loaded fund count is not an inventory denominator. See `findit.cli.coverage.import_inventory` and the local `*-inventory.json` examples.

## Stage, validate and compare

```bash
.venv/bin/python -m findit.cli refresh \
  --source-db real_data/readiness/working.db \
  --staged-db real_data/readiness/stages/2026-08-check.db \
  --prev 2026-07 --curr 2026-08 \
  --load real_data/readiness/inbox/2026-07/_parsed/hdfc_amc.parsed.csv \
         real_data/readiness/inbox/2026-08/_parsed/hdfc_amc.parsed.csv
```

Choose a **new** stage path every time. This copies the source read-only, runs the existing pipeline in isolation, and records input hashes, validation outcomes, coverage, actual elapsed seconds and database bytes in adjacent `.refresh.json` / `.pipeline.log` files. Add repeated `--inventory` arguments for reviewed JSONs. No publication occurs by default. Review expected-fund states, withheld schemes, unusual changes, inferred actions, prices and original rows. Quarantined data stays excluded. A smaller comparison count is not a reason to relax validation.

## Archive a candidate

Freeze a candidate for review without changing `current.json`:

```bash
.venv/bin/python -m findit.cli release \
  --db real_data/readiness/stages/2026-08-check.db \
  --out real_data/readiness/releases \
  --candidate --month 2026-08 \
  --label 'August 2026 research' \
  --notes 'Candidate for review; describe sources and remaining coverage gaps.'
```

Candidates have a content-derived identifier and rules version, immutable database and manifest files, a database checksum, counts and creation time. Reports and evidence retain the content identifier. The same database contents and rules produce the same identifier; operational run timestamps are excluded. The Python `snapshot()` function archives by default; the release CLI uses `--candidate` to request this behavior explicitly. This records research utility, not investment returns.

## Review and activate locally

After source/anomaly review, create or verify the retained files and activate the reviewed month:

```bash
.venv/bin/python -m findit.cli release \
  --db real_data/readiness/stages/2026-08-check.db \
  --out real_data/readiness/releases \
  --month 2026-08 --label 'August 2026 research' \
  --notes 'Describe the completed source/anomaly review and remaining coverage gaps.'
```

Activation requires an explicit reporting month and nonempty review notes, a completed latest run for that month and its adjacent predecessor, and at least one validated source-backed comparison. It records coverage and a review receipt before atomically updating `current.json`. An empty or wrong-month refresh cannot activate; a failed guard preserves the last good pointer. The guard verifies technical eligibility; it does not complete independent source review or participant acceptance.

For a correction, obtain and retain the revised original separately, record why its identity/value/source changed, stage against the last good database, recompute affected adjacent comparisons, and archive with `--candidate --month YYYY-MM --parent OLD_RELEASE_ID --notes 'Specific correction and affected months/results'`. Remove `--candidate` only after review to request activation. Parent releases must exist and pass checksum verification. Old reports use their retained calculation version; unsafe legacy inputs produce a controlled conflict instead of silently using new rules. Source-row history is append-only; the current snapshot links to the new source. The historical table marks retained source revisions. Never quietly overwrite the original disclosure or an old release.

An optional `refresh --publish-to DIRECTORY --review-notes '...' --parent ID` requests the same guarded local activation for `--curr` after an explicit recorded review. It does not deploy a public website. Failed processing/imports mark the stage failed and retain the last good pointer. A killed pipeline stays running and cannot release.

## Roll back and demonstrate

```bash
.venv/bin/python -m findit.cli release \
  --out real_data/readiness/releases --rollback OLD_RELEASE_ID \
  --month 2026-08 --notes 'Describe the reason and reviewed restore target.'
.venv/bin/python -m findit.cli web \
  --db real_data/readiness/releases/OLD_RELEASE_ID.db --port 8000
```

Rollback verifies the retained database byte hash, logical identity and calculation version, then applies the same month, source, validation and review guard before restoring the pointer. Use the restored release's actual month; legacy releases without a manifest month require `--month`. Restart the local server with that explicit database. The running server does not hot-switch databases. Keep a previous retained database and exported monthly report as the demonstration backup.

## Measure operation

Acquisition, processing and human repair effort are different measures. Preserve per-attempt timestamps/errors, source reporting/publication/retrieval dates, processing seconds, stage/release bytes, review/repair minutes and the number of expected/usable portfolios. Report publication delay only when actual publication time is known. A regulatory deadline is not observed publication time.

The local readiness directory includes measured `history-acquisition.json`, `history-processing.json`, source manifests, pipeline logs and refresh metrics. Actual cash fees, hosting, delivery, founder labor and support time remain unmeasured until recorded. Enter them in the pilot evidence workbook with invoices/time records, separately from machine processing time. Missing costs are unknown, never zero. Keep participants, payment receipts and personal notes outside Git.

## Public paid-distribution decision

For each official source and NSE price source, record the exact terms URL/version, review date, permitted extraction/storage/redistribution, attribution, archive access, fees and reviewer decision. The public download URL alone does not establish paid redistribution permission. This session has prepared local research evidence; permission for public paid redistribution is not established. Complete the source-use review and merchant/billing identity decisions before external distribution. Spending, deployment, payments, delivery and outreach require the user's execution authorization.
