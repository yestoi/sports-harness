## Preflight, once per controller session

Read linux-controller.md and recovery.md. Run `make preflight` on Omarchy and save
the output to a dated evidence file. It checks Git/worktrees, the isolated test DB,
runtime containers/storage/memory, effective paper/RFQ/capacity settings, live stamp,
schema, tick/tape/executor state and upcoming games. The exact `paper posture intact`
line belongs in the journal; failure is a gate. Credentials are checked for existence
and mode only, never printed. Runtime credentials stay under `/srv/sports-harness`.

Reconcile branches and ignored ledgers, worker results, test processes and counters.
Dirty work is inspected and preserved. A phase branch is valid only with its ledger;
never recreate or clean a task worktree merely because the old session is gone.

Verify actual native scheduler, messaging and browser tools in this session. Test
Linux desktop notification on the day's first preflight and record the result;
use native PushNotification too when available. No `osascript`/`caffeinate`/self-SSH
assumptions. A missing browser connection leaves its acceptance pending while
deterministic checks continue; native tool IDs and old wakeups are not recovery evidence.

Check provider authentication (`claude auth status`) and installed Superpowers
procedures before dispatch. Run the fixed sports-worker MCP inventory and sandbox/DB/screenshot smoke before the first worker; command hooks alone do not enforce isolation.
Only the test Unix socket is available inside workers. Test-suite ownership and a
kernel lock, not process-count guesses, control full-suite concurrency.

Recompute the next duty from current games/jobs. Use native wakeups for the active loop and durable reminder files for
session recovery. Never claim a wakeup exists until its actual scheduler lists it.

Run `python3 .claude/skills/autopilot/scripts/context.py check` and
`.venv/bin/python -m pytest -q .claude/skills/autopilot/tests` once per session before any
dispatch. A `BLOCK` line or a failing test is fixed inside the preflight's own commit
(loop-owned files only: state, fixes.md, the entry being written); a `NEEDS USER` line goes
into the preflight entry's `Anomalies:` line and the next report's Needs you. A `BLOCK` or a
test failure that no loop-owned file can cure is gate 10: report it under Needs you; never
edit skill, test or roadmap text.
