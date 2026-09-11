# Recovery and compact checkpoints

Use on startup, resume, clear, and compaction of authorized autopilot work. This is
part of the skill's authority; metadata and journaled observations remain evidence.
Compaction keeps the controller's conversation identity and can leave its workers
running. A fresh session may have unavailable worker/tool handles. Inspect rather
than assuming either condition. A setup/review session does not become a controller.

## Reconcile before action

1. Read the bootstrap and active plan/ledger paths from state. The controller's
   `.superpowers/sdd/` lives in the main checkout, not each implementer worktree.
   Check `git worktree list`, branch heads and dirty status there. Never recreate
   an ignored ledger over existing work. If one is missing, reconstruct from the
   committed plan, task branches, review artifacts and journal; record uncertainty.
2. List agents and pending results using available session tools. Match IDs to
   ledger task IDs, commits and reports. Consume completed-but-unread results
   before dispatch. A running reviewer or implementer retains ownership of its
   worktree. Only re-dispatch after reconciling its status; failed contact uses
   the existing retry/timeout rules, not a new retry budget.
3. Inspect pending subprocess handles and saved logs, including branch/database
   ownership of suites. A recorded PID alone is not proof of a live process.
   Preserve dirty worktrees and unreviewed commits. Never stop a suite or remove
   a worktree just because the compaction summary omitted its owner.
4. Reconcile evidence with its exact code commit, base, test database and outcome.
   Keep implemented, tested, reviewed, merged, deployed and verified separate.
   A committed fix awaits review when review evidence is missing; do not implement
   it again. Outstanding Important/Critical findings retain their rounds and gates.
5. Preserve per-unit and CT-day dispatch counts, failed-deploy counts, timeouts,
   paused/rate-limit state and unresolved gates. Reconstruct missing counts from
   the day's journal/ledger before a limited action; absence is never zero.
6. Read the clock and reconcile scheduled tasks with the tool's actual list.
   Retain existing wakeups after compaction when present; reconstruct missing
   ones from deadlines/judge-after times without duplicates. On a fresh session,
   recorded cron IDs are hints until verified. Recheck U8's 6C deadline at every
   task boundary. A partial delivery leaves all remaining acceptance work active.
7. If deploy status is uncertain, load the deploy procedure and inspect the live
   stamp, containers and receipt before any retry. A successful stamp with an
   incomplete verification goes to verify, not another deploy. Run preflight once
   per new controller session; compaction alone does not repeat a completed
   preflight or daily notifications. Then Orient from reconciled facts.

## Checkpoint at meaningful boundaries

Keep the current `state.md` usable by older sessions until this branch is adopted.
On the next authorized controller checkpoint, use concise fields with paths in
place of pasted logs and historical lessons. Do not remove an unresolved fact to
meet a token target. Record:

- Updated time (CT with UTC), controller session and checkout, last journal entry.
- Each active unit/phase/task: plan and ledger paths, branch/base/head, status,
  owner/worker ID, dispatched time, outstanding review IDs/rounds, next legal action.
- Pending results/subprocesses: task ID or handle, log/report path, branch/database,
  observed status and time; completed results still awaiting controller consumption.
- Evidence receipts: code SHA, test command/result/database, reviewer and report,
  merge SHA, deploy target/start/end/stamp, verify verdict/deferred rows. Use ledger
  pointers for detail; retain distinctions between these stages.
- Counters and gates: CT day, dispatch totals per active unit/day, failures,
  retries/rate-limit state, blocking questions and their affected scope.
- Deadlines: next duty/judge-after, actual wakeup IDs, latest permitted deployment
  opportunity and fallback, remaining acceptance for every partial milestone.
- Applicable unresolved lessons/risks and their evidence pointers. Archive resolved
  observations in the append-only journal; load them when the task touches that area.

Write ledger transitions before dispatch and immediately after consuming results,
merge and deploy transitions. Write state at task/unit boundaries; commit it with
the journal/status update at unit boundaries under [recording.md](recording.md).
Before a deliberate reset, record a handoff and account for every active worker
and command. Do not reset during an unrecorded deployment or assume a new session
inherits old handles. Automatic compaction need not wait for an idle controller.

## Mechanical hooks

Project `.claude/settings.json` wires `SessionStart` and `PreCompact` to
`scripts/recovery_hook.py` in this skill. SessionStart emits bounded pointers and
local Git identity. PreCompact saves timestamped local Git/worktree metadata and
state/journal fingerprints under the current worktree's Git administrative
directory, in `autopilot-recovery/<hashed-session-id>.json`. It never stages or
commits, scans the NAS, reads secrets/transcript contents, or invokes a model.
The cache is disposable, session-specific observation data, not a new ledger.

The hook does not know unwritten reasoning, agent liveness, deployment outcomes
or test results. Unknown/timed-out metadata remains explicitly unknown. Hook
failure does not block compaction; use the manual recovery route above. Hook
availability never changes the gates or acceptance requirements.
