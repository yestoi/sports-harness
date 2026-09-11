**Phase 5 independent review: observation record — September 11, 2026**

Local revision: `6eed2d8d49a7bf1f107dfd83141b8eeef556b620`. Deployed revision observed through `/healthz`: `7c3d555`. Observations below were collected around 11:01–11:04 America/Chicago (16:01–16:04 UTC). Values are snapshots, not a continuously monitored interval. Production access was read-only: cached snapshot GETs, host metrics, and bounded SQL with `default_transaction_read_only=on` and `statement_timeout='3s'`. No service/configuration changes or full-table tape scans were performed.

**Actual and counterfactual fill rows at 16:02:30 UTC.** Query grouped non-replay `fills` joined to non-replay `orders`, by variant, fill method and `has_print`. Orders and games are distinct within each row, not additive across rows.

| Variant | Method | Has print | Fill rows | Orders | Games | Contracts |
|---|---|---|---:|---:|---:|---:|
| sharp_two_sided | no_watcher | true | 393 | 113 | 7 | 10,364.88 |
| sharp_two_sided | snapshot_cross | false | 6 | 6 | 1 | 575.00 |
| sharp_two_sided | snapshot_cross | true | 41 | 41 | 4 | 3,661.00 |
| sharp_direct | no_watcher | true | 258 | 85 | 7 | 7,668.85 |
| sharp_direct | queue_model | true | 2 | 1 | 1 | 38.92 |
| sharp_direct | snapshot_cross | false | 6 | 6 | 1 | 575.00 |
| sharp_direct | snapshot_cross | true | 18 | 18 | 3 | 1,517.16 |
| constrained | no_watcher | true | 23 | 10 | 3 | 994.00 |
| constrained | snapshot_cross | true | 5 | 5 | 1 | 445.00 |

There were no queue_model rows for sharp_two_sided or constrained. The two actual rows belong to order 157, ticker `KXNCAAFTOTAL-26SEP12MTUMRSH-59`, YES at 0.45. Both filled at `2026-09-08 15:07:15.332+00`, for 25 and 13.92 contracts. Placement was `2026-09-08 14:36:47.579399+00`; expiry `2026-09-12 22:50:00+00`; cancellation of the remainder `2026-09-08 15:12:06.083229+00`, reason `fair_stale`. Both rows have a WS print and neither is after expiry. This review did not replay that order to determine whether the queue arithmetic defect affected it.

**Non-replay orders at the same observation.**

| Variant | fair_stale cancels | reprice cancels | signal_rejected cancels | venue_move cancels | Expired | Open |
|---|---:|---:|---:|---:|---:|---:|
| sharp_two_sided | 5,024 | 88 | 2 | 7 | 2 | 4 |
| sharp_direct | 2,691 | 40 | 1 | 3 | 1 | 2 |
| constrained | 380 | 1 | 26 | 0 | 1 | 0 |

Total 8,273 orders; 8,095 cancelled for stale fair (97.85%). This is cancellation incidence across all placed order rows, not a distinct-opportunity rejection rate. Repeated placements/variants share market opportunities.

**Cached Floor snapshot generated 16:01:12.072106 UTC.** Six-hour funnel: 1,001 candidate rows, 1,383 reported intents, 276 placements, 42 reported fills; skips fair_stale 489, book_dirty 481, exec_capacity 137; cancellations fair_stale 270. The fills include counterfactual methods and the intent field sums decision events, so these figures must not be converted into an ordinary unique-candidate conversion funnel. Exposure showed one primary position, 38.92 contracts and $17.51 stake; gate and constrained had zero positions.

**Current performance and coverage.**

- Host memory 7,717 MB total, 2,836 MB available, 1,978 MB swap resident; the two one-second vmstat intervals had zero swap-in/out and zero I/O wait. Resident swap alone is not evidence of active thrashing.
- Cached snapshot elapsed times: floor 85 ms, pulse 165 ms, study 204 ms, gate 1 ms, ticket 4 ms; all fresh with error null.
- Executor heartbeat 16:02:11 UTC: 6 open orders, last loop 6,647 ms, rolling p95 10,552 ms, 538 accumulated skipped loops, 132 dirty markets, last_error null. The 538 figure is cumulative, not today's count.
- The preceding 30 minutes had 26 sampled `exec.loop_ms` values: mean 8,506 ms, p95 13,949.25 ms, max 17,059 ms. These are telemetry samples, not every executor iteration. `exec.tape_lag_tickers` summed to zero in that window.
- A bounded read of the newest 1,500 runs restricted to the last six hours found 7 runs with pricing diagnostics, from 13:15:42 to 15:33:42 UTC. Six exhausted their budget; four ran zero variants; gate and primary each ran in three. This small morning sample is not a full game-day rate.
- Run 11468 at 15:18:12 UTC recorded 451 direct fairs, 2,213 derived fairs, 1,937 no-sharp entries and `budget_exhausted=true`, but `variants_run=[]`, `variants_skipped=[]`, `gate_variant_missing=false`, `gate_variant_id=null`. Run 11489 at 15:33:42 ran gate, primary, wide_band and constrained, skipping three others. Both used a 45-second pricing budget.
- Five consecutive completed settlement runs starting 11:25:58 through 15:38:42 UTC had status ok but budget_exhausted true, with markouts exhausting budget and report_wtd skipped for budget. Earlier interrupted runs remained marked running.
- Housekeeping at 09:15 UTC reported database size 46.302 GB, estimated growth 8.942 GB/day; this is a historical rate estimate, not a storage forecast guarantee.
- At 16:04 UTC all six open orders had 27–28 accumulated dirty minutes.

**Real multiplexed tape, latest 12 rows at 16:04 UTC.** Query: `select id,ticker,sid,seq,kind,ts from orderbook_events where ts>now()-interval '1 minute' order by id desc limit 12`. All rows were deltas on sid 2. Listed in arrival order below. The stream has no sequence gap; individual tickers legitimately do.

| Event ID | seq | Ticker |
|---:|---:|---|
| 59955673 | 50604 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955674 | 50605 | KXNCAAFSPREAD-26SEP12TTUORST-TTU25 |
| 59955675 | 50606 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955676 | 50607 | KXNCAAFTOTAL-26SEP12TOWSSCAR-56 |
| 59955677 | 50608 | KXNCAAFTOTAL-26SEP12TOWSSCAR-56 |
| 59955678 | 50609 | KXNCAAFSPREAD-26SEP12WKUUGA-UGA40 |
| 59955679 | 50610 | KXNCAAFSPREAD-26SEP12TTUORST-TTU25 |
| 59955680 | 50611 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955681 | 50612 | KXNCAAFSPREAD-26SEP12WKUUGA-UGA40 |
| 59955682 | 50613 | KXNCAAFSPREAD-26SEP12TENNGT-TENN12 |
| 59955683 | 50614 | KXNFLTOTAL-26SEP13NYJTEN-39 |
| 59955684 | 50615 | KXNFLTOTAL-26SEP13NYJTEN-39 |

Timestamps span `16:04:42.928+00` to `16:04:43.010+00`. TENNGT's sequence is 50604, 50606, 50611, 50613. `BookState.apply_delta` treats those legitimate jumps as dirty. This establishes the live input condition behind the pure reproduction; attribution of every dirty minute requires a corrected replay.

**Validation.** The execution reviewer ran 109 existing tests in `test_exec_plan.py`, `test_fills.py`, and `test_fills_tape.py`: all passed. Root ran `env -u DATABASE_URL_TEST .venv/bin/pytest tests/test_book.py tests/test_cadence.py -q -rA`: 26 passed, 21 database-dependent tests skipped. Root independently ran the adjacent reproduction script, confirming false dirty books, rejected-intent placement, a post-expiry simulated fill, double queue depletion, and underpowered confirmation. No full integration suite or production replay was run. Passing existing tests does not negate the demonstrated defects; some existing assertions encode the defective behavior.
