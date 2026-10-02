# FindIt: mentor readiness and customer validation plan

Original plan prepared 1 October 2026. Implementation has since progressed
under the owner's instructions. Current evidence and pending gates are in
[observed acceptance](observed-acceptance.md) and
[September release preparation](september-release-preparation.md).
This planning document itself does not authorize deployment, outreach or payments.

## 1. Intended outcome

Make FindIt a dependable way to understand and verify monthly mutual-fund
holding changes, then test whether a specific customer group values that
workflow enough to return and pay.

Initial customer hypothesis: independent equity researchers in India who
already compare mutual-fund disclosures while researching stocks. The
initial promise is: "Understand what changed in the stocks you follow and
verify it against the original disclosures."

This is a hypothesis to validate with users. The product's existing research
does not establish that following fund purchases improves returns. Product
claims and paid offers must describe research utility, not predictive alpha.

Two milestones are distinct:

- Mentor readiness: credible coverage, traceable calculations, understandable
  history, observed usability, and a reproducible demonstration.
- Customer validation: evidence about audience, differentiation, repeat use,
  payments, acquisition, and operating costs. Software alone cannot establish
  these facts or guarantee a VC's investment decision.

## 2. Original planning baseline and implementation approach

At plan preparation, the reviewed August 2026 overview had four usable
summaries among eight selected houses. HDFC compared 2 of 29 loaded funds;
Mirae lacked a usable July comparison; Kotak, Franklin Templeton, and HSBC
had no loaded portfolios.
These original loaded counts were not proof of the complete eligible fund universe.
Later repairs and acquisitions are documented in the acceptance records above;
do not use this historical planning baseline as today's coverage status.

Keep the requested five largest houses plus three additional foreign-owned
houses operating in India. Show the dated AMFI AUM selection period. Record
future roster changes with their official source rather than changing
historical cohorts silently.

Use the current server-rendered application, SQLite, and JavaScript. Extend
the existing intake, validation, calculations, and reporting. Avoid a second
implementation of coverage, trading values, or summaries in the browser.

Relevant existing implementation paths, verified through Codebase Memory and
targeted source reads:

| Responsibility | Existing location | Planned use |
| --- | --- | --- |
| Monthly intake and missing-house reporting | `findit/ingest/intake.py` | Extend acquisition manifests and completeness reporting |
| Source tracking and validation orchestration | `findit/pipeline.py` | Carry raw-source provenance through loading and validation |
| Schema, aliases, statuses, and price provenance | `findit/store/db.py` | Add compatible inventory/provenance records as needed |
| Current comparison coverage | `findit/core/coverage.py` | Reconcile official expected scope with loaded and usable data |
| Fund-house aggregation and ranking | `findit/core/consensus_signals.py` | Reuse calculations for evidence, history, and watchlists |
| Monthly web context | `findit/web/app.py` | Serve consistent coverage, evidence, and history contracts |
| Overview cards and main page | `findit/web/templates/_fund_summary.html`, `findit/web/templates/dashboard.html` | Put scope and useful next actions beside results |
| Deterministic fund digest | `findit/cli/digest.py` | Reuse summary rendering; add stock-watchlist reporting separately |

Before implementation, query the graph again for the precise affected paths,
check coverage, and verify the current source. Exact schema and endpoint names
will be selected after the initial data audit. This plan does not prescribe
speculative SQL migrations.

## 3. Phase A: establish the customer hypothesis early

Start this research alongside the data audit, before building the watchlist
or selecting a subscription price.

Prepare a short interview guide and a results sheet for 10–15 prospective
users. Ask them to demonstrate a recent research task, their current tools,
time spent, errors encountered, and what they currently pay for. Observe an
actual workflow rather than asking whether they like the idea.

Record customer role, task frequency, existing alternative, baseline task
time, desired output, and objections. Keep identifying details private. Seek
participants beyond friends; disclose recruitment sources and relationships.

Deliverables: an evidence-backed initial customer profile, one priority job,
and an explicit decision to continue with this audience or change it.

Exit check: several independent participants demonstrate the same recurring
problem and agree to test the next release. If that pattern is absent,
revise the audience or job before expanding the product.

Agent work can prepare interview materials and analyze supplied results.
Recruitment, interviews, and external messages require human participation
or explicit authorization; none are initiated during planning.

## 4. Phase B: repair and explain coverage

First audit July and August for the eight featured houses and the wider
universe contributing to the stock ranking. Build a fund-by-month matrix
with official expected scope, source availability, identity, parse/load
status, validation status, and exclusion reason.

Diagnose HDFC's 2-of-29 comparison specifically. Distinguish missing prior
snapshots, names mapping to different identities, intentionally excluded
fund types, parser/unit problems, and validation withholding. Repair only
verified problems; never lower validation thresholds to make counts look
complete. Deduplicate multiple plans or repeated disclosures of the same
portfolio using verified identities, not speculative name matching.

Retrieve available official disclosures for the missing houses and months.
Preserve original files and hashes. If an official archive cannot be obtained,
record that limitation and keep its result unavailable.

Define coverage states consistently:

- Complete: every officially expected eligible fund in the declared scope
  has the required, validated snapshots and usable comparison.
- Partial: some usable comparisons exist, with exclusions disclosed.
- Unavailable: no usable comparison exists.
- Unknown completeness: the official expected inventory is not established;
  loaded counts must not imply complete coverage.

Show a scope badge near each card title, not only at the bottom. Use wording
such as "Partial: 2 funds compared" and "Biggest move among compared funds."
Once the official denominator is verified, display compared versus expected,
with a breakdown of missing, withheld, new/closed/merged, and out-of-scope
funds. Keep "loaded" and "expected" distinct. Apply the same rule to the
market-wide table: selected-house completeness is not all-market completeness.

Exit checks:

- Every expected fund is accounted for; unexplained gaps are zero.
- The initial July/August target is usable coverage for all eight houses.
  Genuine source unavailability keeps that target unfinished and is reported.
- Claims of complete coverage require the definition above, not an arbitrary
  percentage threshold.
- Missing data never becomes an exit, a zero holding, or a purchase.
- Reloading identical sources does not create duplicate holdings or change
  results; corrected identities are reconciled without losing originals.

## 5. Phase C: make headline results independently verifiable

Add a "View evidence" action to summary moves and stock changes. It should
open the exact house, stock, month, and filter scope that produced the result.

Show previous shares, current shares, adjusted share change, contributing
individual funds, and the netting of opposing trades. Explain split/bonus
adjustments and whether they are confirmed or inferred. Show the estimated
trading-value convention, price source, and the distinction between a change
in shares held and percentage ownership of a company.

Extend the existing source tracking with raw disclosure URL, reporting period,
actual publication date when known, retrieval time, original-file checksum,
workbook/sheet/row location where available, parser version, and data-release
version. Preserve both raw and normalized values. Mark unknown publication
dates and deadline-based assumptions explicitly; do not invent actual dates.
Offer official source links or a permitted archive view without exposing
local filesystem paths through the public application.

Review unusually large moves against originals and independent prices. Record
whether each is verified, explained by an adjustment, or unresolved. Reuse
the existing validation and calculation path for all evidence views.

Exit checks:

- Every displayed featured headline can be traced to both source snapshots
  and reconciles with the sum of contributing funds.
- Review the largest moves plus a varied sample of at least 20 other changes,
  including purchases, reductions, new holdings, exits, and corporate actions.
- Two people can reproduce selected calculations from the evidence panel.
  The user or mentor can provide the independent second review.
- Tests cover incomplete provenance, mixed-direction trades, unit mistakes,
  splits, missing prices, and source corrections; unresolved items retain an
  explicit status. A passing test suite is not a substitute for source review.

## 6. Phase D: add history without creating misleading trends

Backfill seven consecutive monthly snapshots to support six monthly
comparisons. Aim for twelve comparisons later, after archive availability
and maintenance effort are understood. Use the same acquisition and
validation process as current data.

In stock details, show monthly shares held, fund-house buying/selling counts,
net estimated trading values, and the scope/coverage for each point. Add
plain-language statements about continued buying or a reversal only when
the underlying comparisons support them.

A changing set of loaded funds can manufacture a trend. Provide a consistent
cohort for comparisons across the selected range and name it clearly. If the
cohort becomes too limited to support a statement, show the limitation.
Gaps stay gaps; do not interpolate them into zero activity. Show corporate
action and data-correction markers alongside affected points.

Exit checks: seven source-backed snapshots are present for the declared
history scope; history reconciles with each monthly result; changing coverage
cannot silently create a persistence/reversal claim; charts work on mobile
and have an accessible table alternative. Historical fund activity is not
presented as proven investment performance.

## 7. Phase E: test clarity and finish the mentor demonstration

Preserve the simple reading order: selected month and scope, monthly overview,
stock activity, then detailed evidence and individual-fund summaries. Reduce
empty-card visual weight while keeping all eight requested houses visible.
Keep technical explanations close to the result they clarify.

Run five unassisted usability sessions. Tasks: identify a featured move,
explain fund house versus individual fund, recognize partial coverage, verify
a share change, and interpret a historical reversal or gap. Record completion,
time, mistakes, and requests for help.

Initial acceptance targets: at least four of five users complete the core
tasks without help; no participant mistakes partial coverage for the entire
house; users reach and explain one source-backed result within a minute.
These are internal release targets, not proof of market demand. Fix confusing
parts and repeat affected tasks with new participants.

Prepare a three-minute mentor walkthrough built around a real question, with
an example of complete coverage, an honest partial/unavailable case, source
verification, and history. Include reproducible startup steps, the data
release identifier, limitations, and a backup local demonstration.

Mentor milestone: Phases B–E pass their checks, the source review is recorded,
and the demonstration runs on desktop and mobile. Unobtainable sources are
disclosed rather than counted as completed work.

## 8. Phase F: implement a workflow worth returning to

Use findings from Phase A to confirm the stock-following workflow. Implement
a small stock watchlist and a "Changes in my stocks" view: month, fund houses
buying/selling, additions/exits, biggest verified changes, and evidence links.

For the first local/private version, save watchlists on the user's device.
Explain device-only storage and provide export/import; do not imply cross-device
sync. A downloaded monthly report should preserve its month, scope, coverage,
and data-release version. Reuse shared calculations and deterministic summary
rendering rather than generating a separate narrative with different numbers.

Add opt-in monthly update delivery only when the pilot needs it. Sending
requires real delivery infrastructure, recipient consent, unsubscribe handling,
duplicate prevention, and explicit authorization. Accounts and cloud sync are
conditional on demonstrated demand. Hosted personalized pilot features need
appropriate access controls before real participant data is stored.

Exit checks: a user can follow stocks, reload without losing the watchlist,
understand an unchanged or unavailable result, and reproduce the same report
from the same data release. Mobile and keyboard flows work. Historical
replays can test usability but are not counted as real monthly retention.

## 9. Phase G: answer the differentiation and business questions

Benchmark the same real tasks against users' existing tools. RupeeVest already
offers holding trends and monthly net buying/selling lists. Trendlyne documents
fund holdings, portfolio analysis, and monthly rebalance reminders. These
overlapping features make generic holdings tracking insufficient as the pitch.

Sources checked 1 October 2026:

- [RupeeVest holdings tracker](https://www.rupeevest.com/Mutual-Fund-Holdings)
- [Trendlyne fund-holdings workflow](https://help.trendlyne.com/support/solutions/articles/84000383587-how-can-i-create-a-basket-with-stocks-in-a-mutual-fund-)

Compare time to a correct answer, ability to verify a headline, recognition of
incomplete coverage, and usefulness of the exported result. Use participant
workflows and comparable data scopes; do not claim competitor deficiencies
that have not been tested. Choose differentiation based on observed advantage.

Run a small paid pilot after the data and usability milestones. Select an
initial offer and two price hypotheses from interview evidence and observed
time savings. Use a clear research-product offer with defined coverage and
update frequency. Start with a simple paid service/report or subscription;
build automated checkout only if it removes a demonstrated obstacle.

Collect actual settled payments, stated reasons for paying or declining,
renewals, cancellations, and refunds. Record discounted or personally related
participants separately. Real merchant setup, billing identity, pricing,
payment acceptance, public deployment, and outreach are human decisions or
explicitly authorized actions at execution time, not activities in this plan.

Measure two subsequent real monthly publication cycles. Define activation as
completing a useful watchlist task and inspecting its evidence. Define monthly
return use as performing a meaningful research task after a new data release;
exclude staff, test activity, and notification opens alone. Use consented,
minimal event collection or structured pilot interviews before adding an
analytics service. Do not log unnecessary searches or portfolio contents.

Working pilot targets, to register before recruiting: 10 target participants,
at least 7 activated, at least 5 meaningfully returning in each subsequent
release, at least 3 independent paying customers, and at least 2 renewals.
Report counts and denominators. These small-sample targets are decision aids,
not proof of product-market fit. If they fail, investigate the audience, task,
price, and trust issues before adding unrelated features.

## 10. Phase H: prove reliable operation and a plausible business

Make monthly refresh a documented, repeatable process: acquire available
official files, parse, validate, compare, review anomalies, and publish a
versioned release. Run acquisition independently from public web requests.
Stage changes in a separate database copy, retain the last good release,
and promote only a verified snapshot. Corrected disclosures produce an
identified revision and an explanation rather than invisible changed reports.

Measure source availability delays, update latency after retrieval, processing
time, failures, repair effort, storage/hosting/delivery charges, support time,
and any source/license fees. Record founder labor separately from cash costs.
Test failed refreshes and rollback. Source-use and redistribution terms must
be understood before public paid distribution; paid-source purchases require
a spending decision.

For acquisition, test two channels selected from the interviews, using founder
outreach or relevant research communities. Prepare materials first; contact
people only with authorization. Measure qualified participants, activation,
paying conversions, and acquisition cost including time. Do not estimate
conversion rates from page views alone.

Build a simple business worksheet from evidence: verified addressable customer
counts, tested price, conversion/retention assumptions, revenue, operating
cost per active customer, support labor, and sensitivity to lower retention
or higher maintenance cost. Label all unvalidated assumptions. A credible
specialist subscription business and a venture-scale opportunity are separate
claims; test whether broader distribution and expansion can support the latter.

Potential defensibility: accumulated corrected history, reliable identity and
format handling, auditable revisions, and integration into repeat research work.
Document measurable quality and workflow gains. Public data and a polished
interface alone do not establish a durable advantage.

## 11. Evidence required to answer each VC question

| Question | Product/process change | Evidence required for an honest answer |
| --- | --- | --- |
| Who needs this? | Focused customer profile and one priority task | Interview records and observed recurring workflow |
| Why choose FindIt? | Traceable evidence, explicit coverage, useful history | Comparable task results against existing alternatives |
| Why return? | Saved watchlist and monthly change report | Meaningful use across two subsequent real releases |
| Will they pay? | Clear pilot offer and tested pricing | Independent settled payments and renewals |
| How do customers arrive? | Two focused recruitment/distribution experiments | Qualified conversion and acquisition cost, including time |
| Can it operate economically? | Repeatable refresh and cost reporting | Actual operating/support costs and sensitivity worksheet |
| What becomes defensible? | Corrected historical dataset and repeat research workflow | Accuracy, revision history, retention, and maintenance advantage |
| Can this justify venture funding? | Evidence packet and expansion hypotheses | Bottom-up market estimate and tested growth assumptions; no guaranteed conclusion |

Deliver a mentor evidence packet after Phase E. Deliver a VC evidence packet
after the pilot cycles: measured results, sources, customer observations,
payments, retention cohorts, competitor comparison, costs, and remaining
unknowns. Unanswered questions stay explicit; features do not replace evidence.

## 12. Execution order, checkpoints, and scope control

Start Phase A interviews and Phase B coverage audit together. Implementation
order is B → C → D → E → F; Phase G benchmarking starts once usable results
exist, with its paid pilot after mentor readiness and the watchlist workflow.
Instrument operational cost and refresh reliability during B onward; finish
Phase H's business assessment using actual pilot results.

Each implementation batch should be small and independently reviewable:
coverage audit/labels; verified source repairs; provenance storage; evidence
view; historical backfill; history presentation; usability fixes; watchlist;
report export; pilot instrumentation; refresh/revision handling. Commit after
the relevant checks pass, as requested by the user. Keep original disclosures
and databases intact. Keep private data and payment records out of Git.

Tests should exercise meaningful behavior: coverage denominator correctness,
identity reconciliation, provenance, calculations, changing historical cohorts,
gaps, revisions, watchlist persistence, and report reproducibility. Verify
layout and accessibility in the browser. Repeat full checks for substantive
changes, not as a substitute for customer or source validation.

Budget and schedule should be estimated after the coverage audit and initial
interviews reveal archive availability and repair effort. A real retention
answer requires waiting for subsequent monthly releases; it cannot be
compressed into an engineering sprint.

First implementation checkpoint, when authorized: produce the verified
July/August coverage matrix, diagnose HDFC's low comparison count, identify
official source availability for the four missing summaries, and implement
accurate coverage labels. Use that result to size the remaining work.
