# Autopilot execution-efficiency review

2026-09-13. Review of the Sep 12 evening–Sep 13 morning run on Omarchy. Three independent review lenses: test/release scheduling; controller/recovery; implementation/review workflow. Primary reviewer reconciled their findings against source, receipts, and terminal log records.

This is a restructuring proposal, not an implemented change. No active-loop files, source, services, agent sessions, or test processes were changed. This report is outside the checkout because an untracked report could invalidate Claude's current clean-tree checks. Local working ledgers can continue changing after this snapshot.

## Recommendation

Keep Claude's controller, isolated workers, independent reviewers, one database-suite slot, and scientific acceptance. Change how work enters that slot, freeze reviewed candidates before full testing, and make controller-owned acceptance receipts usable for the same exact commit after fast-forward integration. Maintain live operational state outside the release checkout and archive it at safe boundaries.

Implement the scheduling/instruction corrections first, then the small supporting tools. Broader test parallelism, another model provider, and a large orchestration rewrite are separate experiments. The observed bottleneck already has substantial local remedies.

## Measured baseline

The eight standalone full-suite receipt files in the three overnight ledgers account for approximately 231 minutes between recorded start and finish. Three additional, distinct superseded suites have terminal receipts embedded in their logs. Altogether, eleven completed full-suite runs account for approximately 314 minutes. This excludes targeted tests, interrupted runs without terminal receipts, and queue waits; receipt timestamps begin after lock admission/database provisioning and are not complete job latency.

The clearest potentially avoidable portion is:

| Run | Observed outcome | Pytest duration |
|---|---|---:|
| T3 `b65635e` | Completed successfully after review fixes dirtied the candidate | 25.2 min |
| T3 `e6975ab` | Completed successfully after the next fix round dirtied the candidate | 25.5 min |
| Fix 49 `5e034a7` | Completed successfully after review fixes dirtied the candidate | 31.7 min |
| Main `ebf0953` | Repeated a successful controller-run full suite at the identical SHA because release accepts only the main-database receipt path | 27.5 min |
| Total | Test capacity potentially reclaimable under the proposed design | **109.9 min** |

These are capacity costs, not a promise that delivery would have finished 110 minutes earlier. Some activity overlapped model work; cancellation and readiness policies affect the counterfactual. The duplicate main run was required by the existing release contract, so it must not be skipped until that contract is changed and tested.

Evidence: T3 original terminal receipt (historical local evidence: `.superpowers/sdd/2026-09-11-phase6b-repair-execution/t3-full-b65635e-aborted-superseded.log`), T3 round-one terminal receipt (historical local evidence: `.superpowers/sdd/2026-09-11-phase6b-repair-execution/t3-full-e6975ab-superseded-signal.log`), fix49 original terminal receipt (historical local evidence: `.superpowers/sdd/hotfix-2026-09-12-omarchy/fix49-full-5e034a7-aborted-superseded.log`), duplicate main receipt (historical local evidence: `.superpowers/sdd/omarchy-loop-2026-09-12/main-full-ebf0953-receipt.json`).

## Findings and concrete changes

### 1. Admit full suites only after review is clean

Full suites repeatedly began concurrently with review. When review required edits, the full run lost its acceptance value and sometimes continued consuming the shared slot. This delayed targeted tests needed to finish the very fixes that would unblock delivery.

Proposed task sequence:

`implement + covering tests → freeze patch → independent review → fix + scoped tests/re-review → freeze acceptance candidate → full suite → integrate`

Review can overlap independent source investigation and another task's implementation. Scoped tests may run during review when their source snapshot is stable. Full acceptance starts only after no blocking review findings remain. Workers stop editing the acceptance snapshot; a new change creates a new candidate and explicitly supersedes the old request.

Keep full acceptance before integration in the first rollout. Do not introduce targeted-only task merges at the same time as the scheduler change. The instruction already says the controller should run full acceptance outside the worker namespace; make that the sole full-suite admission path.

Evidence: [phase acceptance rules](../../../../.claude/skills/autopilot/references/phase.md), overnight T3 transitions (historical local evidence: `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`).

### 2. Replace implicit lock waiting with explicit test admission

`test-suite.py` acquires one blocking flock before recording branch/head/start. It cannot report queue duration or prioritize a short regression test over a pending obsolete full suite. The same lock covers all `make test` invocations. Worker command timeouts include waiting, so an alive worker can look silent while making no progress.

Initially, the controller maintains an explicit queue and does not launch multiple long-waiting acceptance commands. Next, add durable requests with task/attempt ID, candidate SHA, worktree/database ownership, scope, priority, deadline, queued/start/end timestamps, and status. Return a queued handle promptly. Revalidate head/review/ownership at admission; discard stale requests without running them.

Default scheduling: an imminent approved release candidate gets its reserved slot; otherwise favor short checks that unblock review, then accepted phase candidates. Add aging so a stream of short tests cannot starve full acceptance. Keep a running valid suite non-preemptive by default. Only cancel an obsolete candidate or an explicitly justified higher-priority emergency; acknowledge cancellation after its owned process tree is gone and the lock is free.

Track queued, executing, and no-heartbeat states separately. Queue time still consumes the release deadline; it simply is not evidence of a dead implementer. Do not reinterpret the current three-hour hotfix ceiling retroactively: either enforce it or explicitly revise its definition during the user-directed setup change. The eight-hour fix49 batch shows the current prose limit is not mechanically enforced.

Evidence: [slot acquisition and late receipt start](../../../../scripts/test-suite.py), [signal forwarding without bounded wait](../../../../scripts/test-suite.py), [worker tool timeout](../../../../scripts/worker-tools.py), [ignored cancellation notification](../../../../scripts/worker-tools.py).

### 3. Use trusted, immutable receipts indexed by exact SHA

At 06:14 the release failed before changing runtime because the successful `ebf0953` receipt existed under the task database's name. The release reads only `test-harness_test_main.json`. Its read-only `--plan` returns before this test, so it cannot currently prove that a candidate is release-ready.

First patch: expose test-receipt readiness as a separate result in release planning, including why evidence is absent or invalid. Configuration/window eligibility and complete release readiness must be distinguishable.

Second patch: controller-created acceptance receipts should be immutable, indexed by run ID and exact candidate SHA in a controller-private directory. A full suite run by the trusted controller may satisfy branch integration and release after main fast-forwards to that identical SHA. Record and validate source before/after, clean status, complete scope, runner/dependency/environment identity, permitted database profile, collected-test inventory/outcomes including skips/xfails, and log integrity. Worker-authored/private-temp receipts remain insufficient.

Keep deployment on clean main. Any changed commit requires new acceptance in this first implementation, including docs-only commits. Do not begin with source-tree-equivalence or cross-SHA receipt reuse: Git-sensitive tests, packaging, and policy/config changes make that a separate design problem. Do not copy or relabel a task receipt into the existing main receipt filename to bypass the validator.

Evidence: [release plan/receipt mismatch in code](../../../../scripts/release-omarchy.py), controller's failed single-receipt assumption (historical local evidence: `.superpowers/sdd/omarchy-loop-2026-09-12/controller-notes.md`).

### 4. Separate live controller state from the frozen release checkout

The checkpoint remained at 00:36 while useful transitions accumulated in ignored ledgers through deployment. Writing tracked state during a pristine suite dirties the candidate; committing it changes the SHA. The current rules therefore put timely recovery records and release acceptance in tension. Bootstrap reads canonical state and journal, then relies on the operator finding newer active ledgers.

Add a controller-private persistent event journal and atomically replaced active snapshot outside the release checkout. This is durable operational storage, not disposable cache. Include task/attempt ownership, queued/running tests, candidate receipts, review rounds, counters, release state, next action, and observation deadlines. Bootstrap reads it with the committed authority and reconciles live processes/receipts before acting. Runtime state records observations; it cannot grant permission or amend policy.

Archive immutable journal segments plus referenced evidence into the repository at unit completion and deliberate pause, and include them in the existing authorized backup workflow. Keep the snapshot recoverable after controller loss; unknown or stale handles never imply completion. A schema-versioned snapshot and append-only events should reconstruct deterministically after partial writes. Avoid adding an untracked file under main during a clean-tree operation.

Evidence: [recovery obligations](../../../../.claude/skills/autopilot/references/recovery.md), [current bootstrap](../../../../.claude/skills/autopilot/scripts/context.py), [state checkpoint](../../../../docs/superpowers/autopilot/state.md).

### 5. Make release readiness, not worker occupancy, the scheduling objective

The loop's 'never idle while work is ready' rule creates more implementations competing for one validation resource. The release was held for fix49, then its fallback time moved. Some of that was reasonable prioritization of memory pressure, but a fixed candidate cutoff would make the tradeoff explicit and predictable.

At each boundary, prioritize due operational obligations and calculate the latest safe candidate-freeze time from the actual game window, expected full-suite time, deployment time, and required immediate verification. Batch only fixes that can be accepted by that cutoff. Once frozen, stop adding changes to the candidate and reserve its validation slot. Other workers may prepare disjoint work without mutating the candidate or flooding its queue. A newly discovered emergency gets an explicit supersession decision and updated deadline forecast.

Start with the existing three-implementer ceiling, but permit fewer active implementations when validation is backed up. The cap is a maximum, not a utilization target. Preserve 6C's deadline priority and the exact game-window rules.

### 6. Keep substantive review; reduce brief contradictions and cosmetic loops

T3 reviews found real queue arithmetic, volume-conservation, timestamp-horizon, and legacy-persistence defects. Fix53 review caught an unintended cross-surface CSS change. Those reviews protected the product and should remain independent.

The task packets themselves are costly and sometimes contradictory: T3's prepared brief is roughly 1,000 lines, while T4 carries a long verbatim plan after an Omarchy override header. Legacy worker commit/direct-test instructions coexist with current read-only Git and approved-tool rules. `phase.md`/`overrides.md` still tell reviewers to commit minor fixes, conflicting with Omarchy's read-only reviewer role.

Use a versioned task contract: goal, exact base, allowed files, producers/consumers, invariants, input/output examples, covering tests, dependencies, and unresolved decisions; link required authoritative sections instead of copying implementation code. Do not impose a token cap that discards necessary context. Require a brief consistency check before dispatch, especially for execution arithmetic and historical-data boundaries.

Reviewers return findings only. Fix blocking issues in one implementer round, then review the behavioral delta. Batch cosmetic-only findings for one cleanup at the next planned acceptance boundary. Assertions are not automatically cosmetic. Route re-review by semantics: queue arithmetic, scientific measurements, persistence, and security deserve the appropriate strong reviewer even if the diff is under 60 lines. Keep current model families during the initial workflow experiment so model changes do not confound the result.

Evidence: [reviewer-write and line-count rules](../../../../.claude/skills/autopilot/references/phase.md), [actual worker boundary](../../../../.claude/skills/autopilot/references/linux-controller.md), T3 contradictory expected case (historical local evidence: `.superpowers/sdd/2026-09-11-phase6b-repair-execution/task-3-review.md`).

### 7. Freeze the acceptance contract before tuning measurement tests

Fix49's new production-shape test evolved from 20 to 8 ticks, gained an RSS allowance of max(5%,16 MiB), and changed the traced-allocation filter. The ledger also corrected a claimed 1.05x peak bound to the implemented 1.25x. The original synthetic test and live acceptance remain, but the new shape test's coverage changed. A retained numeric threshold alone does not prove the measurement is unchanged; the harness-attribution filter has finite traceback depth and can miss allocations beyond it.

Before implementation, distinguish deterministic reproduction, regression acceptance, noisy profiling diagnostics, and production soak acceptance. A change to sample count, attribution, baseline, exclusions, or tolerance gets an explicit contract delta and independent review. Run profile diagnostics in a fresh process and record unfiltered data as well as the asserted metric. A test instrument defect can be corrected; a scientific or production criterion cannot be relaxed by calling it a test fix. These decisions should be resolved before the full-suite slot is consumed repeatedly.

Evidence: fix49 rulings and corrections (historical local evidence: `.superpowers/sdd/hotfix-2026-09-12-omarchy/progress.md`), final measurement report (historical local evidence: `.superpowers/sdd/hotfix-2026-09-12-omarchy/fix-49-report.md`).

## Implementation sequence and file map

| Stage | Concrete changes | Validation |
|---|---|---|
| A: workflow before the next dispatch | Reconcile `SKILL.md`, `references/phase.md`, `hotfix.md`, `overrides.md`, `deploy.md`, `linux-controller.md`: review-clean before full suites; one controller admission path; frozen candidate; reviewer read-only; risk-based re-review; explicit deadlines/ceilings | Static consistency checks and a tabletop replay of T3/fix49; keep existing full-before-merge and exact-main requirements until tooling changes land |
| B: test/release support | `scripts/test-suite.py`, `scripts/release-omarchy.py`, runner/release tests: controller-private immutable receipt registry, exact-SHA reuse, explicit readiness, queued/start/end data | Refuse forged/worker, partial, dirty, stale, wrong-environment, failed, unexpected-skip and superseded evidence; accept a trusted same-SHA fast-forward once; run existing rollback/failure tests |
| C: durable scheduling and recovery | `scripts/worker-tools.py`, new small job-state helper, context/recovery scripts and tests: job IDs, ownership, durable pause, cancellation acknowledgment, event snapshot | Controller restart while queued/running/result-unread, partial-write recovery, stale completion fencing, TERM-resistant owned child cleanup, no duplicate dispatch, lock retained until child exit, worker cannot mint trusted evidence |
| D: optional measured performance work | Pure-test classification, fixture optimization, or bounded extra test lane | Prove pure lane cannot access DB/network/production; compare host/tape latency and teardown behavior before concurrency; avoid blanket parallel pytest |

For Stage B/C, a small tested local supervisor is sufficient; provider integration and a large orchestration platform are unnecessary prerequisites. The earlier [Cerebras handoff](../../../../docs/superpowers/plans/2026-09-12-cerebras-autopilot-implementation-handoff.md) already sketches useful durable job interfaces. Reuse those concepts while evaluating provider changes separately.

## Safe handoff for this run

The production release receipt records `ebf0953` healthy at **06:44:01 CT**. This is deployment success, not completion of the broader verification contract. Release receipt (historical pilot path: `/srv/sports-harness/releases/20260913T114210Z-ebf0953/receipt.json`).

The active controller's 06:44 note records a stop-before-T5 plan: finish release verification, rerun and accept T4, commit the deployment/verification/checkpoint records, produce a stopped handoff, account for workers and commands, and release its controller lock. This review did not send it instructions or change that plan.

The stopping handoff should identify every live worker/test owner, preserve unmerged work, carry review findings and counters, and list future observation windows and calendar duties. Do not wait six or twenty-four hours merely to stop: label soak checks pending with judge-after times. Record which reminders remain and which session-only wakeups will disappear. Do not claim that a reminder launches or resumes a model.

After the stopped handoff, make the chosen changes in an isolated development branch, verify them, reconcile all affected instructions together, and resume from the preserved T4/T5 state. Do not mark the phase or production soak criteria complete just to get a clean restart.

## Success measures for the next comparable run

Record implementation, queue, scoped-test, review, full-suite, deployment and verification time separately. Also record superseded full-suite minutes, identical-candidate repeats, first-review acceptance, blocking findings by round, escaped defects, checkpoint age, receipt rejection reasons, and deadline misses. Token/cost savings cannot be derived from the available receipts.

Initial acceptance goals: zero full suites started with known blocking review findings; zero duplicate full suites for the same trusted candidate/environment; zero lost/duplicated tasks across a pause/restart; current state written after every transition; and no weakened scientific/live acceptance. A 25% reduction in median accepted-change lead time is a useful experimental target, not a forecast. Evaluate several comparable tasks and retain the quality/escape evidence; a small pilot cannot establish equal reliability.
