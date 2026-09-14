# Autopilot checkpoint

Updated 2026-09-14 17:04 CT (22:04Z) by controller session sports-5e (session_01K7uXN54fkXzh2teuSzb1bo) in `/home/trey/dev/sports` on Omarchy (lock held by process 718911 under flock in tmux `sports-autopilot`; a peer unlocked Claude session `herdr-autopilot-68` is alive and is not a controller). Last journal entry: 213 (199-212 record the user's fourteen 15:38 CT rulings; 213 the fix 64 merge). Main **2947d51** (docs) = **5d0012c** code (fix 64 merged); runtime still ca30ed1 until the 23:22 CT deploy wakeup (R4: NFL kickoff 19:15 CT).

## Rulings landed at resume (journal 199-212)

User-side closed: NAS key (bundle on the NAS), age key (held off-host, nag dropped), Anthropic account limits, Kalshi read scope, runs 14485/14486 stay as recorded, gate criterion 8 stays (no R1 amendment). Scheduled: 6E cold-start Tue 2026-09-15 07:00 CT (below). Open: owner password hash and TLS files after a release carries `harness owner-password-hash` (tell the user that day). Loop-side: rows 62/63 option A (checks bounded at the NO_WATCHER cutoff fix's release, fix in 6B's integration round); row 64 sonnet hotfix (correction marker + check exclusion); weekend props option (a) on `phase46-fun-tickets`; 6B manifest gate scoped to the resting interval (spec 0.16, audit.py, fixture, rerun on order 157); `parlay_slot_state` table in the 4.6 integration round; `site.web.api.espn.com` on the allowlist (path-pinned). Gate 13 authorized only for the three named predicates; gates 5/6 only for the props cadence change.

## Active units

- **hotfix `fix-20260914-score-corrections`** (row 64; journal 207): **merged to main at 5d0012c** (sonnet impl, sonnet review Approved; suite green at c5f0dc4; Minor 1 indent and Minor 2 away-score case carried). Deploy (full recipe, model change) at the 23:22 CT wakeup once the NFL window clears; then verify; roadmap row 64 closes on the verify.
- **phase 6B** (ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`; dispatches 17): phase branch `phase6b-repair-execution` **b7c6557** (T9 merged 16:4x CT). **audit-interval** (ruling 12, journal 210): spec 0.16 + code + review Minors 1-3 at **2a4f3ca** on b7c6557 (`phase6b-audit-interval`); **unsharded suite running** detached since 17:01 CT (`audit-interval-full-2a4f3ca.log`, Monitor); on green: `--ff-only` in `../sports-wt/phase6b-repair-execution`, the formal `harness audit-order` run on the order 157 capsule (JSON to `evidence/2026-09-14-audit-order-157-<HHMM>.json`), then `ORDER_157_VERDICT` and the audit document's Result section (preview: verdict `unverifiable`, hypothesis null, replay 63.92/0.00 vs recorded 38.92/0.0, 6 manifest slices, 0 in interval). **T11** (sonnet, ref a84d14769b3dd2ebd) running on `phase6b-t11-amendment` at b7c6557 since 16:4x CT (chase 18:55 CT); its unrequested full suite was killed 17:01 CT. Integration round after T11/T12: NO_WATCHER deadline bound + check bounds (journal 206), T9 Minors 2/5, audit Minors 4/5, renumber 0008.
- **phase 4.6** (ledger `.superpowers/sdd/2026-09-13-phase4.6-fun-tickets/progress.md`; dispatches 49): phase branch `phase46-fun-tickets` **8cbed57**. **T15** committed **b646a55** on `phase46-t15-ticket-ui` (opus DONE_WITH_CONCERNS: two design lines not drawable from Task 12's payload; twelfth refusal code `forbidden`); **sonnet review running** since 16:58 CT (ref aad177b88142bdb79, package `review-t15-8cbed57..b646a55.diff`, chase 17:33 CT). Integration round: `parlay_slot_state` table (journal 211), T12 N1-N3, plan annotations; T18a off Path B (ruling 14).
- **phase 6D** (ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`; dispatches 17): phase branch `phase6d-sustained-evaluation` **06e0340**. **T6** at 8f1391c: opus review **CHANGES_REQUIRED** (I1 wiring tests, I2 replay assertions, I3 `fair_age_s` deque cap 10,000, I4 no change: T10 exempts `exec.signal_to_order_ms` in verify row 12; M2/M3/M7 applied, M1/M4/M5/M6 carried to T10); **fix round 1 running** (SendMessage resume of a1051636922e6d4c8 at 16:59 CT, 45 min, chase 17:54 CT). Then scoped re-review (sonnet), suite, `git fetch . phase6d-t6-latency:phase6d-sustained-evaluation`, T7.
- **6C**: planned/partial; the deferral of two funnel units accepted (journal 184); closure must name both as delivered by 6D. **Qwen**: D1-D6 approved; nothing activates before 6B's integration round.

## Pending results / subprocesses

- Suites detached (nohup; Monitor): 6B audit-interval at 2a4f3ca (unsharded, started 17:01 CT after the slot freed). Logs in the ledger dirs; receipts under `~/.cache/sports-harness/test-state/`.
- Agents running: 6B T11 implementer (sonnet, a84d14769b3dd2ebd), 4.6 T15 reviewer (sonnet, aad177b88142bdb79), 6D T6 implementer fix round 1 (sonnet, a1051636922e6d4c8). Refs in the ledgers.
- Wakeups (CronList, session-only): 71baf905 Mon 23:22 CT (deploy-window end: fix 64 release; re-judge fix 49 and row 68), 6ff56c01 Tue 06:35 CT (cold-start prep), chases fb965f01 17:33 CT (T15 review) and 4c4d24bb 17:54 CT (T6 fix round); durable reminder unit sports-reminder-2026091501 (Tue 06:30 CT).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), phase6b_t9_rescore, phase6b_audit_interval, phase6b_t11_amendment, fix_20260914_score_corrections, phase46_t15_ticket_ui, phase6d_t6_latency. Revoked this session: phase6d_t5_budget, phase46_t12_ticket, phase46_props_weekend.
- Worktrees: phase6b-repair-execution, phase6b-t9-rescore (merged; remove), phase6b-audit-interval, phase6b-t11-amendment, fix-20260914-score-corrections (merged; remove after deploy verify), phase46-t15-ticket-ui, phase6d-t6-latency, plus the preserved recovery ones (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke).

## Evidence receipts

- Runtime ca30ed1: receipt `releases/20260914T144712Z-ca30ed1` healthy 14:49:49Z; verify 196 PASS on deterministic rows; 198: fix 49 DEFERRED, row 68 present again. Preflight this session `evidence/2026-09-14-preflight-1541.txt` (`paper posture intact`).
- Main receipt: the 6A verify block's full suite at 8f1ae26 on harness_test_main: 3,344 passed / 6 xfailed / 1 deselected, pristine, release tree = ca30ed1's.
- Phase 6B: 97d639e 3,325 passed (unsharded). Phase 4.6: 379fabb six shards exit 0, pristine, release tree d0d480e50193. Phase 6D: 10921cc 3,366 / 6 xfailed / 1 deselected, pristine.
- Free space `/srv/sports-harness` 14 % used (05:42 CT); database 121.9 GB, growth 16.9 GB/day. Bundle `sports-2026-09-14.bundle` on the NAS (44,024,501 bytes, 15:27 CT) and local.

## Counters and gates

- CT day Sep 14: 104 dispatches at 17:04 CT. Unit counters: phase 6B 17; phase 4.6 49; phase 6D 17; hotfix fix-64 2; batches closed today (57/58: 9; 66: 2; aliases: 2). Failed deploys today: 0. Fix rounds open: 6D T6 round 1. Re-dispatches: T6 review 1/3, fix-49 r3 1/3, T2 1/3. Rate-limit pauses: 1 (06:07-07:47 CT).
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
