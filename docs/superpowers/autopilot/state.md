# Autopilot restart checkpoint

Updated 2026-09-12 19:14 CT (2026-09-13 00:14Z). This is user-directed preparation,
not a running Claude controller. Main 073732f; Omarchy runtime b0a3991/schema 0006.
Controller/development/test destination: /home/trey/dev/sports on Omarchy. Mac
checkout preserves originals; no retired NAS service is to be started.

Active plan: docs/superpowers/plans/2026-09-12-omarchy-loop-restart.md.
Ledger: .superpowers/sdd/omarchy-restart-2026-09-12/progress.md. Prior ledger
.superpowers/sdd/hotfix-2026-09-12/progress.md and the entire recovered SDD tree remain.
The user's restart-preparation instruction authorizes bounded fix 45 round 3; do not
ask the old gate question again. No new 6B task starts in this preparation session.

- 52: reviewed, full suite 3,136 passed / 6 expected xfails at 073732f on Omarchy; merged, undeployed.
- 47: reviewed and merged before migration; still undeployed.
- 48/49: repaired, independent scoped review PASS (review48-r2.md); branch
 recovery/fix48-review now 4ac9008 after clean rebase onto 52 plus host-neutral memory label.
 Fullsuite pending. Original memory cause/<5% synthetic/6 h / 500 MiB acceptance stays OPEN.
- 45: round 3 code independently reviewed, targeted 11 passed. Fullsuite running on
 recovery/fix 45-round 3, log fix 45-full.log in restart ledger directory. No Critical
 remains in reviewed code; merge/deploy evidence still required before 6B T3.
- Controller tooling: reviewed release script: 51 pure tests PASS; fixed MCP worker tool
 boundary replacing hook-only enforcement, actual sandbox/Claude drill pending.
 Claude auth loggedIn=true/claude.ai and Superpowers 6.3.0 installed. No loop launched.

Counters: prior CT-day dispatch count 19 retained (old transcript discrepancy remains
conservative, never reset). Current setup 14 dispatches audited from controller tool
calls through 00:07:38Z; day total 33. Failed deploys 0; no release attempted. Historical
fix 48 redispatch 1 and fix 45 round 2 history retained; old worker IDs/monitors/wakeups
are not live Omarchy handles. Current preparation workers are tracked in restart ledger.

6B branch phase6b-repair-execution still 2d0fd71 with accepted T10/T1/T2 only;
.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md. Rebase onto final main
with 0007 before T3; new 0008 dependency and T1/T2 review minors remain in task 3 brief.
6C remains partial/planned: all 11 tasks deployed b0a3991, Sunday 19:00 week-key checks,
Monday before 09:00 diagnostic report, numeric eligibility/funnel and 6B dependencies open.
6E inventory/restore/cutover are recorded; six-hour observer still running, cold-start
and two representative corrected-workload windows pending. No claim migration fixes 6B.

Actual user-systemd reminder timers installed (not model wakeups):
- sports-reminder-2026091301: Sun 02:50 CT inspect earliest full-release opportunity.
- sports-reminder-2026091302: Sun 09:30 CT readiness before 10:20 NFL deployment block.
- sports-reminder-2026091303: Sun 18:30 CT prepare 19:00 week-key checks.
- sports-reminder-2026091401: Mon 07:30 CT prepare diagnostic before 09:00.
Timers survive SSH loss, not reboot. Drill 2026091201 delivered successfully. Native
Claude wakeups still require actual controller-session setup. Based on recorded games,
full release cannot occur before roughly Sun 03:00 CT; re-query actual statuses/time first.

Fresh observations: original three fix 51 queries PASS within unchanged 2 s (1.506/0.211/
0.078s); no WS timeout/subscription rejection seen in last 2 h, not a 24 h fix 50 acceptance.
One additive host telemetry refresh replaced stale NAS samples with actual Omarchy
862.329 GiB free of 951.852 GiB, 20399.992 MiB available at 00:12:44Z; no old row changed.
UI still reflects historical tape gaps, skipped daily checks and 6B dirty-book findings.
NAS RSS restart thresholds are retired. Preserve RFQ 0, paper/LIVE 0, 600 GB alert budget,
25% disk gate, all scientific/money/retention rules. Use current instructions, not old
state's emergency restart monitor or NAS deploy recipes.
