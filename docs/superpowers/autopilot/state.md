# Autopilot checkpoint

Updated 2026-09-15 09:53 CT (14:53Z) by the post-reboot controller session (tmux `sports-autopilot`, flock held; PID 6734) in `/home/trey/dev/sports` on Omarchy. Last journal entry: 234. Paper-only. Runtime build: **7c3d750** (full release 09:37:47-09:41:28 CT: hotfix batch A, hotfix batch B, fix 74; journal 233; verify 234 PASS with standing items and two deferred re-reads). Main: 7c3d750 + docs. origin/main = 35f7180 (U7 pushes after phases and on Mondays).

## Right now: released and verified; three deferred reads pending; next hotfix batch (rows 75, 76) then the row 72 batch

- **Released 7c3d750** (journal 233): batch A (items 2, 3, 4, 8, 13, 14 + review I-1 bound + the rescore test rewrite), batch B (item 9), fix 74. All four branch worktrees removed, branches deleted, fixture grants revoked. Ledger `.superpowers/sdd/hotfix-2026-09-15/progress.md`.
- **Deferred reads** (journal 234): 10:15 CT executor `p95_loop_ms` (7,500 ms row; 8,039-10,824 ms right after the restart, 4,026-4,213 ms pre-release; `exec_capacity` skips 1,148 / 2 h rising, `nw_pending` 2,728) and fix 74's between-calls `usd_reserved = 0` read (row 74 closes on it), plus the sid-2 gap episodes (four in 08:04-09:05 CT at 15-minute spacing); **11:45 CT** fix 73's closing read on the first post-release settle (due about 10:40 CT; `report_wtd` runs first for about an hour).
- **New carried fixes**: row 75 (`report_wtd` streams past the settle budget: 3,518 s on run 203; runs 202 and 209 interrupted; opus impl/review, own batch after the 11:45 CT read) and row 76 (app-serve's `make_snapshot_engine` sends no `application_name`; sonnet; rides row 75's batch).
- **Row 72 batch** (revision 0013 `orders.nw_executor_version`, the Amendment 6 sub-population sentence; opus impl/review; a full release of its own) after the row 75/76 batch. Narrowed invariant reads 0 / 0 on 762bde4 and 7c3d750.
- **User-side decisions recorded today**: extend amendment 0.18 to `order_rescores`? (roadmap TODO, journal 232); the futures budget sentence in verify.md (200 vs the spec's 500); the storage retention proposal (by 2026-09-22, journal 230).

## Order of work (journal 224 item 17; batch A in flight)

1. Done: 7c3d750 (batch A + batch B + fix 74) released and verified (journal 233/234).
2. Hotfix batch B (item 9's fix 71 narrowing: application_name per service, drain by listed pid and stopped-service names, partition children as bulk, healer lock_timeout, dump-in-progress refusal); opus impl, sonnet review; own release.
3. Row 72 batch (own release, full recipe: revision 0013 `orders.nw_executor_version`, Amendment 6 sub-population sentence, verify.md Layer 2b narrowed queries read 0/0 against evidence/2026-09-15-row72-ids.txt); opus impl, opus review.
4. Verify after each release: journal 219's deferred rows (fix 64's check row, the two cutoff-bounded checks now at 05:23:44Z, `intents_without_order_or_skip`, the 01:00-08:00 open-interval rule, c1066b5-era orphan intents), item 12's exec-health windows on 0d04804 (02:30-06:30 CT vs ca30ed1's 23:38-00:23 CT and Monday's quiet hours), item 16's tape-gap read for 23:34-23:37 CT Sep 14 (journal 229 offers the reading: a clean stop writes no gap row), durable re-reads of rows 49 and 68, the 6B by-cause row, fix 73's closing read (next settle run `ok`).
5. Operate: daily 09:00 CT line; Tue 09:30 CT futures snapshot check (the job runs 09:00 CT; confirm it ran after the reboot); the storage retention proposal is written (item 11; `reports/2026-09-15-storage-retention-proposal.md`, journal 230; the user decides by 2026-09-22; nothing executed). 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (Thu 2026-09-17 19:15 CT; NCAAF 18:30 CT). The user does the LAN files (roadmap TODO line 567).

## Active units

- **operate (6E cold start)**: done, journal 229 (PASS with two notes; roadmap 6E status updated). 6E still needs the corrected-workload benchmark and the original operational acceptance.
- **phase 6D**: on main (ffbecd5), released b69b880 (journal 221); ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`, dispatches 30. Remaining: verify.md 6D acceptance rows at the first game window, §4 read-backs at real ticks, exec-health baseline retaken on 0d04804 (item 12), ledger re-archive and final review (docs/superpowers/reviews/2026-09-13-phase6d-* stale by 9 ledger lines), then roadmap `done`; policy comparison is the user's adoption decision; worktree phase6d-merge-main removable after acceptance. Observation for its funnel rows: the gap_outcomes drain watermark 50028 of 3,195,901 snapshots (journal 229 anomaly 2).
- **phase 4.6**: on main, released c1066b5. Remaining: T18b (watched NFL-window queries), T19 (verification rows), walkthrough items; the user's three LAN files.
- **6C**: planned/partial; closure names both funnel units as delivered by 6D; counterfactual fills separate by `fills.id > 1878` (spec 0.17).
- **6B**: done (journal 216); remaining on main: batch A items (in flight), the row 72 batch, verify §3 rows judged at the first game window.

## Pending results / subprocesses

- Agents: none running. Suites: none. Wakeups: CronCreate one-shots 10:15 CT (p95, fix 74 between calls, gap episodes) and 11:45 CT (fix 73 closing read) with durable reminders 2026091505 and 2026091506; both armed at 09:53 CT.
- Latest receipts (~/.cache/sports-harness/test-state/): fix-20260915-dirty-intervals 0d04804 (exit 0, pristine, release tree = deployed tree); phase6d-merge-main ffbecd5; main e17d0f5. The main release tree since 0d04804 is docs-only, so a rebased branch receipt is the release receipt (deploy.md step 2).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+ shards), phase6d_merge_main, phase6d_merge_review, none (the four hotfix databases' grants were revoked 09:49 CT; the databases remain until the next `make testdb-prune` or the user's cleanup).
- Worktrees: none for hotfixes (the four merged worktrees removed 09:49 CT); the older worktrees unchanged.
- Day counters (CT) Sep 15: dispatches 14 (4 carried + batch A 4 + fix 73 2 + batch B 2 + fix 74 2), failed deploys 1 (row 72 verify FAIL at 00:36 CT; release stands), implementers running 0 of 3.

## Rulings landed (journal 199-212, 224, 226, 229)

User-side closed: NAS key, age key off-host, Anthropic account limits, Kalshi read scope, runs 14485/14486 stay, gate criterion 8 stays, R4 window rule, cutoff constant = 05:23:44Z (journal 224 item 2; verify.md:514 comment landed 08:00 CT), row 64 authorized, 6E cold-start reboot done. Journal 224: row 72 option (b) with spec 0.17; RFQ0 stays; fix 71 ratified standing; walkthrough items 14/18 exempt from the twice-running clause; storage retention decision by 2026-09-22; 6B exec-health unattributable on c1066b5; Amendment 6 range (1, 17016) stays; order 157 vocabulary split (spec 0.18; audit document annotated 08:00 CT); host restart unit a plain oneshot (runbook note landed); U7 governs pushes. Journal 226: the cold-start timing was the loop's. Journal 229: cold start PASS; fix 73's remedy (NULL roi_net on a zero used price); the drain watermark observation is not a fix. Spec amendment 0.19 (loop-owned) landed 08:00 CT.

## Evidence receipts

- Preflight: evidence/2026-09-15-preflight-0746.txt (PASS, paper posture intact, 0d04804). Cold start: evidence/2026-09-15-coldstart-0746.txt. Fix 73: evidence/2026-09-15-gap-outcomes-divzero-0755.txt. Verify 762bde4 (journal 231): evidence/2026-09-15-verify-{summary-0823,layer2-0826,layer2-phases-0826,shell-0826,layer2b-0827,rows-0829,rows2-0830}.txt. Storage: evidence/2026-09-15-storage-by-table-0803.txt. Daily: evidence/2026-09-15-daily-0907.txt. Deploy 7c3d750: evidence/2026-09-15-deploy-full-0937.txt. Verify 7c3d750 (journal 234): evidence/2026-09-15-verify-{summary-0942,shell-0944,layer2-0947,layer2-phases-0947,layer2b-0947,rows-0950}.txt. Fix 73 closing attempts: evidence/2026-09-15-fix73-closing-0907.txt.
- Releases: /srv/sports-harness/releases/20260915T143747Z-7c3d750 (full, healthy); 20260915T131907Z-762bde4 (app, healthy); 20260915T070539Z-0d04804 (full, healthy); 20260915T062147Z-b69b880; 20260915T060556Z-e17d0f5 (app); 20260915T052344Z-c1066b5 (full); 20260915T043416Z-f28cf4d (failed-old-apps-restored). Boundary: evidence/2026-09-15-release-boundary-6b.txt.
- Verify (journal 219): evidence/2026-09-15-verify-*.txt and the walkthrough captures; fix 69/70: evidence/2026-09-15-verify-fix6970-0208.txt; row 72: evidence/2026-09-15-row72-ids.txt; 6D: evidence/2026-09-15-predeploy-baseline-6d.txt, -verify-6d-*.txt. Review of record: https://claude.ai/artifact/3VT5WfN4fWPXXkSNCGXAE2 (journal 223).

## Standing constraints (unchanged)

Paper-only; no live posture; no non-GET venue code; invariant 5 (no DROP/RENAME/TRUNCATE/DELETE/compaction/retention by code or hand; the loop proposes, the user executes); no new outbound hosts; git pushes only as U7 directs (main and the current phase branch after every phase and every Monday; never pull, rebase onto the remote, open PRs or push task worktree branches; creating a remote stays gate 8); secrets never printed; workers sandboxed without production access; ceilings and model rules per the skill.
