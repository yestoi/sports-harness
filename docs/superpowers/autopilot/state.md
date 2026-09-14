# Autopilot checkpoint

Updated 2026-09-14 09:3x CT by controller session sports-80 (session_01TD2U76khHEb44dj9AJhD4K) in `/home/trey/dev/sports` on Omarchy (lock held; launched via `start`, tmux `sports-autopilot`). Last journal entry: 194 (191 deploy refusals and the nightly by hand; 192 deploy 6b9b8d0 app-only healthy; 193 verify 6b9b8d0 PASS with carried data rows; 194 operate: W37 report, gate, daily line, bundle local). Main **ad90885** = fix 66 code on cf47b94 docs (fix 66 ff-merged 09:31 CT, receipt at ad90885 release tree ce0ab536…); runtime **6b9b8d0**; **deploy trigger open** (fix 66) and held for the alias branch so both ship as one **full** release (aliases force full) before ~17:35 CT. GitHub origin `yestoi/sports-harness`: main and every active branch pushed at this checkpoint. Every time below is a clock reading.

## Outage (journal 175, 178) and the pause (190)

Hard crash Sun 15:32 CT; boot Mon ~02:21 CT with a stale clock. Runs 14485/14486 handled (fix 57 deployed in 6b9b8d0; 14485 annotated by hand in 192). Rate limit 06:07-07:47 CT. The app-backup sidecar's stale-clock sleep fires a second nightly at about 14:20 CT (harmless).

## Active units

- **hotfix batch (ledger `.superpowers/sdd/hotfix-2026-09-12-omarchy/progress.md`)**: fix 66 **merged ad90885** (branch `fix-20260914-book-errors`, opus review approved 0/0/5; suite rerun at ad90885 after the `docs/reports/` rebase invalidated the 1351366 receipt). Alias pass `fix-aliases-20260914` approved, rebased onto ad90885 as **50f4091**, **full suite running** (`aliases-full-50f4091.log`, db `harness_test_fix_aliases_20260914`); on green and pristine: ff into main, then `make plan-release-omarchy` (full) and `make deploy-omarchy` (foreground; `timedatectl` first; nightly row 34 from 08:0x CT satisfies the precheck until ~10:00 CT Tue). Game window now: 0/0/0 (09:32 CT); NFL kickoff 19:15 CT closes deploys from ~17:35 CT. Nothing else may land on main until the alias ff: plan files and `docs/reports/` are inside the release tree.
- **Carried fixes**: 49 (deployed; 6 h/500 MiB row at 14:13 CT), 62/63 (one defect, code fix to 6B's integration round; rows and unbounded checks a user decision), 64 (source behaviour; disposition a user decision), 65 (after T10 reaches main), 68 (Pinnacle absent upstream since Sun 20:32Z; observation; re-judge 14:13 CT and Tue 09:31 CT, user-side if persistent, gate 5). Closed today: 45, 52, 57/58, 59, 60, 61, 66 (merged, deploy pending), 67.
- **phase 6B** (ledger `.superpowers/sdd/2026-09-11-phase6b-repair-execution/progress.md`): phase branch `phase6b-repair-execution` **e67bd63** (T10, T1-T7). **T8** (order 157 audit) at **97d639e** on `phase6b-t8-audit`: round-1 re-review APPROVED 09:2x CT, **full suite running** (`t8-full-97d639e.log`, db `harness_test_phase6b_t8_audit`); then ff-merge, then the real-capsule `audit-order` run for order 157 by the controller in a quiet window (read `verdict` first), then **T9** (brief `task-9-brief.md` extracted, header not yet written; needs a slot), T11, T12, integration round (rows 62/63's NO_WATCHER cutoff; manifest gate vs hypothesis (ii) is a user/spec decision), final review. Renumber `0008_phase6b_execution` at merge.
- **phase 4.6** (ledger `.superpowers/sdd/2026-09-13-phase4.6-fun-tickets/progress.md`; dispatches 27): phase branch `phase46-fun-tickets` **2a3a5f5** (T1, T2, T14, T18a, T3, T10, T4, T5, T6 merged; fix 67 pick dropped at the main merge). **T9** (placement) approved after round 1, rebased onto 2a3a5f5 as **14c23a5**, **full suite running** (`t9-full-14c23a5.log`, db `harness_test_phase46_t9_placement`) then ff-merge. **T13** (Floor, opus, since 08:57 CT; chase 10:27 CT) and **T7** (parlay build stage, sonnet, since 09:24 CT; chase 10:24 CT) in flight; then T8, T11, T12, T15/T16, the final review. Carried to integration: `ResearchClient.call` `timeout_s`; `cli.py parlay build` prop lookup and `implied hold None` and its stale comment (T9 MI-6); T9's IM-3 re-grade promise and IM-4 week key; `_newest_prop_point` vs `newest_dk_prop_price`; T11 reads ParlayPlacement for idempotent 200; the placement-free correction refusal surfaces as `correction_not_allowed` in T11. Plan annotations owed (after the alias ff, they touch the release tree): T10 five LAN settings; login page to T15/T16; addendum §9 width 400; `odds_prop_snapshot_id`; the yes-only watch item.
- **phase 6D** (ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`): phase branch `phase6d-sustained-evaluation` **6db5e0d** (T1-T3 complete). **T4** committed **6e13250** on `phase6d-t4-coverage`; opus review in flight since 08:46 CT, chased 09:19 CT, ruled a bounded extension to **09:55 CT** (its live pytest on the recorder memory tests is slowed by five concurrent suites); then suite, ff-merge; T5-T8 serial after it. **T9** (policy, opus, since 09:12 CT; chase 10:42 CT) in flight; T10 last.
- **6C**: planned/partial; Monday duties done in 194 (W37 report `docs/reports/2026-w37.md`, 2f0e068).
- **Qwen package**: D1-D6 approved (ruling 8); nothing activates before 6B's integration round.

## Pending results / subprocesses

- Agents in flight (ListAgents 09:32 CT): 6D T4 review (opus, 08:46), 4.6 T13 (opus, 08:57), 6D T9 (opus, 09:12), 4.6 T7 (sonnet, 09:24). Implementer slots 3/3 (T13, 6D T9, T7); queued for a slot: 6B T9 (after T8's ff), 4.6 T8.
- Suites running (nohup; no notification — read the receipts at boundaries): aliases at 50f4091, 4.6 T9 at 14c23a5, 6B T8 at 97d639e. Then: 6D T4 after its verdict.
- Wakeups (CronList 09:3x CT): ab592c57 09:33 CT (suite receipts; fix 66 part already done); 15a2500f Mon 14:13 CT (fix 49's 6 h row, row 68 re-judge, sidecar's late nightly); 3b75b6c6 Tue 09:31 CT (row 68 re-judge, Tuesday duties/futures check).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+_a/_b/_s), phase46_t9_placement, phase46_t13_floor, phase46_t7_stage, phase6b_t8_audit, phase6d_t4_coverage, phase6d_t9_policy, fix_aliases_20260914. Revoked at this checkpoint: fix_20260914_book_errors, phase46_t6_builder, phase6d_t3_exclusions.
- Worktrees: phase6b-repair-execution, phase6b-t8-audit, phase46-t9-placement, phase46-t13-floor, phase46-t7-stage, phase6d-t4-coverage, phase6d-t9-policy, fix-aliases-20260914, plus the preserved recovery ones (fix-45-raw-events-index, fix-48-pricing-stage-order, recovery/*, restart-worker-smoke). Removed at this checkpoint: fix-20260914-book-errors, phase46-t6-builder, phase6d-t3-exclusions.

## Evidence receipts

- Runtime 6b9b8d0 (receipt releases/20260914T131200Z-6b9b8d0, healthy 13:13:26Z); verify 193 PASS on every deterministic row but the carried data rows (60/62/63 behind Pulse's `check_fail 3`), the 28-day storage projection (6E) and row 68.
- Main ad90885: fix 66's branch receipt at ad90885, 3,342 passed / 6 xfailed / 1 deselected, pristine, exit 0, release tree ce0ab536… (`fix66-full-ad90885.log`) = main's release tree. Alias receipt at b27e1be (3,3xx, pristine, exit 0) is superseded by the rerun at 50f4091.
- Phase 6B: e67bd63 3,311 passed, 0 xfailed (T7's runner). Phase 4.6: 2a3a5f5 (T6's suite, green and pristine, ff-merged 09:2x CT). Phase 6D: 6db5e0d pristine, exit 0.
- Free space `/srv/sports-harness` 14 % used (05:42 CT); database 121.9 GB, growth 16.9 GB/day, 28 days to the housekeeping ceiling (6E storage proposal).
- Bundle `~/.cache/sports-harness/bundles/sports-2026-09-14.bundle` (44,024,501 bytes, verified) local only; NAS key refused (User-side TODO 09:05 CT).

## Counters and gates

- CT day Sep 14: 65 dispatches (61 at 09:0x CT, then 6D T9, 6B T8 re-review, 4.6 T7, 4.6 T9 re-review). Unit counters: phase 6B 11; hotfix 57/58 batch 9 (closed); fix 66 batch 2; alias pass 2; phase 4.6 27; phase 6D 9; verify 193: 1; operate 194: 0. Failed deploys today: 0. Fix rounds open: none (T9 round closed; T8 round closed). Re-dispatches: T6 review 1/3, fix-49 r3 1/3, T2 1/3. Rate-limit pauses: 1 (06:07-07:47 CT).
- Gates: none open.

## Deadlines and open acceptance

- Full release (fix 66 + aliases) after the alias suite (~09:45 CT), before ~17:35 CT; then verify with the full contract (tape gap and first recovered snapshot recorded). 14:13 CT fix 49 row and row 68 re-judge. 6E cold-start (user LUKS window) and corrected-workload windows open. 6F awaits the user's amendment; gate boundary waits for 6F.
- User decisions pending: rows 62/63's rows and unbounded checks; row 64's check; gate criterion 8's unsynced-run exclusion (roadmap TODO 06:02 CT); row 68 if Pinnacle is still absent Tue 09:31 CT (gate 5); the NAS key for the bundle (TODO 09:05 CT); 6B T8's manifest gate vs hypothesis (ii).
- User-side (ruling 9 order): backup age key copy today; spend-limited Anthropic workspace; Kalshi rotation; LUKS window for 6E. 4.6: owner password hash and TLS files after T10 merges to main. DraftKings: the page's general section would fill the four yardage/receptions families.

## Risks and lessons

- A blanket reader sweep can silently redefine a pinned gate criterion; only the golden-literal test caught it. Any future exclusion pass names the criterion family as out of scope up front.
- The subscription limit cuts every in-flight worker at once; resumes from transcripts work (SendMessage), but a dead controller turn cannot arm the wakeup: record the pause after the fact and re-derive due items from the clock.
- Rebase immediately before every ff-merge, in one command; a merge script must check the suite's exit code. Shard layout follows durations (row 67). Review packages: `git -c diff.algorithm=histogram diff`.
- The release tree excludes only `docs/superpowers/autopilot/`: a `docs/reports/` or plan commit on main invalidates every pending branch receipt after rebase. Land such docs after the pending fix branches ff, or accept one rerun.
- Never `git stash pop` in a worktree: the sandbox masks are not controller-side dirt, and the repo's stash list holds preserved recovery entries.
- The app-backup sidecar computes its sleep from the clock at start: after a stale-clock boot the nightly slips and the deploy's backup precheck refuses; the runbook's fallback (`dump.sh nightly` by hand) is the fix, and a refused candidate image tag means the release SHA must move (a journal commit).
- The controller's nohup suites give no notification: read the receipt (`test-state/test-<db>.json`) at boundaries. Five concurrent suites stretch a 10-minute suite to 12+ and a reviewer's scoped run several-fold.
- After an unclean reboot this box can boot with a stale clock; fix 57 makes the recorder refuse an undisciplined clock: `timedatectl` before each release.
- Controller shell: quoted heredocs only for prose; `scripts/omarchy.sh sql` reads SQL from stdin; column names: `exec_heartbeat.last_loop_at`, `fills.filled_at`, `check_results.check_name/ts`, `job_runs.notes` jsonb, `game_score_events.raw_id` -> `raw_responses`. `.superpowers/` is gitignored. Read `date` before stamping a ledger line. `scripts/test-suite.py` splits TEST_ARGS on spaces (no quoted `-k`).
- The recorder process hosts the settle job (interval 3,600 s from process start). The normalizer backlog remains a separate open defect (6D).
