# Autopilot checkpoint

Updated 2026-09-14 18:21 CT by controller session sports-5e (session_01K7uXN54fkXzh2teuSzb1bo) in `/home/trey/dev/sports` on Omarchy (lock held by process 718911 under flock in tmux `sports-autopilot`; a peer unlocked Claude session `herdr-autopilot-68` is alive and is not a controller). Last journal entry: 214 (199-212 the user's fourteen 15:38 CT rulings; 213 fix 64 merged; 214 audit-interval, T15 and T6 merged). Main **2947d51** (docs) = **5d0012c** code (fix 64 merged); runtime still ca30ed1 until the 23:22 CT deploy wakeup (R4: NFL kickoff 19:15 CT).

## Rulings landed at resume (journal 199-212)

User-side closed: NAS key (bundle on the NAS), age key (held off-host, nag dropped), Anthropic account limits, Kalshi read scope, runs 14485/14486 stay as recorded, gate criterion 8 stays (no R1 amendment). Scheduled: 6E cold-start Tue 2026-09-15 07:00 CT (below). Open: owner password hash and TLS files after a release carries `harness owner-password-hash` (tell the user that day). Loop-side: rows 62/63 option A (checks bounded at the NO_WATCHER cutoff fix's release, fix in 6B's integration round); row 64 sonnet hotfix (correction marker + check exclusion); weekend props option (a) on `phase46-fun-tickets`; 6B manifest gate scoped to the resting interval (spec 0.16, audit.py, fixture, rerun on order 157); `parlay_slot_state` table in the 4.6 integration round; `site.web.api.espn.com` on the allowlist (path-pinned). Gate 13 authorized only for the three named predicates; gates 5/6 only for the props cadence change.

## Active units

- **hotfix `fix-20260914-score-corrections`** (row 64; journal 207): **merged to main at 5d0012c**; deploy (full recipe) at the 23:22 CT wakeup once the NFL window clears; then verify; row 64 closes on the verify.
- **phase 6B** (ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`; dispatches 19): phase branch `phase6b-repair-execution` **45c8298** (T9, audit-interval merged; order 157 re-audited, journal 214). **T11** at **e878da9** on 45c8298 (`phase6b-t11-amendment`; review round 1 closed, re-review APPROVED; M-2/M-4 to the integration round); **unsharded suite running** since 17:50 CT (`t11-full-e878da9.log`, Monitor); on green: `--ff-only` in the phase worktree, then dispatch **T12** (sonnet, `task-12-brief.md`, verify.md only) and the **integration round** (opus, `integration-round-brief.md`: journal 206 bound + two check predicates with `NO_WATCHER_CUTOFF_FIXED_AT` set at the release commit, T9 Minors 2/5, audit Minor 4, the `<=` alignment, T11 M-2/M-4) in parallel on the T11-merged head; `0008` renumbers at the phase merge.
- **phase 4.6** (ledger `.superpowers/sdd/2026-09-13-phase4.6-fun-tickets/progress.md`; dispatches 54): phase branch `phase46-fun-tickets` **b5f7390** (T15 b646a55, slot-state 13f4998, plan annotations b5f7390 merged). **t12-integration** at **185e2e1** on b5f7390 (`phase46-t12-integration`; opus DONE: N1, N2, N4 via `_payload_for_listener`, `combined_american`/`anchor` keys, comments; anchor text ruled `plain_text`), **sonnet review running** since 18:22 CT (chase 18:54 CT). Then T18b (needs the phase deployed: after 6B/6D merges and a release) and T19 (verification rows, last).
- **phase 6D** (ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`; dispatches 20): phase branch `phase6d-sustained-evaluation` **470a7d1** (T6 merged). **T7** at **c64da82** on 470a7d1 (`phase6d-t7-denominator`; opus DONE_WITH_CONCERNS: five deviations, EXPLAIN condition met), **opus review running** since 18:16 CT (chase 18:49 CT). Then sharded suite, `git fetch . phase6d-t7-denominator:phase6d-sustained-evaluation`, **T8** (opus, `task-8-brief.md` prepared). T10 carries: I4, M1/M4/M5/M6 (T6), the age read's per-partition cost and the 25,000-run cap sentence (T7).
- **6C**: planned/partial; the deferral of two funnel units accepted (journal 184); closure must name both as delivered by 6D. **Qwen**: D1-D6 approved; nothing activates before 6B's integration round.

## Pending results / subprocesses

- Suites detached (nohup; Monitor): 6B T11 at e878da9 (unsharded, started 17:50 CT, ~18:35 CT). Receipts under `~/.cache/sports-harness/test-state/`.
- Agents running: 6D T7 reviewer (opus, a77e1583d5e4b0f22), 4.6 t12-integration reviewer (sonnet, a21641673eb2a631a). Refs in the ledgers.
- Wakeups (CronList, session-only): 71baf905 Mon 23:22 CT (deploy-window end: fix 64 release; re-judge fix 49 and row 68), 6ff56c01 Tue 06:35 CT (cold-start prep), chases 3c6f5f5a 18:49 CT (T7 review), a6672e81 18:54 CT (t12-integration review), 44e488a0 19:33 CT (stale: t12-integration implementer, done), bdc93e69 18:11 CT fired (nothing to chase); durable reminder unit sports-reminder-2026091501 (Tue 06:30 CT).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), phase6b_t11_amendment, phase46_t12_integration, phase6d_t7_denominator, fix_20260914_score_corrections. Revoked this session: phase6d_t5_budget, phase46_t12_ticket, phase46_props_weekend, phase6b_t9_rescore, phase6b_audit_interval, phase46_t15_ticket_ui, phase6d_t6_latency, phase46_slot_state.
- Worktrees: phase6b-repair-execution, phase6b-t11-amendment, phase46-t12-integration, phase6d-t7-denominator, fix-20260914-score-corrections (merged; remove after the deploy verify), plus the preserved recovery ones (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke).

## Evidence receipts

- Runtime ca30ed1: receipt `releases/20260914T144712Z-ca30ed1` healthy 14:49:49Z; verify 196 PASS on deterministic rows; 198: fix 49 DEFERRED, row 68 present again. Preflight this session `evidence/2026-09-14-preflight-1541.txt` (`paper posture intact`).
- Main receipt: the 6A verify block's full suite at 8f1ae26 on harness_test_main: 3,344 passed / 6 xfailed / 1 deselected, pristine, release tree = ca30ed1's.
- Phase 6B: 97d639e 3,325 passed (unsharded). Phase 4.6: 379fabb six shards exit 0, pristine, release tree d0d480e50193. Phase 6D: 10921cc 3,366 / 6 xfailed / 1 deselected, pristine.
- Free space `/srv/sports-harness` 14 % used (05:42 CT); database 121.9 GB, growth 16.9 GB/day. Bundle `sports-2026-09-14.bundle` on the NAS (44,024,501 bytes, 15:27 CT) and local.

## Counters and gates

- CT day Sep 14: 114 dispatches at 18:21 CT. Unit counters: phase 6B 19; phase 4.6 54; phase 6D 20; hotfix fix-64 2; batches closed today (57/58: 9; 66: 2; aliases: 2). Failed deploys today: 0. Fix rounds open: none (T6, T11, slot-state rounds closed). Re-dispatches: T6 review 1/3, fix-49 r3 1/3, T2 1/3. Rate-limit pauses: 1 (06:07-07:47 CT).
- Gates: none open.

## Deadlines and open acceptance

- Tonight: NFL kickoff 19:15 CT (game 16); deploys blocked about 17:35-23:15 CT (R4). The row 64 hotfix deploys after 23:15 CT if merged and green, else after Tuesday's reboot.
- **Tue 2026-09-15 06:35 CT (journal 203)**: checkpoint state, journal, commit, push; stop the stack cleanly by 06:50 CT; the session dies with the 07:00 CT reboot; the user relaunches per Kickoff; the new session records the cold-start evidence for 6E and runs Tuesday's duties (futures check 09:30 CT, row 68 and fix 49 re-judge).
- Tell the user the day a release carrying `harness owner-password-hash` lands (4.6 T10 reaches main at the phase merge; roadmap TODO 565 stays open).
- User decisions pending: none from the 15:38 CT list. Still the user's: the four yardage/receptions prop families (DraftKings' page has no family rule; they stay `market_unsupported`); 6F's dated amendment; the legal decision.
- User-side order (journal 184 item 9, updated by 199-203): age key done; Anthropic limits done; Kalshi done; LUKS window scheduled Tue 07:00 CT; LAN files after T10 reaches main.

## Risks and lessons

- A blanket reader sweep can silently redefine a pinned gate criterion; only the golden-literal test caught it. Any future exclusion pass names the criterion family as out of scope up front.
- The subscription limit cuts every in-flight worker at once; resumes from transcripts work (SendMessage), but a dead controller turn cannot arm the wakeup: record the pause after the fact and re-derive due items from the clock.
- Rebase immediately before every ff-merge, in one command; a merge script must check the suite's exit code. Shard layout follows durations (row 67). Review packages: `git -c diff.algorithm=histogram diff`. A phase branch with no worktree merges with `git fetch . <task>:<phase>`; one with a worktree merges there with `--ff-only`. Create a dependent task's worktree only after the ff it depends on.
- The release tree excludes only `docs/superpowers/autopilot/`: a `docs/reports/` or plan commit on main invalidates every pending branch receipt after rebase. Land such docs after the pending fix branches ff, or accept one rerun.
- Never `git stash pop` in a worktree: the sandbox masks are not controller-side dirt, and the repo's stash list holds preserved recovery entries.
- The app-backup sidecar computes its sleep from the clock at start: after a stale-clock boot the nightly slips and the deploy's backup precheck refuses; the runbook's fallback (`dump.sh nightly` by hand) is the fix, and a refused candidate image tag means the release SHA must move (a journal commit).
- The controller's nohup suites give no notification: a background `until` waiter or a `Monitor` does; a Bash `run_in_background` suite notifies on exit. Five concurrent suites stretch a 10-minute suite to 12-40 min and can cancel a fixture truncate on the statement timeout (a rerun, not a fix round). The 6B branch's unsharded suite budget is 45-60 min.
- After an unclean reboot this box can boot with a stale clock; fix 57 makes the recorder refuse an undisciplined clock: `timedatectl` before each release. Tuesday's planned reboot: stop the stack first; expect the RTC to be right this time (hardware fixed, journal 178).
- Controller shell: quoted heredocs only for prose; `scripts/omarchy.sh sql` reads SQL from stdin; column names: `exec_heartbeat.last_loop_at`, `fills.filled_at` (no `created_at`), `check_results.check_name/ts`, `job_runs.notes` jsonb, `game_score_events.raw_id` -> `raw_responses`, `venue_trades` has no `id`. `.superpowers/` is gitignored. Read `date` before stamping a ledger line. `scripts/test-suite.py` splits TEST_ARGS on spaces (no quoted `-k`).
- The recorder process hosts the settle job (interval 3,600 s from process start). The normalizer backlog remains a separate open defect (6D).
- Revision numbering: main takes `0009_score_correction` (row 64); the 4.6 and 6D `0009` revisions and 6B's `0008` renumber at their merges.
- A worker may start a full `make test` on its own; the sandbox's shared test-suite lock then queues every other suite. Briefs say scoped sets only; kill a worker's unrequested full run (`fuser` on `test-state/test-suite.lock` names the holder) and message it. Apply reviewer Minors by exact python replacement with every anchor asserted before any write; never chain a suite launch behind a patch step.
