# Correction manifest (record)

Date 2026-09-11. The human half of `harness/corrections.py`: one `## C<n>` heading per entry in
`CORRECTIONS`, in the same order. The code is what the container reads; this is where the prose
goes. `tests/test_corrections.py::test_the_record_headings_equal_the_tuple_ids` asserts the two
agree, so an entry added to one and not the other fails the suite.

Protocol: `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` §"Amendment protocol".
Item 1 governs a measurement amendment; item 4 is the R1 invariant that no criterion, threshold,
family, grid, success threshold or cut-off moves without a dated user decision. Original rows
are never rewritten: a correction is re-scored by one of two instruments (§0.12), named per
correction below. `harness rescore --from-order A --to-order B --correction <ids>` is the
instrument for an **order-scoped** correction -- one whose repair changes what a given order's
simulation produces -- and writes `order_rescores` rows beside the originals. `harness replay
--from-run A --to-run B --population range` is the instrument for a **range-scoped** one -- a
repair that changes which orders exist at all -- and its `replay = true` rows are the estimate.
The `rescore_command` field carries the exact invocation either way.

## C0 — Baseline: the paper record as measured before any 6B repair

C0 corrects nothing. It records what the measurement was, so that every later entry has
something to be "before" of.

- **Code version:** `6eed2d8` through `7c3d555`; deployed at `7c3d555`.
- **Measurement version:** `EXECUTOR_VERSION` 4.4 throughout (`harness/execution/__init__.py:4`).
- **Strategies in force:** the six phase-2 registered variants plus `sharp_two_sided` (U2),
  seven `strategy_variants.variant_id` values in `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** the six distinct `orders.config_hash` values the paper
  run placed under, in `harness.corrections.CONFIG_HASHES_C0`. These are the executor's own
  configuration hashes, not the 12-hex variant ids `config_history` is keyed by; the capsule copies
  that table in full and this manifest does not read it.
- **Affected ranges:** all orders and all runs through the 6B deploy.
- **Eligible measurements:** raw historical cleanliness and counts, labelled retrospective.
  These are statements about what the record contains, not about what the strategy earned.
- **Excluded measurements:** none yet. 6B's first entry is where exclusions begin.
- **Re-scoring:** none. The baseline is the record.

### Known limitations of the baseline

These are the defects `tests/test_execution_regressions.py` pins as strict xfails. They are
listed here so a reader of a C0-era number knows what it does and does not support. None of them
is repaired in 6A.

1. A per-market stream that legitimately skips a subscription sequence number marks the book
   dirty, which inflates `dirty_minutes` and depresses the clean-book share criterion 1 reads.
2. A print and the book delta that records the same trade are counted as two events, so a queue
   drains twice as fast as the tape says and fills appear that the queue does not support.
3. After a gap recovery the print watermark is stale while the delta cursor has advanced, so a
   trade from inside the gap fills against the post-recovery queue.
4. A cancelled order keeps accruing dirty seconds on its own counter for its counterfactual's
   sake.
5. The watched track is simulated to the loop instant rather than to the order's expiry, so an
   order can fill after it stopped existing.
6. A rejected latest verdict cancels a resting order but does not stop a new one being placed.

## C1 — Subscription continuity: the per-book seq check read multiplexed interleaving as a lost frame

C1 reads book continuity from the subscription's own gap rows (`WsSink._check_seq`'s sid-level
`gap` rows under the sentinel `ticker = ""`), an immutable `anchor_as_of` compared against the
newest `operator_events(kind = 'ws_connect')`, plus a consumer-side probe for the delta the
recorder cannot see (`id > :cursor and ts >= :lower` with `lower = as_of - DELTA_LOOKBACK`),
instead of the per-object `int(seq) != self.seq + 1` test that read ordinary multiplexed
interleaving as a lost frame. `book_at` is unchanged.

- **Code version:** `7c3d555` through `<sha>`; deployed at `<sha>` (Amendment 6, one deploy for
  C1-C6).
- **Measurement version:** `EXECUTOR_VERSION` 4.4 before, 4.5 after (`harness/execution/__init__.py`).
- **Strategies in force:** unchanged, `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** `harness.corrections.CONFIG_HASHES_C1`, filled by the
  controller at merge.
- **Affected ranges:** `<filled at merge>` (numeric order-id and run-id boundary; the boundary is
  `boundary_order_id`, with `boundary_fill_id`, `boundary_event_id` and `boundary_ledger_id`
  recorded in Amendment 6).
- **Eligible measurements:** post-boundary `book_source`/`dirty_minutes` classifications, read
  from the subscription's own gap rows.
- **Excluded measurements:** pre-boundary `book_source`/`dirty_minutes` classifications, which
  counted ordinary interleaving as dirty.
- **Re-scoring:** `harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]`.

## C2 — Recovery anchoring: the print floor was not anchored with the queue

C2 sets the `print_floor`, the §1.3 ledger and the delta cursor together from the anchoring book
on both re-anchor branches (the recovery branch and the no-book branch), instead of leaving
`last_print_ts` behind while the queue and cursor advance, which let a trade from inside a gap
apply twice against a newly anchored queue.

- **Code version:** `7c3d555` through `<sha>`; deployed at `<sha>` (Amendment 6, one deploy for
  C1-C6).
- **Measurement version:** `EXECUTOR_VERSION` 4.4 before, 4.5 after.
- **Strategies in force:** unchanged, `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** `harness.corrections.CONFIG_HASHES_C2`, filled by the
  controller at merge.
- **Affected ranges:** `<filled at merge>`.
- **Eligible measurements:** post-boundary `filled_contracts`, anchored with the queue on both
  re-anchor branches.
- **Excluded measurements:** pre-boundary `filled_contracts` on any order that recovered from a
  dirty stretch.
- **Re-scoring:** `harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]`.

## C3 — Trade and decrement reconciliation: a print and its own delta moved the queue twice

C3 replaces the ordering assumption in `_merge_events`/`_apply_queue_delta` with a two-sided
ledger bucketed by timestamp within `store.PRINT_LOOKBACK`: a decrement is claimable only by a
print inside that horizon, so a print and the delta that reports the same trade move the queue
once, not twice.

- **Code version:** `7c3d555` through `<sha>`; deployed at `<sha>` (Amendment 6, one deploy for
  C1-C6).
- **Measurement version:** `EXECUTOR_VERSION` 4.4 before, 4.5 after.
- **Strategies in force:** unchanged, `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** `harness.corrections.CONFIG_HASHES_C3`, filled by the
  controller at merge.
- **Affected ranges:** `<filled at merge>`.
- **Eligible measurements:** post-boundary `queue_remaining`, reconciled against a trade inside
  its own horizon.
- **Excluded measurements:** pre-boundary `queue_remaining` and `traded_at_price`; the two are
  not comparable across the boundary, and `traded_at_price` is null afterwards. Because
  `harness/report/tables.py`'s queue-consumption derivation reads that now-NULL column,
  post-boundary orders drop out of that table until 6C's report work reads the reconciliation
  columns instead (Task 3's whole-branch note).
- **Re-scoring:** `harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]`.

## C4 — Expiry clamp and rejected-signal placement

C4 clamps both fill paths -- the walk and the entry cross taken before it -- to the order's own
expiry, and adds the rejected-verdict test to `_intent_actions` after the data-quality tests and
before the capacity tests, so a rejected verdict never masquerades as a data skip and never
consumes capacity. This is the pre/post-boundary difference in `crossed`/`worst_case_fill`
(Task 5's review, ledger 2026-09-13): pre-boundary orders could carry a fill stamped after their
own expiry; post-boundary the expiry clamp forbids it and a rejected latest signal re-attributes
the skip reason.

- **Code version:** `7c3d555` through `<sha>`; deployed at `<sha>` (Amendment 6, one deploy for
  C1-C6).
- **Measurement version:** `EXECUTOR_VERSION` 4.4 before, 4.5 after.
- **Strategies in force:** unchanged, `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** `harness.corrections.CONFIG_HASHES_C4`, filled by the
  controller at merge.
- **Affected ranges:** `<filled at merge>`.
- **Eligible measurements:** post-boundary fills clamped to the order's own expiry, and
  skip-reason counts re-attributed ahead of capacity.
- **Excluded measurements:** pre-boundary fills stamped after their order's expiry, and
  pre-boundary skip-reason counts, which C4 re-attributes.
- **Re-scoring:** `harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]`.

## C5 — Dirty-time scope, observation coverage and counterfactual backoff

C5 scopes dirty accrual to `store.OPEN_STATUSES` on the watched column, moves the
counterfactual's accrual to its own `orders.nw_dirty_seconds` column, clamps every increment to
the remaining watched interval, records dirtiness and observation coverage per market, and
retries an unreadable counterfactual on an elapsed-time backoff (`exec_period_s` doubling to
`NW_RETRY_MAX_S = 3600`, reset on a successful read) without ever closing the track.

- **Code version:** `7c3d555` through `<sha>`; deployed at `<sha>` (Amendment 6, one deploy for
  C1-C6).
- **Measurement version:** `EXECUTOR_VERSION` 4.4 before, 4.5 after.
- **Strategies in force:** unchanged, `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** `harness.corrections.CONFIG_HASHES_C5`, filled by the
  controller at merge.
- **Affected ranges:** `<filled at merge>`.
- **Eligible measurements:** post-boundary `dirty_seconds`/`dirty_minutes` scoped to the watched
  resting interval, with the counterfactual's own `nw_dirty_seconds` column and a bounded backoff
  on unreadable tickers.
- **Excluded measurements:** pre-boundary `dirty_seconds`/`dirty_minutes` on any cancelled or
  expired order.
- **Re-scoring:** `harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]`.

## C6 — Capacity-equivalent baseline replay

C6 replays the range's own executed population -- resolved from the placed-order chain of the
range, not from today's `Settings.exec_variants` -- under one shared capacity counter, instead of
`replay()`'s single-variant executor scoring one variant against live capacity shared by many.
Spec §1.6's parenthetical "(config_history by the range's runs)" is not implementable:
`config_history` has no run column. **That placed-order-chain population is a lower bound on the
executed set**: a configured variant that placed nothing in the range is invisible to it, and the
replay re-scores under the repaired simulator, so such a variant may place after the repairs and
its absence understates contention. **A per-run exact comparison is out of scope**: it would
refuse a range where one variant merely placed nothing in a single run, which is safe and
frequent, so it is not attempted here.

- **Code version:** `7c3d555` through `<sha>`; deployed at `<sha>` (Amendment 6, one deploy for
  C1-C6).
- **Measurement version:** `EXECUTOR_VERSION` 4.4 before, 4.5 after.
- **Strategies in force:** unchanged, `harness.corrections.VARIANT_IDS_C0`.
- **Executor configurations in force:** `harness.corrections.CONFIG_HASHES_C6`, filled by the
  controller at merge.
- **Affected ranges:** `<filled at merge>` (run-id range; range-scoped, so `affected_run_id_range`
  is the binding one).
- **Eligible measurements:** a range replay under the executor configuration in force over that
  range, sharing one capacity counter.
- **Excluded measurements:** pre-boundary single-variant replay counts as a baseline for a
  shared-capacity live loop. The population is resolved from the placed-order chain of the range
  (spec §1.6's parenthesis is not implementable), and that population is a **lower bound** on the
  executed set (a configured variant that placed nothing in the range is invisible); a per-run
  exact comparison is out of scope.
- **Re-scoring:** `harness replay --from-run <a> --to-run <b> --population range`.
