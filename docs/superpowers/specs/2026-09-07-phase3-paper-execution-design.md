# Phase 3: Paper Execution, Settlement, Benchmarks, CLV — Design Addendum

Date: 2026-09-07. Extends the v2 spec (`2026-09-06-sportsbook-harness-design.md`, §5.5, §6.5, §7.2, §9.1–9.6, §10–§13, §15 item 3). Where this addendum and the v2 spec differ, this addendum wins for phase 3; every difference is listed in §0.

## 0. Amendments to the v2 spec

1. **Staleness (§6.2).** `staleness_s` stays "pricing time − newest sharp `last_update`". A fair value additionally records `feed_kind ∈ {featured, alternate}` (the Odds API endpoint that produced the newest group line), `feed_lag_s` (pricing time − that line's `fetched_at`), and `stale_allowance_s` derived from the feed's own cadence plus the tick budget: 220 s for featured lines and for alternates inside 3 h of kickoff, 1000 s for alternates further out. The `not_stale` label is `staleness_s ≤ max(variant.stale_s, stale_allowance_s)`. Variant configs and ids are unchanged. Reason: a Kalshi rung's exact sharp line comes from the alternates feed (15 min cadence); measured against 180 s every direct fair value was stale and the primary produced zero candidates in Week 1.
2. **Per-day caps (§6.5).** Exposure state is owned by the executor, rebuilt each loop from open paper orders and unsettled positions; the daily cap counts fills since 00:00 America/Chicago. Replay with `--execute` uses the same rebuild, so live and replay agree. The tick's `run_strategy` keeps labelling caps against a fresh state (it never places), and the `constrained` variant's sizing realism comes from the executor.
3. **Expiry (§9.2).** Paper orders carry `expiry = min(kickoff − 10 min, now + 45 s)` renewed each executor loop (three loops of 15 s), the same shape the live Kalshi adapter will use.
4. **Weekly report (§7.2).** Phase 3 produces tables 1–6 and 8; tables 7, 9, 10 print "not collected" until their phases ship. No Claude call in phase 3.
5. **Benchmarks (§5.5).** `novig_mid_t5` is computed from the Odds API `novig` book already in the feed.
6. **Retention (new).** Raw WebSocket deltas and prints are kept in full within a 1 TB database budget; no partitioning of `orderbook_events` in phase 3; the dashboard shows database size against the budget (red at 800 GB).

## 1. Executor process (`harness exec`, Compose service `app-exec`)

- One loop every 15 s (`exec_period_s`), single process, sync, same image, no secrets mounted, no network client. It is the only writer of `intents`, `orders`, `order_events`, `fills`, `exec_heartbeat`.
- **Intake.** Reads `signals` with `decision = candidate`, `replay = false`, variant in `exec_variants` (default `sharp_direct`, `constrained`), and no intent yet. One `intent(id uuid, signal_id, variant_id, venue, venue_market_id, side, target_prob, target_contracts, created_at)` per signal.
- **Reconcile per (variant, venue_market, side).** Newest intent is the target. No open order → place. Open order at another price → cancel (reason `reprice`) and place. Same price → hold. Newest signal for the market is `rejected` → cancel (reason `signal_rejected`).
- **Place** records: `fair_p_at_place`, `fair_books_json` (the BookFair list), `venue_bid_at_place`, `venue_ask_at_place`, `queue_ahead_at_place` (resting contracts at our price on our side from the live book), `edge_at_place`, `config_hash`, `expiry`, `feed_kind`.
- **Watcher, every loop, per open order.** Cancel if: kill switch active (`kill_switch`); kickoff < 10 min (`kickoff`); venue mid moved ≥ 2 pts against the order since placement, from the live book (`venue_move`); latest fair for the market puts edge < edge_min/2 (`edge_decay`); the market is no longer matched (`unmatched`). Reprice (cancel + place) if the latest fair moved ≥ 1 pt and the signal is still a candidate. Renew `expiry`. Orders past `expiry` when the loop runs (dead executor) transition to `expired`.
- **Live book.** `BookState(ticker)` rebuilt from the latest `orderbook_snapshots` row plus `orderbook_events` after it; kept in memory per open order, refreshed each loop from new events only. A `seq` gap marks the state `dirty` and rebuilds from the next snapshot before any fill or cancel decision (no decision on a dirty book).
- **Exposure.** `StrategyState` rebuilt per variant each loop: open orders' stakes, unsettled positions' stakes, fills since 00:00 local; passed to the executor's cap check before placement (caps are labels in `signals`, gates in the executor for `constrained`, labels only for `sharp_direct` — recorded either way as `cap_gate` on the order event).
- **Heartbeat.** `exec_heartbeat(id=1, last_loop_at, loops, open_orders, last_error)` updated every loop; dashboard red when older than 60 s.
- **Kill switch.** Active → no placements, cancel-all with reason `kill_switch`, heartbeat still updated.

## 2. Fill simulation (`harness/execution/fills.py`, pure)

`simulate_fills(order, book_at_place, events, prints, now) -> FillResult(fills_queue, fill_cross, queue_remaining, worst_case)`.

- **Queue model.** `queue_ahead` starts at `queue_ahead_at_place`. Walk events and prints in time order after placement. A print at our price on the opposite taker side (or through it) consumes `queue_ahead` first, then fills us up to remaining contracts; a book delta that reduces resting size at our price reduces `queue_ahead` by that amount (floor 0). Each fill: `prob`, `contracts`, `fee` (maker fee, ceil to cent per fill), `filled_at`, `simulated = true`, `fill_method = queue_model`, `source_trade_id`, `taker_side`.
- **Snapshot cross.** The first moment the opposite best price crosses ours (print or delta) fills the full remaining size at our price, `fill_method = snapshot_cross`. Stored, never used for positions, P&L, CLV, or the gate.
- **Worst case.** If the sharp fair crossed our price between the two fair values bracketing a cancel, the order is marked `worst_case_fill = true` and treated as filled at our price just before the cancel; reported separately in the adverse-selection table.
- **Audit.** `fill_confirmed = true` when a print at or through our price on the right side exists within [placed_at, filled_at + 60 s]; the weekly fill-realism table reports the confirmed share. Gate input.

## 3. Settlement, benchmarks, markouts, CLV (`harness/settlement/`)

- **Settlement job.** Runs in the tick once per hour and via `harness settle`. From stored ESPN scoreboard bodies: status `STATUS_FINAL` → `settlements(game_id, home_score, away_score, source = espn, settled_at)`. Venue markets resolve from score and threshold (moneyline: winner; spread for team T at k.5: margin > k.5; total over k.5: sum > k.5; ties are impossible at .5). `venue_settlements(venue, ticker, result ∈ {yes, no}, settled_at, source = derived)`; when the Kalshi public market row shows a `result`, it is stored as `source = venue` and any disagreement is a `settlement_mismatch` warning on the run and a dashboard alert. Paper positions settle to 0 or 1 per contract; `ledger` rows per fill and per settlement (paper, FIFO basis).
- **Benchmarks.** At kickoff + 5 min for every game with a matched venue market, per (market_type, outcome, threshold): `pinnacle_t5`, `consensus_t5`, `consensus_t60`, `consensus_t180` (sharp consensus at kickoff − 5/60/180 min from the last snapshot at or before that time), `opening_first_seen` (first consensus ever recorded), `kalshi_mid_t5`, `kalshi_last_trade_pre_kick`, `novig_mid_t5`, and `result` after settlement. Each row carries `source_ts` and `stale = source_ts < target − 10 min`. Benchmarks for a game are computed exactly once (unique key) and never updated except `result`.
- **CLV on snapshots.** `gap_outcomes(gap_snapshot_id, benchmark_type, clv_mid_p, clv_target_p, clv_target_roi_net)` for every gap snapshot with a direct or derived fair and every benchmark; probabilities per spec §10 formulas; net-of-fee subtracts the maker fee per contract at the target. Orders and fills inherit CLV through `signals.gap_snapshot_id`.
- **Markouts.** For every order (filled or not): at +1, +5, +30, +120 min after placement and at close (kickoff − 5 min): `fair_p` from the last `fair_values` row at or before the horizon and `venue_mid` from the last `venue_quotes` row at or before it, or the WebSocket book mid when the quote is older than 60 s. `adverse_drift = fair at first fill − fair at place`.
- **Views.** `positions` (open contracts and basis per variant × market × side) and `clv` (per order: CLV vs each benchmark, net of fee) as SQL views created by `create_schema`.

## 4. Report, gate, dashboard, replay

- **`harness report --week N`** writes `docs/reports/2026-wNN.md`: tables 1 (funnel), 2 (CLV per variant vs each benchmark, gate status), 3 (adverse selection: filled vs unfilled, worst-case share), 4 (mispricing map: sport × market type × price bucket × TTK bucket, direct and derived separately, with BH at 10% and empirical-Bayes shrinkage; cells n < 30 marked), 5 (convergence lag: median minutes from a sharp move ≥ 2 pts to the venue mid covering ≥ 50%), 6 (fill realism), 8 (data quality incl. feed lag by feed kind and CLV by feed kind); 7, 9, 10 as "not collected". Every cell: point estimate, n, 90% cluster-robust CI by game.
- **`harness gate`** evaluates spec §9.5 criteria on the same tables and stores `gate_reports(id, evaluated_at, criteria_json, passed)`. Expected to fail all phase 3.
- **Dashboard.** Adds: executor heartbeat in Health; open paper orders and today's fills; paper P&L and equity per exec variant; candidates since the staleness fixes; database size vs budget. Same page, no JS, every query bounded.
- **Replay.** `harness replay --from-run A --to-run B --variant NAME [--file] --execute` runs the executor's pure step over recorded books, events, prints and fair values for the range, writing `orders`/`fills` tagged `replay = true` (unique keys include `replay`). Reproduces live paper decisions from stored rows only.

## 5. Tables added (all timestamps timestamptz)

`intents`, `orders` (spec §5.5 columns plus `feed_kind`, `worst_case_fill`, `replay`; partial unique on `(venue, ticker, side, variant_id)` where status in open states and replay = false), `order_events`, `fills`, `markouts`, `settlements`, `venue_settlements`, `benchmarks` (unique `(game_id, market_type, outcome_team_id, outcome_side, threshold, benchmark_type)`), `gap_outcomes` (PK `(gap_snapshot_id, benchmark_type)`), `ledger`, `exec_heartbeat`, `gate_reports`. Columns added: `fair_values.feed_kind, feed_lag_s, stale_allowance_s`; `market_gap_snapshots.feed_kind, feed_lag_s, stale_allowance_s`; `runs.notes` gains `settlement` and `housekeeping` keys. Views: `positions`, `clv`. All added via `create_all` plus idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `create_schema`.

## 6. Ops

- Compose: `app-exec` (`command: ["exec"]`, no secrets, `restart: unless-stopped`, depends on postgres healthy). `make deploy-nas` unchanged otherwise.
- Settings: `exec_period_s = 15`, `exec_variants = ["sharp_direct", "constrained"]`, `db_budget_gb = 1000`, `settle_period_s = 3600`.
- `harness housekeeping` (nightly from the tick at 09:00 UTC): table sizes into the run notes.

## 7. Testing

Pure, no-DB unit tests: executor step (place/hold/reprice/cancel decisions with explicit `now`), `simulate_fills` (queue consumption, cancels ahead, partial fills, cross fill, worst case), settlement resolution for all three market types, benchmark selection at each horizon incl. `stale`, markout horizon selection, CLV formulas, report statistics (CI, BH, shrinkage) on fixed vectors. DB tests: intent/order state machine and unique keys, idempotent settlement and benchmarks, views, executor heartbeat, kill switch gate, replay `--execute` idempotency. End-to-end: replay `--execute` over a recorded fixture day asserting order, fill, gap_outcome counts and zero orders on unmatched markets.

## 8. Out of scope (later phases)

Live Kalshi adapter and demo smoke (phase 4); shadow veto, RFQ listener, futures, parlay CLI, NWS, Novig adapter, Claude-written bullets (phase 5); orderbook compaction (only if the budget line is crossed); NO-side signals; key-number margin adjustment.
