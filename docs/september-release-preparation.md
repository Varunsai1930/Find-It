# Observed acceptance and September intake — 2 October 2026

**Ready for local moderated testing on the retained August release. Private-pilot acceptance and a September release remain pending.** The owner reconfirmed that the independent reviewer and P01–P05 have produced no findings. No observations, sign-offs, payments or retention have been invented.

Branch: `codex/mentor-ready`. Preserve the [completed acceptance record](observed-acceptance.md), [source-review bundle](../real_data/readiness/observed-acceptance/reviewer-bundle/README.md), [blank session kit](moderated-session-kit.md) and their answer key. Browser acceptance is already complete; it was not repeated for intake-only changes.

## Three separate evidence states

| State | Identity and status |
|---|---|
| Accepted engineering/browser baseline | Retained August release `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`, rules `readiness-2026-10-01`; independent review and participants pending |
| New July/August historical intake | Separate `historical-final-stage.db`, current stage content ID `02ac5d456471a3086a77e2eec54da257415919eba31acb1e4be3fbdd0d566241`; app `98159d8`, parser `portfolio-2026-10-02`; source/anomaly review pending; **not published** |
| September preparation | Separate `september-standby.db`; zero verified September originals, inventories, holdings or deltas; **not an accepted or published monthly release** |

Local artifacts are under [september-2026](../real_data/readiness/september-2026/README.md). Earlier historical stages and processing records are retained for provenance. Their intermediate content IDs do not identify the current inventory-reconciled stage. `historical-stage-review.json` is authoritative for that stage; `september-status.json` records the blocked September status. A successful zero-input preflight is not evidence that September data passed validation.

The original `tracker.db`, mentor database, retained release and reviewer bundle were checksum-verified unchanged. [Integrity record](../real_data/readiness/september-2026/integrity.json): 92 new official originals, 17 original bundle files, and the existing 18-member reviewer ZIP verified. The retained release DB SHA-256 remains `dc70cbde430b8e4ce4a7c7d5cdd91b00fbeeefdc6047530c286bfd83b96f4b74`. No existing release was amended or promoted.

## September official-source matrix

The following are bounded observations from official listings on **2 October 2026**, not proof that a disclosure cannot appear elsewhere or later. **No 30 September 2026 month-end portfolio was acquired or verified for any selected house.** Actual publication dates remain unknown. HTTP retrieval timestamps and response hashes are retained in [source-matrix.json](../real_data/readiness/september-2026/source-matrix.json); document reporting dates, HTTP dates and search crawl dates are not publication dates.

| Selected house / official archive | Observation and required next source | August candidate eligible count |
|---|---|---:|
| [SBI](https://www.sbimf.com/portfolios) | Monthly API latest 31 August. Obtain all-schemes 30 September original and complete dated scheme index. | 40 |
| [ICICI Prudential](https://www.icicipruamc.com/media-center/downloads) | Browser: Other Scheme Disclosures → Monthly Portfolio Disclosures → APPLY; latest August in 20 displayed results. HTTP listing/archive probes returned 404. Obtain September monthly archive and every member workbook/index. | 44 |
| [HDFC](https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio) | Accessible browser 2026 listing showed August files; HTTP returned 403. Obtain every September monthly scheme workbook and dated full listing. Recheck the current year-based browser selector when sources appear. | 29 |
| [Nippon India](https://mf.nipponindiaim.com/investor-service/downloads/factsheet-portfolio-and-other-disclosures) | Latest monthly file 31 August; 15 September is fortnightly. Obtain September **monthly** consolidated original/index. | 23 |
| [Kotak](https://www.kotakmf.com/Information/forms-and-downloads) | Portfolios → Consolidated & Fortnightly Portfolio shows July/August monthly entries and 15 September fortnightly. Use the actual Download control within the monthly item. Obtain September consolidated SEBI original and complete portfolio-sheet index. | 31 |
| [Mirae Asset](https://www.miraeassetmf.co.in/downloads/portfolio) | First monthly page showed ten newest titles dated 31 August. Obtain complete September monthly originals and dated inventory, checking pagination. | 15 |
| [Franklin Templeton](https://www.franklintempletonindia.com/reports) | Reports → Monthly Portfolio Disclosure: latest ISIN-wise original dated 31 August. Obtain September ISIN-wise consolidated original and full sheet index. | 20 |
| [HSBC](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library) | Accessible official library has July/August monthly folders; no September monthly folder observed. September weekly factsheet is excluded. Obtain all September monthly XLSX originals and dated full link inventory. | 18 |

**September expected counts are unknown for all eight houses.** [expected-inventory.json](../real_data/readiness/september-2026/expected-inventory.json) lists 220 August eligible funds as a checklist to reconcile against actual September disclosures, with official keys, names and prior inventory provenance. It is not an exhaustive September inventory and has not been imported as one. New funds, closures, mergers, renames and mandate changes require dated source evidence. September house states remain unavailable with unknown denominators; market completeness remains unknown.

Empty September intake folders are prepared for the eight selected houses. No access blocker currently requires the owner's participation for the successful historical acquisition. If a later monthly download requests CAPTCHA, the owner should use the archive URL above, select the exact 30 September monthly item, complete it personally and save unchanged originals plus the source URL/retrieval time. Do not replace month-end originals with weekly factsheets or fortnightly debt disclosures.

## Kotak and HSBC historical acquisition completed

Official originals and hashes are retained in [historical-source-manifest.json](../real_data/readiness/september-2026/historical-source-manifest.json): **45 HSBC XLSX files per month**, and **one Kotak consolidated XLSX per month**. The HSBC dated link inventory reconciles one-to-one with its 45 originals. Kotak has 117 July and 118 August portfolio sheets; supplementary Common Notes and Scheme notes remain in the originals.

Kotak direct originals: [July](https://vatseelabs-s3.kotakmf.com/FormsDownloads/Portfolios/Consolidated-SEBI-Portfolio-as-on-July-31,-2026/ConsolidatedSEBIPortfolioJuly2026.xlsx), [August](https://vatseelabs-s3.kotakmf.com/FormsDownloads/Portfolios/Consolidated-SEBI-Portfolio-as-on-August-31,-2026/ConsolidatedSEBIPortfolioAugust2026.xlsx). HSBC links, retrieval times and individual hashes are in the manifest. Publication times are unknown.

Real-source defects fixed and committed:

- `3f9e022`: recognize HSBC's Percentage to Net Assets header, extract multiline scheme titles, support Kotak merged instrument headers and recognize its specific supplementary notes sheet.
- `1900aa6`: preserve actual Kotak instrument names when a merged-header spacer contains a literal space.
- `98159d8`: keep KIP eligible under both July “Kotak Debt Hybrid Fund” and August “Kotak Conservative Hybrid Fund.” The same official sheet key and original domestic equity holdings support continuity. No prior portfolio was invented.

Full Python suite: **427 passed** after the final change; Ruff and diff checks passed. Synthetic regressions are engineering evidence. Separately, an agent checked **16,790 actual holding rows** against original quantity, market-value, ISIN, instrument-name and NAV-weight cells, including HSBC's fraction-to-percent conversion. Six annotated market-value cells in matured, out-of-scope FMPs remain unknown rather than guessed.

Existing validation processed **325 new scheme-months**, zero quarantined. Warnings remain explicit: 42 NAV-sum warnings, 206 quantity-ratio warnings, 10 missing-NAV warnings and two passive-fund corporate-action-candidate warnings. Within the new eligible domestic active scope, there are 12 NAV-sum and 68 quantity-ratio warnings. The global 1,521 implied-price warnings include existing houses and out-of-scope securities. Passing a gate does not clear these findings. Review [historical-anomalies.json](../real_data/readiness/september-2026/historical-anomalies.json) and its [scope summary](../real_data/readiness/september-2026/historical-anomaly-scope.json) before promotion; do not suppress thresholds to improve coverage.

The current stage's August audit has seven inventory-reconciled complete houses and SBI partial **39/40**, totaling 219 compared of 220 expected. This is an agent stage audit, not accepted eight-house coverage. The retained acceptance release still has Kotak/HSBC unavailable. SBI Balanced Hybrid is a legitimate August first snapshot, supported by its launch SID; it is holdings-only, not a missing trade. September can first compare it with August when actual September holdings exist. All-eight acceptance remains open until review supports the declared scope.

## Human review and observation gates

Reuse the original compact source bundle for increases, reductions, additions, exits and the real Reliance bonus example. Use [the historical supplement](../real_data/readiness/september-2026/README.md) for new-house inventories, original rows, title/weight checks, validation warnings and blank `HISTORICAL-FINDINGS.md`. Agent raw-row checks and synthetic tests do not complete either findings sheet.

The finance/accounting mentor or equity researcher must be independent of implementation. P01–P05 remain blank. The owner recruits and obtains consent, captures current workflows first, then runs tasks without hints and shares anonymized time, assistance, errors and coverage/release explanations. Keep contact details outside the project. Existing [session materials](moderated-session-kit.md) continue to use the retained release and its original expected answers. If a reviewed successor is selected later, regenerate the answer key and record the exact app/release/rules before sessions; fresh participant testing is needed for changed coverage explanations.

Triage actual findings in order: incorrect calculation/scope/release identity; failed tasks or misleading explanations; secondary usability. Preserve the observation, commit focused fixes, rerun affected engineering checks, and obtain an independent recheck for numerical/scope changes or fresh participant retest for task/comprehension changes. No human findings have arrived yet.

## Next release and decision

When actual September sources appear, retain and hash originals, establish complete dated inventories, then use the existing intake/validation/refresh workflow in a **new** working database. Compare August → September. Use retained August as the reproducible baseline, or an explicitly reviewed historical successor; do not silently substitute the unreviewed Kotak/HSBC stage. Record parser/app/rules versions, parent release, unresolved source/anomaly findings and real publication/retrieval dates. Promote only after the declared scope and review gates pass, keeping August reports and evidence reopenable. No September promotion has occurred.

The audience and useful job remain hypotheses from the [customer-validation kit](customer-validation-kit.md): independent Indian equity researchers explaining followed-stock changes, checking an original and saving/reopening a report. Local moderated testing can begin now. A private research pilot requires independent source review plus observed task and coverage/release comprehension, with an explicit scope decision. Payments, commercial demand and retention remain unproven. Historical replay and a prepared empty September stage do not count as monthly return use.

| Remaining dependency | Owner / action |
|---|---|
| Independent review | Owner confirms independent mentor/researcher; reviewer completes original FINDINGS.md and supplemental historical findings, including anomalies and inventory scope. |
| P01–P05 | Owner recruits/consents, runs the existing protocol and shares anonymized observations; agent triages actual findings. |
| September originals | Official publishers supply actual month-end disclosures; agent rechecks, acquires originals and reconciles dated inventories on the next work pass. |
| Release/pilot acceptance | Owner selects the declared scope after review; agent stages/validates actual September data and implements findings; review and comprehension gates precede promotion/pilot recommendation. |
