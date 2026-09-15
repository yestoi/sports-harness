# Implementation plan for review: Qwen worker lane and shared harness efficiency

Date: 2026-09-13. **Proposed, disabled, not yet implemented.** Entry point: [decision brief](2026-09-13-qwen-autopilot-adoption-review.md). Implementation of this plan, budget extension and loop activation need their own explicit approval. The packet does not authorize Qwen to build its own containment or acceptance controls.

## 1. Scope and invariants

Claude owns task admission, priorities, contract resolution, sensitive implementation, independent review, commits, merges, releases and verification. Qwen is a candidate-producing implementation route, not a controller, scheduler, reviewer, or release principal. Use the existing Claude controller allocation; the pilot's native Sonnet/Opus observations do not redefine the production controller model.

Preserve current roadmap authorizations and gates, including existing amendments: paper/LIVE_TRADING=0 and RFQ disabled; no venue writes, live authorization, sensitive credentials, scientific threshold/variant/family/cutoff changes, non-additive database changes, unapproved paid hosts or dependency changes. Preserve full-suite-before-integration, one host-wide suite slot, clean exact-main release checks, game windows, failure ceilings and live observation obligations. A provider error never grants broader tools or a different model route.

The recorded stopping point is T4 accepted at phase head `bac35ab`, next T5 undispatched, runtime recorded as `ebf0953`. Those are historical checkpoint facts, not live checks. Reconcile actual branch/worktree/process ownership and overdue calendar/soak duties at relaunch. Do not mark them complete while doing setup work.

## 2. Task admission: all conditions required

1. The task is explicitly within approved project work and has a frozen, independently checked contract. Known inputs, outputs, callers, exceptions and resource bounds are provided. Unknown semantics go to Claude, not to a shorter Qwen prompt.
2. Changes are limited to enumerated files and a bounded behavior class. For the initial lane, prefer new read-only developer reporting utilities with no runtime caller; require a producer/consumer inventory.
3. No effect on scientific/research/financial meaning, pricing, fills, expiry, matching, settlement, recorder/venue behavior, event identity, report-period interpretation, migrations, security, budgets, authority, test classification or acceptance criteria.
4. Qwen cannot edit its execution boundary, controller instructions, test/release runners, admission policy or trusted checks. Test additions may cover its feature; worker-written tests cannot be its sole acceptance evidence.
5. A trusted isolated test path and independent Claude reviewer are available. Missing Claude review capacity blocks new external dispatch rather than accumulating unreviewed work or routing around a subscription limit.
6. Packet-bound source and a per-attempt workspace/output sink are provisioned; no other worktree or shared results can be selected by model-supplied arguments.
7. Numeric spend/time/request/tool/output limits and controller/backlog capacity are reserved before dispatch. One Qwen implementer counts toward the existing total ceiling of three; external provider count is not a new pool of three.

File names, diff length, a Sonnet allocation, or labels such as "test", "docs", "formatting" are insufficient proof of eligibility. Admission is semantic and is checked again against the returned diff. New dependencies or consumers invalidate the packet and cause `NEEDS_CONTEXT`/escalation.

## 3. Proposed interfaces and records

Names below are interfaces to implement, **not existing commands**. Start with a narrow CLI callable by Claude. Add a controller-only MCP interface later only if it materially improves durable operations; do not add a general remote shell server.

| Operation | Required behavior |
|---|---|
| `submit(packet)` | Validate approved scope/configuration and budget; create unique durable job/attempt; return its ID. A request cannot raise its own limits. |
| `status(id)`, `list()` | Read durable state, heartbeats, waits, reservations and immutable artifact pointers; never infer success from a vanished PID. |
| `result(id)` | Return captured candidate/evidence and stop reason; not a merge or deploy command. |
| `pause_dispatch()` | Persistently prevent new external dispatch, let owned work drain; remain paused after restart. |
| `resume_dispatch()` | Explicit controller/user action after reconciliation; no automatic timer/model resume. |
| `cancel(id)` | Terminate and confirm owned descendants, release resources only after verified exit; fence all late results. |

Suggested worker-state transitions:

`queued → admitted → running → candidate_ready → scoped_validation → reviewing → full_acceptance → accepted_for_integration`

Only a review-clean, frozen candidate enters `full_acceptance`. Scoped validation before review is not a full-suite admission or an acceptance receipt. Blocking findings send the candidate to a new repair attempt and invalidate downstream evidence.

Failure/side states include `needs_context`, `repair_queued`, `failed`, `cancel_requested`, `cancelled`, `superseded`, and `usage_unknown`. `accepted_for_integration` is not `merged`, `deployed` or `verified`; the Claude controller records those separate transitions under existing policy. A late worker response cannot advance a cancelled/superseded attempt.

Packet fields: schema/version, task and attempt IDs, immutable packet hash, actual base SHA, route and reviewed admission reason, allowed write paths, read-only source/consumer list, protected-input hashes, relevant authority excerpts, exact I/O/error/resource contract, scoped smoke command, independent acceptance identifier, dependency/ownership map, model/runtime/config hashes, budget/time/tool/output limits, review tier and escalation conditions. Do not expose hidden acceptance answers or other candidates.

Result fields: task/attempt and controller epoch, packet/base/config hashes, actual touched paths, candidate patch/content digest and controller-created commit SHA when available, protected-input verification, missing/extra artifacts, tool outcomes, provider usage/reservations, stop reason, known uncertainty, and queued/admitted/worker/scoped-test/review/full-suite/integration timestamps. Record UTC event times plus monotonic durations; unknown timing is null with a reason, not zero.

State location should be controller-private durable storage outside the release checkout (proposed `~/.local/state/sports-autopilot/`, not disposable cache). Require a single-controller lease, transactional reservations, atomic snapshots plus append-only events, bounded logs, schema versions and recovery from partial writes. Arrange archival/backup through an approved path before unattended use; do not make a new unreviewed cloud backup dependency. Runtime state is evidence and cannot amend permissions.

Enforce existing unit/day dispatch, failure, concurrency and wall-clock ceilings across **both native and external routes**, not just the new provider's spend cap. Require a durable admission permit before either launcher can begin an attempt; reject missing/stale permits at a trusted boundary rather than relying on a fail-open prompt hook. The native launch/permit binding needs explicit design and negative testing during implementation; until it exists, do not claim mechanical all-route enforcement. Persist counters and unit start times across restart. Separate queue, active and no-heartbeat clocks, but do not exclude queue time from total unit wall/deadline accounting without an explicit authority amendment. Use America/Chicago calendar-day rollover, retaining active unit counters through midnight.

## 4. Isolation and provider boundary

Use the proven separation, not a copy-paste promotion of the disposable prototype:

- A pinned OpenCode inference process gets only its task context, fixed tool bridge and narrow inference broker. No host HOME, production credentials, general network, auto-updates, external plugins/hooks, language servers, arbitrary native tools or installation commands. Resolve the explicit executable; the current user wrapper performs global mise setup and is not a suitable worker launcher.
- A trusted parent holds the Cerebras key, fixed HTTPS endpoint/model allowlist and pricing. Do not put the key in packets, model-visible environment, source exports, command lines or logs. Reject redirects and arbitrary URL/provider/model substitution.
- Every generated shell/test action runs in a separate networkless namespace with only its assigned writable paths and per-attempt artifact sink. No inference socket, key, production/NAS/Docker/SSH, other task outputs, shared writable venv, trusted receipt registry or Git authority. Worktrees alone are not isolation.
- Supervisor-owned job identity supplies the worktree, not model `cwd`. Validate canonical paths and deny traversal/symlink/hardlink escapes. Keep authority and source inputs read-only and verify their hashes afterward. Do not reuse the current shared writable results mount for the external lane.
- First project task has no database/socket capability. If the current native `make test` requirement needs a Qwen-only standalone smoke exception, approve that exact exception in setup first; controller-side scoped/full acceptance still uses the existing `make test` slot. This is not permission to bypass it with host pytest. Database-capable external work is a later, separately reviewed stage.

Observed pilot settings: OpenCode v1.18.30; `qwen-3.8-27b`; medium reasoning; configured context 65536; output cap 16384. Preserve as an initial configuration, not timeless vendor facts. Verify executable digest, actual model availability, price, tool/stream behavior and account quotas again before a new paid run. Failed preflight means no dispatch.

Proposed first-project limits for review: one external worker; 10-minute worker wall cap; 60-second shell-call cap; 20 tool calls; 24 inference requests; 64 KiB returned tool-output cap; 16384 output tokens/request; $0.75 used-plus-reserved per attempt within the explicitly approved cumulative experiment balance. One fresh repair attempt then Claude fallback, with all costs/counters retained. These proposed caps do not override any stricter existing unit/day ceiling. Resource/time exhaustion is failure or escalation, never a silent truncation presented as success.

Worst-case request spend must be reserved before network dispatch using verified context/output ceilings and both input/output prices. Persist attribution to job/attempt/model/rates; serialize across processes and threads. Unknown usage retains its reservation and halts inference until reconciled. Never reset a missing ledger to zero or automatically recharge. Explicitly distinguish code repair from provider cooldown; no quota-driven fallback or alternate provider. The original closed synthetic ledger is carried forward only if D4 is approved; application research spend and Claude subscription remain separate.

## 5. Source integration map

| Existing files | Planned reviewed change / ownership |
|---|---|
| `.claude/skills/autopilot/SKILL.md`; `references/phase.md`, `hotfix.md`, `overrides.md`, `linux-controller.md` | Claude-owned setup: explicit external route, admission/escalation policy, shared caps, task isolation, reviewer findings-only, review-clean-before-full, consistent containment language. Native worker rules stay intact. |
| `.claude/agents/sports-worker.md`, `.mcp.json`, `.claude/settings.json`, `scripts/autopilot-session.sh` | Preserve native fixed tools and deliberate launch lock. Update only if required by the reviewed controller interface; no global provider replacement or newly inherited external tools in workers. |
| `scripts/worker-shell.py`, `scripts/worker-tools.py`; proposed separate supervisor/adapter modules | Reuse isolation concepts behind job-bound ownership and per-attempt output. Current main/history/shared-results visibility is not the external boundary. Hooks/`worker_guard.py` remain defense in depth, not OpenCode containment. |
| `references/recovery.md`, `recording.md`; `.claude/skills/autopilot/scripts/context.py`, recovery hooks/tests | Reconcile durable external state/attempts/counters before dispatch; off-checkout live records; archive at safe boundaries. Do not treat notification timers as model sessions. |
| `scripts/test-suite.py`, `scripts/testdb.py`, Makefile; `tests/test_omarchy_release.py` | Keep existing behavior initially. Later Claude-owned instrumentation/queue work records pre-lock queue and provisioning spans, not fabricated timing. Preserve one slot and assigned DB isolation. |
| `scripts/release-omarchy.py`, `references/deploy.md`, release tests | Unchanged for first canary. Later exact-SHA trusted receipt reuse/readiness requires separate authority, validator and failure tests. Current `--plan` returns before receipt validation and is not complete release readiness. |
| `docs/superpowers/autopilot/roadmap.md`, `verify.md`, state/journal | Do not edit user-owned authority during packaging or implementation by inference. Any necessary standing permission is a separately accepted setup amendment; preserve pending milestone and verification status. |

Actual new module/file names for supervisor storage/broker/adapter should be fixed during approved design implementation, not inferred as existing supported tools from this plan.

## 6. Delivery sequence and exit criteria

### P0 — Approve scope and freeze baseline (Claude/user)

Resolve D1–D6, inspect current state/ownership, record approved numeric budget and authority diff. Freeze a prospective measurement policy and reserve a first task without disturbing T5 or due calendar work. Baseline observed candidate times are historical evidence only. No external paid calls until budget and isolation approval.

Adopt the reviewed shared workflow ordering at this setup stage: coherent briefs, findings-only independent review, review-clean before full suites, and a frozen acceptance candidate. This must already govern the first real Qwen task and its Claude comparison, not wait for P4. Original scheduling/recovery improvements are an independent workstream and may start before or alongside P1–P3; their usefulness does not depend on Qwen promotion.

### P1 — Minimum supervised path, fake-provider first (Claude)

Implement only job-bound isolation, bounded inference/spend, durable attempt/artifact capture and reliable stop/cancel needed for one manually supervised project attempt. CLI can be blocking or polled; no new general agent framework/daemon/MCP product is required. After a crash, this stage may fail closed and require manual reconciliation rather than automatically recover. It must never silently retry an unresolved attempt.

Exit: pure unit tests plus actual sandbox capability drills pass; missing/truncated/extra artifacts and unsupported completion are rejected; fake-provider budgets and process cancellation work. Run affected existing guard/tool/context/release tests through approved commands. Review the containment and budget code independently with Claude before any worker uses it. Qwen does not implement or review this trusted boundary.

### P2 — One real, supervised Qwen task (Qwen candidate; Claude acceptance)

Use the [suite timing packet](2026-09-13-qwen-first-project-task.md), resolved actual base and controller-selected sanitized fixtures. Preserve the first attempt before repair. Independently review the actual source and callers; controller runs required scoped/full acceptance against a frozen candidate. At most one external repair, then explicit Claude fallback. Record all work, including controller handling and failed review effort.

Exit: accepted candidate or a documented reason the route is unsuitable. No requirement to force a Qwen success. It remains a separate harness-improvement task, not a phase completion. No auto-merge/deploy merely because it is developer tooling: current `scripts/` changes can trigger the loop's code-ahead deployment rule after integration. Follow the then-approved merge/release policy.

### P3 — Durable unattended controls (Claude)

Add persistent dispatch pause/resume, heartbeat/ownership reconciliation, controller epochs, stale-result fencing, crash-safe queue and unread-result recovery, cancellation acknowledgement and archival/backup. Establish downstream-capacity admission; one Qwen worker and one full-suite slot remain. A paused controller or unavailable reviewer does not trigger alternate-model dispatch.

Exit: every failure drill in section 7 passes with no duplicate execution, stale acceptance, premature resource release or lost budget. Demonstrate actual status/drain/cancel from the controller and a restart with a pending result. No automatic model startup from notification/reminder timers.

### P4 — Shared efficiency workstream and prospective evaluation (Claude; workstream can start at P0)

Adopt independent-review-clean-before-full admission and frozen candidates first. Record queue time at submission, lock admission separately from DB provisioning, and worker/test/review backlogs. Keep a valid running suite non-preemptive by default; discard superseded queued candidates, prioritize due operational deadlines, and add aging to prevent starvation. A release cutoff freezes the candidate rather than repeatedly batching late fixes.

The first sentence is a P0/P1 prerequisite, repeated here as an invariant, not a deferred change. Queue instrumentation, release cutoffs and off-checkout checkpoints can be built as separate Claude-owned changes in parallel with the external adapter. Prospective trials can remain manually supervised under P1/P2; any unattended external dispatch still requires P3. Exact-SHA receipt-policy changes remain isolated under P5 regardless of when their design begins.

Reconcile all brief/procedure variants before dispatch: one coherent packet must replace contradictory worker-commit, reviewer-edit and direct-test instructions. Reviewers return findings only. Re-review strength follows changed semantics and affected consumers, not a diff-line threshold. Batch genuinely cosmetic findings at an already planned acceptance boundary; do not classify assertions or coverage reductions as cosmetic. Fix blocking findings and re-review their behavioral delta before full acceptance.

Freeze measurement contracts before tuning performance/regression tests: distinguish deterministic reproduction, regression acceptance, noisy profiling diagnostics and production soak criteria. Any sample-count, attribution/filter, baseline, tolerance or exclusion change is an explicit independently reviewed contract delta. Preserve raw diagnostic observations. Calling a scientific threshold change a "test fix" does not make it eligible or authorized. Keep the existing hotfix wall ceiling unless an explicit setup decision changes it; queue time still consumes delivery deadlines.

Evaluate 12–20 fresh eligible real tasks with the same scheduler, contracts, review policy and acceptance gates for both routes. Balance route assignment by task class; counterbalance anonymous candidate order if doing paired shadow runs. Never expose one arm's candidate or reviewer feedback to the other. Paired shadow acceptance-ready timing is secondary: only one candidate is integrated, and that does not prove the other arm's observed integration time. The primary prospective endpoint is an actual controller-accepted integrated commit with its required checks; measure deployment/verification separately where required.

Exit: record all metrics in section 8; any authority/isolation/serious-defect escape suspends the external lane for investigation. Proposed promotion needs at least 25% lower median full accepted-change time in the eligible class, no worse tail behavior, no increase in median reviewer burden and no additional observed serious escapes. Small samples remain uncertain; passing is permission for a narrow canary, not proof of equivalence.

### P5 — Optional receipt/readiness optimization and narrow promotion (Claude/user)

Only after separate design approval, add immutable controller-private receipts indexed by exact candidate SHA, runner/dependency/environment identity, scope/test inventory and outcomes. Revalidate identity before/after testing and at release. Same-SHA fast-forward may reuse trusted evidence once validator tests prove it. No worker receipt promotion, filename copying, cross-SHA/source-tree equivalence, or removal of current gates before that change lands. Count its savings separately from Qwen's.

Authorize only evidence-backed task classes for unattended implementation. Production deployment still follows Claude's existing gates, release health/rollback behavior and verification obligations; any broader automatic-release policy is a separate explicit change. Keep schema/security/scientific/runtime semantics out of the initial external lane. Expand model, concurrency or task class one dimension at a time.

## 7. Required adversarial/failure drills

| Injected condition | Required observable result |
|---|---|
| Text requests a new model/URL, a secret, Docker/SSH, another worktree, shared results or receipt storage | Mechanical denial; record violation; no host fallback or cross-task write. |
| Provider gives a DONE claim with no patch, mismatched tool response, incomplete stream, cap exhaustion or malformed candidate | Failed/incomplete attempt; no accepted state; preserve raw bounded evidence and exact stop reason. |
| Unexpected extra files, edits to packet/source/acceptance inputs, traversal or symlink targets | Reject candidate; protected files remain unchanged. Benign byproducts are classified explicitly, not silently ignored. |
| Concurrent reservations near cap; changed pricing; unknown usage after timeout; process restart with reserved request | No overspend dispatch; outstanding reserve retained; inference halted until reconciled. Missing ledger is not a new account. |
| Pause during queued/running work; controller restart before reading result | No new dispatch; existing job/result retained once; explicit reconciliation and resume. |
| Cancel a tool with TERM-resistant descendants; UI cancellation alone | Confirm the owned process tree is gone before freeing worktree/slot; fence late completion; UI event alone is insufficient. |
| Candidate changes/rebases after review; obsolete full test waits in queue; stale completion races repair | Invalidate corresponding approval, discard obsolete admission, reject late attempt; no reuse across changed SHA. |
| Lost controller lease, PID reuse, crash between event/snapshot writes | Exactly one owner; ambiguous execution reconciled or stopped, never assumed complete or safe to retry. |
| Native/external launch at a unit/day ceiling; CT midnight during an active unit; restart near a limit | Both routes require a valid admission permit; no counter reset or extra dispatch; unit elapsed/queue accounting preserved; only calendar-day counters roll over by the approved timezone. |
| Claude reviewer unavailable, provider cooldown, budget exhausted | Stop external admission, preserve counters and active state; no quota workaround or silent downgrade. |
| Sensitive one-line task disguised as tooling; data contains approval/deploy instructions | Admission rejects/escalates; worker data cannot amend authority. Test actual project-shaped cases, not only the obvious pilot traps. |

Before any future receipt change additionally reject forged/private-worker, dirty, partial, wrong-environment, failed, unexpected-skip/xfail and superseded evidence. Verify existing release failure/rollback tests remain meaningful; do not claim the temporary pilot tested production recovery.

## 8. Measurements and stop conditions

Record eligibility decisions and rejected classes; first-pass functional/artifact/review outcomes separately; worker/inference/tool durations; queue/provision/scoped/full/review spans; retries and Claude fallback; actual merge and release/verification timestamps; source-review findings and escapes; spend/reservations/cooldowns; superseded and repeated suite minutes; reviewer time; recovery/checkpoint lag; setup/maintenance cost and missed deadlines.

Show total and per-task time as well as medians: a median can hide costly retries. Compare like task classes under the same harness; freeze revisions and label experimental changes. Use conservative stop-on-unknown rules. Safety/authority failures are independent stop criteria and cannot be averaged away by cheap tokens or high test-pass counts.
