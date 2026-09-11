**New roadmap: establish a trustworthy experiment before expanding it**

The next best step is a focused execution-correctness and evidence-recovery milestone. Fix subscription sequencing, recovery anchoring, and queue accounting together, include expiry/rejection boundaries, and audit the only actual filled order on a copied dataset. Prepare the host and complete time-sensitive reporting repairs in parallel. Do not begin by relaxing fair-price freshness or adding a fill-producing strategy variant.

This roadmap reconciles [both reviews and the verified differences](RECONCILIATION.md). It is a proposed replacement for phase 6's current feature-first order, written outside the repository as requested. Application code, production services, frozen strategy files and gate definitions remain unchanged. The baseline is repository commit `6eed2d8d49a7bf1f107dfd83141b8eeef556b620`; last reviewed NAS build `7c3d555`. Prior production observations are time-stamped in the input reviews; this document does not claim a fresh live status.

| Milestone | Work | Completion evidence | Dependency |
|---|---|---|---|
| 6A — preserve and define | Preserve original evidence; identify affected periods; create the correction manifest and runnable failure cases; make deploy behavior reliable. | Reviewed source/data identity, known limitations, failing economic/protocol cases, additive deploy/restart plan. | Start now. |
| 6B — repair execution | Subscription continuity, recovery snapshot/watermarks, trade/delta reconciliation, expiry, rejection, dirty-time scope, and equivalent replay. | Realistic tests and copied-tape audit pass; actual/counterfactual accounting and replay inputs are explicit. | 6A. |
| 6C — make reports trustworthy | Local week keys, gate documentation/measurement eligibility, confirmation floors, exact-contract dashboard joins, complete denominators, prior-week annotation selection. | Sunday/Monday boundary cases pass; report truthfully separates incomplete, insufficient and zero. | Start alongside 6B; final repaired-fill reports depend on 6B. |
| 6D — restore sustained evaluation | Trace scheduled input → fresh fair → primary/gate evaluation → valid resting time; repair scheduling/budget isolation; choose any revised holding/capacity policy explicitly. | Primary/gate completion measured end to end; no silent omissions; current and proposed policies compared on repaired replay. | Instrumentation can start now; policy comparison needs 6B. |
| 6E — prove the operating environment | Inventory target, rehearse complete restore, benchmark corrected workload, select storage/host, then perform a measured cutover if appropriate. | Consistent migration boundary, validated contents and backups, acceptable game-window/cold-start/backup behavior. | Inventory/rehearsal alongside 6B; final benchmark needs corrected workload and deploy plumbing. |
| 6F — collect valid prospective evidence | Start a documented measurement period with stable code/policy and adequate operational coverage. | First healthy football weekend yields per-variant exposure, filled orders, independent games and usable outcomes; sample-accrual forecast. | Execution, essential numeric/eligibility reporting, declared policy and operating environment accepted; optional annotation and migration itself are not prerequisites. |
| 7 — expand only with a working baseline | New variants and optional hypotheses selected from repaired research evidence. | Separately registered question, feasible sample, no regression in core recording/evaluation. | 6F operational checkpoint. |

**6A: make the repair reviewable, and preserve what happened.**

The external reviews already preserve source identities and sampled observations. The implementation work should add a small, complete evidence capsule for order 157 and representative clean, interleaved, gap/recovery, delayed-loop and capacity-bound periods. Include trades, deltas, snapshots, relevant fair/gap rows, original orders/fills/ledger, executor settings, and available restart/recovery events. Obtain it using indexed bounded extraction or a copied database, outside protected game windows; do not run expensive audit scans on the recording NAS.

Record old/new code and measurement versions, strategy/config hashes, the deployment boundary, affected order/run intervals, eligible and excluded measurements, and exact re-score commands. Keep original rows and published reports intact. Corrected replay results are retrospective estimates, not silently rewritten live fills. If the tape or necessary transitions are missing, mark that slice unverifiable. An epoch label by itself does not exclude historical rows from the existing cumulative gate: implement an explicitly documented eligibility mechanism or keep the new-period analysis separate until that mechanism is approved and verified.

Fix deploy plumbing before the next deployment depends on it: carried fix 37 must include all changed services, prevent no-op DDL/read-lock stalls, and restore stopped services if a schema step fails. Recheck the source/deployed diff and pending worktrees before assuming fixes 34/40/41 are already merged. The reviewed HEAD still lists them as unfinished. This roadmap does not activate an autonomous deployment.

**6B: repair the engine that creates positions and counterfactual evidence.**

Treat these as one correctness milestone implemented in small reviewed changes:

- Validate continuity over the complete subscription stream, including connection/session boundaries. Per-market streams legitimately skip subscription sequence numbers. Preserve detection of real missing frames.
- Anchor both order-book deltas and trade/print watermarks consistently when loading or recovering a snapshot. A trade already reflected in the snapshot must not reduce the newly anchored queue again.
- Reconcile printed trades with level decrements so one trade consumes liquidity once. Cover equal timestamps, delayed arrival, out-of-order receipt and batch splits. Swapping sort order alone is not a complete reconciliation algorithm. Aggregate book data also cannot identify whether every unrelated cancellation was ahead of us; report sensitivity where exact queue ownership is unknowable.
- Clamp watched fills at expiry and prevent placement from a latest rejected signal. Verify idempotence after cancellation, retry and restart.
- Separate watched and counterfactual dirty intervals, with cause labels for real gaps, recorder death, event-age policy and recovery. Measure elapsed time when reporting duration. Specify whether gate cleanliness covers placement, a fill, or the actual resting interval; retain the raw historical measurement and version any semantic correction.
- Reproduce the live variant population and shared capacity in baseline replay. An independent single-variant simulation may remain useful, but it is a different experiment and cannot be compared directly to capacity-constrained live output.

Acceptance requires realistic mixed-market fixtures, a genuine gap/reconnect case, recovery containing an older trade, liquidity conservation, no post-expiry fills, no placement from rejected targets, and consistent results when the same event history is batched or restarted differently under the same observation policy. Include an independently calculated expected queue/ledger result; passing a test that repeats the implementation's assumption is insufficient. Timing-policy differences must be recorded rather than hidden under a blanket parity claim.

Audit order 157 before using it as a validated fill. Its saved final quantities can be produced by more than one defective synthetic path, so the review has not identified its actual causal history. Publish the audit result as validated, corrected, or unverifiable with the supporting tape. Re-score no-watcher outcomes only after the same model repairs; the current 27/445 figure is not a trustworthy fill ceiling.

**6C: meet the reporting deadlines without manufacturing a passing experiment.**

By Sunday September 13, 19:00 CT, make Chicago week selection consistent across Ticket, WTD, Study scheduler/readers and Pulse, extending fix 33's original scope. Test Sunday evening, Monday, year boundaries and daylight-saving boundaries. Keep UTC tape partitioning unchanged. If the repair is not safely deployable by then, explicitly generate the correct report period and label the affected surfaces; do not call the data lost or quietly count the wrong week.

Before Monday September 14, 09:00 CT, produce the scheduled report as a diagnostic. It must show the gate variant's actual filled orders and distinct games, separate actual/counterfactual methods, state operational coverage and skipped work, identify the order-157 audit status, and distinguish fresh dashboard construction from the age of underlying report cells. Reconcile README and coded gate definitions with the registered specification; retain every requirement in its proper place and explicitly identify external operational duties. Do not remove criteria or relax sample thresholds to make Monday look successful.

Fix the confirmation path's missing cluster minimum before selection/confirmation is used. Store the intended effect direction when selecting cells and define what confirmation means. Enforce amendment-specific eligibility and show the number of excluded/missing games or outcomes. The current gate is cumulative and excludes replay rows, so writing a new label or replaying a period does not automatically repair its inputs.

Fix the exact-contract fair-value join on Floor and all side-space comparisons. Make funnel units explicit: unique candidate opportunities, distinct intent episodes, placements, skips, actual filled orders, and counterfactual outcomes. A candidate row count and a decision-event count are not a conversion funnel. Address the 14-day exposure display bound with a complete aggregate or an explicit coverage limitation.

Annotation can follow the numerical report. Fix 41's token/render budget is insufficient by itself: the current worker ignores Monday's previous-week final report. Select a bounded backlog of unannotated final reports with backoff/idempotence, using stored report cells. Test successful prior-week annotation. A failed or missing model annotation must not delay the report or consume the executor's budget.

**6D: define when the strategy should actually be able to participate.**

Instrument scheduled-versus-completed collection and evaluation by sport, time to kickoff, feed, market and variant. Distinguish source quote age, transport lag, fair-calculation age, signal-to-order delay, clean resting seconds and tape continuity. Include scheduled work that produced no fair/gap/signal row; the current scored-tick denominator can hide entirely missing stages. Count capacity exclusions separately from strategy rejection and unreliable-data skips.

Prioritize completing direct fair values and the primary/gate evaluations over derived pricing and optional research. The current 45-second budget can expire before the first variant. Measure stage costs and remove duplicate/unnecessary work; if process or job separation is needed, preserve raw recording and use explicit queues, budgets and coverage. Pricing exhaustion and missing primary/gate evaluations already justify investigation. The saved 109/243 exhaustion count uses priced runs; define the eligible game-day denominator before claiming the original 10% normalizer trigger is met. Adding a normalizer process alone is not proof that database contention or pricing cost was fixed.

The initial policy preference is to keep current freshness standards and provide timely refresh/calculation in explicitly defined participation windows. After repair, compare that baseline with any longer-hold, narrower-window or different-capacity proposal on equivalent tape and resources. A 600–900-second stale allowance, rest-to-expiry rule, fixed per-variant slots, fillability-based admission, join-the-bid price or near-kickoff-only strategy each changes the opportunity set. None should be smuggled in as a hardware workaround. Register/version the selected policy before collecting its prospective validation period.

Acceptance is a coverage contract, declared before the run: every scheduled eligible primary/gate evaluation either completes inside the relevant freshness window or leaves an explicit reason and affected interval. There should be zero unexplained omissions. Quantify the remaining documented missingness; agree its tolerance before using the period for inference. Reduced order churn alone is not acceptance—orders must spend time on trustworthy books with fresh information and meaningful queue opportunity.

**6E: prepare migration now, choose the target from evidence, and rehearse the cutover.**

The stronger conclusion from both reviews is a need for reliable capacity and fast local database storage. The NAS has a documented rotational database path and repeated game-window resource pressure. The historical migration trigger was met. The Mini is still not sized: record its chip, RAM, available SSD space, container-runtime allocation and intended unattended operation. Avoid purchasing or requiring a particular configuration from the other review's unverified prices or latency estimates.

Preparation can run while 6B–6D are implemented. Use the corrected workload for the final comparison: removing false dirty states activates previously skipped work and may increase load. Include collection, all intended variants, research/background jobs, report generation, backup and cold startup. Retain indexed reads, batching, cached immutable inputs and stored report-cell rendering. Fix next-partition BRIN maintenance and self-guard behavior; a faster host does not make those concerns disappear.

A complete migration rehearsal must inventory active and legacy partitions, non-bulk tables, sequences, schema/index options, settings, strategy/gate identities, secret availability/permissions and backup-key handling. The ordinary nightly archive excludes bulk data and is not sufficient on its own. For a plain logical-dump cutover, stop all writers before the final dump's snapshot and keep the old stack stopped; otherwise use a separately tested synchronization method that captures all intervening changes. Do not adopt the proposed “copy partitions written since the dump” shortcut. PostgreSQL's dump consistency applies to its snapshot; later writes need their own defined boundary. [PostgreSQL SQL dump documentation](https://www.postgresql.org/docs/16/backup-dump.html).

Measure actual dump/restore time and storage requirements on the target. Reconcile table/partition contents at the chosen boundary using counts and appropriate key/content checks; verify sequences, ledger totals, code/settings identities, restored indexes and backup recoverability. `max(id)` equality alone cannot prove completeness. Start exactly one writer stack at cutover. Retaining the old host is useful, but after the new host records data, simply restarting the old copy would discard the new period; define rollback data reconciliation explicitly.

Use an actual quiet deployment window after rehearsal, checking both games and scheduled jobs. Tuesday September 15 is a possible planning window, not a commitment; its 09:00 futures job must be accounted for. There is no reason to preserve a known-broken build for the entire weekend, and no reason to rush an unrehearsed move before kickoff. If repairs or migration are not ready, continue clearly labeled diagnostic collection.

Operational acceptance uses the existing executor p95 target of at most 7.5 seconds during two representative game windows, with adequate telemetry sampling stated. Also require no persistent tape backlog, no sustained active paging, correctly handled real gaps, timely primary/gate evaluation, completed markout/report work, and fresh required surfaces. Include a backup and cold-start observation. Refresh database growth and free-space projections; moving a copy while retaining the NAS source does not immediately free NAS storage.

**6F: start a valid prospective period, then decide whether the strategy deserves more time.**

Start this period once execution correctness, essential numerical/eligibility reporting, the declared policy and the operating environment are accepted. Optional annotations and peripheral presentation repairs can remain unfinished; the NAS can satisfy the environment requirement if it passes representative corrected-workload checks. Give the period a recorded version boundary and retain the earlier period as diagnostic evidence with measurement-specific eligibility. Do not blindly reset all research, discard adverse observations, or assert that every old price snapshot is invalid because execution was defective.

At the first complete healthy football weekend, report separately for each variant: eligible market/game coverage, clean executable resting hours, unique order episodes, actual queue-filled orders, independent filled games, and mature fresh CLV/markout coverage. Include cancellations, capacity exclusions, measurement gaps, outcome maturity and uncertainty. This checkpoint establishes sample accrual and operating behavior, not profitability.

Forecast the time to the gate's 150 actual filled orders across 40 games/both sports for `sharp_two_sided` alone. Do not pool variants, count partial fill records as independent orders, or use counterfactual fills toward that requirement. Retain the preregistered H1 interpretation floor and uncertainty requirements; reaching a minimum does not prove an edge. If a useful sample will not accrue in the planned period, explicitly extend the calendar or end this particular maker configuration as operationally unproductive. A new strategy becomes a separately registered experiment.

September 14 remains a diagnostic report. September 21/28 must not force selection or confirmation from defective or insufficient evidence. Choose and record revised selection/confirmation dates and any extension rule using operational completeness and sample-accrual information before examining confirmatory effect estimates. Keep hypothesis selection separate from the later confirmation sample. Repeated extensions or interim significance checks cannot silently retain a fixed-horizon significance claim; specify an appropriate analysis rule prospectively if interim decisions are needed.

**What happens to the existing phase-6 backlog.**

| Existing item | New disposition |
|---|---|
| Key-number model/new derived variant | Defer to phase 7 or exploratory replay after model repair; it cannot validate the current gate strategy. |
| Duplicate quote selection | Fold into 6D's deterministic input/coverage work where it affects source identity or cost. |
| Taker-imbalance label | Defer; compute retrospectively only where recorded tape coverage supports it. |
| Dedicated normalizer | Investigate now under 6D because pricing exhaustion and missing evaluations are established. Define the trigger's eligible denominator and choose a design from stage timings. |
| Bare-city aliases | Continue verified operator maintenance when it changes matching coverage; version amendments. |
| Market lifecycle channel | Defer unless a concrete missing lifecycle input prevents correct settlement/measurement; a passing unrelated check alone is not proof it is unnecessary. |
| Archive/drop policy | Write the storage/retention proposal now; execute no deletion as part of this review or roadmap. Use current measured growth and backup coverage. |
| Fix 33 and related week consumers | 6C, before the Sunday/Monday rollover. |
| Fix 34, self-guard behavior | 6E; startup protection must still detect persistent overload. |
| Fix 37, deployment plumbing | 6A, before dependent deploys. |
| Fix 40, RFQ boundary filtering | Finish and verify as background-load containment; do not automatically disable the listener based on old firehose numbers. |
| Fix 41 and prior-week annotation selection | 6C, subordinate to the numeric report. |
| Cosmetic surfaces/extra model research | Phase 7 after the baseline works. |

The concrete first implementation task is therefore: build the small copied-tape evidence capsule, make the sequence/recovery/queue failures executable as regressions, and implement 6B behind a versioned correction manifest. Inventory and restore-rehearsal preparation for the Mini, deploy fix 37, and Sunday/Monday reporting fixes can proceed in parallel. The goal of this phase is a result we can interpret, even if that result is that the strategy has too few executable opportunities.

[Evidence index, preserved inputs and reproduction instructions](evidence/README.md).
