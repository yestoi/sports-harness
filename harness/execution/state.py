"""One track's simulation state, read off an `orders` row and written back to it.

`SimState` is the simulator's own shape (`harness/execution/fills.py`); this module is the one
place that knows which columns it lives in and how the watched and counterfactual tracks share
that shape under a prefix. It was `loop.py`'s until 6B §1.10 moved it out unchanged: the loop
owns the step, and the shape of a track's persisted state is a subject of its own that §1.3
extends with the reconciliation ledger.
"""

import json
from datetime import datetime
from decimal import Decimal

from harness.execution.book import ZERO
from harness.execution.fills import BUCKET_CAP, TRADE_ID_CAP, SimState


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _ts(value) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _number(value) -> Decimal | None:
    """A persisted contract quantity, distinguishing an absent column from a stored zero.

    A NULL column is a track that has never been written and defaults to `ZERO` at the call
    site; a stored `0` is a written track whose term happens to be empty and must read back as
    `0`. `or ZERO` cannot tell the two apart -- it turns any falsy value into the default -- and
    on this column the difference is between "no ledger yet" and "an empty ledger" (T1 review,
    minor 3).
    """
    return None if value is None else Decimal(value)


def recon_state_json(state: SimState) -> dict:
    """The bounded `jsonb` document one track's ledger persists as (§2).

    Decimals are written as strings and instants as ISO-8601, because a round trip through
    `json` would otherwise turn a contract count into a float. Every list is already bounded by
    the simulator; the slices here are a second, cheap guarantee that a column written by an
    older build cannot grow past the cap when this one writes it back.

    `prints` is the ledger's other side, added in round 1 (I5): print volume whose decrement has
    not arrived, stamped with each print's own instant so the horizon can age it exactly as it
    ages a bucket. The scalar `print_unmatched` column is its sum, derived on the way out like
    the two bucket sums.
    """
    return {"buckets": [[_iso(ts), kind, str(size)]
                        for ts, kind, size in state.buckets[-BUCKET_CAP:]],
            "prints": [[_iso(ts), str(size)] for ts, size in state.prints[-BUCKET_CAP:]],
            "trade_ids": [[_iso(ts), tid] for ts, tid in state.trade_ids[-TRADE_ID_CAP:]],
            "print_floor": _iso(state.print_floor)}


def recon_state_of(value) -> tuple[tuple, tuple, tuple, datetime | None]:
    """`(buckets, prints, trade_ids, print_floor)` off the column; a null column starts empty.

    A null is a pre-boundary order, whose ledger has never been written: it starts with an
    empty ledger rather than with anything inferred from `traded_at_price`, which is a different
    quantity (ruling CR-3). `_state_of` is what gives such an order its pre-6B print watermark
    back.

    The `prints` member is round 1's I5 addition, so this returns four values rather than the
    three the task brief published; nothing outside this module consumed it yet.
    """
    if not value:
        return (), (), (), None
    doc = value if isinstance(value, dict) else json.loads(value)
    buckets = tuple((_ts(ts), kind, Decimal(size)) for ts, kind, size in doc.get("buckets", ()))
    prints = tuple((_ts(ts), Decimal(size)) for ts, size in doc.get("prints", ()))
    trade_ids = tuple((_ts(ts), tid) for ts, tid in doc.get("trade_ids", ()))
    return buckets, prints, trade_ids, _ts(doc.get("print_floor"))


def _state_of(row, prefix: str) -> SimState:
    """The persisted `SimState` of one track, read off the order row.

    `traded_at_price` is deliberately not read: C0's charge-against quantity is not a ledger
    term, and a post-boundary order leaves that column null (ruling CR-3). The three scalar
    ledger columns are not read either: they are derived sums of the document beside them, which
    is what §2's invariant compares them with. Only `cancels_ahead` is genuinely persisted as a
    scalar, and a stored `0` there must read back as `0` -- see `_number`.

    **The transitional read (round 1, I3).** An order placed before the 6B deploy and still
    working at it has no `recon_state`, but it does have the pre-6B print watermark
    (`last_print_ts` / `last_print_ids`), and that watermark is the only record of which prints
    it has already applied. Without it the executor's next step re-reads the order's whole
    resting print history from `placed_at - PRINT_LOOKBACK` and applies every print of it a
    second time. So when `recon_state` is null and `last_print_ts` is not, the watermark becomes
    the new shape's floor and id set: nothing at or below `last_print_ts` may re-apply, and the
    ids recorded at exactly that instant are carried as seen. Nothing is written back to a pre-6B
    row beyond what the repaired writer already writes on its first step.
    """
    buckets, prints, trade_ids, print_floor = recon_state_of(
        getattr(row, f"{prefix}recon_state"))
    cancels = _number(getattr(row, f"{prefix}cancels_ahead"))
    watermark = getattr(row, f"{prefix}last_print_ts")
    if not getattr(row, f"{prefix}recon_state") and watermark is not None:
        print_floor = watermark
        trade_ids = tuple((watermark, str(tid))
                          for tid in getattr(row, f"{prefix}last_print_ids") or ())
    return SimState(
        queue_remaining=getattr(row, f"{prefix}queue_remaining"),
        filled_contracts=getattr(row, f"{prefix}filled_contracts"),
        cursor_event_id=getattr(row, f"{prefix}tape_cursor_event_id"),
        crossed=bool(getattr(row, f"{prefix}crossed")),
        cancels_ahead=ZERO if cancels is None else cancels,
        prints=prints, buckets=buckets, trade_ids=trade_ids, print_floor=print_floor)


def _state_columns(prefix: str, state: SimState) -> dict:
    """The columns one track's `SimState` is persisted in.

    The cursor is the last tape row the simulation actually consumed and nothing else. The
    cache's own head runs ahead of it -- `advance_book` folds a delta in on `id` with no upper
    `ts` bound while `_merge_events` stops at the track's deadline -- so writing the head back
    would jump the order over a delta stamped ahead of our clock. The next step reaches that
    delta through `_sim_book`'s `book_at(ts_of(cursor))` branch instead.

    `traded_at_price` is written as NULL on every post-boundary order (ruling CR-3): the C0
    quantity is never written again, and the boundary is visible by nullness rather than by a
    date. The three scalar ledger columns are the surviving entries' sums -- `print_unmatched`
    over the print claims, the other two over the buckets by kind -- which §2's invariant query
    checks against the `jsonb` document beside them.

    `last_print_ts` and `last_print_ids` keep being written so the existing columns stay
    meaningful and no reader of them breaks: the floor is what `last_print_ts` now holds, and the
    id list is empty because the ids live in `recon_state`.
    """
    return {f"{prefix}queue_remaining": state.queue_remaining,
            f"{prefix}traded_at_price": None,
            f"{prefix}tape_cursor_event_id": state.cursor_event_id,
            f"{prefix}crossed": state.crossed,
            f"{prefix}print_unmatched": state.print_unmatched,
            f"{prefix}pending_unmatched": state.pending_unmatched,
            f"{prefix}pending_surplus": state.pending_surplus,
            f"{prefix}cancels_ahead": state.cancels_ahead,
            f"{prefix}recon_state": recon_state_json(state),
            f"{prefix}last_print_ts": state.print_floor,
            f"{prefix}last_print_ids": []}
