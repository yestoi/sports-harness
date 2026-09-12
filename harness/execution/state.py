"""One track's simulation state, read off an `orders` row and written back to it.

`SimState` is the simulator's own shape (`harness/execution/fills.py`); this module is the one
place that knows which columns it lives in and how the watched and counterfactual tracks share
that shape under a prefix. It was `loop.py`'s until 6B §1.10 moved it out unchanged: the loop
owns the step, and the shape of a track's persisted state is a subject of its own that §1.3
extends with the reconciliation ledger.
"""

from harness.execution.book import ZERO
from harness.execution.fills import SimState


def _state_of(row, prefix: str) -> SimState:
    """The persisted `SimState` of one track, read off the order row."""
    return SimState(
        queue_remaining=getattr(row, f"{prefix}queue_remaining"),
        traded_at_price=getattr(row, f"{prefix}traded_at_price") or ZERO,
        filled_contracts=getattr(row, f"{prefix}filled_contracts"),
        cursor_event_id=getattr(row, f"{prefix}tape_cursor_event_id"),
        crossed=bool(getattr(row, f"{prefix}crossed")),
        last_print_ts=getattr(row, f"{prefix}last_print_ts"),
        last_print_ids=tuple(getattr(row, f"{prefix}last_print_ids") or ()))


def _state_columns(prefix: str, state: SimState) -> dict:
    """The columns one track's `SimState` is persisted in.

    The cursor is the last tape row the simulation actually consumed and nothing else. The
    cache's own head runs ahead of it -- `advance_book` folds a delta in on `id` with no upper
    `ts` bound while `_merge_events` stops at the track's deadline -- so writing the head back
    would jump the order over a delta stamped ahead of our clock. The next step reaches that
    delta through `_sim_book`'s `book_at(ts_of(cursor))` branch instead.
    """
    cursor = state.cursor_event_id
    return {f"{prefix}queue_remaining": state.queue_remaining,
            f"{prefix}traded_at_price": state.traded_at_price,
            f"{prefix}tape_cursor_event_id": cursor,
            f"{prefix}crossed": state.crossed,
            f"{prefix}last_print_ts": state.last_print_ts,
            f"{prefix}last_print_ids": list(state.last_print_ids)}
