# Readiness audit — 1 October 2026

The existing architecture and eight-house roster are retained: five largest Indian houses plus three additional foreign-owned houses operating in India, using the displayed April–June 2026 AMFI selection period. Repairs use `real_data/readiness/working.db`. Both original databases and original disclosures are preserved. Byte checks confirm the original databases are unchanged.

Engineering and preparation are implemented. The mentor milestone and paid-pilot acceptance checks remain unfinished where official access or real participants are required.

## Official inventory and comparison coverage

The table describes **August versus July**, with active domestic-equity portfolios as the eligible comparison universe. “Loaded” includes active-classified portfolios without domestic equity; it is not the official denominator. Every compared fund must pass both monthly validation gates.

| House | July eligible inventory | July loaded | August eligible inventory | August loaded | August validated | August compared | August state |
|---|---:|---:|---:|---:|---:|---:|---|
| SBI | 39 | 39 | 40 | 40 | 40 | 39 | Partial: 39/40 |
| ICICI Prudential | 43 | 45 | 44 | 45 | 45 | 44 | Complete: 44/44 |
| HDFC | 29 | 30 | 29 | 30 | 30 | 29 | Complete: 29/29 |
| Nippon India | 23 | 26 | 23 | 26 | 26 | 23 | Complete: 23/23 |
| Kotak Mahindra | Unknown | 0 | Unknown | 0 | 0 | 0 | Unavailable |
| Mirae Asset | 15 | 15 | 15 | 15 | 15 | 15 | Complete: 15/15 |
| Franklin Templeton | 20 | 23 | 20 | 22 | 22 | 20 | Complete: 20/20 |
| HSBC | Unknown | 0 | Unknown | 0 | 0 | 0 | 0 | Unavailable |

The six reviewed houses have 171 expected August portfolios and 170 usable comparisons. This denominator excludes the two houses with unknown inventories and is not a market-wide total. SBI Balanced Hybrid (`SBHF`, scheme 125) appears in the August official inventory but not the July inventory; its current snapshot cannot establish a trade without a prior snapshot. The per-fund reason is `not_in_previous_official_inventory`. Missing data is not interpreted as a new purchase.

July snapshot inventories are reviewed for these six houses. **July-versus-June comparisons are a separate question:** five houses lack June snapshots; Franklin has 20 usable comparisons but no reviewed June official inventory, so whole-house completeness is unknown. The seven-month Franklin history declares its fixed cohort separately.

The wider audit includes all 57 houses in the existing registry, including absent houses. Additional loaded houses do not acquire an official denominator by being present. The ranking retains an unknown market-wide completeness label. Complete requires reviewed inventories for both months, exact identity mapping, every eligible comparison, both validation passes and no unexpected contributing funds. Partial, unavailable and unknown completeness remain distinct.

## HDFC's previous 2-of-29 result

The original database had July snapshots only for `MIDCAP` (scheme 232) and `HDFCT2` (233). Both matched August and passed validation. The other 27 eligible portfolios lacked July snapshots; identity merging or weaker validation would not repair that problem.

The official July archive supplied 109 portfolio workbooks and August supplied 110. All 29 eligible portfolios now have usable comparisons. Former Retirement Savings and Hybrid Debt titles needed consistent classification with the disclosed retirement/conservative-hybrid portfolios. The repair changes classification, not holdings or validation thresholds. A separate active portfolio without domestic equity explains the loaded count of 30 versus the eligible count of 29.

## Official acquisition and remaining access gaps

| House | Official evidence obtained / access result |
|---|---|
| [SBI](https://www.sbimf.com/portfolios) | July and August consolidated workbooks, 121 sheets each. |
| [ICICI Prudential](https://www.icicipruamc.com/media-center/downloads) | Official monthly ZIPs: 146 July and 147 August workbooks; archive provenance retained for extracted files. |
| [HDFC](https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio) | 109 July / 110 August workbooks; dated archive selection repaired. |
| [Nippon India](https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures) | Official consolidated workbooks, 107 sheets each. |
| [Mirae Asset](https://www.miraeassetmf.co.in/downloads/portfolio) | 97 July / 98 August workbooks. |
| [Franklin Templeton](https://www.franklintempletonindia.com/reports) | 40 July / 41 August sheets; seven original monthly workbooks retained for February–August. |
| [Kotak](https://www.kotakmf.com/Information/forms-and-downloads) | Archive presents CAPTCHA; actual monthly originals not obtained. No bypass attempted. |
| [HSBC](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library) | Official library repeatedly timed out; actual monthly originals not obtained. |

Access failure is not proof that a disclosure does not exist. All eight requested houses remain visible. The initial all-eight usable-coverage target is unfinished.

Original files, hashes, retrieval manifests, reviewed inventory JSONs and per-fund states are retained locally under ignored `real_data/readiness/`. The current matrix is `coverage-audit.json`. Actual publication dates remain unknown when not supplied; a deadline assumption is labeled separately. Raw and normalized holding rows, sheet names, one-based row numbers, parser versions and source URLs accompany available evidence. A ZIP URL points to the official archive; the named extracted workbook has its own displayed checksum.

## Calculation review and history

The agent reconciled six featured headlines plus 20 other changes: five purchases, five reductions, five additions and five exits, covering 87 contributing fund comparisons. Original file hashes, source rows, raw-to-normalized quantities and aggregate net changes were checked. Stored original NSE month-end bhavcopies supplied independent closing-price checks; the review's 1% inspection tolerance does not change production validation. Results are recorded in `real_data/readiness/source-review.json` with no unresolved sampled mismatches.

| Featured August move | ISIN | Net shares |
|---|---|---:|
| Franklin — HDFC Bank | INE040A01034 | −8,255,207 |
| HDFC — LIC | INE0J1Y01017 | +41,618,000 |
| ICICI — LIC | INE0J1Y01017 | +266,806,078 |
| Mirae — LIC | INE0J1Y01017 | +16,756,378 |
| Nippon — TD Power | INE419M01035 | +21,325,326 |
| SBI — LIC | INE0J1Y01017 | +142,857,197 |

This is agent review, not independent second-person verification. There are no corporate-action examples in these sampled actual comparisons; synthetic action tests do not count as a source review. Evidence panels retain “not independently reviewed.” Two-person reproduction and actual corporate-action review remain open acceptance items.

Franklin history has seven consecutive February–August snapshots and six comparisons over a fixed cohort of 20 validated, source-backed active domestic-equity portfolios. The chart and accessible table identify the cohort, unavailable ranges, adjustments and retained source revisions. No interpolation or investment-performance claim is made. Other houses do not have this seven-month history.

## Frozen demonstration and checks

Release: `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`.

Rules: `readiness-2026-10-01`. Database SHA-256: `dc70cbde430b8e4ce4a7c7d5cdd91b00fbeeefdc6047530c286bfd83b96f4b74`.

The reviewed working database, fresh staged recomputation and frozen release have the same content identifier. The immutable SQLite copy is 102,060,032 bytes. The final staged refresh took 20.055 seconds; these are machine measurements, not founder labor or cash costs. Costs and actual publication delays remain unknown. Failed stages and verified rollback are tested; prior releases are retained.

Verification: 418 Python tests pass; the subsequent privacy launch change passes four focused tests; Ruff passes. Browser checks cover desktop (1440 px), phone (390 px), evidence, seven-month history, follow/reload/import/export and deterministic report download without page overflow or observed console errors. Keyboard follow, Escape focus restoration, export and report download pass. This does not establish screen-reader conformance or participant comprehension. The evidence workbook recalculates in the artifact runtime; native Excel execution has not been observed.

Device watchlists, share changes, house counts, additions/exits, largest compared fund changes and evidence links are implemented. Reports preserve month, scope, coverage, rules and release ID. They do not describe unreviewed changes as independently verified. The server keeps stock choices out of request access logs; no participant analytics collector is added.

## Required human / external steps

Obtain official Kotak/HSBC originals and reviewed inventories, or explicitly decide a narrower disclosed pilot scope. Independently reproduce selected calculations. Recruit and consent 10–15 interviewees and five unassisted usability participants, then decide the priority job and audience. Resolve applicable source-use permissions, price hypotheses, merchant identity and authorization for external actions. Observe actual payments, renewals and two subsequent monthly publication cycles. No participants, payment results, retention or competitor task scores are invented.

Use the [mentor guide](mentor-demo.md), [mentor evidence packet](mentor-evidence-packet.md), [customer-validation kit](customer-validation-kit.md), [refresh runbook](refresh-runbook.md), [source-use review](source-use-review.md) and [VC evidence register](vc-evidence-packet.md).

## 2 October 2026 reliability follow-up

See [pre-pilot reliability results](pre-pilot-reliability.md) for exact-cohort evidence, retained-release reopening, performance measurements and regression results. The [acceptance pack](pre-pilot-acceptance.md) lists remaining source acquisition, independent review and real-participant steps. Earlier coverage and commercial-evidence limitations remain unless explicitly resolved there.

## Observed acceptance follow-up

See [2 October observed acceptance](observed-acceptance.md) for completed native Chrome file acceptance, the report-opening fix, official SBI launch resolution, and the assembled real-source reviewer bundle. Independent review and P01–P05 remain pending. Use the [blank session kit](moderated-session-kit.md).
