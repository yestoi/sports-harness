# Weekly report 2026-W37

- Week: 2026-09-07T05:00:00+00:00 to 2026-09-14T05:00:00+00:00 (Monday 00:00 America/Chicago, ISO week 37)
- Build: `a193fd0`
- Generated: 2026-09-08T16:56:09.722823+00:00
- Gate criteria hash: `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5`
- Executor `config_hash` values seen this week: `15a491be8fad9588d9d5465d5a159a6cef8b32642afea11680ba71d36c5d176b`, `d6962419ca950dd003f7f8283bc12ab01c36b9fd40e5654901353780d9a9cb9f`, `ef06087f4af152a62a85ca59aef4efbe08c9ca6ce24fbf1277562e28938267bd`

## Table 1 (t1): funnel

Funnel per registered variant x sport, over the week's non-replay rows. `fill_rate` is orders with at least one `queue_model` fill divided by orders; `markets_scanned` is variant-independent (distinct markets with a gap snapshot). `tick_coverage` is the share of the week's pricing ticks on which the variant was scored at all (distinct `signals.run_id` over distinct `market_gap_snapshots.run_id`); after Amendment 4 the gate variant and the primary are at 100 % by construction and the secondaries rotate, so every cross-variant comparison in tables 2 and 4 is read against this column.

| variant | sport | markets_scanned | signals | candidates | orders | fills | fill_rate | distinct_markets | distinct_market_days | tick_coverage |
|---|---|---|---|---|---|---|---|---|---|---|
| sharp_direct | nfl | 772 | 44244 | 2000 | 128 | 0 | 0.0000 | 34 | 34 | 0.7679 |
| sharp_direct | ncaaf | 3819 | 659388 | 11403 | 256 | 2 | 0.0039 | 72 | 72 | 0.7679 |
| constrained | nfl | 772 | 39434 | 391 | 2 | 0 | 0.0000 | 1 | 1 | 0.7607 |
| constrained | ncaaf | 3819 | 648077 | 1613 | 5 | 0 | 0.0000 | 5 | 5 | 0.7607 |
| nfl_only | nfl | 772 | 46026 | 2096 | 0 | 0 | -- | 0 | 0 | 0.7786 |
| nfl_only | ncaaf | 3819 | 666706 | 0 | 0 | 0 | -- | 0 | 0 | 0.7786 |
| no_velocity | nfl | 772 | 46204 | 2098 | 0 | 0 | -- | 0 | 0 | 0.7786 |
| no_velocity | ncaaf | 3819 | 670635 | 11589 | 0 | 0 | -- | 0 | 0 | 0.7786 |
| sharp_plus_derived | nfl | 772 | 46204 | 5348 | 0 | 0 | -- | 0 | 0 | 0.7714 |
| sharp_plus_derived | ncaaf | 3819 | 658933 | 37036 | 0 | 0 | -- | 0 | 0 | 0.7714 |
| sharp_two_sided | nfl | 772 | 9264 | 408 | 32 | 0 | 0.0000 | 16 | 16 | 0.0214 |
| sharp_two_sided | ncaaf | 3819 | 45228 | 746 | 86 | 0 | 0.0000 | 43 | 43 | 0.0214 |
| sharp_two_sided#e82fcd0a1e99 | nfl | 772 | 0 | 0 | 0 | 0 | -- | 0 | 0 | 0.0000 |
| sharp_two_sided#e82fcd0a1e99 | ncaaf | 3819 | 0 | 0 | 0 | 0 | -- | 0 | 0 | 0.0000 |
| wide_band | nfl | 772 | 37890 | 1833 | 0 | 0 | -- | 0 | 0 | 0.7500 |
| wide_band | ncaaf | 3819 | 636371 | 13043 | 0 | 0 | -- | 0 | 0 | 0.7500 |

## Table 2 (t2): CLV per variant

Net-of-fee CLV per registered variant against each benchmark. The primary's row is its level mean; every other row is the paired difference (variant - primary) on shared gap snapshots, clustered by game. Executed variants are read from `order_clv`; the rest from `gap_outcomes` at the variant's own `price_target`. Stale benchmark rows are excluded (their share is in table 8). Family C: Holm at alpha = 0.1 over the contrasts against `pinnacle_t5`. `gate` is the newest stored gate verdict for the variant as of the end of the week (`harness gate` writes it); `*` marks the row the phase gate is judged on.

| variant | tier | basis | pinnacle_t5 | consensus_t5 | consensus_t60 | consensus_t180 | opening_first_seen | kalshi_mid_t5 | kalshi_last_trade_pre_kick | novig_devig_t5 | result | holm(pinnacle_t5) | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sharp_direct | primary | level | grey +0.0558 -- n=6 G=1 | grey +0.0558 -- n=6 G=1 | -- | -- | -- | grey +0.0557 -- n=6 G=1 | -- | -- | grey +0.0557 -- n=6 G=1 | -- | -- |
| constrained | secondary | contrast | grey +0.0000 -- n=6 G=1 | grey +0.0000 -- n=6 G=1 | -- | -- | -- | grey +0.0000 -- n=6 G=1 | -- | -- | grey +0.0000 -- n=6 G=1 | grey | -- |
| nfl_only | secondary | contrast | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| no_velocity | secondary | contrast | grey +0.0000 -- n=6 G=1 | grey +0.0000 -- n=6 G=1 | -- | -- | -- | grey +0.0000 -- n=6 G=1 | -- | -- | grey +0.0000 -- n=6 G=1 | grey | -- |
| sharp_plus_derived | secondary | contrast | grey +0.0000 -- n=6 G=1 | grey +0.0000 -- n=6 G=1 | -- | -- | -- | grey +0.0000 -- n=6 G=1 | -- | -- | grey +0.0000 -- n=6 G=1 | grey | -- |
| sharp_two_sided | secondary | contrast | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | replay | contrast | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| wide_band | secondary | contrast | grey +0.0000 -- n=6 G=1 | grey +0.0000 -- n=6 G=1 | -- | -- | -- | grey +0.0000 -- n=6 G=1 | -- | -- | grey +0.0000 -- n=6 G=1 | grey | -- |

_family C: 0 contrast(s) tested, 7 greyed out (< 10 game clusters) and excluded from Holm._

## Table 3 (t3): adverse selection

Adverse selection per fill set, split by the feed that priced the order and by staleness at placement (the featured allowance, 220 s). Reprice chains are collapsed to episodes. The 1 m and 5 m markouts come off the venue mid; the 30 m and 120 m markouts off the sharp fair and only on `fair_changed` rows. `adverse_drift` is the anchor's own 0 m fair minus the fair at placement in the order's side space, on `fair_changed` rows only; positive means the fair moved our way. It is the placeholder for the `cross_fill` set by construction: `ZERO_M_ANCHORS` writes a 0 m markout for the `fill` and `nw_fill` anchors only, so a cross has no fair-at-fill row to difference. All net of the maker fee. Split by variant first: each exec variant is simulated as the sole participant, so pooling two variants' fills on one market would count the same tape twice. `episodes` counts every episode in the slice, filled or not; an episode belongs to the week its first order was placed in, so a reprice chain straddling Monday is counted once, in the earlier week.

| variant | fill_set | feed_kind | staleness | episodes | fills | markout_1m_mid | markout_5m_mid | markout_30m_fair | markout_120m_fair | adverse_drift |
|---|---|---|---|---|---|---|---|---|---|---|
| constrained | queue_model | featured | <= 220 s | 7 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | featured | <= 220 s | 7 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | featured | <= 220 s | 7 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| constrained | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| nfl_only | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| no_velocity | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | featured | <= 220 s | 384 | 2 | grey +0.0007 -- n=1 G=1 | grey +0.0007 -- n=1 G=1 | grey +0.0235 -- n=1 G=1 | -- | grey +0.0000 -- n=1 G=1 |
| sharp_direct | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | featured | <= 220 s | 384 | 6 | grey +0.0007 -- n=2 G=1 | grey +0.0007 -- n=2 G=1 | grey +0.0235 -- n=2 G=1 | -- | grey -0.0014 -- n=2 G=1 |
| sharp_direct | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | featured | <= 220 s | 384 | 2 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_direct | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_plus_derived | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | featured | <= 220 s | 118 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | featured | <= 220 s | 118 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | featured | <= 220 s | 118 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| sharp_two_sided#e82fcd0a1e99 | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + no_watcher | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | featured | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | featured | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | featured | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | alternate | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | alternate | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | alternate | unknown | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | unknown | <= 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | unknown | > 220 s | 0 | 0 | -- | -- | -- | -- | -- |
| wide_band | queue_model + snapshot_cross | unknown | unknown | 0 | 0 | -- | -- | -- | -- | -- |

## Table 4 (t4): mispricing map

Mispricing map: price x time-to-kickoff x sport x market type, 72 cells per fair source. Sign convention: `gap_mid` is stored as `fair - mid`, so a positive value means the venue is cheap relative to the sharp fair (the spec's 'venue mid minus sharp fair' is its negative). Families A/B, the `posterior` shrinkage and the §9.6 count run on `gap_mid` restricted to the `feed featured` rows (user decision 2026-09-08: the addendum's own words are 'the headline H2 claim is from `feed_kind = featured`'). The `gap_mid`, `gap_maker_net` and `clv_mid_p` panel cells, the three `feed` columns and the four `stale` columns are display columns pooled over every feed, outside the families. `posterior` is the empirical-Bayes interval `m~ +/- t_{0.95, G-1} sqrt(B se^2)` shrunk within sport x market type; `bh` is Benjamini-Hochberg at q = 0.1 on the two-sided cluster-robust t of `gap_mid` over `feed featured` (spec §9.7's H2 quantity), family A the direct cells and family B the derived cells. `gap_maker_net`, the tradeable version of the same gap, is reported beside it and is not in either family. The §9.6 criterion is judged on `posterior`, and a stratum with no heterogeneity contributes no significant cell.

| fair_source | price_bucket | ttk | sport | market_type | gap_mid | gap_maker_net | clv_mid_p | posterior | bh | feed featured | feed alternate | feed unknown | stale < 120 | stale 120-300 | stale 300-1000 | stale > 1000 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| direct | 20-35 | > 24 h | nfl | moneyline | grey -0.0029 [-0.0058, +0.0000] n=309 G=4 | grey +0.0000 [-0.0045, +0.0045] n=309 G=4 | -- | grey -0.0011 [-0.0063, +0.0041] n=159 G=3 | grey | grey -0.0016 [-0.0078, +0.0046] n=159 G=3 | -- | grey -0.0043 [-0.0085, -0.0001] n=150 G=4 | grey -0.0028 [-0.0057, +0.0000] n=294 G=4 | grey -0.0032 [-0.0063, -0.0000] n=11 G=4 | grey -0.0070 [-0.0120, -0.0021] n=4 G=4 | -- |
| direct | 20-35 | > 24 h | nfl | spread | grey -0.0038 -- n=37 G=1 | grey -0.0021 -- n=37 G=1 | -- | -- | -- | -- | -- | grey -0.0038 -- n=37 G=1 | -- | -- | -- | -- |
| direct | 20-35 | > 24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | > 24 h | ncaaf | moneyline | flag -0.0043 [-0.0080, -0.0006] n=3420 G=15 | flag -0.0023 [-0.0059, +0.0013] n=3420 G=15 | -- | flag -0.0029 [-0.0061, +0.0003] n=2561 G=14 | - | flag -0.0039 [-0.0075, -0.0003] n=2561 G=14 | -- | flag -0.0053 [-0.0098, -0.0008] n=859 G=15 | flag -0.0042 [-0.0079, -0.0005] n=3378 G=15 | flag -0.0067 [-0.0105, -0.0028] n=28 G=15 | flag -0.0096 [-0.0142, -0.0049] n=14 G=14 | -- |
| direct | 20-35 | > 24 h | ncaaf | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | > 24 h | ncaaf | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | 3-24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | 3-24 h | ncaaf | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | 3-24 h | ncaaf | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 20-35 | < 3 h | ncaaf | moneyline | grey +0.0340 -- n=40 G=1 | grey +0.0359 -- n=40 G=1 | -- | -- | grey | grey +0.0340 -- n=40 G=1 | -- | -- | grey +0.0093 -- n=27 G=1 | grey -0.0337 -- n=1 G=1 | grey +0.0686 -- n=8 G=1 | grey +0.1489 -- n=4 G=1 |
| direct | 20-35 | < 3 h | ncaaf | spread | grey +0.1685 -- n=4 G=1 | grey +0.1699 -- n=4 G=1 | -- | -- | grey | grey +0.1685 -- n=4 G=1 | -- | -- | grey +0.1685 -- n=4 G=1 | -- | -- | -- |
| direct | 20-35 | < 3 h | ncaaf | total | grey +0.1710 -- n=9 G=1 | grey +0.1750 -- n=9 G=1 | -- | -- | grey | grey +0.1979 -- n=6 G=1 | -- | grey +0.1172 -- n=3 G=1 | -- | grey +0.1787 -- n=1 G=1 | grey +0.2017 -- n=5 G=1 | -- |
| direct | 35-50 | > 24 h | nfl | moneyline | flag -0.0035 [-0.0065, -0.0005] n=1056 G=12 | flag -0.0025 [-0.0055, +0.0006] n=1056 G=12 | -- | flag -0.0032 [-0.0061, -0.0002] n=636 G=12 | - | flag -0.0043 [-0.0077, -0.0009] n=636 G=12 | -- | flag -0.0023 [-0.0055, +0.0010] n=420 G=12 | flag -0.0035 [-0.0065, -0.0005] n=1011 G=12 | flag -0.0031 [-0.0065, +0.0004] n=34 G=12 | flag -0.0022 [-0.0073, +0.0029] n=11 G=11 | -- |
| direct | 35-50 | > 24 h | nfl | spread | grey -0.0001 [-0.0068, +0.0066] n=547 G=6 | grey +0.0006 [-0.0061, +0.0073] n=547 G=6 | -- | grey +0.0006 [-0.0029, +0.0042] n=303 G=6 | grey | grey +0.0024 [-0.0040, +0.0087] n=303 G=6 | -- | grey -0.0032 [-0.0111, +0.0047] n=244 G=6 | grey +0.0012 [-0.0053, +0.0076] n=474 G=6 | grey +0.0004 [-0.0070, +0.0078] n=16 G=6 | grey +0.0039 [-0.0087, +0.0165] n=6 G=5 | -- |
| direct | 35-50 | > 24 h | nfl | total | grey +0.0060 [+0.0011, +0.0109] n=301 G=4 | grey +0.0071 [+0.0031, +0.0111] n=301 G=4 | -- | grey +0.0060 [-0.0017, +0.0136] n=141 G=3 | grey | grey +0.0080 [-0.0036, +0.0196] n=141 G=3 | -- | grey +0.0042 [+0.0003, +0.0081] n=160 G=4 | grey +0.0070 [+0.0000, +0.0140] n=252 G=4 | grey +0.0063 [+0.0006, +0.0120] n=9 G=4 | grey +0.0065 [+0.0038, +0.0091] n=4 G=4 | -- |
| direct | 35-50 | > 24 h | ncaaf | moneyline | flag -0.0002 [-0.0035, +0.0032] n=2453 G=10 | flag +0.0016 [-0.0014, +0.0046] n=2453 G=10 | -- | flag -0.0004 [-0.0037, +0.0029] n=1840 G=10 | - | flag -0.0008 [-0.0045, +0.0028] n=1840 G=10 | -- | flag +0.0017 [-0.0013, +0.0048] n=613 G=10 | flag -0.0002 [-0.0036, +0.0032] n=2425 G=10 | flag +0.0015 [-0.0018, +0.0047] n=19 G=10 | grey +0.0014 [-0.0016, +0.0045] n=9 G=9 | -- |
| direct | 35-50 | > 24 h | ncaaf | spread | flag +0.0086 [+0.0060, +0.0112] n=2783 G=26 | flag +0.0107 [+0.0079, +0.0134] n=2783 G=26 | -- | flag +0.0079 [+0.0053, +0.0105] n=2118 G=18 | reject | flag +0.0081 [+0.0055, +0.0108] n=2118 G=18 | -- | flag +0.0101 [+0.0064, +0.0138] n=665 G=21 | flag +0.0085 [+0.0059, +0.0111] n=2726 G=26 | flag +0.0093 [+0.0062, +0.0124] n=25 G=18 | flag +0.0144 [+0.0063, +0.0225] n=26 G=17 | grey +0.0209 [+0.0051, +0.0367] n=6 G=5 |
| direct | 35-50 | > 24 h | ncaaf | total | flag +0.0083 [+0.0059, +0.0107] n=2039 G=18 | flag +0.0149 [+0.0124, +0.0173] n=2039 G=18 | -- | flag +0.0081 [+0.0056, +0.0105] n=1610 G=17 | reject | flag +0.0082 [+0.0058, +0.0107] n=1610 G=17 | -- | flag +0.0085 [+0.0057, +0.0113] n=429 G=11 | flag +0.0083 [+0.0059, +0.0107] n=2002 G=18 | flag +0.0084 [+0.0044, +0.0124] n=15 G=11 | flag +0.0082 [+0.0053, +0.0112] n=20 G=11 | grey +0.0067 [-0.0040, +0.0174] n=2 G=2 |
| direct | 35-50 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 35-50 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 35-50 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 35-50 | 3-24 h | ncaaf | moneyline | grey +0.0013 -- n=34 G=1 | grey +0.0020 -- n=34 G=1 | grey -0.0026 -- n=4 G=1 | -- | -- | -- | -- | grey +0.0013 -- n=34 G=1 | grey +0.0013 -- n=32 G=1 | grey -0.0033 -- n=1 G=1 | grey +0.0049 -- n=1 G=1 | -- |
| direct | 35-50 | 3-24 h | ncaaf | spread | grey -0.0056 -- n=68 G=1 | grey -0.0049 -- n=68 G=1 | grey -0.0071 -- n=8 G=1 | -- | -- | -- | -- | grey -0.0056 -- n=68 G=1 | -- | -- | -- | -- |
| direct | 35-50 | 3-24 h | ncaaf | total | grey -0.0020 -- n=40 G=1 | grey -0.0014 -- n=40 G=1 | grey +0.0430 -- n=8 G=1 | -- | -- | -- | -- | grey -0.0020 -- n=40 G=1 | -- | -- | grey -0.0042 -- n=1 G=1 | -- |
| direct | 35-50 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 35-50 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 35-50 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 35-50 | < 3 h | ncaaf | moneyline | grey -0.0024 -- n=138 G=1 | grey -0.0016 -- n=138 G=1 | -- | -- | grey | grey -0.0014 -- n=113 G=1 | -- | grey -0.0068 -- n=25 G=1 | grey -0.0041 -- n=131 G=1 | grey -0.0349 -- n=1 G=1 | grey +0.0434 -- n=3 G=1 | grey +0.0351 -- n=3 G=1 |
| direct | 35-50 | < 3 h | ncaaf | spread | grey -0.0017 -- n=258 G=1 | grey -0.0007 -- n=258 G=1 | -- | -- | grey | grey +0.0664 -- n=5 G=1 | -- | grey -0.0031 -- n=253 G=1 | grey +0.0640 -- n=3 G=1 | grey +0.0750 -- n=1 G=1 | grey +0.0650 -- n=1 G=1 | -- |
| direct | 35-50 | < 3 h | ncaaf | total | grey +0.0131 -- n=316 G=1 | grey +0.0148 -- n=316 G=1 | -- | -- | grey | grey +0.0324 -- n=71 G=1 | -- | grey +0.0075 -- n=245 G=1 | grey +0.0226 -- n=48 G=1 | grey +0.0362 -- n=3 G=1 | grey +0.0500 -- n=15 G=1 | grey +0.0720 -- n=5 G=1 |
| direct | 50-65 | > 24 h | nfl | moneyline | flag +0.0029 [-0.0002, +0.0059] n=1005 G=12 | flag +0.0039 [+0.0006, +0.0072] n=1005 G=12 | -- | flag +0.0028 [-0.0003, +0.0059] n=583 G=11 | - | flag +0.0037 [+0.0001, +0.0073] n=583 G=11 | -- | flag +0.0017 [-0.0012, +0.0046] n=422 G=12 | flag +0.0029 [-0.0002, +0.0060] n=960 G=12 | flag +0.0020 [-0.0010, +0.0050] n=33 G=11 | flag +0.0011 [-0.0035, +0.0058] n=12 G=12 | -- |
| direct | 50-65 | > 24 h | nfl | spread | grey -0.0041 [-0.0098, +0.0016] n=330 G=5 | grey -0.0034 [-0.0091, +0.0023] n=330 G=5 | -- | grey -0.0012 [-0.0050, +0.0026] n=212 G=4 | grey | grey -0.0026 [-0.0085, +0.0032] n=212 G=4 | -- | grey -0.0066 [-0.0132, -0.0001] n=118 G=5 | grey -0.0034 [-0.0103, +0.0035] n=284 G=4 | grey -0.0032 [-0.0103, +0.0039] n=8 G=4 | grey +0.0007 [-0.0729, +0.0742] n=2 G=2 | -- |
| direct | 50-65 | > 24 h | nfl | total | grey +0.0005 [-0.0052, +0.0063] n=226 G=4 | grey +0.0019 [-0.0054, +0.0093] n=226 G=4 | -- | grey +0.0026 [-0.0046, +0.0099] n=124 G=3 | grey | grey +0.0008 [-0.0096, +0.0111] n=124 G=3 | -- | grey +0.0002 [-0.0068, +0.0073] n=102 G=3 | grey -0.0000 [-0.0090, +0.0089] n=183 G=3 | grey -0.0010 [-0.0073, +0.0054] n=6 G=3 | grey -0.0039 -- n=1 G=1 | -- |
| direct | 50-65 | > 24 h | ncaaf | moneyline | flag +0.0035 [-0.0007, +0.0076] n=2371 G=10 | flag +0.0045 [+0.0003, +0.0088] n=2371 G=10 | -- | flag +0.0034 [-0.0004, +0.0071] n=1820 G=10 | - | flag +0.0040 [-0.0003, +0.0084] n=1820 G=10 | -- | flag +0.0016 [-0.0028, +0.0059] n=551 G=10 | flag +0.0035 [-0.0007, +0.0077] n=2344 G=10 | flag +0.0006 [-0.0028, +0.0040] n=19 G=10 | grey -0.0008 [-0.0079, +0.0064] n=8 G=8 | -- |
| direct | 50-65 | > 24 h | ncaaf | spread | flag -0.0045 [-0.0083, -0.0007] n=2501 G=25 | flag -0.0021 [-0.0061, +0.0019] n=2501 G=25 | -- | flag -0.0036 [-0.0074, +0.0001] n=1933 G=23 | - | flag -0.0041 [-0.0080, -0.0002] n=1933 G=23 | -- | flag -0.0060 [-0.0099, -0.0020] n=568 G=15 | flag -0.0044 [-0.0081, -0.0006] n=2464 G=25 | flag -0.0100 [-0.0139, -0.0062] n=22 G=16 | grey -0.0189 [-0.0350, -0.0027] n=13 G=7 | grey -0.0241 [-0.0932, +0.0451] n=2 G=2 |
| direct | 50-65 | > 24 h | ncaaf | total | flag -0.0026 [-0.0059, +0.0007] n=685 G=14 | flag +0.0014 [-0.0022, +0.0050] n=685 G=14 | -- | flag -0.0025 [-0.0065, +0.0014] n=549 G=11 | - | flag -0.0030 [-0.0071, +0.0011] n=549 G=11 | -- | grey -0.0010 [-0.0032, +0.0013] n=136 G=8 | flag -0.0020 [-0.0052, +0.0012] n=666 G=14 | grey -0.0109 [-0.0326, +0.0107] n=7 G=6 | grey -0.0301 [-0.0586, -0.0015] n=9 G=6 | grey -0.0329 [-0.1926, +0.1267] n=3 G=2 |
| direct | 50-65 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 50-65 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 50-65 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 50-65 | 3-24 h | ncaaf | moneyline | grey -0.0048 -- n=34 G=1 | grey -0.0041 -- n=34 G=1 | grey +0.0026 -- n=4 G=1 | -- | -- | -- | -- | grey -0.0048 -- n=34 G=1 | grey -0.0051 -- n=32 G=1 | grey +0.0033 -- n=1 G=1 | grey -0.0049 -- n=1 G=1 | -- |
| direct | 50-65 | 3-24 h | ncaaf | spread | grey -0.0058 -- n=68 G=1 | grey -0.0051 -- n=68 G=1 | grey -0.0012 -- n=8 G=1 | -- | -- | -- | -- | grey -0.0058 -- n=68 G=1 | grey -0.0060 -- n=22 G=1 | -- | -- | -- |
| direct | 50-65 | 3-24 h | ncaaf | total | grey -0.0012 -- n=88 G=1 | grey -0.0002 -- n=88 G=1 | grey +0.0461 -- n=4 G=1 | -- | -- | -- | -- | grey -0.0012 -- n=88 G=1 | grey -0.0090 -- n=7 G=1 | -- | grey -0.0108 -- n=1 G=1 | -- |
| direct | 50-65 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 50-65 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 50-65 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 50-65 | < 3 h | ncaaf | moneyline | grey -0.0002 -- n=138 G=1 | grey +0.0005 -- n=138 G=1 | -- | -- | grey | grey +0.0004 -- n=113 G=1 | -- | grey -0.0032 -- n=25 G=1 | grey +0.0013 -- n=131 G=1 | grey +0.0249 -- n=1 G=1 | grey -0.0451 -- n=3 G=1 | grey -0.0318 -- n=3 G=1 |
| direct | 50-65 | < 3 h | ncaaf | spread | grey -0.0124 -- n=301 G=1 | grey -0.0108 -- n=301 G=1 | -- | -- | grey | grey -0.0163 -- n=95 G=1 | -- | grey -0.0106 -- n=206 G=1 | grey -0.0096 -- n=85 G=1 | grey -0.0056 -- n=3 G=1 | grey -0.0254 -- n=20 G=1 | grey -0.0326 -- n=12 G=1 |
| direct | 50-65 | < 3 h | ncaaf | total | grey -0.0024 -- n=368 G=1 | grey -0.0001 -- n=368 G=1 | -- | -- | grey | grey -0.0150 -- n=90 G=1 | -- | grey +0.0016 -- n=278 G=1 | grey -0.0057 -- n=89 G=1 | grey -0.0258 -- n=2 G=1 | grey -0.0403 -- n=13 G=1 | grey -0.0371 -- n=11 G=1 |
| direct | 65-80 | > 24 h | nfl | moneyline | grey +0.0028 [-0.0017, +0.0073] n=360 G=4 | grey +0.0053 [-0.0003, +0.0110] n=360 G=4 | -- | grey +0.0024 [-0.0013, +0.0061] n=212 G=4 | grey | grey +0.0031 [-0.0011, +0.0073] n=212 G=4 | -- | grey +0.0024 [-0.0031, +0.0078] n=148 G=4 | grey +0.0028 [-0.0016, +0.0073] n=345 G=4 | grey +0.0010 [-0.0049, +0.0070] n=12 G=4 | grey +0.0028 [-0.0071, +0.0128] n=3 G=3 | -- |
| direct | 65-80 | > 24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | > 24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | > 24 h | ncaaf | moneyline | flag +0.0064 [+0.0030, +0.0098] n=3461 G=17 | flag +0.0095 [+0.0054, +0.0136] n=3461 G=17 | -- | flag +0.0055 [+0.0025, +0.0084] n=2585 G=15 | reject | flag +0.0063 [+0.0031, +0.0095] n=2585 G=15 | -- | flag +0.0067 [+0.0019, +0.0114] n=876 G=17 | flag +0.0064 [+0.0029, +0.0098] n=3416 G=17 | flag +0.0076 [+0.0039, +0.0113] n=30 G=16 | flag +0.0068 [+0.0017, +0.0119] n=15 G=15 | -- |
| direct | 65-80 | > 24 h | ncaaf | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | > 24 h | ncaaf | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | 3-24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | 3-24 h | ncaaf | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | 3-24 h | ncaaf | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| direct | 65-80 | < 3 h | ncaaf | moneyline | grey -0.0341 -- n=40 G=1 | grey -0.0323 -- n=40 G=1 | -- | -- | grey | grey -0.0341 -- n=40 G=1 | -- | -- | grey -0.0089 -- n=27 G=1 | grey +0.0287 -- n=1 G=1 | grey -0.0692 -- n=8 G=1 | grey -0.1501 -- n=4 G=1 |
| direct | 65-80 | < 3 h | ncaaf | spread | grey -0.2016 -- n=29 G=1 | grey -0.1975 -- n=29 G=1 | -- | -- | grey | grey -0.1941 -- n=13 G=1 | -- | grey -0.2076 -- n=16 G=1 | grey -0.2049 -- n=3 G=1 | grey -0.1882 -- n=1 G=1 | grey -0.1932 -- n=6 G=1 | grey -0.1871 -- n=3 G=1 |
| direct | 65-80 | < 3 h | ncaaf | total | grey -0.1762 -- n=12 G=1 | grey -0.1666 -- n=12 G=1 | -- | -- | grey | grey -0.1823 -- n=11 G=1 | -- | grey -0.1091 -- n=1 G=1 | grey -0.1822 -- n=4 G=1 | grey -0.1525 -- n=1 G=1 | grey -0.1848 -- n=4 G=1 | grey -0.1922 -- n=2 G=1 |
| derived | 20-35 | > 24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | > 24 h | nfl | spread | flag +0.0308 [+0.0271, +0.0345] n=9790 G=16 | flag +0.0374 [+0.0339, +0.0409] n=9790 G=16 | -- | flag +0.0306 [+0.0267, +0.0345] n=6131 G=16 | reject | flag +0.0308 [+0.0269, +0.0347] n=6131 G=16 | -- | flag +0.0308 [+0.0270, +0.0346] n=3659 G=16 | flag +0.0308 [+0.0271, +0.0345] n=9394 G=16 | flag +0.0303 [+0.0268, +0.0338] n=302 G=16 | flag +0.0318 [+0.0282, +0.0355] n=94 G=12 | -- |
| derived | 20-35 | > 24 h | nfl | total | flag -0.0517 [-0.0573, -0.0461] n=2098 G=12 | flag -0.0408 [-0.0472, -0.0344] n=2098 G=12 | -- | flag -0.0510 [-0.0564, -0.0456] n=1360 G=12 | reject | flag -0.0513 [-0.0567, -0.0459] n=1360 G=12 | -- | flag -0.0524 [-0.0589, -0.0458] n=738 G=12 | flag -0.0516 [-0.0572, -0.0461] n=2016 G=12 | flag -0.0538 [-0.0599, -0.0477] n=64 G=12 | grey -0.0452 [-0.0574, -0.0329] n=18 G=9 | -- |
| derived | 20-35 | > 24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | > 24 h | ncaaf | spread | +0.0345 [+0.0304, +0.0386] n=64197 G=49 | +0.0588 [+0.0546, +0.0630] n=64197 G=49 | -- | +0.0346 [+0.0305, +0.0386] n=48828 G=49 | reject | +0.0349 [+0.0308, +0.0389] n=48828 G=49 | -- | +0.0333 [+0.0287, +0.0379] n=15369 G=48 | +0.0345 [+0.0304, +0.0386] n=63344 G=49 | +0.0339 [+0.0296, +0.0383] n=513 G=49 | +0.0270 [+0.0207, +0.0333] n=328 G=44 | grey +0.0223 [+0.0040, +0.0407] n=12 G=4 |
| derived | 20-35 | > 24 h | ncaaf | total | -0.0247 [-0.0288, -0.0206] n=17354 G=38 | -0.0012 [-0.0059, +0.0035] n=17354 G=38 | -- | -0.0253 [-0.0298, -0.0207] n=13472 G=37 | reject | -0.0257 [-0.0303, -0.0211] n=13472 G=37 | -- | -0.0211 [-0.0262, -0.0160] n=3882 G=31 | -0.0247 [-0.0288, -0.0205] n=17154 G=38 | -0.0243 [-0.0307, -0.0178] n=110 G=32 | flag -0.0268 [-0.0347, -0.0189] n=84 G=23 | grey -0.0617 [-0.2057, +0.0824] n=6 G=2 |
| derived | 20-35 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | 3-24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | 3-24 h | ncaaf | spread | grey +0.0490 -- n=340 G=1 | grey +0.0509 -- n=340 G=1 | -- | -- | -- | -- | -- | grey +0.0490 -- n=340 G=1 | grey +0.0490 -- n=320 G=1 | grey +0.0459 -- n=10 G=1 | grey +0.0514 -- n=10 G=1 | -- |
| derived | 20-35 | 3-24 h | ncaaf | total | grey -0.0335 -- n=72 G=1 | grey -0.0300 -- n=72 G=1 | -- | -- | -- | -- | -- | grey -0.0335 -- n=72 G=1 | grey -0.0336 -- n=69 G=1 | -- | grey -0.0332 -- n=3 G=1 | -- |
| derived | 20-35 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | < 3 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 20-35 | < 3 h | ncaaf | spread | grey +0.0473 -- n=1614 G=1 | grey +0.0521 -- n=1614 G=1 | -- | -- | grey | grey +0.0462 -- n=1364 G=1 | -- | grey +0.0535 -- n=250 G=1 | grey +0.0474 -- n=1384 G=1 | grey +0.0220 -- n=18 G=1 | grey +0.0573 -- n=122 G=1 | grey +0.0374 -- n=90 G=1 |
| derived | 20-35 | < 3 h | ncaaf | total | grey -0.0143 -- n=428 G=1 | grey -0.0084 -- n=428 G=1 | -- | -- | grey | grey -0.0110 -- n=378 G=1 | -- | grey -0.0388 -- n=50 G=1 | grey -0.0195 -- n=322 G=1 | grey +0.0638 -- n=8 G=1 | grey +0.0254 -- n=62 G=1 | grey -0.0529 -- n=36 G=1 |
| derived | 35-50 | > 24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | > 24 h | nfl | spread | flag +0.0117 [+0.0064, +0.0170] n=5588 G=16 | flag +0.0152 [+0.0097, +0.0206] n=5588 G=16 | -- | flag +0.0123 [+0.0072, +0.0173] n=3678 G=16 | reject | flag +0.0124 [+0.0073, +0.0175] n=3678 G=16 | -- | flag +0.0103 [+0.0040, +0.0167] n=1910 G=16 | flag +0.0117 [+0.0065, +0.0170] n=5372 G=16 | flag +0.0112 [+0.0056, +0.0167] n=167 G=16 | flag +0.0104 [+0.0035, +0.0174] n=49 G=12 | -- |
| derived | 35-50 | > 24 h | nfl | total | flag -0.0059 [-0.0087, -0.0032] n=3086 G=12 | flag -0.0006 [-0.0033, +0.0021] n=3086 G=12 | -- | flag -0.0056 [-0.0082, -0.0029] n=1992 G=12 | reject | flag -0.0056 [-0.0082, -0.0030] n=1992 G=12 | -- | flag -0.0066 [-0.0097, -0.0035] n=1094 G=12 | flag -0.0059 [-0.0086, -0.0032] n=2965 G=12 | flag -0.0078 [-0.0107, -0.0050] n=93 G=12 | grey -0.0055 [-0.0088, -0.0022] n=28 G=9 | -- |
| derived | 35-50 | > 24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | > 24 h | ncaaf | spread | +0.0061 [-0.0008, +0.0129] n=47640 G=49 | +0.0313 [+0.0284, +0.0342] n=47640 G=49 | -- | +0.0072 [+0.0008, +0.0135] n=36374 G=49 | reject | +0.0073 [+0.0008, +0.0138] n=36374 G=49 | -- | +0.0021 [-0.0071, +0.0112] n=11266 G=48 | +0.0063 [-0.0006, +0.0131] n=46980 G=49 | -0.0011 [-0.0104, +0.0083] n=389 G=49 | -0.0226 [-0.0398, -0.0055] n=261 G=44 | grey +0.0062 [-0.0043, +0.0166] n=10 G=4 |
| derived | 35-50 | > 24 h | ncaaf | total | -0.0029 [-0.0065, +0.0006] n=27304 G=38 | +0.0123 [+0.0090, +0.0155] n=27304 G=38 | -- | -0.0031 [-0.0070, +0.0008] n=21196 G=37 | - | -0.0031 [-0.0071, +0.0008] n=21196 G=37 | -- | -0.0022 [-0.0057, +0.0013] n=6108 G=31 | -0.0029 [-0.0065, +0.0007] n=26976 G=38 | -0.0035 [-0.0083, +0.0014] n=179 G=32 | flag -0.0080 [-0.0156, -0.0005] n=141 G=23 | grey -0.0055 [-0.0545, +0.0435] n=8 G=2 |
| derived | 35-50 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | 3-24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | 3-24 h | ncaaf | spread | grey +0.0127 -- n=136 G=1 | grey +0.0148 -- n=136 G=1 | -- | -- | -- | -- | -- | grey +0.0127 -- n=136 G=1 | grey +0.0125 -- n=128 G=1 | grey +0.0133 -- n=4 G=1 | grey +0.0170 -- n=4 G=1 | -- |
| derived | 35-50 | 3-24 h | ncaaf | total | grey -0.0126 -- n=58 G=1 | grey -0.0116 -- n=58 G=1 | grey +0.0353 -- n=8 G=1 | -- | -- | -- | -- | grey -0.0126 -- n=58 G=1 | grey -0.0127 -- n=56 G=1 | -- | grey -0.0093 -- n=2 G=1 | -- |
| derived | 35-50 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | < 3 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 35-50 | < 3 h | ncaaf | spread | grey -0.0004 -- n=736 G=1 | grey +0.0033 -- n=736 G=1 | -- | -- | grey | grey -0.0026 -- n=636 G=1 | -- | grey +0.0138 -- n=100 G=1 | grey +0.0053 -- n=573 G=1 | grey -0.0212 -- n=13 G=1 | grey -0.0135 -- n=83 G=1 | grey -0.0288 -- n=67 G=1 |
| derived | 35-50 | < 3 h | ncaaf | total | grey +0.0059 -- n=248 G=1 | grey +0.0102 -- n=248 G=1 | -- | -- | grey | grey +0.0085 -- n=223 G=1 | -- | grey -0.0171 -- n=25 G=1 | grey +0.0216 -- n=159 G=1 | grey +0.0998 -- n=8 G=1 | grey -0.0224 -- n=44 G=1 | grey -0.0479 -- n=37 G=1 |
| derived | 50-65 | > 24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | > 24 h | nfl | spread | flag -0.0299 [-0.0414, -0.0185] n=2570 G=14 | flag -0.0283 [-0.0398, -0.0167] n=2570 G=14 | -- | flag -0.0290 [-0.0402, -0.0179] n=1608 G=14 | reject | flag -0.0296 [-0.0409, -0.0183] n=1608 G=14 | -- | flag -0.0305 [-0.0432, -0.0177] n=962 G=13 | flag -0.0299 [-0.0413, -0.0185] n=2466 G=14 | flag -0.0301 [-0.0419, -0.0183] n=79 G=13 | flag -0.0291 [-0.0431, -0.0150] n=25 G=10 | -- |
| derived | 50-65 | > 24 h | nfl | total | flag +0.0213 [+0.0187, +0.0238] n=3387 G=12 | flag +0.0286 [+0.0254, +0.0318] n=3387 G=12 | -- | flag +0.0208 [+0.0181, +0.0235] n=2176 G=12 | reject | flag +0.0208 [+0.0181, +0.0235] n=2176 G=12 | -- | flag +0.0220 [+0.0192, +0.0249] n=1211 G=12 | flag +0.0212 [+0.0186, +0.0238] n=3255 G=12 | flag +0.0211 [+0.0185, +0.0237] n=101 G=12 | grey +0.0242 [+0.0206, +0.0279] n=31 G=9 | -- |
| derived | 50-65 | > 24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | > 24 h | ncaaf | spread | -0.0118 [-0.0159, -0.0078] n=37649 G=47 | +0.0037 [-0.0015, +0.0088] n=37649 G=47 | -- | -0.0120 [-0.0161, -0.0079] n=28760 G=46 | reject | -0.0121 [-0.0162, -0.0080] n=28760 G=46 | -- | -0.0109 [-0.0150, -0.0068] n=8889 G=46 | -0.0118 [-0.0158, -0.0077] n=37120 G=47 | -0.0189 [-0.0262, -0.0117] n=312 G=46 | -0.0105 [-0.0156, -0.0053] n=202 G=41 | grey -0.0093 [-0.0231, +0.0044] n=15 G=4 |
| derived | 50-65 | > 24 h | ncaaf | total | +0.0083 [+0.0055, +0.0110] n=18581 G=38 | +0.0217 [+0.0168, +0.0266] n=18581 G=38 | -- | +0.0077 [+0.0054, +0.0101] n=14250 G=37 | reject | +0.0078 [+0.0054, +0.0102] n=14250 G=37 | -- | +0.0098 [+0.0042, +0.0155] n=4331 G=31 | +0.0082 [+0.0055, +0.0109] n=18363 G=38 | +0.0123 [+0.0050, +0.0196] n=124 G=32 | flag +0.0096 [-0.0064, +0.0256] n=89 G=23 | grey -0.0159 [-0.0201, -0.0118] n=5 G=2 |
| derived | 50-65 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | 3-24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | 3-24 h | ncaaf | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | 3-24 h | ncaaf | total | grey +0.0125 -- n=57 G=1 | grey +0.0135 -- n=57 G=1 | -- | -- | -- | -- | -- | grey +0.0125 -- n=57 G=1 | grey +0.0122 -- n=55 G=1 | -- | grey +0.0217 -- n=2 G=1 | -- |
| derived | 50-65 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | < 3 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 50-65 | < 3 h | ncaaf | spread | grey -0.0815 -- n=126 G=1 | grey -0.0770 -- n=126 G=1 | -- | -- | grey | grey -0.0815 -- n=126 G=1 | -- | -- | grey -0.0782 -- n=35 G=1 | grey -0.1133 -- n=5 G=1 | grey -0.0626 -- n=47 G=1 | grey -0.1031 -- n=39 G=1 |
| derived | 50-65 | < 3 h | ncaaf | total | grey +0.0012 -- n=414 G=1 | grey +0.0039 -- n=414 G=1 | -- | -- | grey | grey -0.0005 -- n=364 G=1 | -- | grey +0.0133 -- n=50 G=1 | grey +0.0222 -- n=313 G=1 | grey +0.1153 -- n=3 G=1 | grey -0.0547 -- n=46 G=1 | grey -0.0822 -- n=52 G=1 |
| derived | 65-80 | > 24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | > 24 h | nfl | spread | grey -0.0513 [-0.0695, -0.0331] n=741 G=4 | grey -0.0470 [-0.0635, -0.0305] n=741 G=4 | -- | grey -0.0505 [-0.0700, -0.0311] n=530 G=3 | grey | grey -0.0519 [-0.0716, -0.0321] n=530 G=3 | -- | grey -0.0500 [-0.0764, -0.0235] n=211 G=4 | grey -0.0514 [-0.0694, -0.0335] n=716 G=4 | grey -0.0491 [-0.0787, -0.0196] n=20 G=3 | grey -0.0469 [-0.1791, +0.0854] n=5 G=2 | -- |
| derived | 65-80 | > 24 h | nfl | total | flag +0.0539 [+0.0515, +0.0563] n=1991 G=12 | flag +0.0659 [+0.0624, +0.0693] n=1991 G=12 | -- | flag +0.0549 [+0.0522, +0.0575] n=1290 G=12 | reject | flag +0.0549 [+0.0523, +0.0575] n=1290 G=12 | -- | flag +0.0520 [+0.0493, +0.0548] n=701 G=12 | flag +0.0540 [+0.0516, +0.0564] n=1913 G=12 | flag +0.0516 [+0.0496, +0.0536] n=60 G=12 | grey +0.0541 [+0.0492, +0.0590] n=18 G=9 | -- |
| derived | 65-80 | > 24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | > 24 h | ncaaf | spread | -0.0261 [-0.0316, -0.0206] n=35516 G=39 | +0.0001 [-0.0058, +0.0061] n=35516 G=39 | -- | -0.0255 [-0.0311, -0.0199] n=27225 G=37 | reject | -0.0260 [-0.0316, -0.0203] n=27225 G=37 | -- | -0.0264 [-0.0319, -0.0209] n=8291 G=38 | -0.0261 [-0.0316, -0.0206] n=34990 G=39 | -0.0267 [-0.0324, -0.0211] n=300 G=38 | -0.0273 [-0.0335, -0.0211] n=208 G=34 | grey -0.0230 [-0.0444, -0.0016] n=18 G=4 |
| derived | 65-80 | > 24 h | ncaaf | total | +0.0249 [+0.0198, +0.0300] n=15624 G=38 | +0.0495 [+0.0411, +0.0579] n=15624 G=38 | -- | +0.0244 [+0.0189, +0.0298] n=12064 G=37 | reject | +0.0249 [+0.0194, +0.0304] n=12064 G=37 | -- | +0.0248 [+0.0198, +0.0297] n=3560 G=31 | +0.0249 [+0.0198, +0.0301] n=15439 G=38 | +0.0243 [+0.0196, +0.0290] n=99 G=31 | flag +0.0205 [+0.0145, +0.0265] n=80 G=23 | grey -0.0045 [-0.1273, +0.1183] n=6 G=2 |
| derived | 65-80 | 3-24 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | 3-24 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | 3-24 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | 3-24 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | 3-24 h | ncaaf | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | 3-24 h | ncaaf | total | grey +0.0414 -- n=88 G=1 | grey +0.0449 -- n=88 G=1 | -- | -- | -- | -- | -- | grey +0.0414 -- n=88 G=1 | grey +0.0414 -- n=86 G=1 | -- | grey +0.0403 -- n=2 G=1 | -- |
| derived | 65-80 | < 3 h | nfl | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | < 3 h | nfl | spread | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | < 3 h | nfl | total | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | < 3 h | ncaaf | moneyline | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |
| derived | 65-80 | < 3 h | ncaaf | spread | grey -0.1197 -- n=105 G=1 | grey -0.1142 -- n=105 G=1 | -- | -- | grey | grey -0.1197 -- n=105 G=1 | -- | -- | grey -0.1564 -- n=30 G=1 | grey -0.1125 -- n=6 G=1 | grey -0.1068 -- n=41 G=1 | grey -0.1008 -- n=28 G=1 |
| derived | 65-80 | < 3 h | ncaaf | total | grey +0.0195 -- n=442 G=1 | grey +0.0236 -- n=442 G=1 | -- | -- | grey | grey +0.0155 -- n=367 G=1 | -- | grey +0.0388 -- n=75 G=1 | grey +0.0371 -- n=364 G=1 | grey -0.0036 -- n=6 G=1 | grey -0.0624 -- n=43 G=1 | grey -0.0758 -- n=29 G=1 |

_family A (direct): 10 tested, 18 greyed and excluded; family B (derived): 15 tested, 9 greyed and excluded; cells whose posterior interval excludes zero: 18 (§9.6 needs 3)_

## Table 4b (t4b): derived minus direct by key number

Derived fair minus direct fair on shapes where the pricing tick produced both, bucketed by the rung's distance to the nearest key number (3, 7). Positive means the margin model prices the rung above the sharp line, so H4 is read net of the model's own error. Clustered by game.

| market_type | key_distance | derived_minus_direct |
|---|---|---|
| moneyline | <= 0.5 | -- |
| moneyline | 0.5-1.5 | -- |
| moneyline | 1.5-3 | -- |
| moneyline | > 3 | -- |
| moneyline | n/a | -- |
| spread | <= 0.5 | -- |
| spread | 0.5-1.5 | -- |
| spread | 1.5-3 | -- |
| spread | > 3 | -- |
| spread | n/a | -- |
| total | <= 0.5 | -- |
| total | 0.5-1.5 | -- |
| total | 1.5-3 | -- |
| total | > 3 | -- |
| total | n/a | -- |

## Table 5 (t5): convergence lag

Convergence lag on featured shapes: seconds from a sharp move of at least 2 pts (`fair_values.newest_book_ts`) to the first venue mid that has covered half of it. Reported as the interval [max(0, lag - sampling floor), lag] with the Kaplan-Meier median of each bound; the observation is censored at 60 min. The venue mid is sampled from the WebSocket order-book snapshots on the tape, so the sampling floor is the interval between snapshots.

| sport | market_type | moves | km_median_lag_s | lag_interval_lo_s | censored_share | negative_share | sampling_floor_s |
|---|---|---|---|---|---|---|---|
| nfl | moneyline | 2 | -- | -- | 1.0000 | 0.0000 | 602.0005 |
| nfl | spread | 14 | -- | -- | 1.0000 | 0.0000 | 3600.0000 |
| nfl | total | 0 | -- | -- | -- | -- | -- |
| ncaaf | moneyline | 14 | 166.8024 | 0.0000 | 0.1429 | 0.0000 | 166.8024 |
| ncaaf | spread | 157 | 2421.2908 | 727.8250 | 0.4968 | 0.0000 | 377.8218 |
| ncaaf | total | 247 | -- | 298.3445 | 0.5344 | 0.0000 | 654.9182 |

## Table 6 (t6): validity panel

Validity panel (F12): whether the paper fills are believable. Every line is one variant's own non-replay orders and fills; nothing is summed across variants. `has_print share (queue_model)` is asserted, not gated -- anything below 1.0 is a bug in the fill simulator, not a result.

| variant | metric | value | n_obs | n_clusters |
|---|---|---|---|---|
| constrained | queue_ahead_at_place p25 | 129.0000 | 7 | 5 |
| constrained | queue_ahead_at_place median | 1510.0000 | 7 | 5 |
| constrained | queue_ahead_at_place p75 | 1510.0000 | 7 | 5 |
| constrained | queue_ahead zero-or-null share | 0.1429 | 7 | 5 |
| constrained | fill prints at our price share | -- | 0 | 5 |
| constrained | fill prints through our price share | -- | 0 | 5 |
| constrained | book_source ws share | 1.0000 | 7 | 5 |
| constrained | book_source rest share | 0.0000 | 7 | 5 |
| constrained | book_source none share | 0.0000 | 7 | 5 |
| constrained | dirty order-minutes total | 1354 | 7 | 5 |
| constrained | queue-consumption ratio median | -- | 0 | 5 |
| constrained | fills queue_model | 0 | 0 | 5 |
| constrained | fills no_watcher | 0 | 0 | 5 |
| constrained | fills snapshot_cross | 0 | 0 | 5 |
| constrained | orders worst_case_fill | 0 | 7 | 5 |
| constrained | tape source ws share | -- | 0 | 5 |
| constrained | tape source rest share | -- | 0 | 5 |
| constrained | realised fee per contract | -- | 0 | 5 |
| constrained | has_print share (queue_model) | -- | 0 | 5 |
| nfl_only | queue_ahead_at_place p25 | -- | 0 | 0 |
| nfl_only | queue_ahead_at_place median | -- | 0 | 0 |
| nfl_only | queue_ahead_at_place p75 | -- | 0 | 0 |
| nfl_only | queue_ahead zero-or-null share | -- | 0 | 0 |
| nfl_only | fill prints at our price share | -- | 0 | 0 |
| nfl_only | fill prints through our price share | -- | 0 | 0 |
| nfl_only | book_source ws share | -- | 0 | 0 |
| nfl_only | book_source rest share | -- | 0 | 0 |
| nfl_only | book_source none share | -- | 0 | 0 |
| nfl_only | dirty order-minutes total | 0 | 0 | 0 |
| nfl_only | queue-consumption ratio median | -- | 0 | 0 |
| nfl_only | fills queue_model | 0 | 0 | 0 |
| nfl_only | fills no_watcher | 0 | 0 | 0 |
| nfl_only | fills snapshot_cross | 0 | 0 | 0 |
| nfl_only | orders worst_case_fill | 0 | 0 | 0 |
| nfl_only | tape source ws share | -- | 0 | 0 |
| nfl_only | tape source rest share | -- | 0 | 0 |
| nfl_only | realised fee per contract | -- | 0 | 0 |
| nfl_only | has_print share (queue_model) | -- | 0 | 0 |
| no_velocity | queue_ahead_at_place p25 | -- | 0 | 0 |
| no_velocity | queue_ahead_at_place median | -- | 0 | 0 |
| no_velocity | queue_ahead_at_place p75 | -- | 0 | 0 |
| no_velocity | queue_ahead zero-or-null share | -- | 0 | 0 |
| no_velocity | fill prints at our price share | -- | 0 | 0 |
| no_velocity | fill prints through our price share | -- | 0 | 0 |
| no_velocity | book_source ws share | -- | 0 | 0 |
| no_velocity | book_source rest share | -- | 0 | 0 |
| no_velocity | book_source none share | -- | 0 | 0 |
| no_velocity | dirty order-minutes total | 0 | 0 | 0 |
| no_velocity | queue-consumption ratio median | -- | 0 | 0 |
| no_velocity | fills queue_model | 0 | 0 | 0 |
| no_velocity | fills no_watcher | 0 | 0 | 0 |
| no_velocity | fills snapshot_cross | 0 | 0 | 0 |
| no_velocity | orders worst_case_fill | 0 | 0 | 0 |
| no_velocity | tape source ws share | -- | 0 | 0 |
| no_velocity | tape source rest share | -- | 0 | 0 |
| no_velocity | realised fee per contract | -- | 0 | 0 |
| no_velocity | has_print share (queue_model) | -- | 0 | 0 |
| sharp_direct | queue_ahead_at_place p25 | 530.0000 | 384 | 48 |
| sharp_direct | queue_ahead_at_place median | 1732.0000 | 384 | 48 |
| sharp_direct | queue_ahead_at_place p75 | 6808.5100 | 384 | 48 |
| sharp_direct | queue_ahead zero-or-null share | 0.0339 | 384 | 48 |
| sharp_direct | fill prints at our price share | 1.0000 | 2 | 48 |
| sharp_direct | fill prints through our price share | 0.0000 | 2 | 48 |
| sharp_direct | book_source ws share | 1.0000 | 384 | 48 |
| sharp_direct | book_source rest share | 0.0000 | 384 | 48 |
| sharp_direct | book_source none share | 0.0000 | 384 | 48 |
| sharp_direct | dirty order-minutes total | 39582 | 384 | 48 |
| sharp_direct | queue-consumption ratio median | 0.0100 | 1 | 48 |
| sharp_direct | fills queue_model | 2 | 6 | 48 |
| sharp_direct | fills no_watcher | 4 | 6 | 48 |
| sharp_direct | fills snapshot_cross | 0 | 6 | 48 |
| sharp_direct | orders worst_case_fill | 0 | 384 | 48 |
| sharp_direct | tape source ws share | 1.0000 | 2 | 48 |
| sharp_direct | tape source rest share | 0.0000 | 2 | 48 |
| sharp_direct | realised fee per contract | 0.0043 | 2 | 48 |
| sharp_direct | has_print share (queue_model) | 1.0000 | 2 | 48 |
| sharp_plus_derived | queue_ahead_at_place p25 | -- | 0 | 0 |
| sharp_plus_derived | queue_ahead_at_place median | -- | 0 | 0 |
| sharp_plus_derived | queue_ahead_at_place p75 | -- | 0 | 0 |
| sharp_plus_derived | queue_ahead zero-or-null share | -- | 0 | 0 |
| sharp_plus_derived | fill prints at our price share | -- | 0 | 0 |
| sharp_plus_derived | fill prints through our price share | -- | 0 | 0 |
| sharp_plus_derived | book_source ws share | -- | 0 | 0 |
| sharp_plus_derived | book_source rest share | -- | 0 | 0 |
| sharp_plus_derived | book_source none share | -- | 0 | 0 |
| sharp_plus_derived | dirty order-minutes total | 0 | 0 | 0 |
| sharp_plus_derived | queue-consumption ratio median | -- | 0 | 0 |
| sharp_plus_derived | fills queue_model | 0 | 0 | 0 |
| sharp_plus_derived | fills no_watcher | 0 | 0 | 0 |
| sharp_plus_derived | fills snapshot_cross | 0 | 0 | 0 |
| sharp_plus_derived | orders worst_case_fill | 0 | 0 | 0 |
| sharp_plus_derived | tape source ws share | -- | 0 | 0 |
| sharp_plus_derived | tape source rest share | -- | 0 | 0 |
| sharp_plus_derived | realised fee per contract | -- | 0 | 0 |
| sharp_plus_derived | has_print share (queue_model) | -- | 0 | 0 |
| sharp_two_sided | queue_ahead_at_place p25 | 773.0000 | 118 | 36 |
| sharp_two_sided | queue_ahead_at_place median | 2754.0000 | 118 | 36 |
| sharp_two_sided | queue_ahead_at_place p75 | 7999.0000 | 118 | 36 |
| sharp_two_sided | queue_ahead zero-or-null share | 0.0085 | 118 | 36 |
| sharp_two_sided | fill prints at our price share | -- | 0 | 36 |
| sharp_two_sided | fill prints through our price share | -- | 0 | 36 |
| sharp_two_sided | book_source ws share | 1.0000 | 118 | 36 |
| sharp_two_sided | book_source rest share | 0.0000 | 118 | 36 |
| sharp_two_sided | book_source none share | 0.0000 | 118 | 36 |
| sharp_two_sided | dirty order-minutes total | 11586 | 118 | 36 |
| sharp_two_sided | queue-consumption ratio median | -- | 0 | 36 |
| sharp_two_sided | fills queue_model | 0 | 0 | 36 |
| sharp_two_sided | fills no_watcher | 0 | 0 | 36 |
| sharp_two_sided | fills snapshot_cross | 0 | 0 | 36 |
| sharp_two_sided | orders worst_case_fill | 0 | 118 | 36 |
| sharp_two_sided | tape source ws share | -- | 0 | 36 |
| sharp_two_sided | tape source rest share | -- | 0 | 36 |
| sharp_two_sided | realised fee per contract | -- | 0 | 36 |
| sharp_two_sided | has_print share (queue_model) | -- | 0 | 36 |
| sharp_two_sided#e82fcd0a1e99 | queue_ahead_at_place p25 | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | queue_ahead_at_place median | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | queue_ahead_at_place p75 | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | queue_ahead zero-or-null share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | fill prints at our price share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | fill prints through our price share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | book_source ws share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | book_source rest share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | book_source none share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | dirty order-minutes total | 0 | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | queue-consumption ratio median | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | fills queue_model | 0 | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | fills no_watcher | 0 | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | fills snapshot_cross | 0 | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | orders worst_case_fill | 0 | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | tape source ws share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | tape source rest share | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | realised fee per contract | -- | 0 | 0 |
| sharp_two_sided#e82fcd0a1e99 | has_print share (queue_model) | -- | 0 | 0 |
| wide_band | queue_ahead_at_place p25 | -- | 0 | 0 |
| wide_band | queue_ahead_at_place median | -- | 0 | 0 |
| wide_band | queue_ahead_at_place p75 | -- | 0 | 0 |
| wide_band | queue_ahead zero-or-null share | -- | 0 | 0 |
| wide_band | fill prints at our price share | -- | 0 | 0 |
| wide_band | fill prints through our price share | -- | 0 | 0 |
| wide_band | book_source ws share | -- | 0 | 0 |
| wide_band | book_source rest share | -- | 0 | 0 |
| wide_band | book_source none share | -- | 0 | 0 |
| wide_band | dirty order-minutes total | 0 | 0 | 0 |
| wide_band | queue-consumption ratio median | -- | 0 | 0 |
| wide_band | fills queue_model | 0 | 0 | 0 |
| wide_band | fills no_watcher | 0 | 0 | 0 |
| wide_band | fills snapshot_cross | 0 | 0 | 0 |
| wide_band | orders worst_case_fill | 0 | 0 | 0 |
| wide_band | tape source ws share | -- | 0 | 0 |
| wide_band | tape source rest share | -- | 0 | 0 |
| wide_band | realised fee per contract | -- | 0 | 0 |
| wide_band | has_print share (queue_model) | -- | 0 | 0 |
| all markets with an order | queue accuracy median |REST - WS| / WS | -- | 0 | -- |
| all markets with an order | queue accuracy share within 10 % | -- | 0 | -- |

## Table 7 (t7): veto

The shadow veto arrives in phase 5. Phase 3 records nothing this table could read.

| item | status |
|---|---|
| veto | not collected in phase 3 (addendum §0.4) |

_not collected in phase 3 (addendum §0.4)_

## Table 8 (t8): data quality

Data quality over the week. The stale share per benchmark type is the fraction whose source was already more than 10 min old at the target instant. Those rows are excluded from every gate criterion and from table 2's executed-variant path, which reads `order_clv.stale`. `gap_outcomes` carries no `stale` column, so table 2's non-executed path and table 4's `clv_mid_p` panel cannot filter on it: read those two beside this share, not net of it.

| metric | value | n |
|---|---|---|
| fair_values feed_lag_s p50 | 29.0000 | 575843 |
| fair_values feed_lag_s p95 | 687.0000 | 575843 |
| fair_values staleness_s p50 | 50.0000 | 733206 |
| fair_values staleness_s p95 | 715.0000 | 733206 |
| orderbook gap events | 2.0000 | 2 |
| unmatched venue markets | 308.0000 | 4899 |
| odds credits spent | 1568.0000 | 3913 |
| missing taker side share | 0.0683 | 366754 |
| matched markets not linear_cent | 50.0000 | 4591 |
| markets with a non-default fee model | 4899.0000 | 4899 |
| markets with a non-zero exchange_index | 0.0000 | 4899 |
| post_only_reject rate | 0.0000 | 4045 |
| stale share: pinnacle_t5 | 0.0000 | 11 |
| stale share: consensus_t5 | 0.0833 | 12 |
| stale share: consensus_t60 | 0.0833 | 12 |
| stale share: consensus_t180 | 0.0833 | 12 |
| stale share: opening_first_seen | 0.0000 | 15 |
| stale share: kalshi_mid_t5 | 0.0000 | 50 |
| stale share: kalshi_last_trade_pre_kick | 0.0811 | 148 |
| stale share: novig_devig_t5 | 0.0000 | 4 |
| stale share: result | 0.0000 | 150 |

## Table 9 (t9): flow

H3's flow imbalance is a later phase. Phase 3 records nothing this table could read.

| item | status |
|---|---|
| flow | not collected in phase 3 (addendum §0.4) |

_not collected in phase 3 (addendum §0.4)_

## Table 10 (t10): RFQ

The combo RFQ listener is a later phase. Phase 3 records nothing this table could read.

| item | status |
|---|---|
| RFQ | not collected in phase 3 (addendum §0.4) |

_not collected in phase 3 (addendum §0.4)_
