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
#: `traded_at_price` is written back exactly as it was read (ruling CR-3): NULL on a
#: post-boundary order, which is what makes the 6B boundary visible by nullness, and its existing
#: value on a pre-boundary order still being simulated.
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
                  cursor_event_id=42, crossed=True, cancels_ahead=Decimal("1.00"),
                  prints=((T0, Decimal("2.00")),),
                  buckets=((T0, "pending", Decimal("4.00")),
                           (T0, "surplus", Decimal("0.50"))),
                  trade_ids=((T0, "a"), (T0, "b")), print_floor=T0)
    return SimState(**dict(values, **over))


def _blank_row(prefix: str = "", **over) -> NS:
    """An `orders` row whose every ledger column is NULL: a pre-6B order, never written."""
    values = {f"{prefix}queue_remaining": None, f"{prefix}filled_contracts": Decimal("0.00"),
              f"{prefix}tape_cursor_event_id": None, f"{prefix}crossed": False,
              f"{prefix}print_unmatched": None, f"{prefix}cancels_ahead": None,
              f"{prefix}recon_state": None, f"{prefix}last_print_ts": None,
              f"{prefix}last_print_ids": None, f"{prefix}traded_at_price": None}
    return NS(**dict(values, **{f"{prefix}{k}": v for k, v in over.items()}))


def test_a_state_round_trips_through_every_column():
    """Expected: the state that comes back is equal to the state that went out.

    Computed independently of the code: `SimState` has nine fields, one of which
    (`filled_contracts`) the loop writes from the track result rather than from the state
    columns. The other eight are persisted across the eleven names below. Three of the eleven are
    derived sums the reader does not need -- `print_unmatched` over the print claims and the two
    bucket sums by kind -- and exist so §2's invariant query can compare them with the `jsonb`
    document beside them; the document is what carries the three lists and the floor. A round
    trip that lost one would come back with that field at its default -- `None`, `0` or `()` --
    and the equality would fail on it.
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
    """Expected: the three lists are `()`, the floor is `None` and the scalars are `0` -- never
    `None`.

    Computed independently: the columns are nullable with no default and a pre-6B order has
    never had a ledger written, so the database holds NULL in all of them. The simulator sums
    and compares those Decimals on the first delta of every order's life, and iterates the three
    tuples, so a `None` reaching it would raise there. A null `recon_state` starts empty rather
    than inferring anything from `traded_at_price`, which is a different quantity (ruling CR-3).
    A pre-6B order with no watermark either has nothing to carry forward, which is this row.
    """
    state = _state_of(_blank_row(), "")
    assert (state.prints, state.buckets, state.trade_ids, state.print_floor) == ((), (), (), None)
    assert state.print_unmatched == Decimal("0.00")
    assert state.cancels_ahead == Decimal("0.00")


def test_a_pre_6b_order_keeps_its_print_watermark_as_a_floor():
    """Expected: `print_floor` is the old `last_print_ts` and the old ids come back as seen
    (round 1, I3).

    Computed independently: the pre-6B shape recorded "every print at or before this instant is
    already applied, and these are the ids applied at exactly it". An order placed before the 6B
    deploy and still working at it has that watermark and no `recon_state`. The executor re-reads
    its whole resting print history from `placed_at - PRINT_LOOKBACK` on the very next step, so a
    reader that ignored the watermark would apply every one of those prints a second time -- the
    double count this task exists to remove, reintroduced by the upgrade itself. The watermark's
    two halves map exactly onto the new shape's two halves: the instant is the floor (nothing at
    or below it may re-apply) and the ids at that instant are the seen set.

    The transitional read is a read: nothing is written back to a pre-6B row beyond what the
    repaired writer writes on its first step, and once it has, `recon_state` is not null and this
    path is dead for that order forever.
    """
    row = _blank_row(last_print_ts=T0, last_print_ids=["a", "b"])
    state = _state_of(row, "")
    assert state.print_floor == T0
    assert state.trade_ids == ((T0, "a"), (T0, "b"))
    assert (state.prints, state.buckets) == ((), ())
    # The counterfactual track carries its own watermark under its own prefix.
    nw = _state_of(_blank_row("nw_", last_print_ts=T0, last_print_ids=["c"]), "nw_")
    assert (nw.print_floor, nw.trade_ids) == (T0, ((T0, "c"),))
    # A watermark with no ids is still a floor: the instant is what the old shape guaranteed.
    assert _state_of(_blank_row(last_print_ts=T0), "").print_floor == T0
    # A written ledger wins: the watermark column now holds the floor the writer put there, and
    # the document is the authority for the ids.
    written = _state_columns("", _ledger_state())
    resumed = _state_of(_row("", written, Decimal("3.00")), "")
    assert resumed.trade_ids == ((T0, "a"), (T0, "b")) and resumed.print_floor == T0


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
    row = _blank_row(queue_remaining=Decimal("0.00"), print_unmatched=Decimal("0.00"),
                     cancels_ahead=Decimal("0.00"),
                     recon_state={"buckets": [], "prints": [], "trade_ids": [],
                                  "print_floor": None})
    state = _state_of(row, "")
    assert (state.print_unmatched, state.cancels_ahead) == (Decimal("0.00"), Decimal("0.00"))
    assert state.queue_remaining == Decimal("0.00")
    # An empty document is not a null one: it is a written ledger, so the pre-6B watermark read
    # must not fire and reinstate a floor from `last_print_ts`.
    assert _state_of(_blank_row(recon_state={"buckets": [], "prints": [], "trade_ids": [],
                                             "print_floor": None}, last_print_ts=T0),
                     "").print_floor is None


def test_traded_at_price_is_null_after_the_boundary_and_kept_before_it():
    """Expected: NULL stays NULL, and a pre-6B value comes back unchanged (round 2, Important A).

    Computed independently: C0's `traded_at_price` is the quantity a print was charged against,
    which the ledger replaces with terms of its own, so no 6B code may compute or update it.
    §2 row 3's invariant is that **post-boundary** rows have it NULL -- that nullness is what
    distinguishes them -- and an order placed after the deploy never acquires one, so a state that
    read NULL writes NULL on both tracks.

    A pre-6B order that is still working at the deploy is the other half, and the one the first
    version of this task got wrong: it has a recorded C0 quantity, it is simulated on every step
    for as long as it rests (and its counterfactual for as long as `nw_done` is false), and a
    writer that replaced that value with NULL would both destroy the record and make the row look
    post-boundary. So the value travels through the state untouched and is written back as it was.
    """
    post = _ledger_state()
    assert post.legacy_traded_at_price is None
    assert _state_columns("", post)["traded_at_price"] is None
    assert _state_columns("nw_", post)["nw_traded_at_price"] is None
    # The round trip of a post-boundary row: NULL in, NULL out, on both tracks.
    columns = _state_columns("", post)
    assert _state_of(_row("", columns, Decimal("3.00")), "") == post

    # A pre-6B order, mid-simulation: `7` in, `7` out, and nothing else about it changes.
    legacy = _blank_row(queue_remaining=Decimal("5.00"), traded_at_price=Decimal("7"),
                        last_print_ts=T0, last_print_ids=["a"])
    state = _state_of(legacy, "")
    assert state.legacy_traded_at_price == Decimal("7")
    written = _state_columns("", state)
    assert written["traded_at_price"] == Decimal("7")
    # ... and it survives a second step, which reads what the first step wrote.
    again = _state_of(_row("", written, Decimal("0.00")), "")
    assert again.legacy_traded_at_price == Decimal("7")
    assert _state_columns("", again)["traded_at_price"] == Decimal("7")
    # The counterfactual track carries its own column, not the watched one's.
    nw = _state_of(_blank_row("nw_", traded_at_price=Decimal("4")), "nw_")
    assert _state_columns("nw_", nw)["nw_traded_at_price"] == Decimal("4")


def test_the_ledger_document_is_json_safe_and_exactly_invertible():
    """Expected: every value in the document is a string, a null or a list, and `recon_state_of`
    returns the three lists and the floor unchanged -- at the caps and past them.

    Computed independently: the column is `jsonb`, so a `Decimal` or a `datetime` in it would
    either raise on write or come back as a float and lose a fractional contract count (F40). The
    document is written whole, with no slice of its own (round 2, minor 6): the scalar columns are
    derived from the same tuples and §2 row 2 compares the two, so trimming here would break that
    equality rather than enforce a bound. Bounding is the simulator's job on every write, where
    the volume is known and a merge can conserve it --
    `tests/test_fills.py::test_six_hundred_decrements_inside_one_horizon_are_all_still_explained`
    is where that is pinned. This case feeds lists past both caps precisely to show that the
    persistence layer neither trims nor reorders what it is handed.
    """
    from harness.execution.fills import BUCKET_CAP, TRADE_ID_CAP

    state = _ledger_state(
        buckets=tuple((T0, "pending", Decimal(str(i + 1))) for i in range(BUCKET_CAP + 10)),
        prints=tuple((T0, Decimal(str(i + 1))) for i in range(BUCKET_CAP + 10)),
        trade_ids=tuple((T0, f"t{i}") for i in range(TRADE_ID_CAP + 10)))
    doc = recon_state_json(state)
    assert len(doc["buckets"]) == len(state.buckets) == BUCKET_CAP + 10
    assert len(doc["prints"]) == len(state.prints) == BUCKET_CAP + 10
    assert len(doc["trade_ids"]) == len(state.trade_ids) == TRADE_ID_CAP + 10
    assert all(isinstance(value, str) for row in doc["buckets"] for value in row)
    assert all(isinstance(value, str) for row in doc["prints"] for value in row)
    assert all(isinstance(value, str) for row in doc["trade_ids"] for value in row)
    assert doc["print_floor"] == T0.isoformat()
    buckets, prints, trade_ids, floor = recon_state_of(doc)
    assert buckets == state.buckets
    assert prints == state.prints
    assert trade_ids == state.trade_ids
    assert floor == T0
    # A column written as text by any reader that round-trips it through `json` still loads.
    import json

    assert recon_state_of(json.dumps(doc)) == (buckets, prints, trade_ids, floor)


def test_the_scalar_columns_are_the_sums_of_the_document_beside_them():
    """§2 row 2, on the writer: `pending_unmatched + pending_surplus` equals the buckets in the
    document, and `print_unmatched` equals its `prints`, with no list trimmed on the way out
    (round 2, minor 6).

    Computed independently: the invariant query compares the scalar columns of a row with the
    `jsonb` beside them, so the two have to be derived from the same tuples. A document sliced at
    the cap while the scalars were summed over the whole state would fail that query on exactly
    the orders the cap exists for -- a busy ticker, where the difference is real volume. Bounding
    the lists is the simulator's job, on every write, and it conserves volume by merging rather
    than by trimming.
    """
    from harness.execution.fills import BUCKET_CAP

    state = _ledger_state(
        buckets=tuple((T0, "pending", Decimal("1.00")) for _ in range(BUCKET_CAP + 7)),
        prints=tuple((T0, Decimal("2.00")) for _ in range(BUCKET_CAP + 5)))
    columns = _state_columns("", state)
    doc = columns["recon_state"]
    assert len(doc["buckets"]) == BUCKET_CAP + 7 and len(doc["prints"]) == BUCKET_CAP + 5
    bucket_sum = sum(Decimal(size) for _ts, _kind, size in doc["buckets"])
    assert columns["pending_unmatched"] + columns["pending_surplus"] == bucket_sum
    assert columns["print_unmatched"] == sum(Decimal(size) for _ts, size in doc["prints"])


def test_a_foreign_bucket_kind_in_the_column_is_refused():
    """Expected: `ValueError`, not a silently unmatchable bucket (round 2, minor 7).

    Computed independently: `fills._claim` selects buckets by comparing `kind` for equality, so a
    kind outside `BUCKET_KINDS` -- a hand-edited row, a future build's name, a typo in a fixture --
    would never be claimed by any print, the decrement it holds would stay unexplained, and the
    next print would take the queue for it a second time. That is the double count C3 removes,
    arriving through the column instead of through the arithmetic. Failing once on the read is the
    cheap place to catch it.
    """
    import pytest

    from harness.execution.fills import BUCKET_KINDS

    assert BUCKET_KINDS == ("pending", "surplus")
    good = {"buckets": [[T0.isoformat(), "surplus", "1.00"]], "prints": [], "trade_ids": [],
            "print_floor": None}
    assert recon_state_of(good)[0] == ((T0, "surplus", Decimal("1.00")),)
    bad = {"buckets": [[T0.isoformat(), "pendign", "1.00"]], "prints": [], "trade_ids": [],
           "print_floor": None}
    with pytest.raises(ValueError, match="pendign"):
        recon_state_of(bad)


def test_the_loop_reads_the_helpers_from_the_new_module():
    """The extraction is a move, not a copy: a second definition in `loop.py` would drift from
    this one the first time §1.3 extends the shape."""
    from harness.execution import loop, state

    assert loop._state_of is state._state_of
    assert loop._state_columns is state._state_columns
