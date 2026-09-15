# Autopilot checkpoint

Updated 2026-09-15 08:00 CT (13:00Z) by the post-reboot controller session (tmux `sports-autopilot`, flock held; PID 6734) in `/home/trey/dev/sports` on Omarchy. Last journal entry: 229. Paper-only. Runtime build: **0d04804** (fix 69/70 full release 02:05-02:07 CT, journal 222), restarted by the cold start 07:44:41 CT. Main: 0d04804 + docs (deploy trigger diff empty). origin/main = 35f7180 (pushed 07:17 CT; this checkpoint is not pushed: U7 pushes after phases and on Mondays).

## Right now: two hotfix implementers running (journal 228-229 done; the 6E cold start is observed)

- **hotfix batch A** (journal 224 items 2, 3, 4, 8 code side, 13, 14; controller landed the document sides of items 2, 8, 14, 15 in this commit): implementer sonnet dispatched 07:52 CT on `.superpowers/sdd/hotfix-2026-09-15/batch-a-brief.md`; worktree `../sports-wt/fix-20260915-batch-a` (branch `fix-20260915-batch-a`, base main 35f7180), db `harness_test_fix_20260915_batch_a` (fixture grant ON); report `results/batch-a-report.md`; chase 08:52 CT, timeout 09:22 CT. Next legal action: consume the report, commit the diff, package `review-batch-a-35f7180..<sha>.diff`, opus reviewer (execution path).
- **hotfix fix 73** (new, journal 229: `gap_outcomes_drain` DivisionByZero on a zero used price): implementer opus dispatched 07:58 CT on `fix-73-brief.md`; worktree `../sports-wt/fix-20260915-gap-outcomes-divzero` (base 35f7180), db `harness_test_fix_20260915_gap_outcomes_divzero` (grant ON); report `results/fix-73-report.md`; chase 08:58 CT, timeout 09:28 CT. Next: commit, package, opus reviewer (settlement path). Files disjoint from batch A; one shared app-only release when both reviews are clean (full only if a diff classifies so).
- Ledger for both: `.superpowers/sdd/hotfix-2026-09-15/progress.md` (append-only; fix 71 and fix 69/70 history above).

## Order of work (journal 224 item 17; batch A in flight)

1. Batch A + fix 73 release (app-only unless `make plan-release-omarchy` classifies otherwise), then verify.
2. Hotfix batch B (item 9's fix 71 narrowing: application_name per service, drain by listed pid and stopped-service names, partition children as bulk, healer lock_timeout, dump-in-progress refusal); opus impl, sonnet review; own release.
3. Row 72 batch (own release, full recipe: revision 0013 `orders.nw_executor_version`, Amendment 6 sub-population sentence, verify.md Layer 2b narrowed queries read 0/0 against evidence/2026-09-15-row72-ids.txt); opus impl, opus review.
4. Verify after each release: journal 219's deferred rows (fix 64's check row, the two cutoff-bounded checks now at 05:23:44Z, `intents_without_order_or_skip`, the 01:00-08:00 open-interval rule, c1066b5-era orphan intents), item 12's exec-health windows on 0d04804 (02:30-06:30 CT vs ca30ed1's 23:38-00:23 CT and Monday's quiet hours), item 16's tape-gap read for 23:34-23:37 CT Sep 14 (journal 229 offers the reading: a clean stop writes no gap row), durable re-reads of rows 49 and 68, the 6B by-cause row, fix 73's closing read (next settle run `ok`).
5. Operate: daily 09:00 CT line; Tue 09:30 CT futures snapshot check (the job runs 09:00 CT; confirm it ran after the reboot); the storage retention proposal (item 11, decision by 2026-09-22), no execution. 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (Thu 2026-09-17 19:15 CT; NCAAF 18:30 CT). The user does the LAN files (roadmap TODO line 567).

## Active units

- **operate (6E cold start)**: done, journal 229 (PASS with two notes; roadmap 6E status updated). 6E still needs the corrected-workload benchmark and the original operational acceptance.
- **phase 6D**: on main (ffbecd5), released b69b880 (journal 221); ledger `.superpowers/sdd/2026-09-13-phase6d-sustained-evaluation/progress.md`, dispatches 30. Remaining: verify.md 6D acceptance rows at the first game window, §4 read-backs at real ticks, exec-health baseline retaken on 0d04804 (item 12), ledger re-archive and final review (docs/superpowers/reviews/2026-09-13-phase6d-* stale by 9 ledger lines), then roadmap `done`; policy comparison is the user's adoption decision; worktree phase6d-merge-main removable after acceptance. Observation for its funnel rows: the gap_outcomes drain watermark 50028 of 3,195,901 snapshots (journal 229 anomaly 2).
- **phase 4.6**: on main, released c1066b5. Remaining: T18b (watched NFL-window queries), T19 (verification rows), walkthrough items; the user's three LAN files.
- **6C**: planned/partial; closure names both funnel units as delivered by 6D; counterfactual fills separate by `fills.id > 1878` (spec 0.17).
- **6B**: done (journal 216); remaining on main: batch A items (in flight), the row 72 batch, verify §3 rows judged at the first game window.

## Pending results / subprocesses

- Agents: batch-a (sonnet, 07:52 CT), fix-73 (opus, 07:58 CT); reports expected under `.superpowers/sdd/results/`. Suites: none (workers run scoped only). Wakeups: none armed (children running; arm at the next idle: 09:32 CT futures check, or the window end when a deploy is blocked).
- Latest receipts (~/.cache/sports-harness/test-state/): fix-20260915-dirty-intervals 0d04804 (exit 0, pristine, release tree = deployed tree); phase6d-merge-main ffbecd5; main e17d0f5. The main release tree since 0d04804 is docs-only, so a rebased branch receipt is the release receipt (deploy.md step 2).
- Fixture grant UPDATE(indisvalid) ON: harness_test_main (+ shards), phase6d_merge_main, phase6d_merge_review, **fix_20260915_batch_a, fix_20260915_gap_outcomes_divzero** (revoke after their last suite).
- Worktrees: fix-20260915-batch-a, fix-20260915-gap-outcomes-divzero (active); phase6d-merge-main (ffbecd5); the older recovery/fix45/fix48/fix52/worker-smoke set (preserve until the recovery handoff is accepted).
- Day counters (CT) Sep 15: dispatches 6 (4 carried + batch A 1 + fix 73 1), failed deploys 1 (row 72 verify FAIL; release stands), implementers running 2 of 3.

## Rulings landed (journal 199-212, 224, 226, 229)

User-side closed: NAS key, age key off-host, Anthropic account limits, Kalshi read scope, runs 14485/14486 stay, gate criterion 8 stays, R4 window rule, cutoff constant = 05:23:44Z (journal 224 item 2; verify.md:514 comment landed 08:00 CT), row 64 authorized, 6E cold-start reboot done. Journal 224: row 72 option (b) with spec 0.17; RFQ0 stays; fix 71 ratified standing; walkthrough items 14/18 exempt from the twice-running clause; storage retention decision by 2026-09-22; 6B exec-health unattributable on c1066b5; Amendment 6 range (1, 17016) stays; order 157 vocabulary split (spec 0.18; audit document annotated 08:00 CT); host restart unit a plain oneshot (runbook note landed); U7 governs pushes. Journal 226: the cold-start timing was the loop's. Journal 229: cold start PASS; fix 73's remedy (NULL roi_net on a zero used price); the drain watermark observation is not a fix. Spec amendment 0.19 (loop-owned) landed 08:00 CT.

## Evidence receipts

- Preflight: evidence/2026-09-15-preflight-0746.txt (PASS, paper posture intact, 0d04804). Cold start: evidence/2026-09-15-coldstart-0746.txt. Fix 73: evidence/2026-09-15-gap-outcomes-divzero-0755.txt.
- Releases: /srv/sports-harness/releases/20260915T070539Z-0d04804 (full, healthy); 20260915T062147Z-b69b880; 20260915T060556Z-e17d0f5 (app); 20260915T052344Z-c1066b5 (full); 20260915T043416Z-f28cf4d (failed-old-apps-restored). Boundary: evidence/2026-09-15-release-boundary-6b.txt.
- Verify (journal 219): evidence/2026-09-15-verify-*.txt and the walkthrough captures; fix 69/70: evidence/2026-09-15-verify-fix6970-0208.txt; row 72: evidence/2026-09-15-row72-ids.txt; 6D: evidence/2026-09-15-predeploy-baseline-6d.txt, -verify-6d-*.txt. Review of record: https://claude.ai/artifact/3VT5WfN4fWPXXkSNCGXAE2 (journal 223).

## Standing constraints (unchanged)

Paper-only; no live posture; no non-GET venue code; invariant 5 (no DROP/RENAME/TRUNCATE/DELETE/compaction/retention by code or hand; the loop proposes, the user executes); no new outbound hosts; git pushes only as U7 directs (main and the current phase branch after every phase and every Monday; never pull, rebase onto the remote, open PRs or push task worktree branches; creating a remote stays gate 8); secrets never printed; workers sandboxed without production access; ceilings and model rules per the skill.
