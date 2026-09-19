# MF Holdings Tracker — Phase 0 (working, tested)

This is the core data pipeline from the build plan, sections 4–9, built
and tested end-to-end on synthetic data. It proves the hard part — messy
AMC Excel parsing → storage → deltas → cross-fund consensus → a plain-English
summary — works, before spending any time on FII data, an API, or a UI.

No LLM is used anywhere. An AI narration layer was built and then removed;
see "Why the GLM narration layer was removed" below. Summaries are produced
by `fallback_summary.py` from numbers Python computed.

## Files

| File | What it does |
|---|---|
| `amfi_mf_parser.py` | Parses one AMC's monthly disclosure Excel workbook into a clean CSV. Fails loudly on unrecognized columns instead of guessing. |
| `db.py` | SQLite schema (schemes, stocks, monthly holdings, deltas) + a loader for parser output. |
| `delta_calculator.py` | Compares two months for every (scheme, stock) pair, classifies each as new / added / trimmed / exited / unchanged. |
| `consensus_signals.py` | Counts how many distinct funds/AMCs bought vs. sold each stock in a month — this is the actual "common holdings" signal. |
| `fallback_summary.py` | Turns one scheme's computed deltas into a short plain-English paragraph. Purely template-based, no AI. |
| `run_pipeline.py` | The one command you run each month once this is wired to real data: load → delta → consensus → summaries. |
| `make_test_fixtures.py` | Generates the synthetic test files below. Not needed once you're using real AMFI data. |

## Try it right now (no real data needed yet)

```bash
pip install pandas openpyxl --break-system-packages
python3 make_test_fixtures.py
python3 amfi_mf_parser.py test_hdfc_march2026.xlsx --amc "HDFC AMC" --month 2026-03
python3 amfi_mf_parser.py test_hdfc_april2026.xlsx --amc "HDFC AMC" --month 2026-04
python3 amfi_mf_parser.py test_sbi_april2026.xlsx  --amc "SBI AMC"  --month 2026-04

python3 run_pipeline.py \
  --load test_hdfc_march2026.parsed.csv \
  --load test_hdfc_april2026.parsed.csv test_sbi_april2026.parsed.csv \
  --prev 2026-03 --curr 2026-04
```

You should see a consensus table where Reliance Industries shows
`amcs_buying = 2` (both synthetic AMCs added to it in April) — that's the
core signal your father described, working.

## Next step — the one that needs you

I can't reach amfiindia.com from this sandbox, so `COLUMN_SYNONYMS` in
`amfi_mf_parser.py` is seeded from the SEBI-prescribed column names but
has **not** been tested against a real file. Grab one real monthly
disclosure (amfiindia.com → Research & Information → Other Data →
Monthly Portfolio Disclosures — start with a large AMC like HDFC or SBI,
they tend to be cleaner) and either:
- run `amfi_mf_parser.py` on it yourself and see what breaks, or
- upload the `.xlsx` here and I'll run it and fix `COLUMN_SYNONYMS`
  against what it actually contains.

Either way, the parser is designed to fail with a clear message telling
you exactly which column it couldn't find — it won't silently get a
number wrong.

## After that (per the roadmap in the build plan)

- **Phase 1 (done):** BSE quarterly FII/DII parsing (`fetch_shareholding.py`,
  `db.load_shareholding_records`) joined against `compute_consensus()` on
  ISIN via `join_shareholding_increase()` for the full MF+FII overlap view.
  `run_pipeline.py` prints the overlap each month with an `as_of_month`
  no-lookahead cutoff; missing filings stay `no_data` (never zero) and stale
  quarters are flagged. `fetch_shareholding.py --report-month YYYY-MM` prints
  the same overlap after a fetch.

  Consensus separates `new_position_flow_lakhs` from
  `accumulation_flow_lakhs`. A new position books its entire market value as
  flow, so without the split an IPO or fresh listing that every fund "bought"
  because it began existing outranks real accumulation. `universe_status` is
  tri-state like the FII/DII directions — `established`, `new_listing`, or
  `unknown` when no previous month is on record. Ranking still leads with
  consensus breadth (`net_amc_count`); the split only orders names within one
  breadth level, and the tiebreak is accumulation flow rather than a position's
  entry value.
- **Phase 2:** built as cached, constrained GLM narration over the rule
  summary, then **removed** — the model's only authority was reordering
  pre-written sentences. See "Why the GLM narration layer was removed".
- **Phase 3:** dashboard UI (Top-5 cards, common holdings screener) — will
  need daily price data too, which nothing here ingests yet.

## Operations (offline)

```bash
pip install -e ".[dev]"
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/test_tooling.py
python3 -m findit.cli.rebuild --db tracker.db --db-copy /tmp/tracker.copy.db \
  --prev 2026-03 --curr 2026-04
python3 -m findit.cli.digest --db /tmp/tracker.copy.db --month 2026-04 --schemes 1 2
```

Rebuild copies the DB first and computes deltas via `delta_calculator` on the
copy only; digest prints a preview from cached rule summaries (no sending,
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

### Month-end prices and signal evaluation

`findit.cli.prices` is the only command that downloads prices. It reads NSE's
UDiFF bhavcopy, which carries an ISIN column, so prices join to holdings with
no symbol-mapping step to get wrong. It walks back from the calendar month end
to the real last trading day, never into a later month, caches each day's zip
in `.price_cache/`, and writes only `security_prices_monthly`. That table is
deliberately separate from `instrument_prices_monthly`, which holds prices
*implied* by fund holdings and therefore cannot be used to check the holdings
they came from.

```bash
python3 -m findit.cli.prices --db tracker.db --month 2026-07 --month 2026-08
python3 -m findit.cli.backtest --db tracker.db \
  --signal-month 2026-08 --forward-month 2026-09
```

`findit.cli.backtest` scores each signal group's forward return against the
*tracked universe* — every equity any tracked scheme held that month — which
holds the selection process fixed and varies only the signal. It is read-only.

Its output always carries the sample-size caveat, and you should take it
seriously: one signal month is one event, not a sample. The permutation `p`
compares against random subsets of the same universe and ignores that stocks
move together, so it is optimistic. It is a cross-sectional comparison, not a
strategy return — no costs, no liquidity limits, no position sizing.

The first measurement (2026-08 -> 2026-09-18, 766 priced equities) is worth
knowing before building anything else on the signal: MF-only net buying beat
the universe by +1.26% (n=98, p=0.035), while adding the FII/DII agreement
filter — the project's headline idea — did *worse*, at +0.28% (n=191,
p=0.275). On one month that settles nothing, but it is the opposite of the
assumed direction, so the overlay needs evidence before it earns its place in
the ranking.

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
