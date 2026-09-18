# MF Holdings Tracker — Phase 0 (working, tested)

This is the core data pipeline from the build plan, sections 4–9, built
and tested end-to-end on synthetic data. It proves the hard part — messy
AMC Excel parsing → storage → deltas → cross-fund consensus → a plain-English
summary — works, before spending any time on FII data, an API, or a UI.

No LLM is used anywhere in this phase. The AI narration layer comes later
and sits *on top of* `fallback_summary.py`'s numbers — see §7 of the build
plan for why that order matters.

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

- **Phase 1:** add FII/DII quarterly shareholding parsing (NSE/BSE), join
  it against `consensus_signals.py`'s output on ISIN for the full MF+FII
  overlap view.
- **Phase 2:** implemented as cached, constrained GLM narration over the
  existing rule summary; see the Phase 2 instructions below. Live API use
  requires `ZAI_API_KEY`.
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

## Phase 2 — cached AI-assisted summaries

The summary API and monthly digest now read `fund_summaries` when its source
hash is current. Otherwise they use the rule-based summary. Neither consumer
calls an AI provider or writes to the database.

The [implementation plan](docs/phase-2-plan.md) describes the design and scope.
Python supplies all financial facts and sentence variants. GLM chooses wording
and order through a strict JSON plan; it cannot insert prose, numbers, stock
names, or recommendations. This is constrained AI editing, not free-form
analysis. Each supplied fact must be retained exactly once.

### Generate a batch

Use an existing database populated by the monthly pipeline. For a safe local
trial, copy the database first:

```bash
cp tracker.db /tmp/findit-narration.db

# Read-only eligibility preview; no credentials, writes, or model calls.
python3 -m findit.cli.narrate --db /tmp/findit-narration.db \
  --month 2026-08 --dry-run

# Offline rule-based cache generation (the default).
python3 -m findit.cli.narrate --db /tmp/findit-narration.db \
  --month 2026-08 --provider rules

# Configure ZAI_API_KEY in your environment before this explicit network run.
python3 -m findit.cli.narrate --db /tmp/findit-narration.db \
  --month 2026-08 --provider zai --model glm-5.3

# Read the resulting summaries without network calls or message delivery.
python3 -m findit.cli.digest --db /tmp/findit-narration.db --month 2026-08
```

`--schemes 1 2` restricts the batch; `--force` regenerates eligible summaries.
Start with one scheme when checking a new API key. Existing matching source and
model versions are skipped. The provider uses Z.ai's
[official Chat Completions API](https://docs.z.ai/api-reference/llm/chat-completion),
a 60-second request timeout, at most two retries for transient failures, and a
bounded response budget. Credentials and raw provider errors are not logged.

The batch writes only the summary table. It does not rerun validation or change
holdings, deltas, flow calculations, or consensus. Current data must have an
`ok` or `validated` status and usable equity deltas. Quarantined current or
referenced previous months, non-finite inputs, and missing data cannot produce
an AI summary. Existing unvalidated data continues to use the ordinary rule
fallback. Run the normal ingestion/validation process to update its status.

Cache freshness includes raw input rows, security/fund metadata, validation
reports, trusted wording, and a narration policy version. A data change while
an API request is running prevents that response from being saved. API errors
and rejected model plans fall back to rules; a later AI batch can retry them.

The real provider adapter is covered by mocked HTTP tests. A live GLM request
was not run during implementation because `ZAI_API_KEY` was not configured.
