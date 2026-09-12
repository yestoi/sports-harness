# Resuming the sports development loop on Omarchy

Assessment: September 12, 2026, approximately 18:10–18:15 America/Chicago
(23:10–23:15 UTC). Controller checkout inspected: Mac `main` at `2a1061c`.
The user selected: **“Run the controller, development, and tests on Omarchy.”**

The recommended next unit is a bounded controller-recovery/setup change, followed by
the outstanding hotfix reviews and Phase 6 correctness work. The runtime migration
has succeeded; the autonomous development loop has not yet been migrated completely.
Retain the existing implementation/review structure, recover its unfinished work,
and measure it on the new host before increasing concurrency or changing providers.

This document records an evaluation and a proposed work sequence. No application
patch, merge, deployment, service restart, gate resolution, or controller launch was
performed during this assessment. Existing dirty files were preserved.

## Evidence and present condition

Read the [migration checkpoint](../../runbooks/nas-to-omarchy-progress.md),
[operations runbook](../../runbooks/omarchy-operations.md),
[autopilot state](../autopilot/state.md), journal entries 128 and 136–150,
active Mac ledgers, outstanding reports/reviews, Git refs/worktrees, and the latest
Claude controller transcript. Historical process findings were cross-checked against
the [earlier session investigation](2026-09-12-cerebras-autopilot-investigation.md)
and [Phase 6 reconciliation](2026-09-11-phase6-roadmap/RECONCILIATION.md).
Remote checks used SSH, status/health endpoints, metadata and bounded read-only SQL.

| Item | Observed state | What it establishes |
|---|---|---|
| Production | `trey@192.168.12.127:/srv/sports-harness`; all seven services up; PostgreSQL, executor and dashboard healthy | The runtime is operating on Omarchy |
| Deployed source/schema | `/healthz` build `b0a3991`; `0006_quotes_run_index` | Pending local code did not ship with the host move |
| Host | i9-10900K, 20 logical CPUs, 31,904 MiB RAM; approximately 22,902 MiB available at 23:10Z | Substantial headroom compared with the old 8 GB machines |
| Storage | 952 GiB runtime filesystem, approximately 865 GiB available | Capacity exists; retention/offload remains separate work |
| Tape | Event `98889257` at `23:12:25.364Z`, sampled almost immediately afterward | Recording has resumed |
| Executor | Latest loop 3,448 ms; trailing p95 19,953 ms; zero open orders; no last error at 23:12Z | Latest latency improved; p95 includes startup/restore load and is not steady-state acceptance |
| Recorder | Observer: 116.8 MiB at 22:11Z, 3.265 GiB at 23:11Z | The application memory follow-up remains necessary; these are Docker memory measurements, not a fresh heap diagnosis |
| Pricing | Completed runs 13064–13066 took 143, 159 and 141 seconds; all had zero gaps/candidates, despite nonempty variant order and `budget_exhausted=false` | The old empty-order symptom is not universal on Omarchy, but useful evaluation is still absent in these samples; determine the actual missing/freshness population before declaring fix 48 sufficient |
| Reports | Latest provisional report 9: ISO 2026-W37, generated September 10 at 10:25:41Z | Reporting was still about 61 hours stale at this check; the first post-cutover settle job was running |
| Observation | 13 five-minute samples, 22:11–23:11Z; all health statuses `ok`; observer PID 53015 was live | Only about one hour of the six-hour observation was available |
| Retired NAS | Retirement marker present; all seven old runtime containers exited | Source writers remain stopped |

The migration checkpoint records the accepted **65m36.205s tape gap**, preserved full
source cluster, and successful fresh encrypted nightly restoration with 58 table-count
comparisons. Those are migration evidence, not proof that execution semantics or the
prospective experiment are now valid. Sustained busy-window acceptance, reboot
recovery, and the corrected-workload benchmark remain outstanding. LUKS still needs
console unlock following a reboot.

## Recoverable work: exact status

Mac main and Omarchy main both name `2a1061c`. Mac task worktrees were clean at
inspection. Omarchy has only a local `main` branch/worktree, but its bundle import
preserved the other heads under `refs/remotes/migration-bundle/*`; the commit objects
are present. Recover those refs locally rather than implementing their work again.

| Work | Preserved head/status | Next action after setup and applicable gate resolution |
|---|---|---|
| Fix 47: settle/markout/WTD scheduling | Merged to main through `b9c0cdd`, reviewed, undeployed | Test the final integration candidate on Omarchy and ship through the new release path; verify stage timings, markouts and a fresh provisional report |
| Fixes 48+49: pricing priority and recorder memory | `fix-48-pricing-stage-order` at `b447827`; six commits, clean worktree; implementer reported 3,122 passed/6 xfailed | Independent pricing/recorder review, resolve findings, integrate and verify; do not label this approved |
| Fix 45: partitioned raw-response lookup index | `fix-45-raw-events-index` at `003375e`; Critical remains after repair wave 2 | Resolve gate 11, then bounded round 3 and independent review, with interrupted/retry/repair-only migration cases |
| Phase 6B | `phase6b-repair-execution` at `2d0fd71`; T10, T1, T2 accepted on phase branch; not merged to main | Preserve completed tasks; T3 currently depends on fix 45/0007 and introduces 0008; reconcile the phase base before continuing |
| Phase 6C | `6c11df3` already merged; its code is in deployed `b0a3991` | Complete Sunday/Monday runtime acceptance and remaining funnel disposition; retain partial status |
| Fix 52: weather refetch storm | Recorded finding; no completed implementation established | Prioritize before Sunday 01:00 CT quiet hours; the existing dirty weather test change only freezes time and does not fix this defect |
| Fix 51: integrity checks skip on timeout | Recorded finding; no completed implementation established | Reassess on Omarchy; ensure actual pass/fail evidence without loosening the check |
| Fix 50: WS sink timeout storm | Conditional follow-up after load reduction | Observe the new host/build; open implementation if it recurs, retaining gap evidence |
| Fix 46 / Phase 6D | RFQ budgeting and evaluation coverage not delivered | Keep listener off; plan instrumentation and workload budgets, then evaluate declared policy after 6B |

Only five non-document/application-test files differ between deployed `b0a3991` and
committed main: the three settlement modules plus their two test files (fix 47).
The migration tooling and two weather-test time freezes are additional uncommitted
changes and require separate review/disposition before a clean release stamp.

**Missing recovery material on Omarchy:** `.superpowers/sdd/`, including active
ledgers, briefs, review packages, detailed reports and test evidence. It is ignored
by Git and absent from the development checkout. Copy it additively from the Mac,
verify a file/hash manifest, and retain the originals. Do not copy Mac `.venv`, test
database files, Git administrative worktree links, credentials, or old subprocess
handles as though they were runnable Linux state. Existing absolute paths in reports
remain provenance; new briefs should use Omarchy paths or an explicit path map.

The separate `review/autopilot-context-recovery` worktree at `816d80e` is clean and
user-owned; its adopted changes are already on main. Keep it outside recovery cleanup.

## What the earlier loops teach

1. **Reviews caught consequential defects; preserve them.** Fix 45's repeated
   Criticals concern invalid partition-index recovery. Phase 6B review caught the
   REST-anchor probe and initial-load continuity issues. Phase 6C review caught
   cross-variant report population and contract-side mistakes. More host capacity
   does not correct these semantics.
2. **Status claims drifted beyond evidence.** State still carries an old controller
   SHA, active-worker claims and consumed wakeups, although the journal reaches 150.
   Later messages call 48+49 “reviewed.” The hotfix ledger explicitly withheld its
   review dispatch; no review report was found, and the latest controller transcript
   has the two implementation dispatches but no 48+49 reviewer dispatch. Treat it as
   implemented/tested, awaiting review. Preserve old records and append the correction.
3. **Independent databases did not isolate shared test resources.** Ledgers record
   competing full suites, database-drop/checkpoint stalls, misleading timeout
   diagnoses, and workers waiting for already-finished or killed background runs.
   The 6B ledger also records four overlapping implementers despite a ceiling of
   three. An actual shared test-slot lock and clear process ownership are more useful
   than another instruction to inspect `ps`.
4. **Operational containment became repeated disruption.** Journal 150 records ten
   recorder restarts by 13:41 CT. The hotfix ledger explains how restarts postponed
   the hourly settler. Do not transplant the NAS restart thresholds onto Omarchy:
   today's recorder already exceeds them while the host has ample free memory.
5. **Specification and environment failures need distinct diagnoses.** The Phase 6
   reconciliation corrected several earlier claims about queue fills, cadence and
   gate feasibility. Today's zero-gap runs no longer match the old empty-order
   symptom. Reproduce current failure mechanisms and review real consumers, instead
   of assuming all previous diagnoses still describe the new host.

The prior session audit found large controller/worker contexts and mixed implementation,
review and operational time. It did not establish inference as the dominant critical
path. Keep the current model/reviewer policy for the recovery baseline. The Cerebras
and GPU handoffs remain separate experiments after useful measurement is restored.

## Concrete setup change before launching the controller

| Surface | Required adaptation | Acceptance evidence |
|---|---|---|
| Controller ownership | `/home/trey/dev/sports` becomes the sole active controller checkout; Mac preserved as source/history | No second sports controller; reconcile actual processes and timers; fresh session identity and durable checkpoint |
| Branches/evidence | Create local branches/worktrees from the preserved bundle heads; restore ignored SDD material | Matching heads and file hashes, clean task worktrees, no lost findings or retry counts |
| Runtime routing | Centralize production host, runtime path and `./sports-compose` selection | Every operational command resolves to `/srv/sports-harness`; retired NAS app writes remain rejected |
| Release tooling | Implement reviewed full/app-only Omarchy targets using exact source/image stamps and the existing override | Preserve effective env, PostgreSQL pin and RFQ-off setting; cover all changed services; backup gate, migration-failure recovery and prior-image rollback rehearsal |
| Preflight/verification | Port `scripts/preflight.sh`, skill Orient/deploy/operate/verify references and applicable runbook commands | Linux checks, correct DB/host, paper posture, current game/job schedule, matching deployed stamp; no hardcoded NAS operational access |
| Host policy | Replace obsolete `/volume1`, 2 TB and Mac-memory assumptions with measured Omarchy paths/budgets | Distinguish runtime 600 GB alert budget from filesystem free-space gate; keep destructive retention separately controlled |
| Test scheduling | Keep PG16.15 test container at loopback 5433, independent volume and current 4 GB/4 CPU bounds; begin with one full suite at a time | One shared suite slot, branch/DB/process ownership, exact candidate SHA and terminal exit status; benchmark before allowing a second suite |
| Linux controller facilities | Check provider login, installed skills/tools, notifications, browser access and persistent scheduling | Replace Mac `caffeinate`/`osascript` assumptions; test one bounded wakeup/recovery drill; preserve deadlines through disconnects |
| Worker access | Extend “no NAS access” to production on the same machine | Workers/tests must not gain `/srv` credentials, production DB access or Docker control merely by running locally; choose an enforceable boundary as part of setup |
| Git/recovery backups | Preserve origin policy and an additive off-host bundle destination on the NAS | Git authentication checked separately; no copying production or Mac recovery credentials to workers; bundles remain recoverable |

`scripts/omarchy.sh` and the new Make targets already provide status, SQL, preflight,
tunnel and summary verification. **They do not provide a release procedure.** Simply
changing `.env.nas` would invoke recipes that overwrite runtime env from `deploy/nas.env`
and fail to advance the pinned Omarchy service images correctly.

Claude is installed on Omarchy, but this assessment did not validate provider login,
all required skills/browser integrations, or durable scheduler behavior. The migration
full suite had two weather time-dependent failures; both tests were corrected locally
and the targeted weather rerun passed 15 tests. A pristine full-suite pass of the
final integrated recovery/application candidate remains required before deployment.

Do not retain worker handles or wakeup IDs as active merely because state lists them.
The latest transcript contains 18 unique Agent dispatches, while state carries a
day total of 19; the earlier session boundary must be included when reconciling that
counter. Preserve 19 conservatively until reconciliation is complete, not zero.
No sports Claude/test worker was established as running during the process check;
the lone Mac `claude` PID inspected had cwd `~/.claude/chrome`.

## Work order after recovery

1. **Finish setup and resolve the recorded stop.** Produce one reviewed routing/release
   setup change and reconciled checkpoint. Preserve gate 11 on fix 45; recommend
   authorizing bounded round 3 and allowing independent 47/48/49/51/52 work to proceed.
   The evaluation/location choice is not itself that gate answer.
2. **Restore useful collection and reporting.** Review 48+49, implement weather fix 52,
   and prepare a coherent app-only release with already-merged 47 as readiness allows.
   Do not make the unrelated fix 45 repair a release prerequisite for these fixes.
   Verify actual pricing coverage and missingness, memory over six hours, settle-stage
   progress and report freshness. A nonempty `order` array alone is insufficient.
3. **Protect the dated 6C work.** Weather quiet hours begin Sunday September 13 at
   01:00 CT. Week-key acceptance is due Sunday at 19:00 CT, and the diagnostic report
   before Monday September 14 at 09:00 CT. Recompute the latest feasible delivery
   window from current games/jobs and remaining review/test/deploy time. If the
   release cannot land in time, prepare the correct-period diagnostic report and
   affected-surface labels; keep the milestone partial.
4. **Complete the correctness path.** Fix 45 review/migration validation enables
   6B T3; continue recovery anchoring, liquidity conservation, expiry/rejection,
   dirty-time scope, capacity-equivalent replay and the order 157 audit. Retain
   accepted T1/T2/T10 work and exact evidence.
5. **Adapt 6D and 6E to measured Omarchy behavior.** Plan evaluation coverage and
   workload isolation alongside 6B where dependencies permit. Reconcile 6E's
   completed host choice/cutover and backup drill rather than repeating a Mac-mini
   selection exercise. Finish sustained acceptance and the benchmark on corrected
   6B workload before marking the environment accepted.
6. **Establish a valid prospective period through 6F.** Record the migration gap
   separately from the eventual corrected-code/eligibility boundary. Preserve old
   observations; the new machine does not start a clean experiment automatically.
   Dates and eligibility changes still need their dated decisions. GPU research,
   provider experiments, PG18 and additional strategies follow their own plans.

The September 11 user decision in journal 128 allowed qualifying app-only deployments
during Thursday–Saturday college windows, preserving full-deploy and NFL restrictions.
The migration's live-game waiver was explicitly migration-only. Neither is a reason
to use the retired NAS recipe on Omarchy. Carry the intended app-only service/tape
semantics into the reviewed target and resolve the stale canonical R4 wording during
setup; do not silently extend the migration waiver.

## Ready-to-use instruction for the next setup session

> Prepare the existing sports autopilot to run its controller, development and tests
> on Omarchy. Read this assessment and the migration operations/checkpoint documents.
> Recover local branches from `migration-bundle/*`, copy and hash-check the Mac's
> ignored SDD ledgers/evidence, preserve the source worktrees, and reconcile exact
> task/review status and counters. Implement and review Omarchy routing, full/app-only
> release tooling, Linux preflight/notifications/scheduling and a shared test-suite
> slot. Preserve the live paper runtime configuration, pinned PostgreSQL, RFQ-off,
> NAS retirement and backup boundaries. Prepare a clean, reviewable setup handoff
> with the unresolved fix-45 gate explicit and 48+49 awaiting independent review.
> Complete setup before the autonomous loop is launched.

The governing recovery procedure explicitly states: **“A setup/review session does
not become a controller.”** See
[recovery.md](../../../.claude/skills/autopilot/references/recovery.md).
The remaining stop is explicit in
[gate 11](../../../.claude/skills/autopilot/SKILL.md): **“A Critical open after the
second fix wave, or parked anywhere (the task breaker included).”** Its concrete
scope and options are preserved in the
[stopped report](../autopilot/reports/2026-09-12-stopped-0550.md).
