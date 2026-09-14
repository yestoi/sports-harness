# Autopilot checkpoint

Updated 2026-09-14 02:54 CT (07:54Z) by controller session sports-80 (session_01TD2U76khHEb44dj9AJhD4K) in `/home/trey/dev/sports` on Omarchy (lock held; launched via `start`, tmux `sports-autopilot`). Last journal entry: 175 (176 pending: the credentials-mask merge). Every time below is a clock reading.

## Outage (journal 175)

Hard crash Sun 2026-09-13 15:32 CT (nothing logged first); boot Mon ~02:21 CT with the RTC at 2020-01-01, clock advanced to the timesync file (Sun 15:31:17 CT), NTP corrected 32 s later. Recording gap Sun 15:30 CT to Mon 02:21 CT. Runs 14485/14486 mis-stamped (carried fix 57, user ruling on the rows); 15 ghost `running` rows (fix 58); the 19:07/21:09 CT Sunday passes and the 6C 19:00 CT discriminator missed. Journal 174 + the 15:33 checkpoint were committed one minute before the crash.

## Active units

- **hotfix fix-59** (calendar defect, tests only; ledger `.superpowers/sdd/hotfix-2026-09-12-omarchy/progress.md`): every full suite fails 67 cases with `CheckViolation: no partition ... (ts) = (2026-09-09 ...)` since the ISO week rolled to 38; worktree `../sports-wt/fix-20260914-partition-weeks` from main 3b79d79, DB `harness_test_fix_20260914_partition_weeks` granted; implementer `fix-59` (haiku, brief `fix-59-brief.md` with the complete code) dispatched 02:5x CT, report `results/fix-59-report.md`. Next: sonnet review, suite, ff-merge; then rebase the credentials-mask and T1 branches onto main and rerun their suites. **Blocks every receipt until merged.**
- **user-directed: credentials mask** (journal 176 pending): branch `fix-worker-credentials-mask` 0bf5f3c on worktree `../sports-wt/fix-worker-credentials-mask` (base 3b79d79): `scripts/worker-shell.py` `overlays()` stops at `site-packages`; skill test added; unit tests green. Its suite (`credmask-full-0bf5f3c.log`) failed only on fix 59's defect (67 CheckViolation). Next: rebase onto main after fix 59, rerun the suite, ff-merge, verify `import anthropic` inside the sandbox live, journal 176.
- **hotfix fix-49 round 3b** (row 49; same ledger): the first round-3 implementer died in the crash after 71 min; its diff is preserved in worktree `../sports-wt/fix-20260913-recorder-peak` (branch `fix-20260913-recorder-peak`, base fdc351d; DB `harness_test_fix_20260913_recorder_peak` granted) and saved as `fix-49-round3-partial-20260913-1532.diff`; fresh implementer `fix-49-r3b` (opus) dispatched 02:43 CT (90 min timeout, chase 04:13 CT), report `results/fix-49-round3-report.md`. Then opus review, rebase onto main, suite, merge, next release. A third FAIL of the row after that deploy is a gate.
- **phase 6B** (plan `docs/superpowers/plans/2026-09-11-phase6b-repair-execution.md`, ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`): phase branch `phase6b-repair-execution` 86bb8c7 (T10, T1-T5 accepted). **T6 in review (again)**: bae1e66 on `phase6b-t6-dirty-time` (worktree kept; DB granted; report `task-6-report.md`, five deviations); package `review-86bb8c7..bae1e66.diff`; reviewer `rev-6b-t6-r2` (opus) dispatched 02:43 CT (30 min, chase 03:13 CT), report `results/task-6-review.md`. Next: rulings, fix rounds if any, branch suite after the verdict (after fix 59), ff-merge into the phase branch. Renumber `0008_phase6b_execution` -> `0009` at 6B's merge. Remaining: T7-T9, T11, T12, final review.
- **phase 4.6** (plan `docs/superpowers/plans/2026-09-13-phase4.6-fun-tickets.md` rev 2 amended; ledger `.superpowers/sdd/2026-09-13-phase4.6-fun-tickets/progress.md`): phase branch `phase46-fun-tickets` 17113eb. **T1 approved**, 87187f0 on `phase46-t1-policy`; its rerun suite (`t1-full-87187f0-r2.log`) failed only on fix 59's defect; rerun after the fix (rebase onto main first). **T2 in progress (again)**: `impl-46-t2-r2` (opus) dispatched 02:43 CT on `phase46-t2-odds-props` (base 17113eb; DB granted; the first implementer's 3-minute diff preserved and saved as `task-2-partial-20260913-1532.diff`), report `results/phase46-task-2-report-r2.md` (90 min, chase 04:13 CT). T10, T14 not started.
- **6D plan-next** (ledger `.superpowers/sdd/plan-next-phase6d/ledger.md`): plan committed bd59a37 on `plan6d-sustained-eval` (writer output; report lost in the crash); **plan review in progress**: `plan-review-6d` (opus) dispatched 02:46 CT (45 min), brief `plan-review-brief.md`, report `results/plan-review-6d.md`. Then rulings, amend, commit, roadmap `planned`, journal. Dispatches 5 of 12; wall clock 119 min used (outage excluded), ceiling 4 h.
- **deploy**: runtime ebf0953; main 3b79d79 ahead by batch C's dashboard code (940fd6d), fix 56 (tests) and fix 56 second row (positions view + revision 0008: **full release**). Game window open now (verify.md counts 0/0/0; next kickoff Mon 19:15 CT). Main's release receipt: 440095d's (`test-harness_test_fix_20260913_positions.json`, release tree unchanged by the docs commits). Deploy next, before the launcher/tests-only merges (which change no image behaviour).
- **verify**: after the deploy: full contract with the walkthrough (day's first verify; dashboard diff); judge the deferred items (6C (i)/(iii), fix 50, fix 45 Mon 06:44 CT, fix 52 daytime, walk item 19 and the Study half) and the anomalies of journal 175 (settle `degraded`, `yes_dollars_fp` exception).
- **6C**: planned/partial; the Sun 19:00 CT discriminator was not verified (outage); Mon 09:00 CT diagnostic report due (wakeup 08:57 CT).
- **Qwen package**: review record only; D1-D6 and the budget decision await the user.

## Pending results / subprocesses

- Agents in flight (5): fix-49-r3b (opus, 02:43 CT), impl-46-t2-r2 (opus, 02:43 CT), rev-6b-t6-r2 (opus, 02:43 CT), plan-review-6d (opus, 02:46 CT), fix-59 (haiku, 02:5x CT). All three test-running workers were told (SendMessage) that the CheckViolation is environmental.
- Suites: none running (both finished 02:49-02:51 CT with fix 59's 67 failures; receipts exit 1, not release receipts).
- Wakeups: 84208e55 Mon 08:57 CT (Monday duties, 6C 09:00 report; verified by CronList at creation). Durable reminder `sports-reminder-2026091401` 08:30 CT.
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), harness_test_phase46_t1_policy, harness_test_phase46_t2_odds_props, harness_test_phase6b_t6_dirty_time, harness_test_fix_20260913_recorder_peak, harness_test_fix_worker_credentials_mask, harness_test_fix_20260914_partition_weeks. Revoke each after its branch's last suite.
- Worktrees to keep: phase6b-repair-execution, phase6b-t6-dirty-time, phase46-t1-policy, phase46-t2-odds-props, plan6d-sustained-eval, fix-20260913-recorder-peak, fix-worker-credentials-mask, fix-20260914-partition-weeks, plus the preserved recovery ones (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke). The user's interactive session herdr-autopilot-3a is not a controller.

## Evidence receipts

- Runtime ebf0953 all five app services, schema 0007; health 02:39 CT `status: ok`, credits 4,899,092 of 5,000,000; preflight `evidence/2026-09-14-preflight-0239.txt` (`paper posture intact`).
- Main release receipt: 440095d 3,308 passed / 6 xfailed / 1 deselected, pristine (`test-harness_test_fix_20260913_positions.json`); valid for main 3b79d79 (docs commits only since).
- Phase 6B: T5 86bb8c7 3,286/2 pristine (Sunday). No receipt is obtainable after 00:00Z Monday until fix 59 merges.

## Counters and gates

- CT day Sep 14: 5 dispatches (T6 review, fix-49-r3b, T2-r2, 6D plan review, fix-59). Unit counters: phase 6B 3; hotfix fix-49 2; hotfix fix-59 1; phase 4.6 6 (impl 3, review 2, writer 1); plan-next 6D 5 of 12. Failed deploys today: 0. Fix rounds: none open. Re-dispatches: T6 review 1/3, fix-49 r3 1/3, T2 1/3 (crash, same tier).
- Gates: none open. Rate limit: none.

## Deadlines and open acceptance

- Mon 09:00 CT 6C diagnostic report (wakeup 08:57 CT); Monday duties (alias pass 09:30, replay 09:45, bundle, daily line). Deploy window open until ~17:35 CT Monday (NFL kickoff 19:15 CT). 6E cold-start (user LUKS) and corrected-workload windows open. 6F awaits the user's amendment. 48/6D: normalizer backlog unchanged. Fix 49: round 3b in progress; the 6 h/500 MiB row is re-judged on the next build.
- 4.6 user-side: owner password hash and TLS files pending; User-side TODO 2026-09-14 (CMOS battery, time-sync ordering, ruling on runs 14485/14486).

## Risks and lessons

- Any full suite run after 00:00Z Monday fails 67 cases until fix 59 merges (fixture weeks); next Monday's rollover would have hit the 09-18 fixtures the same way; the fix builds ISO weeks 36-45.
- After an unclean reboot this box can boot with a stale clock; check `journalctl -b 0 | grep 'Initial clock synchronization'` and rows stamped between boot and the sync before trusting post-reboot data (fix 57 adds the in-app guard).
- `scripts/omarchy.sh sql` reads SQL from stdin (heredoc); `scripts/omarchy.sh logs` can hang past 60 s (use `docker logs --since`). `scripts/testdb.py` needs the venv python.
- The sandbox anthropic import failure was the launcher masking `anthropic/lib/credentials` (fixed on branch, journal 176), not an environmental limit.
- Release receipts match by release tree; a code merge moves it. Suites lock per test database; provision the indisvalid grant before a new branch database's first full suite.
- The recorder process hosts the settle job; a six-hourly `report_wtd` render stepped RSS by ~370 MiB (fix 49 round 3). Do not resurrect NAS restart thresholds. The normalizer backlog remains a separate open defect (6D).
