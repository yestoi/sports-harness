# Autopilot checkpoint

Updated 2026-09-18 07:40 CT by controller session sports-ed in `/home/trey/dev/sports` on Omarchy. Last journal entry: 274 (260 was a separate user-directed session: sports.tunderwood.com behind Authelia, U9 amended, no release). Paper-only. Runtime build: **4066197** (journal 273, app-only 07:23 CT Sep 18, fix 79 print cache; 747791c full 10:04 CT Sep 16, journal 257; app-ws on 747791c). Main = the runtime; deploy trigger empty. Rollback: app-only redeploy of 1a12781 (the cache has no off switch). origin/main pushed 2026-09-16 at the user's request (journal 259; U7 pushes after phases and on Mondays); main's upstream is origin/main.

## Right now

- **Journal 262/263 (item 20)**: fix 79 released 07:23 CT (journal 273); judge 07:43 CT, tell the user; the loop-metrics FAIL may stand through the release (no twice-running ceiling); Fri NCAAF window re-read of liquidity conservation, no-post-expiry-fill and fills-per-hour as the AFTER half (BEFORE: journal 269); **a step down in fills at the release hour is an alarm to the user, not a row**.
- **Friday rulings (journal 272, evidence/2026-09-18-rulings-for-controller.md)**: item 21 coverage row FAIL waived from 2026-09-18 until the user's definition ruling (row 84 stays Watch); its steps 1-3 are 6D acceptance work (vehicle named when planned). Item 19: caps unchanged; the veto study today as a finding only (duty deferred, journaled); one-page pacing brief for the user; the daily line notes annotator/parlay refusals on the shared cap. Item 22: row 85. Item 23: row 86. Item 1: option 1, the user's (a) decrypt first; nothing dropped; label GB vs GiB.
- **Preflight Sep 17 20:48 CT** (journal 270, fresh session sports-ed): paper posture intact; NCAAF 460 and NFL 115 in progress; next kickoffs Fri 18:30 CT.
- **User decisions**: packet items 2-10 and 17 ruled and applied (journal 239/240/245). Open: item 1 (the user's (a), decide-by 2026-09-22), items 11-15 upcoming; 19-23 ruled (journal 272). Packet: https://claude.ai/artifact/4wRJo92rcU5UUU9dhMt6Z1, `reports/2026-09-15-open-decisions-packet.md`.
- **Fix rows** (`fixes.md`): Open 3 (79 print cache, release Fri; 85 `orders(intent_id)` index, Monday full release; 86 expiry cohort design read by Sat 09:00 CT), Watch 14 (84 coverage FAIL waived, journal 272; 83 swap), Closed 52.

## Order of work

Journal 224 item 17, as it stands:

1. Done: 7c3d750 (journal 233/234) and f7a1ccb (journal 237/238) released and verified; f8053c6 (fix 78 part 1) released and judged (journal 243/244).
2. Fri 2026-09-18: fix 79 release (deploy, judge, tell the user); row 86 design read (opus, read-only); item 19 finding and pacing brief; row 85 build (opus/opus; full release Mon); item 21 step 1 as 6D plan work. Ledgers under `.superpowers/sdd/hotfix-2026-09-17-executor-prints/` and new per-row directories.
3. Verify after each release: journal 219's deferred rows, item 12's exec-health windows on 0d04804, item 16's tape-gap read (journal 229), durable re-reads of rows 49 and 68, the 6B by-cause row, the `fixes.md` Watch reads (56, 70).
4. Operate: daily 09:00 CT line Thu 2026-09-17 done (journal 261; today's veto budget was spent by 09:00 CT); the storage retention proposal is written (`reports/2026-09-15-storage-retention-proposal.md`, journal 230; the user decides by 2026-09-22; nothing executed). 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (Thu 2026-09-17 19:15 CT; NCAAF 18:30 CT). The user does the LAN files (roadmap User-side TODOs). After the first full day at 300k the user runs usage.py (journal 247).

## Active units

- **Thu window (journal 267-269)**: done. 6B PASS; 6D coverage FAIL (row 84, Watch); BEFORE half recorded (0 fills since 16:38 CT, explained); NFL expiry cohort drained over four loops of 116-253 s; T18b unwatched (no card). Docket v11 items 21-23; packet appended.
- **hotfix fix 79**: merged 2cb2219 (journal 266), **released 4066197 07:23 CT** (journal 273), Layer 3 PASS 9/9; judge at 07:43 CT then close row 79, `make worktree-rm BR=fix-2026-09-17-executor-prints`, `git branch -d`; test db harness_test_fix_2026_09_17_executor_prints kept until then (grant revoked).
- **phase 6D**: on main, released b69b880 (journal 221); ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`, dispatches 30. Remaining: 6D acceptance rows at the first game window, §4 read-backs at real ticks, exec-health baseline on 0d04804 (item 12), ledger re-archive and final review, then roadmap `done`; worktree phase6d-merge-main removable after acceptance.
- **phase 4.6**: on main, released c1066b5. Remaining: T18b (watched NFL-window queries), T19 (verification rows), walkthrough items; the user's LAN files.
- **6E**: cold start done (journal 229); corrected-workload benchmark and operational acceptance remain. **6C**: planned/partial, closure via 6D (`fills.id > 1878`). **6B**: done (journal 216), §3 rows at the first game window.

## Pending results

- Agents: none. Suites: none. Wakeups: judge wakeup 07:43 CT (ScheduleWakeup); cron one-shot Fri 18:25 CT (f2b3647e, after-window read); 7674cf9e (07:27 CT) superseded by the release; durable reminders 2026091802 stands, 2026091801 consumed.
- Receipts by stage: code 33ceeb1 (fix 81); test: full suite 4,196 passed at 33ceeb1 on its branch db (receipt test-harness_test_fix_2026_09_16_recorder_sink_lag.json, release tree 3c9be15f); review: fix-81-review.md APPROVED WITH MINORS 0/0/2; merge: main 33ceeb1 09:54 CT; deploy: /srv/sports-harness/releases/20260916T145941Z-747791c (full, healthy 10:04 CT; journal 257); verify: journal 258 PASS (evidence/2026-09-16-fix81-judgement-1027.txt); fix 78 part 3: eac4414 judged PASS (journal 256, evidence/2026-09-16-fix78c-judgement-0957.txt); preflight: evidence/2026-09-15-preflight-2120.txt.
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+ shards), phase6d_merge_main, phase6d_merge_review, harness_test_fix_2026_09_15_executor_batch_2 (the Sep 16 branch databases revoked 10:00 and 10:05 CT). Older fix_20260915 databases remain until `make testdb-prune`.
- Worktrees: fix-2026-09-15-executor-batch-2 (merged, removable); phase6d-merge-main; the Mac-era fix-45/fix-48/recovery worktrees unchanged (preserved).

## Counters and deadlines

- CT day Sep 18: dispatches 0 (00:00-now); Sep 17 closed at 4 (fix-82, rev-fix-82, fix-79, rev-fix-79; timers batch closed at 2 of 12, 10:03-11:06 CT; fix 79 batch 2 of 12 from 11:10 CT); failed deploys 0; implementers running 0 of 3. Sep 16 closed at 5 dispatches, 0 failed deploys.
- Next duties: fix 79 judge 07:43 CT; the morning contract (verify) after the Thursday games; daily 09:00 CT line (from today: annotator/parlay refusals on the shared cap); the alias pass; T18b after-window read (game 115 final), then T19; item 19 veto study as a finding plus the pacing brief; row 86 design read due Sat 09:00 CT; row 85 build for Monday's full release; item 21 step 1 planning; storage retention: the user's (a) first, decide-by 2026-09-22.
- Wakeups: judge 07:43 CT; cron one-shot Fri 18:25 CT (f2b3647e, reminder 2026091802). Reminders through 2026091801 are consumed.

## Constraints

Paper-only; no live posture; no non-GET venue code; invariant 5 (the loop proposes, the user executes); no new outbound hosts; git pushes only per U7 (main and the phase branch after every phase and every Monday; never pull, rebase onto the remote, PRs or worktree-branch pushes); secrets never printed; workers sandboxed; ceilings and model rules per the skill.

Applicable rulings: the loop-metrics FAIL gated at journal 252, answered by 253 (the row reads FAIL until the part 3 re-judge); walkthrough items 14 and 18 are standing data items (journal 224 item 10); RFQ0 stays (224 item 6); rows 62/63 bounded at 05:23:44Z (224 item 2); runs 14485/14486 stay (journal 205); the batch clock ruling (journal 247).
