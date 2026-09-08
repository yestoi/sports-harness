# Autopilot state (rewritten by the loop at every unit boundary; the journal is the history)

- Updated: 2026-09-07 23:28 CT
- Session: session_01ACYvEDzjr3oQvGNst45sjf (trailers from this session); preflight journal 39 (paper posture intact)
- Position: **phase 3 in progress** (Orient rule 5); SDD ledger `.superpowers/sdd/2026-09-07-phase3-paper-execution/progress.md`
- Branch: `phase3-paper-execution` at 1be170d (Tasks 3b, 5, 6, 7, 8, 9 merged; 655 tests) (main checkout); `main` 659ba67 = NAS build 659ba67 (deployed 16:26 CT, verified 16:31 CT)
- Agents in flight: `p3-impl-t10` (opus, dispatched 23:27 CT, timeout 00:57 CT) on `../sports-wt/phase3-t10-report`; `p3-impl-t12` (sonnet, dispatched 23:27 CT, timeout 00:57 CT) on `../sports-wt/phase3-t12-dashboard`; both from 1be170d; contexts task-10-context.md, task-12-context.md; reports land in task-10-report.md, task-12-report.md
- Next in the phase: Task 10 review (sonnet: report/ is outside the opus paths) and Task 12 review (sonnet) -> fix rounds -> rebase, suite, ff-merge (10 before 12 in plan order; both touch nothing shared) -> 11 (opus) after 10; 12b (U6, sonnet; brief task-12b-brief.md) after 6, 7, 10, 11, 12 and before 13; 13 (opus); 14 (sonnet)
- Deploys pending: Task 2b quiet-window deploy (01:00-08:00 CT, no game window): stop `app-ws` and `app-run` first, ff `main` to the phase branch, `make deploy-nas`, then `docker compose run --rm -T app-run partition-bulk-tables` (tens of minutes; recovery in `task-2b-live-run-recovery.md`), `show shared_buffers` for compose fix 9, verify Layers 1-2 plus `pg_partitioned_table` listing the three tables, remove carried fixes 9 and 13 when the rows pass. Task 4b deploy before 2026-09-16 (replay first, Amendment 3, `gate_variant` flip). Every deploy stops the writers first until fix 13 is on the NAS
- Game window: FSU-SMU 18:15-22:30 CT tonight (blocks deploys; run the verify.md Game window query before any deploy)
- Carried fixes: 9 and 13 open, both phase-bound (no hotfix unit)
- Deferred items: fix 4 live gap recovery (read the app-ws log at the first `gap` row); 7 null feed_kind direct rows on run 2322 (re-check at the next verify); morning-after-game full verify with the walker (Tue 2026-09-08 after 08:10 CT); daily 09:00 CT line
- U7: after the phase and on Mondays, `git push origin main phase3-paper-execution` beside the bundle; never pull; a failed push is journaled only
- Day counters (2026-09-07): failed deploys 1 (358c808 init-db deadlock), same-item repeat FAILs 0, dispatches 88 (this session 25), test notifications sent today: yes (11:15 CT)
- Lessons: run controller git operations from /Users/trey/dev/sports (never inside a worktree cwd); never day-wide scans on orderbook_events during verification; never a reviewer's suite while an implementer works on the same branch database; forced ticks fetch every family by design; reviewer reports over ~90 lines get truncated: ask for the full report in a file and a compact copy under 70 lines
