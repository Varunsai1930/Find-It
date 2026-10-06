# FindIt

**Indian mutual fund holdings research, with traceable monthly comparisons.**

FindIt reads fund houses’ monthly portfolio disclosures, compares adjacent
months, and shows which stocks active fund managers added to or reduced.
A read-only dashboard and Markdown reports combine fund-house breadth,
estimated trading flows, coverage and source evidence. Quarterly FII/DII
filings are joined using publication cutoffs. Calculations and summaries run
in Python; no language model or provider account is required.

## Project status

FindIt is a historical research MVP. Rankings describe observed fund activity;
the recorded research has not established reliable outperformance against
comparable stocks. Missing disclosures and withheld comparisons remain visible.

As of **6 October 2026**, local staging engineering checks pass. Public launch
and hosted staging acceptance remain pending Vercel access, protected-preview
checks, independent source review and participant acceptance. See the
[implementation verification](docs/staging-implementation-verification-2026-10-06.md)
for measured results and the [staging runbook](docs/vercel-staging.md) for the
remaining deployment work.

## What it does

- Parses AMC Excel disclosures and records source provenance.
- Validates each scheme-month and withholds unsafe comparisons.
- Separates new positions from accumulation in existing holdings.
- Counts buying and selling fund houses, with active and equity filters.
- Explains stock-level evidence, coverage and fixed-cohort history.
- Exports private watchlist reports pinned to an immutable release and rules version.
- Retains corrected releases while preserving reproducible historical reports.

## Quick start

Use **Python 3.12** and [uv](https://docs.astral.sh/uv/). Node.js 22 is used only
for the dashboard interaction tests. Python 3.12 and 3.14 are covered by CI.

```sh
git clone https://github.com/Varunsai1930/Find-It.git
cd Find-It
uv sync --locked --extra dev --python 3.12
uv run --no-sync findit --help
```

The repository includes source, tests, documentation, screenshots and blank
evidence templates. Real portfolio workbooks, holdings databases, private
release bundles, credentials and participant records are not included.
The tests create temporary synthetic databases and require no external data.

### Try a synthetic dashboard

These POSIX-shell commands build the example in a new temporary directory,
keeping any existing holdings database untouched. The workbook names and
company names are illustrative; the numbers are synthetic.

```sh
FINDIT_PROJECT_DIR="$(pwd)"
FINDIT_DEMO_DIR="$(mktemp -d)"
cd "$FINDIT_DEMO_DIR"
"$FINDIT_PROJECT_DIR/.venv/bin/findit" fixtures
"$FINDIT_PROJECT_DIR/.venv/bin/findit" parse test_hdfc_march2026.xlsx --amc "HDFC AMC" --month 2026-03
"$FINDIT_PROJECT_DIR/.venv/bin/findit" parse test_hdfc_april2026.xlsx --amc "HDFC AMC" --month 2026-04
"$FINDIT_PROJECT_DIR/.venv/bin/findit" parse test_sbi_april2026.xlsx --amc "SBI AMC" --month 2026-04
"$FINDIT_PROJECT_DIR/.venv/bin/findit" pipeline \
  --load test_hdfc_march2026.parsed.csv \
  --load test_hdfc_april2026.parsed.csv test_sbi_april2026.parsed.csv \
  --prev 2026-03 --curr 2026-04 --db tracker.db
"$FINDIT_PROJECT_DIR/.venv/bin/findit" web --db tracker.db
```

Open <http://127.0.0.1:8000/?month=2026-04>. Stop the server with Ctrl+C and
return to the checkout with `cd "$FINDIT_PROJECT_DIR"`.
SBI has only an April snapshot in this example, so its holdings are disclosed
as having no comparison rather than counted as a complete new purchase.

### Use a prepared local database

```sh
uv run --no-sync findit web --db /absolute/path/to/tracker.db
uv run --no-sync findit report --db /absolute/path/to/tracker.db \
  --month 2026-08 --out report_2026-08.md
```

The web command listens on loopback and disables request access logs.
Data acquisition and refresh are separate local commands; serving the dashboard
does not download or ingest disclosures. Follow the
[refresh runbook](docs/refresh-runbook.md) for real data and the
[mentor demo guide](docs/mentor-demo.md) for a retained local presentation.

## Verification

From the checkout:

```sh
uv run --no-sync pytest -q
uv run --no-sync ruff check .
node --test tests/web.test.cjs
```

[CI](.github/workflows/ci.yml) runs the Python suite on 3.12 and 3.14, the
Node.js interaction tests, lint, an installed-wheel smoke check outside the
checkout, and known dependency-advisory checks. The
[verification report](docs/staging-implementation-verification-2026-10-06.md)
records the latest local results, size and performance gates, and their limits.
Private staging artifacts are needed only for the separate hosting/load checks.

## Project map

| Path | Purpose |
| --- | --- |
| `findit/core/` | Comparison, ranking, coverage, evidence, history and calculation versions. |
| `findit/store/` | SQLite storage, queries, validation and immutable releases. |
| `findit/ingest/` | Workbook parsing, intake, shareholding and price acquisition. |
| `findit/research/` | Monthly and quarterly backtests. |
| `findit/pipeline.py`, `findit/summary.py` | Monthly processing and rule-based summaries. |
| `findit/cli/` | The `findit` command and its subcommands. |
| `findit/web/` | FastAPI routes, Jinja templates and vanilla JavaScript assets. |
| `app.py`, `deployment/`, `tools/` | Hosted entrypoint, pinned build descriptor and verification utilities. |
| `tests/` | Synthetic-data calculation, ingestion, release, web and tooling regressions. |
| `docs/` | Operating guides, methodology, dated audits and review materials. |

Layer boundaries and package assets are checked by the layout tests.
See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and
[the documentation index](docs/README.md) for the complete guide list.

## License

The project source is available under the [MIT License](LICENSE).
Third-party portfolios, filings and market data retain their own terms and
are not included in this repository. Source-use decisions are recorded in
[the source review](docs/source-use-review.md).

## Detailed workflows

The [workflow and research reference](docs/research-and-workflows.md) retains
acquisition commands, ranking methodology, offline tools, scheme identity,
summary design and recorded historical backtest results.
