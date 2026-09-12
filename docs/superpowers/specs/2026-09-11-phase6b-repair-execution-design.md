# Phase 6B design addendum: repair execution (continuity, anchoring, reconciliation, expiry, rejection, dirty scope, capacity-equivalent replay)

Date 2026-09-11. **Revision 2**, after two opus design reviews - `design-review-6b-exp` (experiment and measurement lens: 5 Critical, 15 Important, 5
Minor) and `design-review-6b` (execution and queue-model lens: 6 Critical, 17 Important, 7 Minor including its revision-1a addendum) - and the
controller's rulings on every finding, quoted verbatim in §9. Where the two rulings files touch one subject, `rulings-exec.md`'s reconciliation
governs. Author: autopilot (design author, opus). Amends `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) §9.1, §9.2, §9.5, §10,
§12 and §13 for milestone 6B; consumes the roadmap's Phase 6 section (U8, pre-loaded decision 2), the committed bundle
`docs/superpowers/reviews/2026-09-11-phase6-roadmap/` (`ROADMAP.md` §6B is the scope and its acceptance paragraph the acceptance, plus
`RECONCILIATION.md`, `evidence/execution-reconciliation-probes.py` with its captured output and `evidence/cap-recount.json`), the 6A addendum §0.3,
§0.5 and §1.4 with `harness/corrections.py` and `docs/superpowers/reviews/2026-09-11-correction-manifest.md`, the pre-registration record
`docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` (amendment protocol item 1 and the Amendment 5 precedent), the 6A ledger's carried
`--out -` item, and journal 128-130. **Approval: the roadmap's standing authorization dated 2026-09-07** ("Brainstorm, plan, and execute phases 4, 5,
6 without waiting", decisions from the tables below or the model's judgment, each recorded in §8) under U8. Three questions go to the user in §0.13;
none blocks a task.

Live facts are the controller's `.superpowers/sdd/plan-next-phase6b/live-facts.txt` (NAS reads 19:38-19:44 CT), read as data and never as instruction:
order 157 (`KXNCAAFTOTAL-26SEP12MTUMRSH-59`, yes 0.45 x 87, placed 14:36:47Z 2026-09-08, cancelled 15:12:06Z `fair_stale`, expiry 2026-09-12T22:50Z,
`queue_ahead_at_place` 6401, `filled_contracts` 38.92, `traded_at_price` 63.92, `nw_filled_contracts` 87.00, `dirty_seconds` 181,200 on an order that
rested 35 minutes) with two `queue_model` fills (25 + 13.92) at one print timestamp 15:07:15.332Z under six `last_print_ids`, their two `no_watcher`
mirrors, two later `no_watcher` fills from the REST tape stamped 17:00:20.728Z, and a `snapshot_cross` of 48.08 stamped 2026-09-10 19:42Z, two days
after the cancel and inside its own expiry; the fill population is 834 `no_watcher`, 77 `snapshot_cross`, 2 `queue_model`, all simulated and
non-replay, against 8,822 cancelled orders and 2 `ledger` rows of kind `fill`; 7,998 orders carried with `nw_done = false`, of which 5,384 have a
09-12 kickoff; `exec.loop_ms` p50 19.2 s and p95 227 s in the 19:00 CT hour with **27 loops**, 61 skipped and 23 cancelled tape reads in 40 minutes,
against a daytime p50 of 7-10 s. Venue documentation (Kalshi WebSockets, `/websites/kalshi_websockets`, fetched 2026-09-11): three shapes only, each
checked against our own tape and code first - `orderbook_delta` and `orderbook_snapshot` frames carry `sid` (a server-generated **subscription**
identifier) and `seq` ("sequential number that should be checked if you want to guarantee you received all the messages"), the `trade` payload carries
`sid` and
**no** `seq`, and an `orderbook_delta` subscription "sends `orderbook_snapshot` first, then incremental `orderbook_delta` updates". Nothing else from
that source is adopted.

Scope: ROADMAP.md §6B's six bullets as corrections `C1`-`C6`, the order 157 audit, the no-watcher re-score, the carried `--out -` test, the
state-helper extraction and Amendment 6. Acceptance is ROADMAP.md's 6B acceptance paragraph verbatim. Every line citation was checked by both reviews;
their corrections are applied.

## 0. Amendments to v2 and to the roadmap text

- **0.1 (§6.7, U8 6B) C1-C6 are versioned corrections and collectively Amendment 6.** Each is a `Correction` appended to
  `harness.corrections.CORRECTIONS` with the fields `C0` uses; `MANIFEST_VERSION` becomes 7. Because they change label semantics (a bug fix that
  changes a label), the pre-registration record's amendment protocol item 1 applies and the Amendment 5 precedent governs: **C1-C6 collectively are
  Amendment 6**, appended by §1.11 with item 1's five fields, kept in step by a parity test. `affected_order_id_range` and `affected_run_id_range`
  carry the **numeric** boundary, filled at merge time (the D11 pattern), and the record prose carries `boundary_fill_id`, `boundary_event_id` and
  `boundary_ledger_id`, so the manifest alone reproduces §3's queries. The narrow claim, and the only one made: `gate.py`'s `CRITERIA` text, its
  thresholds and `criteria_hash()` are untouched and the pinned hash `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5` must not move
  (R1, invariant 2). The repairs do change the rows those criteria read; where that reaches the gate's meaning it goes to the user in §0.13. No
  original `orders`, `fills` or `ledger` row is updated or deleted (invariant 5).
- **0.2 (§9.2) Continuity is a property of the subscription, not of a ticker.** `seq` counts per `sid` across every market on it, so a ticker whose
  frames read 1 then 3 lost nothing when frame 2 was another ticker's. `BookState.apply_delta` tests `int(seq) != self.seq + 1` per book object
  (`book.py:272-274`) and dirties the book on ordinary interleaving, while `WsSink._check_seq` (`ws_sink.py:84-100`) already makes the
  subscription-level test and writes a `gap` row under the sentinel `ticker = ""`. 6B deletes the per-object check and reads continuity from those
  sid-level rows (`_gapped` / `_gapped_at`, `book.py:346-356`), plus a consumer-side probe for the one hole the recorder cannot see: the delta read is
  `id > :cursor and ts >= :lower` with `lower = as_of - DELTA_LOOKBACK` (`book.py:49, 56-59, 403`), so a delta stamped more than five seconds behind
  the book passes the id cursor and is dropped by the `ts` floor with no gap row anywhere. `book_at` is **not** changed (its docstring stands);
  `_sim_book` calls `load_book_at` for the live verdict (`book.py:467-471`).
- **0.3 (§9.2) Session boundaries are a continuity case with no gap row.** On a reconnect the client drops its sids and clears remembered sequences
  (`ws.py:358-368`), so frames lost across the outage produce no `gap` row and a cached book anchored before the outage keeps folding in the new
  subscription's deltas. The test cannot be made on `as_of`, which `apply_delta` overwrites with every delta (`book.py:283`) and which would read
  10:06 for a book anchored at 10:00 whose reconnect was at 10:05: `BookState` gains an immutable `anchor_as_of`, set in `from_levels` /
  `from_ws_raw`, carried by `copy()`, untouched by `apply_delta`, and compared with the newest `operator_events(kind = 'ws_connect')`. The executor
  reads that instant once per step and writes the verdict onto the **cached** book, so `advance_book`'s re-anchor branch (`book.py:409-413`), which
  reads `out.dirty` off the cached object, can clear it.
- **0.4 (§9.5) Anchoring a snapshot anchors the print floor with it.** The recovery branch (`loop.py:828-836`) takes the queue to what is resting now
  and advances `cursor_event_id` but leaves `last_print_ts`, so a trade from inside the gap, already in the snapshot, is applied again against the
  newly anchored queue; the no-book branch (`loop.py:816-826`) sets the watermark to `now`, the recorder's clock rather than the book's. Both branches
  instead set the `print_floor` of §0.6, the §1.3 ledger and the delta cursor together from the anchoring book, and the queue keeps its existing
  `min()` clamp (`loop.py:834`): a level that grew during the dirty stretch must not charge us for liquidity that joined behind us under price-time
  priority.
- **0.5 (§9.5) Trades and level decrements are reconciled inside a horizon, not ordered.** `_merge_events` folds deltas before prints at equal
  timestamps (`fills.py:47, 253-271`) assuming the delta arrives with the trade it reports; `_apply_queue_delta` then charges the decrement against
  `traded_at_price` (`fills.py:307-320`), which only prints fill. With the delta first the accumulator is empty, the decrement reads as a cancel, the
  queue moves, and the print moves it again - the double count the probes captured (`same_event`: queue 0.00, fill 1.00 where 2 and 0 are due) and the
  mechanism that reproduces order 157's quantities with no recovery. §1.3 replaces the ordering assumption with a two-sided ledger **bucketed by
  timestamp within `store.PRINT_LOOKBACK`**, a decrement being claimable only by a print inside that horizon; without it the repair would regress a
  case the current code gets right (a cancellation of 2, then a real trade of 3 ten minutes later: fill 3, not 1). Swapping the sort order is not the
  repair, and §1.3 states the arithmetic as a formula.
- **0.6 (§9.5) The print floor and the trade-id dedup set are separate things.** `_seen_print` (`fills.py:162-172`) skips any print stamped before
  `last_print_ts`, which makes a late REST backfill unusable and is what silently does the re-anchor's work today; replacing it with dedup alone would
  reintroduce the recovery defect. So: a `print_floor` on `SimState`, set by both re-anchor branches to `anchor_as_of - DELTA_LOOKBACK` (a snapshot's
  `as_of` is the recorder's receive clock, `ws_sink.py:151`, while prints carry the venue's `ts_ms`, `ws_sink.py:124-126`), enforced in
  `_merge_events` as `ts > max(placed_at, print_floor)`; and a `trade_id` set governing everything above it, bounded by the track's own window
  (`placed_at - PRINT_LOOKBACK` to the deadline) rather than by 60 seconds, because `loop.py:717` re-reads the order's whole resting print history
  every loop. It is persisted per order and capped; at the cap the oldest ids drop and `print_floor` rises to the oldest retained id's timestamp, so
  nothing below the floor can re-apply.
- **0.7 (§9.5, U8 6B) The cancel convention is stated and measured.** Aggregate book data cannot say whether a cancellation was ahead of us. The point
  estimate keeps today's convention - an unmatched decrement at our price rests **ahead** of us - and the other end of the band (all cancels behind
  us) is reported. `cancels_ahead = 0` proves an order insensitive to the choice; the converse does not hold, so the two policies' disagreement rate
  is the band rather than an asserted equality.
- **0.8 (§9.2, R8) Expiry is clamped on both fill paths, and no order is placed from a rejected latest signal.** The watched track is simulated to
  `now` (`loop.py:850-852`) while the counterfactual clamps to `min(now, expiry)` (`loop.py:867`). Clamping the walk is not enough: the cross fill
  taken **before** the walk tests `_crosses` on entry and stamps itself with the book at the loop instant (`fills.py:364-369`), which is how order 157
  carries a `snapshot_cross` stamped two days after its cancel and would leave §3 row 2 reporting lawful rows as failures. The entry cross is
  therefore taken only when `working.as_of <= deadline`. The rejected-verdict test, which exists only on the cancel path (`plan.py:511`), is added to
  `_intent_actions` (`plan.py:519-556`) **after** the data-quality tests (`_fair_stale`, `market.dirty`) and **before** the capacity tests, so a
  rejected verdict never masquerades as a data skip and never consumes capacity; the reason re-attribution is recorded in `C4` and named as an input
  to 6C's funnel units.
- **0.9 (§9.5, §10, U8 6B) Dirty time is scoped and caused on the market; elapsed time is derived, not accrued.** `store.add_dirty_seconds`
  (`store.py:684-693`) is called before any status test (`loop.py:799-803`), so a cancelled order whose counterfactual still runs keeps accruing on
  its own column - the mechanism behind order 157's 181,200 seconds on a 35-minute order. That scope error is the repair: the watched column accrues
  only while the order is in `store.OPEN_STATUSES`, the counterfactual's accrual moves to `orders.nw_dirty_seconds`, and each increment is clamped to
  the remaining watched interval, never past `expiry`, because `_order_action` deliberately holds `Expire` on a lagging ticker (`loop.py:760-768`) so
  a past-expiry order can stay `open`. Accrual stays **nominal** (`exec_period_s` per observation): the gate reads it and changing its units is
  §0.13c. Elapsed truth is derived at read time from `market_dirty_intervals` (dirty stretches with a cause) and `market_observation_intervals`
  (stretches the executor actually stepped the market) by one bounded parameterised query. One elapsed mechanism, one nominal one: different
  quantities by design, both reported.
- **0.10 (§9.5, gate cleanliness) The observation interval is specified; the elapsed switch is not adopted.** Criterion 1 reads `book_source = 'ws'
  and dirty_minutes = 0` (`gate.py:360`), and `dirty_minutes` is integer division of accrued seconds (`store.py:692`), so the classification boundary
  is **60 accrued seconds, not one**. Elapsed accrual would move that classification in both directions: at the 19:00 CT p95 of 227 s one dirty
  observation would take an order from `dirty_minutes = 0` to 3, where four nominal observations are needed today, while a market dirty for a few
  elapsed seconds across several fast loops would accrue less than the nominal 15 s per loop and move orders the other way. That is "which resting
  interval counts" in U8's words, so it is **not adopted here**: the gate-read columns keep nominal accrual and the question goes to the user as
  §0.13c. 6B specifies the interval as the whole watched resting interval, `[placed_at, min(cancelled_at, expiry)]`, which is what the repaired scope
  measures; the argument rests on the correction record's defect list, not on the column comment, which names no interval.
- **0.11 (§12, R14) Baseline replay reproduces the range's population, and the parity verdict is suspended.** `replay()` scores one variant and builds
  a single-variant executor (`replay.py:249-250`) while live capacity is shared by one counter (`plan.py:609`). The population must come from the
  **range**, not from today's `Settings.exec_variants`: the executed set changed during the paper run (pre-registration Amendments 2, 3 and 4), so a
  replay spanning a change is not population-equivalent. The command resolves the set from the executor configuration in force over the range
  (`config_history` by the range's runs) and **refuses**, with an exit code and no partial output, a range spanning a change of the executed set or
  the 6B deploy boundary. The Monday duty's 2 % parity verdict is suspended while the timing policies differ - the grid steps exactly `exec_period_s`
  while the live loop ran 27 loops in the sampled hour - and the duty publishes both step counts, the divergence and the correction ids on each side
  instead. Stepping a replay at recorded loop instants is 6D's.
- **0.12 (U8 6B) Corrected results are new rows, and the re-scoring instrument is documented.** The re-score and the audit write `order_rescores` rows
  tagged with the corrections in force and labelled retrospective estimates; no pre-deploy `orders`, `fills` or `ledger` row is updated. `harness
  rescore --correction <ids>` becomes a recognised re-scoring instrument beside `harness replay`: an `order_rescores` row is the retrospective
  estimate for an order-scoped correction, a `replay = true` row remains the instrument for a range-scoped one, and `Correction.rescore_command`'s
  docstring, the correction record's Protocol paragraph and pre-registration item 1's template change together in §1.11. 27/445 is quoted nowhere
  except the sentence that refuses it.
- **0.13 Needs the user's dated decision (R1); nothing waits on any of the three.** (a) *Eligibility boundary:* "Should the cumulative gate count only
  orders placed under the 6B corrections - that is, should `GATE_ELIGIBLE_FROM_ORDER_ID` and `GATE_ELIGIBLE_FROM_RUN_ID` be set together to the 6B
  deploy boundary, and on what date?" That boundary is a **measurement** boundary, not 6F's prospective-period boundary, which the roadmap records
  only once 6B, 6C's numeric and eligibility rows, 6D's policy and 6E's acceptance are in; the loop's recommended default is the third option, **leave
  both dormant until 6F records its own boundary**, with post-6B analysis carried by 6C's "since boundary" rows as 6A §0.4 contemplates. Until then
  the gate reads the whole non-replay history, which after 6B is a mixed population, and §3 row 10 checks every gate render says so. (b) *Cleanliness
  interval:* "Criterion 1's clean-book share is measured over the whole watched resting interval (§0.10). Do you want it measured up to the first
  fill, or at placement only, instead?" (c) *Elapsed accrual:* "Should `orders.dirty_seconds` / `dirty_minutes` accrue elapsed wall time instead of a
  nominal `exec_period_s` per observation, and from which boundary?" 6B keeps nominal and records elapsed in parallel, so either answer is adoptable
  with no further code: §1.5 reports the watched, counterfactual, to-first-fill and unobserved seconds for every order.
- **0.14 (§9.5, F3) The counterfactual's scope is stated; its cost is bounded by backoff, never by abandonment.** Every order keeps a `no_watcher`
  track from `placed_at` to its natural `expiry` (kickoff minus 10 min) whatever we did; that is F3's design, unchanged here, and it is why order 157
  took fills two days after its cancel and inside its own expiry. Of the 7,998 tracks carried, 5,384 have a 09-12 kickoff and are open for that reason
  alone. The cost is real
- a ticker whose read failed is skipped before the track runs (`loop.py:559`) and `done` needs `row.ticker not in lagging` (`loop.py:878-880`), so
    under starvation a past-expiry track is re-read every loop - but closing such a track would remove its order from gate criterion 4's population,
    which `_MARKOUTS` (`gate.py:437-446`) builds with no `_FILL_EVENT` predicate from the `nw_fill` anchor (`gate.py:66`, `markouts.py:273`), and with
    834 no-watcher fills against 2 queue-model fills that population is almost entirely no-watcher driven. **6B therefore never closes a
    counterfactual track** (§6). A track whose ticker's tape read failed is retried on an exponential backoff in elapsed wall seconds -
    `exec_period_s` doubling per failed read up to `NW_RETRY_MAX_S = 3600`, reset on a successful read - evaluated where the step chooses which
    tickers to read, **before** `_tape`, so a ticker whose next attempt is in the future is simply not read and the unread skip at `loop.py:559` never
    sees it. Criterion 4's population is untouched and no R1 question arises. The backoff removes only the repeated reads of unreadable tickers (23
    cancelled reads in 40 minutes on the sampled evening); it is not a performance fix for the healthy population, most of which is pre-kickoff and
    legitimately carried (§4.5). Every bound here is elapsed wall time, never a loop count: at 27 loops in the sampled hour a loop-counted bound would
    stretch by an order of magnitude in exactly the conditions it exists for.
- **0.15 (roadmap 37, journal 128) The 6B deploy is the full recipe.** The additive DDL touches `harness/db/models.py`, on the full-deploy trigger
  list, so journal 128's app-only allowance does not apply and R4 governs.

## 1. Components

Each names the spec section or roadmap bullet it implements, its files, what it depends on, the regression it turns green, and the independently
  calculated expected result it is judged by. §1.1-§1.6 are ROADMAP §6B's six bullets in order; §1.7-§1.9 the audit, the re-score and the carried
  `--out -` test; §1.10 and §1.11 the refactor and the amendment the reviews required. Each component removes the `xfail` marker of the defect it
  repairs, one at a time; a strict XPASS is a hard failure.

### 1.10 State-helper extraction (no correction; execution review M-5)
`_state_of` and `_state_columns` move from `harness/execution/loop.py` into `harness/execution/state.py` unchanged, with a test asserting a round trip
  through every column. A pure refactor with no behaviour change, it runs first so §1.3 can own the per-track state shape without touching `loop.py`
  and can start beside §1.1.
*Files:* `harness/execution/state.py`, `harness/execution/loop.py`, `tests/test_exec_state.py`. *Depends on:* nothing.
*Expected result:* `make test` unchanged; the moved functions are identical apart from their imports.

### 1.1 Subscription continuity (correction C1; ROADMAP §6B bullet 1; §0.2, §0.3)
`BookState.apply_delta` stops testing `seq` and stops setting `dirty` from it, keeping `seq` only as a record of the last frame applied. Continuity
comes from three places instead: the sid-level `gap` rows `_gapped` / `_gapped_at` already read on every book path; the consumer-side probe of §0.2
per advance (`select 1 from orderbook_events where ticker = :t and kind = 'delta' and id > :cursor and ts < :lower limit 1`, one bounded row on
`ix_obe_ticker_id`); and §0.3's session boundary through the new immutable `anchor_as_of`. `BookState` also gains `dirty_cause`, set at every site
that sets `dirty` - a gap row (`book.py:389, 407`), a malformed tape row (`book.py:332-336`), a stale REST delta (`book.py:329-332`), the probe, the
boundary - because `dirty` is one flag with at least four producers and §1.5's labels cannot be derived from `MarketNow.dirty`'s three booleans.
`book_at` is unchanged; `_sim_book` (`loop.py:902-905`) calls `load_book_at`.
*Files:* `harness/execution/book.py`, `harness/execution/loop.py`, `tests/test_book.py`, `tests/test_execution_regressions.py`. *Depends on:* nothing.
*Turns green:* `test_multiplexed_subscription_sequence_does_not_dirty_the_book` (case 1a).
*Expected result, computed independently:* subscription 7 carried frames 1, 2, 3 with nothing missing and frame 2 was ticker B's, so ticker A received
every frame addressed to it and no level of A is stale: `dirty` is False. The guard `test_a_real_missing_subscription_frame_still_writes_a_gap_row`
(1b) stays green and `test_book.py:84` (`test_seq_gap_marks_dirty`), which pins the behaviour being removed, is rewritten to the new rule. Three new
cases: a book anchored 10:00 with a `ws_connect` at 10:05 and a delta at 10:06 is dirty until a post-10:05 snapshot lands, with the fixture advancing
`as_of` past the reconnect so the test cannot pass on the wrong field; a delta stamped six seconds behind `as_of` dirties the book through the probe;
`dirty_cause` carries the producing site in each.

### 1.2 Recovery anchoring (correction C2; ROADMAP §6B bullet 2; §0.4, §0.6)
Both re-anchor branches of `_simulate_order` set, per track: `queue_remaining = min(queue_remaining, resting)`, `cursor_event_id =
book.last_event_id`, `print_floor = book.anchor_as_of - DELTA_LOOKBACK`, the §1.3 buckets empty and the trade-id set pruned to the floor. One helper
serves both branches so the no-book and recovery paths cannot drift. The two clocks are named in the code: the floor is on the recorder's receive
clock, prints on the venue's `ts_ms`, and `DELTA_LOOKBACK` (5 s, `book.py:45-49`) is the slack, inside which a print is reconciled by the ledger, not
dropped.
*Files:* `harness/execution/loop.py`, `harness/execution/fills.py`, `tests/test_exec_loop.py`, `tests/test_execution_regressions.py`. *Depends on:*
1.3, 1.10.
*Turns green:* `test_recovery_takes_no_fill_from_a_trade_inside_the_gap` (case 3).
*Expected result, computed independently:* five contracts rest ahead before the gap; one trade of three happens inside it; the recovery snapshot,
taken after that trade, shows two resting. Two is five minus the same three, so the trade is spent: applying it again would take the queue to -1 and
pay us one contract we were never in line for. Fill 0, queue 2, `print_floor` at the anchor instant less the slack. Two further cases: a state of 5
against a resting 9 keeps queue 5, not 9 (the `min` clamp); a REST print stamped **above** the floor arriving late is applied exactly once.

### 1.3 Trade and decrement reconciliation, with sensitivity (correction C3; ROADMAP §6B bullet 3; §0.5-§0.7)
Per track at our own `(side, price)`, the ledger is three terms in **new** columns - `print_unmatched` (print volume whose decrement has not arrived),
`pending_unmatched` (decrement volume that took queue ahead of us, unclaimed) and `pending_surplus` (decrement volume beyond the queue, unclaimed) -
plus `cancels_ahead` (retired, unclaimable). `traded_at_price` and `nw_traded_at_price` are **left null** on every post-boundary order rather than
reused, so C0's charge-against quantity and the ledger term are never confused and the boundary is visible by nullness. The arithmetic, as a formula,
with `remaining = contracts - filled`:

- shrinking delta of size `d` at our price: `m = min(d, print_unmatched)`; `print_unmatched -= m`; `rest = d - m`; `consumed = min(queue, rest)`;
  `queue -= consumed`; bucket `(delta.ts, consumed)` joins `pending_unmatched` and bucket `(delta.ts, rest - consumed)` joins `pending_surplus`.
- hitting print of `count c` at our price: `u = min(c, pending_unmatched within horizon)` (ahead of us and now known to have traded: no fill); `s =
  min(c - u, pending_surplus within horizon)` (beyond the queue, so they reach us: fill `min(s, remaining)`); `r = c - u - s`; `consumed = min(queue,
  r)`; `queue -= consumed`; fill `min(r - consumed, remaining)`; `print_unmatched += r`. A print **through** our price still sweeps the queue to zero.
- horizon: a bucket is claimable only by a print within `store.PRINT_LOOKBACK` (60 s, `store.py:60`) of the bucket's own timestamp; an aged
  `pending_unmatched` bucket retires into `cancels_ahead`, an aged `pending_surplus` bucket is discarded, having never moved the queue.

Buckets, trade-id set and floor persist per track in one bounded `jsonb` column (`recon_state`, `nw_recon_state`) as `{buckets: [[ts, kind, size]...],
  trade_ids: [...], print_floor}`, capped and pruned to the horizon and the track window on every write; a restart reads it back, and a null column (a
  pre-boundary order) starts empty. The scalar columns are the surviving buckets' sums, which §2 makes an invariant. `simulate_fills` takes
  `cancel_policy` (`ahead` by default, `behind` for the bound), used only offline by §1.8.
*Files:* `harness/execution/fills.py`, `harness/execution/state.py`, `harness/db/models.py`, `harness/db/schema.py`,
`migrations/versions/0007_phase6b_execution.py`, `tests/test_fills.py`, `tests/test_fills_tape.py`, `tests/test_execution_regressions.py`. *Depends
on:* 1.10.
*Turns green:* `test_a_print_and_its_own_delta_are_one_event` (case 2).
*Expected result, computed independently:* five rest ahead at 0.30; one real trade of three lifts three of them, leaving two ahead and nothing for us;
the same-timestamp delta of -3 is the exchange reporting that trade, not a second removal. Queue 2, fill 0 - and the identical answer for
print-then-delta, a delta split -2 then -1, a print split 2 then 1, and across a persisted loop boundary. Queue 2 with a print of 5 gives fill 3,
queue 0 in both orders (the `pending_surplus` path). The fifth case is the horizon's: queue 2, a genuine cancellation of 2 at our price, a real trade
of 3 ten minutes later - queue 0, fill 3, `cancels_ahead` 2, which today's code also gets right and a horizon-less ledger would break. The probe
`fixture_equal_timestamp_matching_delta` (196 of 196 prints carry a same-timestamp matching delta) says this is our tape's normal case, carried with
its own recorded limitation: "At least one matching delta per print; not unique event pairing."

### 1.4 Expiry clamp and rejected-latest-signal placement (correction C4; ROADMAP §6B bullet 4; §0.8)
The watched track's deadline becomes `min(now, expiry)`, and `simulate_fills` takes its entry cross only when `working.as_of <= deadline`, which is
what actually delivers "no post-expiry fills"; the in-walk cross is already bounded because `event.ts` comes from `_merge_events`. `_intent_actions`
gains `if intent.latest_decision == REJECTED: return [Skip(intent.intent_id, SIGNAL_REJECTED)]` after the `book_dirty` skip and before the target and
capacity tests, which `uq_skip_once` (`0001_baseline.py:840`, partial on `kind in ('skipped','cap_gate')`) makes idempotent.
*Files:* `harness/execution/loop.py`, `harness/execution/fills.py`, `harness/execution/plan.py`, `tests/test_exec_plan.py`, `tests/test_exec_loop.py`,
`tests/test_execution_regressions.py`. *Depends on:* 1.2.
*Turns green:* `test_the_watched_track_takes_no_fill_after_expiry` (case 5) and `test_a_rejected_latest_verdict_yields_no_place` (case 6).
*Expected result, computed independently:* an order with expiry T0+10 s is off the market from T0+10 s, so a print at T0+20 s cannot trade against it:
fill 0 even with queue 0. A book that first crosses at T0+20 s against the same order produces **no** `snapshot_cross` row where today it produces one
stamped T0+20 s. And `latest_decision = rejected` is the strategy's own current answer that this is not a bet, so `Place` actions number 0 and exactly
one `skipped` row with reason `signal_rejected` is written. Idempotence is asserted by re-running the loop over the same tape after persisting and
re-reading the state: no second fill, no second skip row.

### 1.5 Dirty intervals, observation coverage and counterfactual backoff (correction C5; ROADMAP §6B bullet 5; §0.9, §0.10, §0.14)
Two additive tables, both models: `market_dirty_intervals` (one row per market per contiguous dirty stretch, `cause` from `BookState.dirty_cause` -
`gap`, `session_boundary`, `recorder_dead`, `event_age`, `malformed_row`; there is no `recovery` cause, the recovery branch being the one taken when a
market has **stopped** being dirty) and `market_observation_intervals` (one row per contiguous stretch the executor actually stepped the market). Both
close every open row for a market absent from the step's market set, stamped at the last observation that saw it, so a market whose last order closes
while dirty cannot leave a row open forever (`loop.py:509` prunes the cache). Per-order dirty time is **derived**:
`harness.execution.dirty_time.order_dirty_time(session, boundary_order_id, limit)` is a bounded parameterised query, never a bare view, intersecting
each order's watched `[placed_at, min(cancelled_at, expiry)]` and counterfactual `[placed_at, expiry]` intervals with both tables and returning
`watched_dirty_s`, `counterfactual_dirty_s`, `to_first_fill_dirty_s`, `unobserved_s` and the per-cause breakdown, clamping a still-open interval to
`least(now, deadline)`. Unobserved time is reported, never folded into clean time: absence of a row means "not observed", not "observed clean".
`add_dirty_seconds` is called only for an order in `store.OPEN_STATUSES`, still adds the nominal `exec_period_s`, and is clamped to the remaining
watched interval (§0.9); the counterfactual's nominal accrual moves to `orders.nw_dirty_seconds`. The nominal columns and the elapsed derivation are
different quantities by design, both reported, and the derivation is what the correction record and the re-score quote. Counterfactual backoff
(§0.14): `nw_next_attempt_at = now + min(NW_RETRY_MAX_S, exec_period_s * 2 ** nw_attempts)`, `nw_attempts` reset on any complete read, evaluated
before `_tape` when the step chooses tickers. Nothing is closed, so `nw_done` alone distinguishes a completed counterfactual from a pending one, and
every consumer of the `nw_*` columns - §1.8's re-score, 6C's actual-versus-counterfactual separation, any funnel denominator - filters or labels on it
(§3 row 12). The pending count is published as `exec.nw_pending`.
*Files:* `harness/execution/loop.py`, `harness/execution/store.py`, `harness/execution/dirty_time.py`, `harness/db/models.py`, `harness/db/schema.py`,
`migrations/versions/0007_phase6b_execution.py`, `tests/test_exec_loop.py`, `tests/test_dirty_time.py`, `tests/test_execution_regressions.py`.
*Depends on:* 1.1, 1.4.
*Turns green:* `test_a_cancelled_order_accrues_no_dirty_seconds` (case 4).
*Expected result, computed independently:* `dirty_minutes` is a property of the watched order - how long the order we placed sat against a book we
could not read. A cancelled order is not sitting against anything, so no write to its own counter is due: `add_dirty_seconds` calls 0, seconds added
0, and the same observation adds `exec_period_s` to `nw_dirty_seconds`. Three more cases: an order cancelled at T+60 whose market is dirty T+30 to
T+120 with expiry T+600 has `watched_dirty_s` 30 and `counterfactual_dirty_s` 90; the same order with observation intervals ending at T+200 has
`unobserved_s` 400, appearing nowhere in the clean total; a past-expiry order still `open` on a lagging ticker accrues nothing past its expiry. A
fourth pins the backoff: a track whose read fails three times has `nw_next_attempt_at - now` of 15, 30 and 60 s, caps at `NW_RETRY_MAX_S`, is never
closed, and resets to `nw_attempts = 0` on the first complete read.

### 1.6 Capacity-equivalent baseline replay (correction C6; ROADMAP §6B bullet 6; §0.11)
`replay()` accepts a list of variants, or `--population range`, which resolves the executed set from the executor configuration in force over the
replayed range (`config_history` by the range's runs), scores every run under each and constructs **one** executor over all of them so `plan_actions`
applies the same shared `max_open_orders` counter as the live loop (`plan.py:589-609`). It refuses, with an exit code and no partial output, a range
spanning a change of the executed set or the 6B deploy boundary. `ReplayCounts` carries the resolved population, today's `exec_variants`, the
correction ids on each side, both step counts and the timing divergence; single-variant replay is unchanged and labelled `single_variant`. The Monday
duty's 2 % parity **verdict is suspended** while the timing policies differ and the duty publishes the numbers instead.
*Files:* `harness/replay.py`, `harness/cli.py`, `tests/test_replay.py`. *Depends on:* 1.1-1.5.
*Expected result, computed independently:* over a seeded range with three variants and `max_open_orders = 2`, seven intents whose edges rank across
variants leave at most two orders open at any instant and produce exactly the `exec_capacity` skip **occurrences** the live ordering rule
(`_order_action` first, then intents by edge) gives - counted as planner actions, since `uq_skip_once` collapses repeated skips of one intent to a
single row, which the test asserts separately. The single-variant replay of the same range produces strictly more orders for that variant, and a range
crossing a registered change of the executed set exits non-zero with nothing written.

### 1.7 The order 157 audit (ROADMAP §6B "audit order 157 before using it as a validated fill")
`harness audit-order --capsule <dir>` reads the 6A capsule for order 157 (tape, prints, snapshots, fills, events, watch samples, ledger) with no
database and no NAS access, replays it under the repaired simulator and compares against the
**recorded** quantities - `filled_contracts` 38.92, `traded_at_price` 63.92 (read under C0's charge-against definition only, which is why §1.3 leaves
that column null afterwards), `queue_remaining` 0 - rather than against a C0 code path, which C1-C5 remove from the tree. Verdicts: `validated`
(reproduces the recorded fills within one contract), `corrected` (differs **and** one hypothesis's stated expected counts are met), `unverifiable`
(the tape does not cover the interval, nothing anchors it - the 6A manifest's `unverifiable_slices` is that call's input - **or** it differs and no
hypothesis's counts are met, which is RECONCILIATION.md's "requires tape audit" rather than a causal story the evidence does not support). The
hypotheses: (i) the equal-timestamp double count, predicting a decrement near -6,376 at our price stamped 15:07:15.332Z beside prints summing to 63.92
across the six recorded `last_print_ids`; (ii) a recovery anchoring error, predicting a `gap` row on the anchor's sid and a snapshot between 14:36:47Z
and 15:07:15Z; (iii) a genuine queue collapse, predicting prints of 6,401 or more at 0.45 before the fills. Each is a capsule query with a stated
expected count, so the verdict is evidence, not a preference. The verdict string lives in `harness/corrections.py` (6C's t13 reads it inside the
container) beside the record `docs/superpowers/reviews/order-157-audit.md`, undated because the run date is the controller's.
*Files:* `harness/audit.py`, `harness/cli.py`, `harness/corrections.py`, `tests/test_audit_order.py`, `docs/superpowers/reviews/order-157-audit.md`.
*Depends on:* 1.1-1.5.
*Expected result, computed independently:* four synthetic capsules, one per outcome. The reconciliation's own counterexample (queue 6,401, a
same-timestamp decrement of -6,376, prints of 25, 25 and 13.92, reproducing 38.92, 63.92 and queue 0 with no recovery) is `corrected` with a repaired
fill of 0; one with no anchoring snapshot is `unverifiable`; one whose prints genuinely exhaust the queue is `validated`; one that differs with no
hypothesis met is `unverifiable`. The controller runs it on the real capsule.

### 1.8 The no-watcher re-score (ROADMAP §6B "re-score no-watcher outcomes only after the same model repairs")
`harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]` walks each original order's capsule or tape
window under the repaired simulator and writes one `order_rescores` row per (order, correction set, cancel policy): `order_id`, `correction_ids`,
`cancel_policy`, `watched_filled`, `counterfactual_filled`, `queue_remaining`, `cancels_ahead`, `watched_dirty_s`, `counterfactual_dirty_s`,
`unobserved_s`, `verdict`, `computed_at`, `build_sha`. It is resumable on the primary key, each order's tape read is bounded by
`store.DELTA_BATCH_LIMIT` and its own `statement_timeout`, and a cancelled read writes `verdict = 'unverifiable'` rather than aborting the run. It
selects on `nw_done` so a pending counterfactual is never read as a completed one. The result is reported as a **partition with counts over an
order-level denominator** - completed, unverifiable-no-tape, unverifiable-read-cancelled - with the right-censoring caveat attached, never as a single
ratio; 27/445 is a pooled, key-level, right-censored count from the defective simulator, is not recomputed in 6B and is not quoted beside the
re-scored numbers. The report side of this component owns the mixed-population sentence and the correction ids §3 row 10 checks in `render_gate`'s
output.
*Files:* `harness/rescore.py`, `harness/cli.py`, `harness/report/gate.py` (the render line only), `harness/db/models.py`, `harness/db/schema.py`,
`migrations/versions/0007_phase6b_execution.py`, `tests/test_rescore.py`. *Depends on:* 1.7.
*Expected result, computed independently:* a seeded world of four orders - one whose recorded fill the repaired simulator reproduces, one whose fill
it removes (the equal-timestamp case), one whose tape has a gap, one whose read is cancelled - gives verdicts `validated`, `corrected`,
`unverifiable`, `unverifiable`, an unchanged `fills` table (row count, `sum(contracts)`, `max(id)` identical before and after), a partition summing to
the denominator 4, and two policy rows per order whose pair is equal whenever `cancels_ahead = 0` (the implication only; the disagreement rate over
the range is the reported band). A second run with `--resume` writes nothing.

### 1.9 `harness capsule --out -` (carried from the 6A ledger, final review M2)
`write_capsule(..., out="-")` streams a tar of the capsule on `sys.stdout.buffer` (`capsule.py:463-486`) and is exercised only by hand today. The test
captures that buffer, un-tars it, and asserts the members equal a directory-written capsule byte for byte, `manifest.json` last, with no log or
progress text in the stream.
*Files:* `tests/test_capsule.py`. *Depends on:* nothing.
*Expected result, computed independently:* a capsule of *n* slices produces *n* + 1 tar members (`<table>.jsonl.gz` plus `manifest.json`), each
member's sha256 equal to the manifest's entry and its bytes equal to the directory path's, because both go through the same `_jsonl_gz`.

### 1.11 Amendment 6 and the re-scoring documents (experiment review CR-1, IM-8)
Appends **Amendment 6** to `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` with amendment protocol item 1's five fields: the date,
deploy sha and time (`<sha>`/`<time>` placeholders filled in the deploy commit as Amendment 5's were); the numeric order-id and run-id ranges
affected; the report tables and gate criteria touched - criterion 1 explicitly, through the clean-book share, and criteria 2-7 through the fill-event
population; the re-scoring command; and which range the next report excludes or re-scores. In the same change, `Correction.rescore_command`'s
docstring, the correction record's Protocol paragraph and item 1's command template gain `harness rescore` beside `harness replay` (§0.12). A parity
test extends `tests/test_corrections.py`: the ids in `CORRECTIONS` equal the `## C<n>` headings in the record **and** Amendment 6 names the same id
set.
*Files:* `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`, `docs/superpowers/reviews/2026-09-11-correction-manifest.md`,
`harness/corrections.py`, `tests/test_corrections.py`.
*Depends on:* 1.1-1.8 (the correction set must be final).
*Expected result:* the parity test fails if an id appears in one of the three records and not the other two.

## 2. Data

Additive only, declared where this repo's schema machinery looks. The three new tables are **models**, so `Base.metadata.create_all` (`schema.py:711`)
creates them and `drop_schema`'s metadata-generated list drops them; their indexes go on `__table_args__` or in `_INDEX_DDL` (`schema.py:133`); the
new `orders` columns go in `_COLUMN_DDL` (`schema.py:93`) as `add column if not exists`; `migrations/versions/0007_phase6b_execution.py` carries the
same statements; `tests/test_alembic.py`'s catalogue diff is the check. There is **no view**: `order_dirty_time` is a parameterised query in
`harness/execution/dirty_time.py` bounded to `id > :boundary_order_id` with a `limit`, expected to return inside 2 s over the ~8,800-order table and
abandoned under §4.4's rule if it does not.

| Addition | Shape | Invariant query (must return 0) |
|---|---|---|
| `orders.print_unmatched`, `.pending_unmatched`, `.pending_surplus`, `.cancels_ahead` and the four `nw_` twins | `numeric(14,2)`, nullable, no default | `select count(*) from orders where replay = false and id <= :boundary_order_id and (print_unmatched is not null or pending_unmatched is not null or pending_surplus is not null or cancels_ahead is not null or nw_print_unmatched is not null or nw_pending_unmatched is not null or nw_pending_surplus is not null or nw_cancels_ahead is not null or nw_dirty_seconds is not null)` - no pre-6B row is backfilled |
| `orders.recon_state`, `.nw_recon_state` | `jsonb`, nullable: buckets, trade ids, `print_floor`, pruned to the horizon and the track window on every write | `select count(*) from orders where recon_state is not null and (pending_unmatched + pending_surplus) <> (select coalesce(sum((b->>2)::numeric), 0) from jsonb_array_elements(recon_state->'buckets') b)` - the scalars are the surviving buckets' sums |
| `orders.traded_at_price`, `.nw_traded_at_price` after the boundary | unchanged columns, left **null** by the repaired writer | `select count(*) from orders where replay = false and id > :boundary_order_id and (traded_at_price is not null or nw_traded_at_price is not null)` - the C0 quantity is never written again |
| `orders.nw_dirty_seconds` | `integer`, nullable, no default | `select count(*) from orders where nw_dirty_seconds < 0` |
| `orders.nw_next_attempt_at`, `.nw_attempts` | `timestamptz` and `integer`, nullable | `select count(*) from orders where nw_done = true and nw_next_attempt_at is not null` - a finished track carries no pending retry |
| `market_dirty_intervals` (model) | `id`, `venue_market_id`, `ticker`, `started_at`, `ended_at` null, `cause varchar(20)`, `replay`; index `(venue_market_id, started_at)` | `select count(*) from market_dirty_intervals where ended_at < started_at` and `select count(*) from (select venue_market_id from market_dirty_intervals where ended_at is null and replay = false group by 1 having count(*) > 1) x` |
| `market_observation_intervals` (model) | the same shape without `cause` | `select count(*) from market_observation_intervals i where i.ended_at is null and not exists (select 1 from orders o where o.venue_market_id = i.venue_market_id and (o.status in ('open','partially_filled') or o.nw_done = false))` - no open row whose market has no working order |
| `order_rescores` (model) | `order_id`, `correction_ids`, `cancel_policy`, the measured columns, `verdict`, `computed_at`, `build_sha`; primary key `(order_id, correction_ids, cancel_policy)` | `select count(*) from order_rescores r left join orders o on o.id = r.order_id where o.id is null or r.verdict not in ('validated','corrected','unverifiable')` |

## 3. Verification (the plan's last task extends `verify.md`)

The boundary values (`:boundary_order_id`, `:boundary_fill_id`, `:boundary_event_id`, `:boundary_ledger_id` and the pre-deploy sums) are read and
journaled by the controller immediately before the deploy and, per §0.1, written into C1-C6's numeric range fields at merge time.

1. **Originals intact.** `select count(*), sum(contracts), max(id) from fills where replay = false and id <= :boundary_fill_id` equals the journaled
   triple; so do `orders`' `sum(filled_contracts)`, `sum(dirty_seconds)`, `sum(traded_at_price)`, `sum(nw_traded_at_price)` and `sum(queue_remaining)`
   at `id <= :boundary_order_id`, and `ledger`'s `count(*)`, `sum(contracts)` and `max(id)` at `id <= :boundary_ledger_id`. *Every verify, any hour.*
2. **No post-expiry fill.** `select count(*) from fills f join orders o on o.id = f.order_id where f.replay = false and f.id > :boundary_fill_id and
   o.expiry is not null and f.filled_at > o.expiry` = 0, unfiltered by `fill_method` so it stays a real test of §1.4's entry-cross clamp. *Judged from
   the first game window after the deploy.*
3. **No placement from a rejected target.** `select count(*) from order_events e where e.kind = 'place' and e.id > :boundary_event_id and (select
   s.kind || ':' || coalesce(s.reason, '') from order_events s where s.intent_id = e.intent_id and s.id < e.id order by s.id desc limit 1) =
   'skipped:signal_rejected'` = 0 - narrowed to intents whose **newest** prior event is the rejection skip, so a key whose verdict lawfully flips back
   is not flagged - with the `skipped/signal_rejected` count above 0 by the first game window.
4. **Liquidity conservation.** For ten post-deploy orders with a `queue_model` fill: `filled_contracts <= sum(count)` over hitting prints at or
   through the order's price inside its resting interval, from `venue_trades` by `(ticker, ts)`; and `select count(*) from fills where replay = false
   and id > :boundary_fill_id and fill_method = 'queue_model' and has_print = false` = 0. *Game days; deferred when the sample is empty.*
5. **Dirty scope and the backoff.** `select count(*) from orders where replay = false and id > :boundary_order_id and status in
   ('cancelled','expired') and dirty_seconds > extract(epoch from (coalesce(cancelled_at, expiry) - placed_at))` = 0, true by construction under
   §0.9's clamp; and `select count(*) from orders where replay = false and nw_done = false and nw_next_attempt_at < now() - interval '3600 seconds'` =
   0, the interval being `NW_RETRY_MAX_S` itself, so the cadence verifies itself. *Every verify.* At 01:00-08:00 CT neither interval table has an open
   row older than two hours; inside a game window open rows are expected and journaled beside `exec.dirty_markets`.
6. **Manifest and amendment.** `harness manifest` prints `"manifest_version": 7`, `"measurement_version": "4.5"`, seven corrections `C0`-`C6` with
   each of C1-C6 carrying a **non-empty** `config_hashes` tuple and a numeric `affected_order_id_range`, and the order 157 verdict; Amendment 6 exists
   in the pre-registration record with the same id set. *After the 6B deploy.*
7. **Regressions.** `make test`'s summary line reports **0 xfailed** from `tests/test_execution_regressions.py` and zero `XPASS`; the two passing
   guards and §1.1-§1.5's new cases are green. *Every verify after the deploy* (this replaces the 6A row's "exactly 6 xfailed").
8. **Gate untouched.** the newest `gate_reports` row's `criteria_hash` is still `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5` and
   `select count(*) from gate_reports where criteria_json ? 'eligibility'` is still 0. *Every verify.*
9. **Re-scores outside every criterion.** A test asserts that no `gate.py` criterion constant names `order_rescores`, and `criteria_hash` is unchanged
   (row 8). *Every verify.* An estimate reaching a criterion is an integrity anomaly.
10. **Mixed-population disclosure.** `render_gate`'s output carries the correction ids in force and the sentence naming the mixed population, until
    §0.13a is answered. Checked in `make test` and read once after the deploy.
11. **Counterfactual pending count.** `exec.nw_pending` and `select count(*) from orders where replay = false and nw_done = false and expiry < now()`
    are journaled beside criterion 4's `n_obs` in every report, so the retry backlog is visible and cannot silently become an exclusion. *Every
    verify.*
12. **No pending track counted as complete.** No report cell counts an `nw_done = false` order as a completed counterfactual: the 6C separation, the
    funnel denominators and §1.8's re-score all filter on `nw_done`, asserted by a test over the report builders. *Every verify.*

The deploy is judged on rows 1, 2, 3, 5 and 7 with the executor's own health: `exec.loop_ms` p95 no worse than the pre-deploy hour it is compared
    against, and `exec.open_orders`, `exec.nw_pending` and the `exec.skipped` reasons journaled before and after, because §1.4's rejection skip
    re-attributes reasons and §1.1's repair changes how many orders rest.

## 4. Ops

- 4.1 Target: the **full** recipe (`make deploy-nas`, fix 37's reviewed version), because the diff touches `harness/db/models.py` (§0.15). `app-run`,
  `app-serve`, `app-exec` and `app-research` carry the changed code; `app-ws` is rebuilt although no recorder file changes. `init-db` applies the
  additive DDL and migration `0007_phase6b_execution` carries the same statements.
- 4.2 Window: R4 in full (no deploy while a matched game is `in_progress`, within 4 h after any kickoff, within 15 min before one, or 60-100 min
  before an NFL kickoff); journal 128's app-only allowance does not apply.
- 4.3 Rollback: previous sha plus `make deploy-nas`. The additive columns and tables stay - no DROP is ever part of a rollback (invariant 5) - and a
  rolled-back build ignores them, with the correction record saying which build produced which rows.
- 4.4 No new secret, host, container or cron. The re-score, the audit and `order_dirty_time` are read-mostly commands the controller runs over ssh in
  the quiet window (01:00-08:00 CT), one at a time, abandoned if `exec.loop_ms` exceeds 30 s during one; the re-score is resumable, so an abandoned
  run costs only the orders it had not reached.
- 4.5 Expected load change: §1.4 and §1.5 shorten what the loop simulates, and §0.14's backoff removes only the repeated reads of unreadable tickers -
  23 cancelled reads in 40 minutes on the sampled evening - while carrying every track. It is not a performance fix for the healthy population: 5,384
  of the 7,998 carried tracks are pre-kickoff and legitimately open. The before and after `exec.loop_ms` and `exec.nw_pending` numbers are journaled,
  not promised.

## 5. Testing

- **Fixtures.** Realistic mixed-market fixtures come from the 6A capsules (`clean`, `interleaved`, `gap_recovery`, `delayed_loop`, `capacity_bound`,
  plus order 157's) through a reader over the gzipped JSON-lines files, with `tests/fixtures/tape_sample_lou_miss_2026-09-07T03.json` kept as the
  premise check. A capsule not yet extracted is not a blocker: each case has a synthetic twin from `tests/test_fills.py`'s helpers. No test skips.
- **Capsule-backed expectations.** Every capsule-backed case names which of two routes it takes, and the plan task text repeats the requirement: (a) a
  committed independent derivation beside it - a small script over the capsule's rows computing the expected queue and fills **without importing
  `harness.execution.fills`** - or (b) conservation-only assertions independent of the implementation: fills ≤ hitting print volume at or through our
  price inside the interval, queue monotone non-increasing, and identity across one-call, twenty-chunk and persisted-boundary feeds. Freezing the new
  code's own output is not an expectation.
- **The genuine gap/reconnect case** is the `gap_recovery` capsule: a real `gap` row with its `sid` and `ts`, the resubscribe, the fresh snapshot and
  the deltas either side, proving §1.1 (the interleaving before the gap does not dirty the book, the reconnect does) and §1.2 (the snapshot anchors
  cursor and floor together) on one slice. **A recovery containing an older trade** is §1.2's case 3 plus its capsule twin: a print stamped inside the
  gap window arriving after that snapshot.
- **Batch and restart consistency under one observation policy:** the same event history fed as one call, as twenty chunks, and across a persisted
  boundary (`_state_columns` written and re-read through `_state_of`, now in `harness/execution/state.py`) yields identical fills, queue, buckets and
  trade-id set - `test_chunking_invariance` extended to the ledger state.
- **Independently calculated expectations.** Every case's docstring states the expected queue and ledger result and how it was derived from the tape;
  a test that re-runs the code's own arithmetic is rejected at review (ROADMAP §6B).
- **Acceptance mapping.** ROADMAP.md §6B's acceptance clauses are proven at: mixed-market fixtures §5 and §1.3; gap/reconnect §1.1, §1.2; recovery
  with an older trade §1.2; liquidity conservation §3 row 4 and §1.3's property test; no post-expiry fills §1.4 (walk and entry cross), §3 row 2; no
  placement from rejected targets §1.4, §3 row 3; batch/restart consistency §5; independently calculated queue/ledger results every component's
  *Expected result* line plus the capsule rule above; timing-policy differences §1.6; order 157 §1.7; no-watcher re-scored only after the repairs
  §1.8; raw cleanliness retained §0.9, §0.10, §2 and §3 row 1.

## 6. Out of scope

6C (week keys, gate documentation, confirmation floor, exact-contract joins, funnel units - which §1.4's reason re-attribution is an input to, not a
  substitute for - and the annotation backlog); 6D (scheduled-versus-completed instrumentation, budget isolation, the holding and capacity policy
  comparison including any change to `max_open_orders`, the stale allowance, rest-to-expiry or admission rules, **closing the `nw_done = false`
  backlog early**, stepping a replay at recorded loop instants, and the key-level recomputation of 27/445); 6E (inventory, restore rehearsal,
  benchmark, host choice, cutover); 6F (the version boundary and the prospective period; §0.13a's boundary is a measurement boundary only); anything
  live, any venue write, any real money. The carve-out that makes the 6D line exact: **6B never closes a counterfactual track; it changes only how
  often an unreadable one is retried** (§0.14). No new variant: the registered ids are frozen, nothing under `harness/variants/` is touched, and
  anything new after Mon 2026-09-21 09:00 CT would be exploratory. No change to any gate criterion, threshold, family, grid, success threshold or
  cut-off (R1); no switching on of the eligibility settings (§0.13a); no change to the recorder, the kill switch, the dashboard token, spend caps or
  outbound hosts.

## 7. Conformance (autopilot plan-next 1a)

1. **Components.** §1.1-§1.6 implement roadmap decision 2's six bullets in their order; §1.7 the order 157 audit, §1.8 the no-watcher re-score, §1.9
   the carried `--out -` test, §1.10 the state-helper extraction (execution review M-5) and §1.11 Amendment 6 (experiment review CR-1). Nothing else.
2. **Dependencies.** None new: standard library (`tarfile`, `json`), SQLAlchemy, Typer and pytest as pinned; `pyproject.toml` and `constraints.txt`
   untouched.
3. **Pre-registered ids.** Untouched, as are `MAX_PRIMARY`, `MAX_SECONDARY` and `harness/variants/`. `EXECUTOR_VERSION` moves 4.4 → 4.5 once
   (`harness/execution/__init__.py:4`), changing `config_hash` (`plan.py:116-124`) for orders placed afterwards, which is the measurement boundary
   C1-C6 record (§0.1); `tests/test_fills.py:62` moves with it.
4. **Schema. PASS** - additive only and declared where this repo looks: `market_dirty_intervals`, `market_observation_intervals` and `order_rescores`
   are models, so `create_all` (`schema.py:711`) creates them and `drop_schema` (`schema.py:735`) drops them with the metadata list; indexes on
   `__table_args__` or in `_INDEX_DDL` (`schema.py:133`); the `orders` columns `add column if not exists` in `_COLUMN_DDL` (`schema.py:93`); no view,
   because `order_dirty_time` is a parameterised query (§2, §1.5); migration `0007_phase6b_execution` carries the same statements and
   `tests/test_alembic.py` is the check. No DROP, RENAME, TRUNCATE, DELETE or backfill.
5. **Venue writes.** None; no venue client is opened, the gateway stays `PaperGateway`, the refusal tests are unchanged.
6. **Money.** None; no metered call, no Anthropic call, no Odds API credit.
7. **Secrets.** None; no component reads `secrets/`.
8. **Ops.** §4: full deploy recipe under R4, rollback to the previous sha, no new secret, host, container or cron.
9. **Verification. PASS** - §3 carries an invariant query per new table and column family (§2's right-hand column), expected values by time of day on
   rows 2, 4 and 5, and the rows the reviews found missing: row 1 now covers `ledger`, `traded_at_price`, `nw_traded_at_price` and `queue_remaining`;
   row 9 checks `order_rescores` reaches no criterion; row 10 the mixed-population disclosure; row 12 that no pending counterfactual is counted
   complete. Row 2 measures what it claims now that §1.4 clamps the entry cross, row 3 is narrowed to a newest-event test, and row 5's two clauses
   hold by construction under §0.9's clamp and `NW_RETRY_MAX_S`.
10. **Decisions taken on the user's behalf.** §8, each with source, rationale, cost if wrong, blast radius and exact reversal, including D13 for the
    cleanliness-interval adoption and D14 for the skip-chain re-attribution.
11. **Out of scope** matches the roadmap's milestone boundaries (§6), with the 6D carve-out stated explicitly.
12. **Files and Depends on. PASS** - every component carries both lines and no component listed as an independent start shares a file with another.
    §1.10 runs first and takes `_state_of`/`_state_columns` out of `loop.py`; then §1.1 and §1.3 in parallel (`book.py`; `fills.py` plus `state.py`),
    then §1.2 (after §1.3), §1.4 (after §1.2), §1.5 (after §1.1, §1.4), §1.6 and §1.7 (after §1.1-§1.5), §1.8 (after §1.7), §1.11 (after §1.1-§1.8),
    with §1.9 independent throughout and `verify.md` last. §1.1, §1.2, §1.4 and §1.5 still share `loop.py` and remain serialized.

## 8. Decisions taken on the user's behalf

| # | Decision | Source | Rationale | Cost if wrong | Blast radius | Reversal |
|---|---|---|---|---|---|---|
| D1 | Continuity is read from sid-level `gap` rows plus a consumer-side probe; the per-book `seq` check is deleted | pre-loaded (bullet 1), review I-1 | the recorder's verdict covers what the sink received, the probe what the executor read back; the deleted check covered neither cleanly | a delta dropped by the `ts` floor goes unnoticed if the probe is wrong, leaving a book silently stale | file, one bounded query per advance | restore the per-object check |
| D2 | A book whose immutable `anchor_as_of` predates the newest `ws_connect` is dirty until it re-anchors | model (§0.3), review C-1 | reconnects are frequent (370 disconnects, 297 connects) and `as_of` moves with every delta, so the test must be on the anchor | briefly more dirty markets right after a reconnect | file, NAS behaviour | drop the check |
| D3 | The point estimate keeps "unmatched decrement rests ahead of us"; the other end is reported as a band | pre-loaded ("report sensitivity where ownership is unknowable") | it is the coded and documented model; changing the point estimate would be a model change dressed as a repair | the point estimate stays optimistic about cancellations; the band says by how much | file, DB additive | switch `cancel_policy` default to `behind` |
| D4 | The sensitivity walk runs offline only; the loop records `cancels_ahead` | model (loop p95 227 s, 7,998 tracks) | a second full walk per order per loop on an IO-starved box costs more than the number is worth | a sensitivity figure needs a re-score run rather than a column read | file, DB additive | compute both walks in the loop |
| D5 | Print idempotence is a `print_floor` plus a capped `trade_id` set bounded by the track's window | model, reviews C-2 and IM-12 | the floor makes a re-anchor sound and the set makes a late REST backfill usable; one cannot do both jobs | an id set at its cap raises the floor, so a print below it is skipped rather than double-applied | file, DB additive | revert to the timestamp watermark |
| D6 | Dirtiness and observation coverage are recorded on the market; per-order time is derived at read time | model, reviews I-4 and IM-15 | 136 dirty markets against 7,998 orders, and an interval is a measurement where an accrual is a running total | one more query to run when explaining a number | DB additive | keep only the nominal per-order counters |
| D7 | `orders.dirty_seconds` keeps its nominal accrual and the gate keeps reading it; no criterion reads anything new | pre-loaded (R1), review CR-2 | the criterion's SQL, threshold and hash must not move, and elapsed accrual would move the 60-second classification boundary | the cumulative gate reads a mixed population until §0.13a is answered, which row 10 makes every render say | file | none needed |
| D8 | Corrected results are `order_rescores` rows, not `replay = true` orders | model, review IM-8 | keeps the link to the original order and the estimate out of the gate; §1.11 updates the three standing documents to match | one more table; a full replay is still available for a range-scoped correction | DB additive | drop the table, use `harness replay` |
| D9 | The order 157 verdict lives in `harness/corrections.py` as well as in an undated record | model (6A D4), review MI-4 | `docs/` is absent inside the container and 6C's t13 must read the status; the run date is the controller's | two places to update | file | keep only the record |
| D10 | One `EXECUTOR_VERSION` bump (4.5) for the whole milestone | model | the milestone deploys once, so one boundary and one new `config_hash` set is what the record needs | a later correction inside 6B shares the boundary | file | bump again before the deploy |
| D11 | C1-C6's `config_hashes` and numeric id ranges are filled by the controller at merge time, as C0's were | pre-loaded (6A's pattern), review IM-7 | the post-deploy values do not exist until the deploy; the tuples ship empty with width-only tests and row 6 checks they are filled | the record's numbers arrive in the controller's own commit | file | none |
| D12 | A counterfactual whose tape read fails is retried on an exponential backoff capped at `NW_RETRY_MAX_S` = 3600 elapsed seconds, evaluated before `_tape`, and is **never** closed | model, reviews CR-4, CR-5, C-5, C-6 | the load comes from re-reading unreadable tickers every loop, not from the tracks existing; closing them would remove their orders from criterion 4's population, non-randomly and on the worst-taped tickers; a loop-counted bound would stretch to about nine hours at the 27-loops-per-hour rate measured on the sampled evening | a starved host carries pending tracks longer, visible in `exec.nw_pending` and §3 rows 11-12, rather than a smaller biased population | file, DB additive (two columns) | raise or lower the cap |
| D13 | The watched resting interval `[placed_at, min(cancelled_at, expiry)]` is adopted as criterion 1's observation interval; elapsed accrual is **not** adopted | model for the scope repair; §0.13b and §0.13c put both alternatives to the user | removing the counterfactual's accrual restores the column to its documented intent, while changing its units would move the 60-second classification in both directions | the clean-book share of every post-boundary order moves as the scope repair intends; a narrower interval is supplied by the derivation with no new code | gate criterion 1 on post-boundary orders | answer §0.13b or §0.13c; §1.5 already reports all four interval readings |
| D14 | The rejected-verdict skip is placed after the data-quality tests and before the capacity tests | model, review IM-1 | a rejected verdict is the strategy's own current answer, so it should neither masquerade as a data skip nor consume capacity | funnel denominators shift by the rejected count, which C4 records and 6C's funnel task reads | file, report denominators | move the test to the end of the chain |
| D15 | The Monday 2 % replay parity verdict is suspended while the timing policies differ | pre-loaded (ROADMAP §6B "recorded rather than hidden"), review IM-6 | a grid stepping 15 s against a live loop that ran 27 loops in an hour is a different number of observation opportunities, so a 2 % pass/fail would be uninterpretable | no parity verdict until 6D's instrumentation; the step counts and divergence are still published | operator duty text | restore the verdict once the policies match |

## 9. Rulings

Both design reviews, with the controller's ruling on every finding, quoted verbatim from
`.superpowers/sdd/plan-next-phase6b/rulings-exp.md` (experiment and measurement lens, 2026-09-11 20:15 CT) and
`.superpowers/sdd/plan-next-phase6b/rulings-exec.md` (execution and queue-model lens, 20:20 CT, with its addendum after
the reviewer's revision-1a update at 20:22 CT). Where the two touch one subject the execution file's reconciliation
governs, which is why there is no per-loop elapsed accrual, no `dirty_seconds_elapsed` or `last_observed_at` column,
and no tail close.

### Experiment and measurement lens (5 Critical, 15 Important, 5 Minor)

- CR-1 Ruling: accepted - the pre-registration record's amendment protocol item 1 and the Amendment 5 precedent make C1-C6 a measurement amendment; add a component and a plan task that appends **Amendment 6** with the five fields, its `<sha>/<time>` placeholders filled in the deploy commit the way Amendment 5's are; state in §0.1 that C1-C6 collectively are Amendment 6; add a parity test between `harness/corrections.py` and the record's heading - cost if wrong: one extra docs task.
- CR-2 Ruling: accepted, option (a) - `dirty_minutes` is integer division of accrued seconds so the 60 s floor makes elapsed accrual a classification change under R1's "which resting interval counts"; the gate-read columns (`orders.dirty_seconds`, `dirty_minutes`) keep nominal accrual until a dated user decision; elapsed truth is recorded in `market_dirty_intervals` and a new nullable `orders.dirty_seconds_elapsed`; §0.10 is corrected (quantify both directions against the live p95); §0.13 gains question (c): adopt elapsed accrual on the gate-read column, yes or no, and from which boundary - cost if wrong: one extra column carried until the decision; the gate's documented measurement stays intact meanwhile.
- CR-3 Ruling: accepted with a stricter shape - the reconciliation's three ledger terms get new columns (names chosen by the author, e.g. `print_unmatched`, `pending_surplus`, `cancels_ahead` and their `nw_` twins) and post-boundary orders leave `traded_at_price` / `nw_traded_at_price` null rather than reusing them, so the boundary is visible by nullness; §3 row 1 adds `sum(traded_at_price)`, `sum(nw_traded_at_price)`, `sum(queue_remaining)` at `id <= :boundary_order_id`; C3's `excluded_measurements` states the non-comparability; §1.7 reads 157's 63.92 under C0 only - cost if wrong: three more nullable columns.
- CR-4 (revised: the tail rule changes criterion 4's population) Ruling: accepted, with a different shape - the tail rule is replaced by a **backoff**, not a close: a counterfactual track whose ticker's tape read failed is retried with an exponential delay (capped, `NW_RETRY_MAX_SECONDS`, elapsed wall time) instead of every loop, so the load falls without any track being abandoned; a track closes only when the tape it needs no longer exists (partition dropped by retention), which is `unverifiable` in truth and destroys nothing; `nw_tail` becomes `nw_next_attempt_at` / `nw_attempts` bookkeeping. Criterion 4's population is then untouched and no R1 question arises; §0.14, §1.5, §2, row 5 and D12 are rewritten accordingly; keep the §3 row that journals the pending-track count beside criterion 4's `n_obs` (as `exec.nw_pending` and the count of tracks past expiry) - cost if wrong: a starved host carries a long tail of pending tracks (visible in the metric and the row) rather than a smaller, biased population.
- CR-5 Ruling: accepted - every bound in this design is elapsed wall time, never a loop count (`NW_RETRY_MAX_SECONDS`, and the loop count only as a secondary attempt floor if wanted); §3 row 5 is restated so the rule and its verification agree by construction (no track with `nw_next_attempt_at` older than now minus the cap); D12 restated - cost if wrong: none.
- IM-1 Ruling: accepted - state the insertion point after the data-quality tests (`_fair_stale`, `market.dirty`) and before the capacity tests, so a rejected verdict never masquerades as a data skip and never consumes capacity; record the reason re-attribution in C4 and name 6C's funnel-units block as a reader - cost if wrong: a funnel denominator shifts by the rejected count, which is itself reported.
- IM-2 Ruling: accepted, second option - drop the "C0 code path" leg; the audit compares the repaired simulation against the recorded quantities, which the three hypotheses already predict against - cost if wrong: no frozen C0 reference; the 6A capsule plus C0's recorded values are the reference.
- IM-3 Ruling: accepted - `unverifiable` also covers "differs and no hypothesis's expected counts are met"; fourth fixture added - cost if wrong: none.
- IM-4 Ruling: accepted - §1.8 states the re-scored quantity's unit and denominator (orders, per cancel policy, right-censoring caveat attached) and the reports stop quoting 27/445 beside it; the key-level figure is not recomputed in 6B (it was the defective simulator's artefact) - cost if wrong: a reader wanting the key-level comparison waits for 6D's coverage contract.
- IM-5 Ruling: accepted - resolve the variant set from the executor configuration in force during the replayed range (`config_history` by the range's runs) and refuse a range spanning a change of the executed set (exit code, no partial output) - cost if wrong: a replay over a boundary must be split by hand.
- IM-6 Ruling: accepted, first option - suspend the 2 % parity verdict while the timing policies differ; the Monday duty reports both step counts and the divergence; stepping the replay at recorded loop instants is deferred to 6D's instrumentation - cost if wrong: no parity pass/fail until 6D; the numbers are still published.
- IM-7 Ruling: accepted - numeric `affected_order_id_range` / `affected_run_id_range` filled at merge time (the D11 pattern), `boundary_fill_id`, `boundary_event_id`, `boundary_ledger_id` in the record prose - cost if wrong: none.
- IM-8 Ruling: accepted - update the `rescore_command` docstring, the record's Protocol paragraph and pre-registration item 1's template in the same change; `harness rescore --correction <id>` for order-scoped corrections, `replay = true` rows for range-scoped ones - cost if wrong: none.
- IM-9 Ruling: accepted - §0.13a states the 6B boundary is a measurement boundary, not 6F's prospective-period boundary, and offers the third option (leave dormant until 6F) as the default the loop recommends - cost if wrong: the user picks another option.
- IM-10 Ruling: accepted, second option - add a §3 row asserting `render_gate` output carries the correction ids in force and the mixed-population sentence; the component that owns the sentence is §1.8 (report side), not 6C - cost if wrong: one verify row.
- IM-11 Ruling: accepted, drop `recovery` as a cause label; four causes - cost if wrong: none.
- IM-12 Ruling: accepted - bound the trade_id set by the track's own window (`placed_at - PRINT_LOOKBACK` to the deadline), persisted per order (a bounded set, capped, with the cap stated); §0.6 and D5 say so; the execution review's finding on the same point is folded in - cost if wrong: memory per open order, bounded by the cap.
- IM-13 Ruling: accepted - first accrual anchored at `placed_at`; the per-order previous-observation instant is persisted (`orders.last_observed_at`), not inferred from the loop clock - cost if wrong: one column.
- IM-14 Ruling: accepted - the view (`order_dirty_time`) is authoritative for the record and the re-score; the accrued column is a running total; add the invariant that column and view agree for tracks with `nw_done = true` (there is no `nw_tail` close under the CR-4 ruling) - cost if wrong: one invariant row.
- IM-15 Ruling: accepted - record observation coverage per market (`market_observation_intervals`: one row per contiguous stretch the executor stepped the market, additive table with its own invariant query), and `order_dirty_time` reports `unobserved_s` beside `counterfactual_dirty_s`; unobserved time is never folded into clean time - cost if wrong: one more small table.
- IM-4 (revised, three separations) Ruling: as above, plus §1.8 states the denominator and the two unverifiable classes explicitly so the two numbers cannot be read as a trend; 27/445 is quoted nowhere except the refusal sentence - cost if wrong: none.
- MI-1..MI-5 Ruling: accepted as written (all six columns in the invariant; ledger in row 1; drop the comment appeal; undated audit path `docs/superpowers/reviews/order-157-audit.md`; non-empty `config_hashes` in row 6).
- Conformance 9 and 10 FAIL Ruling: accepted - §3 gains the two rows (rescores outside every criterion; ledger intact) and §8 gains the D-row for the cleanliness-interval adoption, now reframed by CR-2 as "nominal kept, elapsed recorded, decision (c) pending".

### Execution and queue-model lens (6 Critical, 17 Important, 7 Minor, including the revision-1a addendum)

- C-1 Ruling: accepted - `BookState.anchor_as_of` immutable, carried by `copy()`, untouched by `apply_delta`; the boundary test compares it with the newest `ws_connect` - cost if wrong: one field.
- C-2 Ruling: accepted - an explicit `print_floor` (the anchor instant, with I-3's slack) enforced in `_merge_events`, separate from the `trade_id` dedup set - cost if wrong: one more piece of per-track state.
- C-3 Ruling: accepted - the decrement accumulators are bucketed by timestamp within `store.PRINT_LOOKBACK` and aged buckets retire into `cancels_ahead`; the persisted shape is the author's choice (a bounded jsonb column per order, or reconstruction from the tape at restart), stated in §2 with its restart behaviour, and §1.3's expected results gain the reviewer's case (queue 2, cancel 2, later trade 3 → fill 3) - cost if wrong: one column or one restart re-read.
- C-4 Ruling: accepted - the entry cross is taken only when `working.as_of <= deadline`; §3 row 2 keeps its zero expectation - cost if wrong: none.
- I-1 Ruling: accepted - the consumer-side probe (`... id > :cursor and ts < :lower limit 1` on `ix_obe_ticker_id`) marks the book dirty; D1's cost-if-wrong restated - cost if wrong: one bounded query per advance.
- I-2 Ruling: accepted - keep the `min()` clamp; add the resting-9-against-5 case - cost if wrong: none.
- I-3 Ruling: accepted - print floor at `anchor_as_of - DELTA_LOOKBACK`; §1.2 names each side's clock; a print inside the slack is reconciled by the §1.3 ledger - cost if wrong: none.
- I-4 Ruling: accepted, second option, reconciled with rulings-exp CR-2/IM-13/IM-14 - there is **no** per-loop elapsed accrual and no `dirty_seconds_elapsed` or `last_observed_at` column: the gate-read columns keep today's nominal accrual (the documented measurement, until the user's decision (c)); elapsed dirty time is derived at read time from `market_dirty_intervals` and the observation-coverage intervals (rulings-exp IM-15) by a bounded parameterised query (I-8), which is the single elapsed mechanism and the quantity the correction record quotes; IM-14's relationship statement becomes "nominal column and elapsed derivation are different quantities by design, both reported" and its invariant is dropped - cost if wrong: the elapsed figure is computed, never stored.
- I-5 Ruling: accepted - `BookState.dirty_cause` set at every site that sets `dirty`; causes `gap`, `session_boundary`, `recorder_dead`, `event_age`, `malformed_row` (no `recovery`, per rulings-exp IM-11) - cost if wrong: none.
- I-6 Ruling: accepted - close every open interval for a market absent from the step's market set; the derivation clamps a still-open row to `least(now, deadline)`; invariant: no open row whose market has no working order - cost if wrong: one invariant row.
- I-7 Ruling: accepted - the two tables are models (`create_all` and `drop_schema` see them), the index on `__table_args__`, the view in `_VIEW_DDL` and `drop_schema`; §2 corrected; `tests/test_alembic.py` is the check - cost if wrong: none.
- I-8 Ruling: accepted - no bare view: `order_dirty_time(boundary_order_id, limit)` is a parameterised query (a Python function over a bounded SQL text) run over `id > :boundary_order_id` only, with its expected runtime stated; the GiST option is not taken - cost if wrong: readers call a function instead of selecting a view.
- I-9 Ruling: accepted, first option - §3 row 3 narrows to intents whose newest `order_events` row is the `signal_rejected` skip - cost if wrong: none.
- I-10 Ruling: accepted - implication only; the disagreement rate is the band - cost if wrong: none.
- I-11 Ruling: accepted - `harness rescore` resumable on the primary key, per-order tape read bounded by `DELTA_BATCH_LIMIT` and its own `statement_timeout`, cancelled read → `unverifiable`, `--limit`/`--resume` in the command and in C1-C6's `rescore_command` - cost if wrong: none.
- I-12 Ruling: accepted, same as rulings-exp IM-5/IM-6 - population resolved from the range's `config_history`, both lists recorded, a duty whose live side straddles the deploy boundary is refused, correction ids on both sides - cost if wrong: none.
- I-13 Ruling: accepted - every capsule-backed fixture names (a) a committed independent derivation that does not import `harness.execution.fills`, or (b) conservation-only assertions; the plan's task text repeats the requirement - cost if wrong: none.
- I-14 Ruling: accepted - `book_at` unchanged; `_sim_book` calls `load_book_at` - cost if wrong: none.
- M-1..M-4, M-6 Ruling: accepted as written.
- M-5 Ruling: accepted, split - a first small component moves `_state_of`/`_state_columns` out of `loop.py` (a pure refactor with no behaviour change, its own test), so §1.3 stays an independent start; Conformance 12's wave order is re-derived - cost if wrong: one extra small task.
- Conformance 4, 9, 12 FAIL Ruling: accepted; resolved by I-7, I-8 plus the new §3 rows (rulings-exp), and M-5.
- C-5 and C-6 Ruling: superseded by rulings-exp CR-4/CR-5 - there is no tail close and no loop counter; the retry backoff is elapsed-time based and persisted in additive columns (`orders.nw_next_attempt_at timestamptz null`, `orders.nw_attempts integer null`), doubled per failed read from `exec_period_s` up to `NW_RETRY_MAX_S = 3600`, reset on a successful read; it is evaluated where the step chooses which tickers to read (before `_tape`), so a ticker whose next attempt is in the future is simply not read this step and the unread skip at `loop.py:559` never sees it; §3 row 5 becomes "no track with `nw_done = false` whose `nw_next_attempt_at` is older than now minus `NW_RETRY_MAX_S`" (the cadence verifies itself), plus the pending-track count beside criterion 4's `n_obs` - cost if wrong: two columns; a starved host carries pending tracks longer, visibly.
- I-15 Ruling: accepted in the nominal regime (rulings-exec I-4 leaves no elapsed accrual on the watched column) - the nominal accrual stops at `expiry` for an order held open on a lagging ticker, the same clamp §1.4 puts on the fill deadline, versioned inside C5; §3 row 5's first clause then holds by construction - cost if wrong: none.
- I-16 Ruling: accepted in the no-close shape - `nw_tail` does not exist; the distinguishing mark between a completed and a pending counterfactual is `nw_done`, and §1.5 states that every consumer of the `nw_*` columns (the re-score, 6C's actual/counterfactual separation, funnel denominators) filters or labels on `nw_done`; §3 gains the invariant that no report cell counts an `nw_done = false` order as a completed counterfactual - cost if wrong: one verify row.
- I-17 Ruling: accepted - with no close there is no contradiction; §6 keeps "closing the backlog early is 6D's" and gains the sentence "6B never closes a counterfactual track; it changes only how often an unreadable one is retried", cross-referenced from §0.14 - cost if wrong: none.
- M-7 Ruling: accepted - §0.14 and §4.5 say plainly that the backoff removes only the repeated reads of unreadable tickers (23 cancelled reads in 40 minutes tonight) and is not a performance fix for the healthy population.
- Conformance 11 carve-out Ruling: accepted (I-17's sentence).
