# Monthly refresh and revision runbook

Use the local `.venv` from the repository root. Original databases and disclosures are reference material. Write every new acquisition, parsed result, stage and release under ignored `real_data/`. The web application performs no acquisition.

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

## Retain a release

After recording the source/anomaly review:

```bash
.venv/bin/python -m findit.cli release \
  --db real_data/readiness/stages/2026-08-check.db \
  --out real_data/readiness/releases \
  --label 'August 2026 research' \
  --notes 'Describe the reviewed sources, sample and remaining coverage gaps.'
```

Releases have a content-derived identifier and rules version, immutable database and manifest files, a database checksum, counts, creation time and a local `current.json` pointer. Reports and evidence retain the content identifier. The same database contents and rules produce the same identifier; operational run timestamps are excluded. This records research utility, not investment returns.

For a correction, obtain and retain the revised original separately, record why its identity/value/source changed, stage against the last good database, recompute affected adjacent comparisons, and release with `--parent OLD_RELEASE_ID --notes 'Specific correction and affected months/results'`. Parent releases must exist and pass checksum verification. Old reports and databases remain reproducible. Source-row history is append-only; the current snapshot links to the new source. The historical table marks retained source revisions. Never quietly overwrite the original disclosure or an old release.

An optional `refresh --publish-to DIRECTORY --review-notes '...' --parent ID` performs the same local promotion after an explicit recorded review. It does not deploy a public website. Failed processing/imports mark the stage failed and retain the last good pointer. A killed pipeline stays running and cannot release.

## Roll back and demonstrate

```bash
.venv/bin/python -m findit.cli release \
  --out real_data/readiness/releases --rollback OLD_RELEASE_ID
.venv/bin/python -m findit.cli web \
  --db real_data/readiness/releases/OLD_RELEASE_ID.db --port 8000
```

Rollback verifies the retained database checksum and restores the pointer. Restart the local server with that explicit database. The running server does not hot-switch databases. Keep a previous retained database and exported monthly report as the demonstration backup.

## Measure operation

Acquisition, processing and human repair effort are different measures. Preserve per-attempt timestamps/errors, source reporting/publication/retrieval dates, processing seconds, stage/release bytes, review/repair minutes and the number of expected/usable portfolios. Report publication delay only when actual publication time is known. A regulatory deadline is not observed publication time.

The local readiness directory includes measured `history-acquisition.json`, `history-processing.json`, source manifests, pipeline logs and refresh metrics. Actual cash fees, hosting, delivery, founder labor and support time remain unmeasured until recorded. Enter them in the pilot evidence workbook with invoices/time records, separately from machine processing time. Missing costs are unknown, never zero. Keep participants, payment receipts and personal notes outside Git.

## Public paid-distribution decision

For each official source and NSE price source, record the exact terms URL/version, review date, permitted extraction/storage/redistribution, attribution, archive access, fees and reviewer decision. The public download URL alone does not establish paid redistribution permission. This session has prepared local research evidence; permission for public paid redistribution is not established. Complete the source-use review and merchant/billing identity decisions before external distribution. Spending, deployment, payments, delivery and outreach require the user's execution authorization.
