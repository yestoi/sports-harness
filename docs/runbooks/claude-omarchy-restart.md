# Resume Claude on Omarchy

Read the latest committed checkpoint before launching. This runbook records the
launch path and the boundary before new 6B work; it does not launch a controller.
The app release is deployed, but overall live/research verification is **FAIL**.
See `docs/superpowers/reviews/2026-09-12-omarchy-restart/release-and-verification-evidence.md`.

## Launch after the preparation handoff

In an Omarchy terminal:

```sh
cd /home/trey/dev/sports
scripts/autopilot-session.sh start
```

In Claude, select `/effort` **high**, then enter `/autopilot`. The helper attaches an
existing `sports-autopilot` tmux session or creates one under a controller lock. It
loads only the committed worker MCP server and uses the installed Claude binary.
Claude's user login and official Superpowers plugin have already passed actual drills.
Authentication does not launch the loop; no reminder launches a model.

The controller, development and tests run on Omarchy. Production is
`/srv/sports-harness`; main is `/home/trey/dev/sports`; worktrees are under
`/home/trey/dev/sports-wt`. The retired NAS is an archive source, never a writer target.

## Recover the exact checkpoint

Read `.claude/skills/autopilot/SKILL.md` and its routed recovery/Linux references, then
run its bootstrap reader. The committed `docs/superpowers/autopilot/state.md`,
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
returned patches and produces clean exact-SHA full-suite receipts; namespace-only
credential masks are never returned as source edits. Reconcile a cancelled test's
actual process and host-wide lock before another suite.

## Boundary before new 6B work

Main contains the reviewed and fully tested fix45 source. The accepted phase branch
contains T10/T1/T2 only; Task3 has not been dispatched. Its final base/head and existing
task smoke result are in the mirrored phase ledger. Confirm those before dispatching T3.
The fresh prepared brief is
`.superpowers/sdd/2026-09-11-phase6b-repair-execution/task-3-omarchy-brief.md`.
It uses `0008_phase6b_execution` after `0007_raw_events_lookup`; the original brief
is retained alongside it. Keep the original scientific acceptance and T1/T2 review
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
