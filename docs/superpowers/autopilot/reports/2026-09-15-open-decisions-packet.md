# Open decisions packet (user-side items), 2026-09-15 12:20 CT

Prepared at the user's request ("Write a packet for me to review the open decisions I need to make to help the loop").
Rendered page: https://claude.ai/artifact/4wRJo92rcU5UUU9dhMt6Z1. Sources: roadmap.md User-side TODOs, journal 224-236,
`reports/2026-09-15-storage-retention-proposal.md`, results/row72-review.md (I-3/I-4). The user answers by item number
in chat; each answer is journaled as a `decision` entry quoting the user verbatim with the time. The loop edits none of
the files named as "your edit".

## Decide this week

**Revised 13:46 CT.** Items 2-10 were ruled at 12:42 CT (relayed, journal 239) and confirmed in the loop's session at 12:53 CT (journal 240; the four one-time edits landed as ab353cc). Open: items 1 and 16.

1. **Storage retention** (due 2026-09-22; invariant 5, the user executes). 121.88 of 600 GB, 13-16 GB/day; 480 GB band
   about Oct 7-12, 600 GB about Oct 14-21, filesystem gate about a week later. Options 0-5 as sized in the proposal.
   Loop's lean: option 1 with a one-week lag after an off-host checksummed copy and a partition decrypt drill.
2. **Three documentation lines stale after revision 0013** (row 72 review I-3/I-4; the alembic verify row reads FAIL by
   the letter from today's release until edited): verify.md line 486 expected `0013_nw_executor_version`;
   docs/runbooks/alembic.md 28-40 add the 0013 row and move the pinned-head sentence; the Amendment 6 record in
   docs/superpowers/reviews/2026-09-07-phase2-preregistration.md 163-169 gains the sub-population sentence.
   Loop's lean: edit all three today, or "plan task" to defer to a plan's last task.
3. **Amendment 0.18 extension to `harness rescore` / `order_rescores`**: yes or no (journal 232). Loop's lean: no.
4. **Futures request budget** in verify.md: 200 (contract) vs 500 (spec, code; job 208 used 468). Loop's lean: 500.
5. **R4 replacement wording** (journal 128) pasted into the rulings table with the Omarchy target names. No behavior change.

## Hands-on when convenient

6. LAN listener files for 4.6 (owner password hash, TLS key/cert, install on phone/laptop); the `lan` profile stays off until all exist.
7. Host time-sync unit: `systemd-time-wait-sync.service` enabled and `After=/Wants=time-sync.target` on the stack's unit (journal 175 item 2).
8. Gate measurement boundary (`GATE_ELIGIBLE_FROM_ORDER_ID` / `_RUN_ID`, set together). Loop's lean: decide with 6F's period (item 13).
9. Odds API 5M tier (U1): health reports a 5,000,000 budget; confirm so the TODO closes.
10. Kalshi demo account (optional; phase 4 demo smoke only).

## Coming up (not askable yet)

11. 6D holding/capacity policy adoption, after the Thu 2026-09-17 NFL-window acceptance rows.
12. 6E environment acceptance, after the corrected-workload benchmark.
13. 6F dated pre-registration amendment (revised selection/confirmation dates; U8 overrides R7's Sep 21/28).
14. Off-site copies (R5/U7): standing, nothing to decide.
15. The legal decision before any live trading (gate 1; never autonomous).

## Added 13:46 CT

16. **Executor backlog: the loop-metrics verify row reads FAIL** (carried fix 78, journal 241). p95 8,423 ms against 7,500 at 13:40 CT with 0 open orders;
    `exec.nw_pending` 2,878 (09:30 CT) -> 5,055 (13:30 CT), about 300 per 30 minutes; not a restart artifact (17,314 on 7c3d750 at 12:00 CT).
    Options: (a) a per-loop time budget for counterfactual advances (opus hotfix on the user's yes; loop's lean); (b) accept until Thursday's
    window drains the backlog and settle the cadence in 6D's policy (the loop gates at the next FAIL reading); (c) other. Item 1 note: the
    trailing 22.5 GB/day rate reported by the user's other session is recorded, unverified, in journal 239.

**Item 16 ruled 2026-09-15 13:55 CT (journal 242): option (c), a set-based counterfactual write path as an executor hotfix (`fix-2026-09-15-executor-batch`, opus/opus); no budget (§0.13c stays the user's); rows 79/80 added as follow-ups. Open: item 1.**

## Added 16:03 CT

17. **Residual executor loop time after fix 78 (§0.13c; carried fixes 78/79, journal 244).** Fix 78 live 15:40 CT: median loop 10.2 -> 7.6 s, p95 27.9 -> 13.9 s
    over the surrounding half hours, errors 0, no measured value changed; heartbeat p95 13,850 ms still over 7,500 at 6,387 pending tracks (+280 per 25 min).
    Residual is the read side: row 79's per-ticker print rescan from the earliest placement (34,014 rows a loop at 13:50 CT) and one simulation walk per pending row.
    Options: (a) bound the rescan as a second hotfix (loop's lean; read-side, no measured value moves); (b) a per-observation budget or round-robin (the user's §0.13c amendment);
    (c) accept until the weekend drains the backlog; (d) other. The FAIL stands and does not gate (journal 242).

**Item 17 ruled 2026-09-15 16:57 CT (journal 245): option (d), batch the clean-market cursor-advance writes plus phase timings, as a second fix-78-shaped hotfix (`fix-2026-09-15-executor-batch-2`); (a) deferred by the controller; (b) not needed; §0.13c untouched. Open: item 1.**

## Added 22:23 CT

### 18. Fix 78 after both parts: the executor loop is still over its bound, and the phase timings now say why (gate, journal 252)

**Status.** Part 2 (batched clean-market cursor advances, phase timings) was reviewed (opus, APPROVED WITH MINORS 0/0/2), passed the full suite (4,185) and was released app-only as 49cf5f4 at 21:57 CT. It did what it claimed: loops with no per-row rows run 12.0-13.8 s against a 29.3 s median before. But `p95_loop_ms` over the first 15 loops is 45,592 ms against the 7,500 ms bound, `loops_skipped` keeps rising (35 in the first 20 minutes), and the loop is gated on the daily "same item twice running" ceiling. Errors 0, tape fresh, Layer 3 6/6.

**The numbers** (evidence/2026-09-15-fix78b-judgement-2218.txt), per loop at 9,863 pending rows on 140 tickers, 58 games, 150 open orders:

| phase | per loop |
|---|---|
| `exec.phase_tape_ms` (per-ticker prints and deltas read) | 1.3-2.0 s |
| `exec.phase_walk_ms` (the pure simulation of every clean-ticker pending row, to decide whether its write moves) | 5.1-7.7 s |
| `exec.phase_batch_ms` (the dirty accrual, close and VALUES statements) | 1.6-5.3 s |
| `exec.phase_per_row_ms` / `exec.per_row_n` (rows still taking a savepoint) | 0-47 s / 0-842 rows (about 40-110 ms a row) |
| loop with `per_row_n` = 0 | 12.0-13.8 s |
| loop with `per_row_n` 116-842 | 31-65 s |

The tape read is not the lever (row 79's rescan would save about a second), so option (a) of item 17 is not dispatched. The walk alone is near the bound at this backlog. The per-row rows are the ones whose walk inserts a fill or a crossing, the two re-anchor branches, cursors `_sim_book` must query for, and the open orders (per-row by your ruling); they are not yet counted by cause. Expiries: Thu 692, Fri 380, Sat 5,113, Sun 3,458, Mon 220, so Thursday's window runs at about 9,200 pending.

**Options.**
- (a) §0.13c: a per-loop time budget or round-robin over pending tracks. Bounds the loop regardless of N. Changes `nw_dirty_minutes` accrual (ruling I-4): yours.
- (b) A third hotfix, no semantics change: a per-ticker pre-filter so the walk simulates only rows on tickers with new prints or deltas since their last walk (today every clean-ticker row is simulated to learn that it is quiet), and per-cause counters for the per-row population (fill, crossing, re-anchor, `_sim_book`, open order, load failure). Expected: the walk falls to well under a second when most tickers are quiet between loops; the per-row remainder is then measured for its own lever. Reversible by revert; identity test as in parts 1 and 2. Cost if wrong: one more review/suite/release cycle (about 90 minutes) and the bound still missed on burst loops.
- (c) Live with the FAIL until the weekend drain and re-judge Monday. Thursday's window runs with skipped loops.
- (d) Lengthen `exec_period_s`. An executor setting: a pre-registration amendment, yours.

**Recommendation.** (b) now, then re-judge twenty minutes after its restart; hold (a) for you if (b)'s numbers still miss, because nothing that batches writes reaches the bound at this backlog and (a) is the only lever that bounds the loop for any N. The loop implements nothing until you answer (journal 242). Answer in chat as "item 18: (b)" or your own wording; the loop records it verbatim as a decision entry.

## Added Wed 2026-09-16 10:12 CT

### 18. Outcome

Ruled 07:20 CT Wed (journal 253): (a) restricted with (b)'s batching and counters, one hotfix. Shipped as fix 78 part 3, released app-only eac4414 at 09:36 CT (journal 255), judged PASS at 09:57 CT with the heartbeat p95 at 6,974 ms against 7,500 (journal 256); row 78 closed. Loops run 5.7-7.3 s at about 12,600 pending rows. The measurement note's window is 13:40 CT Tue to 09:36 CT Wed. Watched: one 30 s loop at 09:48 CT with about 25 s outside the timed phases.

### 19. Veto spend caps: the ISO-week cap binds early on NFL Sunday at the current rate

The $25 daily cap (`veto_daily_usd_cap`) has bound every day since Sun 13 Sep ($24.41-24.60 a day, 268-947 calls). The weekly cap (`veto_weekly_usd_cap`, $150) sums the ISO week, Monday to Sunday; Mon-Wed spent $73.30, so at $24.4 a day the week reaches about $147 by Saturday night and Sunday has about $3.50 before every call is refused. The veto is then dormant for the NFL slate. Options: (a) keep both caps and accept a dormant Sunday; (b) raise the weekly cap to $175 (7 × $25) so only the daily cap binds, at most $25 more a week; (c) lower the weekday daily cap (for example $15 Mon-Thu) to bank about $40 for Sunday; (d) something else. Loop's lean: (b). Gate 6: the caps and their code are the user's; the loop changes nothing until ruled. Due before Sat 19 Sep.

## Added Thu 2026-09-17 09:10 CT

### 19. Update

Thursday's $24.40 was spent by 09:00 CT (578 calls), so the veto is dormant for tonight's two games. The ISO week stands at $97.70 through Thursday; the Sunday projection in item 19 is unchanged.

### 20. Executor loop over the bound again: the print rescan (row 79) is now the largest phase

**What happened.** The loop-metrics row passed twice on 747791c on Wednesday morning (p95 6,974 and 6,737 ms, journal 256/258). At the Thursday daily line it reads FAIL: heartbeat p95 17,809 ms against 7,500, loops skipped 4,494 of 44,002 since the restart, median loop 10.4 s and 203 of 1,179 sampled loops over 15 s since the release. Evidence: `evidence/2026-09-17-daily-0857.txt`.

**Cause, measured.** Part 3's budget holds (`exec.phase_walk_ms` 1.2-1.7 s every hour). The phase that grew is the tape read, which your item 18 ruling left unbudgeted:

| Hour (CT) | pending rows | tape ms | walk ms | batch ms | per-row ms | loop ms |
|---|---|---|---|---|---|---|
| Wed 10 | 13,001 | 1,885 | 1,635 | 307 | 505 | 7,210 |
| Wed 16 | 16,478 | 3,909 | 1,416 | 256 | 791 | 10,472 |
| Wed 22 | 19,929 | 6,471 | 1,181 | 219 | 1,170 | 12,617 |
| Thu 08 | 21,563 | 6,537 | 1,710 | 228 | 827 | 14,209 |

`_tape` re-reads every print since the earliest `placed_at` on each ticker, every loop (row 79, your follow-up of journal 242). That is now 112,234 `venue_trades` rows a loop over 149 tickers (34,014 when you measured it Tuesday; item 18's note that it was worth about a second no longer holds). The oldest pending row was placed Sun 13 Sep, and rows do not leave until their game's expiry. Monday's game window printed 33,000 to 105,000 trades an hour, so tonight's window adds to the rescan every loop and loops of 25 s or more are likely during the 6D/6B acceptance read. The remainder outside the timers (about 4-5 s, and the 150-order placement loops at 27-38 s) is row 82.

**Why it is yours.** Row 79 sits in Watch as your ruled follow-up ("do not fix them here"), and a Watch row returns to Open only on your ruling. Journal 242 also says to bring a still-failing p95 back as a packet item. Nothing is dispatched.

**Options.**
- (a) Hotfix row 79 now, value-identical: keep each ticker's prints in executor memory and refresh them incrementally. Both writers of `venue_trades` are insert-only (`on_conflict_do_nothing`; no UPDATE exists under `harness/`), so a per-ticker `count(*)` over the same window proves the cache complete each loop; a mismatch that the rows at or after the cached maximum `ts` do not explain (a late REST backfill) falls back to today's full read for that ticker. Same window, same rows, same order, same dedupe; replay keeps today's read. Opus implementer and opus reviewer, an identity test against today's `load_prints` including a late backfill, app-only release. Ships today only if the review and the full suite are clean by about 14:00 CT; otherwise Friday morning.
- (b) Leave it through tonight and measure the window as it is; take (a) Friday morning. The acceptance read then runs with skipped ticks, and dirty time accrues at 15 s nominal per observed loop as in journal 253's measurement note.
- (c) Narrow the SQL window to each ticker's lowest print floor. Cheaper still, but a late REST backfill above a floor would no longer be applied: that changes stored values. Not recommended.
- (d) Your own wording.

**Recommendation.** (a). Answer in chat as "item 20: (a)" or your own wording; the loop records it verbatim. Measurement note either way: from about Wed 19:00 CT loops ran 15-17 s against the 15 s period (longer on placement loops), so dirty time accrued at 15 s nominal per observed loop from then until a fix ships.

## Added Thu 2026-09-17 10:00 CT

### 20. Outcome and correction

Ruled 09:50 CT Thu (journal 262): (a) amended by your review session. The print cache is built and reviewed today from `fix-79-brief-amended.md` and released Friday 2026-09-18 morning, app-only; a timers-only batch (row 82) goes first and ships today only if clean by early afternoon. Rows 79 and 82 are Open.

**Correction.** Item 20's sentence that tonight's window "adds to the rescan every loop" with loops of 25 s or more likely is withdrawn: every pending row on tonight's games expires by 19:05 CT, so in-game prints never enter a pending window. The cache alone will not pass the loop-metrics row (p95 is about 11 s with the tape phase removed). Option (a)'s "insert-only" premise was wrong as drafted (`harness/normalize/runner.py:355` deletes `source='rest'` rows on reprocess); the amended brief guards on count, minimum and maximum `ts` in one statement.

**Still open from the ruling file's OPTIONAL lines:** (1) may the loop-metrics row read FAIL through these two releases without tripping "the same verify item failing twice running"; (2) do the executor-coupled 6B section 3 and 6D acceptance rows move to Friday's or Saturday's NCAAF window.

