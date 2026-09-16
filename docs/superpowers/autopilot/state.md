# Autopilot checkpoint

Updated 2026-09-15 20:12 CT by the user-directed context-hygiene session (branch `context-hygiene-2026-09-15`; no controller running) in `/home/trey/dev/sports` on Omarchy. Last journal entry: 246. Paper-only. Runtime build: **f8053c6** (journal 243, app-only, fix 78 part 1; f7a1ccb full at 12:32 CT under it, journal 237/238). Main after the merge: f8053c6 + docs, skill scripts and the launcher (no deployable code ahead of the runtime; the release tree differs from the deployed tree by non-deployable files, so the next deploy needs a post-merge full-suite receipt). origin/main = 35f7180 (U7 pushes after phases and on Mondays).

## Resume first

- **Production is safe to leave.** Runtime f8053c6 (fix 78 part 1) healthy; no deploy in progress; main clean; nothing half-merged. The loop-metrics FAIL (p95, journal 244/246) does not gate (journal 242) and needs no operator action; the backlog drains at the weekend's expiries if nothing else ships.
- **Fix 78 part 2 is implemented and committed, not reviewed:** branch `fix-2026-09-15-executor-batch-2` at **e0c9888** (worktree `../sports-wt/fix-2026-09-15-executor-batch-2`, db `harness_test_fix_2026_09_15_executor_batch_2`, fixture grant ON). Implementer report `results/fix-78b-report.md` (110 passed scoped, 413 wider; red recorded; statement count 40/175 -> 28/28; phase metrics `exec.phase_tape_ms`, `phase_walk_ms`, `phase_batch_ms`, `phase_per_row_ms`, `per_row_n`). Deviation flagged for the reviewer: one statement per column set (at most two) and per 500 rows, not literally one per loop. Review package `.superpowers/sdd/hotfix-2026-09-15/review-fix-78b-fa2f4fd..e0c9888.diff` (891 lines). The worktree may carry an untracked `.redbak/` backup directory from the red runs: not part of the change, delete before the merge.
- **Resume order:** dispatch the opus reviewer on the package (task `rev-fix-78b`, brief `fix-78b-brief.md`; ask it to rule on the deviation), fix rounds by SendMessage, full suite on the branch's database after the rebase onto the merged main (that receipt is the post-merge release receipt, deploy.md step 2), `--ff-only`, app-only release inside the window (Wednesday daytime open; Thursday closes about 14:30 CT under R4), re-judge p95 against 7,500 twenty minutes after the restart with the phase timings, journal, close row 78 or return to the user (option (a), row 79, if the tape phase dominates).
- **The 18:40 CT wakeup fired before the stop** (journal 246): row 75 CLOSED, row 77's tape coverage 1.0 for five hours, p95 23,693 ms at 7,833 pending (worse; part 2 is the remedy). No wakeup remains; the first verify is the Wed 09:00 CT daily line plus the p95 read. Urgency: at a 14 s median loop against a 15 s period, `loops_skipped` rises overnight; ship part 2 Wednesday morning.
- **User ruling 2026-09-15 18:17 CT** (the context-hygiene review session): the user's stop at 17:34 CT suspends the hotfix batch wall-clock (3 h, started 16:59 CT); by the user's ruling of 20:12 CT ("the longer safe route") it resumes at relaunch with 35 min consumed (16:59 to the 17:34 CT stop; the controller's run to its 18:41 CT checkpoint does not count). Do not journal `ceiling` for fix 78 part 2 on that basis.
- The first wake's Orient rule 2 reads the deploy trigger with the launcher excluded (deploy.md step 1); main is not ahead in code, and the next deploy needs a post-merge full-suite receipt (fix 78 part 2's rebased suite supplies it).
- The bootstrap is three parts (`bootstrap`, `bootstrap authority`, `bootstrap operator`); fix rows are in `fixes.md`; run `context.py check` and the skill tests at preflight (preflight.md).
- **User-side:** packet item 1 (storage retention) open until 2026-09-22; everything else ruled (packet URL under Right now).

## Right now

- **Released f7a1ccb** (journal 237, full): batch C, fix 77, the row 72 batch. Verify PASS with the standing items (journal 238); rows 72, 76 (238), fix 73, row 77 (241) and row 75 (246) closed.
- **Fix 78 in two parts.** Part 1 released f8053c6 15:40 CT, judged 16:02 CT (journal 243/244: heartbeat p95 13,850 still over 7,500 at 6,387 pending). Part 2 ruled 16:57 CT (journal 245, packet item 17 option d) and in flight (Active units). Option (a) (row 79 rescan) deferred by the controller to its own hotfix if the timings say so; §0.13c untouched; the loop-metrics FAIL stands and does not gate (journal 242).
- **User decisions**: packet items 2-10 ruled and applied (journal 239/240, commit ab353cc; the four one-time edits landed). Open: item 1 (retention by 2026-09-22), item 16 (fix 78), items 11-15 upcoming. Packet: https://claude.ai/artifact/4wRJo92rcU5UUU9dhMt6Z1, `reports/2026-09-15-open-decisions-packet.md`.
- **Fix rows** (`fixes.md`): Open 1 (78 part 2, in flight), Watch 13, Closed 49 (migration: `reports/2026-09-15-context-hygiene-migration.md`); 79 and 80 are follow-ups by the user's ruling (journal 242), not hotfixes.
- Main's release tree differs from the deployed tree (f8053c6) by non-deployable files only (`.claude/`, the launcher, `CLAUDE.md`, the runbook, the spec); the deploy trigger reads empty; the next deploy needs a post-merge full-suite receipt.

## Order of work

Journal 224 item 17, as it stands at the stop:

1. Done: 7c3d750 (journal 233/234) and f7a1ccb (journal 237/238) released and verified.
2. Fix 78 part 2 (Resume first): review, suite, rebase, `--ff-only`, app-only release, p95 re-judge.
3. Verify after each release: journal 219's deferred rows (listed there), item 12's exec-health windows on 0d04804 (02:30-06:30 CT vs ca30ed1's 23:38-00:23 CT and Monday's quiet hours), item 16's tape-gap read for 23:34-23:37 CT Sep 14 (journal 229: a clean stop writes no gap row), durable re-reads of rows 49 and 68, the 6B by-cause row, the `fixes.md` Watch reads (56 dup: `positions` and order 157; 70: the first live departure).
4. Operate: daily 09:00 CT line; the Tue 09:30 CT futures snapshot check (the job runs 09:00 CT; confirm it ran after the reboot); the storage retention proposal is written (item 11; `reports/2026-09-15-storage-retention-proposal.md`, journal 230; the user decides by 2026-09-22; nothing executed). 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (Thu 2026-09-17 19:15 CT; NCAAF 18:30 CT). The user does the LAN files (roadmap User-side TODOs).

## Active units

- **hotfix fix 78 part 2**: ledger `.superpowers/sdd/hotfix-2026-09-15/progress.md`; branch `fix-2026-09-15-executor-batch-2` at e0c9888 (from fa2f4fd); implementer finished 17:32 CT; review not dispatched; next legal action: dispatch the opus reviewer (Resume first). Batch wall-clock: 35 min of 3 h consumed at relaunch (user ruling, Resume first).
- **operate (6E cold start)**: done (journal 229); 6E still needs the corrected-workload benchmark and the original operational acceptance.
- **phase 6D**: on main (ffbecd5), released b69b880 (journal 221); ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`, dispatches 30. Remaining: verify.md 6D acceptance rows at the first game window, §4 read-backs at real ticks, exec-health baseline retaken on 0d04804 (item 12), ledger re-archive and final review (docs/superpowers/reviews/2026-09-13-phase6d-* stale by 9 ledger lines), then roadmap `done`; policy comparison is the user's adoption decision; worktree phase6d-merge-main removable after acceptance. Observation for its funnel rows: the gap_outcomes drain watermark (journal 229 anomaly 2).
- **phase 4.6**: on main, released c1066b5. Remaining: T18b (watched NFL-window queries), T19 (verification rows), walkthrough items; the user's three LAN files.
- **6C**: planned/partial; closure names both funnel units as delivered by 6D; counterfactual fills separate by `fills.id > 1878` (spec 0.17).
- **6B**: done (journal 216); verify §3 rows judged at the first game window.

## Pending results

- Agents: none running (fix-78b implementer aef7149b7435d1aa1 finished 17:32 CT; its result is committed as e0c9888). Suites: none. Wakeups: none (12:46, 13:40 and 18:40 CT consumed; journal 241/246).
- Receipts by stage: code e0c9888 (fix 78 part 2, unreviewed); test: latest full-suite receipts (~/.cache/sports-harness/test-state/) fix-20260915-dirty-intervals 0d04804 (exit 0, pristine, release tree = deployed tree), phase6d-merge-main ffbecd5, main e17d0f5, and none for the post-merge tree (Right now); review: pending (`rev-fix-78b`); merge: main f8053c6 + docs; deploy: /srv/sports-harness/releases/20260915T203705Z-f8053c6 (app, healthy; journal 243); verify: journal 244/246 (loop-metrics FAIL; everything else PASS or closed).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+ shards), phase6d_merge_main, phase6d_merge_review, harness_test_fix_2026_09_15_executor_batch_2. The four earlier hotfix databases' grants were revoked 09:49 CT (databases remain until the next `make testdb-prune` or the user's cleanup). harness_test_fix_20260915_{row72,batch_c,ws_seq_ack} remain until `make testdb-prune`; the 18:41 CT checkpoint recorded their grant both as absent and as ON (revoke after their last suite): read the grant before relying on either.
- Worktrees: fix-2026-09-15-executor-batch-2 (Active units); the older phase worktrees unchanged.

## Counters and deadlines

- CT day Sep 15: dispatches 23 (4 carried + batch A 4 + fix 73 2 + batch B 2 + fix 74 2 + row72 2 + batch C 2 + fix 77 2 + fix 78 3; fix rounds by SendMessage are not dispatches), failed deploys 1, implementers running 0 of 3.
- Next duties: daily 09:00 CT line Wed 2026-09-16; p95 re-judge 20 min after part 2's restart; 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (Thu 2026-09-17 19:15 CT; NCAAF 18:30 CT); the R4 window closes Thu about 14:30 CT; storage retention decision is the user's by 2026-09-22.
- Wakeups: none armed. Reminders under `~/.cache/sports-harness/reminders/` through 2026091510.

## Constraints

Paper-only; no live posture; no non-GET venue code; invariant 5 (no DROP/RENAME/TRUNCATE/DELETE/compaction/retention by code or hand; the loop proposes, the user executes); no new outbound hosts; git pushes only as U7 directs (main and the current phase branch after every phase and every Monday; never pull, rebase onto the remote, open PRs or push task worktree branches; creating a remote stays gate 8); secrets never printed; workers sandboxed without production access; ceilings and model rules per the skill.

Applicable rulings: the loop-metrics row's FAIL twice running is answered while fix 78 part 2 is in flight (journal 242); walkthrough items 14 and 18 are standing data items exempt from the twice-running clause (journal 224 item 10); RFQ0 stays (journal 224 item 6); rows 62/63's checks are bounded at 05:23:44Z (journal 224 item 2); runs 14485/14486 stay as recorded (journal 205).
