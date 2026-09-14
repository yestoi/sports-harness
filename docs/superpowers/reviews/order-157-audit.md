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
it is met, so an `unverifiable` verdict still says what was seen. Every query is written with the
**simulator's own matching rules**, never with a looser reading of the sentence that states it: a
hypothesis a print that could not have lifted us can satisfy would name a cause the evidence does
not support, which is the failure ruling IM-3 exists to prevent.

| Hypothesis | Query | Met when |
|---|---|---|
| (i) | decrements **on our side at our price, shrinking** (exactly `_apply_queue_delta`'s test), stamped at an instant a print that could have lifted us also carries | their sum is within 1 % of -6,376 |
| (ii) | gap rows (`orderbook_events_gaps`, or `kind = 'gap'` inline, deduplicated) **inside the resting interval and on the sid** an in-interval `kind = 'snapshot'` row anchored | a gap on that sid and an in-interval snapshot are both present |
| (iii) | prints that **could have lifted us** -- `hits()` (canonical taker side opposite ours) and `price_on_side() <= 0.45`, at or *through* our level -- before the earliest recorded fill, the whole resting interval when the record has no fill | their sum is 6,401 or more |

Three consequences of writing them this way, each pinned by a test:

- A print at 0.45 whose taker side is *ours* lifted a resting ask on the other side of the book
  and never touched us; it is not a queue collapse, however large. The literal "volume at 0.45,
  whoever the taker was" count is still reported, as `observed_volume_at_price_any_taker`, so the
  record shows what was on the tape; it never meets a hypothesis.
- A print at 0.44 that a taker-no lifted swept *through* our 0.45 bid and is exactly a queue
  collapse, although an equality test on the price would not see it.
- A decrement on the no side at 0.45 is a different level of a different queue (no 0.45 is yes
  0.55) and moves nothing of ours, so it cannot be the double count.

Hypothesis (ii)'s snapshot is deliberately the one *inside* the resting interval -- a re-anchor
while we rested -- and not the anchor every capsule carries at its own window's start, which
would make the hypothesis met by construction; its gap must be inside that interval too, and on
the anchor's `sid`, because a period capsule's gap slice spans the whole capsule window and gap
detection is subscription-level. Where no in-interval snapshot carries a `sid` there is nothing
to compare against, and the evidence dict says so in `sid_rule` rather than silently loosening.
Hypothesis (i)'s `met` test is on the decrement alone; the print volume stamped with it is
reported beside it as evidence rather than folded into the test, because 63.92 is itself a C0
recorded quantity.

**Hypothesis (ii) not being met is weak evidence, and should be read as such.** A capsule carries
at most **one** snapshot per ticker: `export_ws_tape` takes the newest anchoring snapshot with
`order by ts desc, id desc limit 1` (`harness/fixtures.py`), and the capsule appends that single
row. A re-anchor at 14:50 is therefore invisible in the file whenever any later snapshot exists --
and for an order capsule the window runs to `cancelled_at + 30 min`, so a later one usually does.
`observed_snapshots = 0` means "this capsule's one snapshot is outside the resting interval", not
"no re-anchor happened". Ruling (ii) out properly needs a tape read the capsule does not carry.

## Verdicts

- `validated` -- the repaired simulation reproduces the recorded fills within one contract.
- `corrected` -- it differs, and one hypothesis's stated expected counts are met.
- `unverifiable` -- the tape does not cover the interval and nothing anchors it (the 6A
  manifest's `unverifiable_slices` is that call's input), **or** it differs and no hypothesis's
  counts are met. The second case is the reconciliation's "requires tape audit", not a causal
  story the evidence does not support.

The manifest test is applied first and is decisive: a capsule whose manifest lists any
`unverifiable_slices` entry is `unverifiable` without a replay, and the three hypotheses' counts
are reported anyway. Because no replay is run on that path, a gated result carries **no** simulated
quantities: both `repaired_filled` and `repaired_queue` are `null`, so a gated verdict can never be
misread as a simulated fill of nothing (which is also the meaningful answer in the `corrected`
case, and must stay distinguishable from it). One consequence is worth stating plainly, because it bears on hypothesis
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
