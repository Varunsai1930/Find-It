# Observed acceptance and September intake — 2 October 2026

**Ready for local moderated testing on the retained August release. Private-pilot acceptance and a September release remain pending.** The owner reconfirmed that the independent reviewer and P01–P05 have produced no findings. No observations, sign-offs, payments or retention have been invented.

Branch: `codex/mentor-ready`. Preserve the [completed acceptance record](observed-acceptance.md), [source-review bundle](../real_data/readiness/observed-acceptance/reviewer-bundle/README.md), [blank session kit](moderated-session-kit.md) and their answer key. Browser acceptance is already complete; it was not repeated for intake-only changes.

The owner has selected the expanded August candidate for new round `B-candidate-65594777209e`. Its separate local preview is <http://127.0.0.1:65102/>; use the [candidate moderator/reviewer handoff](august-candidate-session/moderator-key.md) and separate [participant handout](august-candidate-session/participant-instructions.md). R01 and P01–P05 are not recruited. Retained August remains the default; no human acceptance or promotion has occurred.

## Frozen expanded August candidate — review completed 2 October

Candidate **`65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4`** is ready for independent source review. Retained August remains the default; promotion and pilot acceptance remain pending. Use app `439a0f9`, parser `portfolio-2026-10-02-scope-1`, rules `readiness-2026-10-01`.

- [Candidate record and artifact hashes](../real_data/readiness/september-2026/august-review/candidate-record.json), [frozen database](../real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.db), [manifest](../real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.json). The manifest records the retained parent and engineering-review hash. `--candidate` creates files without changing `current.json`, including when the candidate already exists.
- [Engineering review](../real_data/readiness/september-2026/august-review/engineering-review.json), [original-row checks](../real_data/readiness/september-2026/august-review/original-row-verification.json), [before/after results](../real_data/readiness/september-2026/august-review/before-after.json), [application checks](../real_data/readiness/september-2026/august-review/application-checks.json). These are agent checks; both human review and P01–P05 remain pending.

The old `historical-final-stage.db` / `02ac5d45…` is superseded for review. Its files and earlier findings below are preserved as history. The corrected working database is `august-review/reviewed-stage-v2.db`; use the frozen candidate for review and any explicitly selected candidate session round.

### Source counts, scope and numerical comparison

All **92 originals** match their retained hashes and July/August reporting dates: 90 HSBC single-portfolio workbooks and two Kotak consolidated workbooks. They contain **325 portfolio-months**, **163 distinct official portfolio identities** across the two months, and **49 eligible adjacent-month comparisons** (Kotak 31, HSBC 18). File counts do not establish eligible coverage. Official sheet keys, publisher indexes and dated inventories support identity; KIP’s August publisher index explicitly refers to its former Debt Hybrid name.

| House | Retained compared / expected | Candidate compared / expected | Candidate state |
|---|---:|---:|---|
| Franklin | 20/20 | 20/20 | Complete |
| HDFC | 29/29 | 29/29 | Complete |
| ICICI | 44/44 | 44/44 | Complete |
| Mirae | 15/15 | 15/15 | Complete |
| Nippon | 23/23 | 23/23 | Complete |
| SBI | 39/40 | 39/40 | Partial: legitimate first snapshot |
| Kotak | 0/unknown | 31/31 | Complete within reviewed inventory |
| HSBC | 0/unknown | 18/18 | Complete within reviewed inventory |

Selected-scope usable comparisons rise **170 → 219** of 220 expected; broader ranking scope remains separate. All **12,148 previously covered comparison rows** retain quantities, market values, adjustments and flows (maximum flow rounding difference below 0.000000001 lakh). Existing source tables and classifications are unchanged; latest publisher display names/capitalization can change. New houses add 2,941 comparison rows and 45 securities; consensus output grows 873 → 918 securities, including unchanged ones. The browser excludes unchanged stocks from its activity list.

Existing-house featured numerical moves are unchanged. New HSBC headline: TD Power **+10,708,765 shares**, estimated **₹83,207.10 lakh**. New Kotak headline: Eternal **−40,664,235 shares**, estimated **₹−133,061.84 lakh**. Original contributors reconcile in [featured cases](../real_data/readiness/september-2026/august-review/featured-original-cases.json). Buying top five change from LIC, Dhoot, Apollo, Juniper, Mahindra & Mahindra to LIC, Dhoot, Juniper, Manipal, Shiprocket as house breadth widens. Broader Reliance changes **−11,672,309 / 105 contributors → −12,089,031 / 136**: HSBC contributes −210,324 and Kotak −206,398. Franklin’s fixed 20-fund history remains **−1,451,458**, 16 August contributors.

### Anomaly disposition and verified fixes

`17e7974` excludes explicit Kotak Futures/Options/Derivatives sections and appended exposure tables after the original Grand Total. Previously positive futures sharing a cash equity ISIN inflated holdings. **871 exposure rows** are now excluded, **15,919 accepted original rows** checked, **19 comparison rows corrected**, and **two derivative-only comparisons removed**. Original files remain unchanged. [Correction ledger](../real_data/readiness/september-2026/august-review/superseded-stage-corrections.json) records exact source rows and before/after quantities. Synthetic regressions preserve legitimate multiple cash lots.

All **2,941 new-house comparison calculations** reconcile to original quantities/values; 24 largest increase/reduction/addition/exit cases have exact rows, hashes and calculations in [selected cases](../real_data/readiness/september-2026/august-review/selected-original-cases.json). There are no duplicate corrected input holdings, missing selected domestic-equity quantities/values, or unpriced selected new-house moves. **69 quantity-ratio warnings**, **14 NAV-sum warnings** and **four domestic-equity price warnings with new active holders** were traced to originals. No unsupported split or identity adjustment was applied. Snapshot consistency does not establish transaction motive or execution price.

Validation remains explicit: 44 NAV-sum, 207 quantity-ratio, 10 missing-NAV and two passive corporate-action-candidate warnings across all new portfolio-months. The global 1,521 price warnings retain pre-existing/out-of-scope cases. Six annotated matured-FMP values remain unknown outside selected scope; 16 tiny annotated active-equity NAV cells remain unknown. No weights were rescaled to force 100%. Independent reviewer must accept or challenge these dispositions; this is not blanket independent valuation clearance.

`ea26afe` adds the tested candidate-only freeze path. `439a0f9` fixes the observed misleading “new listing” badge to “new in loaded portfolios”; absence from prior loaded holdings does not prove an exchange listing date. Numerical rules/ranking are unchanged. **431 Python tests, 12 JavaScript tests, Ruff and diff checks pass.** Real candidate application checks verify report/evidence identity, exact-cohort history, original provenance, byte-identical retained report reopening, and rejection of missing releases/wrong rules.

Chrome native saved-file checks also pass: exported JSON was inspected and hashed, the actual saved file restored the isolated watchlist, the downloaded Markdown report matches verified output byte-for-byte, and its complete URL reopened candidate evidence showing −12,089,031 / 136. [Browser record and saved-file hashes](../real_data/readiness/september-2026/august-review/browser-checks.json). Native automation focus/clipboard retries were recorded separately from product friction; Markdown still requires copying its complete URL. These are agent browser checks, not P01–P05 observations.

### Minimal independent handoff

Use the unchanged original reviewer bundle plus the corrected supplement above; record findings in the existing blank `FINDINGS.md` and `HISTORICAL-FINDINGS.md`. The independent mentor/researcher needs to check:

1. Inventory eligibility and official identity continuity, especially KIP; SBI’s first snapshot remains holdings-only.
2. Cash-versus-derivative exclusions and the 19 corrected comparisons/two removed entries.
3. Selected increases, reductions, additions, exits and both new-house headline sums against original rows; reproduce quantities and estimated-value units.
4. Warning dispositions/unknown cells and the original bundle’s real Reliance corporate-action example.
5. Release/scope reconciliation: candidate versus retained reports and fixed-cohort versus monthly Reliance totals.

Source/calculation fixes require independent recheck. Changed coverage explanations and the portfolio badge need comprehension testing with fresh participants where possible. Use one frozen release and exact app commit per round; see the [round-specific session key](moderated-session-kit.md). No rounds have begun.

### September external dependency

One focused official-source check was completed on **2 October 2026**: [responses, hashes and retrieval basis](../real_data/readiness/september-2026/august-review/september-check/availability.json). No verified September monthly original was obtained. Dynamic/inaccessible listings are indeterminate, not proof of non-publication. No repeated checks during this review; September expected inventories remain unknown and no September release exists. Continue only when genuine month-end originals and dated inventories are available.

## Earlier intake record (superseded stage results retained for provenance)

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
