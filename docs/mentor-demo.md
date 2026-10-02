# FindIt five-minute local mentor demonstration

Baseline **`C-demo-65594777209e-v1`** uses the reviewed application fixes and the unchanged frozen August candidate. It answers: “What changed in the stocks I follow, and can I verify the result?” This is a separately identified demonstration/testing baseline. **Agent audit — not human participant evidence.** R01 and P01–P05 remain pending; no human acceptance or data-release promotion has occurred.

## Identity and restart

| Item | Frozen identity |
|---|---|
| Primary branch | `codex/mentor-ready`; reviewed commits `1240ff9`, `284ddd7`, `7534cfe`, `0ecaf27` integrated by fast-forward |
| Application source | `7534cfed7896e04494ab343b409c7d0a97145fa8`; integrated head `0ecaf273f74734151407c26858f3b822d99d92fe` has documentation only after that application commit |
| Demo Git tag | `mentor-demo-august-candidate-v1` pins the application and this guide |
| Data release | `65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4` |
| Rules | `readiness-2026-10-01` |
| Scope | August 2026; active stock-pickers; domestic equity |
| Local verification | [Integration record](../real_data/readiness/mentor-demo/C-demo-65594777209e-v1/integration-checks.json), separate from human records |

Run this command from any directory after a restart. It uses the existing CLI, an explicit immutable database and a local-only port. Keep the terminal open; Ctrl+C stops this server. If already running on 65103, use its URL instead of starting another copy. Keep this checkout at the demo tag for this baseline.

```sh
env PYTHONPATH=/Users/varun/Downloads/Varun/Find_v2 \
  /Users/varun/Downloads/Varun/Find_v2/.venv/bin/python -P -m findit.cli web \
  --db /Users/varun/Downloads/Varun/Find_v2/real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.db \
  --host 127.0.0.1 --port 65103
```

Open [August demo](http://127.0.0.1:65103/?month=2026-08). Explicit `PYTHONPATH` and Python `-P` select this application regardless of the shell's directory. The sibling [candidate manifest](../real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.json) records its database hash, parent and rules. Original sources, databases, retained release, `current.json` and Round B materials/records are preserved. Local data and verification artifacts are excluded from Git; a fresh clone alone does not contain this demo's data.

## Five-minute walkthrough — owner only

These are moderator expectations, not participant observations or timed human results. Allow one minute per step for this demonstration; actual moderated sessions still allow about 35 minutes.

| Step | Action and explanation |
|---|---|
| T1 · Monthly change | Select August, equity and active scope. Open HDFC's LIC **More info**: previous 4,741,830, current 46,359,830, increase **+41,618,000 shares**, across 29 compared eligible funds. A fund house aggregates individual funds; 30 loaded/validated portfolios is a different denominator. |
| T2 · Coverage | Open SBI **More info** for **39/40**, then **Coverage breakdown**. Seven selected houses are complete within reviewed inventories, including Kotak 31/31 and HSBC 18/18. SBI Balanced Hybrid has a legitimate first snapshot, not invented July holdings. Selected scope 219/220 is not all-market completeness; the wider denominator is unknown. Missing coverage does not establish selling. |
| T3 · Original evidence | From HDFC's LIC **More info**, open **View evidence**, then an August source disclosure and **Data release and calculation rules**. One contributing original is HDFC Large Cap's August workbook, sheet **HDFCT2**, row **48**, raw quantity **3,890,000**; that single row is not the whole-house total. Show original URL/hash, prior/current quantities and full candidate/rules identity. Estimated value is not execution value, company ownership or investment return. |
| T4 · History scope | Search Reliance, open **Holding history**, select **Franklin Templeton AMC** in the visible **Fund house** selector and choose **Update history**. February–August uses the same **20-fund cohort**. Open August: **16 contributors**, previous 22,084,715, current 20,633,257, change **−1,451,458**. First snapshot is holdings only; missing data is a gap. The broader monthly candidate result is **−12,089,031 / 136 contributors**, so it answers a different scope. |
| T5 · Saved files and release | Follow Reliance, reload and check persistence. Export the actual watchlist JSON; preserve it, remove the demo entry if needed, then import that saved file and confirm restoration. Download August's Markdown report, open the saved file in a text editor/Markdown viewer, and click its evidence link or copy the complete URL into the browser. Confirm **−12,089,031**, the full candidate ID and rules. The saved link uses **127.0.0.1:65103**, the exporting server's origin. |

“Fund houses net adding/reducing shares” reports each house's net direction; “houses with any fund adding/reducing shares” counts individual-fund directions and can overlap. For Reliance, net-house counts are **2/8**. “New in loaded portfolios” means absent from loaded prior holdings, not newly exchange-listed. History and evidence retain their release and filters.

Verified local examples: [saved JSON](../real_data/readiness/mentor-demo/C-demo-65594777209e-v1/demo-watchlist.json), [saved report](../real_data/readiness/mentor-demo/C-demo-65594777209e-v1/demo-report.md), [reopened evidence screenshot](../real_data/readiness/mentor-demo/C-demo-65594777209e-v1/saved-report-evidence.png). **Agent audit — not human participant evidence.** JSON export/removal/import and report saving were performed in ordinary Chrome. The exact saved report URL reopened successfully in the in-app browser after the Mac locked. This does not measure first-time comprehension or human task time.

Restart this server on 65103 before reopening its saved report. Older reports retain their original origin; if that origin is unavailable, follow their opening instructions and preserve path, stock, scope, month, release and rules when using an available local preview. Do not silently substitute current data. Official disclosure links need network access and can fail independently of locally captured evidence.

## Reuse the existing review/session materials

Use the existing [moderator key, recruitment drafts and source-review tasks](august-candidate-session/moderator-key.md), [session protocol](moderated-session-kit.md), reviewer bundle, new-house supplement and blank record templates. This guide is the **C-demo application/answer-key amendment**: numerical T1–T5 expectations and scoring gates are unchanged. T4 now uses the visible house selector, labels distinguish net-house/individual-fund directions, and T5 reports open at the exporting origin 65103 without substitution during this demo.

Share only the [C-demo participant handout](august-candidate-session/demo-participant-instructions.md), not this guide or the answer key. When people are arranged, record this baseline, application, release, rules and browser in separate C-demo records using the existing blank templates. Do not populate or combine Round B observations. R01's source review remains independent of participant task results.

Core tasks are **T1–T5**. At least **four of five participants must each complete all five correctly without assistance**, with per-task results also reported. **All five** must correctly explain a source-backed T3 answer within **60 seconds**, starting after T3 is read at their T2 finishing state; navigation and disclosure-opening time count. No participant may interpret partial coverage as complete. Preserve every hint, failure and retry. This usability measure does not complete R01's numerical audit. See the existing moderator key for recording details.

## Preserve and reproduce original Round B

Round B `B-candidate-65594777209e`, its material hashes and pending records are unchanged. Its application commit **`439a0f96e424e973b474f45a533c5d089d60dd67`** is retained in a detached managed worktree at `/Users/varun/.codex/worktrees/round-b-frozen/Find_v2`. Since the primary checkout now has revised code, use this command instead of the historical key's primary-checkout launcher when reproducing Round B:

```sh
env PYTHONPATH=/Users/varun/.codex/worktrees/round-b-frozen/Find_v2 \
  /Users/varun/Downloads/Varun/Find_v2/.venv/bin/python -P -m findit.cli web \
  --db /Users/varun/Downloads/Varun/Find_v2/real_data/readiness/releases/65594777209eccae96a60f18294f0bb98a21abcd95559713142ecce5e66e34d4.db \
  --host 127.0.0.1 --port 65102
```

Original Round B remains [local on 65102](http://127.0.0.1:65102/). Keep its unchanged answer key/handout for that original round. Retained August `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec` and the current pointer remain unchanged; application integration does not promote the candidate.

## Verification and limits

**Agent audit — not human participant evidence.** Integrated `findit/` and `tests/` exactly match the reviewed branch. The owner-reported independent agent result of **435 Python tests, 12 JavaScript tests and changed-file lint passing** is reused; this documentation change does not alter application code. Startup from stopped ports, the five-step flow, saved files, matching-release reopening and protected-file hashes were checked locally in the integration record.

This baseline is ready for a local mentor demonstration and separately recorded moderated testing. R01 independent review, P01–P05 recruitment/consent/sessions, comprehension gates and human usability validation remain pending. SBI is partial; all-market completeness and provenance for some broader-house inputs remain unknown/unavailable. September remains pending genuine official disclosures. Demand, payments and monthly retention are unproven; historical replay is not monthly retention. Promotion requires real passing review/session evidence and explicit owner authorization.
