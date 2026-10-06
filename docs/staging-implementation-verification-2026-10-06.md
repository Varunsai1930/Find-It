# Staging implementation verification — 6 October 2026

**Decision: the local staging implementation passes its engineering gates. Public launch and hosted staging acceptance remain pending.** No Vercel project, private Blob credentials or deployment authentication were available. No public deployment, production promotion or original-data activation occurred.

This implements the approved follow-up to the [baseline deployment audit](deployment-readiness-audit-2026-10-06.md). The target is a protected Vercel preview of the August historical MVP. The [deployment runbook](vercel-staging.md) records the remaining account and hosted checks.

## Step-by-step results

| Step | Benefit and potential break checked | Result |
|---|---|---|
| 1. Preserve the baseline | Prevent loss of original disclosures, frozen reports or the current pointer; repeated immutable WAL reads must not create journals. | 118 protected files retain their saved SHA-256 hashes. Both original release pairs and the current pointer remain unchanged. WAL regression fix committed as `aca7da8`. |
| 2. Correct numeric availability | A missing position can mean zero; an unreadable cell in a present row cannot mean a sale. Ensure valid direction/flow does not drift. | Domestic invalid quantities withhold the scheme comparison. Foreign quantity gaps preserve usable domestic data. Unknown values, NAV and incomplete discretionary sleeves stay unavailable; partial currency totals are withheld. Valid breadth and finite flow match all 684 retained and 729 expanded-candidate baseline rows within 1e-10 relative tolerance. |
| 3. Reconcile stock detail | Prevent actions being presented as valid when the previous snapshot failed, is missing, or is not adjacent. Preserve raw holdings for inspection. | API and template regressions pass. The real `IN002025Z211` July example now has one withheld comparison and no asserted action/change. Unknown raw market values cannot expose a stale stored flow. |
| 4. Guard activation and version replay | Prevent empty/wrong-month refresh promotion and silent changes to saved reports. Check rollback cannot bypass the same guard. | Generic snapshots archive by default. Activation requires an explicit month, completed matching adjacent run, source-backed validated comparisons, coverage receipt and review notes. Failed activation preserves the pointer. Two rule versions are allowlisted; incorrect/malformed versions, changed files and unsafe legacy inputs return controlled conflicts. Both saved watchlists, Markdown reports and fixed-cohort history results reproduce exactly under v1. Unsafe legacy NAV evidence returns 409 with a verified successor link. |
| 5. Enforce web boundaries | Prevent poisoned report origins, private stock lists in normal UI URLs, unbounded IDs and misleading readiness probes. | Exact hosted hostnames and HTTPS report links, private no-store POST watchlists/reports, bounded SQLite IDs, `/health/live` and data-aware `/health/ready` pass. GET report compatibility remains available and is not the UI's normal private flow. Documentation disables local access logs; provider logging remains a hosted check. |
| 6. Package the actual runtime | Prevent missing templates/assets, accidental development dependencies, private inputs in the wheel or an unsafe public entrypoint. | Locked Python 3.12 runtime, native Vercel build hook, server-only four-file private bundle, redirect-rejecting authenticated build acquisition and production-environment rejection implemented. Source-only wheel smoke passes outside the checkout on Python 3.12 and 3.14. Runtime and development advisory scans report no known advisories. |
| 7. Verify staging mechanics | Detect excessive function size, response overflow, slow concurrent pages, missing release data and unintended data writes. | Exact descriptor/content verification and fresh local rebuild pass. Conservative Linux inventory is 380,732,547 bytes, below the unchanged 400,000,000-byte gate. Hosted adapter route/host/private-path tests pass. Local load gates pass; actual Vercel-generated function, protection, network timing, logging and preview rollback remain pending. |
| 8. Preserve results and record limitations | Prevent approving an incomplete public release or concealing regressions. | Full suites: 524 tests on each of Python 3.12 and 3.14; 12 browser-script tests; lint and whitespace checks pass. Original failed load result is retained alongside the passing rerun. Existing independent source, public distribution and participant acceptance gates remain open. |

## Corrected data artifact

The corrected candidate is `93c004568c08d1361833798a41b18062c7dfaa7040aacb61d9fe986295ed8e8d`, under `readiness-2026-10-06`. Its parent is retained `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`, under `readiness-2026-10-01`.

A separate copy was revalidated across 1,778 scheme-months and recalculated across six adjacent pairs. All ten prior non-OK decisions were retained; no new quarantines appeared. Its read-only technical activation check confirms 170 source-backed adjacent compared funds. **The candidate was archived, not promoted.** The original `current.json` still names `28bb0fa…`. Existing inventories, disclosures and wider-market unknown completeness were preserved. This does not approve missing September data or the expanded `655947…` candidate.

[deployment/releases.json](../deployment/releases.json) pins the corrected and legacy database/manifest hashes and byte counts. Local `deploy-data/` contains exactly four read-only files, totaling 204,269,665 bytes. These private artifacts remain ignored by Git and excluded from public/static routes; they must be uploaded to the intended private Blob store before an authenticated cloud build can succeed.

## Local load gate

The first five-request concurrency run failed: warm core p95 was 7.478 seconds and the slowest endpoint p95 was 7.583 seconds. The repeated overview calculation was corrected with a bounded cache for verified immutable releases, calculation version, file signatures, month and filter scopes. Mutable databases remain uncached for that overview; per-request copies and changed-file rejection are regression tested.

The same thresholds passed after the fix, across **129 successful requests**:

| Measurement | Result | Gate |
|---|---:|---:|
| Five-request warm core p95 | 4.079 s | ≤5 s |
| Slowest core endpoint p95 | 4.219 s | ≤5 s |
| Selected-house February–August history maximum | 4.038 s | ≤8 s |
| Cold local ASGI maximum, including startup | 10.593 s | ≤15 s |
| Largest full page/fragment response across all seven months and both directions | 3,722,527 bytes | ≤4,000,000 bytes |
| HTTP failures / new journals / protected-file changes | 0 / 0 / 0 | 0 |

These measure this machine's ASGI application with an uncontrolled operating-system filesystem cache. They do not establish Vercel network latency, platform cold-start performance, memory behavior, usage limits or service availability. The conservative Linux inventory includes locally generated bytecode; the actual platform function must still be measured.

The native browser rendered the corrected dashboard and stock details, completed the private watchlist calculation, and saved a 2,535-byte monthly report that matches the server's pinned report exactly. The download link contains no stock list. The in-app browser's download-event API did not report that Blob download; the actual saved file was independently verified.

## Reproduce and remaining work

Run the source suites with the locked development environment, then prepare the exact private pair and run the local gate:

```sh
uv sync --locked --extra dev --python 3.12
uv run --no-sync pytest -q
node --test tests/web.test.cjs
uv run --no-sync ruff check .
uv run --no-sync python tools/prepare_vercel_release.py --local-releases real_data/readiness/releases
uv run --no-sync python tools/verify_staging_web.py
```

Local receipts are in ignored `real_data/readiness/vercel-staging/`: `baseline.json`, `golden-verification.json`, `candidate-verification.json`, `candidate.json`, `web-verification-before-cache.json`, and `web-verification.json`. Bundle inventories and clean-environment wheel receipts are local build outputs. They are evidence for this run, not committed substitute fixtures or rewritten goldens.

Account-dependent work is pending: establish the new protected preview project and private artifact storage; verify unsigned access is blocked on pages and direct APIs; perform the authenticated build; inspect the generated function and provider logs; measure hosted performance; reopen reports after a clean rebuild; and restore a previous protected preview with matching code, descriptor and data. Follow the [runbook](vercel-staging.md) without production promotion or paid upgrades.

Public launch also requires resolving the existing independent numerical/source review, corporate-action/source coverage, public-distribution decision and participant acceptance records in the [acceptance pack](pre-pilot-acceptance.md). Automated engineering checks do not complete those human decisions. No questions were sent while the owner was taking the exam.
