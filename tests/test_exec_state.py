"""`harness/execution/state.py`: one track's `SimState` off an `orders` row and back.

A pure refactor is tested by the round trip it preserves: every column `_state_columns` writes
is a column `_state_of` reads, and a state that goes out through one and comes back through the
other is the state it started as. 6B §1.3 extends both ends of that trip -- the reconciliation
ledger's four scalars and its bounded `jsonb` document -- so the trip itself is pinned here.

No database: an `orders` row is attribute access, and both helpers read and write attributes.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

from harness.execution.fills import SimState
from harness.execution.state import _state_columns, _state_of, recon_state_json, recon_state_of

T0 = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

#: The columns one track is persisted in, for a track with no prefix. `filled_contracts` is
#: deliberately not among them: the loop writes it beside the state, from the track result.
#: `traded_at_price` is written, always as NULL (ruling CR-3): C0's charge-against quantity is
#: not a ledger term, and the 6B boundary is visible by that nullness.
WATCHED_COLUMNS = ["cancels_ahead", "crossed", "last_print_ids", "last_print_ts",
                   "pending_surplus", "pending_unmatched", "print_unmatched", "queue_remaining",
                   "recon_state", "tape_cursor_event_id", "traded_at_price"]


def _row(prefix: str, columns: dict, filled: Decimal) -> NS:
    """An `orders` row carrying exactly the columns one track is persisted in, plus the
    running fill total `_state_of` reads beside them."""
    return NS(**dict(columns, **{f"{prefix}filled_contracts": filled}))


def _ledger_state(**over) -> SimState:
    """A state with every field set to something that is not its default, so a round trip that
    drops one fails on it."""
    values = dict(queue_remaining=Decimal("5.00"), filled_contracts=Decimal("3.00"),
                  cursor_event_id=42, crossed=True, print_unmatched=Decimal("2.00"),
                  cancels_ahead=Decimal("1.00"),
                  buckets=((T0, "pending", Decimal("4.00")),
                           (T0, "surplus", Decimal("0.50"))),
                  trade_ids=((T0, "a"), (T0, "b")), print_floor=T0)
    return SimState(**dict(values, **over))


def test_a_state_round_trips_through_every_column():
    """Expected: the state that comes back is equal to the state that went out.

    Computed independently of the code: `SimState` has nine fields, one of which
    (`filled_contracts`) the loop writes from the track result rather than from the state
    columns. The other eight are persisted across the eleven names below -- the buckets reach
    three of them, the two scalar sums plus the `jsonb` document §2's invariant compares them
    with. A round trip that loses one would come back with that field at its default -- `None`,
    `0` or `()` -- and the equality would fail on it.
    """
    state = _ledger_state()
    columns = _state_columns("", state)
    assert sorted(columns) == WATCHED_COLUMNS
    assert _state_of(_row("", columns, Decimal("3.00")), "") == state


def test_the_counterfactual_track_uses_the_same_shape_under_its_own_prefix():
    """Expected: the two tracks share no column name, and the `nw_` names are the watched names
    with one prefix.

    Computed independently: F3's counterfactual is a second simulation of the same order on the
    same tape, so it needs the same eight fields; it must never write into the watched track's
    columns, or a cancelled order's counterfactual would overwrite the record of what the order
    we placed actually did.
    """
    state = SimState(queue_remaining=Decimal("1.00"), filled_contracts=Decimal("0.00"),
                    cursor_event_id=None)
    watched, counterfactual = _state_columns("", state), _state_columns("nw_", state)
    assert set(watched) & set(counterfactual) == set()
    assert sorted(counterfactual) == [f"nw_{name}" for name in WATCHED_COLUMNS]
    assert _state_of(_row("nw_", counterfactual, Decimal("0.00")), "nw_") == state


def test_a_null_ledger_reads_back_as_an_empty_one():
    """Expected: the buckets and the id set are `()`, the floor is `None` and the two scalars
    are `0` -- never `None`.

    Computed independently: the columns are nullable with no default and a pre-6B order has
    never had a ledger written, so the database holds NULL in all of them. The simulator sums
    and compares those Decimals on the first delta of every order's life, and iterates the two
    tuples, so a `None` reaching it would raise there. A null `recon_state` starts empty rather
    than inferring anything from `traded_at_price`, which is a different quantity (ruling CR-3).
    """
    row = NS(queue_remaining=None, filled_contracts=Decimal("0.00"), tape_cursor_event_id=None,
             crossed=False, print_unmatched=None, cancels_ahead=None, recon_state=None)
    state = _state_of(row, "")
    assert (state.buckets, state.trade_ids, state.print_floor) == ((), (), None)
    assert state.print_unmatched == Decimal("0.00")
    assert state.cancels_ahead == Decimal("0.00")


def test_a_stored_zero_is_not_an_absent_column():
    """Expected: a column holding `0` reads back as `0`, and `0` is what it was written as.

    Computed independently (T1 review, minor 3): the old round trip read
    `getattr(row, ...) or ZERO`, which cannot tell an absent column from a stored falsy value.
    On these columns the two mean different things -- "this track has no ledger yet" against
    "this track's ledger is written and this term of it is empty" -- and the 6B boundary query
    of §2 reads exactly that nullness. The test is therefore that the read preserves the
    difference: `0` stays `0` and only NULL defaults.
    """
    written = _state_columns("", SimState(queue_remaining=Decimal("0.00"),
                                         filled_contracts=Decimal("0.00"), cursor_event_id=None))
    assert written["print_unmatched"] == Decimal("0.00")
    assert written["pending_unmatched"] == Decimal("0.00")
    assert written["cancels_ahead"] == Decimal("0.00")
    row = NS(queue_remaining=Decimal("0.00"), filled_contracts=Decimal("0.00"),
             tape_cursor_event_id=None, crossed=False, print_unmatched=Decimal("0.00"),
             cancels_ahead=Decimal("0.00"), recon_state={"buckets": [], "trade_ids": [],
                                                        "print_floor": None})
    state = _state_of(row, "")
    assert (state.print_unmatched, state.cancels_ahead) == (Decimal("0.00"), Decimal("0.00"))
    assert state.queue_remaining == Decimal("0.00")


def test_traded_at_price_is_written_null_on_every_track():
    """Expected: both tracks write NULL there, and neither reads it (ruling CR-3, §1.3).

    Computed independently: C0's `traded_at_price` is the quantity a print was charged against,
    which the ledger replaces with terms of its own; reusing the column would make the two
    indistinguishable in the table. §2's invariant for the post-boundary range is
    `traded_at_price is null`, so the writer must leave it null and the reader must not depend
    on it -- which is why a row with no such attribute at all still reads back a state.
    """
    state = _ledger_state()
    assert _state_columns("", state)["traded_at_price"] is None
    assert _state_columns("nw_", state)["nw_traded_at_price"] is None
    columns = _state_columns("", state)
    del columns["traded_at_price"]
    assert _state_of(_row("", columns, Decimal("3.00")), "") == state


def test_the_ledger_document_is_bounded_and_json_safe():
    """Expected: every value in the document is a string, a null or a list, and the two lists
    are capped.

    Computed independently: the column is `jsonb`, so a `Decimal` or a `datetime` in it would
    either raise on write or come back as a float and lose a fractional contract count (F40).
    The caps are §2's: an order resting for hours must not be able to grow the column without
    limit. `recon_state_of` is the inverse and is what a restart reads.
    """
    from harness.execution.fills import BUCKET_CAP, TRADE_ID_CAP

    state = _ledger_state(
        buckets=tuple((T0, "pending", Decimal(str(i + 1))) for i in range(BUCKET_CAP + 10)),
        trade_ids=tuple((T0, f"t{i}") for i in range(TRADE_ID_CAP + 10)))
    doc = recon_state_json(state)
    assert len(doc["buckets"]) == BUCKET_CAP and len(doc["trade_ids"]) == TRADE_ID_CAP
    assert all(isinstance(value, str) for row in doc["buckets"] for value in row)
    assert all(isinstance(value, str) for row in doc["trade_ids"] for value in row)
    assert doc["print_floor"] == T0.isoformat()
    buckets, trade_ids, floor = recon_state_of(doc)
    assert buckets == state.buckets[-BUCKET_CAP:]
    assert trade_ids == state.trade_ids[-TRADE_ID_CAP:]
    assert floor == T0
    # A column written as text by any reader that round-trips it through `json` still loads.
    import json

    assert recon_state_of(json.dumps(doc)) == (buckets, trade_ids, floor)


def test_the_loop_reads_the_helpers_from_the_new_module():
    """The extraction is a move, not a copy: a second definition in `loop.py` would drift from
    this one the first time §1.3 extends the shape."""
    from harness.execution import loop, state

    assert loop._state_of is state._state_of
    assert loop._state_columns is state._state_columns
