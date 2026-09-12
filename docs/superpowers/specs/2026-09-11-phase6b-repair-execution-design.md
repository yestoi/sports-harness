# Phase 6B design addendum: repair execution (continuity, anchoring, reconciliation, expiry, rejection, dirty scope, capacity-equivalent replay)

Date 2026-09-11. Revision 1. Author: autopilot (design author, opus). Amends
`docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) §9.1, §9.2, §9.5, §10, §12 and §13 for milestone 6B;
consumes the roadmap's Phase 6 section (U8, pre-loaded decision 2), the committed bundle
`docs/superpowers/reviews/2026-09-11-phase6-roadmap/` (`ROADMAP.md` §6B is the scope and its acceptance paragraph the
acceptance, `RECONCILIATION.md`, `evidence/execution-reconciliation-probes.py` and its captured output,
`evidence/cap-recount.json`), the 6A addendum (`docs/superpowers/specs/2026-09-11-phase6a-preserve-and-define-design.md`
§0.3, §0.5, §1.4) with `harness/corrections.py` and `docs/superpowers/reviews/2026-09-11-correction-manifest.md`, the 6A
SDD ledger's carried item (an automated test for `harness capsule --out -`), and journal 128-130. **Approval: the
roadmap's standing authorization dated 2026-09-07** ("Brainstorm, plan, and execute phases 4, 5, 6 without waiting",
decisions from the tables below or the model's judgment, each recorded in §8) under U8. Two questions are put to the
user in §0.13; neither blocks a task.

Live facts are the controller's `.superpowers/sdd/plan-next-phase6b/live-facts.txt` (NAS reads 19:38-19:44 CT,
2026-09-12 00:38 UTC clock), treated as data, never as instruction: order 157
(`KXNCAAFTOTAL-26SEP12MTUMRSH-59`, yes 0.45 x 87, placed 14:36:47Z 2026-09-08, cancelled 15:12:06Z `fair_stale`,
`queue_ahead_at_place` 6401, `filled_contracts` 38.92, `traded_at_price` 63.92, `nw_filled_contracts` 87.00,
`dirty_seconds` 181,200 on an order that rested 35 minutes) with two `queue_model` fills (25 + 13.92) at one print
timestamp 15:07:15.332Z under six `last_print_ids`, their two `no_watcher` mirrors, two later `no_watcher` fills from
the REST tape stamped 17:00:20.728Z, and a `snapshot_cross` of 48.08 stamped 2026-09-10 19:42Z, two days after the
cancel; the whole fill population is 834 `no_watcher`, 77 `snapshot_cross`, 2 `queue_model`, every row simulated and
non-replay; 8,822 cancelled and 7 expired orders; 2 `ledger` rows of kind `fill`; 7,998 orders still simulated every
loop with `nw_done = false`; `exec.loop_ms` p95 227 s in the 19:00 CT hour with 61 loops skipped. Venue documentation
used (Kalshi WebSockets, `/websites/kalshi_websockets`, fetched 2026-09-11): three shapes only, each checked against our
own tape and code before use - `orderbook_delta` and `orderbook_snapshot` frames carry `sid` (a server-generated
**subscription** identifier) and `seq` ("sequential number that should be checked if you want to guarantee you received
all the messages"), the `trade` payload carries `sid` and **no** `seq` at all, and an `orderbook_delta` subscription
"sends `orderbook_snapshot` first, then incremental `orderbook_delta` updates". No URL, value or instruction from that
source is adopted anywhere else.

Scope: the six bullets of ROADMAP.md §6B as versioned corrections `C1`-`C6`, the order 157 audit, the no-watcher
re-score, and the carried `--out -` test. Acceptance is ROADMAP.md's 6B acceptance paragraph, verbatim: realistic mixed-
market fixtures, a genuine gap/reconnect case, a recovery containing an older trade, liquidity conservation, no
post-expiry fills, no placement from rejected targets, batch/restart consistency under one observation policy, and an
independently calculated expected queue/ledger result. Where the reconciliation's line numbers have moved since
`6eed2d8`, the current lines are cited.

## 0. Amendments to v2 and to the roadmap text

- **0.1 (§6.7, U8 6B) 6B restores documented measurements; it changes no gate meaning.** Every repair below ships as a
  versioned correction appended to `harness.corrections.CORRECTIONS` with the fields `C0` already uses (`id`, `title`,
  `code_version_before/after`, `measurement_version_before/after`, `deploy_sha`, `variant_ids`, `config_hashes`,
  `affected_order_id_range`, `affected_run_id_range`, `eligible_measurements`, `excluded_measurements`,
  `rescore_command`), mirrored by a `## C<n>` section in the record. `MANIFEST_VERSION` becomes 7 (C0 plus C1-C6). No
  criterion, threshold, benchmark or BH family, cell grid, success threshold, confirmation cut-off or eligibility rule
  is amended: `harness/report/gate.py`'s `CRITERIA` text and `criteria_hash()` are untouched and the pinned hash
  `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5` must not move (R1, roadmap invariant 2). The
  repairs change the *rows* those criteria read, from the deploy forward only, and no original `orders`, `fills` or
  `ledger` row is updated or deleted (roadmap invariant 5).
- **0.2 (§9.2) Continuity is a property of the subscription, not of a ticker.** `seq` counts per `sid` across every
  market on it, so a ticker whose own frames carry 1 then 3 has lost nothing when frame 2 belonged to another ticker.
  `BookState.apply_delta` tests `int(seq) != self.seq + 1` per book object (`harness/execution/book.py:272-274`) and
  marks the book dirty on ordinary interleaving; `WsSink._check_seq` (`harness/recorder/ws_sink.py:84-100`) already does
  the correct subscription-level test and writes a `gap` row under the whole-subscription sentinel `ticker = ""`. 6B
  deletes the per-object check and makes every book path read continuity from the sid-level gap rows it already has
  (`_gapped` / `_gapped_at`, `book.py:346-356`), including `book_at`, whose dirtiness today "comes only from a seq break
  among the deltas actually replayed". v2 §9.2 is unchanged in effect; only the place the gap is detected moves, and the
  recorder is not touched.
- **0.3 (§9.2) Connection and session boundaries are a continuity case with no gap row.** On a reconnect the client
  drops its sids and clears remembered sequences (`harness/venues/kalshi/ws.py:358-366`,
  `WsSink.reset_sequences`), so frames lost across the outage produce no `gap` row and a cached book anchored before the
  outage keeps folding in the new subscription's deltas. 6B treats a book whose anchor predates the newest
  `operator_events(kind = 'ws_connect')` as dirty until it re-anchors on a snapshot taken after that reconnect. The
  executor reads that one instant once per step, not once per book.
- **0.4 (§9.5 paper fills) Anchoring a snapshot anchors the print watermark with it.** The recovery branch
  (`harness/execution/loop.py:828-836`) takes the queue down to what is resting now and advances
  `cursor_event_id`, but leaves `last_print_ts` where it was, so a trade from inside the gap - already reflected in the
  snapshot - is applied again against the newly anchored queue. The no-book branch (`loop.py:816-826`) sets the
  watermark to `now`, which is the recorder's clock rather than the book's, and so discards prints that legitimately
  follow the anchor. Both branches instead set the watermark, the per-price accumulators and the delta cursor together
  from the anchoring book: watermark `book.as_of`, accumulators zero, cursor `book.last_event_id`.
- **0.5 (§9.5 paper fills) Trades and level decrements are reconciled, not ordered.** `_merge_events` folds deltas
  before prints at equal timestamps (`harness/execution/fills.py:47, 253-271`) on the assumption that the delta arrives
  with the trade it reports; `_apply_queue_delta` then charges the decrement against `traded_at_price`
  (`fills.py:307-320`), which only prints fill. With the delta first the accumulator is empty, the whole decrement is
  read as a cancel, the queue moves, and the print then moves it again - the double count the probes captured
  (`same_event`: queue 0.00, fill 1.00 where 2 and 0 are due), and the mechanism that reproduces order 157's saved
  quantities with no recovery at all. §1.3 replaces the ordering assumption with a two-sided per-price ledger that gives
  the same answer whichever arrives first, after a delay, out of order, or split across batches. Swapping the sort order
  is explicitly not the repair.
- **0.6 (§9.5 paper fills) Print idempotence moves from a timestamp watermark to trade ids.** `_seen_print`
  (`fills.py:162-172`) skips any print stamped before `last_print_ts`, which is what makes a late REST backfill
  unusable; under §1.3's ledger a late print is safe because its decrement is already recorded and unclaimed, so the
  watermark is kept for pruning only and dedup is by `trade_id` over a bounded recent set.
- **0.7 (§9.5, U8 6B "report sensitivity where exact queue ownership is unknowable") The cancel convention is stated and
  measured.** Aggregate book data cannot say whether a cancellation was ahead of us. The point estimate keeps today's
  convention - an unmatched decrement at our price is treated as resting **ahead** of us - because that is the coded and
  documented model, and the alternative (all cancels behind us) is reported as the other end of the band. The live loop
  records only a counter; both walks are computed offline, so the executor pays nothing extra per loop.
- **0.8 (§9.2, R8) The watched track is clamped at expiry and no order is placed from a rejected latest signal.** The
  watched track is simulated to `now` (`loop.py:850-852`) while the counterfactual clamps to `min(now, expiry)`
  (`loop.py:867`), so a print in the interval between an order's expiry and the loop instant can fill an order that has
  stopped resting. The rejected-verdict test exists only on the cancel path (`harness/execution/plan.py:511`) and not in
  `_intent_actions` (`plan.py:519-556`), so a rejected intent still produces a `Place` that the next loop cancels.
  Both are repaired; both must hold after a cancel, a retried step and a process restart.
- **0.9 (§9.5, §10, U8 6B) Dirty time is scoped, caused and measured in elapsed seconds.**
  `store.add_dirty_seconds` (`harness/execution/store.py:684-693`) is called before any status test
  (`loop.py:799-803`), so a cancelled order whose counterfactual is still running keeps accruing dirty seconds on its
  own column - the mechanism behind order 157's 181,200 seconds on a 35-minute order - and it adds a nominal
  `exec_period_s` whatever the loop actually took (p95 227 s in the 19:00 CT hour). 6B records dirtiness on the market:
  one `market_dirty_intervals` row per market per contiguous dirty stretch with a cause label, from which each order's
  watched and counterfactual exposure is an intersection and every duration is elapsed wall time.
  `orders.dirty_seconds` / `dirty_minutes` keep their column meaning, the watched order's own resting interval, and stop
  carrying the counterfactual's.
- **0.10 (§9.5, gate cleanliness) The observation interval is specified, not changed.** Criterion 1 reads
  `book_source = 'ws' and dirty_minutes = 0` (`harness/report/gate.py:360`). 6B specifies that quantity as **the whole
  watched resting interval**, `[placed_at, min(cancelled_at, expiry)]`, which is what the repaired accrual measures and
  what the column's own comment already claims. Elapsed-versus-nominal seconds changes magnitudes and never the
  zero/non-zero classification the criterion reads. Removing the counterfactual's accrual does move orders from non-zero
  to zero, which is the repair, versioned as `C5` and disclosed in the record; historical rows keep their raw values.
- **0.11 (§12, R14) Baseline replay reproduces the live variant population and the shared cap.** `replay()` scores one
  variant and constructs a single-variant executor (`harness/replay.py:249-250`), while live capacity is shared across
  every executed variant by one counter (`plan.py:609`, `still_open < s.max_open_orders`). A single-variant replay is a
  different experiment and cannot be compared with capacity-constrained live output. 6B adds a population replay and
  makes the Monday replay-versus-live duty (operator calendar, 09:45) compare population-equivalent runs; the
  single-variant mode stays, labelled.
- **0.12 (U8 6B) Corrected results are new rows, never rewritten ones.** The no-watcher re-score and the order 157 audit
  write `order_rescores` rows tagged with a correction id and labelled retrospective estimates; `fills`, `orders` and
  `ledger` rows from before the deploy are never updated, and the 27/445 no-watcher figure is neither a fill ceiling nor
  a basis for a forecast.
- **0.13 Needs the user's dated decision (R1); nothing waits on it.** (a) *Eligibility:* "Should the cumulative gate
  count only orders placed under the 6B corrections - that is, should `GATE_ELIGIBLE_FROM_ORDER_ID` and
  `GATE_ELIGIBLE_FROM_RUN_ID` be set together to the 6B deploy boundary, and on what date?" 6A built that mechanism
  dormant and the loop never sets it; until a dated decision the gate keeps reading the whole non-replay history, which
  after 6B is a mixed population, and every report quoting it says so. (b) *Cleanliness interval:* "Criterion 1's
  clean-book share is measured over the order's whole watched resting interval (§0.10). Do you want it measured over the
  interval up to the first fill, or at placement only, instead?" 6B adopts the coded interval; either alternative is an
  R1 amendment, adoptable without further code from the `order_dirty_time` view's `to_first_fill_dirty_s` column, which
  is computed and stored but read by nothing.
- **0.14 (roadmap 37, journal 128) The 6B deploy is the full recipe.** The additive DDL touches `harness/db/models.py`,
  which is on the full-deploy trigger list, so journal 128's app-only allowance does not apply and R4 governs.

## 1. Components

Each names the spec section or roadmap bullet it implements, its files, what it depends on, the regression it turns
green, and the independently calculated expected result it is judged by. `tests/test_execution_regressions.py`'s
markers are removed one at a time by the component that repairs the defect; a strict XPASS is a hard failure, so a
forgotten marker cannot pass review.

### 1.1 Subscription continuity (correction C1; ROADMAP §6B bullet 1; §0.2, §0.3)
`BookState.apply_delta` stops testing `seq` per book and stops setting `dirty` from it; `book.seq` is still carried so a
book records the last frame it applied. Every path that builds or advances a book takes its dirty verdict from the
sid-level gap rows: `load_book` and `advance_book` already call `_gapped`; `book_at` gains `_gapped_at` bounded at its
instant (which `load_book_at` supplies today), so the historical path does not lose its only dirty source when the
per-object check goes. The executor reads `max(ts)` of `operator_events(kind = 'ws_connect')` once per step and marks a
book dirty when its anchor's `as_of` precedes that instant (§0.3); `advance_book`'s existing re-anchor rule then clears
it on the first clean snapshot, which a resubscribe always produces.
*Files:* `harness/execution/book.py`, `harness/execution/loop.py`, `tests/test_book.py`,
`tests/test_execution_regressions.py`. *Depends on:* nothing.
*Turns green:* `test_multiplexed_subscription_sequence_does_not_dirty_the_book` (case 1a).
*Expected result, computed independently:* subscription 7 carried frames 1, 2, 3 with nothing missing and frame 2 was
ticker B's, so ticker A received every frame addressed to it and no level of A is stale: `dirty` is False. The paired
guard `test_a_real_missing_subscription_frame_still_writes_a_gap_row` (1b) stays green, and `test_book.py:84`
(`test_seq_gap_marks_dirty`), which pins the behaviour being removed, is rewritten to the new rule: a book is dirty when
a `gap` row exists on its sid after its anchor, and not otherwise. A second case covers §0.3 - a book anchored at 10:00,
a `ws_connect` at 10:05, a delta at 10:06 - dirty until a snapshot after 10:05 is applied.

### 1.2 Recovery anchoring (correction C2; ROADMAP §6B bullet 2; §0.4)
Both re-anchor branches of `_simulate_order` set, for each track: `queue_remaining` from the anchoring book,
`cursor_event_id = book.last_event_id`, `last_print_ts = book.as_of`, `last_print_ids = ()`, and the §1.3 accumulators
to zero. One helper does it for both branches so the no-book and recovery paths cannot drift apart again.
*Files:* `harness/execution/loop.py`, `tests/test_exec_loop.py`, `tests/test_execution_regressions.py`.
*Depends on:* 1.3 (the accumulators it clears).
*Turns green:* `test_recovery_takes_no_fill_from_a_trade_inside_the_gap` (case 3).
*Expected result, computed independently:* five contracts rest ahead before the gap; one trade of three happens inside
it; the recovery snapshot, taken after that trade, shows two resting. Two is five minus the same three, so the trade is
spent: applying it again would take the queue to -1 and pay us one contract we were never in line for. Fill 0, queue 2,
and the print watermark ends at the snapshot's `as_of` (00:00:20), not at the trade's (00:00:10).

### 1.3 Trade and decrement reconciliation, with sensitivity (correction C3; ROADMAP §6B bullet 3; §0.5, §0.6, §0.7)
Per track and per our own `(side, price)`, three persisted quantities replace the one-way `traded_at_price` charge:
`traded_at_price` (print volume whose decrement has not arrived), `unmatched_decrement` (decrement volume that took
queue ahead of us and no print has claimed) and `pending_surplus` (decrement volume beyond the queue, unclaimed). A
shrinking delta of size `d` at our price first repays `traded_at_price`, then removes `min(queue, rest)` from the queue
into `unmatched_decrement` and the remainder into `pending_surplus`. A hitting print of `c` at our price claims
`unmatched_decrement` first (those units were ahead of us and are now known to have traded, so no fill), then
`pending_surplus` (those units were beyond the queue, so they reach us and fill), then consumes queue directly, and only
the unclaimed remainder is added to `traded_at_price`. A print through our price still sweeps the queue to zero
unchanged. Prints are deduplicated by `trade_id` over a bounded set covering `store.PRINT_LOOKBACK`, so a late REST
backfill is applied once and correctly instead of being dropped by the watermark. `cancels_ahead` accumulates the
decrement volume never claimed by any print - the only quantity whose attribution is unknowable - so an order with
`cancels_ahead = 0` is provably insensitive to the convention; `simulate_fills` takes a `cancel_policy` argument
(`ahead` by default, `behind` for the bound) used only offline by §1.8.
*Files:* `harness/execution/fills.py`, `harness/execution/loop.py` (`_state_of` / `_state_columns`),
`harness/db/models.py`, `harness/db/schema.py`, `migrations/versions/0007_phase6b_execution.py`, `tests/test_fills.py`,
`tests/test_fills_tape.py`, `tests/test_execution_regressions.py`. *Depends on:* nothing.
*Turns green:* `test_a_print_and_its_own_delta_are_one_event` (case 2).
*Expected result, computed independently:* five contracts rest ahead at 0.30; one real trade of three lifts three of
them, leaving two ahead and nothing for us. The same-timestamp delta of -3 at the same price is the exchange reporting
that trade, not a second removal. Queue 2, fill 0, and the same pair fed in the other order, split into a 2 and a 1, or
separated by a persisted loop boundary gives the identical answer. The fixture check
`fixture_equal_timestamp_matching_delta` (196 of 196 prints carry a same-timestamp matching delta) says this path is the
normal case on our tape, not an edge.

### 1.4 Expiry clamp and rejected-latest-signal placement (correction C4; ROADMAP §6B bullet 4; §0.8)
The watched track's deadline becomes `min(now, expiry)`, the same expression the counterfactual already uses.
`_intent_actions` gains, immediately after its match test, `if intent.latest_decision == REJECTED: return
[Skip(intent.intent_id, SIGNAL_REJECTED)]`, which `uq_skip_once` makes idempotent per `(intent_id, kind, reason)`.
*Files:* `harness/execution/loop.py`, `harness/execution/plan.py`, `tests/test_exec_plan.py`,
`tests/test_exec_loop.py`, `tests/test_execution_regressions.py`. *Depends on:* 1.2.
*Turns green:* `test_the_watched_track_takes_no_fill_after_expiry` (case 5) and
`test_a_rejected_latest_verdict_yields_no_place` (case 6).
*Expected result, computed independently:* an order with expiry T0+10 s is off the market from T0+10 s, so a print at
T0+20 s cannot trade against it: fill 0 even with queue 0. And `latest_decision = rejected` is the strategy's own
current answer that this is not a bet - the cancel path already cancels a resting order for exactly that reason - so the
number of `Place` actions is 0 and exactly one `skipped` event with reason `signal_rejected` is written. Idempotence is
asserted by re-running the same loop over the same tape after persisting and re-reading the state (`_state_columns` then
`_state_of`): no second fill, no second skip row.

### 1.5 Watched versus counterfactual dirty intervals (correction C5; ROADMAP §6B bullet 5; §0.9, §0.10)
A new table `market_dirty_intervals(id, venue_market_id, ticker, started_at, ended_at, cause, replay)` gets one row per
market per contiguous dirty stretch, opened when the book step first sees the market dirty and closed when it clears or
when the executor stops (a row left open is closed by the next step that sees the market clean). `cause` is one of
`gap`, `session_boundary`, `recorder_dead`, `event_age`, `recovery`, taken from the same three tests
`MarketNow.dirty` makes (`plan.py:246-257`) plus §0.3's boundary. `add_dirty_seconds` is called only for an order in
`store.OPEN_STATUSES` and with the elapsed seconds since that order's previous observation rather than
`exec_period_s`; the counterfactual's own exposure accrues to a new nullable `orders.nw_dirty_seconds`. A view
`order_dirty_time` reports, per order, `watched_dirty_s`, `counterfactual_dirty_s`, `to_first_fill_dirty_s` and the
per-cause breakdown, by intersecting the order's intervals with its market's.
*Files:* `harness/execution/loop.py`, `harness/execution/store.py`, `harness/db/models.py`, `harness/db/schema.py`,
`migrations/versions/0007_phase6b_execution.py`, `tests/test_exec_loop.py`, `tests/test_execution_regressions.py`.
*Depends on:* 1.1 (the causes include the continuity verdict), 1.4.
*Turns green:* `test_a_cancelled_order_accrues_no_dirty_seconds` (case 4).
*Expected result, computed independently:* `dirty_minutes` is a property of the watched order - how long the order we
placed sat against a book we could not read. A cancelled order is not sitting against anything, so no write to its own
counter is due: `add_dirty_seconds` calls 0, seconds added 0. A second case computes elapsed time: two observations 47 s
apart over a market dirty throughout add 47 s, not 15, and the interval row carries one cause. A third pins the scope
split: an order cancelled at T+60 whose market is dirty from T+30 to T+120 and whose expiry is T+600 has
`watched_dirty_s` 30 and `counterfactual_dirty_s` 90.

### 1.6 Capacity-equivalent baseline replay (correction C6; ROADMAP §6B bullet 6; §0.11)
`replay()` accepts a list of variants (`--variant` repeatable, or `--population live` resolving
`Settings.exec_variants`), scores every run under each, and constructs **one** executor over all of them so
`plan_actions` applies the same shared `max_open_orders` counter the live loop applies. Single-variant replay is
unchanged and its output is labelled `single_variant` in `ReplayCounts` and in the Monday duty's line, so a parity claim
can never be made across different populations. Timing-policy differences between the replay grid and the live loop
(the grid steps exactly `exec_period_s`; the live loop took a p95 of 227 s in the sampled hour) are recorded in the
replay's summary rather than absorbed into the 2 % band.
*Files:* `harness/replay.py`, `harness/cli.py`, `tests/test_replay.py`. *Depends on:* 1.1-1.5 (a population replay of a
defective simulator proves nothing).
*Expected result, computed independently:* over a seeded range with three variants and `max_open_orders = 2`, seven
intents whose edges rank across variants produce at most two open orders at any instant and exactly the `exec_capacity`
skips the live ordering rule (`_order_action` first, then intents by edge) gives; the single-variant replay of the same
range produces strictly more orders for that variant, which is why the label exists.

### 1.7 The order 157 audit (ROADMAP §6B "audit order 157 before using it as a validated fill")
`harness audit-order --capsule <dir>` reads the 6A capsule for order 157 (its tape, prints, snapshots, fills,
`order_events`, watch samples and ledger rows) with no database and no NAS access, replays the order under the C0 code
path and under the repaired one, and publishes one of three verdicts with the supporting tape:
`validated` (the repaired simulation reproduces the recorded fills within one contract), `corrected` (it differs and
the tape explains why, the corrected quantities recorded as a retrospective estimate in `order_rescores`),
`unverifiable` (the capsule's tape does not cover the interval, or no snapshot anchors it - the 6A manifest's
`unverifiable_slices` is the input for that call). It discriminates the hypotheses the reconciliation left open, in this
order: (i) the equal-timestamp double count, which predicts a decrement of about -6,376 at our price stamped
15:07:15.332Z beside prints summing to 63.92 across the six recorded `last_print_ids`; (ii) a recovery anchoring error,
which predicts a `gap` row on the anchor's sid and a snapshot between 14:36:47Z and 15:07:15Z; (iii) a genuine queue
collapse, which predicts prints of 6,401 or more at 0.45 before the fills. Each is a query over the capsule with a
stated expected count, so the verdict is evidence and not a preference. The verdict string is stored in
`harness/corrections.py` beside `CORRECTIONS` so 6C's t13 can read it inside the container, and the prose record is
`docs/superpowers/reviews/2026-09-12-order-157-audit.md`.
*Files:* `harness/audit.py`, `harness/cli.py`, `tests/test_audit_order.py`, `harness/corrections.py`,
`docs/superpowers/reviews/2026-09-12-order-157-audit.md`. *Depends on:* 1.1-1.5.
*Expected result, computed independently:* the fixture is a synthetic capsule built to each hypothesis in turn. The
reconciliation's own counterexample (queue 6,401, a same-timestamp decrement of -6,376, prints of 25, 25 and 13.92,
reproducing `filled_contracts` 38.92, `traded_at_price` 63.92 and `queue_remaining` 0 with no recovery) is classified
`corrected` with a repaired fill of 0; a capsule with no anchoring snapshot is `unverifiable`; one whose prints
genuinely exhaust the queue is `validated`. This design fixes the procedure, not its answer: the controller runs it
against the real capsule.

### 1.8 The no-watcher re-score (ROADMAP §6B "re-score no-watcher outcomes only after the same model repairs")
`harness rescore --from-order <a> --to-order <b> --correction C5` walks each original order's own capsule or tape
window under the repaired simulator and writes one `order_rescores` row per (order, correction set, cancel policy):
`order_id`, `correction_ids`, `cancel_policy`, `watched_filled`, `counterfactual_filled`, `queue_remaining`,
`cancels_ahead`, `watched_dirty_s`, `counterfactual_dirty_s`, `verdict`, `computed_at`, `build_sha`. Two rows per
order, one per cancel policy, are the sensitivity band of §0.7. Nothing is written back to `orders` or `fills`; the rows
are labelled retrospective estimates wherever they are read, they are excluded from the gate exactly as `replay = true`
rows are, and the 27/445 figure is reported only beside its re-scored replacement, never as a ceiling.
*Files:* `harness/rescore.py`, `harness/cli.py`, `harness/db/models.py`, `harness/db/schema.py`,
`migrations/versions/0007_phase6b_execution.py`, `tests/test_rescore.py`. *Depends on:* 1.7.
*Expected result, computed independently:* a seeded world of three orders - one whose recorded fill the repaired
simulator reproduces, one whose fill it removes (the equal-timestamp case), one whose tape has a gap - produces
verdicts `validated`, `corrected`, `unverifiable`, an unchanged `fills` table (row count, `sum(contracts)` and `max(id)`
identical before and after) and two policy rows for each order, equal to each other exactly when `cancels_ahead` is 0.

### 1.9 `harness capsule --out -` (carried from the 6A ledger, final review M2)
`write_capsule(..., out="-")` streams a tar of the whole capsule on `sys.stdout.buffer` (`harness/capsule.py:463-486`)
and is exercised only by hand today. The test captures `sys.stdout.buffer`, un-tars the stream, and asserts the members
equal a directory-written capsule of the same slices byte for byte, that `manifest.json` is the last member, and that no
log line or progress text contaminates the stream.
*Files:* `tests/test_capsule.py`. *Depends on:* nothing.
*Expected result, computed independently:* a capsule of *n* slices produces *n* + 1 tar members (`<table>.jsonl.gz` plus
`manifest.json`), each member's sha256 equal to the manifest's entry for it and its bytes equal to the directory path's,
because both go through the same `_jsonl_gz`.

## 2. Data

Additive only. Every statement is `add column if not exists` / `create table if not exists` / `create or replace view`,
declared in `harness/db/models.py`, `harness/db/schema.py` (`_COLUMN_DDL`, `_VIEW_DDL`) and
`migrations/versions/0007_phase6b_execution.py` together, which is what `tests/test_alembic.py`'s catalogue diff
requires (§0.1 for the no-rewrite rule, §4.3 for rollback).

| Addition | Shape | Invariant query (must return 0) |
|---|---|---|
| `orders.unmatched_decrement`, `.pending_surplus`, `.cancels_ahead` and the three `nw_` twins | `numeric(14,2)`, nullable, no default | `select count(*) from orders where replay = false and id <= :boundary_order_id and (unmatched_decrement is not null or nw_unmatched_decrement is not null)` - no pre-6B row is ever backfilled |
| `orders.nw_dirty_seconds` | `integer`, nullable, no default | `select count(*) from orders where nw_dirty_seconds is not null and nw_dirty_seconds < 0` |
| `market_dirty_intervals` | `id bigserial`, `venue_market_id int`, `ticker varchar(64)`, `started_at timestamptz`, `ended_at timestamptz null`, `cause varchar(20)`, `replay bool`; index on `(venue_market_id, started_at)` | `select count(*) from market_dirty_intervals where ended_at is not null and ended_at < started_at` and `select count(*) from (select venue_market_id from market_dirty_intervals where ended_at is null and replay = false group by 1 having count(*) > 1) x` |
| `order_rescores` | `order_id bigint`, `correction_ids varchar(64)`, `cancel_policy varchar(6)`, the six measured columns, `verdict varchar(12)`, `computed_at timestamptz`, `build_sha varchar(24)`; primary key `(order_id, correction_ids, cancel_policy)` | `select count(*) from order_rescores r left join orders o on o.id = r.order_id where o.id is null or r.verdict not in ('validated','corrected','unverifiable')` |
| `order_dirty_time` view | per order: `watched_dirty_s`, `counterfactual_dirty_s`, `to_first_fill_dirty_s`, per-cause seconds | `select count(*) from order_dirty_time where watched_dirty_s > counterfactual_dirty_s` - the watched interval is contained in the counterfactual one |

## 3. Verification (the plan's last task extends `verify.md`)

The deploy boundary values (`:boundary_order_id`, `:boundary_fill_id`, `:boundary_event_id` and the pre-deploy triple
`count(*), sum(contracts), max(id)` over `fills where replay = false`) are read and journaled by the controller
immediately before the deploy; the rows below quote them.

1. **Originals intact.** `select count(*), sum(contracts), max(id) from fills where replay = false and id <=
   :boundary_fill_id` equals the journaled pre-deploy triple, and the same for `orders`' `sum(filled_contracts)` and
   `sum(dirty_seconds)` at `id <= :boundary_order_id`. *Every verify after the 6B deploy; any hour.* A difference is an
   integrity anomaly, not a fix-forward.
2. **No post-expiry fill.** `select count(*) from fills f join orders o on o.id = f.order_id where f.replay = false and
   f.id > :boundary_fill_id and o.expiry is not null and f.filled_at > o.expiry` = 0. *Judged from the first game
   window after the deploy; before one has run it reads "deferred: no post-deploy fill yet".*
3. **No placement from a rejected target.** `select count(*) from order_events e join orders o on o.intent_id =
   e.intent_id where e.kind = 'place' and e.id > :boundary_event_id and exists (select 1 from order_events s where
   s.intent_id = e.intent_id and s.kind = 'skipped' and s.reason = 'signal_rejected' and s.ts <= e.ts)` = 0, and the
   `skipped/signal_rejected` count is > 0 by the first game window (the rule fires, rather than being unreachable).
4. **Liquidity conservation.** For a sample of ten post-deploy orders with a `queue_model` fill:
   `filled_contracts <= sum(count) over hitting prints at or through the order's price inside its resting interval`,
   read from `venue_trades` by `(ticker, ts)`; and `select count(*) from fills where replay = false and id >
   :boundary_fill_id and fill_method = 'queue_model' and has_print = false` = 0. *Sundays and game days; deferred when
   the sample is empty.*
5. **Dirty scope.** `select count(*) from orders where replay = false and id > :boundary_order_id and status in
   ('cancelled','expired') and dirty_seconds > extract(epoch from (coalesce(cancelled_at, expiry) - placed_at))` = 0 -
   no order accrues more watched dirty time than it spent resting. *Every verify.* At 01:00-08:00 CT
   `market_dirty_intervals` has no open row older than two hours; inside a game window open rows are expected and their
   count is journaled beside `exec.dirty_markets`.
6. **Manifest.** `harness manifest` on the NAS prints `"manifest_version": 7`, `"measurement_version": "4.5"`, seven
   corrections `C0`-`C6`, and the order 157 verdict. *After the 6B deploy.*
7. **Regressions.** `make test`'s summary line reports **0 xfailed** from `tests/test_execution_regressions.py` and zero
   `XPASS`; the two passing guards and the new cases of §1.1-§1.5 are green. *Every verify after the 6B deploy* (this
   replaces the 6A row's "exactly 6 xfailed").
8. **Gate untouched.** the newest `gate_reports` row's `criteria_hash` is still
   `5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5` and
   `select count(*) from gate_reports where criteria_json ? 'eligibility'` is still 0. *Every verify.*

The deploy is judged on rows 1, 2, 3, 5 and 7 together with the executor's own health: `exec.loop_ms` p95 no worse than
the pre-deploy hour it is compared against, and `exec.open_orders` and `exec.skipped` reasons journaled before and
after, because §1.4's rejection skip and §1.1's continuity repair both change how many orders rest.

## 4. Ops

- 4.1 Target: the **full** recipe (`make deploy-nas`, fix 37's reviewed version), because the diff touches
  `harness/db/models.py` (§0.14). `app-run`, `app-serve`, `app-exec` and `app-research` carry the changed code;
  `app-ws` is rebuilt by the full recipe although no recorder file changes. `init-db` applies the additive DDL, and
  migration `0007_phase6b_execution` carries the same statements for a database that takes migrations instead.
- 4.2 Window: R4 in full (no deploy while a matched game is `in_progress`, within 4 h after any kickoff, within 15 min
  before one, or 60-100 min before an NFL kickoff); journal 128's app-only allowance does not apply.
- 4.3 Rollback: previous sha plus `make deploy-nas`. The additive columns, tables and view stay; no DROP is ever part of
  a rollback (roadmap invariant 5). A rolled-back build writes the old columns and ignores the new ones, and the
  correction record says which build produced which rows.
- 4.4 No new secret, host, container or cron. The re-score and the audit are read-mostly commands the controller runs
  over ssh in the quiet window (01:00-08:00 CT), one at a time, abandoned if `exec.loop_ms` exceeds 30 s during one -
  the rule the capsule extraction already runs under.
- 4.5 Expected load change: §1.4 and §1.5 shorten what the loop simulates; the 7,998 orders carried with `nw_done =
  false` remain carried (closing them early is 6D's policy question). The before and after `exec.loop_ms` numbers are
  journaled, not promised: 6B is not a performance fix.

## 5. Testing

- **Fixtures.** Realistic mixed-market fixtures come from the 6A capsules (`clean`, `interleaved`, `gap_recovery`,
  `delayed_loop`, `capacity_bound`, plus order 157's), loaded through a small reader over the gzipped JSON-lines files,
  with `tests/fixtures/tape_sample_lou_miss_2026-09-07T03.json` kept as the existing premise check. A capsule not yet
  extracted is not a blocker: each case has a synthetic twin built from `tests/test_fills.py`'s helpers with the same
  shape, and the capsule-backed variant is added by the task that has the file. No test skips.
- **The genuine gap/reconnect case** is the `gap_recovery` capsule: a real `gap` row with its `sid` and `ts`, the
  resubscribe, the fresh snapshot and the deltas either side, proving §1.1 (the interleaving before the gap does not
  dirty the book) and §1.2 (the snapshot after it anchors both cursors) on one slice. **A recovery containing an older
  trade** is §1.2's case 3 plus its capsule twin: a print stamped inside the gap window, arriving after that snapshot.
- **Batch and restart consistency under one observation policy:** the same event history fed as one call, as twenty
  chunks, and across a persisted boundary (`_state_columns` written and re-read through `_state_of`) yields identical
  fills, queue and accumulators - the existing `test_chunking_invariance` extended to the new state.
- **Independently calculated expectations.** Every case's docstring states the expected queue and ledger result and how
  it was derived from the tape; a test that re-runs the code's own arithmetic is rejected at review (ROADMAP §6B).
- **Acceptance mapping.** ROADMAP.md §6B's acceptance clauses are proven at: realistic mixed-market fixtures §5 and
  §1.3; genuine gap/reconnect §1.1 and §1.2; recovery containing an older trade §1.2; liquidity conservation §3 row 4
  and §1.3's property test; no post-expiry fills §1.4 and §3 row 2; no placement from rejected targets §1.4 and §3
  row 3; batch/restart consistency §5; independently calculated expected queue/ledger result every component's
  *Expected result* line; timing-policy differences recorded §1.6; order 157 published as validated, corrected or
  unverifiable §1.7; no-watcher re-scored only after the repairs §1.8; raw historical cleanliness retained §0.9, §2 and
  §3 row 1.

## 6. Out of scope

6C (week keys, gate documentation, confirmation floor, exact-contract joins, funnel units, annotation backlog); 6D
(scheduled-versus-completed instrumentation, budget isolation, the holding and capacity policy comparison - including
any change to `max_open_orders`, the stale allowance, rest-to-expiry or admission rules, and closing the `nw_done =
false` backlog early); 6E (inventory, restore rehearsal, benchmark, host choice, cutover); 6F (the version boundary and
the prospective period); anything live, any venue write, any real money. No new variant: the registered ids are frozen,
nothing under `harness/variants/` is touched, and anything new after Mon 2026-09-21 09:00 CT would be exploratory. No
change to any gate criterion, threshold, family, grid, success threshold or cut-off (R1); no switching on of the
eligibility settings (§0.13a); no change to the recorder, the kill switch, the dashboard token, spend caps or outbound
hosts.

## 7. Conformance (autopilot plan-next 1a)

1. **Components.** §1.1-§1.6 implement roadmap decision 2's six bullets in their order; §1.7 is its order 157 audit,
   §1.8 its no-watcher re-score, §1.9 the 6A ledger's carried `--out -` test. Nothing else.
2. **Dependencies.** None new: standard library, SQLAlchemy, Typer and pytest as already pinned. `pyproject.toml` and
   `constraints.txt` are untouched.
3. **Pre-registered ids.** Untouched; `MAX_PRIMARY` and `MAX_SECONDARY` untouched; `harness/variants/` untouched.
   `EXECUTOR_VERSION` moves 4.4 → 4.5 once, which changes `config_hash` for orders placed afterwards and is the
   measurement boundary C1-C6 record (§0.1); `tests/test_fills.py::test_executor_version_is_bumped_for_fills` moves with
   it.
4. **Schema.** Additive only: six nullable `numeric` columns and one nullable `integer` on `orders`, two new tables, one
   view, declared in models, `schema.py` and migration `0007_phase6b_execution` together (§2). No DROP, RENAME,
   TRUNCATE, DELETE or backfill.
5. **Venue writes.** None. No code path here opens a venue client; the gateway stays `PaperGateway` and the refusal
   tests are unchanged.
6. **Money.** None; no metered call, no Anthropic call, no Odds API credit.
7. **Secrets.** None; no component reads `secrets/`.
8. **Ops.** §4: full deploy recipe under R4, rollback to the previous sha, no new secret, host, container or cron.
9. **Verification.** §3, with one invariant query per new table and column family (§2) and expected values by time of
   day on rows 2, 4 and 5.
10. **Decisions taken on the user's behalf.** §8, each with source, rationale, cost if wrong, blast radius and its exact
    reversal.
11. **Out of scope** matches the roadmap's milestone boundaries (§6).
12. **Files and Depends on.** Every component above carries both lines; the plan writer turns them into tasks and the
    plan review checks the pairing (the addendum's component sections are not the plan). Independent starts: §1.1,
    §1.3, §1.9. Then §1.2 (after §1.3), §1.4 (after §1.2), §1.5 (after §1.1, §1.4), §1.6 (after §1.1-§1.5), §1.7 (after
    §1.1-§1.5), §1.8 (after §1.7); `verify.md` last. `harness/execution/loop.py` is touched by §1.1, §1.2, §1.4 and
    §1.5, which is why those four are serialized rather than run wide.

## 8. Decisions taken on the user's behalf

| # | Decision | Source | Rationale | Cost if wrong | Blast radius | Reversal |
|---|---|---|---|---|---|---|
| D1 | Continuity is read from sid-level `gap` rows and the per-book `seq` check is deleted rather than made sid-aware | pre-loaded (bullet 1) confirmed by the venue docs' "sid ... subscription identifier" and "seq ... guarantee you received all the messages" | the recorder already writes the correct subscription-level verdict; a second, weaker copy in the consumer is what produced the false dirty state | a lost frame that the sink somehow failed to record would go unseen | file (`book.py`), NAS behaviour after deploy | restore the per-object check |
| D2 | A book anchored before the newest `ws_connect` is dirty until it re-anchors | model (§0.3; no gap row exists at a session boundary) | reconnects are frequent (370 `ws_disconnect` / 297 `ws_connect` observed) and a book that keeps its pre-outage anchor is a book with an unrecorded hole | briefly more dirty markets right after a reconnect, until the fresh snapshot lands | file, NAS behaviour | drop the check |
| D3 | The reconciliation keeps the "unmatched decrement rests ahead of us" convention as the point estimate and reports the other end as a band | pre-loaded ("report sensitivity where exact queue ownership is unknowable") | it is the coded and documented model; changing the point estimate would be a model change dressed as a repair | the point estimate stays optimistic about cancellations; the band says by how much | file, DB additive | switch `cancel_policy` default to `behind` |
| D4 | The sensitivity walk runs offline only; the loop records `cancels_ahead` | model (loop p95 227 s, 7,998 counterfactual orders) | a second full walk per order per loop on an IO-starved box would cost more than the number is worth, and `cancels_ahead = 0` already proves insensitivity | a sensitivity figure needs a re-score run rather than a column read | file, DB additive | compute both walks in the loop |
| D5 | Print idempotence moves to a bounded `trade_id` set; the timestamp watermark is kept for pruning | model (§0.6; order 157's two REST fills stamped 17:00:20Z were applied on 09-10) | the watermark drops genuinely new late prints and is the reason a REST backfill cannot be replayed correctly | a memory of ids per order, bounded by `PRINT_LOOKBACK`, instead of one timestamp | file, DB additive | revert to the watermark test |
| D6 | Dirtiness is recorded on the market in `market_dirty_intervals`, not per order per loop | model | 136 dirty markets against 7,998 orders: one row per market per stretch is cheaper than the per-order UPDATE it replaces, and intersection gives both scopes and elapsed time exactly | one more table to read when explaining a number | DB additive | keep only the repaired per-order counters |
| D7 | `orders.dirty_seconds` keeps its column meaning and the gate keeps reading it; the new view is not wired into any criterion | pre-loaded (R1) | the criterion's SQL, threshold and hash must not move; the repair changes inputs, not meaning | the cumulative gate reads a mixed population until §0.13a is answered, which every report says | file | none needed |
| D8 | Corrected results are `order_rescores` rows, not `replay = true` orders | model (a replay order has a new id and cannot be tied to the original) | keeps the link to the original order, keeps the estimate out of the gate, and never touches `fills` | one more table; a full replay is still available for a whole range | DB additive | drop the table, use `harness replay` |
| D9 | The order 157 verdict lives in `harness/corrections.py` as well as in a record | model (6A D4's reasoning: `docs/` is absent inside the container, and 6C's t13 must read the status) | one import, no file read, and the parity test keeps the two honest | two places to update | file | keep only the record |
| D10 | One `EXECUTOR_VERSION` bump (4.5) for the whole milestone rather than one per correction | model | the milestone deploys once, so one boundary and one new `config_hash` set is what the record needs; per-task bumps would invent boundaries no order was placed across | a later correction inside 6B shares the boundary with the others | file | bump again before the deploy |
| D11 | `C1`-`C6`'s `config_hashes` are filled by the controller at merge time, as `C0`'s were | pre-loaded (6A's established pattern; agents have no NAS access) | the post-deploy hashes do not exist until the deploy; the tuples ship empty with width-only tests | the record's hashes are added in the controller's own commit | file | none |

## 9. Rulings
