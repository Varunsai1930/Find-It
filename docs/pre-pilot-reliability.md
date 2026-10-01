# Pre-pilot reliability pass — 2 October 2026

Engineering verification only. Independent review, participant comprehension, payments and retention remain pending. This supplements the [readiness audit](readiness-audit.md), [mentor packet](mentor-evidence-packet.md) and [VC packet](vc-evidence-packet.md); it does not replace their coverage limitations.

## Fixed findings

- `abf5a8a`: History evidence now carries the fixed cohort, period, filters, release ID and calculation-rules version. The server recomputes and validates the scope. First snapshots show holdings without invented prior activity; unavailable points have no evidence link. Contributing funds are listed separately from cohort membership.
- `abf5a8a`: Retained releases are verified against their manifest, byte hash, content identity and supported calculation rules. Missing, corrupt or mismatched releases return an explicit error instead of silently substituting current data. Verification invalidates when retained files change; mutable databases are re-identified, including WAL changes.
- `f385d02`: Reports reuse immutable-release metadata and comparison frames, share coverage calculations, and avoid constructing thousands of original-source panels that reports do not display. Empty watchlists skip comparison generation. Mutable working databases do not use the immutable comparison cache.
- `7ecadc8`: Clearing/changing a watchlist invalidates pending responses. A pending request hides the old download; the completed download is pinned to the displayed release and rules. Regression coverage also verifies downloaded report A remains byte-identical after switching to release B.

No original database or disclosure was repaired or replaced in this pass. No validation thresholds or numerical calculation rules were weakened.

## Exact reproduction

Retained release: `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`; rules: `readiness-2026-10-01`.

Reliance (`INE002A01018`), February–August 2026, active domestic-equity stock-pickers, all compared houses:

| Scope | August change | Funds |
|---|---:|---|
| Historical chart and its linked evidence | −1,451,458 shares | 20 fixed-cohort members; 16 contributing holdings |
| Broader monthly evidence | −11,672,309 shares | 105 contributing holdings |

Historical previous/current shares are 22,084,715 / 20,633,257. The February baseline is 21,277,011 shares with no within-range comparison. These are different scopes, not conflicting calculations. The retained full-range cohort is Franklin's 20 funds; this is not seven-month coverage of every requested house.

## Tests and browser checks

- Full Python suite: **423 passed** (6.63 seconds on final run).
- JavaScript suite: **12 passed**; Ruff: passed.
- Regressions include exact chart/evidence reconciliation, first snapshot, unavailable cohort, malformed/tampered scope, unsupported rules, missing/corrupt releases, A → B reopening and identical downloaded A bytes, mutable WAL invalidation, report determinism, stale requests and malformed imports.
- Desktop checks exercised stock search with keyboard, follow, reload persistence and history/evidence reconciliation. At 390 × 844, watchlist and history had no page-level horizontal overflow; import preserved Reliance; matching-release evidence → history → August evidence showed the exact cohort and contributing funds. Keyboard Enter activated the historical link. Viewport override was reset.
- Browser export/download completion could not be confirmed through the in-app browser's download event (timeout). Download endpoint, attachment headers, content and release reopening are verified automatically. Native-browser export/save/open remains an explicit acceptance check; it is not claimed as completed UI evidence. Import was exercised through the actual file picker.
- Engineering checks are not independent review or five-user comprehension results.

## Repeated watchlist measurements

Local TestClient, same retained database, August 2026 active scope, three sequential runs per size. Stocks are deterministic equity ISINs ranked by number of holdings then ISIN. Timing includes request dispatch and JSON encoding, excludes application startup, and is not a production concurrency benchmark. Sizes share one app instance, so larger lists reuse the comparison cache.

| Stocks | Before median | Final median |
|---:|---:|---:|
| 0 | 1.145 s | 0.008 s |
| 1 | 1.337 s | 0.096 s |
| 25 | 5.079 s | 0.110 s |
| 100 | 13.280 s | 0.179 s |

Final application startup verification: 0.797 s. First nonempty request with cold comparison cache: 0.488 s. Final 100-stock runs: 0.179, 0.187, 0.177 s. Before/after report objects matched after excluding the deliberately updated evidence URL. Numerical values, order, scope and release identity remained identical.

The baseline 25-stock profile showed repeated coverage queries (199 calls) and original-source construction (4,320 snapshot calls), plus comparison generation and content hashing. Optimization retains the existing calculations instead of introducing a second report calculation path.

Reproduce from the project root:

```sh
PYTHONPATH=. .venv/bin/python tests/benchmark_watchlist.py real_data/readiness/releases/28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec.db real_data/readiness/pre-pilot/recheck
```

Local ignored artifacts are under `real_data/readiness/pre-pilot/`: before/after/final timings and report JSON, profiles, and `reliance-reconciliation.json`. Do not interpret these local timings as hosted operating-cost measurements.

## Reopen an exported report locally

Keep the retained `.db` and matching `.json` manifest together in the release directory. From the project root:

```sh
FINDIT_DB=real_data/readiness/releases/28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec.db .venv/bin/uvicorn findit.web.app:create_app --factory --host 127.0.0.1 --port 65100
```

If that port already serves FindIt, use the running instance or choose another port. Prefix report-relative evidence URLs with the actual local origin (for example `http://127.0.0.1:65100`), preserving all query parameters. A server on release B can reopen A when verified A remains alongside B. Missing A or unsupported rules are rejected; restore its retained artifacts and compatible application version. Do not edit a manifest to bypass verification.

## Source and acceptance limitations

Rechecked 2 October 2026: [Kotak's official archive](https://www.kotakmf.com/Information/forms-and-downloads) was reachable but its extracted page exposed no usable monthly download links. The earlier interactive archive check recorded a CAPTCHA; this pass did not solve it. [HSBC's official information library](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library) timed out through the web fetch tool. These are access observations, not proof that the disclosures do not exist. No new portfolios were obtained; Kotak/HSBC remain unavailable. SBI remains partial, with 39/40 eligible August comparisons. Original-source provenance gaps and unknown publication dates remain disclosed.

A real corporate-action candidate is Reliance's **1:1 bonus**, record date **28 October 2024**, established by its [16 October issuer filing](https://www.ril.com/sites/default/files/2024-10/SE_16102024.pdf). This is outside the retained 2026 period. End-to-end source verification still requires original September/October 2024 holdings for the same fund and an independent reviewer; the filing alone does not verify an actual fund-level adjusted change.

Use the [acceptance pack](pre-pilot-acceptance.md) for the specific human steps and participant protocol. Do not label the product independently verified or commercially validated until those observations exist.
