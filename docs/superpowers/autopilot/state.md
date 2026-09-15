# Autopilot checkpoint

Updated 2026-09-14 19:18 CT by controller session sports-5e (session_01K7uXN54fkXzh2teuSzb1bo) in `/home/trey/dev/sports` on Omarchy (lock held by process 718911 under flock in tmux `sports-autopilot`; a peer unlocked Claude session `herdr-autopilot-68` is alive and is not a controller). Last journal entry: 215 (213 fix 64 merged; 214 audit-interval, T15, T6 merged; 215 phase 4.6 on main). Main **50cb103** = the 4.6 merge (fix 64 + slices A-E; revision `0010_phase46_fun_tickets`); runtime still ca30ed1 until the 23:22 CT deploy wakeup (R4: NFL kickoff 19:15 CT; full recipe).

## Rulings landed at resume (journal 199-212)

User-side closed: NAS key (bundle on the NAS), age key (held off-host, nag dropped), Anthropic account limits, Kalshi read scope, runs 14485/14486 stay as recorded, gate criterion 8 stays (no R1 amendment). Scheduled: 6E cold-start Tue 2026-09-15 07:00 CT (below). Open: owner password hash and TLS files after a release carries `harness owner-password-hash` (tell the user that day). Loop-side: rows 62/63 option A (checks bounded at the NO_WATCHER cutoff fix's release, fix in 6B's integration round); row 64 sonnet hotfix (correction marker + check exclusion); weekend props option (a) on `phase46-fun-tickets`; 6B manifest gate scoped to the resting interval (spec 0.16, audit.py, fixture, rerun on order 157); `parlay_slot_state` table in the 4.6 integration round; `site.web.api.espn.com` on the allowlist (path-pinned). Gate 13 authorized only for the three named predicates; gates 5/6 only for the props cadence change.

## Active units

- **deploy pending** (Orient 2): main 50cb103 is 77 files ahead of the runtime ca30ed1 (fix 64 + the 4.6 release). Wakeup 71baf905 at 23:22 CT: preconditions, `make deploy-omarchy` (full recipe: models, revision `0010`, the `app-serve-lan` compose profile which stays dormant without the user's three LAN files), then verify (fix 64 row; Layer 3b walkthrough at the day's first verify); else after Tuesday's 07:00 CT reboot.
- **phase 6B** (ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`; dispatches 22): phase branch `phase6b-repair-execution` **0e3f3b3** (T11 merged 19:13 CT). **T12** committed **117d5bd** on 0e3f3b3 (`phase6b-t12-verify`; the worker's diff applied by the controller: the sandbox mounts `docs/superpowers/autopilot` read-only), **sonnet review running** since 19:17 CT (chase 19:50 CT); then unsharded suite, ff. **Integration round** (opus, ref a743beda07ee48cb5) **running** on `phase6b-integration` at 45c8298 since 18:37 CT (chase 20:08 CT; rebases onto the T12-merged head at commit; its scoped runs were starved by the T11 suite's exclusive flock until 19:12 CT). After both: the phase's final review/acceptance, `0008` renumber at the main merge (main now holds 0009 and 0010).
- **phase 4.6** (ledger `.superpowers/sdd/2026-09-13-phase4.6-fun-tickets/progress.md`; dispatches 56): **on main at 50cb103** (journal 215); `phase46-fun-tickets` = main. Remaining: **T18b** (controller-executed queries in the first watched NFL window after the release; sonnet writes the report), **T19** (verification rows: the worker prepares the diff, the controller applies it), the walkthrough items. Status stays `planned`.
- **phase 6D** (ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`; dispatches 22): phase branch `phase6d-sustained-evaluation` **606fb5a** (T7 merged 18:45 CT). **T8** (opus, ref a1e4be97acfd9cfa9) **running** on `phase6d-t8-episodes` at 606fb5a since 19:02 CT (chase 20:34 CT). Then T10 (verify rows: worker diff, controller applies) - T9 is merged. T10 carries: I4, M1/M4/M5/M6 (T6), Minor 7 and the cap/age-read notes (T7).
- **6C**: planned/partial; the deferral of two funnel units accepted (journal 184); closure must name both as delivered by 6D. **Qwen**: D1-D6 approved; nothing activates before 6B's integration round.

## Pending results / subprocesses

- Suites: none running. Receipts under `~/.cache/sports-harness/test-state/` (main's acceptance receipt: 50cb103 on harness_test_phase46_merge_main, release tree 7abd4c63da3a).
- Agents running: 6B T12 reviewer (sonnet, a86c2a6c41bf2227f), 6B integration implementer (opus, a743beda07ee48cb5), 6D T8 implementer (opus, a1e4be97acfd9cfa9). Refs in the ledgers.
- Wakeups (CronList, session-only): 71baf905 Mon 23:22 CT (deploy-window end: the release; re-judge fix 49 and row 68), 6ff56c01 Tue 06:35 CT (cold-start prep), chases b333fe8f 19:50 CT (T12 review), ec84284b 20:08 CT (6B integration), 8c8a1707 20:34 CT (T8); durable reminder unit sports-reminder-2026091501 (Tue 06:30 CT).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), phase6b_integration, phase6b_t12_verify, phase6d_t8_episodes, fix_20260914_score_corrections. Revoked this session: phase6d_t5_budget, phase46_t12_ticket, phase46_props_weekend, phase6b_t9_rescore, phase6b_audit_interval, phase46_t15_ticket_ui, phase6d_t6_latency, phase46_slot_state, phase46_t12_integration, phase6b_t11_amendment, phase6d_t7_denominator, phase46_merge_main.
- Worktrees: phase6b-repair-execution, phase6b-integration, phase6b-t12-verify, phase6d-t8-episodes, fix-20260914-score-corrections (merged; remove after the deploy verify), plus the preserved recovery ones (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke).

## Evidence receipts

- Runtime ca30ed1: receipt `releases/20260914T144712Z-ca30ed1` healthy 14:49:49Z; verify 196 PASS on deterministic rows; 198: fix 49 DEFERRED, row 68 present again. Preflight this session `evidence/2026-09-14-preflight-1541.txt` (`paper posture intact`).
- Main receipt: the 6A verify block's full suite at 8f1ae26 on harness_test_main: 3,344 passed / 6 xfailed / 1 deselected, pristine, release tree = ca30ed1's.
- Phase 6B: 97d639e 3,325 passed (unsharded). Phase 4.6: 379fabb six shards exit 0, pristine, release tree d0d480e50193. Phase 6D: 10921cc 3,366 / 6 xfailed / 1 deselected, pristine.
- Free space `/srv/sports-harness` 14 % used (05:42 CT); database 121.9 GB, growth 16.9 GB/day. Bundle `sports-2026-09-14.bundle` on the NAS (44,024,501 bytes, 15:27 CT) and local.

## Counters and gates

- CT day Sep 14: 121 dispatches at 19:18 CT. Unit counters: phase 6B 22; phase 4.6 56; phase 6D 22; hotfix fix-64 2; batches closed today (57/58: 9; 66: 2; aliases: 2). Failed deploys today: 0. Fix rounds open: none (T6, T11, slot-state rounds closed). Re-dispatches: T6 review 1/3, fix-49 r3 1/3, T2 1/3. Rate-limit pauses: 1 (06:07-07:47 CT).
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
- The worker sandbox mounts `docs/superpowers/autopilot` read-only: a plan's verify.md task is prepared by the worker as a unified diff and applied by the controller (`patch -p0`). The 6B branch's pre-sharded runner holds one exclusive flock, so a 6B full suite starves every other 6B-branch `make test` (workers included) for its 40-45 min.
- A phase branch far behind main merges with a merge commit resolved by an opus implementer (unions only) and a scoped review against both parents; a 54-commit rebase conflicts at every models.py hunk.
