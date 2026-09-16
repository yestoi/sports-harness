# Context hygiene migration (spec 2026-09-15-context-hygiene-design, revision 3)

Written 2026-09-15 19:38 CT by the user-directed implementation session on branch `context-hygiene-2026-09-15`,
pre-migration sha c962036741c83ce0c96394d0e1dbb5aa8dec560b (later tasks fall back to this line if /tmp is gone).
The last controller checkpoint was 2026-09-15 18:41 CT, last journal entry 246. The tables here back
the `repair` journal entry.

## 1. Row normalisation

Rows 71, 78, 79, 80 rewritten to six cells (original text preserved, nothing shortened). Row 71 had seven
cells: its sixth (the batch disposition) and seventh (the close) are joined into one Deploy cell with a
space. Rows 78, 79 and 80 had four cells (#, Finding, Files, disposition): the disposition becomes the Deploy
cell and Change and Covering test read `none stated`.

Row 71, original `roadmap.md@c962036:659` (7 cells); six-cell form:

```
| 71 | deploy 2026-09-14 23:34 CT (release `20260915T043416Z-f28cf4d`, `failed-old-apps-restored`; journal 217): the full recipe stops the apps, then `migrate ensure`'s `create index concurrently if not exists ix_intents_market_created` (revision 0010) was cancelled by `lock_timeout = '5s'`; the Postgres log shows a stopped app's streaming-cursor backend (`FETCH FORWARD 2000`) outliving its container by ~30 s (server `tcp_keepalives_* = 0`, `idle_in_transaction_session_timeout = 0`), whose snapshot the concurrent build waited on; the cancelled build left `ix_intents_market_created` INVALID (`indisvalid = false`) on production, which a retry's `if not exists` would skip | `scripts/release-omarchy.py` (`deploy()`: a `draining` step after the stop), `harness/db/migrate.py` (`heal_invalid_indexes` before the upgrade), `tests/test_omarchy_release.py`, `tests/test_alembic.py` | after the stop, list and `pg_terminate_backend` the orphaned harness client backends (never `pg_dump`/`psql`), poll until none remain or abort into the rollback; before `command.upgrade`, `REINDEX INDEX` every invalid index on a non-bulk table (a bulk-table one raises: the controller decides); no DROP anywhere (invariant 5) | release tests: stop → drain → migrate order, undrained backends abort into the rollback, app-only never drains, pg_dump excluded; migrate tests: an index marked invalid is valid after `heal_invalid_indexes`, a bulk-table one raises untouched | hotfix batch `fix-20260915-release-lingering-backends` (opus impl, sonnet review), then the 6B release retried (attempt 2; the cutoff constant reset at that release commit) **Closed 2026-09-15 00:27 CT (journals 218/219):** shipped in c1066b5 (c907b60, 15c987f, c1066b5) and exercised by its own release: the healer rebuilt `ix_intents_market_created` (valid again) and the drain found no orphan (`drained_backends: []`, 0.077 s). |
```

Row 78, original `roadmap.md@c962036:666` (4 cells); six-cell form:

```
| 78 | verify re-read 2026-09-15 13:40 CT on f7a1ccb (journal 241; evidence/2026-09-15-reread-1340.txt): the loop-metrics row reads **FAIL**: `exec_heartbeat.p95_loop_ms` 8,423 ms against the 7,500 ms bound (`exec_period_s × 1000 ÷ 2`), single loops of 20-27 s, `loops_skipped` 3,432 -> 3,435 in an hour, with 0 open orders and no error, on a quiet Tuesday afternoon. Not a restart artifact: the 10-minute p95 maxima ran 4,743 (10:10 CT, the 10:15 CT PASS) -> 6,111-6,468 (10:40-11:20) -> 8,943 / 12,555 / 16,208 / 17,314 (11:20-12:10, on 7c3d750) -> 7,471-8,423 (13:00-13:40, on f7a1ccb). The one monotone series beside it is `exec.nw_pending`: 2,878 at 09:30 CT -> 5,055 at 13:30 CT, about 300 more pending `no_watcher` counterfactual tracks every 30 minutes on both builds: midweek every cancelled order (fair_stale, book_dirty) leaves a track pending until its game's deadline (Thursday / Saturday), and the executor advances every due track each loop (`harness/execution/loop.py` about line 907; `set_nw_backoff` spaces retries). At this rate Thursday 19:15 CT arrives with a five-figure backlog unless the slate's expiries drain it first, and the first NFL window's 6D acceptance rows would be read on a starved executor. Disposition is a design choice, not a defect in today's fixes (none touch the counterfactual step): (a) a per-loop time budget for counterfactual advances, round-robin by due time, leaving the real order work first (opus impl/review, a hotfix on the user's yes; changes when counterfactuals are stepped, not what they measure); (b) accept the FAIL until the first game window drains the backlog and settle the cadence in 6D's holding/capacity policy (item 11); (c) something else. Put to the user as decisions packet item 16. **A second FAIL reading on this row at the next verify pass is the daily failure ceiling (same item twice running): the loop gates and waits.** **User ruling 2026-09-15 13:55 CT (journal 242): option (c), a set-based counterfactual write path (one UPDATE per loop for the dirty-market accrual with the same clamp and stamp, one set-based close of expired tracks, skip the no-change write on clean tickers; open orders unchanged; identical-results test and a statement count flat in N); branch `fix-2026-09-15-executor-batch`, opus/opus; no budget or round-robin (§0.13c stays the user's); the loop-metrics ceiling is answered while this is in flight; verify.md unedited. User-provided evidence: 5,055 pending rows all cancelled over 133 tickers / 56 games; expiries Thu 356, Fri 181, Sat 2,957, Sun 1,448, Mon 113; per-row UPDATE in subtransactions 2,360 ms vs one set-based UPDATE 4 ms.** **Released f8053c6 (app-only) 2026-09-15 15:40 CT (journal 243). Judged 16:02 CT (journal 244): median loop 10.2 -> 7.6 s, p95 27.9 -> 13.9 s over the surrounding 30 minutes, errors 0, no measured value changed; heartbeat p95 13,850 ms still over 7,500 with 6,387 pending tracks (growing about 280 per 25 min). The residual is the read side (row 79's print rescan and one simulate_fills per pending row). Row stays open; the §0.13c question is decisions packet item 17 (user).** **Second part ruled 2026-09-15 16:57 CT (journal 245, packet item 17 option d): batch the clean-market cursor-advance writes the same way (one UPDATE from a VALUES list per loop for cancelled pending rows whose walk yields no fill, no cross and no re-anchor; fill/cross rows keep their savepoint), plus phase timings (tape read, batch, per-row count and time) in the heartbeat; branch `fix-2026-09-15-executor-batch-2`, opus/opus; (a) deferred, (b) not needed, §0.13c untouched.** **18:40 CT reading (journal 246): p95 23,693 ms, median loop 14.4 s, 7,833 pending (+550/h through the evening); part 2 committed e0c9888 on `fix-2026-09-15-executor-batch-2`, unreviewed at the user's stopping point; first work of the resumed session.** | `harness/execution/loop.py` (the counterfactual step and its scheduling), `harness/execution/store.py` (`working_orders`, `set_nw_backoff`), `tests/test_exec_loop.py` | none stated | none stated | **second hotfix in flight (journal 245)** |
```

Row 79, original `roadmap.md@c962036:667` (4 cells); six-cell form:

```
| 79 | user measurement 2026-09-15 about 13:50 CT (journal 242, user-provided, read-only on production): the executor's counterfactual step rescans prints from the earliest `placed_at` per ticker on every loop, about 34,014 `venue_trades` rows a loop at the 13:50 CT backlog (5,055 pending rows, 133 tickers). The user ruled it out of fix 78's scope: a follow-up, not fixed in the executor-batch hotfix. | `harness/execution/loop.py` (the per-ticker tape read for pending counterfactuals), `harness/execution/store.py` | none stated | none stated | follow-up (user ruling journal 242); not a hotfix now |
```

Row 80, original `roadmap.md@c962036:668` (4 cells); six-cell form:

```
| 80 | user measurement 2026-09-15 about 13:50 CT (journal 242, user-provided): the `orders` table is 1,042 MB for 15,572 live rows after about 10.9 M updates (the per-row counterfactual writes of the pre-fix-78 path; autovacuum keeps up on dead tuples but the heap does not shrink). The user ruled it out of fix 78's scope: a follow-up. Any compaction, `VACUUM FULL`, `CLUSTER` or `pg_repack` is gate 3 (the user executes). | none in `harness/` (operational; the fix-78 path removes most of the churn) | none stated | none stated | follow-up (user ruling journal 242); gate 3 for any compaction |
```

## 2. Classification table

Rule: spec 3.2 step 2, `Closed` before `Open`, everything else `Watch`. The draft column is the script of
plan Task 5 step 1 (20 Closed, 2 Open, 41 Watch); the section column is the corrected reading of every row's
Deploy cell and its most recent cited journal entry. Rows the rule leaves ambiguous are marked `(ambiguous)`;
they sit in `Watch` pending your ruling and the reason names the uncited journal evidence and a recommendation.

| # | line | draft | section | reason | original length |
|---|---|---|---|---|---|
| 16 | 606 | Watch | Watch | Deploy cell: phase work, gate 13 (a check change is never a hotfix); journal 113/157 cite it as the precedent | 847 |
| 20 | 607 | Closed | Closed | Deploy cell and journal 200: closed, the user holds the key off-host | 580 |
| 21 | 608 | Watch | Closed | ambiguous by rule (cites journal 66 only). Uncited journal 71: fix 21 merged 3badf58 and deployed in wave 3 (417eecd). Recommend Closed (ambiguous; ruled Closed) | 637 |
| 22 | 609 | Watch | Closed | ambiguous by rule (cites 68). Uncited journal 70: fix 22 on bdea218 FAIL on the tape-read row, carried as 26. Recommend Closed (superseded by 26) (ambiguous; ruled Closed) | 992 |
| 23 | 610 | Watch | Closed | ambiguous by rule (cites 68). Uncited journal 71 (merged with 24) and 79 (limits read PASS: tier and capacities). Recommend Closed (ambiguous; ruled Closed) | 483 |
| 24 | 611 | Watch | Closed | ambiguous by rule (cites 68). Uncited journal 82: demo smoke PASS 15/15 on 4c554ea. Recommend Closed (ambiguous; ruled Closed) | 963 |
| 25 | 612 | Closed | Closed | Deploy cell: "Row stays closed" (journal 92-93 traced the page time to fix 32) | 864 |
| 26 | 613 | Watch | Closed | ambiguous by rule (cites 70). Uncited journal 74 and 79: fix 26 rows PASS warm (0 tape read failed, lag 0, batch_min 20000). Recommend Closed (ambiguous; ruled Closed) | 843 |
| 27 | 614 | Watch | Closed | ambiguous by rule (cites 73). Uncited journal 76: place now confirms (404, 404, 200); the residual amend defect carried as 28 (closed). Recommend Closed (ambiguous; ruled Closed) | 696 |
| 28 | 615 | Closed | Closed | Deploy cell and journal 82: deployed d347ee8, closed on the 15/15 smoke | 875 |
| 29 | 616 | Closed | Closed | Deploy cell and journal 82: deployed 4c554ea, smoke 15/15, closed | 598 |
| 30 | 617 | Watch | Closed | ambiguous by rule (cites 89). Uncited journal 98: "Carried fixes 30, 31, 32 are all deployed and closed". Recommend Closed (ambiguous; ruled Closed) | 831 |
| 31 | 618 | Watch | Closed | Deploy cell: deployed 4f7b2c8 then the Scheduler re-enable row PASS (journal 96, "Roadmap row 31 verified") | 1798 |
| 32 | 619 | Watch | Closed | journal 98 (cited): deployed 3ca32dd; "Carried fixes 30, 31, 32 are all deployed and closed" | 1164 |
| 33 | 620 | Watch | Watch | Deploy cell: phase work 6C (U8); not a hotfix | 1058 |
| 34 | 621 | Watch | Watch | Deploy cell: phase work 6E (the guard warm-up); not a hotfix | 1409 |
| 35 | 622 | Closed | Watch | ambiguous by rule (cites 110: deployed, verify at the morning pass). Journal 124 shows the replay path working; the covering row needs the listener on, which RFQ0 keeps off (journal 224 item 6). Recommend Closed (superseded by RFQ0) or keep Watch (ambiguous; ruled Watch) | 1749 |
| 36 | 623 | Closed | Closed | ambiguous by rule (cites 110). Uncited journal 112 and 144: "fix 36 working" (the hourly then 24 h back-off engaged as designed). Recommend Closed (ambiguous; ruled Closed) | 1057 |
| 37 | 624 | Watch | Closed | ambiguous by rule (cites 123, a deploy entry). Uncited journal 124: "fix 37 PASS" (78 no-op DDL skipped, no ABORT, executor back inside 90 s). Recommend Closed (ambiguous; ruled Closed) | 1778 |
| 38 | 625 | Closed | Closed | ambiguous by rule (cites 112: deployed). Row 40 records that fix 38 still stored about 3,000 rows a minute; 40 superseded it (journal 112 carried 40). Recommend Closed (superseded by 40) (ambiguous; ruled Closed) | 967 |
| 39 | 626 | Watch | Closed | journal 112 (cited): deployed 7c3d555, "Fix 39 verified: the annotator's calls are accepted (HTTP 200)"; the token residual is row 41 | 1052 |
| 40 | 627 | Watch | Closed | ambiguous by rule (cites 124: deferred). Uncited journal 136 (g): rfqs 63,443 rows in 50 min against the row's "under 1,000 a day", carried as 46 (6D). Recommend Closed (superseded by 46) (ambiguous; ruled Closed) | 857 |
| 41 | 628 | Watch | Watch | ambiguous by rule (cites 123/124: deferred). Shipped bd220b8; the row itself says the budget change is insufficient and the backlog selection is 6C work. Recommend Closed (shipped; the remainder is 6C's) or keep Watch (ambiguous; ruled Watch) | 1067 |
| 42 | 629 | Watch | Closed | ambiguous by rule (cites 132: verify pending). Deploy cell: pricing recovered 14:24 CT on the hand-built index; journal 126 and 132: revision 0006 current, indisvalid true, pricing runs ok. Recommend Closed (ambiguous; ruled Closed) | 1399 |
| 43 | 630 | Watch | Closed | journal 124 (cited): "fix 43 PASS" after the bd220b8 deploy (no subscription rejected in 20 min) | 879 |
| 44 | 631 | Watch | Closed | ambiguous by rule (cites 132: verify pending). Row 46 (journal 136) records "the RFQ listener revived by fix 44", i.e. the resubscribe worked. Recommend Closed (ambiguous; ruled Closed) | 1441 |
| 45 | 632 | Closed | Closed | Deploy cell and journal 193: 24 h normalize row PASS, closed | 1620 |
| 46 | 633 | Watch | Watch | Deploy cell: phase work 6D (budget isolation); RFQ0 stays by ruling (journal 224 item 6) | 1163 |
| 47 | 634 | Closed | Closed | Deploy cell and journal 160: closed (settle 172 ran the due WTD report first) | 1351 |
| 48 | 635 | Watch | Closed | ambiguous by rule (cites 136; Deploy cell carries the FAIL to 6D). Uncited journal 221 (6D release): "fix 48's remaining clause 0 PASS". Recommend Closed (ambiguous; ruled Closed) | 1452 |
| 49 | 636 | Watch | Closed | journal 219 (cited): "49 closed" (recorder RSS 191-299 MiB over nine hours) | 2078 |
| 50 | 637 | Watch | Watch | state.md: rows 50-58 are phase-assigned or observations; Deploy cell: acceptance OPEN with a judge-after the Sunday crash overtook (observation) | 1363 |
| 51 | 638 | Open | Watch | state.md: rows 50-58 phase-assigned or observations; journal 157: not a hotfix (a check window change is gate 13, "same as fix 16"), carried to the 6D plan-next. The Deploy cell's word "Actionable" is the restart-era note. Recommend Watch (ambiguous; ruled Watch) | 1160 |
| 52 | 639 | Closed | Closed | Deploy cell and journal 193: closed (quiet-hour nws rows 0, freshness misses 0) | 2414 |
| 53 | 640 | Closed | Closed | state.md: rows 50-58 phase-assigned or observations. Merged 940fd6d, shipped in the Sep 14 full releases; journal 219 shows fix 53's disclosure live, but walk item 19 was never re-judged in the journal. Recommend Closed on the release verify, or Watch until a walkthrough re-judges item 19 (ambiguous; ruled Closed) | 1464 |
| 54 | 641 | Watch | Closed | state.md: rows 50-58 phase-assigned or observations. Merged 940fd6d and shipped; no journal re-judge of the Gate reading text. Recommend Closed on the release verify, or Watch (ambiguous; ruled Closed) | 973 |
| 55 | 642 | Watch | Closed | state.md: rows 50-58 phase-assigned or observations. Merged 940fd6d and shipped; no journal re-judge of the signals label. Recommend Closed on the release verify, or Watch (ambiguous; ruled Closed) | 1156 |
| 56 | 643 | Closed | Closed | Deploy cell and journal 173: closed (merged 84880cd, tests only) | 1518 |
| 56 (dup) | 644 | Watch | Watch | state.md: rows 50-58 phase-assigned or observations. Merged 440095d (revision 0008), rode the c1066b5 full release (journal 218); the closing read (positions no longer returns order 157) is not in the journal. Recommend one read at the next verify, then Closed (ambiguous; ruled Watch) | 2380 |
| 57 | 645 | Watch | Closed | ambiguous by rule (cites 175 only). Uncited journal 192/193: fix 58's sweep closed run 14486, "57/58 verified (sweep, key, readers) and the batch closes". Recommend Closed (ambiguous; ruled Closed) | 1554 |
| 58 | 646 | Watch | Closed | ambiguous by rule (cites 175 only). Uncited journal 192/193/236: the sweep runs as designed; "57/58 verified ... the batch closes". Recommend Closed (ambiguous; ruled Closed) | 1072 |
| 59 | 647 | Watch | Closed | journal 181 (cited): merged ca028e8, Result done; tests only, nothing to deploy | 1471 |
| 60 | 648 | Watch | Closed | Finding cell and journal 189 (cited): "Fix 60's row 60 closes" (settle ok, executor clean) | 1867 |
| 61 | 649 | Watch | Closed | state.md lists 61 as phase-assigned or observation; uncited journal 185 and row 67 show fix 61 landed (its durations reordered shard 1). Tests only. Recommend Closed (ambiguous; ruled Closed) | 850 |
| 62 | 650 | Watch | Closed | ambiguous by rule (cites 224, a decision). Batch A (7c3d750, journal 233) set the cutoff constant; journal 231 already read fills_outside_placement_window "pass" under the bounded predicate; the deadline bound shipped with 6B. Recommend Closed (ambiguous; ruled Closed) | 2229 |
| 63 | 651 | Watch | Closed | ambiguous by rule (cites 224). Same disposition as 62; journal 231 reads markouts_at_after_horizon "pass". Recommend Closed (ambiguous; ruled Closed) | 1329 |
| 64 | 652 | Watch | Closed | ambiguous by rule (cites 213: merged; deployed in c1066b5, journal 219). The closing condition (check reads 0 after a correction, or 24 h with no fail) has no journal reading since the Tuesday 04:00 CT sweep; journal 231 lists no score-check failure. Recommend Closed if the next sweep read is 0, else Watch (ambiguous; ruled Closed) | 1980 |
| 65 | 653 | Watch | Watch | state.md: 65 is phase-assigned or observation; no journal entry mentions fix 65; rides the next dashboard batch | 664 |
| 66 | 654 | Closed | Closed | Deploy cell: closed (merged ad90885, deployed ca30ed1, verified journal 196) | 1188 |
| 67 | 655 | Watch | Closed | Finding cell: closed (main suite green at a7e86cc; journal 188) | 1644 |
| 68 | 656 | Watch | Closed | journal 219 (cited): "68 closed" (pinnacle back since Monday); the Deploy cell's "open (observation)" predates it | 1487 |
| 69 | 657 | Watch | Closed | Finding cell and journal 222 (cited): closed, shipped in 0d04804 | 1276 |
| 70 | 658 | Watch | Watch | ambiguous: closed in journal 222, then annotated by ruling (journal 224 items 4 and 16) that closure is judged at the first live departure and batch A re-derives `gone`; batch A shipped in 7c3d750 (journal 233) with no departure read since. Recommend Watch until a live departure is read (ambiguous; ruled Watch) | 1933 |
| 71 | 659 | Closed | Closed | Deploy cell and journal 218/219: closed, exercised by its own release | 1890 |
| 72 | 660 | Closed | Closed | Finding cell and journal 238 (cited): CLOSED (narrowed reads 0 / 0) | 3737 |
| 73 | 661 | Closed | Closed | Finding cell and journal 241 (cited): CLOSED (settle 211 drained 69,875) | 2909 |
| 74 | 662 | Closed | Closed | Deploy cell and journal 235 (cited): closed (usd_reserved 0) | 2208 |
| 75 | 663 | Closed | Closed | Deploy cell and journal 246 (cited): CLOSED (report_wtd 64.5 s inside the budget) | 1989 |
| 76 | 664 | Closed | Closed | Finding cell and journal 238 (cited): CLOSED (unnamed_backends empty) | 1513 |
| 77 | 665 | Closed | Closed | Finding cell and journal 241/246 (cited): CLOSED with closing evidence | 3281 |
| 78 | 666 | Open | Open | state.md: "78 part 2 (in flight)"; Deploy cell: second hotfix in flight (journal 245); branch fix-2026-09-15-executor-batch-2 at e0c9888, review pending | 4072 |
| 79 | 667 | Watch | Watch | user ruling journal 242: a follow-up, not a hotfix now; waits for part 2's phase timings | 576 |
| 80 | 668 | Watch | Watch | user ruling journal 242: a follow-up; any compaction is gate 3 (the user executes) | 569 |

Counts: Open 1, Watch 13, Closed 49 (63 rows, 56 twice).

### Needs you

Ambiguous rows (in Watch pending your ruling), each with the recommendation from the table:

- 21: ambiguous by rule (cites journal 66 only). Uncited journal 71: fix 21 merged 3badf58 and deployed in wave 3 (417eecd). Recommend Closed (ambiguous)
- 22: ambiguous by rule (cites 68). Uncited journal 70: fix 22 on bdea218 FAIL on the tape-read row, carried as 26. Recommend Closed (superseded by 26) (ambiguous)
- 23: ambiguous by rule (cites 68). Uncited journal 71 (merged with 24) and 79 (limits read PASS: tier and capacities). Recommend Closed (ambiguous)
- 24: ambiguous by rule (cites 68). Uncited journal 82: demo smoke PASS 15/15 on 4c554ea. Recommend Closed (ambiguous)
- 26: ambiguous by rule (cites 70). Uncited journal 74 and 79: fix 26 rows PASS warm (0 tape read failed, lag 0, batch_min 20000). Recommend Closed (ambiguous)
- 27: ambiguous by rule (cites 73). Uncited journal 76: place now confirms (404, 404, 200); the residual amend defect carried as 28 (closed). Recommend Closed (ambiguous)
- 30: ambiguous by rule (cites 89). Uncited journal 98: "Carried fixes 30, 31, 32 are all deployed and closed". Recommend Closed (ambiguous)
- 35: ambiguous by rule (cites 110: deployed, verify at the morning pass). Journal 124 shows the replay path working; the covering row needs the listener on, which RFQ0 keeps off (journal 224 item 6). Recommend Closed (superseded by RFQ0) or keep Watch (ambiguous)
- 36: ambiguous by rule (cites 110). Uncited journal 112 and 144: "fix 36 working" (the hourly then 24 h back-off engaged as designed). Recommend Closed (ambiguous)
- 37: ambiguous by rule (cites 123, a deploy entry). Uncited journal 124: "fix 37 PASS" (78 no-op DDL skipped, no ABORT, executor back inside 90 s). Recommend Closed (ambiguous)
- 38: ambiguous by rule (cites 112: deployed). Row 40 records that fix 38 still stored about 3,000 rows a minute; 40 superseded it (journal 112 carried 40). Recommend Closed (superseded by 40) (ambiguous)
- 40: ambiguous by rule (cites 124: deferred). Uncited journal 136 (g): rfqs 63,443 rows in 50 min against the row's "under 1,000 a day", carried as 46 (6D). Recommend Closed (superseded by 46) (ambiguous)
- 41: ambiguous by rule (cites 123/124: deferred). Shipped bd220b8; the row itself says the budget change is insufficient and the backlog selection is 6C work. Recommend Closed (shipped; the remainder is 6C's) or keep Watch (ambiguous)
- 42: ambiguous by rule (cites 132: verify pending). Deploy cell: pricing recovered 14:24 CT on the hand-built index; journal 126 and 132: revision 0006 current, indisvalid true, pricing runs ok. Recommend Closed (ambiguous)
- 44: ambiguous by rule (cites 132: verify pending). Row 46 (journal 136) records "the RFQ listener revived by fix 44", i.e. the resubscribe worked. Recommend Closed (ambiguous)
- 48: ambiguous by rule (cites 136; Deploy cell carries the FAIL to 6D). Uncited journal 221 (6D release): "fix 48's remaining clause 0 PASS". Recommend Closed (ambiguous)
- 51: state.md: rows 50-58 phase-assigned or observations; journal 157: not a hotfix (a check window change is gate 13, "same as fix 16"), carried to the 6D plan-next. The Deploy cell's word "Actionable" is the restart-era note. Recommend Watch (ambiguous)
- 53: state.md: rows 50-58 phase-assigned or observations. Merged 940fd6d, shipped in the Sep 14 full releases; journal 219 shows fix 53's disclosure live, but walk item 19 was never re-judged in the journal. Recommend Closed on the release verify, or Watch until a walkthrough re-judges item 19 (ambiguous)
- 54: state.md: rows 50-58 phase-assigned or observations. Merged 940fd6d and shipped; no journal re-judge of the Gate reading text. Recommend Closed on the release verify, or Watch (ambiguous)
- 55: state.md: rows 50-58 phase-assigned or observations. Merged 940fd6d and shipped; no journal re-judge of the signals label. Recommend Closed on the release verify, or Watch (ambiguous)
- 56 (dup): state.md: rows 50-58 phase-assigned or observations. Merged 440095d (revision 0008), rode the c1066b5 full release (journal 218); the closing read (positions no longer returns order 157) is not in the journal. Recommend one read at the next verify, then Closed (ambiguous)
- 57: ambiguous by rule (cites 175 only). Uncited journal 192/193: fix 58's sweep closed run 14486, "57/58 verified (sweep, key, readers) and the batch closes". Recommend Closed (ambiguous)
- 58: ambiguous by rule (cites 175 only). Uncited journal 192/193/236: the sweep runs as designed; "57/58 verified ... the batch closes". Recommend Closed (ambiguous)
- 61: state.md lists 61 as phase-assigned or observation; uncited journal 185 and row 67 show fix 61 landed (its durations reordered shard 1). Tests only. Recommend Closed (ambiguous)
- 62: ambiguous by rule (cites 224, a decision). Batch A (7c3d750, journal 233) set the cutoff constant; journal 231 already read fills_outside_placement_window "pass" under the bounded predicate; the deadline bound shipped with 6B. Recommend Closed (ambiguous)
- 63: ambiguous by rule (cites 224). Same disposition as 62; journal 231 reads markouts_at_after_horizon "pass". Recommend Closed (ambiguous)
- 64: ambiguous by rule (cites 213: merged; deployed in c1066b5, journal 219). The closing condition (check reads 0 after a correction, or 24 h with no fail) has no journal reading since the Tuesday 04:00 CT sweep; journal 231 lists no score-check failure. Recommend Closed if the next sweep read is 0, else Watch (ambiguous)
- 70: ambiguous: closed in journal 222, then annotated by ruling (journal 224 items 4 and 16) that closure is judged at the first live departure and batch A re-derives `gone`; batch A shipped in 7c3d750 (journal 233) with no departure read since. Recommend Watch until a live departure is read (ambiguous)

### Ruling

2026-09-15 19:50 CT, the user, on the classification table and the Needs you list above, and on the batch-clock question
put in the same message (the clock ran from 16:59 CT to the 18:41 CT checkpoint, 1 h 42 min consumed):

> accept the recommendations

Applied: rows 21, 22, 23, 24, 26, 27, 30, 36, 37, 38, 40, 42, 44, 48, 53, 54, 55, 57, 58, 61, 62, 63, 64 move
to `Closed`; rows 35, 41, 51, 56 (dup) and 70 stay in `Watch`; the batch clock resumes at relaunch with 1 h 42 min
consumed (the session's computed figure, adopted under the same answer).

## 3. State fact inventory

Every sentence (bullets count as sentences) of `state.md@c962036` and where it goes in the rewrite. "Words kept"
means the sentence has no journal record and stays as text in the named section; a `journal N` destination was
grepped in that entry before the pointer was written.

| state.md sentence | destination | pointer |
|---|---|---|
| Updated 2026-09-15 18:41 CT by the post-reboot controller session ... | header line | rewritten header (session, checkout, last entry, runtime build f8053c6, main, origin/main 35f7180 kept) |
| Stopping point: Production is safe to leave. Runtime f8053c6 healthy ... | Resume first | words kept; p95 FAIL does not gate: journal 242 |
| Fix 78 part 2 is implemented and committed, not reviewed: e0c9888 ... | Resume first | words kept (branch, worktree, db, grant, report path, counts, deviation, package, .redbak: none of these are in the journal) |
| Resume order: dispatch the opus reviewer ... close row 78 or return ... | Resume first | words kept (option (a) = row 79) |
| The 18:40 CT wakeup fired before the session closed (journal 246) ... | Resume first | journal 246 (row 75 closed, tape 1.0, p95 23,693 at 7,833); urgency sentence kept as words |
| User-side: packet item 1 open until 2026-09-22; packet URL | Resume first | words kept (the artifact URL is not in the journal); deadline also under Counters and deadlines |
| Right now heading: released and verified; fix 73 and row 77 closed; ... | Right now | journal 241/246 (row 75 has since closed) |
| Released f7a1ccb (journal 237 ...): batch C, fix 77, row 72 batch ... | Right now | journal 237 (release), 238 (rows 72/76 closed), 241 (fix 73, row 77 closed; settle 211 69,875) |
| Fix 78 in two parts. Part 1 released f8053c6 15:40 CT and judged 16:02 CT ... | Right now | journal 243/244 (median 10.2 -> 7.6, p95 27.9 -> 13.9, 13,850 at 6,387) |
| Part 2 ruled 16:57 CT (journal 245, packet item 17 option d) ... | Active units | journal 245 (scope, branch, opus/opus, about 4 s a loop); the dispatch time 16:59 CT and chase/timeout figures kept as words |
| Option (a) (row 79 rescan) deferred ...; §0.13c untouched; the loop-metrics FAIL stands and does not gate (journal 242) | Right now | journal 242/245 |
| User decisions: packet items 2-10 ruled and applied (journal 239/240, commit ab353cc ...) | Right now | journal 239/240; commit ab353cc kept as words |
| Open: item 1 (retention by 2026-09-22), item 16 (fix 78), items 11-15 upcoming. Packet URL, reports/2026-09-15-open-decisions-packet.md | Counters and deadlines | words kept (report path, deadline) |
| Rows 75 and 77 closed (journal 246); no pending re-read besides p95 | Right now | journal 246 |
| Carried fixes open and actionable: 78 part 2 (in flight). 79 waits ...; 80 operational (gate 3) ... | fixes.md row 78 / 79 / 80 | Open row 78; Watch rows 79 and 80 (journal 242) |
| Rows 50-58, 61, 65, 66 are phase-assigned or observations | fixes.md Watch / Closed | report section 2 (the classification table; 66 is Closed) |
| Next duties: daily 09:00 CT line Wed 2026-09-16; 6D acceptance rows and 4.6 T18b/T19 at the first NFL window (Thu 19:15 CT; NCAAF 18:30 CT); retention by 2026-09-22 | Counters and deadlines | words kept |
| Order of work (journal 224 item 17; batch A in flight): 1. Done: 7c3d750 ... | Order of work | journal 233/234 |
| 2. Hotfix batch B (item 9's fix 71 narrowing ...) | Order of work | journal 233 (released in 7c3d750); the item list kept as a pointer |
| 3. Row 72 batch (own release, full recipe: revision 0013 ...) | Order of work | journal 237/238 (released f7a1ccb, closed) |
| 4. Verify after each release: journal 219's deferred rows ...; item 12's exec-health windows on 0d04804; item 16's tape-gap read ...; durable re-reads of rows 49 and 68, the 6B by-cause row, fix 73's closing read | Order of work | words kept as one line with pointers (journal 219, 224 items 12/16, 229) |
| 5. Operate: daily 09:00 CT line; Tue 09:30 CT futures snapshot check; storage retention proposal written (item 11; reports/2026-09-15-storage-retention-proposal.md, journal 230) ...; the user does the LAN files (roadmap TODO line 567) | Order of work | journal 230; words kept |
| Active units: operate (6E cold start): done, journal 229 ...; 6E still needs the corrected-workload benchmark and the original operational acceptance | Active units | journal 229; remainder kept as words |
| phase 6D: on main (ffbecd5), released b69b880 (journal 221); ledger path, dispatches 30. Remaining: ...; watermark 50028 (journal 229 anomaly 2) | Active units | journal 221/229; dispatches 30, the stale-review note and the removable worktree kept as words |
| phase 4.6: on main, released c1066b5. Remaining: T18b, T19, walkthrough items; the user's three LAN files | Active units | words kept |
| 6C: planned/partial; closure names both funnel units as delivered by 6D; counterfactual fills separate by fills.id > 1878 (spec 0.17) | Active units | journal 224 item 5 |
| 6B: done (journal 216); remaining on main: batch A items (in flight), the row 72 batch, verify §3 rows judged at the first game window | Active units | journal 216; batch A and the row 72 batch have since released (journal 233/237) |
| Agents: none running (fix-78b implementer aef7149b7435d1aa1 finished 17:32 CT; result committed as e0c9888). Suites: none. Wakeups: none (the 18:40 CT one consumed, journal 246). The 12:46 and 13:40 CT wakeups are consumed (journal 241) | Pending results | journal 241/246; the implementer id kept as words |
| Latest receipts (~/.cache/sports-harness/test-state/): fix-20260915-dirty-intervals 0d04804 ...; phase6d-merge-main ffbecd5; main e17d0f5. The main release tree since 0d04804 is docs-only ... | Pending results | journal 222/221/220; receipts as pointers; the release-tree sentence is superseded by the new Right now bullet (section 3 of the spec) |
| Fixture grant UPDATE(indisvalid) ON: harness_test_main (+ shards), phase6d_merge_main, phase6d_merge_review, none (the four hotfix databases' grants were revoked 09:49 CT ...) | Pending results | words kept (not in the journal) |
| Worktrees: fix-2026-09-15-executor-batch-2 (on fa2f4fd; db ..., fixture grant ON); the older phase worktrees unchanged. Test databases harness_test_fix_20260915_{row72,batch_c,ws_seq_ack} ... (revoke after their last suite) | Pending results | words kept (the two contradictory grant sentences are recorded as one: the three batch databases keep the grant until their last suite) |
| Day counters (CT) Sep 15: dispatches 23 (...), failed deploys 1, implementers running 0 of 3 | Counters and deadlines | words kept (journal 235 has the implementer count; the dispatch breakdown is not in the journal) |
| Rulings landed: User-side closed: NAS key, age key off-host, Anthropic account limits, Kalshi read scope, runs 14485/14486 stay, gate criterion 8 stays, R4 window rule, cutoff constant = 05:23:44Z (journal 224 item 2; verify.md:514 comment landed 08:00 CT), row 64 authorized, 6E cold-start reboot done | journal 199, 200, 201, 202, 205, 208, 207, 224, 207, 226-229 (already recorded) | each grepped: 199 NAS key; 200 off this host; 201 spending limits; 202 read-scoped; 205 stay as recorded; 208 criterion 8; R4 in 207/214/215/218; 224 '5, 23, 44' and verify.md:514; 207 row 64; 226-229 cold start |
| Journal 224: row 72 option (b) with spec 0.17; RFQ0 stays; fix 71 ratified standing; walkthrough items 14/18 exempt from the twice-running clause; storage retention decision by 2026-09-22; 6B exec-health unattributable on c1066b5; Amendment 6 range (1, 17016) stays; order 157 vocabulary split (spec 0.18); host restart unit a plain oneshot; U7 governs pushes | journal 224 (already recorded) | each grepped in 224: '0.17', 'RFQ0 stays', 'ratif', item 10 (items 14 and 18), 'retention', 'unattributable', '17016', '0.18', 'oneshot', 'U7' |
| Journal 226: the cold-start timing was the loop's. Journal 229: cold start PASS; fix 73's remedy (NULL roi_net on a zero used price); the drain watermark observation is not a fix. Spec amendment 0.19 (loop-owned) landed 08:00 CT | journal 226, 229, 224 (already recorded) | grepped: 226 'timing'; 229 'PASS', 'roi_net', 'watermark'; 224 '0.19' |
| Evidence receipts: Preflight ... Cold start ... Fix 73 ... Verify 762bde4 ... Storage ... Daily ... Deploy 7c3d750 ... Deploy f7a1ccb ... Verify f7a1ccb ... Re-reads 13:40 ... Fix 78 ... Verify 7c3d750 ... Fix 73 closing ... Re-reads 10:15 ... row 75 plans | report section 4 | every receipt grepped in the journal (section 4); the two not cited in the journal by name are listed there; the latest stage receipts stay as one pointer line per stage under Pending results |
| Releases: /srv/sports-harness/releases/20260915T203705Z-f8053c6 (app, healthy); ... 20260915T043416Z-f28cf4d (failed-old-apps-restored). Boundary: evidence/2026-09-15-release-boundary-6b.txt | report section 4 / Pending results | all nine stamps grepped in the journal (section 4); the runtime stamp stays in the header and Pending results |
| Verify (journal 219): evidence/2026-09-15-verify-*.txt and the walkthrough captures; fix 69/70: -verify-fix6970-0208.txt; row 72: -row72-ids.txt; 6D: -predeploy-baseline-6d.txt, -verify-6d-*.txt. Review of record: https://claude.ai/artifact/3VT5WfN4fWPXXkSNCGXAE2 (journal 223) | report section 4 / journal 219, 222, 223, 221 | grepped: 223 carries the review URL; 2026-09-15-predeploy-baseline-6d.txt is cited by no journal entry or ledger (section 4) and is cited from the repair entry |
| Standing constraints (unchanged): Paper-only; no live posture; ... ceilings and model rules per the skill | Constraints | words kept verbatim |

## 4. Receipts grep

Every evidence path and release stamp named in `state.md`'s Evidence receipts and Rulings landed sections,
searched in `journal.md` (full names, brace groups expanded, and the journal's hyphen-led shorthand on a line
carrying the same date) and in the active ledgers (`.superpowers/sdd/*/progress.md`).

| receipt | in journal | in a ledger |
|---|---|---|
| 2026-09-15-coldstart-0746.txt | yes | NO |
| 2026-09-15-daily-0907.txt | yes | NO |
| 2026-09-15-deploy-app-1537.txt | yes | NO |
| 2026-09-15-deploy-full-0937.txt | yes | NO |
| 2026-09-15-deploy-full-1228.txt | yes | NO |
| 2026-09-15-fix73-closing-0907.txt | yes | NO |
| 2026-09-15-fix78-judgement-1602.txt | yes | NO |
| 2026-09-15-gap-outcomes-divzero-0755.txt | yes | yes |
| 2026-09-15-predeploy-baseline-6d.txt | NO | NO |
| 2026-09-15-predeploy-fix78-1537.txt | yes | NO |
| 2026-09-15-preflight-0746.txt | yes | yes |
| 2026-09-15-release-boundary-6b.txt | yes | NO |
| 2026-09-15-reread-1015.txt | yes | yes |
| 2026-09-15-reread-1340.txt | yes | NO |
| 2026-09-15-row72-ids.txt | yes | NO |
| 2026-09-15-storage-by-table-0803.txt | yes | NO |
| 2026-09-15-t4-explain-1000.txt | NO | yes |
| 2026-09-15-tape-post-1233.txt | yes | NO |
| 2026-09-15-tape-pre-1227.txt | yes | yes |
| 2026-09-15-verify-fix6970-0208.txt | yes | NO |
| 2026-09-15-verify-layer2-0826.txt | yes | NO |
| 2026-09-15-verify-layer2-0947.txt | yes | NO |
| 2026-09-15-verify-layer2-phases-0826.txt | yes | NO |
| 2026-09-15-verify-layer2-phases-0947.txt | yes | NO |
| 2026-09-15-verify-layer2b-0827.txt | yes | NO |
| 2026-09-15-verify-layer2b-0947.txt | yes | NO |
| 2026-09-15-verify-layers-1233.txt | yes | NO |
| 2026-09-15-verify-rows-0829.txt | yes | NO |
| 2026-09-15-verify-rows-0950.txt | yes | NO |
| 2026-09-15-verify-rows-1235.txt | yes | NO |
| 2026-09-15-verify-rows2-0830.txt | yes | NO |
| 2026-09-15-verify-shell-0826.txt | yes | NO |
| 2026-09-15-verify-shell-0944.txt | yes | NO |
| 2026-09-15-verify-shell-1234.txt | yes | NO |
| 2026-09-15-verify-summary-0823.txt | yes | NO |
| 2026-09-15-verify-summary-0942.txt | yes | NO |
| 2026-09-15-verify-summary-1233.txt | yes | NO |
| 20260915T043416Z-f28cf4d | yes | NO |
| 20260915T052344Z-c1066b5 | yes | yes |
| 20260915T060556Z-e17d0f5 | yes | NO |
| 20260915T062147Z-b69b880 | yes | yes |
| 20260915T070539Z-0d04804 | yes | NO |
| 20260915T131907Z-762bde4 | NO | yes |
| 20260915T143747Z-7c3d750 | yes | NO |
| 20260915T172800Z-f7a1ccb | yes | yes |
| 20260915T203705Z-f8053c6 | yes | NO |

Not found elsewhere: 1 (2026-09-15-predeploy-baseline-6d.txt; the file exists on disk under `evidence/`, so the
record is not lost; the repair entry cites it). Not named by any journal entry: 3
(2026-09-15-predeploy-baseline-6d.txt, 2026-09-15-t4-explain-1000.txt, 20260915T131907Z-762bde4; the second is named by a ledger).

## 5. Reviewer findings

(filled by the independent read-only reviewer in Task 10)
