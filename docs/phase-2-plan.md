> **Superseded.** This phase was implemented and then removed: the model's
> only authority was reordering pre-written sentences and swapping synonyms,
> which did not justify a provider adapter, a hash-invalidated cache and a
> live API dependency. The eligibility logic described here was kept, in
> `findit/summary.py`. Retained as the record of the design.

# Phase 2: cached AI-assisted fund summaries

## Outcome

Generate a monthly fund summary once, persist it in `fund_summaries`, and serve it from the summary API and digest. Keep the existing rule-based summary available when data is unvalidated, the model is unavailable, or its response fails validation.

## Implementation plan

1. **Trusted snapshot and rendering.** Read existing equity deltas, holdings, identity, and validation reports. Hash the complete relevant input deterministically. Require validated current data, reject quarantined current or previous data, and do not send missing/non-finite data for narration.
2. **Bounded GLM-5.3 provider.** Use Z.ai's official Chat Completions API, environment-based credentials, bounded timeouts and retries, and no model tools. Send only the selected fund's summary facts.
3. **Constrained AI editing.** Python builds complete factual sentence variants from the existing rule summary. The model selects wording and order using a JSON plan. Every fact must appear exactly once. Rendering accepts only known fact IDs and variant indices; arbitrary prose, new numbers, recommendations, omissions, and duplicate facts are rejected. This is deliberately constrained narration, not free-form financial analysis.
4. **Batch cache.** Add a CLI with offline rules as the default, explicit Z.ai selection, dry-run, scheme selection, and refresh. Reuse cache only when source hash and generation/model version match. Recheck source data after the provider returns. Write summary rows only; never migrate or recalculate holdings in this command.
5. **Read-only consumers.** Summary API and digest read current cache or return the fallback. Neither consumer calls the provider or writes to SQLite. Cache eligibility is rechecked on every read.
6. **Verification.** Use temporary databases, a mocked provider, malformed-response and outage tests, input mutation tests, previous/current quarantine tests, and API/digest tests. Run the full existing test suite and lint. Exercise the rules batch and mocked AI batch against database copies.

## Work allocation

- Agent: trusted snapshots, rendering contract, and focused tests.
- Agent: provider adapter, transport error handling, and focused tests.
- Agent: cache service, batch CLI, and focused tests.
- Orchestrator: integration, documentation, cross-component tests, review, and final verification.

## Boundaries

No changes to holdings arithmetic, classification, consensus ranking, scheme identity, corporate-action adjustment, or delivery of emails/messages. No live AI calls during dashboard requests. Tests and demonstrations must not alter the committed `tracker.db` or downloaded data. API credentials stay outside the repository.

## Provider reference and live verification

The official [Chat Completion reference](https://docs.z.ai/api-reference/llm/chat-completion) documents `glm-5.3`, the endpoint `https://api.z.ai/api/paas/v4/chat/completions`, Bearer authentication, and JSON response mode. The provider is tested with mocked HTTP responses. A real API smoke test requires `ZAI_API_KEY`; that variable was not configured in the implementation environment.

## Verification completed

- Full suite: 258 tests passed; lint and whitespace checks passed. The suite emits one existing FastAPI/Starlette deprecation warning.
- Current database copy: 35 eligible summaries generated, then all 35 reused; no source-table changes by the summary batch.
- Separately refreshed validation on a database copy: 169 August summaries generated and then reused. The remaining 89 selections lacked usable equity comparisons or were otherwise ineligible.
- A mocked Z.ai HTTP response passed through the real provider adapter, strict renderer, persistent cache, summary API, and digest. Exactly one simulated provider call was made; the repeated generation was cached. API/digest reads did not modify the database.
- The committed `tracker.db` SHA-256 stayed `cdf99d0d9320002d1e0ee724767711a384ac7877cbdebb2ddb1eb4f23b2d3eb1`.
- Live provider verification remains pending environment configuration of `ZAI_API_KEY`. No API spending or live outbound model requests occurred during verification.
