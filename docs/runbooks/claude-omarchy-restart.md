# Resume Claude on Omarchy

Read the latest committed checkpoint before launching. This runbook records the
launch path and the boundary before new 6B work; it does not launch a controller.
The app release is deployed, but overall live/research verification is **FAIL**.
See `docs/superpowers/reviews/2026-09-12-omarchy-restart/release-and-verification-evidence.md`.

## Launch after the preparation handoff

In a fresh herdr tab or pane (run `herdr` first if it is not open):

```sh
cd /home/trey/dev/sports
scripts/autopilot-session.sh start-herdr
```

On a host without herdr, `scripts/autopilot-session.sh start` runs the same command
inside a `sports-autopilot` tmux session. In Claude, select `/effort` **high** explicitly (the saved
default may be xhigh, which costs three to four times the tokens for no measured gain), then
enter `/autopilot`. Both forms take the controller lock (a second launch fails), load
only the committed worker MCP server and use the installed Claude binary. Detaching
from herdr is fine; after a herdr *server* restart, exit the pane herdr auto-resumes
and launch again, because a plain `claude --resume` has neither the lock nor the
strict MCP config.
Claude's user login and official Superpowers plugin have already passed actual drills.
Authentication does not launch the loop; no reminder launches a model.

The controller, development and tests run on Omarchy. Production is
`/srv/sports-harness`; main is `/home/trey/dev/sports`; worktrees are under
`/home/trey/dev/sports-wt`. The retired NAS is an archive source, never a writer target.

## Recover the exact checkpoint

Read `.claude/skills/autopilot/SKILL.md` and its routed recovery/Linux references, then
run its bootstrap reader (all three parts: `bootstrap`, `bootstrap authority`, `bootstrap operator`). The committed `docs/superpowers/autopilot/state.md`,
roadmap, journal and active ledgers retain the actual task heads, receipts, counters,
review findings, deployment stamp and next legal action. Inspect live processes and
timers rather than adopting old Mac worker IDs or remembered wakeups.

The recovered `.superpowers/sdd/` tree remains in the main checkout. Its restart
ledger is `.superpowers/sdd/omarchy-restart-2026-09-12/progress.md`; original task
briefs/reviews, original Mac branches and migration stashes are preserved.

All workers use the `sports-worker` custom agent with the plan's model allocation.
Its only tools are the fixed sandbox shell and controller-provided screenshot tools.
Native worker shell/browser tools are outside this setup. Before each branch suite,
the controller provisions its isolated index fault-fixture permission as described in
`.claude/skills/autopilot/references/linux-controller.md`, then revokes it after final use.
Workers keep the restricted SQL role and never receive Docker/admin access. The controller commits
returned patches and produces clean exact-SHA (or exact-tree) full-suite receipts; namespace-only
credential masks are never returned as source edits. Reconcile a cancelled test's
actual process and per-database lock before another suite.

## Boundary before new 6B work

**Superseded (2026-09-13, journal 161/162):** T3 and T4 are accepted on the phase branch and
T5 is the next legal action (`task-5-omarchy-brief.md`); phase 4.6 was added to the roadmap.
`docs/superpowers/autopilot/state.md` is current; the paragraphs below are the 2026-09-12 record.

Main contains the reviewed and fully tested fix45 source. The accepted phase branch
contains T10/T1/T2 only; Task3 has not been dispatched. Its final base/head and existing
task smoke result are in the mirrored phase ledger. Confirm those before dispatching T3.
The fresh prepared brief is
`.superpowers/sdd/2026-09-11-phase6b-repair-execution/task-3-omarchy-brief.md`.
It uses `0008_phase6b_execution` after `0007_raw_events_lookup` (renumbered
`0011_phase6b_execution` on `0010_phase46_fun_tickets` at the merge of `main` into the
phase branch, D9); the original brief is retained alongside it. Keep the original scientific acceptance and T1/T2 review
rulings. A source integration prerequisite does not waive production's game window.

Release only clean tested main using `make deploy-omarchy-app` or
`make deploy-omarchy`, after the read-only release plan. The index/migration changes
require a full release. Re-query the actual game/job schedule at the quiet window;
roughly Sunday03:00CT is an estimate, not permission. The prior migration waiver has
expired. Keep paper/LIVE0, RFQ0, the600GB capacity budget, disk gate and all scientific,
money, provider, cadence and retention limits.

## Acceptance failures to reconcile at resume

Build93dfb95 is deployed to the four application services; WS retains b0a3991 and
PostgreSQL remains schema0006. The release receipt is healthy and summary checks9/9pass.
Broader verification fails: current venue quotes are about12h behind raw collection,
so corrected pricing order still produces no current gaps/candidates. Recorder RSS
continues growing. The fresh integrity sweep has23pass/3fail/1timeout; sequence-gap
markers still appear. Populated Gate overflows390px and Pulse shows historical exception
names. These findings, counters and evidence are in state/roadmap; do not relabel them
as accepted simply because the host has more RAM or the release command succeeded.

The resumed controller reconciles the failed verify and carried fixes before choosing
6B Task3. Separate user dashboard design work remains in the Mac checkout; inspect
its eventual branch before duplicating UI work. No new correctness task was started
by the restart setup.

## Timed acceptance and remaining operational work

The checkpoint retains actual judge-after times. WTD's next due instant is no earlier
Sun02:10:38CT (07:10:38Z); inspect the next scheduled settle at/after that instant.
Fix50's24h judge-after is SunSep13 21:05:30CT (Sep14 02:05:30Z). Fix51's fresh
duplicate-trades timeout means its25h all-pass history remains unestablished. The original memory diagnosis and
20-tick/<5% plus6h/500MiB acceptance remain open; additional RAM is not correctness
evidence. Fix50 needs its full observation window, fix51 a fresh recorded sweep and
its25h history, and6E still needs cold-start and corrected-workload acceptance.

6C remains partial: Sunday September13 at19:00CT uses Chicago ISO week37, and the
Monday diagnostic report is due before09:00CT. The full-release opportunity and
pre-NFL deadline must be checked from current games. The September14 report is
diagnostic; formal selection/confirmation awaits the user's6F amendment.

User-systemd reminders are installed for Sun02:50,09:30,18:30 and Mon07:30CT. They
survive SSH loss, not reboot. Reconstruct them after a reboot and set the actual
Claude session's native wakeups. Console LUKS unlock remains a user action.

Omarchy-to-NAS SSH authentication is currently unavailable. Local bundles and the
original archive are preserved; failed off-host copying is recorded. No login key
or NAS account authorization was silently changed.

## Host restart unit (user, 2026-09-15, journal 224 item 15)

`/etc/systemd/system/sports-harness.service` is a oneshot `sports-compose up -d --no-build --pull never`
with `RemainAfterExit`, no `Restart=` line and no timer; the path unit activates it only on the
production-enabled marker. The release reviewer's race finding (a restart unit re-raising the stack
while a release was mid-recipe) is closed on that shape: the unit runs once per boot, never on a
schedule, and a release never touches the marker. Observed at the 2026-09-15 07:43 CT cold start:
docker.service up 07:44:01 CT, the unit ran 07:44:30-07:44:41 CT (postgres healthy at 07:44:41, the
six app containers started in the same second), first recorder tick 07:45:12 CT
(`docs/superpowers/autopilot/evidence/2026-09-15-coldstart-0746.txt`). A clean `sports-compose stop`
or the systemd stop at reboot SIGKILLs `app-backup` after the stop timeout (Exited 137, journal 227):
the sidecar does not handle SIGTERM; no dump was in progress either time.
