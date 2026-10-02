# FindIt August candidate — agent usability audit, 2 October 2026

**Agent audit — not human participant evidence.** The agent had prior knowledge of FindIt. This records actual browser actions and outcomes; it cannot measure first-time comprehension, participant completion time or the human acceptance gates. R01 and P01–P05 remain pending. Their records and Round B materials were not edited.

Frozen Round B: `B-candidate-65594777209e`, application `439a0f9`, documentation baseline `3c0c61d`, preview <http://127.0.0.1:65102/>. Candidate `65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4`, rules `readiness-2026-10-01`. Retained August and `current.json` are unchanged.

Revised **development-only testing baseline**: `agent-audit-dev-7534cfe`, application commit `7534cfe`, branch `codex/agent-usability-audit`, isolated checkout `/Users/varun/.codex/worktrees/agent-usability-audit/Find_v2`, preview <http://127.0.0.1:65103/>. It reads the same frozen candidate without altering its database, manifest or rules. These fixes are not merged into Round B. A human round using this application needs a separate round identifier, key and records; never combine it with Round B silently.

[Detailed local action/outcome record](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/audit.json), SHA-256 `665eb570d9f9f00a46d3c639360d9da3ffabe446e35a319f22798873db50aa72`, includes artifact hashes and before/after preservation checks. It uses no human timings or quotes. All results below have the same agent-only label.

## Actual browser tasks

Codex in-app browser was used for T1–T4 and the initial T5 attempt. Ordinary Google Chrome Incognito was used for the actual saved JSON, import file picker, downloaded Markdown report and official XLSX download. Browser-control failures were recorded separately from product defects.

| Task | Actual actions and observed outcome |
|---|---|
| T1 | **Agent audit — not human participant evidence.** Opened August summary and expanded HDFC. LIC +41,618,000 shares among 29 compared HDFC funds; the evidence has six LIC contributors. The reading guide distinguishes a fund house from an individual portfolio. |
| T2 | **Agent audit — not human participant evidence.** Expanded SBI and opened Coverage breakdown. Expected/loaded/validated 40, compared 39; Balanced Hybrid is named as absent from the prior official inventory. The interface explains that missing snapshots are not purchases/exits and selected-house completeness is not market completeness. The row itself does not supply launch proof. |
| T3 | **Agent audit — not human participant evidence.** Returned to HDFC evidence, expanded HDFC Large Cap August source, and followed its official XLSX link. The in-app download capture timed out; the source was subsequently downloaded in Chrome. Its hash matches the displayed original. Reading HDFCT2 row 48 in that downloaded copy confirms INE0J1Y01017, 3,890,000 shares, 16,260.2 lakh and 0.41% NAV. The total is 46,359,830 − 4,741,830 = +41,618,000. This bounded check does not constitute R01’s independent audit or a human 60-second result. |
| T4 | **Agent audit — not human participant evidence.** Searched Reliance and opened Holding history. The frozen interface had no house selector, so the required Franklin selection was blocked. Its all-house full-range cohort happens to consist of Franklin’s 20 funds; that coincidence does not establish explicit scope selection. August evidence shows 16 contributors and −1,451,458 shares. The development selector now reaches that explicit Franklin scope and preserves the release/rules/range. |
| T5 | **Agent audit — not human participant evidence.** Followed Reliance, reloaded, exported actual JSON, removed it and imported the saved JSON through Chrome’s native picker; Reliance was restored. Downloaded and reopened the actual Markdown file. Its 65100 evidence URL failed with a network error; changing only the port to 65102 reopened matching evidence. The development file now uses 65103 and reopens directly without an address correction. |

The in-app browser did not expose export/download events reliably. One native Save operation treated an absolute path entered as the filename as a colon-separated filename; the newly created audit export was moved to its intended folder before import. This is recorded as agent operation friction, not a FindIt defect. First failed attempts are retained in the detailed record.

[Saved JSON](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/baseline-watchlist.json), [frozen report](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/baseline-report.md), [development report](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/development-report.md), [downloaded official source copy](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/official-hdfc-large-cap-2026-08.xlsx). The source SHA-256 is `d9c3c4ae96af2bcc475bd8e582428ac5a04b483102ec286acd19c48c1b4aff56`; sheet HDFCT2, row 48. All are agent audit artifacts, not human findings.

## Verified fixes

- **Agent audit — not human participant evidence. A01, `1240ff9`:** added the visible history fund-house selector. Its GET form pins the existing release, rules, range and active flag. Browser selection of Franklin and its August evidence reproduces 20 cohort members, 16 contributors, previous 22,084,715, current 20,633,257 and change −1,451,458. Tests cover house isolation, retained-release selection after the default data changes, and missing-month gaps.
- **Agent audit — not human participant evidence. A02, `284ddd7`:** clarified the two house-direction definitions. Detail’s 6 buying/8 selling counts houses with any buying/selling fund; watchlist’s 2/8 nets share changes within each house. A house can be counted on both sides in detail. Watchlist, report and history now name net adding/reducing shares, and detail explains the distinction. Quantities and ranking rules did not change. An opposing-trades regression reproduces the difference.
- **Agent audit — not human participant evidence. A03, `7534cfe`:** downloaded web reports use the exporting preview’s address. The full release/rules/filter query stays pinned; non-web rendering retains its standard local default. The actual saved development report opens candidate evidence on 65103 with −12,089,031 shares and 136 contributors. Tests retain rejection of unknown releases and unsupported rules.

**Agent audit — not human participant evidence:** 14 focused Python report/history tests passed; the existing 12 JavaScript tests passed; lint on changed Python files and whitespace checks passed. The unrelated engineering and source anomaly audits were not repeated. Candidate/retained databases and manifests, current pointer and all six existing Round B files retain their before-audit SHA-256 hashes. The primary checkout remains at `3c0c61d`; existing `.claude/` work is untouched.

## Screenshots

Each screenshot is **Agent audit — not human participant evidence**:

- [Frozen HDFC expanded summary](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/t1-hdfc.jpg).
- [SBI coverage limitation](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/t2-sbi-coverage.jpg).
- [Original source row reference](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/t3-original-row.jpg).
- [Frozen history without house selection](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/t4-no-house-control.jpg).
- [Failed frozen-report origin](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/t5-wrong-report-origin.jpg).
- [Final development history and release identity](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/final-dev-history.jpg).
- [Reopened development report evidence](/Users/varun/Downloads/Varun/Find_v2/real_data/readiness/agent-usability-audit/2026-10-02/fix-a03-reopened-report.jpg).

## Limitations and next human action

**Agent audit — not human participant evidence:** seven selected houses are complete only within reviewed inventories; SBI is 39/40 and wider market completeness is unknown. The coverage row names missing prior inventory, but R01 still needs to check the existing official launch/source evidence. History is the common full-range cohort, not every monthly fund. Publication dates may be unknown; deadline assumptions and estimated values remain disclosed. A saved report still needs an active matching local server and a copied URL or Markdown viewer; it is not an offline evidence app. First-time comprehension remains unmeasured.

**Agent audit — not human participant evidence:** the revised development application is ready for a mentor demonstration and a separately identified moderated test round. This is no independent-review or participant-acceptance recommendation. R01 must resolve material numerical/scope findings; actual participants must provide the all-five-task, coverage-comprehension and 60-second source-backed results in the existing protocol. Report/selector changes need fresh participant testing; R01 should check filtered-history and report identity behavior. The frozen Round B UI still has the recorded defects. No promotion, outreach, public deployment or payments occurred. Commercial demand, payments and real monthly retention remain unproven. September intake was not examined in this audit.

Owner: use the existing recruitment drafts to arrange R01 and five consenting target users privately. Before using the changed application, record a separate round and update its moderator key for the visible selector, net-direction wording and 65103 report origin. Share only the participant handout. Return anonymized actual findings with exact assistance and preserved failed attempts. No human round has been created or started by this audit.

## Short mentor-demo walkthrough

Use the development preview on the moderator’s computer. This demonstration is agent-prepared, not participant evidence; do not give it to first-time participants before their tasks.

1. Expand HDFC: show LIC +41,618,000 shares, and explain house versus individual fund.
2. Open SBI coverage: show 39/40 and explain why a missing snapshot is not selling or complete market coverage.
3. Open HDFC LIC evidence and HDFC Large Cap’s original-source disclosure: match release, sheet HDFCT2 row 48 and estimated-value caveat.
4. Search Reliance, open history, select Franklin, update, then open August: distinguish the fixed 20-fund cohort’s −1,451,458 from the broader monthly −12,089,031. The first point is holdings only.
5. Follow Reliance, reload, export/import the saved JSON, download the report, open it and paste its complete Evidence URL. Compare full release and rules on the evidence page.
