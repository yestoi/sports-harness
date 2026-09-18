# Saturday question for the user: may a past-deadline counterfactual track close once its batch has been read through its deadline? (row 86, docket item 23)

Prepared by the controller (sports-ed) on Fri 2026-09-18 from the read-only opus argument `.superpowers/sdd/results/design-86b-lagging-close-argument.md` (worktree at main 82401bd, no code changed, no test run), under the user's ruling of 2026-09-18b §3: the loop prepares the argument and proposes nothing; no executor code ships before the user rules. Tonight's query 4 (design-86, ~17:55 and 18:25 CT) is attached at the end when it has run.

**The question, as the user put it:** may a past-deadline track close once its batch has been read through its deadline, rather than only when the ticker is no longer lagging?

**The one thing to settle first (argument §6 Q1).** Read literally ("the last consumed delta's `ts` at or past the deadline") the rule never bites, because a past-deadline track consumes only deltas with `ts <= deadline` (`harness/execution/fills.py:430`); it collapses to "the batch was not truncated", which is what `lagging` already means. Read as the batch's *coverage* (`covered = deltas[-1].ts`, `harness/execution/loop.py:1832`) it bites. Everything below assumes the second reading.

**What the change would have saved on Thursday:** the three post-expiry loops spent 524.5 s of `phase_per_row_ms` (574.2 s of loop time) on 1,104 tracks whose value could no longer move, and rewrote about a thousand `orders` rows a loop with the values they already held (Watch row 80's churn). It does nothing for the Sep 14 cursor or the 25,301 pending tracks (all have future expiries; §5): that backlog is a different defect, recorded below.

**The strongest case against:** a late REST print inside the deadline (`harness/normalize/kalshi.py:257` is a second writer of `venue_trades`, the print read has no id bound, `store.py:481-485`) would be fed by today's rule and not by the proposed one; and the unread remainder of a delta batch is bounded by `id`, not `ts` (`book.py:46-50`), so "covered past the deadline" is evidence, not proof. Against that: no non-lagging ticker and no dirty-market row has that grace today (`loop.py:2371`, `store.py:1195-1198`), so the change removes an accident, not a designed window.

**What a test must be (§4):** not the design read's replay - a replay executor never truncates (`store.py:928-931`) and cannot recreate the cohort - but the live path stepped on a fixed instant grid over a scratch restore, twice, with equality required on every `nw_*` value column, `fills`, `ledger` and `market_dirty_intervals`, and differences accepted only in `nw_done`'s loop, the loop count and the per-loop gauges.

**Open questions for the user (§6):** 1 which reading (above); 2 which deadline (`expiry` as the close test uses, or `_nw_deadline = min(expiry, kickoff - 10 min)`); 3 whether the watched track's held `Expire` (`plan.py:513-522`) changes too; 4 whether the id/`ts` skew needs a margin (`DELTA_LOOKBACK = 5 s`); 5 whether the late-print grace is worth its cost; 6 whether row 72's 1,176 pre-6B ids are bounded away; 7 whether a replay is the instrument at all.

**Citation corrections the argument records:** `_merge_events` and the `d.ts <= deadline` test are `fills.py:410-436`, not `book.py`; row 72 is Closed, not Watch; the earlier extract's "when the cohort rows closed" histogram dates the cancellations.

**A separate observation, not part of the question:** the oldest pending cursor (170434701, 2026-09-14 18:09Z, 28.8 M deltas behind) has not moved in four days although its whole batch is admissible; the argument reads that as a permanently dirty market (`plan.py:255-266`) or the rotation's budget deferral (`walk_deferred_n` 14.7-19.3k a loop), a different defect from row 79's tape cost. Tonight's 17:55 CT read adds one read-only query on it.

---

## The argument in full (verbatim)

# Row 86 / docket item 23: the argument on the lagging-close rule, both sides

Read-only design read for the user's Saturday decision. Worktree
`/home/trey/dev/sports-wt/design-2026-09-18-lagging-close`, main at `82401bd`; no code changed, no
test added or run, no production or NAS access. Citations are `path:line` at `82401bd`. Nothing here
is a proposal: the question is the user's and the loop only assembles it.

**Citation correction before anything rests on it.** `_merge_events` and its `d.ts <= deadline` test
are in `harness/execution/fills.py:410-436` (the test at `fills.py:430`), not `book.py`. `book.py`
holds `_DELTAS_BY_TS` (`:110-114`) and `_NEWEST_WS_SNAPSHOT_AT` (`:119-122`); `DELTA_BATCH_LIMIT` is
`harness/execution/store.py:846`. The row 86 design read cites `fills.py` correctly.

## 1. The rule as it stands

**The close condition,** `harness/execution/loop.py:2497-2498`:

    done = (row.ticker not in lagging
            and ((row.expiry is not None and row.expiry <= now) or filled >= row.contracts))

That dict is the whole write on both paths - per-row `_simulate_order` (`loop.py:2055-2058`) and fix
78b's batched pre-check (`:1518-1521`) - so `lagging` gates the close in one place. The docstring
states the intent: a track on a ticker whose read truncated "is not finished, even at its own expiry:
the tape between its cursor and now has not been fed to it yet" (`:2492-2495`).

**"Lagging" in code.** `_tape` builds one window per ticker: the lower `ts` bound is the earliest
`placed_at` of its working rows, the cursor is the **minimum** over every live track on the ticker -
the watched cursor of an open row and the `nw_` cursor of every row with `nw_done = false`
(`loop.py:1729-1749`, the `nw_` arm at `:1734-1735`, nulls excluded at `:1736-1748`). The read is
`load_deltas(..., cursor or 0, lower, at, limit=limit)` (`loop.py:1773-1774`) against `_DELTAS`:
`where ticker = :t and kind = 'delta' and id > :cursor order by id limit :limit`
(`store.py:874-878`). `truncated` is `len(rows) >= limit`, measured against the limit the read
actually ran with (`store.py:939-941`); a truncated batch marks the ticker lagging and nothing else
does (`loop.py:1808-1815`), published as `exec.tape_lag_tickers` (`loop.py:622`, `:1865`). Two other
things ride on the flag: the batch doubles (`:1821`), and prints past the last delta are held back -
`prints = [p for p in prints if p.ts <= covered]`, `covered = deltas[-1].ts` (`:1822-1833`).

So "lagging" is one statement about the **whole ticker**, taken from the read made for the oldest
cursor on it, and it is about row count, never about time. A ticker anchored at an old track's
cursor reads as lagging wherever the expiring cohort's own cursors sit.

**Thursday's cohort.** 1,104 rows, 5 tickers, all expiring 2026-09-18 00:05:00Z, all cancelled days
earlier (`.superpowers/sdd/hotfix-2026-09-18-expiry-cohort/data-thu-cohort-extract.txt`). Loops ran
17.7-24.8 s before the expiry. At 00:05:14Z: `loop_ms` 184,438, `expiring_n` 1,104,
`per_row_book_query` 1,155, `phase_per_row_ms` 165,398.9, `tape_lag_tickers` 6 - and 62 rows closed
(SPREAD-BUF5, the one cohort ticker not lagging). Then 00:08:29Z, 253,471 ms, 174 closed; 00:12:44Z,
204,287, none; 00:16:14Z, 116,450, 868 closed, lag down to 2. The five surviving cursors are
184739229/272/273/274/277 - one instant, five tickers - because the deadline is fixed at the expiry
(`_nw_deadline`, `loop.py:2474-2485`), `_merge_events` admits no delta past it (`fills.py:430`) and
the cursor advances only over admitted deltas (`fills.py:723-725`). A frozen cursor that is not the
base book's head routes the row to `PER_ROW_BOOK_QUERY` (`loop.py:1494-1497`) and `_sim_book`'s
historical branch (`loop.py:2074-2086`) - about seven round trips and a delta replay per row per
loop, the 142-223 ms the design read measured.

**One place the rule is already not applied.** A past-expiry counterfactual on a **dirty** market
closes with no lagging test: `_close_nw_if_expired` writes `nw_done = true` on `row.expiry <= now`
alone (`loop.py:2363-2372`), as does `close_nw_expired_batch` / `_NW_CLOSE_BATCH`
(`store.py:1195-1198`, `:1222-1233`), called for the dirty population at `loop.py:1327`. Rows on a
ticker the loop could not read are excluded earlier (`loop.py:1254`, `:1085-1094`), but a lagging
ticker *is* read - so the rule protects clean-market rows on a truncated read, not dirty-market
ones.

## 2. The case for closing at deadline-read

**No delta past the deadline can change the value.** `_merge_events` admits a delta only when
`placed_at < d.ts <= deadline` and `d.event_id > cursor` (`fills.py:427-431`), a print only when
`floor < p.ts <= deadline` (`:432-434`). For a past-expiry row `_nw_deadline` is a constant
(`loop.py:2474-2485`), so the admissible event set is **fixed**; a later loop can move the row only
by reading a member of that set it had not read yet. Once the batch covers the set, every later loop
is the same walk over the same events.

**What "read through its deadline" must mean.** The coverage is already named in the code:
`covered = deltas[-1].ts` (`loop.py:1832`). The question's two clauses are the two branches at
`loop.py:1808-1838`: `batch.truncated is False` (the read reached the head) or `covered >= deadline`
(it stopped short of the head but past the deadline). Under the second the hold-back at `:1833` drops
nothing usable: every admissible print has `p.ts <= deadline <= covered`.

**What is identical under the two rules.** The walk admits no event, so `simulate_fills` returns
`fills=[]`, `cross=None` and the state it was handed (`fills.py:718-750`; `_mark_prints([], ...)` at
`:746` is idempotent once the window bound is fixed). By column, from `_nw_columns` and
`_state_columns` (`loop.py:2499-2503`, `harness/execution/state.py:139-170`):

- `nw_filled_contracts`, `nw_queue_remaining`, `nw_crossed`, `nw_tape_cursor_event_id`,
  `nw_traded_at_price`, `nw_print_unmatched`, `nw_pending_unmatched`, `nw_pending_surplus`,
  `nw_cancels_ahead`, `nw_recon_state`, `nw_last_print_ts`, `nw_last_print_ids`: unchanged on every
  loop after the deadline-covering one, so the last value written is the same either way.
- `worst_case_fill`: written only when `crossed` (`loop.py:2501-2502`), which is sticky
  (`fills.py:748-750`). The pre-walk cross test is `working.as_of <= deadline and _crosses(...)`
  against the book reconstructed at the frozen cursor instant (`fills.py:702-713`,
  `loop.py:2085-2086`), and that reconstruction is a pure function of tape rows with `ts <= instant`
  (`book.py:591-614`, `:653-671`). Same input, same verdict, every loop.
- fills and ledger: `result.fills` is empty so `_persist_track` inserts nothing
  (`loop.py:2095-2107`), and the counterfactual runs `ledger=False`, writing no `ledger` row in any
  case (`:2108-2116`).
- `nw_dirty_seconds`: `_clamped` is 0 once `now > expiry` (`loop.py:2526-2545`) and a zero clamp is
  never written (`store.py:1201-1219`) - I-15's rule; a past-expiry track accrues nothing either way.
- `market_dirty_intervals`: written per market in `_advance_books` (`loop.py:958-984`); no order row
  is an input.
- `exec.skipped{reason=book_dirty}`: written by the intent chain (`plan.py:578-579`,
  `loop.py:2173-2181`) from markets and signals; no order row is an input per loop (§4 for the
  window total).

**Assumptions, and where each is guaranteed.**

1. *Deltas ordered by `ts` within the read.* **Not guaranteed.** The batch is ordered by `id`
   (`store.py:877`), the recorder's insertion order, while `ts` is the venue's clock - the harness
   says so in justifying `DELTA_LOOKBACK = 5 s`: "a delta that genuinely follows the snapshot can
   carry a slightly earlier timestamp" (`book.py:46-50`); the sink stamps
   `_ts(body.get("ts_ms"), received_at)` (`harness/recorder/ws_sink.py:243-247`). So
   `covered >= deadline` bounds the unread remainder by id, not by `ts`. What holds: deltas have one
   writer, that sink, appending in arrival order (the other `OrderbookEvent(...)` constructions are
   gap and snapshot rows, `ws_sink.py:178`, `:235`, `:297`), so the inversion is bounded by
   venue-side reordering and overlapping subscriptions, not by any backfill.
2. *The WS snapshot re-anchor.* `load_book_at` anchors on the newest snapshot at or before the
   instant and applies `_DELTAS_BY_TS` from it (`book.py:596-613`), with the gap and session tests
   bounded at the instant (`:665-671`). Nothing after the deadline enters it, so the historical book
   the cross test reads is the same every later loop - given no tape row with `ts <= instant` lands
   afterwards.
3. *Late events with an older `ts`.* For deltas, see (1). For **prints** it is a fact, not an
   assumption: `venue_trades` has a second writer, the REST normalizer
   (`harness/normalize/kalshi.py:257` beside `ws_sink.py:208`), and the print read is
   `ts >= :lower` with no id bound (`store.py:481-485`), so a backdated print is picked up on
   whatever loop it lands and deduplicated by `trade_id` (`fills.py:736-739`). Fix 79's cache does
   not hide one: its guard compares count, min/max `ts` and a token sum and falls back to a full read
   when any moves (`store.py:740-752`).

**The cost the rule carries.** While it holds, every cohort row re-enters `PER_ROW_BOOK_QUERY` and
pays a reconstruction each loop. Thursday's three post-expiry loops spent
235,229.4 + 187,524.5 + 101,784.0 = **524.5 s** of `phase_per_row_ms` on tracks whose value could no
longer move, and rewrote about a thousand `orders` rows a loop with the values they already held -
the churn behind fixes.md Watch row 80 (1,042 MB after about 10.9 M counterfactual updates,
`docs/superpowers/autopilot/fixes.md:42`).

## 3. The case against

1. **A delta with `ts <= deadline` arriving after the read covered the deadline.** Assumption (1)
   inverted: the unread remainder is bounded by `id`, and `id` and `ts` are not co-monotone
   (`book.py:46-50`, `ws_sink.py:243-247`), so `covered >= deadline` is evidence, not proof, that the
   admissible set is exhausted. If such a row exists the current rule feeds it (`fills.py:427-431`)
   and the proposed rule does not. Nothing in the code protects this: no `ts`-ordered live read, no
   watermark that would notice.
2. **A late REST print inside the deadline.** The strongest concrete case: the writer is real
   (`normalize/kalshi.py:257`), the read finds it (`store.py:481-485`), the ledger accepts it exactly
   once (`fills.py:736-739`), and the normalizer backlog is an open operational concern, so "late" is
   not hypothetical. The current rule gives a lagging ticker's past-expiry track extra loops of that
   grace. Against it: no non-lagging ticker has the grace (Thursday's BUF5 closed on the expiry loop)
   and no dirty-market row has it (`loop.py:2371`, `store.py:1195-1198`), so the change removes an
   accident, not a designed window.
3. **A re-anchor from `_NEWEST_WS_SNAPSHOT_AT`.** The historical book at a past instant can
   reconstruct differently later if a row with `ts <= instant` lands after the close - the mechanism
   the design read gives as its reason to reject a cross-loop memo (proposal ii-2). The values that
   would move are the cross: `crossed`, `worst_case_fill` and a `snapshot_cross` fill row
   (`fills.py:702-713`, `loop.py:2118-2132`). The population is not empty: 235 of row 72's 1,176
   pre-boundary pending rows carry `worst_case_fill`. Unprotected under either rule; the current one
   only leaves a longer window.
4. **`book_dirty` skip accounting.** Per loop, unaffected. Over a wall-clock window, not: skip rows
   are one per intent per loop, so a window that runs more loops writes more. Thursday's window
   recorded 615 `book_dirty` and 1,116 `exec_capacity` skips over 13 loops averaging 80 s; the same
   25 minutes at 15 s would be about 100 loops. An earlier close makes loops faster, so it moves
   those counts - not because a verdict changed but because the loop count did.
5. **The dirty-interval close.** `market_dirty_intervals` closes for `clean + gone` (`loop.py:984`),
   and Watch row 70 records `gone` as always empty, so a departed market's interval never closes
   (`fixes.md:41`). Indifferent to when a track closes - but that ledger cannot serve as a control in
   a comparison of the two rules, because it does not close reliably in either.
6. **Cursor semantics for the ticker's other tracks.** The window is anchored at the minimum cursor
   over live tracks, and a row with `nw_done = false` contributes its `nw_` cursor
   (`loop.py:1734-1735`). Closing a past-deadline track removes its frozen cursor from that minimum
   one or more loops earlier, so the window can only move later, and every other track on the ticker
   is handed a shorter delta list on those loops. No track can lose an event it had not consumed
   (`fills.py:427-431`), but `covered` moves with the window (`loop.py:1832-1833`), so a print one
   rule holds back from a still-open neighbour the other feeds on that loop. The neighbour's final
   value is the same - it is re-read next loop - but the **loop on which** its fill is written can
   differ. This is the one way the change reaches rows that are not past their deadline.
   For Thursday the cohort's cursor was *not* the binding anchor: each cohort ticker's own backlog
   past 184739229 was 1,240 / 1,768 / 110 / 833 / 797 at 00:08:29Z, under the 20,000 limit
   (`controller-queries-0745.txt` Q1). The change stops the cohort paying for the lag; it does not
   shorten it.
7. **The pre-6B rows.** Row 72's 1,176 pre-boundary pending tracks (expiries 2026-09-17 to
   2026-09-22, 235 with `worst_case_fill`, ids in
   `docs/superpowers/autopilot/evidence/2026-09-15-row72-ids.txt`) are inside this population. The
   Layer 2b invariant was narrowed by ruling to rows that carried `nw_done = true` at the boundary
   (fixes.md row 72, amendment 0.17), so an earlier close does not re-open it - but it moves the loop
   on which each of the 1,176 stops being written and the version stamp it stops at.
8. **The same flag is load-bearing elsewhere.** The watched track's `Expire` is held by the identical
   test: `return None if order.ticker in lagging else Expire(order.order_id)` (`plan.py:508-522`,
   reasoning at `:513-521`, threaded from `loop.py:823`). Two tests encode the pair as one behaviour:
   `tests/test_exec_loop.py:1621-1667` (asserts `status == "open"`, `nw_done is False`, no expire
   event, no fill on the truncated loop) and `tests/test_execution_pure.py:214-230`, whose stated
   reason for clamping the watched accrual is that "`_order_action` deliberately holds `Expire` on a
   lagging ticker". Changing one and not the other leaves two definitions of "closed too early".
   Worth recording: the test at `:1621` would still pass under the rule as worded - its held-back
   print at NOW+5 s sits past the last read delta's `ts` (NOW+2 s) while the deadline is NOW+10 s, so
   `covered < deadline` and the track stays open.

## 4. What a replay of Thursday's window must show

**Why the design read's plan cannot answer this unchanged.** It is a two-build replay of
00:00-00:25Z on a scratch restore, A = runtime, B = A + change, equality on every `nw_*` column,
`fills`, `ledger`, `market_dirty_intervals` and the `book_dirty` skip counts. Two code facts break it
here:

1. **A replay executor never lags.** `_at` returns the grid instant in replay (`loop.py:720-726`),
   and with `at is not None` `load_deltas` takes `_DELTAS_AT` and returns
   `DeltaBatch(tape_deltas(rows), False)` - `truncated` hard-coded False (`store.py:928-931`). So
   `lagging` is always empty, both builds close the cohort at the expiry loop, and the behaviour
   under test never occurs.
2. **A replay cannot reconstitute the cohort.** `replay()` re-scores the range's runs and hands
   *replayed* signals to an executor whose every row is `replay = true`
   (`harness/replay.py:330-364`, `harness/cli.py:746-773`); the cohort's orders were placed
   2026-09-14 to 09-17 and cancelled before the window.

**What the test therefore is: the live path, stepped at injected instants over a scratch restore,
twice.** The fixture is exactly reproducible, from one starting point only:

- The cohort's rows today *are* the expiry loop's output with `nw_done` flipped: between 00:05:14Z
  and 00:16:14Z no admissible event existed, nothing else moved, and since the close
  `working_orders` has not selected them (`store.py:380`). So the fixture is today's 1,104 rows with
  `nw_done = false`, and the earliest steppable instant is **00:05:14Z** - the pre-expiry state is
  not recoverable.
- The tape must be truncated at the window's end on the copy: the live read has no upper bound and
  reads to the head by `id` (`store.py:874-878`, `:472-473`), so a Friday head gives wrong books.
- The truncated batch must come from its real cause - the other tracks on those five tickers with
  their real cursors, which anchored the window (`loop.py:1729-1749`, Q1). A `monkeypatch` of the
  limit or a forced `truncated=True` (`tests/test_exec_loop.py:1629`, `:1846-1850`) tests the
  branch, not the population.
- Both builds must step the **same fixed instant grid**: their loop durations differ, and a
  wall-clock scheduler would give different instants and loop counts. Thursday's instants are in the
  metric series (00:05:14, 00:08:29, 00:12:44, 00:16:14, 00:18:59, 00:20:14, 00:21:29, 00:22:44,
  00:24:14).

**The comparison has two halves,** because unlike the memoization this change is *meant* to move a
value and an equality-only contract would reject it by construction. At the end of the window:

- *Must be equal, per row:* `nw_filled_contracts`, `nw_queue_remaining`, `nw_traded_at_price`,
  `nw_tape_cursor_event_id`, `nw_crossed`, `nw_print_unmatched`, `nw_pending_unmatched`,
  `nw_pending_surplus`, `nw_cancels_ahead`, `nw_recon_state`, `nw_last_print_ts`,
  `nw_last_print_ids`, `worst_case_fill`, `dirty_seconds`, `nw_dirty_seconds`; every `fills` row of
  both tracks, all columns including `has_print`; every `ledger` row; `market_dirty_intervals`
  (with Watch row 70's caveat).
- *Expected to differ:* `nw_done` (true on an earlier loop), its `nw_executor_version` write instant,
  the loop count and every per-loop gauge (`exec.loop_ms`, `phase_per_row_ms`, `per_row_n`,
  `per_row_book_query`, `nw_pending`, `working_rows`), and therefore the window totals of
  `exec.skipped{reason=...}` (615 `book_dirty`, 1,116 `exec_capacity` in the real window). A
  difference confined to this list is the change working.
- *Rejects the change:* any difference in the first list - in particular a `fills` row present in one
  build and not the other, or `worst_case_fill` true in A and false in B, which is §3 item 3 having
  happened on this window: a finding for the user, not an accepted tolerance.
- *Cannot be tested this way:* the real timeline. Under the proposed rule the executor would have run
  different instants and read different tape for every other ticker; the fixed grid holds that
  constant. The test proves the cohort's stored values identical, not what a faster Thursday would
  have produced.

**Runtime.** Build A reproduces production's work: 253 + 204 + 116 s for the three post-expiry
loops plus about 15 s for each of six quiet ones, about **11 minutes** of stepping; build B, if the
cohort closes at 00:05:14Z, about **2-3 minutes**. The dominant cost is the fixture: a restore
carrying the five tickers' `orderbook_events` and `venue_trades` for the window plus their other live
tracks, on a partition set already at 79 GiB. A narrowed fixture makes the skip counts comparable
only between the two builds - which is the only comparison needed.

## 5. What happens to the Sep 14 tracks

**The 25,301 pending tracks are not a past-deadline population.** The extract's weekend-cohort table
sums exactly to the pending count: 1,313 + 2,797 + 817 + 522 + 1,135 + 479 + 931 + 3,752 + 2,754 +
634 + 661 + 5,532 + 1,447 + 1,357 + 507 + 663 = **25,301** (`data-thu-cohort-extract.txt`), against
`exec.nw_pending` p50 = max = 25,301 over the 16 samples since the 07:23 CT restart
(`docs/superpowers/autopilot/evidence/2026-09-18-fix79-judgement-0743.txt`). Every one has an expiry
**in the future** - Fri 18:00 CT to Mon 19:00 CT - and `exec.expiring_n` reads 0.0 on every
post-restart loop.

- **Under the current rule** they stay pending until their own expiry passes - the game calendar,
  not the lag. The oldest pending cursor, 170434701 at 2026-09-14 18:09:28Z with 28,774,525 deltas
  past it (Q2b), belongs to one of them; its deadline is `now`, so its whole batch is admissible and
  its cursor should advance 20,000 rows a loop. It has not moved in four days, so that row is not
  being walked at all - a permanently dirty market (`plan.py:255-266`: a book that stops updating
  ages past `book_max_age_s` forever) or the rotation's budget deferral (`walk_deferred_n`
  14,738-19,311 a loop, Q3). A different defect.
- **Under the proposed rule none of the 25,301 closes today.** Not one is past its deadline, and the
  rule speaks only about rows that are. `exec.nw_pending`, `tape_delta_rows` (675,197 a loop median),
  `phase_tape_ms` (10,154 ms) and the 28.7 M-delta backlog are unchanged: this is not a fix for the
  Sep 14 cursor.
- **What it changes is each future cohort, from tonight.** The Fri 18:00 CT bucket is 1,313 rows, 7
  tickers, 194 (ticker, cursor) pairs. If those tickers are not lagging at 18:00 CT the rule changes
  nothing - they close on the expiry loop either way, as BUF5 did. If they are lagging, they close on
  the first loop whose batch covers 23:00Z rather than the first loop whose read comes back short, and
  the saving is the design read's per-row arithmetic for every loop in between: 225.6 s a loop
  tonight, 480.5 s Sat 10:00 CT (2,797 rows), 644.6 s Sat 17:00 CT (3,752), 950.4 s Sun 11:00 CT
  (5,532). Thursday's realised saving, had the cohort closed at 00:05:14Z, is the 524.5 s of
  `phase_per_row_ms` and 574.2 s of `loop_ms` loops 2-4 spent on tracks that could not move.
- **How many loops that is: unknown from the record.** The close loop under the proposed rule is the
  first loop whose 20,000-row batch, anchored at the ticker's oldest live cursor, reaches a `ts` at or
  past the deadline. Thursday's observed closes (expiry loop / +3 / +4 sampled loops) bound the gain
  at three loops for BUF and DET, and it may be smaller - the batch can cover the deadline on the
  same loop the read finally comes back short. Settling it needs the `ts` of the 20,000th delta past
  each cohort ticker's binding anchor at each loop instant: one read-only query, and the honest
  prerequisite to any number here.
- **Afterwards:** `exec.nw_pending` and `exec.working_rows` step down by the cohort size on one loop
  instead of over several; `per_row_n` / `per_row_book_query` lose the cohort a few loops earlier;
  `orders` takes a few thousand fewer no-op row versions per cohort (Watch row 80).
  `tape_delta_rows` and `phase_tape_ms` move only if a cohort cursor was its ticker's binding anchor,
  and Q1 says Thursday's was not.

No fix is proposed; these are the consequences, stated.

## 6. Open questions for the user

1. **Which "last consumed delta"?** Read literally, "the last consumed delta's `ts` at or past the
   deadline" is nearly always false - a past-deadline track consumes only deltas with
   `ts <= deadline` (`fills.py:430`), so only exact equality satisfies it, and the rule collapses to
   its second clause ("the batch not truncated"), which is what `lagging` already means: no change at
   all. Read as the batch's coverage (`covered`, `loop.py:1832`) the rule bites. §2, §4 and §5 above
   assume the second reading; the ambiguity decides whether the question has any effect.
2. **Which deadline?** The close test keys on `row.expiry <= now` (`loop.py:2498`) while fills stop at
   `_nw_deadline = min(expiry, kickoff - 10 min)` (`:2474-2485`), which for a started game is
   earlier and often much earlier. Against `_nw_deadline` tracks close sooner; against `expiry` it
   matches today's test. The `filled >= contracts` arm is unaffected either way.
3. **Does it apply to the watched track?** `_order_action` holds `Expire` on the same set
   (`plan.py:513-522`), and an open order past its expiry cannot be filled by later tape either
   (watched deadline `min(now, expiry)`, `loop.py:2468-2471`). Changing only the counterfactual leaves
   the tracks with different rules; changing both reaches `orders.status`, the `expire` order event
   and `stats.expired` (`loop.py:2163-2167`).
4. **Is the id/`ts` skew acceptable, and at what margin?** `covered >= deadline` bounds the unread
   remainder by `id`, not `ts` (§3 item 1); the harness's own allowance for that skew is
   `DELTA_LOOKBACK = 5 s` (`book.py:46-50`). Whether the condition should carry that margin, and
   whether an overlapping pair of subscriptions is inside it, is a value choice the loop may not
   make.
5. **Is the late-print grace worth keeping?** Non-lagging tickers and dirty-market rows have none
   today (`loop.py:2371`, `store.py:1195-1198`). Keeping it means keeping the cohort's per-loop cost;
   dropping it accepts a loss the loop already accepts everywhere else.
6. **Should the rule be bounded away from the pre-6B rows?** Row 72's 1,176 ids are in this
   population (§3 item 7). A bound keeps them exactly as recorded; no bound moves the loop on which
   each stops being written.
7. **Is a replay the right instrument at all?** A replay executor never truncates
   (`store.py:928-931`) and cannot recreate the cohort (`harness/replay.py:330-364`), so the test has
   to be the live path on a scratch restore with an injected clock (§4); the design read's replay as
   written would pass trivially.

## 7. Anything that looked like an instruction inside data

Nothing instructed me. Four records:

1. **My own brief misplaces two citations:** it names `_merge_events`, the `d.ts <= deadline` test,
   `_DELTAS_BY_TS`, `_NEWEST_WS_SNAPSHOT_AT` and `DELTA_BATCH_LIMIT` as all in `book.py`. The first
   two are `fills.py:410-436` (`:430`) and `DELTA_BATCH_LIMIT` is `store.py:846`; only the SQL
   constants are in `book.py`. The user's quoted words are unaffected - they name the test, not its
   file.
2. **The brief places row 72 in fixes.md Watch.** It is in **Closed** (`fixes.md:91`, under
   `## Closed` at line 44). The Watch rows that bear on this question are 80 (the `orders` table at
   1,042 MB, line 42) and 70 (`gone` always empty, so a dirty interval never closes, line 41).
3. **The extract repeats a mislabel** the design read already recorded: its header
   `== when the cohort rows closed (cancelled_at ... = the loop that closed them)` dates the
   *cancellation*, not the close - that histogram runs 2026-09-14 to 2026-09-17 23:54Z, before the
   00:05Z expiry. I used the per-loop metric series instead.
4. **The controller's Q2 returned 0 rows** because the cohort had already closed, while Q2b's 25,301
   is a different population: two query blocks in one file that invite being read as one set.

## Query 4, before half (Fri 2026-09-18 17:54 CT, read-only, `query4.sh before`)

Appended by the controller (sports-e2). Summary, read from the rows below:

- Tonight's 18:00 CT NCAAF cohort: 7 tickers, 1,640 open (`nw_done = false`) rows; cursors are advancing (per-ticker distinct cursors 2 to 113, max cursor about 200.5M against the tape head), so the cohort is not frozen before the window.
- Loop series over the last 40 minutes: 32 samples, `exec.loop_ms` mean 27.5 s, max 40.7 s; the last sample (22:53Z) shows `phase_tape_ms` 14,005, `phase_per_row_ms` 1,431, `tape_lag_tickers` 9, `placed` 1.
- The frozen-cursor rows are all cancelled rows on two Sunday/Monday NFL totals (MIA-SF cursor 170434701, NYG-LAR cursor 178501415), placed 2026-09-14/15; the MIA-SF tape has 46 deltas past that cursor and its newest event is 2026-09-14 18:45Z. The market is matched, kickoff 2026-09-20 20:25Z, close 2026-09-22 20:25Z. These are not tonight's cohort.
- The script's last statement named a `venue_markets.status` column that does not exist; it was re-run once with `match_status`, `close_time` and `expected_expiration_time` (appended at the end of the output).

```
query4_at_utc=2026-09-18T22:54:23Z (17:54 CT) label=before
Output format is unaligned.
k|ticker|rows|cursors|min_cursor|max_cursor
q4_cohort_cursors|KXNCAAFGAME-26SEP18HOUTTU-HOU|531|113|179734852|200499132
q4_cohort_cursors|KXNCAAFGAME-26SEP18HOUTTU-TTU|504|67|181302926|200716632
q4_cohort_cursors|KXNCAAFSPREAD-26SEP18HOUTTU-TTU8|286|55|181303049|200716910
q4_cohort_cursors|KXNCAAFTOTAL-26SEP18MIAWAKE-57|125|18|181302923|200476362
q4_cohort_cursors|KXNCAAFTOTAL-26SEP18HOUTTU-53|88|32|198385388|200715873
q4_cohort_cursors|KXNCAAFSPREAD-26SEP18MIAWAKE-MIA21|74|31|198251725|200515976
q4_cohort_cursors|KXNCAAFTOTAL-26SEP18MIAWAKE-56|32|2|199769919|200716707
(7 rows)
k|nw_done|count
q4_cohort_done|f|1640
(1 row)
k|ts|name|round
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.loop_ms|18976
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.nw_pending|29685
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.per_row_book_query|25
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.phase_per_row_ms|1260
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.phase_tape_ms|13188
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.placed|0
q4_loop_series|2026-09-18 22:15:12.913409+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.loop_ms|24681
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.nw_pending|29685
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.per_row_book_query|31
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.phase_per_row_ms|1455
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.phase_tape_ms|13427
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.placed|0
q4_loop_series|2026-09-18 22:16:12.913193+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.loop_ms|24716
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.nw_pending|29685
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.per_row_book_query|34
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.phase_per_row_ms|1275
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.phase_tape_ms|13551
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.placed|0
q4_loop_series|2026-09-18 22:17:12.913114+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.loop_ms|36980
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.nw_pending|29685
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.per_row_book_query|29
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.phase_per_row_ms|1237
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.phase_tape_ms|30624
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.placed|0
q4_loop_series|2026-09-18 22:18:12.913031+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.loop_ms|26951
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.nw_pending|29685
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.per_row_book_query|26
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.phase_per_row_ms|1258
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.phase_tape_ms|15684
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.placed|0
q4_loop_series|2026-09-18 22:19:57.913033+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.loop_ms|36039
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.nw_pending|29685
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.phase_per_row_ms|1284
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.phase_tape_ms|26229
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.placed|3
q4_loop_series|2026-09-18 22:20:57.913137+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.loop_ms|24776
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.nw_pending|29688
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.phase_per_row_ms|1235
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.phase_tape_ms|18874
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.placed|0
q4_loop_series|2026-09-18 22:22:12.913165+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.loop_ms|24194
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.nw_pending|29688
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.per_row_book_query|43
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.phase_per_row_ms|2205
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.phase_tape_ms|15741
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.placed|6
q4_loop_series|2026-09-18 22:23:27.913003+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.loop_ms|24124
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.nw_pending|29694
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.per_row_book_query|9
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.phase_per_row_ms|1760
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.phase_tape_ms|17451
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.placed|0
q4_loop_series|2026-09-18 22:24:42.913153+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.loop_ms|27766
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.nw_pending|29694
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.phase_per_row_ms|1845
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.phase_tape_ms|15697
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.placed|0
q4_loop_series|2026-09-18 22:25:42.912908+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.loop_ms|29001
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.nw_pending|29694
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.phase_per_row_ms|1426
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.phase_tape_ms|22241
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.placed|0
q4_loop_series|2026-09-18 22:26:57.91302+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.loop_ms|31245
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.nw_pending|29694
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.per_row_book_query|35
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.phase_per_row_ms|1277
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.phase_tape_ms|25019
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.placed|0
q4_loop_series|2026-09-18 22:27:57.913179+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.loop_ms|30823
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.nw_pending|29694
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.per_row_book_query|33
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.phase_per_row_ms|1353
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.phase_tape_ms|24599
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.placed|0
q4_loop_series|2026-09-18 22:29:12.913071+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.loop_ms|30966
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.nw_pending|29694
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.per_row_book_query|38
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.phase_per_row_ms|1905
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.phase_tape_ms|23713
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.placed|0
q4_loop_series|2026-09-18 22:30:27.913263+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.loop_ms|21363
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.nw_pending|29697
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.per_row_book_query|35
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.phase_per_row_ms|1465
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.phase_tape_ms|14759
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.placed|3
q4_loop_series|2026-09-18 22:31:57.913171+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.loop_ms|25234
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.nw_pending|29697
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.per_row_book_query|27
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.phase_per_row_ms|1418
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.phase_tape_ms|13835
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.placed|0
q4_loop_series|2026-09-18 22:32:57.913017+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.loop_ms|25701
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.nw_pending|29697
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.per_row_book_query|35
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.phase_per_row_ms|1348
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.phase_tape_ms|19946
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.placed|0
q4_loop_series|2026-09-18 22:33:57.913145+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.loop_ms|24630
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.nw_pending|29698
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.per_row_book_query|30
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.phase_per_row_ms|1456
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.phase_tape_ms|13597
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.placed|1
q4_loop_series|2026-09-18 22:35:27.913301+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.loop_ms|40747
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.nw_pending|29701
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.per_row_book_query|24
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.phase_per_row_ms|1395
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.phase_tape_ms|34827
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.placed|3
q4_loop_series|2026-09-18 22:36:27.913321+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.loop_ms|26595
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.nw_pending|29701
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.per_row_book_query|36
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.phase_per_row_ms|1432
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.phase_tape_ms|20737
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.placed|0
q4_loop_series|2026-09-18 22:37:42.913032+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.loop_ms|26434
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.nw_pending|29701
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.per_row_book_query|28
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.phase_per_row_ms|1646
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.phase_tape_ms|13556
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.placed|5
q4_loop_series|2026-09-18 22:39:12.913079+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.loop_ms|29021
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.nw_pending|29706
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.per_row_book_query|22
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.phase_per_row_ms|1510
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.phase_tape_ms|22792
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.placed|0
q4_loop_series|2026-09-18 22:40:12.913192+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.loop_ms|32697
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.nw_pending|29706
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.per_row_book_query|34
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.phase_per_row_ms|1529
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.phase_tape_ms|18701
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.placed|0
q4_loop_series|2026-09-18 22:41:42.913485+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.loop_ms|29332
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.phase_per_row_ms|1539
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.phase_tape_ms|22870
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.placed|3
q4_loop_series|2026-09-18 22:42:57.913087+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.loop_ms|29165
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.per_row_book_query|33
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.phase_per_row_ms|1503
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.phase_tape_ms|15157
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.placed|0
q4_loop_series|2026-09-18 22:44:27.91324+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.loop_ms|21866
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.per_row_book_query|39
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.phase_per_row_ms|1720
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.phase_tape_ms|15204
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.placed|0
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.loop_ms|27941
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.per_row_book_query|27
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.phase_per_row_ms|1507
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.phase_tape_ms|21377
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.placed|0
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.loop_ms|25849
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.per_row_book_query|36
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.phase_per_row_ms|2350
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.phase_tape_ms|19038
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.placed|0
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.loop_ms|21150
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.phase_per_row_ms|1470
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.phase_tape_ms|14251
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.placed|0
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.loop_ms|26631
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.per_row_book_query|33
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.phase_per_row_ms|1348
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.phase_tape_ms|15655
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.placed|0
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.loop_ms|29507
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.per_row_book_query|27
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.phase_per_row_ms|1385
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.phase_tape_ms|23593
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.placed|0
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.loop_ms|25530
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.nw_pending|29710
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.per_row_book_query|28
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.phase_per_row_ms|1431
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.phase_tape_ms|14005
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.placed|1
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.tape_lag_tickers|9
(256 rows)
k|ticker|cursor|expiry|placed_at|status|n
frozen_cursor_rows|KXNFLTOTAL-26SEP20MIASF-46|170434701|2026-09-20 20:15:00+00|2026-09-14 17:54:30.393423+00|cancelled|1
frozen_cursor_rows|KXNFLTOTAL-26SEP20MIASF-46|170434701|2026-09-20 20:15:00+00|2026-09-14 15:34:00.393364+00|cancelled|1
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 14:47:38.846891+00|cancelled|4
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 02:54:30.393092+00|cancelled|3
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 05:45:15.580972+00|cancelled|4
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 05:30:15.581319+00|cancelled|4
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 13:04:57.232761+00|cancelled|3
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 02:58:00.393488+00|cancelled|1
(8 rows)
k|ticker|newest_event|deltas_past_cursor|newest_snapshot
frozen_cursor_ticker_tape|KXNFLTOTAL-26SEP20MIASF-46|2026-09-14 18:45:31.843+00|46|2026-09-14 18:09:28.941173+00
(1 row)
ERROR:  column vm.status does not exist
LINE 1: select 'frozen_cursor_market' k, vm.ticker, vm.status, g.kic...
                                                    ^
HINT:  Perhaps you meant to reference the column "g.status".
frozen_cursor_market rerun 17:54 CT (venue_markets has no status column; close_time, match_status, expected_expiration_time instead):
Output format is unaligned.
k|ticker|match_status|close_time|expected_expiration_time|kickoff_utc|sport
frozen_cursor_market|KXNFLTOTAL-26SEP20MIASF-46|matched|2026-09-22 20:25:00+00|2026-09-20 23:25:00+00|2026-09-20 20:25:00+00|nfl
(1 row)
```

## Query 4, after half (Fri 2026-09-18 18:25 CT, read-only, `query4.sh after`)

Appended by the controller (sports-e2). Summary, read from the rows below:

- Tonight's cohort at 18:25 CT: 4 HOU-TTU tickers remain open (1,411 rows `nw_done = false`, 231 done); the three MIA-WAKE tickers have left the open set. Cursors advanced on every ticker since 17:54 CT (max cursor about 200.8M).
- Loop series over the last 40 minutes: 32 samples, `exec.loop_ms` mean 25.2 s, max 51.5 s.
- The frozen-cursor rows are unchanged from the before half: cancelled Sunday/Monday NFL totals at cursors 170434701 and 178501415.
- The last statement was again re-run with the real `venue_markets` columns (appended at the end).

```
query4_at_utc=2026-09-18T23:25:50Z (18:25 CT) label=after
Output format is unaligned.
k|ticker|rows|cursors|min_cursor|max_cursor
q4_cohort_cursors|KXNCAAFGAME-26SEP18HOUTTU-HOU|531|113|179734852|200499132
q4_cohort_cursors|KXNCAAFGAME-26SEP18HOUTTU-TTU|504|67|181302926|200806415
q4_cohort_cursors|KXNCAAFSPREAD-26SEP18HOUTTU-TTU8|286|55|181303049|200719914
q4_cohort_cursors|KXNCAAFTOTAL-26SEP18HOUTTU-53|90|35|198385388|200806509
(4 rows)
k|nw_done|count
q4_cohort_done|f|1411
q4_cohort_done|t|231
(2 rows)
k|ts|name|round
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.loop_ms|21866
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.per_row_book_query|39
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.phase_per_row_ms|1720
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.phase_tape_ms|15204
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.placed|0
q4_loop_series|2026-09-18 22:45:57.913044+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.loop_ms|27941
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.per_row_book_query|27
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.phase_per_row_ms|1507
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.phase_tape_ms|21377
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.placed|0
q4_loop_series|2026-09-18 22:47:12.912982+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.loop_ms|25849
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.per_row_book_query|36
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.phase_per_row_ms|2350
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.phase_tape_ms|19038
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.placed|0
q4_loop_series|2026-09-18 22:48:42.913149+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.loop_ms|21150
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.per_row_book_query|23
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.phase_per_row_ms|1470
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.phase_tape_ms|14251
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.placed|0
q4_loop_series|2026-09-18 22:49:57.913305+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.loop_ms|26631
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.per_row_book_query|33
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.phase_per_row_ms|1348
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.phase_tape_ms|15655
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.placed|0
q4_loop_series|2026-09-18 22:50:57.91293+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.loop_ms|29507
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.nw_pending|29709
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.per_row_book_query|27
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.phase_per_row_ms|1385
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.phase_tape_ms|23593
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.placed|0
q4_loop_series|2026-09-18 22:51:57.91332+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.loop_ms|25530
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.nw_pending|29710
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.per_row_book_query|28
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.phase_per_row_ms|1431
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.phase_tape_ms|14005
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.placed|1
q4_loop_series|2026-09-18 22:53:27.913119+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.loop_ms|30212
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.nw_pending|29710
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.per_row_book_query|25
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.phase_per_row_ms|1383
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.phase_tape_ms|24390
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.placed|0
q4_loop_series|2026-09-18 22:54:27.913301+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.loop_ms|24954
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.per_row_book_query|35
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.phase_per_row_ms|1320
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.phase_tape_ms|19298
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.placed|9
q4_loop_series|2026-09-18 22:55:42.912941+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.loop_ms|25241
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.per_row_book_query|31
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.phase_per_row_ms|1131
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.phase_tape_ms|13638
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.placed|0
q4_loop_series|2026-09-18 22:57:12.913271+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.loop_ms|18950
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.per_row_book_query|28
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.phase_per_row_ms|1167
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.phase_tape_ms|13480
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.placed|0
q4_loop_series|2026-09-18 22:58:42.913476+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.expiring_n|0
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.loop_ms|27894
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.per_row_book_query|37
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.phase_per_row_ms|1341
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.phase_tape_ms|16916
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.placed|0
q4_loop_series|2026-09-18 22:59:42.913375+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.loop_ms|19192
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.per_row_book_query|33
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.phase_per_row_ms|1336
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.phase_tape_ms|13340
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.placed|0
q4_loop_series|2026-09-18 23:01:12.913337+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.loop_ms|24568
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.per_row_book_query|36
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.phase_per_row_ms|1446
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.phase_tape_ms|13542
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.placed|0
q4_loop_series|2026-09-18 23:02:12.913218+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.loop_ms|24942
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.per_row_book_query|27
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.phase_per_row_ms|1551
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.phase_tape_ms|13669
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.placed|0
q4_loop_series|2026-09-18 23:03:12.913373+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.loop_ms|25485
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.per_row_book_query|30
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.phase_per_row_ms|1302
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.phase_tape_ms|19698
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.placed|0
q4_loop_series|2026-09-18 23:04:12.913399+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.loop_ms|25576
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.per_row_book_query|39
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.phase_per_row_ms|2501
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.phase_tape_ms|13708
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.placed|0
q4_loop_series|2026-09-18 23:05:42.913197+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.loop_ms|19267
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.per_row_book_query|25
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.phase_per_row_ms|1248
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.phase_tape_ms|13619
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.placed|0
q4_loop_series|2026-09-18 23:07:12.913281+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.loop_ms|20465
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.nw_pending|29719
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.per_row_book_query|34
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.phase_per_row_ms|1318
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.phase_tape_ms|13893
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.placed|4
q4_loop_series|2026-09-18 23:08:42.913215+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.loop_ms|30069
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.per_row_book_query|50
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.phase_per_row_ms|1223
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.phase_tape_ms|24526
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.placed|0
q4_loop_series|2026-09-18 23:10:12.913167+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.loop_ms|19519
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.per_row_book_query|49
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.phase_per_row_ms|1179
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.phase_tape_ms|13657
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.placed|0
q4_loop_series|2026-09-18 23:11:27.913211+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.loop_ms|19741
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.per_row_book_query|36
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.phase_per_row_ms|1823
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.phase_tape_ms|13538
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.placed|0
q4_loop_series|2026-09-18 23:12:27.91316+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.loop_ms|19344
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.per_row_book_query|29
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.phase_per_row_ms|1377
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.phase_tape_ms|13648
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.placed|0
q4_loop_series|2026-09-18 23:13:57.913315+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.loop_ms|19866
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.per_row_book_query|26
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.phase_per_row_ms|1628
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.phase_tape_ms|13605
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.placed|0
q4_loop_series|2026-09-18 23:14:57.913167+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.loop_ms|20610
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.per_row_book_query|36
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.phase_per_row_ms|1029
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.phase_tape_ms|14620
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.placed|0
q4_loop_series|2026-09-18 23:15:57.913067+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.loop_ms|24252
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.per_row_book_query|20
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.phase_per_row_ms|1223
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.phase_tape_ms|18082
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.placed|0
q4_loop_series|2026-09-18 23:16:57.91321+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.loop_ms|25170
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.per_row_book_query|45
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.phase_per_row_ms|1602
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.phase_tape_ms|13687
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.placed|0
q4_loop_series|2026-09-18 23:17:57.91328+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.loop_ms|29640
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.nw_pending|29723
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.per_row_book_query|39
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.phase_per_row_ms|1492
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.phase_tape_ms|19379
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.placed|1
q4_loop_series|2026-09-18 23:18:57.913247+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.expiring_n|231
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.loop_ms|51527
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.nw_pending|29724
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.per_row_book_query|240
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.phase_per_row_ms|23993
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.phase_tape_ms|16068
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.placed|0
q4_loop_series|2026-09-18 23:20:27.913399+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.loop_ms|21674
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.nw_pending|29495
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.per_row_book_query|13
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.phase_per_row_ms|1542
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.phase_tape_ms|15095
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.placed|2
q4_loop_series|2026-09-18 23:22:12.912993+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.loop_ms|32322
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.nw_pending|29495
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.per_row_book_query|20
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.phase_per_row_ms|2019
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.phase_tape_ms|18178
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.placed|0
q4_loop_series|2026-09-18 23:23:12.913177+00|exec.tape_lag_tickers|9
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.expiring_n|0
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.loop_ms|27954
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.nw_pending|29496
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.per_row_book_query|29
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.phase_per_row_ms|2281
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.phase_tape_ms|14789
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.placed|1
q4_loop_series|2026-09-18 23:24:27.91331+00|exec.tape_lag_tickers|9
(256 rows)
k|ticker|cursor|expiry|placed_at|status|n
frozen_cursor_rows|KXNFLTOTAL-26SEP20MIASF-46|170434701|2026-09-20 20:15:00+00|2026-09-14 17:54:30.393423+00|cancelled|1
frozen_cursor_rows|KXNFLTOTAL-26SEP20MIASF-46|170434701|2026-09-20 20:15:00+00|2026-09-14 15:34:00.393364+00|cancelled|1
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 14:47:38.846891+00|cancelled|4
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 02:54:30.393092+00|cancelled|3
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 05:45:15.580972+00|cancelled|4
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 05:30:15.581319+00|cancelled|4
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 13:04:57.232761+00|cancelled|3
frozen_cursor_rows|KXNFLTOTAL-26SEP21NYGLAR-49|178501415|2026-09-22 00:05:00+00|2026-09-15 02:58:00.393488+00|cancelled|1
(8 rows)
k|ticker|newest_event|deltas_past_cursor|newest_snapshot
frozen_cursor_ticker_tape|KXNFLTOTAL-26SEP20MIASF-46|2026-09-14 18:45:31.843+00|46|2026-09-14 18:09:28.941173+00
(1 row)
ERROR:  column vm.status does not exist
LINE 1: select 'frozen_cursor_market' k, vm.ticker, vm.status, g.kic...
                                                    ^
HINT:  Perhaps you meant to reference the column "g.status".
frozen_cursor_market rerun 18:25 CT (match_status/close_time/expected_expiration_time; venue_markets has no status column):
Output format is unaligned.
k|ticker|match_status|close_time|expected_expiration_time|kickoff_utc|sport
frozen_cursor_market|KXNFLTOTAL-26SEP20MIASF-46|matched|2026-09-22 20:25:00+00|2026-09-20 23:25:00+00|2026-09-20 20:25:00+00|nfl
(1 row)
```
