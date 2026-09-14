# Autopilot checkpoint

Updated 2026-09-14 07:5x CT by controller session sports-80 (session_01TD2U76khHEb44dj9AJhD4K) in `/home/trey/dev/sports` on Omarchy (lock held; launched via `start`, tmux `sports-autopilot`). Last journal entry: 190 (187 fix 49 merged 07a4de8; 188 fix 67 merged 5395ac9; 189 verify 185 deferred rows PASS, row 60 closed; 190 paused: rate limit 06:07-07:47 CT, four workers resumed). Main ed4b9d8 = 5395ac9 code (fix 49 + fix 67 on d4527db) + docs; runtime a8d8fd3; **deploy trigger open** (fix 49 recorder peak, fix 67 tests) waiting for fix 57/58 to merge so the recorder fixes ship as one app-only batch. GitHub origin `yestoi/sports-harness`: main and every active branch pushed at this checkpoint. Every time below is a clock reading.

## Outage (journal 175, 178) and the pause (190)

Hard crash Sun 15:32 CT; boot Mon ~02:21 CT with a stale clock. Runs 14485/14486 mis-stamped: fix 57 lands the key and the readers; 14485 annotated by the controller after fix 57 deploys; 14486 closed by fix 58's sweep. Rate limit 06:07-07:47 CT (reset 07:20): four workers died and were resumed from their transcripts; the 06:44 CT fix 45 judge-after folds into the next verify.

## Active units

- **hotfix batch 57/58 (+61, 67 closed)** (ledger `.superpowers/sdd/hotfix-2026-09-12-omarchy/progress.md`; batch dispatches 9): branch `fix-20260914-clock-ghosts` **180c974** on main f8b449a (297b33e fix 57/58, f376c24 round 1 executor/parlay readers, 180c974 round 2 gate.py reverted to main). Opus review Approved with two Importants (I1 addressed, re-reviewed ADDRESSED; I2 = sequence the annotation). Round 2 exists because the round-0 sweep had put the exclusion on gate criterion 8 (`_STALENESS`): a gate criterion definition is R1/skill gate 9, reverted; the question is a **User-side TODO** (roadmap, 06:02 CT). In flight: full suite at 180c974 (`fix57-58-full-180c974.log`), haiku scoped re-review of round 2 (`rereview-3`), sonnet re-review of the rebase resolution (`rereview-2`, resumed 07:47 CT). Then rebase-and-ff in one command, app-only release (pre-deploy: `timedatectl` synchronized), verify, then the 14485 annotation by hand (UPDATE `runs.notes`, no DELETE).
- **next release (app-only)**: fix 49 + fix 57/58 + fix 67; window until ~17:35 CT (NFL kickoff 19:15 CT). Failed deploys today: 0. Verify after it re-judges fix 45 (missed 06:44 CT), fix 49's 6 h / 500 MiB row (08:58 CT), fix 52 daytime, walk items 9/22/29, pricing coverage at the first daytime tick (08:10 CT).
- **Carried fixes**: 45, 49 (merged; re-judge on the next build), 52, 57/58 (in flight), 62/63 (one defect, code fix to 6B's integration round; rows + unbounded checks a user decision), 64 (source behaviour; check disposition a user decision), 65 (after T10 reaches main), 66 (fix 60 follow-ups). Closed today: 59, 60, 61, 67.
- **phase 6B** (ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`; dispatches 8): phase branch `phase6b-repair-execution` **f9ed9d4** (T10, T1-T6). **T7** committed 3b66155 on `phase6b-t7-population`; opus review Needs fixes (I1 cap truncation refusal, I2 `capacity_skips` assertion; R14 judged refuted; population lower-bound caveat ruled for T11/6C); **round 1 in flight** (resumed 07:47 CT; chase 08:3x CT); then sonnet scoped re-review, suite (branch's old runner, ~45 min), ff-merge; then T8 (needs T7), T9, T11, T12, integration round (also rows 62/63's NO_WATCHER cutoff), final review. Renumber `0008_phase6b_execution` at merge.
- **phase 4.6** (ledger `.superpowers/sdd/2026-09-13-phase4.6-fun-tickets/progress.md`; dispatches 17): phase branch `phase46-fun-tickets` **956154d** (T1, T2, T14, T18a, T3, T10 merged; the documented Alembic parity red persists until T4). **T4** Approved 0/0/0, rebased d34c8f5 (fix 61 pick + the revision `0009_phase46_fun_tickets` on `0008_positions_open_fill`; `ix_gap_outcomes_order` not built), **full suite running** (`t4-full-d34c8f5.log`, expected fully green); then ff-merge and **wave 4** (T5 grading, T6 builder, T9 placement, T13 Floor; all opus; briefs `task-{5,6,9,13}-brief.md` written with headers and the carried T3 Minors) three at a time under the ceiling. Plan annotations owed: T10 "five LAN settings"; login page in the static rules to T15/T16. LAN files after T10 reaches main (user). Heads-up: main's fix 57 touched `harness/parlay/build.py` (`_POOL`); the phase-to-main merge resolves it.
- **phase 6D** (ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`; journal 186; dispatches 6): phase branch `phase6d-sustained-evaluation` **24fcc1c** (T1 fix 51 and T2 fix 46 complete). **T3** committed 3a32df0 on `phase6d-t3-exclusions`, opus review in flight (resumed 07:47 CT); then suite, ff-merge; **T4** (opus, the spine) ready after T3 merges; T5-T8 serial after it; T9 after T3; T10 last. Revision `0009_phase6d_sustained_eval` (27 chars: `alembic_version.version_num` is String(32)); later merger of 4.6/6D/6B renumbered at merge (D9).
- **6C**: planned/partial; Mon 09:00 CT diagnostic report due (wakeup 84208e55 08:57 CT); ruling 6: the closure names both funnel units as 6D's.
- **Qwen package**: D1-D6 approved (ruling 8); nothing activates before 6B's integration round.

## Pending results / subprocesses

- Agents in flight: 6B T7 round 1 (opus resume), 6D T3 review (opus resume), fix 57/58 resolution re-review (sonnet resume), fix 57/58 round-2 re-review (haiku). Implementer slots 1/3 (T7's round).
- Suites running: fix 57/58 (`harness_test_fix_20260914_clock_ghosts`, 180c974), 4.6 T4 (`harness_test_phase46_t4_revision`, d34c8f5). Next: 6D T3 after its verdict; 6B T7 after its round.
- Wakeups (CronList-verified at 05:0x CT): 84208e55 Mon 08:57 CT (Monday duties, 6C report). Durable reminder `sports-reminder-2026091401` 08:30 CT. No other one-shot armed.
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), phase6b_t6_dirty_time, fix_20260914_clock_ghosts, phase46_t4_revision, phase6b_t7_population, phase6d_t3_exclusions. Revoked today: fix_20260914_partition_weeks, fix_worker_credentials_mask, fix_20260914_scratch_url, fix_20260914_empty_snapshot, phase46_t1_policy, phase46_t2_odds_props, phase46_t14_tokens, phase46_t18a_market_defs, phase46_t3_players, phase46_t10_auth, fix_20260913_recorder_peak, fix_20260914_pipeline_reload, phase6d_t1_checks_window, phase6d_t2_rfq_yield.
- Worktrees: phase6b-repair-execution, phase6b-t6-dirty-time (removable), phase6b-t7-population, phase46-t4-revision, phase6d-t3-exclusions, fix-20260914-clock-ghosts, plus the preserved recovery ones (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke). Removed today: every merged task worktree of 4.6 (T1, T2, T3, T10, T14, T18a), 6D (T1, T2), fixes 49/60/67.

## Evidence receipts

- Runtime a8d8fd3; health 05:42 CT `status: ok`; verify 185+189 PASS (settle run 186 ok, 42 markouts; executor clean 66 min).
- Main release receipt: a7e86cc (= 5395ac9 code) 3,3xx passed / 6 xfailed / 1 deselected, pristine, exit 0, release tree b1d50f04… (`main-full-5395ac9.log`). The next release's receipt is fix 57/58's rebased branch suite.
- Phase 6B: f9ed9d4 3,302 / 1 xfailed. Phase 4.6: 956154d suites green but for the documented parity case. Phase 6D: 24fcc1c pristine, exit 0.
- Free space `/srv/sports-harness` 14 % used (05:42 CT); database 121.9 GB, growth 16.9 GB/day, 28 days to the housekeeping ceiling (6E storage proposal).

## Counters and gates

- CT day Sep 14: 45 dispatches. Unit counters: phase 6B 8; hotfix batch 57/58+61+67 9; hotfix fix-49 4 (closed); phase 4.6 17; phase 6D 6; plan-next 6D 8 of 12 (closed); verify 185/189: 0. Failed deploys today: 0. Fix rounds open: 6B T7 round 1. Re-dispatches: T6 review 1/3, fix-49 r3 1/3, T2 1/3. Rate-limit pauses: 1 (06:07-07:47 CT).
- Gates: none open (the gate-criterion exclusion was reverted before it could become one).

## Deadlines and open acceptance

- 08:10 CT first daytime tick (pricing coverage, limits); 08:30 CT durable reminder; 08:57 CT Monday duties and the 6C 09:00 report; 08:58 CT fix 49 row; the release as soon as fix 57/58 merges; verify after it. Deploy window until ~17:35 CT. 6E cold-start (user LUKS window) and corrected-workload windows open. 6F awaits the user's amendment; gate boundary waits for 6F.
- User decisions pending: rows 62/63's rows and unbounded checks; row 64's check; gate criterion 8's unsynced-run exclusion (roadmap TODO 06:02 CT).
- User-side (ruling 9 order): backup age key copy today; spend-limited Anthropic workspace; Kalshi rotation; LUKS window for 6E. 4.6: owner password hash and TLS files after T10 merges to main. DraftKings: the page's general section would fill the four yardage/receptions families.

## Risks and lessons

- A blanket reader sweep can silently redefine a pinned gate criterion; only the golden-literal test caught it. Any future exclusion pass names the criterion family as out of scope up front.
- The subscription limit cuts every in-flight worker at once; resumes from transcripts work (SendMessage), but a dead controller turn cannot arm the wakeup: record the pause after the fact and re-derive due items from the clock.
- Rebase immediately before every ff-merge, in one command; a merge script must check the suite's exit code. Shard layout follows durations (row 67). Review packages: `git -c diff.algorithm=histogram diff`.
- After an unclean reboot this box can boot with a stale clock; fix 57 makes the recorder refuse an undisciplined clock: `timedatectl` before each release.
- Controller shell: quoted heredocs only for prose; `scripts/omarchy.sh sql` reads SQL from stdin; column names: `exec_heartbeat.last_loop_at`, `fills.filled_at`, `check_results.check_name/ts`, `job_runs.notes` jsonb, `game_score_events.raw_id` -> `raw_responses`. `.superpowers/` is gitignored. Read `date` before stamping a ledger line. `scripts/test-suite.py` splits TEST_ARGS on spaces (no quoted `-k`).
- The recorder process hosts the settle job (interval 3,600 s from process start). The normalizer backlog remains a separate open defect (6D).
