# Implementation handoff: Claude Remote Control with Cerebras workers

Date: 2026-09-12. Status: proposed implementation for later; no configuration or running-loop changes made.

## Decision and goal

Keep Claude Code as the controller and remote interface. Add external Cerebras workers for bounded implementation tasks, retaining strong Claude review and existing acceptance gates. Use Astra to prepare decisions, contracts and task packets for the next ready wave of work. Start with the current Fable 5.1 controller; evaluate a different controller separately after measuring the worker change.

The goal is the same quality with less elapsed time to an accepted change. Faster token generation is only useful if planning, tool execution, review, repairs and integration do not consume the savings. Moving to a more capable personal computer should provide room for local workers and tests, but measure the actual test/database bottlenecks before raising concurrency.

This handoff supplements the [investigation](../reviews/2026-09-12-cerebras-autopilot-investigation.md) and [adversarial review](../reviews/2026-09-12-cerebras-autopilot-adversarial-review.md). Those documents contain historical evidence, provider research and evaluation caveats.

## Architecture

```text
User in Claude mobile app or browser
                 ↕ Remote Control
Claude Code controller on the personal computer
                 ↓ local CLI or MCP tools
Local supervisor: jobs, limits, evidence and recovery
                 ├── OpenCode → Cerebras implementation workers
                 └── Claude workers/reviewers
                     (native subagents initially; Agent SDK optional)
```

Claude Code owns prioritization, escalation, review coordination and integration. The supervisor enforces resource ceilings and durable job transitions. Workers receive scoped task packets and produce candidates and evidence; they do not own the controller's shared state or merge authority.

The Agent SDK is optional in this architecture. Use it later if programmable Claude worker sessions materially simplify the supervisor. It is not necessary just to call Cerebras; OpenCode supplies that worker runtime.

### Remote Control boundary

The documented Remote Control workflow connects Claude's web/mobile interface to a local Claude Code session. Start it from the project directory:

```bash
cd /Users/trey/dev/sports
claude --remote-control "Sports autopilot"
```

Alternatively, run `/rc` inside an existing interactive Claude Code session. Use the corresponding project path after moving machines. Remote Control currently requires an eligible Claude subscription login; API-key authentication and custom API gateways are not supported for that session. Separate workers use their own provider authentication. See [Anthropic's Remote Control documentation](https://code.claude.com/docs/en/remote-control).

The documentation mentions hosts built on the Agent SDK, but this research did not establish a documented public SDK switch that gives a standalone SDK controller the same remote interface. Recheck before implementation if considering that route. Do not make a standalone SDK-to-Remote-Control bridge a prerequisite for the pilot.

External worker sessions should not be assumed to appear as native subagents in the Claude app. The controller must surface their status, results and questions through the supervisor. A remote conversational interruption does not necessarily cancel an external process. Local jobs require the computer to remain running; reconnecting the UI does not make execution independent of the host.

## Example workflow

Suppose the next milestone needs a small report-formatting helper and CLI wiring, with known inputs, outputs and consumers.

1. Astra prepares a task packet with the exact base commit, authoritative requirements, relevant callers, examples, edge cases, allowed files, dependencies and acceptance checks. An independent review checks the contract itself.
2. The Claude Code controller admits the task to the Cerebras route only if its semantics and dependencies are sufficiently bounded. Sensitive financial/research semantics or uncertain consumers remain with Claude.
3. The supervisor creates an isolated worker environment and starts OpenCode against an account-verified Cerebras model. Begin with `gpt-oss-120b` if still available and appropriate; verify current account capabilities first.
4. The worker reads the packet and relevant source, implements the candidate and returns its patch, checks and unresolved questions. Missing context or a changed contract triggers escalation.
5. A trusted runner performs required checks, and a Claude reviewer examines the frozen candidate and actual consumers. Worker claims and worker-authored tests alone do not establish acceptance.
6. Allow one bounded repair attempt in the pilot, then escalate to Claude. Record every attempt and the review/repair time.
7. The controller integrates only after existing gates pass and records evidence against the resulting commit.

From the phone, the user can say: “Finish running jobs, pause new dispatches, and show me anything awaiting review.” The controller translates this into supervisor operations and summarizes the result. Resuming dispatch and cancelling a specific job must also be explicit operations with observable outcomes.

## Context design

Keep the controller's active context focused on current decisions, the dependency graph, outstanding jobs and evidence pointers. Store full worker logs and historical transcripts as retrievable artifacts.

Each task packet should contain:

- Task ID, packet version/hash, base SHA, goal and exclusions.
- Authoritative rule excerpts, relevant source paths and known producers/consumers.
- Input/output contracts, invariants, error cases and resource constraints.
- File ownership, semantic dependencies and prerequisites.
- Acceptance commands/examples and escalation conditions.

Start by testing a 10–25k-token briefing budget, with room for tool results, reasoning and edits. This is an experimental target, not a hard limit that permits omitting necessary context. Workers must be able to discover relevant callers and request more context. New dependencies or contradictory requirements invalidate the packet and return it to the controller.

## Supervisor requirements for unattended operation

These are proposed interfaces to implement, not existing commands:

| Operation | Required behavior |
|---|---|
| `submit(packet)` | Validate admission, freeze configuration, enqueue a uniquely identified attempt. |
| `status(job_id)` / `list_jobs()` | Return durable state, heartbeat, resource waits and evidence pointers. |
| `pause_dispatch()` / `resume_dispatch()` | Persist dispatch policy across controller or supervisor restarts. |
| `cancel(job_id)` | Stop the owned process group, record termination and prevent stale acceptance. |
| `result(job_id)` | Return the immutable candidate, validation evidence and unresolved questions. |

Before connecting this supervisor to the live loop, implement:

- A single-controller lease, per-attempt identity, heartbeat and stale-result fencing. Reconcile running processes before retrying after a crash; never give a replacement attempt a checkout still owned by the old one.
- Separate budgets for model requests, active implementers, tests/database operations and review backlog. Start with one external worker and one full-suite slot; raise limits only from measurements.
- Real filesystem/process isolation. Worktrees help isolate edits but do not protect credentials or shared environments. Keep controller credentials, SSH access, production access and writable shared state outside worker reach. Generated tests also execute untrusted code.
- Trusted disposable database provisioning before admitting database tasks. The first offline screen can avoid Postgres and shared-environment writes.
- Time, output, tool-call, spend and repair limits; provider cooldowns; explicit escalation on missing context or truncated/incomplete results.
- A frozen candidate SHA and evidence bound to its packet, checks and environment. Relevant edits or rebases invalidate previous approval evidence.
- Structured events for dispatch, model/tool work, test/review queues, repairs, escalation and acceptance. Keep logs free of credentials.

Use a thin local CLI first. Add MCP if typed asynchronous job operations improve the controller experience. Avoid building a new general-purpose agent runtime when OpenCode already supplies the worker loop.

## Staged implementation

### 1. Prepare the new host and baseline

Reconcile the existing loop at a clean handoff boundary. Confirm Claude Code login and Remote Control on the personal computer. Measure the current context-recovery workflow and local test/database performance. Keep the existing routing and review policy during baseline collection.

Verify current OpenCode/Cerebras setup, available model IDs, context limits, reasoning settings, multi-turn tool behavior and actual account quotas. Keep provider credentials outside the repository. Reference: [Cerebras OpenCode integration](https://inference-docs.cerebras.ai/integrations/opencode) and [OpenCode CLI](https://opencode.ai/docs/cli/).

### 2. Run a small offline feasibility screen

Use six bounded tasks with frozen packets and independent checks, comparing current Claude workers with one Cerebras candidate. Use one supervised external worker, avoid Postgres initially, and do not merge candidates. Include two separate routing challenges to test whether deceptively small sensitive tasks are escalated.

Measure draft plus scoped-validation/review time, errors, repair effort and usage. This first screen can reject an ineffective adapter or worker route; it does not establish full acceptance speed or equal quality. It does not require the full durable supervisor.

### 3. Build the minimum durable supervisor if the screen warrants it

Implement the operations and unattended requirements above. Exercise pause/resume, cancellation, controller restart, worker crash, stale completion and resource admission. Expose the supervisor to Claude Code and confirm that remote requests produce the intended durable state transitions.

### 4. Evaluate complete acceptance and enable a limited canary

Compare the same packets, hardware, scheduling and review policy with Claude versus Cerebras workers. Include all required tests, review, fallback and integration costs. Begin with a 12–20-task screening evaluation, followed by prospective canary work and a larger quality evaluation before broad routing.

Suggested practical screening targets: at least 25% lower median route-to-acceptance time for the eligible class, no worse tail behavior, no observed additional Important/Critical escapes and no increase in median reviewer effort. These are proposed promotion criteria, not proof of quality equivalence. Count failures and Claude fallback time; measure how much real milestone work qualifies. Include setup and maintenance time when deciding whether the change pays off.

### 5. Expand only from evidence

Keep sensitive paths and independent review with strong Claude models. Add another Cerebras candidate or test Opus as controller in separate experiments. If worker savings disappear after review and repair, retain the context/supervisor improvements and use Claude workers.

## Completion criteria for the future implementation

- The user can inspect status, pause/resume dispatch and request cancellation through Claude Remote Control, with confirmed supervisor outcomes.
- Jobs survive controller context resets without duplicate execution or lost evidence; crash recovery rejects stale attempts.
- External workers cannot bypass resource limits, mutate controller state or access production credentials.
- Existing quality and integration gates remain enforced; no worker's self-reported completion substitutes for acceptance.
- Recorded comparisons justify the enabled task classes. No claim of equal quality or project-wide speedup is made from token rates or a small pilot alone.

This file records the intended future implementation. Creating it does not start the experiment, install software, configure providers or modify the active autopilot.
