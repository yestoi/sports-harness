# Order 157: audit of the roadmap's one validated fill

Undated by design: the run date is the controller's, and this record is written before the run
so that the verdict cannot be chosen after seeing the answer.

Order 157 (`KXNCAAFTOTAL-26SEP12MTUMRSH-59`, yes 0.45 x 87) was placed 14:36:47Z on 2026-09-08
and cancelled 15:12:06Z with reason `fair_stale`. It records `queue_ahead_at_place` 6,401,
`filled_contracts` 38.92, `traded_at_price` 63.92 and `queue_remaining` 0, with two
`queue_model` fills (25 and 13.92) at one print timestamp, 15:07:15.332Z, under six
`last_print_ids`.

## Method

`harness audit-order --capsule <dir> --order 157` reads the 6A capsule -- tape, prints,
snapshots, fills, events, watch samples, ledger -- with no database and no NAS access, replays
it under the repaired simulator (C1-C5), and compares against the **recorded** quantities. The
comparison is not against a C0 code path: C1-C5 remove that path from the tree, so the capsule
plus C0's recorded values are the reference (ruling IM-2).

The replay is the queue arithmetic alone: `simulate_fills` is handed the capsule's prints and
deltas for the order's own ticker, the order's recorded `queue_ahead_at_place`, and the deadline
the track actually stopped at (the cancel, or the expiry when there was none). No book is handed
in, because a capsule's anchoring snapshot is the book at the capsule window's start rather than
at ours; the only thing that costs is F41's worst-case `snapshot_cross` fill, which is never a
position and is not the quantity compared here.

Every count is taken over the order's own resting interval, `(placed_at, deadline]`, and over its
own ticker: a period capsule carries several tickers in one file, and a print an hour after the
cancel says nothing about the queue this order rested in.

## The three hypotheses and what each predicts

1. **The equal-timestamp double count.** A decrement near -6,376 at 0.45 stamped 15:07:15.332Z,
   beside prints summing to 63.92 across the six recorded `last_print_ids`.
2. **A recovery anchoring error.** A `gap` row on the anchor's sid, and a snapshot between
   14:36:47Z and 15:07:15Z.
3. **A genuine queue collapse.** Prints of 6,401 or more at 0.45 before the fills.

Each is a query over the capsule's own files, and each reports its observed count whether or not
it is met, so an `unverifiable` verdict still says what was seen:

| Hypothesis | Query | Met when |
|---|---|---|
| (i) | decrements at 0.45 on our side stamped at an instant a print at 0.45 also carries | their sum is within 1 % of -6,376 |
| (ii) | the capsule's gap rows (`orderbook_events_gaps`, or `kind = 'gap'` inline) and its `kind = 'snapshot'` rows inside the resting interval | both are present |
| (iii) | prints at 0.45 before the earliest recorded fill (the whole resting interval when the record has no fill) | their sum is 6,401 or more |

Hypothesis (ii)'s snapshot is deliberately the one *inside* the resting interval -- a re-anchor
while we rested -- and not the anchor every capsule carries at its own window's start, which
would make the hypothesis met by construction. Hypothesis (i)'s `met` test is on the decrement
alone; the print volume stamped with it is reported beside it as evidence rather than folded
into the test, because 63.92 is itself a C0 recorded quantity.

## Verdicts

- `validated` -- the repaired simulation reproduces the recorded fills within one contract.
- `corrected` -- it differs, and one hypothesis's stated expected counts are met.
- `unverifiable` -- the tape does not cover the interval and nothing anchors it (the 6A
  manifest's `unverifiable_slices` is that call's input), **or** it differs and no hypothesis's
  counts are met. The second case is the reconciliation's "requires tape audit", not a causal
  story the evidence does not support.

The manifest test is applied first and is decisive: a capsule whose manifest lists any
`unverifiable_slices` entry is `unverifiable` without a replay, and the three hypotheses' counts
are reported anyway. One consequence is worth stating plainly, because it bears on hypothesis
(ii): 6A marks a window containing **any** `gap` row as an unverifiable slice
(`harness/capsule.py`'s `unverifiable`), so a capsule whose tape has the gap that hypothesis (ii)
predicts is ruled `unverifiable` on the manifest rather than `corrected` on the hypothesis. That
is the conservative direction -- a lost frame means the tape genuinely does not cover the
interval -- but it means a `corrected`/(ii) verdict is not reachable from a gapped capsule, and
the reader of a `unverifiable` result should look at `evidence["ii"]`'s counts and at the
manifest's own entries before concluding that no anchoring error occurred.

## Result

*Unfilled. The controller runs the command in the quiet window and pastes the verdict, the
observed count for each hypothesis, and the run date here and into
`harness/corrections.py`'s `ORDER_157_VERDICT`.*
