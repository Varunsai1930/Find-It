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

1. **Parse** — `amfi_mf_parser.py` turns an AMC's monthly Excel workbook into
   a clean CSV. It fails loudly on unrecognised columns instead of guessing.
2. **Load and validate** — `run_pipeline.py` loads parsed files into SQLite
   (`db.py`). A validation gate (`findit/store/validation_gate.py`) checks
   every scheme-month; one that fails is **quarantined**: kept for
   inspection, withheld from every ranking.
3. **Compare** — `delta_calculator.py` classifies each (scheme, stock) as new,
   added, trimmed, exited or unchanged against the previous month. Only
   schemes present in both months are compared.
4. **Rank** — `consensus_signals.py` counts AMCs buying minus AMCs selling per
   stock (the ranking key), counting only active stock-pickers. Flow into
   existing positions breaks ties. A discretionary count (changes beyond
   what price moves alone would cause) is reported beside it.
5. **Join filings** — quarterly FII/DII shareholding (`fetch_shareholding.py`,
   BSE or NSE) is joined by ISIN, using only filings published by the date
   the month's portfolios became public. A missing filing stays *missing*,
   never zero.
6. **Report** — `findit.cli.report` writes a one-page monthly report, and
   `findit.web` serves the dashboard. Both use the same
   `ranked_consensus`, so they cannot disagree.

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
```

The dashboard reads `./tracker.db`. To try it without real data, build one
from the synthetic fixtures, then start the server:

```bash
python3 make_test_fixtures.py
python3 amfi_mf_parser.py test_hdfc_march2026.xlsx --amc "HDFC AMC" --month 2026-03
python3 amfi_mf_parser.py test_hdfc_april2026.xlsx --amc "HDFC AMC" --month 2026-04
python3 amfi_mf_parser.py test_sbi_april2026.xlsx  --amc "SBI AMC"  --month 2026-04
python3 run_pipeline.py \
  --load test_hdfc_march2026.parsed.csv \
  --load test_hdfc_april2026.parsed.csv test_sbi_april2026.parsed.csv \
  --prev 2026-03 --curr 2026-04
uvicorn findit.web.app:create_app --factory   # http://127.0.0.1:8000
```

The pipeline prints the April ranking. Reliance Industries leads with one
AMC buying (HDFC). SBI also added it, but SBI's April file has no March file
to compare against, so SBI is listed as "no comparison" rather than counted
as buying its whole portfolio. Without a database, the dashboard says so
instead of showing an empty page.

GitHub Actions runs the tests and Ruff on Python 3.12 and 3.14 for every
push and pull request (`.github/workflows/ci.yml`).

## Data is not in the repo

The repository holds code only. `tracker.db`, `real_data/` (downloaded AMC
disclosures), the fetch caches and the generated `test_*.xlsx` fixtures are
git-ignored and live only on your machine. A fresh clone rebuilds them:
`make_test_fixtures.py` for the synthetic files, the AMCs' monthly
disclosure workbooks for real data, and `fetch_shareholding.py` /
`findit.cli.prices` for filings and prices. The test suite needs none of
them.

## Files

| File | What it does |
|---|---|
| `amfi_mf_parser.py` | Parses one AMC's monthly disclosure workbook into a clean CSV. |
| `db.py` | SQLite schema and loaders for parsed holdings and shareholding filings. |
| `run_pipeline.py` | The monthly command: load, validate, compare, rank, summarise. |
| `delta_calculator.py` | Month-on-month change per (scheme, stock). |
| `consensus_signals.py` | Cross-fund buying vs selling per stock, the FII/DII join, and the ranking. |
| `fetch_shareholding.py` | Quarterly shareholding filings from BSE or NSE. |
| `fallback_summary.py` | Template-based plain-English summary of one scheme's month. |
| `findit/core/` | Publication dates, active weights, corporate actions, instrument types. |
| `findit/store/validation_gate.py` | Validation checks that quarantine a bad scheme-month. |
| `findit/cli/` | Report, backtests, prices, re-validation, scheme aliases and titles, digest. |
| `findit/web/` | Read-only FastAPI dashboard (Jinja templates, vanilla JavaScript). |
| `make_test_fixtures.py` | Generates the synthetic workbooks used in the quick start. |

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
python3 -m findit.cli.rebuild --db tracker.db --db-copy /tmp/tracker.copy.db \
  --prev 2026-03 --curr 2026-04
python3 -m findit.cli.digest --db /tmp/tracker.copy.db --month 2026-04 --schemes 1 2
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

A scheme's stored name is the AMC's Excel *sheet* name (`SCRF`, `SETFNIF50`,
`SBI  Bluechip Fund`). Punctuation, casing and spacing drift between months is
absorbed automatically by `db.normalize_scheme_name`, so a re-punctuated sheet
keeps its existing `scheme_id` instead of forking the fund into two identities
and manufacturing a phantom full exit plus a phantom new fund in the deltas.

A *real* rename (`SCRF` -> `SBI Credit Risk Fund`) is a judgement call and is
never guessed. Record it yourself:

```bash
python3 -m findit.cli.alias --db tracker.db --amc "SBI AMC" --list
python3 -m findit.cli.alias --db tracker.db --add-alias SCRF --scheme-id 33
python3 -m findit.cli.alias --db tracker.db --merge-from 214 --merge-into 33
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
   automatically; for an existing DB:

   ```bash
   python3 -m findit.cli.scheme_titles --db tracker.db --amc "SBI AMC" \
     real_data/sbi_aug2026.xlsx --dry-run
   ```
4. **Months, not stocks, are the sample.** Stocks in one month move together,
   so the track record reports each period's excess return per group and a
   t-statistic *across periods*.

#### Monthly: the signal this project actually ranks

```bash
python3 -m findit.cli.prices --db tracker.db --date 2026-09-11 --date 2026-10-11
python3 -m findit.cli.backtest --db tracker.db --from 2026-07 --to 2026-08
```

`findit.cli.prices` is the only command that downloads prices. `--month`
stores month-end closes; `--date D` stores the first trading day on or after
D (both NSE bhavcopy formats: UDiFF from July 2024, legacy before), and the
backtests print the exact `--date` list they are missing. A holding period
that has not ended is marked `*`, valued at the latest stored close, and kept
out of the pooled line. `run_pipeline.py` prints the same track record under
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
python3 fetch_shareholding.py --db tracker.db --history 0          # every filing
python3 -m findit.cli.backtest_quarterly --db tracker.db           # lists missing closes
python3 -m findit.cli.prices --db tracker.db --date ... --date ... # as printed
python3 -m findit.cli.backtest_quarterly --db tracker.db
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
python3 -m findit.cli.report --db tracker.db --month 2026-08 --out report_2026-08.md
```

One page: coverage (who voted, who was excluded and why), the broadest buying
and selling with the discretionary count and FII/DII direction, the evidence
above restated from the live data, and the monthly signal's own track record.
It uses `consensus_signals.ranked_consensus`, the same ranking the dashboard
serves, so the two cannot disagree.

### Dashboard (`findit.web`)

```bash
uvicorn findit.web.app:create_app --factory
```

Read-only view of `./tracker.db` at http://127.0.0.1:8000.

- **Month** and **Buying / Selling** pick the view; rows, *Equity only* and
  *Active stock-pickers only* refine it. Every choice is kept in the URL, so a
  link reopens the same view.
- Three facts sit above the table: **schemes compared** (exactly the schemes
  the ranking counts under the current filters — a scheme loaded without a
  previous month, or withheld by validation, is never among them), **withheld**
  and the date the month's **portfolios became public**. **Data coverage**
  expands to the rest: schemes loaded and not compared, validation counts,
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
python3 -m findit.cli.revalidate --db tracker.db --dry-run
python3 -m findit.cli.revalidate --db tracker.db
```

`--dry-run` reports the status changes from a temporary copy and never opens
`--db` for writing. `--month` (repeatable) and `--schemes` restrict the run.
Source hashes and drop provenance are carried over from the existing status
rows, so re-validating never invents provenance the ingest did not record.

## Summaries (rule-based, no LLM)

`fallback_summary.build_summary` turns one scheme's computed deltas into a
plain-English paragraph. `findit.summary.get_summary` wraps it with the
data-safety checks the summary API and digest rely on:

- a quarantined current **or referenced previous** month is withheld, with
  wording that says so explicitly — a delta is a comparison, so bad data on
  either side makes it unsafe to describe;
- non-finite numbers or an unrecognised action are withheld the same way;
- "no data" is always reported as no data, never as no activity.

Neither consumer writes, calls a network service, or caches anything.

```bash
python3 -m findit.cli.digest --db tracker.db --month 2026-08 --schemes 1 2
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
genuinely valuable part and was kept, in `findit/summary.py`.
`docs/phase-2-plan.md` remains as the record of the design, and the code is in
git history if the decision is ever revisited with a job worth the constraint
budget — cross-fund synthesis, say, with the numbers still Python-computed.
