# Adversarial review — quantitative market microstructure lens

**Date:** 2026-09-07
**Reviewer lens:** paper-execution and measurement design (phase 3) plus the quant decisions pre-loaded for phases 4–6.
**Question:** is the system design and its autonomous build loop set up to achieve the original spec's goals (§1, §9.5, §9.6, §9.7, §10)?
**Read:** v2 spec, first adversarial review, phase 3 design addendum, phase 3 plan, SDD pre-flight rulings, autopilot roadmap, phase 2 final review, and `harness/pricing/*`, `harness/strategy/*`, `harness/recorder/{tick,ws_sink,cadence}.py`, `harness/venues/kalshi/{public,ws}.py`, `harness/db/{models,schema}.py`, `harness/replay.py`, `harness/variants/*.yaml`.
**External checks:** Kalshi fee rounding and market lifecycle pages, fetched 2026-09-07 (URLs cited inline).

---

## 0. Verdict

The recording layer is sound and the measurement *intent* is right. The phase 3 design, as written, will not deliver the three things §9.6 says week 3 must produce. Four independent mechanisms each bias the H1 (adverse selection) answer, and three of them bias it in the **optimistic** direction — the direction that makes a losing strategy look passable at the gate:

| Mechanism | Direction of bias on measured maker markout |
|---|---|
| Watcher cancels on a 2-pt adverse venue move and on edge decay | **optimistic** — removes exactly the fills H1 is about |
| Queue model fills only from prints *at* our price; a sweep *through* our price is queue-limited | **optimistic** — misses the informed-flow fills |
| Prints and book deltas both drain `queue_ahead` (double count) | pessimistic on fill timing, but inflates the fill count with fictitious fills |
| Per-fill ceil-to-cent fee (wrong unit, see §1.4) | pessimistic — overstates fees by up to 2.3× on small partials |

Net: a fill set that is too small, drawn from the benign half of the distribution, priced with the wrong fee, and audited by a check that is true by construction. H2 (the mispricing map) is in better shape but has an ill-defined success criterion and an unstratified staleness confound.

Separately, one structural decision — **YES-side-only signals, deferred to phase 6** — removes the single best-evidenced edge in the literature the spec itself cites, and probably prevents the gate's fill count from ever being reached.

Nothing here contradicts a resolution from the 2026-09-06 panel. Items 1.1, 1.2, 1.5, 1.7 and 1.8 are new defects introduced by the phase 3 design; 1.4 is a factual correction to a spec claim that the first panel accepted; 1.3 and 1.6 are consequences of decisions the first panel made that the phase 3 design now makes concrete.

---

## 1. MUST-FIX before the autonomous loop starts

### 1.1 The queue model double-counts traded volume, ignores sweeps, and lets late arrivals jump the queue

**Where:** `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` Task 4 "Interfaces (produce)" rules; `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md` §2 "Queue model".

The rule as written is:

> a print with `taker_side = "no"` and `yes_price <= prob` … consumes `queue_ahead` first, then fills `min(count − consumed, remaining)`; a delta on side `yes` at `price == prob` with negative `delta` reduces `queue_ahead` by `|delta|` (floor 0)

Three defects, all in these two clauses.

**(a) Double count.** On Kalshi a trade *is* a book event: the resting size that gets hit disappears from the ladder and the `orderbook_delta` channel reports that reduction. So the same 100 contracts arrive twice — once as a print, once as a negative delta at our price — and drain `queue_ahead` twice. The queue empties at roughly double the true rate and we are filled roughly twice as often, and earlier in each order's life, than a real order at the back of the queue would be. The plan's comment ("deltas caused by our own simulated fill do not exist in the tape, so no double counting") addresses a different double count and does not cover this one.

**(b) A sweep through our price is queue-limited when it should fill us completely.** If our bid rests at 0.55 and a print occurs at 0.53 on the NO taker side, every bid at 0.55 and 0.54 — ours included — was consumed on the way down. The rule instead applies `queue_ahead` (the size at 0.55) before filling us, so a large adverse sweep can leave us unfilled. This removes exactly the informed-flow fills H1 exists to measure, and it is the second of the three optimistic biases in §0.

**(c) Late arrivals join ahead of us.** The rule says nothing about *positive* deltas at our price. A naive implementation that mirrors the negative case will increase `queue_ahead` when someone joins the price level — but orders that arrive after ours sit behind us under price-time priority and can never delay our fill.

**Exact change.** Replace the Task 4 rules with:

```
For each event in (prints ∪ deltas) ordered by ts, prints after deltas at equal ts:

  print, taker_side == "no", yes_price <  prob:      # swept through us
      queue_ahead = 0
      fill min(count, remaining)                     # we were consumed on the way down
      credit count at this price to traded[price]

  print, taker_side == "no", yes_price == prob:      # traded at our level
      consumed = min(queue_ahead, count); queue_ahead -= consumed
      fill min(count - consumed, remaining)
      traded[prob] += count

  delta, side == "yes", price == prob, delta < 0:    # size left our level
      cancels = max(0, |delta| - traded[prob])       # attribute to trades first
      traded[prob] = max(0, traded[prob] - |delta|)
      queue_ahead = max(0, queue_ahead - cancels)    # only genuine cancels shorten the queue

  delta, side == "yes", price == prob, delta > 0:    # someone joined behind us
      no effect on queue_ahead
```

`traded[prob]` is a per-price accumulator, not a time window, so out-of-order delivery of the print and its delta cannot leak.

**Test to add in Task 4 Step 1, against recorded rows rather than synthetic ones:** for a sample of real `venue_trades` prints on a WS-subscribed ticker, assert that an `orderbook_events` delta with the same ticker and price and `|delta| >= count` exists within 1 s. That check both proves the double-count premise on this venue and guards the rule.

**Cost if ignored:** the fill count, the fill *set*, and every number computed from it (H1, adverse drift, filled-minus-unfilled CLV, all four numeric gate criteria) are wrong by an unknown factor, in mixed directions. Not recoverable by re-analysis, because the tape cursor state is destroyed as it goes (see 1.2).

---

### 1.2 Queue state is not persisted between executor loops

**Where:** plan Task 2 `Order` column list; Task 6 step 3.

`FillResult` returns `queue_remaining`, and Task 6 step 3 persists `tape_cursor_event_id` and `tape_cursor_trade_ts` on the order — but `queue_remaining` is on neither the `Order` column list (Task 2) nor the persistence step. `simulate_fills` starts from `queue_ahead_at_place` every call. Because each 15 s loop feeds it only the *new* slice of tape, the queue is reset to its full placement value every 15 s and can essentially never be consumed. Orders will almost never fill.

If an implementer notices and patches it by re-reading the whole tape from `placed_at` each loop instead, the fills become O(order life × tape rate) rows per loop — at 4M events/hour on a busy ticker that is a self-inflicted denial of service on the executor.

**Exact change.** Add to Task 2's `Order` model: `queue_remaining int` (initialised to `queue_ahead_at_place`), `traded_at_price Numeric(14,2) default 0` (the accumulator from 1.1), `tape_cursor_event_id bigint`, `tape_cursor_trade_id str`, `tape_cursor_ts timestamptz`. Task 6 step 3 writes all five back in the same transaction as the fills.

Note the trade cursor must be `(ts, trade_id)`, not `ts` alone: `venue_trades` has PK `(venue, trade_id)` and no monotone id, and a sweep produces several prints sharing one millisecond. A `ts >` cursor drops prints; a `ts >=` cursor re-reads them and, with 1.1's accumulator, re-consumes the queue.

**Cost if ignored:** zero fills (visible), or an executor that reads the whole tape every loop (visible late, on the first NCAAF Saturday), or silent double consumption.

---

### 1.3 The watcher's cancel rules destroy the H1 measurement; simulate the un-watched counterfactual

**Where:** design addendum §1 "Watcher"; §2 "Fill simulation"; plan Task 4 and Task 6 step 3.

The watcher cancels an order when the venue mid moves ≥ 2 pts against it (`venue_move`) or when the fair implies edge < `edge_min`/2 (`edge_decay`). Both fire precisely when an informed taker is about to lift us. The surviving fill set is therefore conditioned on *no adverse move having occurred*, and the measured 30-minute markout is the markout of benign fills only.

H1 is "maker fills on football are adversely selected". Measuring it on a fill set from which adverse fills have been filtered answers a different question. The gate criterion "mean 30-minute markout net of maker fee > 0 with t > 2" is then satisfiable by a strategy whose unconditional maker markout is negative.

This is not an argument against the watcher — a live maker should cancel. It is an argument that in paper mode we can have both for free.

**Exact change.** In `harness/execution/fills.py` (Task 4) add a second simulation track per order, run to the order's *natural* deadline (`min(kickoff − 10 min, expiry ladder)`) regardless of when the watcher cancelled it:

- `Fill.fill_method` gains `'no_watcher'`.
- `no_watcher` fills never touch `positions`, `ledger`, P&L or exposure — exactly the treatment `snapshot_cross` already gets.
- Report table 3 (adverse selection) reports the 1/5/30/120-minute markout under three fill sets: `queue_model` (watched), `queue_model ∪ no_watcher` (unwatched), and `queue_model ∪ snapshot_cross` (pessimistic execution).
- Gate criterion "mean 30-minute markout net of maker fee > 0 with t > 2" must hold on the **unwatched** set. The watched set is reported, not gated.

Cost: one extra pass over the same tape slice the executor already loaded, and one enum value.

**Cost if ignored:** H1 is answered with a number that is optimistic by construction and cannot be corrected later, because the un-watched fills were never simulated and the tape cursor has moved on. This is the single most consequential item in this review.

---

### 1.4 The fee rounding unit is wrong: Kalshi ceils to the centicent, with a per-order accumulator

**Where:** `harness/pricing/fees.py:8` (`ceil_to_cent`); spec §6.0 ("Fees are dollars per order, `ceil_to_cent(...)`"), §5.2 ("ceiling to the cent per order"); plan Task 4 ("Fee per fill: `ceil_to_cent(fee_for_order(...))`"), Global Constraints ("ceil to cent per fill").

Kalshi's current documentation (https://docs.kalshi.com/getting_started/fee_rounding, fetched 2026-09-07) states:

> Each fill generates three fee components: a trade fee (rounded up to the nearest $0.0001), a rounding fee … and a rebate … The fee accumulator tracks cumulative rounding overpayments across all fills of an order. When the accumulated rounding exceeds $0.01, a whole-cent rebate is issued … This mechanism ensures that the total fee for many small fills approximates the fee for a single equivalent fill.

Two corrections follow:

1. The trade fee ceils to **$0.0001**, not $0.01.
2. Fee is charged per fill, but the per-order accumulator makes many small fills cost approximately what one equivalent fill costs.

Worked at p = 0.55, maker rate 0.0175:

| Basis | 1 contract | 12 contracts | 100 contracts |
|---|---|---|---|
| Correct (ceil to $0.0001) | $0.0044/ct | $0.004333/ct | $0.004332/ct |
| Plan as written (ceil to $0.01 per fill) | $0.0100/ct | $0.005000/ct | $0.004400/ct |

A 1-contract partial fill is charged **1.00 probability point** instead of 0.44 — half the entire 2-point edge floor — and a 2-contract partial is charged 0.50 points instead of 0.44.

Three consequences worth noting:

- **The 100-contract fee basis problem disappears.** With centicent rounding the per-contract fee is `rate·p·(1−p)` to within $0.0001/contracts, so the 100-contract reference in `harness/strategy/run.py:186-188` is accurate at every size. Roadmap **phase 6 item 3 can be closed** rather than scheduled.
- The phase 2 final review (§7, "Fee basis") states the error as "up to ~0.6 probability points". Under cent rounding it is 0.0006 in probability = **0.06 points**, ten times smaller than stated; under the correct centicent rounding it is ~0.
- Spec §3's premise row "Maker fee is up to 44% of a 1-point edge, so the edge floor is 2 points" is unaffected and remains correct.

**Exact change.**
- `harness/pricing/fees.py`: add `CENTICENT = Decimal("0.0001")` and `ceil_to_centicent()`; `fee_for_order` ceils to centicent. Keep `ceil_to_cent` only if something else needs it.
- Spec §6.0 and §5.2: replace "ceiling to the cent per order" with "ceiling to the centicent ($0.0001) per fill, with Kalshi's per-order rounding accumulator and whole-cent rebate, so many small fills cost approximately one equivalent fill (docs.kalshi.com/getting_started/fee_rounding, verified 2026-09-07)".
- Plan Task 4 and Global Constraints: per-fill fee is `ceil_to_centicent(rate · contracts · p · (1−p))`.
- Plan Task 1 or 2 unit test: `assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.55"), n) == Decimal("0.0043")` for n in (12, 100) and `== Decimal("0.0044")` for n = 1.

**Cost if ignored:** every net-of-fee number — CLV net, markout net, `edge`, `gap_maker_net`, gate criteria 3 and 4 — is biased pessimistic by up to 0.56 points per contract on small partials, which is 28% of the minimum edge. It would reject a viable strategy.

---

### 1.5 `fill_confirmed` is true by construction; the fill-realism gate criterion measures nothing

**Where:** design addendum §2 "Audit"; plan Task 4 `confirmed()`; plan Task 11 gate criterion "fills confirmed ≥ 150".

> `fill_confirmed = true` when a print at or through our price on the right side exists within [placed_at, filled_at + 60 s]

Every `queue_model` fill is *generated by* such a print. The predicate is therefore identically true on the whole fill set it is applied to, the "share of paper fills confirmed by a print" in weekly table 6 is 100.0% by definition, and spec §9.5's "≥ 150 paper fills confirmed by prints" degenerates to "≥ 150 paper fills".

This mattered less when the panel wrote it, because the fill model might have been snapshot-based. It is now circular.

The obvious repair — cross-check the WS print against the REST print — is **not available**: `venue_trades` has PK `(venue, trade_id)` and the sink uses `on_conflict_do_nothing`, so the WS and REST copies of one trade collapse into a single row whose `source` records only whichever arrived first.

**Exact change.** Replace the single boolean with three realism measures in design §2 and weekly table 6:

1. `queue_ahead_at_place` distribution, and the share of fills where it was 0 (we were alone at the price — the model's strongest case) versus > 0.
2. Fill count under each of the four models: `queue_model`, `queue_model ∪ no_watcher` (1.3), `queue_model ∪ snapshot_cross`, `worst_case`. Their spread is the honest uncertainty band on "how many fills would we really have got".
3. **Tape coverage**, which nothing currently records: per order, `tape_source ∈ {ws, rest, none}` and `book_source ∈ {ws_snapshot, rest_snapshot}` and `book_age_s` at placement. See 1.7 for why this is not hypothetical.

Rename the existing predicate `has_print` and keep it as a sanity assertion (it must be 100%), not as a gate input. Replace the gate criterion with "≥ 150 `queue_model` fills across ≥ 40 games and both sports, of which ≥ 80% on WS-covered tickers".

**Cost if ignored:** the gate's fill-realism criterion passes automatically, and the one number that would tell the user whether the fill model is credible is never produced.

---

### 1.6 YES-only signals exclude the maker edge the spec's own evidence points at

**Where:** `harness/strategy/run.py:322` (`side="yes"` hard-coded), every `harness/variants/*.yaml`; roadmap "Phase 6 — deferred items" item 1; design addendum §8 "Out of scope".

Spec §3 records the premise, from Bürgi, Deng and Whelan 2026, that *makers on Kalshi are net positive only because retail overbuys YES on favorites*. Retail overbuying YES pushes the YES price **up**; the maker who profits from that flow is the one **selling** YES, i.e. bidding NO.

Our rule posts a YES bid only when the venue's YES bid is already at or below `fair − edge_min − AS − fee`. By construction we quote only where YES is too *cheap*, and never where YES is too *dear*. The YES-only board is not half the sample — under the cited premise it is the half with the adverse sign.

Two further consequences:

- **Fill count.** Observed rate is ~42 primary candidates per 6 h. Game-day active windows are roughly 30 h/week (Thu, Sat, Sun, Mon), so ~210 candidates/week, ~630 over the three-week window. A maker resting 3.4 points below fair, cancelled on any 2-point adverse move, will convert a small fraction of those. Even at a generous 20% the fill count lands near 120, below the gate's 150, and the "≥ 40 games, both sports, ≥ 30% NFL or marquee NCAAF" sub-conditions are tighter still. NO-side roughly doubles the candidate pool at zero new matching work.
- **Cost to add is small.** `venue_quotes` already stores `no_bid`/`no_ask` (`harness/normalize/kalshi.py:104`); the Kalshi fee is symmetric because `q(1−q) = p(1−p)` for `q = 1−p`; `fair_no = 1 − fair_p`. The change is a `side` loop in `_filters`/`_price_and_size`, a `side` dimension on the executor's `(venue, ticker, side, variant_id)` key which already exists, and a dated pre-registration amendment.

**Exact change.** Move roadmap phase 6 item 1 into phase 3 as a new Task 4b, landing **before NFL Week 2 (2026-09-16)**, with a pre-registration amendment 3 registering the primary as two-sided. Update design addendum §8 to remove "NO-side signals" from out-of-scope and §0 to list the amendment.

**Cost if ignored:** the three-week dataset, the week-3 success criteria and the gate are all evaluated on a board half that the spec's own literature says is the losing half; the fill count probably misses the gate; and by the time phase 6 runs (after phases 4 and 5, so late October) the season's high-information weeks are gone.

---

### 1.7 `orderbook_events` is unpartitioned and mis-indexed for every query phase 3 makes

**Where:** `harness/db/models.py:174-188` (`OrderbookEvent`), `harness/db/schema.py:46-50`; design addendum §0.6 ("no partitioning of `orderbook_events` in phase 3"); plan Task 3 `load_book`, Task 6 step 3.

Live facts: ~4M rows/hour during games, 19M rows after 12 h. Existing indexes are `ix_obe_ticker_ts (ticker, ts)` and a BRIN on `ts`. Phase 3 adds three access patterns, none of which those indexes serve:

1. **"Newest snapshot for this ticker at or before `now`."** `kind` is not in any index, and a ticker only receives a fresh `orderbook_snapshot` when it is newly subscribed — `_resubscribe` (`harness/venues/kalshi/ws.py:146-160`) sends `add_markets` only for *changed* tickers, so an established ticker's last snapshot can be many hours old. Finding it means scanning backwards through every delta for that ticker in between.
2. **"Deltas for this ticker with `id` > cursor."** There is no `(ticker, id)` index. Postgres will range-scan `(ticker, ts)` for the whole ticker and filter.
3. **Markouts and replay call `load_book` at arbitrary past instants**, repeating both of the above.

Partitioning is the item that cannot wait. `orderbook_events` is 19M rows today and will be several hundred million by mid-season. Converting a populated unpartitioned table to a partitioned one requires a full rewrite; doing it now costs one `create_schema` change and reuses the weekly-partition machinery `ensure_partitions` already implements for `raw_responses` (`harness/db/schema.py:22-40`).

**Exact change.**
- `harness/db/models.py`: `OrderbookEvent.__table_args__` gains `{"postgresql_partition_by": "RANGE (ts)"}` with `ts` in the primary key, mirroring `RawResponse`. Extend `ensure_partitions` to create weekly partitions for it. Do this in Task 2, and have the controller run the migration on the NAS before Week 1 kickoff on 2026-09-09.
- `harness/db/schema.py`: add
  ```sql
  create index if not exists ix_obe_snapshot on orderbook_events (ticker, ts desc) where kind = 'snapshot';
  create index if not exists ix_obe_ticker_id on orderbook_events (ticker, id);
  ```
- Plan Task 3 `load_book`: anchor on **whichever of the newest WS snapshot and the newest REST `orderbook_snapshots` row is fresher**, not "REST only when no WS snapshot exists"; record `book_source` and `book_age_s` on the order (1.5).
- Plan Task 6 step 3: bound the delta query by both cursors — `ticker = ? and ts >= cursor_ts and id > cursor_id` — so the index can range-scan.

**Also record the budget arithmetic in design §0.6.** At ~48M rows on a heavy game day and ~300 bytes/row with indexes, `orderbook_events` alone accrues roughly 14 GB per game day, ~60 GB/week, ~1 TB across a 17-week season. The stated 1 TB budget is therefore approximately exactly one season with no margin and no headroom for `raw_responses`, which stores every Kalshi markets page, trades page and ladder body verbatim. Phase 6 item 8 ("compaction only if the database passes 800 GB") is a plan to react at 800 GB with no pre-decided action. Add to Task 12's housekeeping: project days-to-budget from the trailing 7-day growth rate and surface it on the dashboard, so the loop gets weeks of warning rather than days.

**Cost if ignored:** an executor whose per-loop tape queries degrade from milliseconds to tens of seconds on the first NCAAF Saturday (the plan's own note already measures a cold 1-hour count at ~23 s), missed 15 s loops, and a table that cannot be pruned or archived without a multi-hour rewrite on a home NAS.

---

### 1.8 `replay --execute` cannot reproduce live paper decisions

**Where:** plan Task 13 "Interfaces" and Step 1; `harness/replay.py:98-101`; design addendum §4 "Replay".

Task 13 asserts:

> replay with `--execute` over the seeded pipeline run produces exactly the orders/fills the live executor produced for the same rows

Three independent reasons it cannot, as specified:

1. **Step frequency.** Live runs `Executor.step` every 15 s (`exec_period_s`). Replay drives one step per *recorded run*, i.e. every 120 s on game days and up to 900 s otherwise. With `expiry = min(kickoff − 10 min, now + 45 s)` renewed each loop, a live order is renewed eight times between two runs while its replay counterpart expires before the next step. Replay produces almost no orders.
2. **Signal timestamps.** `harness/replay.py:101` writes `created_at = resolved_now` (wall clock), not the run's time. The executor intake filters `created_at >= now − 1 h` against the injected historical `now`, so every replayed signal is filtered out as being in the future.
3. **Fill-window granularity.** Fills depend on the tape slice between consecutive steps. A 120 s slice and eight 15 s slices give different queue-consumption paths under any correct version of 1.1.

Spec §12 and §9.6's "strategy is a pure function of recorded data" both rest on this property, as does the whole "the dataset is the product" premise.

**Exact change.**
- Plan Task 13: replay drives `Executor.step` on a **15 s grid** over `[pricing_clock_for_run(from_run), pricing_clock_for_run(to_run)]`, injecting each grid instant as `now`, rather than once per run. Signals become visible at their own run's pricing clock.
- `harness/replay.py`: `_insert_signals(..., now=pricing_clock_for_run(run_row, settings.tick_budget_s))` instead of `resolved_now`, so a replayed signal carries the time it would have had.
- Task 13 Step 1 keeps the strict equality assertion; it is now achievable and is the only test that protects the property.

**Cost if ignored:** the replay equality test is quietly weakened to a count comparison, and the harness loses the ability to re-run any strategy variant over the season's tape — which is the stated product of the first three weeks.

---

## 2. FIX in phase 3

### 2.1 Gate criteria: one silent metric substitution, one criterion with no power, two ambiguous ones

**Where:** plan Task 11 `evaluate_gate`; spec §9.5; design addendum §0 (which claims "every difference is listed").

- **Substitution.** Spec §9.5 requires "median feed staleness < 90 s". Task 11 implements "median **feed lag** < 90 s". These are different quantities: `staleness_s` is pricing time − the book's own `last_update` (measured at 910–932 s for direct fairs on alternate-fed rungs); `feed_lag_s` is pricing time − our `fetched_at` (~0–95 s). The substitution turns a criterion the system currently fails by a factor of ten into one it passes trivially, and it is **not listed in §0**. Fix: list it as amendment 7, and keep both — `feed_lag_s` median < 90 s *and* a reported `staleness_s` distribution by `feed_kind`, with the gate report stating plainly that the primary trades on sharp lines up to ~1000 s old inside the alternates window.
- **No power.** `result` is a `benchmark_type` (spec §5.5), so "mean CLV ≥ 0 under **every** benchmark" makes realized win/loss at n≈150 a gate input — the exact thing spec §3 and §9.6 say is unmeasurable and must "never gate anything". Fix: exclude `benchmark_type = 'result'` from that criterion and report it separately with its CI.
- **Stale benchmarks.** `stale = source_ts < target − 10 min` will flag most `consensus_t180` rows, because alternates run at 900 s outside T−3 h. The gate says nothing about whether stale benchmark rows are included. Fix: exclude `stale = true` rows from every gate criterion; report their share.
- **Variant double counting.** `exec_variants = ["sharp_direct", "constrained"]` differ only in `apply_caps`, so they place near-identical orders on the same tickers and both get filled by the same prints. Fix: every gate criterion counts **primary-variant, non-replay** fills only, and no table pools fills across variants. State in design §2 that each variant's fills are simulated as if it were the only participant, so its liquidity is not shared.

### 2.2 A post-only order that would cross is rejected, not filled as a maker

**Where:** plan Task 5 `plan_actions`, the per-intent branch.

Spec §5.2 sets `post_only=true`. The executor's placement branch checks kill switch, kickoff cutoff, dirty book and caps, but never re-checks the book. Between the tick that produced the signal (up to ~60 s earlier) and placement, the market can move so that our target `prob >= best_yes_ask`. A real post-only order there is **rejected**; the simulation happily rests it and later books a maker fill and a maker fee where reality would have produced nothing (or a taker fill at 4× the fee).

**Exact change.** Add, before `Place`: `if market.best_yes_ask is not None and prob >= market.best_yes_ask: Skip(intent_id, "post_only_reject")`. Same check on the reprice path. `Skip` already writes an `order_events` row, so the rejection is recorded rather than lost, and its rate is a data-quality line in table 8.

### 2.3 A dead WebSocket recorder leaves a frozen book that never reads as dirty

**Where:** plan Task 3 `load_book` / `advance_book`; Task 5 `MarketNow.book_dirty`.

`dirty` is set only by a `seq` gap or a `gap` row. If the `app-ws` process dies, no new events arrive, no gap row is written, and `load_book` returns a perfectly consistent book frozen at the moment of death. The executor keeps placing orders, computing `queue_ahead_at_place` and evaluating the `venue_move` cancel against a book that stopped moving. `simulate_fills` sees an empty tape and reports zero fills. Nothing flags it.

**Exact change.** `MarketNow.book_dirty` is also True when the newest `orderbook_events` row for the ticker is older than `2 × 60 s`; add `ws_last_event_at` (global max) to `exec_heartbeat` and to the dashboard Health block in Task 12, red above 120 s. Same treatment for `load_book` at a past instant in markouts: return `source = 'none'` rather than a stale book mid.

### 2.4 Settled markets are never recorded, so the venue-settlement cross-check cannot run

**Where:** `harness/venues/kalshi/public.py:88` (`"status": "open"`); plan Task 7 `run_settlement`; spec §5.4.

`fetch_markets_all` always passes `status=open`. Kalshi's lifecycle documentation (https://docs.kalshi.com/getting_started/market_lifecycle, fetched 2026-09-07) states that `settled` refers to markets in the `finalized` state and `closed` covers markets past `close_time` that are not yet finalized — so a market drops out of our capture the moment it closes, and its `result` is never recorded anywhere. Task 7's `venue_settlements(source = 'venue')` branch ("if the newest Kalshi market summary for the ticker carries a `result`") can never fire, the ESPN-versus-venue disagreement check never runs, and the gate criterion "zero settlement mismatches" is satisfied by having zero comparisons.

**Exact change (cheapest first).** Kalshi publishes a `market_lifecycle_v2` WebSocket channel whose `settled` and `determined` events carry `result` (https://docs.kalshi.com/websockets/market-and-event-lifecycle). Add `"market_lifecycle_v2"` to `CHANNELS` in `harness/venues/kalshi/ws.py:20` and a handler branch in `harness/recorder/ws_sink.py` writing `venue_settlements(venue, ticker, result, source='venue', settled_at)`. The socket, the subscription and the sink already exist; this is roughly 15 lines and it captures settlement at the source, in real time, for every ticker we already follow. As a belt-and-braces fallback, add an hourly `fetch_markets_all(series, status="settled")` for the six football series.

### 2.5 Markouts are anchored at placement, not at fill, and carry no freshness flag

**Where:** design addendum §3 "Markouts"; plan Task 9.

- **Anchor.** Horizons are "+1, +5, +30, +120 min after placement". H1 and gate criterion 4 are about the value of a **fill**: `fair(t_fill + 30m) − fill_prob − fee`. With repricing and partial fills the two anchors diverge. Fix: `Markout` PK becomes `(order_id, anchor, horizon)` with `anchor ∈ {place, fill}`; filled orders get both, unfilled orders get `place` only. The gate reads the `fill` anchor.
- **Statistic.** The design stores levels (`fair_p`, `venue_mid`) and never writes the markout formula down; Task 10 and Task 11 then have to invent it. Fix: state it in design §3 — `markout_net(h) = fair_p(h) − p_used − fee_per_contract(maker, p_used)`, with `p_used = fill prob` for the `fill` anchor and `price_target` for the `place` anchor, sign positive = we profit.
- **Freshness.** `fair_values` for an alternate-fed rung updates every 900 s outside T−3 h and every 120 s inside it. A "+1 minute markout" against `fair_p` is then literally the same number as at placement — noise, or worse, an exact zero that shrinks the variance and inflates t-statistics. Fix: add `fair_age_s` and `mid_age_s` to `Markout`; report `fair_p` markouts only for horizons ≥ the feed's own cadence, and read the 1 m and 5 m markouts off `venue_mid` (WS book), which does have the resolution.

### 2.6 `worst_case_fill` is defined off the wrong series

**Where:** design addendum §2 "Worst case".

> If the sharp fair crossed our price between the two fair values bracketing a cancel

Whether a resting order fills is a fact about the **venue book**, not about the sharp fair, and the sharp fair is sampled at 120–900 s while the book is available at WS resolution. Fix: define `worst_case_fill = true` when `BookState.best_yes_ask() <= prob` at any instant during the order's life — which is exactly the `snapshot_cross` condition, so the two collapse into one well-defined pessimistic bound. Keep the sharp-fair version as a third, separately named flag if it is wanted for the report; do not use it as the primary pessimistic case.

Related, in plan Task 4: the cross-fill trigger is written as "a delta on side `no` at price `>= 1 − prob` with positive delta". Evaluate the condition on the maintained `BookState` after applying each event instead, otherwise a pre-existing crossing NO bid is missed.

### 2.7 The mispricing map's headline number is confounded with our own feed latency

**Where:** design addendum §4 table 4; plan Task 10 `weekly_tables`.

`gap_mid = fair_p − venue_mid` where `fair_p` is up to ~1000 s old on alternate-fed rungs. When the sharp line has moved and our fair has not, `gap_mid` measures **our staleness**, not the venue's error — and it does so with a sign that correlates with recent line movement, i.e. non-randomly. Table 4's cells (sport × market type × price bucket × TTK bucket) do not stratify on it. Table 8 reports "CLV by feed kind" but table 4, the one that answers H2 and carries the week-3 success criterion, does not.

**Exact change.** Add `feed_kind` and a `staleness_s` bucket (`<120`, `120–300`, `300–1000`, `>1000`) as strata in table 4, and make the headline H2 claim from `feed_kind = 'featured'` rows only (moneyline, main spread, main total at 120 s cadence). The columns exist on `market_gap_snapshots` after Task 1; this is a `GROUP BY` change.

### 2.8 The week-3 success criterion is not well defined

**Where:** spec §9.6; design addendum §4 table 4; plan Task 10 `bh_reject` / `eb_shrink` / `cluster_ci`.

Three separate ambiguities in one sentence ("at least three cells whose 90% CI excludes zero after shrinkage"):

1. **BH and shrinkage are applied to different objects.** Benjamini–Hochberg operates on p-values from unshrunk cell means; the reported estimates are shrunk. A shrunk point estimate paired with an unshrunk interval is not a valid interval, and "CI excludes zero after shrinkage" names neither. Fix: pick one and write it into design §4 — either (a) BH-adjusted one-sided intervals on **raw** cell means, shrinkage shown alongside as a robustness column, or (b) posterior intervals from the empirical-Bayes model with the BH step dropped. (a) is simpler and matches "pre-registered".
2. **`n` is ambiguous.** "cells with n < 30 marked" — gap snapshots are written every tick, so one game contributes ~90 near-identical rows per market per 3 hours. A cell with 30 *rows* can be one game. Fix: `n` is the number of **clusters (games)**; the display and BH thresholds use `n_games ≥ 30`; report `n_rows` beside it.
3. **`cluster_ci` uses a normal quantile.** With G ≈ 40 clusters a normal approximation understates the interval, and every numeric gate criterion is a one-sided bound. Fix: use `t` with `G − 1` degrees of freedom and the standard finite-cluster correction, and write the estimator explicitly in the plan so it cannot be mis-implemented:
   ```
   SE² = (G/(G−1)) · Σ_g ( Σ_{i∈g} (x_i − x̄) )² / N²
   bound = x̄ ± t_{0.95, G−1} · SE
   ```
   `statistics` has no inverse-t; a 40-line Newton solve on the regularised incomplete beta, or a vendored table for G ≤ 200, is enough. At G = 40 the difference is 2.4%; at G = 10 it is 11%, and small-G cells are exactly where a spurious "CI excludes zero" would come from.

### 2.9 Convergence lag cannot be measured below the sharp poll interval

**Where:** design addendum §4 table 5; spec §9.6 ("a measured convergence lag per venue").

Table 5 measures "median minutes from a sharp move ≥ 2 pts to the venue mid covering ≥ 50%". The venue mid is available at WS resolution; the sharp move is only observable at the poll boundary — 120 s for featured, 900 s for alternates outside T−3 h. The measured lag is therefore bounded below by the sampling interval and any value under ~2 minutes is an artifact.

**Exact change.** Compute table 5 on `feed_kind = 'featured'` shapes only, report the sampling floor as a column, and express the result as an interval `[max(0, lag − interval), lag]` rather than a point estimate. Note in the report that H6 (news latency) needs the burst-mode 15–20 s cadence, which the recorder already implements from T−100 to T−60 min for NFL — so H6's window, not table 5's, is where a sub-minute lag can be seen.

### 2.10 Order-message budget: the 15 s renew shape breaches the spec's own 60/min limit when it goes live

**Where:** design addendum §1 ("expiry … renewed each executor loop (three loops of 15 s), the same shape the live Kalshi adapter will use"); spec §9.2 (60 order messages/min per venue).

25 open orders renewed every 15 s is 100 messages/min before any place or cancel, against a 60/min budget whose breach trips the kill switch (spec §9.2). Phase 3 is paper so nothing breaks now, but the design explicitly commits phase 4 to this shape.

**Exact change.** Renew only when an order's remaining life is under `2 × exec_period_s`, i.e. once every third loop: 25 orders → ~33 renewals/min, leaving headroom for places and cancels. Write the rule into design §1 now so phase 4 inherits it.

---

## 3. NOTE

- **The adverse-selection estimate is never fed back.** Spec §6.3 defines `AS` as "the trailing measured 30-minute markout on filled orders by (sport, price bucket, side), seeded at 0.01 until 50 fills exist in the bucket". In code it is `as_estimate = _dec(cfg["as_seed"])` (`harness/strategy/run.py:180`) with `as_seed: 0.01` frozen in all six YAMLs. Phase 3 computes the markouts and never uses them; phases 4–6 in the roadmap do not schedule it. The design tension is real — `as_seed` is inside the config hash, so making AS live would change every `variant_id` and void the pre-registration. **Recommended resolution:** keep `as_seed` frozen for the pre-registered variants, add an `as_measured` column on `signals` recorded every tick from the trailing markout table (never used in the decision), and amend spec §6.3 to say AS stays at the seed through phase 4. The counterfactual is then computable by replay, and the promotion decision becomes a pre-registered week-4 amendment rather than a silent omission. If AS ever does feed the decision, its trailing window must end strictly before the decision instant — nothing in the plan guards that today.
- **Key numbers and the derived board.** `harness/pricing/margin_model.py:60` is a single normal with fixed σ (13.5 NFL / 17 CFB) and documents "no key-number adjustment; NFL ties ignored". NFL margins mass at 3 and 7, and the Kalshi NFL spread ladder sits at 2.5/3.5/6.5/7.5 — the rungs where the error is largest. H4 ("spread and total boards are mispriced relative to the moneyline board") would then read our own model error as venue mispricing, concentrated in its biggest cells. Roadmap phase 6 item 2 schedules the fix for late October. **Cheap interim measure, and better than the fix:** we already compute `direct` fairs from sharp alternates on many of the same rungs. Add a report table "derived − direct where both exist", bucketed by `|threshold − nearest key number|`. That measures the model's own error directly, costs one query, and turns H4 from a claim about the venue into a claim net of a measured correction. `p_moneyline` uses P(margin > 0) and so silently prices NFL ties (~0.2–0.4% of games) as losses for both sides; the two moneylines therefore sum slightly above 1.
- **CLV ROI denominator omits the fee.** Design §3 gives `clv_target_roi_net = (1/p_used)/(1/p_bench) − 1 − fee/p_used`, i.e. `(p_bench − p_used − fee)/p_used`. The cash-flow-correct form divides by what we actually pay: `(p_bench − p_used − fee)/(p_used + fee)`. The difference is ~0.7% relative at p = 0.55; a one-line change, worth making before the number is published weekly.
- **`clv_target_p` silently mixes two quantities.** Task 8: "`clv_target_p = p_bench − target` where target = the primary's `price_target` from `signals` (fallback `best_bid`)". For rows with a target this is "CLV of the price we would have posted"; for rows without it is "CLV of the venue's own bid". They cannot be pooled in a mispricing map. Split into `clv_target_p` (NULL when there is no target) and `clv_bid_p`.
- **`novig_mid_t5` needs a stated method.** Design §0.5 keeps this benchmark but does not say how the probability is derived. Novig is a no-vig exchange, so power-devigging its two-way prices the way Pinnacle's are devigged would distort rather than correct. State the method (normalise the two implied probabilities to sum to 1, no power devig) in design §3. Separately, H8 (Kalshi/Novig lead-lag) is bounded by the 120–900 s Odds API cadence; a cross-correlation at that resolution can only detect lags of minutes, and the report should say so rather than print a number.
- **`gap_outcomes` volume.** One row per gap snapshot per benchmark. With ~2000 matched markets on a game day and 30 ticks/hour, gap snapshots run to a few million per week; the full cross product with nine benchmark types reaches tens of millions of rows over three weeks. Restrict `compute_gap_outcomes` to the benchmark types the tables actually consume (`pinnacle_t5`, `consensus_t5`, `kalshi_mid_t5`, `result`) and batch it per game, as Task 8 already does.
- **`(sid, seq)` ordering is unsafe across a reconnect.** The SDD ruling has `load_book` apply deltas "in `(sid, seq)` order". `sid` is assigned per subscription within a connection and restarts on reconnect, and `WsRecorder.run_forever` resets `_sids` on every reconnect while `WsSink.reset_sequences()` clears the remembered sequence. Two connections can therefore both write `sid = 1`, and ordering by `(sid, seq)` would place the newer connection's `seq = 1` before the older one's `seq = 50000`. Order by `id` (insertion order, monotone with arrival) instead; keep `seq` for gap detection only.
- **WS trade `taker_side` defaults to "yes" when absent** (`harness/recorder/ws_sink.py:80`). A missing field silently becomes a buy, which biases H3's signed flow imbalance and, after 1.1, fabricates queue consumption on the wrong side. Prefer dropping the print with a counted warning, as the surrounding code already does for other missing fields.
- **Snapshot rows carry `received_at`, delta rows carry exchange `ts_ms`** (`ws_sink.py:88` vs `:92`). Mixing client and exchange clocks in one `ts` column is fine for ordering by `id` but not for "book mid at instant T" in markouts. The recorder already computes a Kalshi clock offset for authentication (`harness/venues/kalshi/clock.py`); record it on the snapshot row so the two can be reconciled later.
- **Ties on the moneyline board.** Task 7's `resolve_market` resolves a moneyline tie to `no` for both sides, which makes the YES pair sum to zero. Verify against the `KXNFLGAME` series rules before Week 1; if Kalshi voids instead, our derived settlement will disagree with the venue on any tied game and the mismatch counter will fire.
- **Kickoff changes invalidate frozen benchmarks.** Design §3 computes benchmarks "exactly once … and never updated except `result`", keyed to `kickoff_utc`. Spec §5.4 expects kickoffs to change and records `kickoff_changed`. Recompute (or invalidate) a game's benchmarks when its `kickoff_utc` moves after they were written.
- **Two active primaries would 500 the dashboard** — carried over from the phase 2 review (`dashboard/app.py:145-147`, `scalar_one_or_none()`). Now that the executor keys on `variant_id`, this is a one-line `.limit(1)` fix worth taking in Task 12.

---

## 4. What is sound

- **The recording layer and the separation of concerns.** Every source is `fetch_* → dict` / `parse_* → [Model]`, raw bodies are append-only and partitioned, normalized tables have real unique constraints, and `latest_book_lines` already bounds `fetched_at <= now` — so the phase 2 look-ahead defect is genuinely closed at the loader, not just at one call site. Given a fixed set of gap rows, the strategy is deterministic and replayable.
- **Units, signs and the pricing chain.** The phase 2 review's end-to-end trace holds up under re-derivation: the margin model round-trips (`mu = −home_point + σ·Φ⁻¹(p_cover)` recovers 0.5200 at −3.5), Kalshi's `+k.5` to book `−k.5` flip is right, `floor_cents` on a maker bid rounds the correct way, and `n_groups >= 2` correctly refuses to read `disagreement = 0` from a single group as agreement. No unit or sign defect at any module boundary.
- **The measurement architecture is the right one.** CLV attached to snapshots rather than bets, plural benchmarks, filters as labels, pre-registered variants, cluster-robust CIs by game, and a gate that ignores ROI and hit rate — this is the design that lets a negative answer be recognised as a negative answer. The defects above are in the execution of that architecture, not in the architecture.
- **The staleness amendment was the right call and is honestly documented.** Amendment 1 in design §0 diagnoses the zero-candidate result correctly (a Kalshi rung's exact sharp line comes from the 900 s alternates feed) and fixes it by feed cadence rather than by loosening a global constant. The residual issue is only that the gate then quietly changed metric (2.1).

---

## 5. Questions only the user can answer

1. **NAS free space.** §1.7 projects `orderbook_events` alone at ~1 TB across a 17-week season, before `raw_responses`. Is there 1 TB free on the DXP4800 Pro's docker volume, or should the retention decision in design §0.6 be revisited before Week 1 rather than at the 800 GB alarm?
2. **Whether to accept the two-day cost of NO-side signals before Week 2** (§1.6), which is the one recommendation here that adds scope rather than correcting a defect. The alternative is a three-week dataset that cannot test the maker edge the spec's own evidence points at.
