# Protected Vercel staging

This runbook covers the historical August 2026 MVP in a **new protected staging project**. It does not authorize public activation, production promotion, paid upgrades, or changes to the existing human review gates. Cloud verification remains pending until Vercel project access and private Blob credentials are available.

## Local evidence — 6 October 2026

The corrected current release is `93c004568c08d1361833798a41b18062c7dfaa7040aacb61d9fe986295ed8e8d`. Local preparation verified both pairs and produced exactly four read-only files totaling 204,269,665 bytes. The real hosted adapter passed ten route checks, including readiness, private report export, legacy replay and hostile-host rejection, with unchanged artifact hashes; startup was 1.094 seconds locally. A fresh local rebuild produced identical hashes for all four artifacts. A clean installed wheel passed eleven synthetic routes and three assets outside the checkout on Python 3.12 and 3.14; both production environments passed their 32-package compatibility check. The locked runtime and development advisory checks found no known vulnerabilities on this date.

The 31 locked Linux Python 3.12 runtime wheels expand to 123,710,254 bytes; removing vendor test directories and bytecode would reduce that to 103,851,171 bytes. The deployed configuration keeps dependencies intact. Expanding the full wheels and compiling their sources locally with Python 3.12 yields 174,184,908 bytes. Including both real releases, application source and existing bytecode produced a conservative inventory of 380,732,547 bytes, below the 400 MB gate with about 19 MB remaining. These are local measurements using Linux wheel files on macOS, not a completed Vercel build or hosted load test. Recheck the actual generated function before preview acceptance.

## Build contract

`app.py` exports the hosted FastAPI instance. It requires exact `VERCEL_URL` / `VERCEL_BRANCH_URL` hostnames, rejects `VERCEL_ENV=production`, and pins the report month from `deployment/releases.json`. The application keeps its native root routes and `/static/` asset URLs; no `/api` rewrite or `root_path` prefix is needed. The deployment has a 30-second request budget. FastAPI's native Vercel integration supports the module entrypoint and runs `[tool.vercel.scripts].build` after dependency installation. Static sources remain in the function because startup reads them to fingerprint CSS and JavaScript. [Vercel FastAPI guide](https://vercel.com/docs/frameworks/backend/fastapi)

`.python-version` selects Python 3.12. `uv.lock` pins runtime and optional development dependencies; deployment installs runtime dependencies only, with neither the development extra nor the optional browser downloader. CI tests Python 3.12 and 3.14 and separately installs a wheel outside the checkout. Vercel supports those Python versions and accepts a `pyproject.toml` / `uv.lock` project. [Python runtime](https://vercel.com/docs/functions/runtimes/python)

The version-1 descriptor pins exactly two immutable database/manifest pairs: the corrected August successor under `readiness-2026-10-06`, and retained release `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec` under `readiness-2026-10-01` for historical replay. Each entry includes the complete release ID, rules, database SHA-256, manifest SHA-256, and database byte count. The selected current release and month are committed together with those pins. Generate the descriptor only from the reviewed candidate and preserved legacy artifacts; never edit hashes to make a failed verification pass.

`tools/prepare_vercel_release.py` obtains only those four files during the build, places them in server-only `deploy-data/`, and checks exact names, sizes, byte hashes, manifest agreement, and both content identities. Downloads require HTTPS on a private Vercel Blob hostname, use a bearer credential in the header, reject redirects, and abort on oversized files. Files become read-only after an atomic local copy. Runtime performs no artifact download, refresh, or database write. Private Blob supports authenticated raw HTTPS reads using `BLOB_READ_WRITE_TOKEN`. [Private storage](https://vercel.com/docs/vercel-blob/private-storage)

`real_data/`, original spreadsheets, working databases, outputs, local environments, credentials, and binary documentation are excluded from uploads/function inputs. Database files are explicitly included only in the function bundle, outside `public/`. `public: false` hides deployment source/log visibility; it **does not enable Deployment Protection**. Preview authentication must be configured and tested independently. [Project configuration](https://vercel.com/docs/project-configuration), [Deployment Protection](https://vercel.com/docs/deployment-protection)

## Local preparation

After the reviewed corrected release and descriptor exist, run from the project root:

```sh
uv sync --locked --extra dev --python 3.12
uv run --no-sync python tools/prepare_vercel_release.py --local-releases /absolute/path/to/reviewed/releases
```

The source directory may retain other releases; only descriptor-pinned pairs are copied. An existing `deploy-data/` must contain exactly those four matching files, otherwise preparation fails. Its inventory is written to ignored `.vercel-build-audit.json` and must stay below **400,000,000 bytes**, allowing headroom against the standard 500 MB uncompressed Python limit. This conservative local count includes installed dependencies and source; the final generated Vercel function must also be measured because platform-generated files and bytecode can differ. The platform response limit is 4.5 MB. [Function limits](https://vercel.com/docs/functions/limitations), [Python bundling](https://vercel.com/docs/functions/runtimes/python)

To serve the prepared staging adapter locally, set synthetic preview hostnames and keep access logs disabled:

```sh
VERCEL_ENV=preview VERCEL_URL=findit-local.vercel.app uv run --no-sync uvicorn app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Requests must carry the configured host, for example `Host: findit-local.vercel.app`. This tests the hosted adapter locally and does not demonstrate Vercel routing, protection, or cold-start behavior.

## Protected project setup and cloud acceptance

Complete these steps only with authenticated access to the intended new staging project. Keep the repository's production/main branch disconnected from automatic public deployment. Do not run `vercel --prod`, promote a preview, add a public production domain, or purchase a plan.

1. Confirm the project/team, Hobby usage limits, preview-only target, and Vercel Authentication settings. Verify an unsigned browser cannot reach both the preview homepage and a direct API URL. Retain an authenticated tester session; keep bypass secrets out of shared URLs and logs.
2. Use a private Blob store and upload only the four descriptor-pinned files under one approved prefix, preserving `<release_id>.db` and `<release_id>.json` filenames. Keep original portfolios local. Retain the same four verified files offline as the rebuild source.
3. Configure `FINDIT_BLOB_BASE_URL` as the private store URL plus that prefix, and `BLOB_READ_WRITE_TOKEN` as a sensitive Preview environment value. Do not place credentials in the repository, descriptor, command arguments, reports, screenshots, or build inventory. Leave the build command unset in project overrides so the committed native build hook runs.
4. Build a protected preview from the reviewed commit. Inspect the generated function inventory: exactly the approved pairs, templates, static files and runtime code; no original disclosures, secrets, working databases, outputs or development/browser dependencies. Record actual uncompressed function bytes below the 400 MB gate, build duration and usage. Stop if the gate fails.
5. Check `/health/live` and `/health/ready` in the protected preview. Readiness must show the descriptor's corrected release and August month. Verify dashboard buy/sell filters, search, stock details, coverage, history-to-evidence reconciliation, private POST watchlist/export, authenticated report reopening, and legacy replay. Confirm CSS/JavaScript and the download work in a native browser. Private watchlist responses must retain `private, no-store`; links must use the exact HTTPS preview host.
6. Measure cold and warm requests for the largest dashboard, largest fragment and a 100-stock report. Record response bytes below the 4,000,000-byte safety gate (platform limit 4.5 MB), warm core p95 at or below 5 seconds with five concurrent requests, selected-house history maximum at or below 8 seconds, cold maximum at or below 15 seconds, completion inside 30 seconds, memory/errors, repeated-request isolation and no database sidecars. Inspect platform logs for unwanted watchlist/query disclosure; local `--no-access-log` does not configure Vercel's own logs.
7. Rebuild the same pins without relying on the previous local `deploy-data/` or build cache. Verify both artifact hashes, current readiness identity, and report results match. Record that authenticated downloads ran during build and that requests did not fetch Blob objects.
8. Exercise rollback between two protected preview builds using their bundled commit, descriptor and data; verify readiness and retained report links after restoration. If the platform's rollback flow promotes production, stop and test restoration by a protected preview redeploy instead. Do not change an environment variable to switch current release.

## Rollback and review boundaries

Retain the last verified preview's commit, descriptor, four original pinned files and build receipt. Roll back code, descriptor and bundled data together. Restored deployments retain their previous environment values, so a credential rotation does not change an existing deployment's bundled data; a new rebuild needs working private artifact access. [Instant Rollback](https://vercel.com/docs/instant-rollback)

The hosted runtime serves immutable bundled SQLite data. Its filesystem is read-only; `/tmp` is ephemeral and is not a release store. Monthly ingestion, provenance review, numerical golden checks and activation receipts remain local reviewed operations. This historical staging build does not enable scheduled refreshes. [Function runtimes](https://vercel.com/docs/functions/runtimes)

Independent numerical/source review, corporate-action source coverage, participant comprehension, payment/retention evidence, and any remaining disclosed filing gaps remain governed by the existing [acceptance pack](pre-pilot-acceptance.md) and [refresh runbook](refresh-runbook.md). Passing engineering or protected-hosting checks does not complete those gates.
