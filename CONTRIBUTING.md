# Contributing to FindIt

FindIt depends on reproducible calculations and visible data limits. Changes
should make a reported number easier to verify, preserve unknown values, and
keep retained releases reproducible.

## Set up the checkout

Use Python 3.12, uv and Node.js 22 for the dashboard interaction tests:

```sh
uv sync --locked --extra dev --python 3.12
uv run --no-sync findit --help
```

The source tests create temporary databases. Real AMC disclosures, downloaded
prices, credentials and hosted release artifacts are unnecessary for the test
suite. The [README](README.md#try-a-synthetic-dashboard) provides an isolated
synthetic demo.

## Make a focused change

1. Create a branch for the issue or feature.
2. Identify the affected rule, command or route and its existing tests.
3. Preserve the package layers; calculation and storage code must not import
   the CLI or web layer. Layout tests check the established boundaries.
4. Add a regression test for a changed calculation, data-integrity rule or
   externally visible behavior. Use small synthetic inputs that expose the
   failure instead of copying private databases.
5. Update the relevant guide when a command, API, release requirement or
   interpretation changes.

When using coding agents, use the Codebase Memory graph first to locate the
affected code, then verify the relevant source before making changes. Follow
any additional applicable agent instructions supplied with the task.

## Preserve data and release semantics

- Keep unreadable values distinct from absent holdings and real zeroes.
- A comparison needs usable adjacent snapshots; failed or unavailable inputs
  must not become asserted buying, selling or no activity.
- Keep source provenance and explicit coverage gaps. A loaded count is not
  an independently reviewed inventory denominator.
- Work on database copies and new stage paths. Do not replace original
  disclosures, a retained release or a saved report to make a check pass.
- Changes to calculation semantics require explicit rules-version handling
  and replay tests for retained versions.
- Preserve private watchlist response controls and exact hosted origin rules.

The [refresh runbook](docs/refresh-runbook.md) describes reviewed local releases.
The [staging runbook](docs/vercel-staging.md) describes the private build contract
and hosted acceptance checks.

## Run the checks

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
node --test tests/web.test.cjs
git diff --check
```

For a targeted change, run its relevant tests while developing, then the full
checks before proposing it. CI covers Python 3.12 and 3.14, installed-wheel
behavior outside the checkout, packaged assets and dependency advisories.
Changes to packaging or hosted setup also need the applicable checks described
in [the staging verification report](docs/staging-implementation-verification-2026-10-06.md).

When changing dependency pins, regenerate `uv.lock`, review the resulting
versions and validate with the locked environment. Keep optional browser and
development dependencies separate from the hosted runtime.

## Open an issue or pull request

Use [GitHub issues](https://github.com/Varunsai1930/Find-It/issues) for defects
and proposals. Include the command or page, expected behavior, actual behavior,
report month, relevant version and a minimal synthetic example when possible.
Redact personal information and credentials; link an official public source
instead of attaching private disclosures or participant records.

A pull request should explain the concrete problem, the resulting behavior,
the checks run, and any data or deployment limitations. Keep documentation
claims tied to measured evidence. Passing automated tests does not complete
independent source review, participant acceptance or hosted deployment gates.

Contributions to project source use the [MIT License](LICENSE). Third-party
source data remains subject to its own terms.
