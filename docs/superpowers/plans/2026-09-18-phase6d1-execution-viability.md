# Phase 6D.1 delivery outline: execution viability experiment

Date: 2026-09-18. Status: adopted milestone breakdown; **plan-next input, not an execution-ready plan**. Required design/conformance/plan reviews are pending. Expand this outline through the standard plan-next procedure, binding actual interfaces, fixtures, `Files:` and `Depends on:` lines, and the final verification task before implementation dispatch. Set roadmap status `planned` only after committing the reviewed plan.

Authority: roadmap U10 and the [design](../specs/2026-09-18-phase6d1-execution-viability-design.md). Evidence: [confirmed live-data review](../autopilot/reports/2026-09-18-fill-starvation-review-confirmed.md). Separate milestone, existing repository, isolated paper state. Do not open another repository or rewrite the fill model.

## Scheduling and dependencies

| Task | Deliverable | Depends on |
|---|---|---|
| T1 | Immutable experiment manifest, source/destination isolation, capability labels | None; inspect latest main and active work first |
| T2 | Timestamped capture, lifecycle bounds and baseline reconstruction | T1 |
| T3 | Stateful arm runner, liquidity and capacity accounting | T2 |
| T4 | Cadence-aware arm and common outcome/reporting layer | T3 |
| T5 | Book inactivity and continuity diagnosis | T1; may use T2 capture |
| T6 | Independent veto pacing and sampling amendment | T1; independent of T2-T5 results |
| T7 | Budgeted faster observer and frozen prospective cohort | T4, T5, resource/coverage preflight |
| T8 | Completed cohort, forecast and decision report for 6F | T4-T7; explicit unavailable-arm results allowed |

Keep shared-file edits serialized and follow the repository's normal review/test/release process. No new agent/model routing is introduced by this plan. Existing release holds and R4 remain binding. No inference deadline is a release exception.

## T1. Freeze the experiment contract and isolate its capabilities

- [ ] Inspect current settings, execution population, table schema and timestamp semantics; compare current main with the reviewed revision.
- [ ] Add `harness/experiments/execution_viability/` manifest and storage interfaces, using existing dependencies. Prove source reader and experiment writer separation.
- [ ] Implement immutable run/arm hashes, copied variant configuration and simulator/source provenance. No write to registered variant ids.
- [ ] Make production destination refusal and absence of a venue writer structural. Define reset/resume behavior for scratch-only state.
- [ ] Distinguish the legacy `policy-compare` admission diagnostic from the new stateful runner in CLI help/report labels. Do not silently repurpose its old filled column.
- [ ] Capture initial data/resource envelopes and the exact remaining manifest fields; do not claim the collection period started.

Validation: reject source==destination and production destinations; deny writes through source capability; two arms cannot see each other's state; manifest mismatch rejects resume; legacy default execution behavior is unchanged.

## T2. Capture the inputs and reproduce a baseline lifecycle

- [ ] Select the smallest representative repaired slice using indexed, read-only extraction or a copied database. Preserve the extraction SQL, hashes, version/row boundaries and exclusions.
- [ ] Include actual venue/session/tape/fair/signal/loop inputs and initial outstanding orders. Define event-time versus availability-time handling.
- [ ] Establish the meaning of recorded loop timestamps; do not substitute a 15-second grid without labelling it.
- [ ] Define warm-up, placement and follow-up intervals separately. Follow selected orders through expiry and required outcomes, retaining censored records.
- [ ] Add baseline comparison output for actions, fills, cancellations, capacity exclusions and visibility assumptions.

Validation: one true cancel/replacement chain, partial fill, overnight transition, delayed loop and gap/recovery. Independently trace expected queue and order transitions. Missing input history produces an explicit incomplete/unverifiable result, not silent parity.

## T3. Carry independent arm state and conserve liquidity

- [ ] Drive the existing planner, book and simulator functions through a stateful adapter. Share the algorithms; isolate the state and persistence.
- [ ] Preserve each portfolio's orders, queue tenure, print consumption, exposure and cursor between instants and across resume.
- [ ] Reproduce the shared 150-slot operational pool across the current three variants inside each arm. Preserve per-variant economic portfolio identity.
- [ ] Enforce portfolio-level print accounting through the admitted order state: reject overlapping same-key orders, conserve volume through partials and cancel/re-entry, and audit trade-through. Add an allocator only if a permitted multi-order shape requires it; do not build a second matching engine. Do not sum liquidity across alternative worlds.
- [ ] Keep historical counterfactual processing off the current production decision path; use bounded local batches and resource limits.

Validation: the captured 10-contract print cannot yield more than 10 contracts within one portfolio; restart and chunk boundaries do not double-consume; capacity stays occupied while orders rest and releases correctly; no fill after expiry; repricing changes queue priority; all claims of parity name the clock and source scope.

## T4. Implement the initial holding comparison and reporting contract

- [ ] Implement A with baseline settings and B with the finite cadence-aware executor allowance from design §4. Keep strategy quote-age checks, venue movement, edge decay and repricing unchanged.
- [ ] Exercise weekday, weekend, active sport-wide window, burst, overnight suspension, missed fetch and regime transition cases. Prove two early placements can have different lifecycles under A and B; extra admission alone is insufficient.
- [ ] Freeze opportunity episode/re-entry rules, economic weighting, common outcome observation schedule and missingness rules before reading new arm outcomes.
- [ ] Report order-, episode-, market-side- and game-level quantities without silently changing any registered gate statistic. Include fresh-source ages and concentration.
- [ ] Run the historical comparison on isolated state after baseline validation; label it exploratory and resource-bound. Do not infer unobserved faster odds for C.

Validation: a fixture where stale cancellation blocks a real later print and B receives it, plus a fixture where new information triggers repricing/edge decay and prevents the apparent counterfactual gain. Show actual simulated fills changing, not a join to historical fills.

## T5. Investigate book inactivity separately from feed failure

- [ ] Select representative `event_age`, actual gap, reconnect and missing-observation intervals from the captured data.
- [ ] Reconcile ticker inactivity with subscription/session continuity and available transport evidence. Use bounded read-only book verification if necessary.
- [ ] Publish confirmed inactivity, confirmed data loss and unresolved cases separately, with queue/recovery consequences.
- [ ] Keep baseline book-dirty eligibility intact in A/B/C. If a health-policy alternative is justified, describe a separately frozen later arm; do not sneak it into freshness B.

Validation: quiet ticker with intact subscription, actual missing frame, healthy global heartbeat but lost ticker subscription, and re-anchor with intervening trades. Report limitations when the tape cannot distinguish them.

## T6. Pace the shadow veto independently

- [ ] Inspect current queue, caching and atomic spend reservation semantics. Retain all candidate and disposition records.
- [ ] Write a concrete pacing profile from the slate: kickoff-window reserves inside each day, weekly reserves, unused-budget release times and deterministic claim order. Include annotation/parlay demand under the same caps.
- [ ] Evaluate that profile against stored arrivals and worst-case reservation cost; report coverage opportunities without inventing unasked model answers.
- [ ] Implement the profile and feature/news-based cache invalidation. Record the measurement amendment and actual activation boundary before new decisions.
- [ ] Verify coverage of near-kickoff opportunities and Sunday/Monday budget availability. Keep veto shadow-only, separate from A/B/C execution.

Validation: no overspend with concurrent reservations; midnight/ISO-week/DST boundaries; a busy Saturday cannot consume an explicitly reserved Sunday allocation; stale backlog cannot monopolize new kickoff windows; new material information bypasses cached context; all skipped evaluations remain labelled.

## T7. Collect the bounded faster-observation cohort

- [ ] Resolve real source-credit headroom, shared quota accounting, host/disk envelope and release window. Reject a production ledger destination.
- [ ] Freeze up to eight games, balanced by sport under the deterministic eligibility rule, and enumerate all markets before observing their outcomes. Persist seed, hashes, UTC/Chicago start/end times and review deadline.
- [ ] Add the prospective observer using existing source clients and isolated experiment storage. Extra fair observations must not enter ordinary production signal inputs.
- [ ] Deliver the same venue tape and common outcome observations to A/B/C; mask extra decision-time odds from A/B. Record scheduled/completed/missing observations and costs.
- [ ] Start only with a complete manifest, recorded measurement boundary, resource preflight and ordinary release verification. If C cannot fit existing budgets, retain its unavailable status and the reason.

Validation: A/B cannot read C-only odds; no lookahead from later fetches/backfills; shared source quota counts both observers; outcome availability is measured equally across arms; observer slowdown never delays the production recorder/executor; safe stop preserves data and checkpoint state.

## T8. Finish the cohort and make the decision reviewable

- [ ] Follow every admitted placement to expiry/outcome or label it censored/missing. Apply the frozen administrative deadline without significance-driven extension.
- [ ] Publish the baseline and arm results, book-health diagnosis, veto coverage and costs, operational workload and uncertainty.
- [ ] Forecast gate-variant sample accrual in scenarios with explicit distinct-game, sport, clean-book and outcome-coverage assumptions. No pooling with the primary and no counting partial fill rows as filled orders.
- [ ] Include charter mispricing-map/convergence-lag/H9 status so the dataset goals remain visible.
- [ ] Produce a dated retain/revise/stop/insufficient recommendation. Prepare any production adoption proposal with exact settings, hash, effective date, rollback and affected measurement populations; that is the existing user adoption decision, not an automatic change.
- [ ] Hand the concrete findings to 6F's period/confirmation amendment. Mark 6D.1 done only when its completion evidence exists, not when its CLI or tests are finished.

## Handoff status

Milestone integration, a draft design and this delivery outline are complete. No task checkbox is claimed complete, no collector has been activated, and no policy or veto sampling change has shipped. The controller resumes **plan-next for 6D.1**, completes its required reviews, then dispatches T1 through the normal task-brief process. U10 gives this sequence development priority while existing scheduled duties and release decisions continue. The user has authorized this milestone's bounded implementation; do not ask again whether to create the milestone or run its isolated paper investigation.
