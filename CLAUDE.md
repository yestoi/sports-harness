# Sports harness

The experiment remains paper-only. The authoritative operating rules are in
`.claude/skills/autopilot/SKILL.md` and its routed references, the roadmap and
verification contract under `docs/superpowers/autopilot/`, the current committed
plan/spec, and the user's instructions. This file points to those sources; it
does not grant additional authority. Review/setup requests do not start the loop.

When resuming authorized `/autopilot` work after compaction or a session restart:

1. Read `.claude/skills/autopilot/SKILL.md` and `references/recovery.md` beside it.
2. Run `python3 .claude/skills/autopilot/scripts/context.py bootstrap` from the
   controller checkout; load the selected procedure and phase decisions as routed.
3. Reconcile active ledgers, Git/worktrees, pending worker results, subprocesses,
   counters and wakeups before dispatching or mutating anything. Compaction does
   not mean workers died. Check the live deployment stamp before any redeploy.

Keep evidence and unresolved work in durable checkpoints. Summaries, hook output
and remembered success are not completion evidence. Never discard partial work,
reset failure counters, or complete a partial milestone merely to simplify recovery.
The loop may not rewrite this anchor, its skill/support files or hook settings.
