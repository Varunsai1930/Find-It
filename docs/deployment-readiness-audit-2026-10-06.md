# Public deployment readiness audit — 6 October 2026

**Decision: not ready for a public launch in its current configuration.** The application and installed package run successfully, and the local frozen-release demonstration is usable. Confirmed calculation/validation gaps and an unverified public hosting setup remain. Passing tests demonstrate the covered behavior; they do not complete the outstanding release and operational checks below.

Scope: public, read-only FindIt website, as selected by the owner. Baseline commit `4b33e666e43bda024838d498d51d3c070995f632`, plus the uncommitted legacy-WAL compatibility fix produced during this audit. No public deployment, data promotion, production-data repair, contact with participants, or source acquisition occurred.

## Follow-up completed

Older frozen SQLite releases can retain a WAL header. Reading them with ordinary read-only connections can create journal sidecars and make later requests reject the release. The follow-up opens byte-verified, journal-free retained releases with `immutable=1`, and routes dashboard, search, summary, coverage and stock-detail reads through the same release checks. Mutable working databases continue to use ordinary read transactions and WAL updates remain visible.

Two new regressions exercise a legacy release as both the launch database and a retained report reopened from a mutable launch. They check repeated requests, all ordinary dashboard read paths, report/evidence/history reopening, unchanged database and manifest bytes, no created sidecars, and rejection of a subsequently introduced journal. Retained artifacts are not rewritten. Actual mutable journal files still require investigation rather than deletion or silent bypass.

Implementation: [release connections](../findit/store/releases.py), [web connections](../findit/web/app.py), [regression tests](../tests/test_readiness_reports.py).

SQLite's immutable option skips locking/change detection, so production must keep these artifacts on a read-only mount and replace a release by switching to another retained artifact, rather than modifying it in place. [SQLite URI documentation](https://www.sqlite.org/uri.html).

## Confirmed findings

### A01 · P2 · Unreadable numeric cells become zero in comparisons

Location: [delta_calculator.py:76](../findit/core/delta_calculator.py#L76), [consensus_signals.py:177](../findit/core/consensus_signals.py#L177), [validation_gate.py:513](../findit/store/validation_gate.py#L513).

Temporary reproduction: a July holding has quantity 100, market value 10 and NAV 100. The same August holding has quantity NULL, market value 10 and NAV 100. Validation marks both snapshots `ok`, but evidence reports current shares 0, change −100, estimated trading value −10 and `no_data=False`. Numeric parser failures are warnings, and the calculation replaces unreadable cells in a present holding with zero.

Neither inspected frozen release has missing quantity or market value among active domestic-equity holdings with `ok` status, so that false-sale example is a future ingestion safeguard failure. Missing NAV cells do exist in the frozen data: 106 selected August rows in the retained release and 114 in the candidate. Scheme 8 / `INE029A01011` has stored current NAV NULL and original normalized evidence NULL, but the comparison asserts current NAV 0 and a persisted change of −0.54. The ordinary stock table correctly displays raw missing NAV as unavailable; comparison fields should agree.

Required change: distinguish absent holding rows from unreadable numeric cells. Quarantine or withhold unknown quantities, preserve unavailable values/flows/NAV, and add regressions covering both valid absence and unreadable present rows. This is a correctness gate before relying on automatic monthly updates.

### A02 · P2 · Stock details ignore a quarantined previous snapshot

Location: [app.py:330](../findit/web/app.py#L330), [_stock.html:55](../findit/web/templates/_stock.html#L55).

Stock details join validation only for the current snapshot. The retained August release contains 104 affected stock/month rows with current status `ok` and previous status `quarantined`; all are non-equity. A real example is `/api/stock/IN002025Z211?month=2026-07`: Franklin fund 1391 appears with `validation_status="ok"`, `action="new"` and `qty_change=1400000`, while `/api/summary/1391/2026-07` correctly reports a quarantined comparison. The detail panel supports all instrument types, so this is still exposed through the public application even though the default ranking is domestic equity.

Required change: separate current-holding validity from comparison eligibility. Withhold or explicitly label actions and changes when either referenced snapshot fails validation. Add API/template tests and reconcile detail, summary and evidence behavior.

### A03 · P2 · Empty refreshes can promote a nonexistent reporting month

Location: [refresh.py:66](../findit/cli/refresh.py#L66), [pipeline.py](../findit/pipeline.py).

Temporary reproduction: start from an August-only database and call `refresh(source, stage, "2026-08", "2026-09", [], [], releases, "Recorded review notes")`. It returns `status="released"`, labels the manifest “Monthly research 2026-09,” and activates `current.json` despite zero September holdings, validated sources or comparisons. The generic snapshot guard checks the latest pipeline status; a completed empty pipeline satisfies it.

Required change: retain no-input preflight if useful, but block monthly promotion until source-backed current-month data and usable validated comparisons exist for the declared scope. Test that failed/empty promotion preserves the previous pointer. Review both the refresh promotion path and monthly release acceptance so another entry point cannot bypass the decision.

### A04 · P2 · Documented direct launches bypass watchlist logging privacy

Location: [README.md:405](../README.md#L405), [pre-pilot-reliability.md:64](pre-pilot-reliability.md#L64), [web.py:30](../findit/cli/web.py#L30).

The project CLI explicitly disables access logs because URLs contain users' research choices. Some documented commands invoke Uvicorn directly, whose installed default enables access logging. Following those commands can record watchlist query strings. The same concern applies to a hosting provider or reverse proxy even when application logging is disabled.

Required change: use one canonical deployment launcher or explicit `--no-access-log`, update the launch instructions, and verify proxy/application logs do not store stock lists. Preserve useful service/error telemetry without query-string disclosure.

### A05 · Deployment configuration · Exported links trust the request host

Location: [app.py:698](../findit/web/app.py#L698).

A download request with `Host: malicious.example` produces evidence URLs beginning `http://malicious.example/`, because the report uses `request.base_url` directly. This can be addressed by the public proxy, application host validation or a configured canonical external origin. It does not establish a need for user accounts on a public read-only service.

Required check: allow only the chosen public hostname, configure trusted forwarding of HTTPS scheme/host, and test exported links through the real proxy. The audit did not exercise a deployed proxy.

### A06 · P3 · Relative release-directory configuration fails

Location: [releases.py:115](../findit/store/releases.py#L115).

`ReleaseStore(valid_db, Path("real_data/readiness/releases")).connect()` fails when converting a relative path to a file URI; the same directory resolved to an absolute path succeeds. The default derived directory is already absolute. An explicit relative `create_app(..., release_dir=...)` setting can therefore reject a valid retained release.

Required change: resolve the configured directory once in the constructor, with a relative-path regression.

### A07 · P3 · Oversized scheme identifiers produce HTTP 500

Location: [app.py:622](../findit/web/app.py#L622).

`/api/summary/999999999999999999999999/2026-08` is accepted as a Python integer but overflows SQLite's integer binding, returning HTTP 500.

Required change: bound identifiers before database access and return a normal client error. This is input hardening, rather than a demonstrated systemic outage.

### A08 · P3 · Development dependency has a known temporary-directory advisory

Location: [pyproject.toml:30](../pyproject.toml#L30).

`pip-audit` checked 44 installed packages and reported `pytest==8.4.2` as affected by CVE-2025-71176 / GHSA-6w46-j5rx-g56g. The scanner returned the same advisory twice; this is one unique vulnerability. It concerns local Unix temporary-directory handling. The upstream [pytest changelog](https://docs.pytest.org/en/latest/changelog.html#pytest-9-0-3-2026-04-07) records the fix in 9.0.3. No known advisories were reported for the other scanned packages.

Required change: upgrade the development pin to a verified patched version and rerun both supported CI Python versions. Keep development/test dependencies out of the production image. This is a development/CI environment issue, not evidence of a remotely exploitable runtime dependency in this public application.

## Public hosting and operational acceptance

The repository contains local launch/refresh/rollback instructions, but no chosen and verified public hosting configuration. A specific Dockerfile is not required; a concrete deployment and evidence that it works are required.

Before launch, establish and test:

1. HTTPS termination, allowed hostname, trusted proxy headers and one canonical external origin.
2. A supervised application process with automatic restart, resource limits and retained-release configuration.
3. Delivery and retention of the selected `.db` plus matching manifest; a read-only serving mount; a separate writable ingestion workspace. A fresh clone has no production database.
4. A data-aware readiness probe. `/health` and `/healthz` are absent. Missing data correctly produces 503 from `/` and `/api/coverage`, but `/docs` and `/openapi.json` remain 200, so documentation or a listening port is not enough to establish readiness.
5. Logging that protects watchlist query strings, with useful error monitoring and failure notification.
6. A tested remote restore and rollback/restart procedure. Local retention and rollback exist; remote storage durability, crash recovery and hosted restoration were not tested.
7. Response-time/traffic targets and concurrency tests using the intended worker/proxy configuration, with caching or request controls where measurements justify them.

These are provider-specific acceptance tasks; adding files alone does not complete them. [FastAPI deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/) and [HTTPS deployment](https://fastapi.tiangolo.com/deployment/https/) describe the relevant runtime concerns.

CI currently tests an editable checkout on Python 3.12 and 3.14 plus JavaScript. The installed-wheel smoke succeeded locally, but [CI](../.github/workflows/ci.yml#L36) should also build/install the artifact outside the checkout and verify packaged assets and startup. This is a prevention gap, not a current broken package.

## Verification results

| Check | Result | Limit |
|---|---|---|
| Current Python suite | 441 passed in 7.98 seconds | Synthetic automated coverage, including legacy WAL follow-up |
| JavaScript suite | 12 passed | Mocked DOM/request tests |
| Repository lint and diff whitespace | Passed | No production behavior claim |
| Current environment dependency consistency | 44 packages checked; compatible | `uv pip check`, not a vulnerability scan |
| Known dependency advisory scan | 44 packages; one unique pytest advisory (A08), duplicated in scanner output | Version/database matching, including development dependencies; not proof of absence of vulnerabilities |
| Wheel build and fresh dependency installation | Passed on Python 3.14 | Wheel was built before the WAL follow-up |
| Installed wheel outside checkout | 17 application/static endpoints passed; report attachment/identity valid; DB unchanged | Synthetic frozen database; no hosted/Linux environment |
| Actual frozen-release integrity | Both retained and candidate byte hashes/logical IDs match; no journals | Selected artifacts, not independent source-cell verification |
| Browser smoke on retained August | Dashboard, HDFC expansion, keyboard Reliance search, stock details, Franklin history selection and exact linked evidence render correctly | Desktop in-app browser; no new mobile/screen-reader or five-person usability certification |

The live evidence view reproduced Franklin's 20-fund fixed cohort, 16 August contributors, previous shares 22,084,715, current shares 20,633,257 and change −1,451,458. No browser warnings/errors were observed in that bounded smoke. Earlier automation selector failures were resolved using the live accessible controls and are not claimed as application failures.

Single-request measurements using retained `28bb0fa…`:

| Request | Initial / repeated elapsed time | Status |
|---|---:|---:|
| Homepage | 2.18 / 1.19 s | 200 |
| One-stock watchlist | 0.53 / 0.009 s | 200 |
| 100-stock watchlist | 0.095 / 0.10 s | 200 |
| 25-snapshot history | 1.31 / 1.28 s | 200 |
| Evidence | 0.52 / 0.52 s | 200 |

These are local sequential measurements, not public capacity or operating-cost estimates. The repeated homepage still recomputes substantial coverage/house-summary work. Mutable launches also rehash content for identity, so the public service should serve a verified retained release.

## Release and product acceptance state

- Current local pointer: retained August `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`. Five selected houses are complete, SBI is 39/40 partial, Kotak/HSBC are unavailable, and wider-market completeness is unknown.
- Expanded candidate: `65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4`. Seven selected houses are complete; SBI remains 39/40; selected scope is 219/220. Its recorded acceptance and promotion are false, independent review is pending and participant testing has not started.
- Inspected datasets stop in August. Stored September records contain zero verified originals, holdings, deltas and inventories. This does not establish whether publishers have since made sources available. September acquisition is needed for a September release, not for an honestly labeled August historical product.
- Independent findings and five participant records remain pending under the project's [existing acceptance checklist](pre-pilot-acceptance.md). Automated checks do not complete those human observations.
- The recorded [source-use review](source-use-review.md) has no public-distribution approval decision. Its old Kotak/HSBC acquisition statement is superseded by later local originals. This audit makes no legal conclusion about permissions; the owner needs to settle the intended public distribution scope.
- The existing demo tag predates the fixes. Preserve it as a historical baseline; create a separately identified tested baseline for any later public release rather than silently changing old test records.

## Recommended planning order

1. Fix A01–A03 with targeted failing regressions and reconcile all public representations of the same comparisons.
2. Address A04–A08 and add installed-artifact smoke coverage to CI.
3. Choose the public host, data release, domain and declared historical/update scope; implement its operational configuration and probes.
4. Run HTTPS/proxy/logging, concurrency, restart and restore/rollback acceptance on the actual deployment target.
5. Complete or explicitly revisit the recorded independent review, usability and public-distribution decisions. Then freeze the new application/data baseline and make the launch decision.

The next step is remediation planning. The additional findings were documented rather than implemented during this audit; only the explicitly requested legacy WAL follow-up was changed.

## Audit boundaries

Codebase Memory was the primary structural discovery tool. Exact-path coverage and relevant scope pagination were checked; reported template parse gaps were read directly. Git-ignored release artifacts were inspected through direct read-only access. Reviews covered web routes/UI rendering, ingestion/calculation/validation/release paths, packaging, CI and local operations. This is a task-directed audit, not a proof that every possible bug is absent. No live public TLS/proxy, Linux runtime, production load, current publisher availability, operating-system/native-library security assessment, legal source permission or human comprehension assessment was completed.
