# Correction manifest (record)

Date 2026-09-11. The human half of `harness/corrections.py`: one `## C<n>` heading per entry in
`CORRECTIONS`, in the same order. The code is what the container reads; this is where the prose
goes. `tests/test_corrections.py::test_the_record_headings_equal_the_tuple_ids` asserts the two
agree, so an entry added to one and not the other fails the suite.

Protocol: `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` §"Amendment protocol".
Item 1 governs a measurement amendment; item 4 is the R1 invariant that no criterion, threshold,
family, grid, success threshold or cut-off moves without a dated user decision. Original rows
are never rewritten: a correction is re-scored by a replay that writes `replay = true` rows
beside the originals, and the `rescore_command` field carries the exact invocation.

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
