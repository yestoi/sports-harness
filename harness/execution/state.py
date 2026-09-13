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
    on these columns the difference is between "no ledger yet" and "an empty ledger" (T1 review,
    minor 3).
    """
    return None if value is None else Decimal(value)


def recon_state_json(state: SimState) -> dict:
    """The bounded `jsonb` document one track's ledger persists as (§2).

    Decimals are written as strings and instants as ISO-8601, because a round trip through
    `json` would otherwise turn a contract count into a float. Both lists are already capped by
    the simulator; the slices here are a second, cheap guarantee that a column written by an
    older build cannot grow past the cap when this one writes it back.
    """
    return {"buckets": [[_iso(ts), kind, str(size)]
                        for ts, kind, size in state.buckets[-BUCKET_CAP:]],
            "trade_ids": [[_iso(ts), tid] for ts, tid in state.trade_ids[-TRADE_ID_CAP:]],
            "print_floor": _iso(state.print_floor)}


def recon_state_of(value) -> tuple:
    """`(buckets, trade_ids, print_floor)` off the column; a null column starts empty.

    A null is a pre-boundary order, whose ledger has never been written: it starts with an
    empty ledger rather than with anything inferred from `traded_at_price`, which is a different
    quantity (ruling CR-3).
    """
    if not value:
        return (), (), None
    doc = value if isinstance(value, dict) else json.loads(value)
    buckets = tuple((_ts(ts), kind, Decimal(size)) for ts, kind, size in doc.get("buckets", ()))
    trade_ids = tuple((_ts(ts), tid) for ts, tid in doc.get("trade_ids", ()))
    return buckets, trade_ids, _ts(doc.get("print_floor"))


def _state_of(row, prefix: str) -> SimState:
    """The persisted `SimState` of one track, read off the order row.

    `traded_at_price` is deliberately not read: C0's charge-against quantity is not a ledger
    term, and a post-boundary order leaves that column null (ruling CR-3).
    """
    buckets, trade_ids, print_floor = recon_state_of(getattr(row, f"{prefix}recon_state"))
    unmatched = _number(getattr(row, f"{prefix}print_unmatched"))
    cancels = _number(getattr(row, f"{prefix}cancels_ahead"))
    return SimState(
        queue_remaining=getattr(row, f"{prefix}queue_remaining"),
        filled_contracts=getattr(row, f"{prefix}filled_contracts"),
        cursor_event_id=getattr(row, f"{prefix}tape_cursor_event_id"),
        crossed=bool(getattr(row, f"{prefix}crossed")),
        print_unmatched=ZERO if unmatched is None else unmatched,
        cancels_ahead=ZERO if cancels is None else cancels,
        buckets=buckets, trade_ids=trade_ids, print_floor=print_floor)


def _state_columns(prefix: str, state: SimState) -> dict:
    """The columns one track's `SimState` is persisted in.

    The cursor is the last tape row the simulation actually consumed and nothing else. The
    cache's own head runs ahead of it -- `advance_book` folds a delta in on `id` with no upper
    `ts` bound while `_merge_events` stops at the track's deadline -- so writing the head back
    would jump the order over a delta stamped ahead of our clock. The next step reaches that
    delta through `_sim_book`'s `book_at(ts_of(cursor))` branch instead.

    `traded_at_price` is written as NULL on every post-boundary order (ruling CR-3): the C0
    quantity is never written again, and the boundary is visible by nullness rather than by a
    date. The two scalar bucket columns are the surviving buckets' sums, which §2's invariant
    query checks against the `jsonb` document beside them.

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
