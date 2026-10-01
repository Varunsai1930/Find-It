# FindIt three-minute mentor demonstration

FindIt answers: “What changed in the stocks I follow, and can I verify the result?” The local release supports six of the eight requested houses, explicit coverage, original-row evidence, scoped history and reproducible reports. The full mentor acceptance milestone remains unfinished; see the [readiness audit](readiness-audit.md).

## Reproduce this workspace

From the repository root:

```bash
.venv/bin/python -m findit.cli web \
  --db real_data/readiness/releases/28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec.db \
  --port 65100
```

Open [August 2026](http://127.0.0.1:65100/?month=2026-08). This workspace already has that local server running. Do not launch another copy on the occupied port; choose a free port if restarting alongside it. The default listens only on this computer. Keep the terminal running; Ctrl+C stops your launched server.

Release ID: `28bb0fa89c482a98b86fa6d3c9f7f9b372bb63d2f8f1aee70a82593eacc756ec`.
Rules: `readiness-2026-10-01`. Manifest and checksum are in `real_data/readiness/release-manifest.json`; the immutable database is under `real_data/readiness/releases/`. Retain the corresponding code and rules when reproducing old reports. Future calculation changes require a rules-version change.

Original `tracker.db`, the prior mentor database and original disclosures are preserved. Repairs, logs, manifests, source-review records and acquired history are under ignored `real_data/readiness/`. These databases are intentionally absent from Git. A fresh clone can use the README synthetic quick start or its own official disclosures. The displayed data and reports need no network once captured; opening official source URLs does.

## Three-minute walkthrough

| Time | Demonstration |
|---|---|
| 0:00–0:40 | Select August, equity and active stock-picker scope. HDFC's LIC move shows +41.6M shares; **More info** reveals +41,618,000 and 29 compared / 29 expected eligible funds. Its 30 loaded includes an active portfolio without domestic equity. Explain that several funds contribute to one fund house. |
| 0:40–1:05 | Show SBI's partial badge, open **More info** for 39/40 and **Coverage breakdown** for the separate fund-by-fund page. Show Kotak/HSBC's unavailable cards. The wider market denominator is unknown. A missing prior snapshot is not a purchase or exit. Keep all eight houses visible. |
| 1:05–1:50 | From HDFC's **More info**, open LIC's **View evidence**. Confirm the exact month, house and filter scope; reconcile previous/current shares and opposing fund changes. Show original URL, workbook checksum, sheet/row, raw/normalized values, parser and release. Estimated value is not execution value or company ownership. Second-person review is pending. |
| 1:50–2:20 | Show [Franklin Reliance history](http://127.0.0.1:65100/history/INE002A01018?start=2026-02&end=2026-08&amc=Franklin%20Templeton%20AMC). Seven snapshots use the same 20-fund cohort. Explain gaps and the declared scope; do not call this all-house history or proven performance. |
| 2:20–3:00 | Follow a stock, inspect **Changes in my stocks**, reload, export the watchlist and download the monthly report. Show shares, house counts, additions/exits and largest compared fund changes. The report retains scope and release ID. Watchlists stay on this device; import/export supports transfer. |

Repeat on a phone-width view. The chart has a scrollable table alternative. Keyboard search/selection, Follow, Escape focus restoration and export/download were checked locally. Five real unassisted participant sessions remain required; browser checks do not supply those results.

## Backup and limitations

Keep the immutable database, manifest, matching code and a downloaded Markdown monthly report. Restart with the explicit retained database and a free port if needed. Captured raw evidence remains local; official web links can fail independently of the demo. The old two-fund HDFC database is the diagnostic baseline, not the repaired presentation.

Five August house scopes are complete; SBI is partial; Kotak and HSBC originals have not been obtained. Franklin alone has the declared seven-month history. Independent source review, corporate-action sample review, observed comprehension, source-use permissions and real customer/payment/retention evidence remain pending. No investment-return claim is supported.

Use the [mentor evidence packet](mentor-evidence-packet.md) to record the second review, and the [customer-validation kit](customer-validation-kit.md) for the participant steps. Refreshes and corrections follow the [runbook](refresh-runbook.md).
