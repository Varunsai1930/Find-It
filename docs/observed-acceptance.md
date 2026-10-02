# Observed acceptance — 2 October 2026

**Ready for supervised moderated testing; private research pilot not yet accepted.** Real browser file acceptance is now observed by the agent. Independent source review and five real user sessions remain pending, explicitly confirmed by the owner. No payments, retention, outreach or public deployment occurred.

Baseline `3191cec`; report-opening fix `7a33dbe`. Release `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`; rules `readiness-2026-10-01`. Original retained DB SHA-256 `dc70cbde430b8e4ce4a7c7d5cdd91b00fbeeefdc6047530c286bfd83b96f4b74` verified unchanged. Working copy: `real_data/readiness/observed-acceptance/working.db`; no existing release was promoted, replaced or amended. Existing `.claude/` work was not touched.

## Browser acceptance record

Chrome **154.0.8037.95**, macOS, ordinary native browser, localhost port 65100, August 2026 active domestic equity. These are agent-operated observations, not participant results.

| Check | Actual saved artifact / observation | Result |
|---|---|---|
| Export | Chrome download history: `findit-watchlist (2).json`, 56 bytes, Done | Pass |
| Read saved JSON | Opened actual Downloads file in Chrome and file-picker preview: version 1; stocks `["INE002A01018"]` | Pass |
| Import | Unfollowed Reliance; empty watchlist observed; selected that exact downloaded file through native picker; Reliance and matching report restored | Pass |
| Monthly report | `findit-2026-08-28bb0fa89c48.md`, 2,041 bytes, Done; opened local file in Chrome | Pass with opening friction |
| Fixed report | `findit-2026-08-28bb0fa89c48 (1).md`, 2,412 bytes, Done; opened actual saved file; inspected numbered steps and complete URL | Pass |
| Reopen evidence | Entered complete URL visible in fixed saved report; page showed −11,672,309 shares, 105 contributing funds; expanded release/rules showed exact values above | Pass |

Saved files remain in the user's Downloads folder. macOS denied terminal reads there even with escalation; file content was verified through Chrome and native picker, not replaced by HTTP response checks. No checksum of those browser downloads is claimed. Tool transcript contains download-state observations and screenshots of both saved reports. Chrome was intermittently interrupted by foreground changes; these were automation friction, not application failures. Native input once dropped the first character of a file URL; using the address field directly resolved it.

Observed product friction: the original Markdown report required users to manually prefix a long relative evidence path. Fix: a complete deterministic standard-local-origin Markdown link plus numbered opening instructions, alternate-port guidance, and release comparison. Query identity is unchanged. Chrome displays Markdown as text, so copy the URL; a Markdown viewer can click it. Six existing report tests and Ruff passed, including retained A→B byte-identical report reopening and rejection of unavailable/tampered releases. No numerical rules changed. The prior 423 Python/12 JavaScript baseline remains engineering evidence; the full suite was not rerun for this text/link change.

## Coverage investigation

**SBI resolved as a legitimate new portfolio.** The [official SID](https://www.sbimf.com/docs/default-source/default-library/sid---sbi-balanced-hybrid-fund.pdf?sfvrsn=600fb554_0), page 1, states NFO 10–24 August 2026; page 3 gives scheme code SBIM/O/H/BHF/26/07/0207. The [official launch page](https://www.sbimf.com/balanced-hybrid-fund) corroborates this. The retained July inventory has Balanced Advantage (SBAF, scheme 8), while August additionally has Balanced Hybrid (SBHF, scheme 125). This supports a new fund, not a rename of Balanced Advantage or an acquisition gap. August is holdings-only for SBHF; keep comparison partial 39/40. Do not manufacture July zeros or label opening holdings as purchases. Downloaded SID and hashes are retained in the bundle.

**Kotak:** [Forms and Downloads](https://www.kotakmf.com/Information/forms-and-downloads) reached in Chrome and an isolated in-app browser. Portfolios → Consolidated & Fortnightly Portfolio exposes “Consolidated SEBI Portfolio as on July 31, 2026” and “…August 31, 2026”. Clicking the title and Download produced no completed file in this attempt. Prior acceptance recorded CAPTCHA; this attempt did not solve or bypass one. Inventory remains unknown and coverage unavailable.

**HSBC:** [official library](https://www.assetmanagement.hsbc.co.in/en/mutual-funds/investor-resources/information-library) again timed out through web fetch and browser navigation. Search indexing suggests portfolio documents exist, but indexed snippets are not downloaded originals or reviewed inventories. Coverage remains unavailable.

Owner action: at the exact archive URLs above, obtain the full **31 July and 31 August 2026 consolidated portfolios** (original XLS/XLSX/ZIP, all schemes, not fortnightly files or factsheets), plus the dated eligible-scheme list or full consolidated workbook index for each month. For Kotak use the exact two titles above and complete any CAPTCHA personally. For HSBC retry from your ordinary browser/network; if still blocked, request originals from official support yourself. No request has been sent. Save unchanged files in a new intake folder with source URLs and retrieval time. Agent then reconciles inventory, parses and validates the separate working DB and only publishes a new retained release after checks. Unknown inventories are not assumed complete.

## Reviewer bundle

Open [the local bundle](../real_data/readiness/observed-acceptance/reviewer-bundle/README.md). It contains original SBI July/August workbooks; four compact increase/reduction/addition/exit cases with exact sheet/row/cells, original-file hashes, absence checks and expected calculations; full prior 26-case agent review; retained database/manifest; SBI inventories and launch SID; rules identification; and blank `FINDINGS.md`. `checksums.json` inventories the assembled files. Originals and large artifacts remain local/ignored, not added to Git or publicly distributed.

An independent finance/accounting mentor or equity researcher who did not implement the product should perform and sign the review. Owner will identify and confirm the reviewer. Agent preparation and numerical reconciliation are not that sign-off.

A **real corporate-action example is now obtained**: Franklin India Pension Plan, Reliance, September 2024 FIPP row 12, October 2024 FIPP row 13; column D quantities 24,300 → 48,600. Original Franklin workbooks and [issuer bonus filing](https://www.ril.com/sites/default/files/2024-10/SE_16102024.pdf) are included. 1:1 bonus, record date 28 October: `48,600 − 2 × 24,300 = 0`. Production ratio/delta calculator on those real original rows returns zero adjusted shares and zero estimated flow. `corporate-action.json` records inputs/output. This isolated original-row calculation is outside the retained 2026 release; it is not a full 2024 ingestion release or independent review. Synthetic tests remain separate.

## Sessions and pilot decision

Use [the moderator/participant kit and blank results](moderated-session-kit.md). Owner will recruit and consent P01–P05, run sessions and provide anonymized observations. No participants exist yet; no results were filled. Collect current workflow first, then tasks without hints; record assistance, errors, time and coverage/release understanding. Fix observation-supported failures and retest affected tasks with new people where possible.

Audience hypothesis from the existing customer kit: independent equity researchers in India already comparing monthly mutual-fund holdings. Useful task: explain changes in followed stocks, verify one against originals, and save/reopen the report. Candidate pilot scope remains explicitly limited to available comparisons: five complete featured houses, SBI partial, Kotak/HSBC unavailable; Franklin 20-fund February–August history; unknown wider-market completeness. Any narrower advertised pilot scope requires the owner's explicit decision.

Next real release target: **September 2026 reporting month**, after actual original publication, acquisition, validation and anomaly review. It does not exist as an accepted release yet; record its real release ID/date when published. October and November reporting cycles can provide the next two return-use observations after they actually occur. Historical replay is not retention. Pricing and payment evidence remain blank; no willingness-to-pay or renewal claims are supported.

Proceed with local moderated testing to collect evidence. Do not claim private-pilot acceptance until independent review and observed usability gates pass, and the owner resolves the eight-house coverage target or explicitly accepts a narrower scope. No public deployment, participant contact, spending or payment actions are authorized by this preparation.

| Unresolved item | Owner / exact next action |
|---|---|
| Independent review | Owner identifies and confirms independent mentor/researcher; reviewer completes bundle FINDINGS.md and source calculations |
| Five user sessions | Owner recruits/consents P01–P05, uses kit, shares anonymized observations |
| Kotak/HSBC coverage | Owner obtains specified originals/inventories through archive access; agent validates separate intake and working DB |
| Observation-backed defects | Agent investigates supplied failures, fixes and commits; owner arranges fresh-participant retests |
| Pilot scope and next release | Owner resolves coverage scope; agent stages actual September sources when obtained, retaining August release |
| Commercial claims | Owner runs authorized real pilot; payments/retention remain unproven until observed |
