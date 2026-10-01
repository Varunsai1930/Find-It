# FindIt — mutual fund holdings tracker

FindIt reads the monthly portfolio disclosures Indian mutual funds publish,
works out what each scheme bought and sold since the previous month, and
counts how many fund houses (AMCs) moved the same way on each stock. It joins
that with each company's quarterly FII/DII shareholding filings, using only
filings that were public when the month's portfolios were, and serves the
result as a monthly report and a read-only web dashboard.

> **Rankings describe fund activity. They are not proven buy signals.**
> A ten-year test on quarterly filings found that no group of stocks funds
> were buying beat comparable stocks reliably once company size is
> controlled (see [Research findings](#research-findings)). Read every list
> here as a record of what funds did, not as investment advice.

No LLM is used anywhere. Every number is computed in Python, and summaries
are template-based (see "Why the GLM narration layer was removed" below).

## How it works

1. **Parse** — `findit/ingest/amfi_mf_parser.py` turns an AMC's monthly Excel
   workbook into a clean CSV. It fails loudly on unrecognised columns instead
   of guessing. It recognises Mirae's `Market/Fair Value (Rs. in Lacs)` header
   and parses UTI's stacked scheme sections separately.
2. **Load and validate** — `findit.cli.pipeline` loads parsed files into
   SQLite (`findit/store/db.py`). A validation gate
   (`findit/store/validation_gate.py`) checks every scheme-month; one that
   fails is **quarantined**: kept for inspection, withheld from every ranking.
3. **Compare** — `findit/core/delta_calculator.py` classifies each (scheme,
   stock) as new, added, trimmed, exited or unchanged against the previous
   month. Only schemes present in both months are compared.
4. **Rank** — `findit/core/consensus_signals.py` counts AMCs buying minus AMCs selling per
   stock (the ranking key), counting only active stock-pickers. Flow into
   existing positions breaks ties. A discretionary count (changes beyond
   what price moves alone would cause) is reported beside it.
5. **Join filings** — quarterly FII/DII shareholding
   (`findit/ingest/shareholding.py`, BSE or NSE) is joined by ISIN, using only filings published by the date
   the month's portfolios became public. A missing filing stays *missing*,
   never zero.
6. **Report** — `findit.cli.report` writes a one-page monthly report, and
   `findit.web` serves the dashboard. Both use the same `ranked_consensus`
   and the same coverage counts (`findit/core/coverage.py`), so they cannot
   disagree.

## Research findings

The claim behind the project — stocks many AMCs are buying beat comparable
stocks, more so when FIIs agree — has been tested, and does not hold up yet:

- **Quarterly, 2016–2026** (774 stocks, 40 quarters, entry after
  publication): MF ownership up with FII also up returned +0.63% a quarter
  against size-matched stocks (t=1.18); MF up alone −0.42% (t=−1.56); MF
  down −0.51% (t=−1.70). None of these is reliable.
- **Monthly:** only one signal month (August 2026) is stored, and its holding
  period has not finished. A t-statistic needs roughly 24 complete months.
  Moving the entry from month end to after publication turned that month's
  early result from +1.26% to −0.98%, which shows how much look-ahead can
  manufacture.

Details, and the rules that keep the measurement honest, are under
"Testing the signal before building on it" below.

## Quick start

Python 3.12 or newer.

```bash
pip install -e ".[dev]"
python3 -m pytest -q          # every test builds its own temporary database
ruff check .
node --test tests/web.test.cjs  # Node 22+, no npm dependencies
```

The dashboard reads `./tracker.db`. To try it without real data, build one
from the synthetic fixtures, then start the server:

```bash
findit fixtures
findit parse test_hdfc_march2026.xlsx --amc "HDFC AMC" --month 2026-03
findit parse test_hdfc_april2026.xlsx --amc "HDFC AMC" --month 2026-04
findit parse test_sbi_april2026.xlsx  --amc "SBI AMC"  --month 2026-04
findit pipeline \
  --load test_hdfc_march2026.parsed.csv \
  --load test_hdfc_april2026.parsed.csv test_sbi_april2026.parsed.csv \
  --prev 2026-03 --curr 2026-04
findit web --db tracker.db   # http://127.0.0.1:8000
```

The pipeline prints the April ranking. Reliance Industries leads with one
AMC buying (HDFC). SBI also added it, but SBI's April file has no March file
to compare against, so SBI is listed as "no comparison" rather than counted
as buying its whole portfolio. Without a database, the dashboard says so
instead of showing an empty page.

GitHub Actions runs the Python tests and Ruff on Python 3.12 and 3.14, plus
the dashboard interaction regressions on Node 22, for every
push and pull request (`.github/workflows/ci.yml`).

## Adding a month of disclosures

AMCs publish their monthly portfolios in different places and formats. The
download command covers nine major houses: SBI, ICICI Prudential, HDFC,
Nippon India, UTI, Aditya Birla Sun Life, Mirae Asset, DSP, and PPFAS.
Install its optional browser dependency
once (`pip install -e '.[download]'`). It uses installed Chrome if available;
otherwise install Playwright Chromium with `python -m playwright install chromium`.
Preview a published month, then download it:

```bash
findit download --month 2026-08 --dry-run
findit download --month 2026-08 --prepare
findit intake --month 2026-08
```

Downloads are checked as real Excel/ZIP files and staged before an AMC's folder
is written. A completed download has a checksum manifest and a repeat run
reuses it after checking every file. A changed or hand-filled folder is left
untouched for review. `--prepare` runs intake validation after successful
downloads; it prints the database load command but never executes it. If a
source fails, the command prints its official page and the exact folder for a
manual download, then exits with an error. ICICI also tries its public archive
before the browser listing. Kotak (CAPTCHA) and Axis still require manual
downloads; the command lists their pages every full run. For other houses,
use the manual intake flow:

```bash
findit intake --month 2026-09 --init   # folders for AMCs already tracked
# download each AMC's September files into real_data/inbox/2026-09/<AMC name>/
findit intake --month 2026-09          # check, parse, print the load command
```

Each folder is named as the database names the fund house ("SBI AMC",
"Nippon India AMC"); `findit/ingest/amfi_amcs.json` lists all 57 from AMFI,
with each one's disclosure page, and the report ends with the ones not yet
downloaded. ZIP extraction is temporary: intake removes `_unzipped` afterward,
including when processing fails; source archives and accepted parsed CSVs are
retained. For UTI's official portfolio ZIP, intake selects the
`Sebi Exposure as on ...` holdings workbook and excludes auxiliary riskometer,
dividend and futures tables. An AMC is refused, with nothing written for it,
when its files would load wrong data silently: a folder name AMFI does not
list, the same sheet in two files (a consolidated and a per-scheme download),
a sheet titled as another fund house's scheme, a workbook dated another month,
or a workbook the parser cannot read. The intake never downloads anything and
never writes the database: it prints the `findit.cli.pipeline` command that loads
the accepted CSVs, and running it is your step. A new AMC needs two months
loaded before its schemes are compared.

Running a month again is safe. Each scheme-month in a file replaces what was
stored for it (a stock a corrected file drops is dropped), and each file loads
in one transaction, so a failure leaves the previous state rather than half a
month. Reloading identical files changes nothing. When a reload does change
a scheme-month's numbers, its validation status and every comparison built on
the old numbers are cleared, and the load names any later month that must be
re-run. The run log (`ingest_runs`) records `running`, then `completed` only
once the last step has finished, or `failed` with the error.

When an AMC renames a sheet between months, the two sheets load as separate
schemes and neither is compared. Check with
`findit alias --db tracker.db --amc "DSP AMC" --list` after a
load and merge renames as described under "Scheme identity" below.

## Data is not in the repo

The repository holds code only. `tracker.db`, `real_data/` (downloaded AMC
disclosures), the fetch caches and the generated `test_*.xlsx` fixtures are
git-ignored and live only on your machine. A fresh clone rebuilds them:
`findit.cli.fixtures` for the synthetic files, the AMCs' monthly
disclosure workbooks for real data, and `findit.cli.shareholding` /
`findit.cli.prices` for filings and prices. The test suite needs none of
them.

## Layout

All code lives in the `findit` package. Imports only point down this list,
and `tests/test_layout.py` fails the build if one points up: a lower layer
reaching into a higher one is how a rule ends up defined twice.

Each calculation is a pure function over DataFrames (`diff_holdings`,
`rank_consensus`, `add_shareholding_signal`, `render_summary`); the function
that reads the database hands it the rows, so any number can be reproduced
from those rows alone (`tests/test_pure_rules.py`).

| Layer | What it holds |
|---|---|
| `findit/core/` | The rules: month-on-month changes (`delta_calculator`), the consensus ranking and FII/DII join (`consensus_signals`), coverage counts, publication dates, active weights, corporate actions, instrument types. |
| `findit/store/` | SQLite schema and loaders (`db`), the shared reads every rule is applied through (`queries`: validation status, active-scheme filter, quarterly filings, prices), and the validation gate's checks. |
| `findit/ingest/` | Reading outside data: AMC workbooks (`amfi_mf_parser`), a month's intake, shareholding filings, NSE prices. `amfi_amcs.json` lists AMFI's 57 fund houses. |
| `findit/summary.py` | Rule-based plain-English summary of one scheme's month, withheld when its data failed validation. |
| `findit/research/` | The monthly and ten-year quarterly backtests. |
| `findit/pipeline.py` | The monthly run's steps: provenance, the validation gate, the FII/DII overlap. |
| `findit/cli/` | Every command, as `findit <command>` once installed (`findit --help` lists them), or `python3 -m findit.cli <command>` without installing. |
| `findit/web/` | Read-only FastAPI dashboard (Jinja templates, vanilla JavaScript). |

### How the ranking treats new positions

Consensus separates `new_position_flow_lakhs` from `accumulation_flow_lakhs`.
A new position books its entire market value as flow, so without the split an
IPO or fresh listing that every fund "bought" because it began existing would
outrank real accumulation. `universe_status` is `established`, `new_listing`,
or `unknown` when no previous month is on record. Ranking leads with breadth
(`net_amc_count`); the tiebreak is accumulation flow, not a position's entry
value.

## Operations (offline)

```bash
pip install -e ".[dev]"
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_tooling.py
findit rebuild --db tracker.db --db-copy /tmp/tracker.copy.db \
  --prev 2026-03 --curr 2026-04
findit digest --db /tmp/tracker.copy.db --month 2026-04 --schemes 1 2
```

Rebuild copies the DB first and computes deltas via `delta_calculator` on the
copy only; digest prints rule-based summaries with the same safety checks (no sending,
no credentials). Both are offline and never write `./tracker.db` or `real_data/`.

### Validation gate failure policy

A check inside `validate_holdings_month` that raises is reported as a
`<check>_crashed` error and fails the gate. A validator that could not run has
not validated anything, so the scheme-month is quarantined for a human rather
than published on the strength of checks that silently did not happen.
Likewise, `_quarantined_scheme_ids` returns `[]` only for a genuinely absent
table; any other database error propagates, because silently returning `[]`
would publish quarantined schemes as though they had passed.

### Scheme identity (`findit.cli.alias`)

A scheme's stored name normally comes from the AMC's Excel *sheet* name
(`SCRF`, `SETFNIF50`, `SBI  Bluechip Fund`); UTI's stacked layout uses each
section's `SCHEME:` title. Punctuation, casing and spacing drift between months
is absorbed automatically by `db.normalize_scheme_name`, so a re-punctuated name
keeps its existing `scheme_id` instead of forking the fund into two identities
and manufacturing a phantom full exit plus a phantom new fund in the deltas.

A *real* rename (`SCRF` -> `SBI Credit Risk Fund`) is a judgement call and is
never guessed. Record it yourself:

```bash
findit alias --db tracker.db --amc "SBI AMC" --list
findit alias --db tracker.db --add-alias SCRF --scheme-id 33
findit alias --db tracker.db --merge-from 214 --merge-into 33
```

`--merge-from` moves every holdings/delta/status/summary row onto the surviving
`scheme_id`, keeps the old sheet name as a resolvable alias, then deletes the
duplicate. It refuses to merge across AMCs, and refuses when both schemes hold a
row for the same (isin, month) — that is a data conflict, not a rename, and
dropping one side silently is exactly the invisible wrong number this project
refuses to produce. Two schemes that already share a normalized key make
ingestion raise `AmbiguousSchemeError` naming both IDs rather than picking one.

### Testing the signal before building on it

The project's claim -- stocks many AMCs are buying beat comparable stocks,
more so with FII/DII agreement -- is a hypothesis, and everything downstream
(ranking, dashboard) is only worth building if it holds. Four rules keep the
measurement honest:

1. **Only use what was public.** A month's portfolios are public on the SEBI
   deadline (month end + 10 days), a company's shareholding pattern when BSE
   broadcast it (`published_at`, recorded for every filing) -- or its 21-day
   deadline when that was never observed, which the output counts. Positions
   are entered at the close of the first trading day *after* publication.
   Before this, the backtest entered at the month-end close and the FII/DII
   join admitted any quarter ending by month end: both used data nobody had.
2. **Count managers' choices, not their inflows.** A fund with inflows buys
   more of everything. `findit.core.active_weight` compares each stock's weight
   now with the weight it would have had with no trading (last month's shares
   at this month's prices). `net_active_amc_count` counts AMCs whose change
   beats that drift. Splits and bonuses are detected from the holdings (every
   holder's quantity on the same multiple, price on its inverse) so they are
   not read as buying. It is reported beside `net_amc_count`; the ranking
   still uses `net_amc_count` until the track record says otherwise.
3. **Only active stock pickers vote.** Index funds, ETFs, arbitrage and
   equity-savings funds, FoFs and debt funds are excluded -- decided from the
   workbook's own scheme name ("SBI Arbitrage Fund"), not the sheet code
   ("SAOF"), which the old heuristic could not read. New ingests record it
   automatically; backfill missing titles in an existing DB from workbooks:

   ```bash
   findit scheme-titles --db tracker.db --amc "SBI AMC" \
     real_data/sbi_aug2026.xlsx --dry-run
   ```

   To reclassify using already stored titles, without workbooks:

   ```bash
   findit scheme-titles --db tracker.db --from-db --dry-run
   ```

   Omit `--dry-run` to apply classification changes. Schemes without stored
   titles still need workbook backfill. These commands update classification
   flags, not holdings.
4. **Months, not stocks, are the sample.** Stocks in one month move together,
   so the track record reports each period's excess return per group and a
   t-statistic *across periods*.

#### Monthly: the signal this project actually ranks

```bash
findit prices --db tracker.db --date 2026-09-11 --date 2026-10-11
findit backtest --db tracker.db --from 2026-07 --to 2026-08
```

`findit.cli.prices` is the only command that downloads prices. `--month`
stores month-end closes; `--date D` stores the first trading day on or after
D (both NSE bhavcopy formats: UDiFF from July 2024, legacy before), and the
backtests print the exact `--date` list they are missing. A holding period
that has not ended is marked `*`, valued at the latest stored close, and kept
out of the pooled line. `findit.cli.pipeline` prints the same track record under
each month's ranking, and the web dashboard ranks with the same
`compute_consensus` and FII/DII join -- one implementation, so the two can
never disagree, and an older month never shows filings published after it.

Deltas compare only schemes present in both months. A fund launched this
month, or one whose previous file was not loaded, is listed as "no
comparison" rather than counted as buying (or selling) its whole portfolio.

The first measurement changed under rule 1. With a month-end entry, August's
MF-only buying beat the universe by +1.26%; entering after publication
(2026-09-11, first 7 trading days only) it is -0.98%. Neither number is
evidence -- one partial month -- but the difference shows how much of a
one-month result the look-ahead can manufacture.

#### Quarterly: ten years, every company

Monthly AMC files here cover a few months; they cannot settle the question.
Every listed company's shareholding pattern can: one format, back to 2015,
with mutual-fund ownership (`mf_pct`) and FPI ownership as separate lines.

```bash
findit shareholding --db tracker.db --history 0     # every filing
findit backtest-quarterly --db tracker.db           # lists missing closes
findit prices --db tracker.db --date ... --date ... # as printed
findit backtest-quarterly --db tracker.db
```

The fetcher reads both BSE formats (inline-XBRL HTML for recent quarters,
XBRL instance XML for 2016-2025), keeps a stored filing unless it predates
`mf_pct`, and fills `published_at` from BSE's filing index. `--universe nse`
fetches every NSE-listed equity instead of those tracked schemes hold -- a
broader universe, at roughly 45 requests per company. The backtest decides
30 days after quarter end (`--decision-lag-days`), uses a filing only if it
was published by then (late filers sit that quarter out), and groups stocks
by the change in MF ownership (`mf_up_fii_up`, `mf_up_only`, `mf_flat`,
`mf_down`, top/bottom fifth). It prints two track records: raw, and
size-neutral -- each stock against stocks in the same third of that quarter's
universe by entry-day turnover, because MF ownership swings are larger in
smaller, riskier stocks. Survivorship (delisted firms are missing) is printed
with every run.

Newly persisted shareholding source attachments are gzip-compressed in the
fetch cache as `attachments/<sha256>.ixbrl.gz`. SHA-256 is calculated from the
original uncompressed bytes; existing cache files are not automatically migrated.

**Two sources.** BSE's filing API rate-limits heavy use (a full history run
was blocked for over a day). `--source nse` reads the same filings from NSE's
index and XBRL archive, from September 2021, through the same parser; per
quarter the earliest broadcast is kept, since a later revision was not public
on the original date. The fetcher stops after five stocks in a row fail on the
network rather than failing the rest in seconds, and summarises failures by
reason; re-running any fetch resumes, because stored filings are skipped.

**What it found** (774 stocks, 40 quarters, 2016-2026): no group beats
comparable stocks reliably once size is controlled. MF ownership up with FII
also up: +0.63% a quarter size-neutral (t=1.18); MF up alone: -0.42% (t=-1.56);
MF down: -0.51% (t=-1.70). The monthly lists below are a record of what funds
did, not a buy list, until more evidence says otherwise.

#### The monthly report

```bash
findit report --db tracker.db --month 2026-08 --out report_2026-08.md
```

One page: coverage (who voted, who was excluded and why), the broadest buying
and selling with the discretionary count and FII/DII direction, the evidence
above restated from the live data, and the monthly signal's own track record.
It uses `consensus_signals.ranked_consensus`, the same ranking the dashboard
serves, so the two cannot disagree.

### Dashboard (`findit.web`)

```bash
findit web --db tracker.db
# Without installing the console command:
python3 -m findit.cli web --db tracker.db
```

For a local presentation, follow [the mentor demo guide](docs/mentor-demo.md).
The [readiness audit](docs/readiness-audit.md) records official inventory scope,
verified repairs and open acceptance items. Use the
[mentor evidence packet](docs/mentor-evidence-packet.md),
[refresh/revision runbook](docs/refresh-runbook.md) and
[customer-validation kit](docs/customer-validation-kit.md) for the review and pilot.
The UI supports light and dark appearances, readable phone layouts, keyboard
stock search, and a stock detail drawer.

Read-only view of `./tracker.db` in the current working directory at http://127.0.0.1:8000.
To choose a different database, including when running from an installed package:

```bash
FINDIT_DB=/absolute/path/to/tracker.db uvicorn findit.web.app:create_app --factory
```

Keep the default loopback address for a local mentor demo. The dashboard needs no
network connection once the database is built. Missing, invalid or unrelated databases
show a setup message and return HTTP 503 instead of a misleading empty report.

- **Month** and **Buying / Selling** pick the view; rows, *Equity only* and
  *Active stock-pickers only* refine it. Every choice is kept in the URL, so a
  link reopens the same view.
- Three facts sit above the table: **schemes compared** (exactly the schemes
  the ranking counts under the current filters — a scheme loaded without a
  previous month, withheld by validation, or compared against a previous
  month that was itself withheld, is never among them), **withheld**
  and the date the month's **portfolios became public**. **Data coverage**
  expands to the rest: schemes loaded and not compared (including how many
  were compared against a withheld previous month), validation counts,
  filing freshness, the method, and the last ingest run (database-wide; the
  ingest log does not record every load, so it is never shown as the month's).
- The table is the same `ranked_consensus` ranking as the report (selling uses
  `broadest_selling`) and shows stock, AMCs net, schemes buying/selling,
  existing-position flow and filing status. **More columns** adds
  discretionary net, new-position flow and the FII/DII changes. Filters
  re-render the one table via `/fragments/month/{month}`.
- **Find a stock** suggests companies as you type (name or ISIN prefix, via
  `/api/stocks/search`; arrow keys and Enter choose). Choosing one, or a stock
  in the table, opens its detail for the selected month and filters: the
  month's activity including the hidden columns, every scheme's holding, and
  its quarterly filings. Filings published after that month's portfolios went
  public are flagged, never silently used. Escape closes it.
- A holding from a scheme that failed validation that month stays visible for
  inspection but is marked **withheld**: its action is shown as raw text, not
  as buying or selling, because the ranking leaves it out. A scheme with no
  validation result is marked *not validated*, and a database where
  validation never ran says so. `/api/stock/{isin}` carries the same status
  per holding (`validation_status`: `ok`, `quarantined`, `not_validated` or
  `not_run`).
- **Scheme summary** takes a typed scheme or AMC name (↓ browses the list) and
  follows the selected month.

The ranking behind a month is cached in memory until the database file
changes, so switching views and opening stocks does not recompute it.

### Re-validation (`findit.cli.revalidate`)

Statuses in `scheme_month_status` are whatever the gate's rules said on the day
the data was ingested. When those rules change, a database can carry quarantines
the current code would never produce — and the consensus filter and summary
eligibility still read them, withholding good data. Re-run the gate over stored
holdings, with no CSVs and no network:

```bash
findit revalidate --db tracker.db --dry-run
findit revalidate --db tracker.db
```

`--dry-run` reports the status changes from a temporary copy and never opens
`--db` for writing. `--month` (repeatable) and `--schemes` restrict the run.
Source hashes and drop provenance are carried over from the existing status
rows, so re-validating never invents provenance the ingest did not record.

## Summaries (rule-based, no LLM)

`findit.summary.build_summary` turns one scheme's computed deltas into a
plain-English paragraph. `findit.summary.get_summary` wraps it with the
data-safety checks the summary API, digest and pipeline rely on -- always
call that one, since `build_summary` only renders:

- a quarantined current **or referenced previous** month is withheld, with
  wording that says so explicitly — a delta is a comparison, so bad data on
  either side makes it unsafe to describe;
- non-finite numbers or an unrecognised action are withheld the same way;
- "no data" is always reported as no data, never as no activity.

Neither consumer writes, calls a network service, or caches anything.

```bash
findit digest --db tracker.db --month 2026-08 --schemes 1 2
```

### Why the GLM narration layer was removed

An earlier phase put a constrained GLM editor over these summaries: Python
computed every number and wrote every sentence, and the model returned only
`{"fact_id", "variant_index"}` pairs, which a strict renderer validated.

The containment was sound and the idea was defensible, but the model's entire
authority came down to reordering pre-written sentences and choosing between
wordings like "Added to" and "Increased holdings in". That is not worth ~1,000
lines, a provider adapter, a cache with hash-based invalidation, and a live API
dependency that was never actually exercised — `ZAI_API_KEY` was never
configured, so the real provider path only ever ran against mocks.

It was removed rather than left dormant. The output is unchanged, because the
model never produced any of it. The eligibility logic it carried was the
genuinely valuable part and was kept, in `findit/summary.py`. The design
(`docs/phase-2-plan.md`) and the code are in git history if the decision is
ever revisited with a job worth the constraint
budget — cross-fund synthesis, say, with the numbers still Python-computed.

### Updating previously parsed NAV weights

Parsing uses an explicitly printed portfolio total to identify fractional or
percentage NAV units. When no unambiguous total exists, it falls back to the
sum of detail rows; ambiguous values remain raw. Category headings and
post-portfolio derivative disclosures do not establish additional portfolio
weight. Original weights remain in `pct_nav_raw`.

A database loaded with an older parser must be **reparsed from its source
workbooks and reloaded** to correct its stored NAV units. Re-validation alone
changes validation statuses, not stored weights. Work on a database copy,
load both months, and recompute the comparison with the pipeline before
using the corrected database.
