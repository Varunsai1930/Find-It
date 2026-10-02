# Independent review and five-user acceptance pack

Prepared 2 October 2026. All participant results and independent sign-offs are **pending**. Companion: [engineering results](pre-pilot-reliability.md), [interview and competitor protocol](customer-validation-kit.md), [mentor packet](mentor-evidence-packet.md).

## Owner actions before the pilot

1. Open [Kotak Forms and Downloads](https://www.kotakmf.com/Information/forms-and-downloads) in an ordinary browser. Locate monthly portfolios for July and August 2026, complete the site's CAPTCHA personally if requested, and save original ZIP/XLSX files plus the eligible-scheme inventory. Record the original URLs and retrieval time. Do not substitute factsheet totals for portfolio rows.
2. Retry [HSBC's information library](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library) in an ordinary browser/network and obtain the same two months and scheme inventory. If it remains inaccessible, the owner may request the originals from official support; no message has been sent. Keep unavailable status until validated originals exist.
3. Put obtained originals in a new intake location, preserving their bytes. Import/repair only a separate working database, run existing validation and inventory reconciliation, then publish a new retained local release. Preserve the current release for report reproduction.
4. Assign a reviewer who did not implement this pass. Supply the retained DB/manifest, original disclosures, engineering results and calculation rules. Agree a review date; do not count agent checks as independent review.
5. Reuse the completed Chrome saved-file acceptance in [observed acceptance](observed-acceptance.md): export → inspect saved JSON → native-picker import → download/open report → matching-release evidence all passed after `7a33dbe`. Repeat affected tasks only after a new defect or relevant change; these agent observations do not replace participant testing.
6. Recruit five real target users with consent. Use participant IDs and keep contact details separately. No invitations have been sent. Payments, a public deployment and any narrowing of the advertised eight-house scope require the owner's separate decision.

## Independent reviewer sheet

Record reviewer, relationship to project, date, application commit, release ID, rules version and evidence artifact references. Use release `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`, rules `readiness-2026-10-01`, unless a new release is explicitly chosen.

- Reconcile one source-backed increase, decrease, addition and exit from original rows to displayed quantity changes. Record sheet, row, file hash, month, validation result and any missing source. Verify exclusions rather than treating missing rows as zero automatically.
- Reconcile Reliance February–August history: fixed cohort 20; August adjusted previous 22,084,715, current 20,633,257, change −1,451,458; 16 contributing funds. Contrast the broader August result −11,672,309 across 105 contributing holdings and explain the scope difference.
- Check the first snapshot has no implied prior-period activity, and unavailable months/cohorts are gaps. Change filters/period and confirm evidence follows the recomputed cohort.
- Reopen report A after switching the server to retained B. Record whether A totals, release, rules and source rows match; unavailable/corrupt A must fail explicitly.
- Verify expected/loaded/validated/compared counts independently against eligible inventories. SBI 39/40 is partial; missing Kotak/HSBC and unknown market completeness must remain visible.
- Review the acquired real corporate-action case in the [source bundle](../real_data/readiness/observed-acceptance/reviewer-bundle/README.md): Franklin India Pension Plan, Reliance, September 2024 FIPP row 12 and October FIPP row 13, quantities 24,300 → 48,600. The included [issuer filing](https://www.ril.com/sites/default/files/2024-10/SE_16102024.pdf) gives a 1:1 bonus and record date 28 October 2024. Independently verify applicability and `48,600 − 2 × 24,300 = 0`. The agent’s isolated production-calculator check is outside the retained 2026 release; independent review remains pending.

Sign-off: pending. Findings and unresolved exceptions: pending. Any failed numerical or scope check blocks claims of verified accuracy for that case.

## Participant prompt (read verbatim)

> We are testing FindIt, not you. Please think aloud and use the product without help. You may stop at any time. These are historical research data, not a recommendation. First tell us how you currently check changes in mutual-fund stock holdings. Then complete the tasks below and explain what you think each answer means.

Do not show the answer key before testing. Run the same release for all five users; record desktop/mobile and whether they have seen FindIt before. Start a timer for each task. Any hint counts as assistance.

1. Find HDFC's biggest displayed increase in August and explain which funds it represents.
2. Find a coverage limitation and explain whether a missing house means it sold everything.
3. Open evidence for one change and show how you would verify it against the original disclosure. Explain what the share and estimated-value figures mean.
4. Inspect Reliance from February through August. Open August evidence, explain the scope and first snapshot, and explain why its number can differ from the monthly summary.
5. Follow Reliance, reload, export and re-import the watchlist, download its monthly report and reopen the report's evidence locally. Identify the release and confirm it matches.

Moderator key: HDFC LIC +41,618,000 shares among 29 compared active domestic-equity funds; SBI partial 39/40; unavailable is not zero selling. History answer is −1,451,458, fixed cohort 20, 16 contributors; broader monthly evidence is −11,672,309. First point is holdings only. Estimated trading value is neither execution value, ownership percentage nor investment return. Recheck this key if the release changes.

| Participant | Consent / device / date | Per-task seconds and assistance | Correct explanations / errors | Evidence reference |
|---|---|---|---|---|
| P01 | Pending | Pending | Pending | Pending |
| P02 | Pending | Pending | Pending | Pending |
| P03 | Pending | Pending | Pending | Pending |
| P04 | Pending | Pending | Pending | Pending |
| P05 | Pending | Pending | Pending | Pending |

Acceptance targets from the approved plan: at least four of five complete core tasks without help; no participant interprets partial coverage as the complete universe; a source-backed result can be reached and explained within one minute. Report each task's numerator/denominator, time and assistance separately. Retain failures and contradictory feedback. Correct problems and retest with new participants; never fill this table from automated checks.

## Actual pilot checklist

- Complete or explicitly resolve the source-review and eight-house scope gates before offering a paid pilot.
- Run 10–15 real workflow interviews using the existing kit; select the priority job from demonstrated behavior.
- Compare the same tasks and scope with each participant's current workflow and accessible competitor tools; counterbalance order. Mark inaccessible paid tasks untested rather than buying access.
- Agree the pilot scope, price and duration with real participants. Count payment only from an actual receipt; no charges have been initiated.
- Deliver the next real monthly release, measure repeat usage, report reopening, time to correct answers, support minutes, direct operating cost and actual renewals. Record denominators and dates. Retention remains unknown until enough time and releases have elapsed.

## Observed acceptance follow-up

See [2 October observed acceptance](observed-acceptance.md) for completed native Chrome file acceptance, the report-opening fix, official SBI launch resolution, and the assembled real-source reviewer bundle. Independent review and P01–P05 remain pending. Use the [blank session kit](moderated-session-kit.md).
