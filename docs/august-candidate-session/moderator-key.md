# Round B — owner and moderator only

Prepared 2 October 2026. The owner selected `B-candidate-65594777209e` and confirmed **neither R01 nor P01–P05 is arranged**. No sessions or independent review have occurred. Share only [participant instructions](participant-instructions.md) with participants. Do not show this key, reviewer calculations or findings sheets before their tasks.

Frozen release: `65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4`. Rules: `readiness-2026-10-01`. Application source commit: `439a0f9`; setup baseline `847d34f` contains subsequent documentation only. Month: August 2026; active domestic equity. Keep this application and release unchanged throughout the round. A release or application change requires a separately identified round, answer key and results; preserve prior observations.

Preview: <http://127.0.0.1:65102/>. Local [round record](../../real_data/readiness/observed-acceptance/rounds/B-candidate-65594777209e/round.json), [blank P01–P05 records](../../real_data/readiness/observed-acceptance/rounds/B-candidate-65594777209e/records.md), [blank R01 findings](../../real_data/readiness/observed-acceptance/rounds/B-candidate-65594777209e/R01-findings.md), [candidate record](../../real_data/readiness/september-2026/august-review/candidate-record.json). If the preview stops, run on the owner's computer:

```sh
cd /Users/varun/Downloads/Varun/Find_v2
PYTHONPATH=. .venv/bin/python real_data/readiness/observed-acceptance/rounds/B-candidate-65594777209e/serve_preview.py
```

The launcher uses the frozen candidate explicitly and binds only to `127.0.0.1`. Retained August and `current.json` stay unchanged. The local record and anonymized observation sheets are excluded from Git. Keep access and deletion arrangements with the consent record outside the project.

[Setup checks](../../real_data/readiness/observed-acceptance/rounds/B-candidate-65594777209e/setup-checks.json) record seven passing preview/key/reopening checks. Fourteen recorded hashes match, including candidate DB/manifest, eight review references, original databases, retained DB and current pointer. All handoff links resolve. The prior 431 Python/12 JavaScript/lint results apply to the unchanged application; the completed anomaly and saved-file audits were reused. These setup checks are agent work, not human acceptance.

## Recruitment drafts — owner sends only after choosing people

Independent reviewer:

> Would you independently review FindIt’s August research candidate? I’m looking for a finance/accounting mentor or equity researcher who was not involved in implementation. Please allow about 60–90 minutes initially, with more time if calculations need follow-up. You’ll check a bounded set of official-source rows, quantities, estimated values, fund scope and release identity, and record disagreements or unresolved questions. Passing agent checks is not a sign-off. I can provide the originals, exact-row supplement and blank findings sheet locally. May I arrange a review with you? We’ll use ID R01 in project notes and keep your contact details private.

Target participant (send individually to five people):

> I’m seeking people who already check mutual-fund stock holdings for Indian equity research to try FindIt in a 35-minute moderated session. First you’ll show a nonsensitive example of your current workflow; then you’ll try five historical research tasks and think aloud without hints. We’re testing the product, not you, and you can stop at any time. With your consent I’ll take anonymized notes under a participant ID; recording would require separate consent. This is a local prototype session, with no purchase required. Would you be willing to participate?

Do not send messages automatically. Agree consent, note access/deletion and scheduling privately. The localhost URL is for the moderator's computer, not a recruitment link or a remote participant's computer.

## R01: materials and exact review tasks

Use the existing [source bundle](../../real_data/readiness/observed-acceptance/reviewer-bundle/README.md), its originals and [blank findings sheet](../../real_data/readiness/observed-acceptance/reviewer-bundle/FINDINGS.md). Add the candidate [manifest](../../real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.json), [database](../../real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.db), [original-source manifest](../../real_data/readiness/september-2026/historical-source-manifest.json), [dated inventories](../../real_data/readiness/september-2026/historical-inventories), [official originals](../../real_data/readiness/september-2026/historical-inbox), and [historical findings sheet](../../real_data/readiness/september-2026/HISTORICAL-FINDINGS.md). The old bundle identifies retained August; use its unchanged cases alongside this candidate supplement, not its old all-house totals as candidate answers. Local blank R01 copies are linked above.

1. Verify eligible inventory counts and official identity continuity, especially Kotak KIP's publisher key/index. Check SBI Balanced Hybrid's official launch and first snapshot; do not invent July holdings. Confirm seven complete houses, SBI 39/40, selected comparison scope 219/220 and unknown market completeness.
2. Check cash versus derivative/exposure exclusions in the [correction ledger](../../real_data/readiness/september-2026/august-review/superseded-stage-corrections.json): 19 corrected comparison rows and two removed derivative-only comparisons. Reproduce the source quantities using the recorded sheets/rows, not names alone.
3. Recalculate the selected increases, reductions, additions and exits in [selected original cases](../../real_data/readiness/september-2026/august-review/selected-original-cases.json), plus all contributors to the [Kotak/HSBC headline cases](../../real_data/readiness/september-2026/august-review/featured-original-cases.json). Verify original hashes, ISINs, dates, quantities, value units and opposite-month absence checks. First record your own figures, then compare with agent expectations.
4. Challenge the [warning dispositions](../../real_data/readiness/september-2026/august-review/engineering-review.json) and [price review](../../real_data/readiness/september-2026/august-review/price-warning-review.json), including unknown annotated cells. Recheck the original bundle's real Reliance 1:1 bonus example: 24,300 → 48,600, adjusted change zero. Its 2024 source/calculator example is separate from the August 2026 release, which contains no corporate-action adjustments.
5. Reconcile candidate versus retained scope in [before/after results](../../real_data/readiness/september-2026/august-review/before-after.json). Verify unchanged old-house calculations, Franklin's fixed-cohort history versus the broader monthly Reliance total, and reopen both reports' release-pinned evidence through the preview. Keep the retained report's release query unchanged. Record release, rules, scope and original rows for each result.

Classify supplied findings as confirmed error, explained difference or unresolved question; include the reviewer’s own calculation, exact source reference, materiality and required recheck. Material numerical or scope questions block acceptance. Existing agent checks and synthetic fixtures are supporting evidence only. The 60–90 minutes is a scheduling estimate, not a completed review or a deadline to sign off.

## P01–P05: run the existing protocol

Use a fresh browser profile/watchlist, or preserve an existing watchlist before testing. Have a browser, local preview, downloads folder/native file picker, nonsensitive current-workflow example, timer, participant handout and blank records ready. Give participants access to original disclosures for verification without pointing to an answer. Record exact device/browser, prior exposure, round, release, rules and application commit.

Allow **35 minutes each**: consent 2, current workflow 8, tasks 20, debrief 5. First ask: “Show the last task where you checked mutual-fund stock holdings. What prompted it, what tools did you use, and what answer did you need?” Ask frequency, checking steps, baseline time and consequences of missing data before demonstrating FindIt. Then read the handout's tasks verbatim without a demonstration or hints. Round B names Franklin in T4 so the task's scope agrees with the existing fixed-cohort key.

Start/stop a timer for every task. Separately time reaching and correctly explaining a source-backed result. On a help request ask “What would you try next?” and record it. If blocked, record unassisted failure before minimal help. Every hint, intervention or explanation counts as assistance; retain failures and exact help. At debrief ask whether it helped the demonstrated workflow, what stopped them, and whether they would try the next actual release.

## Candidate answer key — agent-verified expectations, not observations

| Task | Expected result / scoring basis |
|---|---|
| T1 | HDFC LIC +41,618,000 shares, among 29 compared active domestic-equity funds. A fund house contains multiple individual funds. |
| T2 | Kotak 31/31 and HSBC 18/18 complete within reviewed eligible inventories; SBI partial 39/40 with a legitimate first snapshot. Seven complete selected houses; selected scope is not all-market completeness. Missing coverage is not proof of selling. |
| T3 | Match stock, house/filter, month, release and original rows. Explain previous/current/adjusted quantities and calculation. Estimated value is neither execution value, ownership percentage nor investment return. A missing whole portfolio is not zero holdings. |
| T4 | Franklin February–August fixed cohort 20, 16 August contributors: previous 22,084,715, current 20,633,257, change −1,451,458. Broader candidate monthly Reliance: −12,089,031 across 136 contributors. First history point is holdings only; unavailable cohort/month is a gap. |
| T5 | Actual saved valid JSON restores the watchlist after import; downloaded August report and reopened evidence match full candidate `65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4` and rules `readiness-2026-10-01`. Record actual reopening route and any help. |

“New in loaded portfolios” describes absence from loaded prior holdings, not an exchange listing date. The retained monthly Reliance total is −11,672,309 / 105; do not score that as the candidate total. Report Markdown includes complete URLs at the standard port 65100 and instructions for other preview origins. This round uses 65102. Do not pre-coach participants to change the origin; record whether the existing instructions and reopening path work for them. Preserve the release/rules/path/query if changing an origin becomes necessary, and count moderator help as assistance.

## Acceptance and findings

Record correctness, per-task seconds, exact assistance, errors/self-corrections, verbatim coverage/release explanations and evidence references in the blank local records. Report every task's numerator/denominator over all five people. Targets: **at least four of five complete core tasks without help; no participant interprets partial coverage as complete; a source-backed result is reached and correctly explained within 60 seconds**. Report each participant's measured source-backed time; do not average away a failure.

Current decision: **ready for independent review and moderated testing; promotion/private-pilot acceptance pending**. R01 and P01–P05 have no observations. Material numerical/scope findings must be resolved and independently rechecked; the usability targets must be assessed from real sessions. If a reproducible defect is found, preserve observations, fix/test in a focused commit, identify the affected independent check and fresh participant retest, and record any changed application/release as a separate round. Promotion still needs explicit owner authorization after the gates pass. September originals, payments, demand and monthly retention remain unproven.
