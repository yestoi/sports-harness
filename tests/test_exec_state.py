"""`harness/execution/state.py`: one track's `SimState` off an `orders` row and back.

A pure refactor is tested by the round trip it preserves: every column `_state_columns` writes
is a column `_state_of` reads, and a state that goes out through one and comes back through the
other is the state it started as. 6B §1.3 extends both ends of that trip, so the trip itself is
pinned here first.

No database: an `orders` row is attribute access, and both helpers read and write attributes.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

from harness.execution.fills import SimState
from harness.execution.state import _state_columns, _state_of

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

#: The columns one track is persisted in, for a track with no prefix. `filled_contracts` is
#: deliberately not among them: the loop writes it beside the state, from the track result.
WATCHED_COLUMNS = ["crossed", "last_print_ids", "last_print_ts", "queue_remaining",
                   "tape_cursor_event_id", "traded_at_price"]


def _row(prefix: str, columns: dict, filled: Decimal) -> NS:
    """An `orders` row carrying exactly the columns one track is persisted in, plus the
    running fill total `_state_of` reads beside them."""
    return NS(**dict(columns, **{f"{prefix}filled_contracts": filled}))


def test_a_state_round_trips_through_every_column():
    """Expected: the state that comes back is equal to the state that went out.

    Computed independently of the code: `SimState` has seven fields, one of which
    (`filled_contracts`) the loop writes from the track result rather than from the state
    columns. The other six are the six names below. A round trip that loses one would come back
    with that field at its default -- `None`, `0` or `()` -- and the equality would fail on it.
    """
    state = SimState(queue_remaining=Decimal("5.00"), traded_at_price=Decimal("2.00"),
                     filled_contracts=Decimal("3.00"), cursor_event_id=42, crossed=True,
                     last_print_ts=T0, last_print_ids=("a", "b"))
    columns = _state_columns("", state)
    assert sorted(columns) == WATCHED_COLUMNS
    assert _state_of(_row("", columns, Decimal("3.00")), "") == state


def test_the_counterfactual_track_uses_the_same_shape_under_its_own_prefix():
    """Expected: the two tracks share no column name, and the `nw_` names are the watched names
    with one prefix.

    Computed independently: F3's counterfactual is a second simulation of the same order on the
    same tape, so it needs the same six fields; it must never write into the watched track's
    columns, or a cancelled order's counterfactual would overwrite the record of what the order
    we placed actually did.
    """
    state = SimState(queue_remaining=Decimal("1.00"), traded_at_price=Decimal("0.00"),
                     filled_contracts=Decimal("0.00"), cursor_event_id=None)
    watched, counterfactual = _state_columns("", state), _state_columns("nw_", state)
    assert set(watched) & set(counterfactual) == set()
    assert sorted(counterfactual) == [f"nw_{name}" for name in WATCHED_COLUMNS]
    assert _state_of(_row("nw_", counterfactual, Decimal("0.00")), "nw_") == state


def test_a_null_print_watermark_reads_back_as_an_empty_tuple():
    """Expected: `last_print_ids` is `()`, never `None`.

    Computed independently: the column is nullable JSONB and a freshly placed order has never
    seen a print, so the database holds NULL there. `SimState._seen_print` iterates the tuple,
    so a `None` reaching it would raise on the first print of every order's life.
    """
    row = NS(queue_remaining=None, traded_at_price=None, filled_contracts=Decimal("0.00"),
             tape_cursor_event_id=None, crossed=False, last_print_ts=None, last_print_ids=None)
    state = _state_of(row, "")
    assert state.last_print_ids == ()
    assert state.traded_at_price == Decimal("0.00")


def test_the_loop_reads_the_helpers_from_the_new_module():
    """The extraction is a move, not a copy: a second definition in `loop.py` would drift from
    this one the first time §1.3 extends the shape."""
    from harness.execution import loop, state

    assert loop._state_of is state._state_of
    assert loop._state_columns is state._state_columns
