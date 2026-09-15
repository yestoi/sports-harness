"""F1's premise, checked against a real 60-second slice of the recorded tape.

The queue model reads a print as "the resting side's level shrank by this much". That is a
claim about how Kalshi reports a trade, and it is only true if a print on the taker side
pairs with a negative delta *on the other side, at the other side's price*: a taker-YES
trade at a YES price of 0.03 is a NO bid at 0.97 being lifted, and the delta the venue
publishes is `no -156.06 @ 0.97`, not `yes ... @ 0.03`.

This file never skips. It reads a committed fixture, no database, and if the premise ever
fails on a fresh export that is a ruling on the fill rules, not a reason to relax the test.

6B §1.3 adds the ledger's own premise check on the same slice, asserted **conservation-only**
(ruling I-13, route (b)): no number this code produced is frozen here.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

from harness.execution.book import opp
from harness.execution.fills import (
    _DELTA,
    _PRINT,
    FillResult,
    PaperOrder,
    SimState,
    TapeDelta,
    TapePrint,
    hits,
    price_on_side,
)
from harness.execution.state import _state_columns, _state_of
from tests.test_fills import DEADLINE, T0, run

FIXTURE = Path(__file__).parent / "fixtures" / "tape_sample_lou_miss_2026-09-07T03.json"
WITHIN = timedelta(seconds=1)


def _tape() -> tuple[dict, list[TapePrint], list[TapeDelta]]:
    body = json.loads(FIXTURE.read_text())
    prints = [
        TapePrint(trade_id=r["trade_id"], ts=datetime.fromisoformat(r["ts"]),
                  yes_price=r["yes_price"], count=r["count"],
                  # The canonical taker side is `taker_outcome_side or taker_side`, never
                  # defaulted (F5); this slice carries only the already-canonical column.
                  taker_side=r.get("taker_outcome_side") or r.get("taker_side"),
                  source=r["source"])
        for r in body["prints"]
    ]
    deltas = [
        TapeDelta(event_id=r["id"], ts=datetime.fromisoformat(r["ts"]), side=r["side"],
                  price=r["price"], delta=r["delta"], sid=r["sid"], seq=r["seq"])
        for r in body["deltas"]
    ]
    return body, prints, deltas


def _matching_delta(p: TapePrint, deltas: list[TapeDelta], price: Decimal, side: str):
    for d in deltas:
        if (d.side == side and d.price == price and d.delta < 0
                and -d.delta >= p.count and abs(d.ts - p.ts) <= WITHIN):
            return d
    return None


def test_fixture_shape_is_the_recorded_slice():
    body, prints, deltas = _tape()
    assert body["ticker"] == "KXNCAAFGAME-26SEP06LOUMISS-LOU"
    assert (len(prints), len(deltas)) == (196, 616)
    assert body["counts"]["taker_sides"] == {"yes": 65, "no": 131}
    assert all(p.taker_side in ("yes", "no") for p in prints)
    # Fractional counts are real on this venue (F40), which is why every quantity is Decimal.
    assert any(p.count % 1 != 0 for p in prints)


def test_recorded_prints_have_a_matching_delta_within_1s():
    _, prints, deltas = _tape()
    matched, misses = 0, []
    for p in prints:
        resting = opp(p.taker_side)
        if _matching_delta(p, deltas, price_on_side(p, resting), resting) is not None:
            matched += 1
        else:
            misses.append((p.trade_id, p.taker_side, str(p.yes_price), str(p.count)))
    assert matched == len(prints) == 196, f"{matched} of {len(prints)} matched; misses {misses[:5]}"


def test_the_worked_example_pairs_across_the_book():
    _, prints, deltas = _tape()
    p = next(x for x in prints if x.taker_side == "yes" and x.yes_price == Decimal("0.0300")
             and x.count == Decimal("156.06"))
    d = _matching_delta(p, deltas, price_on_side(p, "no"), "no")
    assert (d.side, d.price, d.delta) == ("no", Decimal("0.9700"), Decimal("-156.06"))
    # The literal reading -- look for the delta on the taker's own side -- finds nothing.
    assert _matching_delta(p, deltas, price_on_side(p, "yes"), "yes") is None


def test_the_taker_side_reading_matches_nothing_on_this_tape():
    """The contrast case: a delta on the *taker's* side, which is what "same side, same price
    as the print" would mean. It matches 0 of 196, which is why the rule reads across."""
    _, prints, deltas = _tape()
    matched = sum(
        _matching_delta(p, deltas, price_on_side(p, p.taker_side), p.taker_side) is not None
        for p in prints
    )
    assert matched == 0


# --- the ledger on the real slice (6B §1.3, route (b)) -----------------------


def _slice() -> tuple[list[TapePrint], list[TapeDelta], PaperOrder]:
    """The committed slice as the two streams one resting order would have been fed.

    A YES order at 0.02 is the level this slice is busy at: 131 of its 196 prints are NO takers
    at a YES price of 0.02, which is exactly the resting YES bid at 0.02 being lifted, and 206 of
    its 616 deltas are that level's own changes. `placed_at` is the slice's own lower bound, so
    every event in it is inside the order's resting interval, and the deadline is
    `tests.test_fills.DEADLINE`, an hour later, so none is cut off at the top. The queue and size
    are the fixture's own ladder rounded down -- roughly 5 000 contracts rest ahead of us at that
    price in the snapshot -- not a number tuned to an outcome.
    """
    body, prints, deltas = _tape()
    order = PaperOrder(order_id=1, ticker=body["ticker"], side="yes", prob=Decimal("0.02"),
                       contracts=Decimal("250"), placed_at=T0, expiry=DEADLINE,
                       queue_ahead_at_place=Decimal("5000"))
    return prints, deltas, order


def _merged(prints, deltas) -> list[tuple]:
    """The two streams in one list, in the order the simulator walks them: `ts`, then deltas
    before prints at an equal `ts`, then input order within a kind (`fills._merge_events`)."""
    events = [(d.ts, _DELTA, i, d) for i, d in enumerate(deltas)]
    events += [(p.ts, _PRINT, i, p) for i, p in enumerate(prints)]
    return sorted(events, key=lambda e: (e[0], e[1], e[2]))


def _feed(order: PaperOrder, chunks: list[list[tuple]]) -> tuple[FillResult, list[Decimal]]:
    """Feed a merged history in contiguous pieces, each from the previous piece's state.

    Returns one `FillResult` carrying every fill and the final state, plus the queue after each
    piece, so a caller can check that the queue never grew.
    """
    state = SimState.initial(order)
    fills: list = []
    queues: list[Decimal] = []
    for chunk in chunks:
        result = run(order, state=state,
                     prints=[e[3] for e in chunk if e[1] == _PRINT],
                     deltas=[e[3] for e in chunk if e[1] == _DELTA])
        state = result.state
        fills.extend(result.fills)
        queues.append(state.queue_remaining)
    return FillResult(fills=fills, state=state, cross=None, crossed=state.crossed), queues


def _in_chunks(order: PaperOrder, prints, deltas, pieces: int) -> FillResult:
    """The same history in `pieces` contiguous slices of the merged event list."""
    events = _merged(prints, deltas)
    size = -(-len(events) // pieces)
    result, _queues = _feed(order, [events[i:i + size] for i in range(0, len(events), size)])
    return result


def test_the_real_tape_slice_conserves_liquidity_under_the_ledger():
    """Route (b): conservation only, no frozen output (ruling I-13).

    Three properties that hold whatever the arithmetic is, and that the pre-6B double count
    violated: we cannot fill more than the volume that actually printed at or through our price
    while we rested; a queue ahead of us never grows, because a late joiner sits behind us; and
    the same history fed as one call, as twenty chunks, and across a persisted boundary is the
    same history.
    """
    prints, deltas, o = _slice()
    hitting = sum(p.count for p in prints
                  if hits(p, o.side) and price_on_side(p, o.side) <= o.prob
                  and o.placed_at < p.ts <= DEADLINE)
    one = run(o, prints=prints, deltas=deltas)
    assert one.state.filled_contracts <= hitting
    assert one.state.queue_remaining <= o.queue_ahead_at_place
    # The slice has to actually do something for the bounds to mean anything: this is a busy
    # in-game minute at the level the order rests on, so both sides of the ledger are exercised.
    assert one.state.filled_contracts > Decimal("0")
    assert hitting > Decimal("0")
    chunked = _in_chunks(o, prints, deltas, 20)
    assert (chunked.state.queue_remaining, chunked.state.filled_contracts) == (
        one.state.queue_remaining, one.state.filled_contracts)
    assert chunked.fills == one.fills
    assert chunked.state == one.state


def test_the_queue_never_grows_across_the_real_slice():
    """The monotonicity half of route (b), checked piece by piece rather than at the end.

    Liquidity joining our level after we did sits behind us under price-time priority, and this
    minute adds more of it than it takes away (the level's positive deltas outweigh its negative
    ones). A queue that grew would mean the simulator had charged us for size that joined behind
    us, which no arithmetic may do, so the property is asserted after every one of the twenty
    pieces and not only on the total.
    """
    prints, deltas, o = _slice()
    events = _merged(prints, deltas)
    size = -(-len(events) // 20)
    _result, queues = _feed(o, [events[i:i + size] for i in range(0, len(events), size)])
    assert len(queues) == 20
    assert queues == sorted(queues, reverse=True)
    assert queues[0] <= o.queue_ahead_at_place


def test_the_real_slice_reconciles_across_a_persisted_loop_boundary():
    """The third feed of §5: the same history with the state written to columns and read back.

    The executor persists a track between loops, so the ledger has to survive the round trip
    through `orders` -- the two scalar sums, the bounded `jsonb` document and the floor -- and not
    merely the in-process boundary `_in_chunks` exercises. Asserted against the one-call feed,
    which is an identity and not a frozen number.
    """
    prints, deltas, o = _slice()
    events = _merged(prints, deltas)
    half = len(events) // 2
    first = run(o, prints=[e[3] for e in events[:half] if e[1] == _PRINT],
                deltas=[e[3] for e in events[:half] if e[1] == _DELTA])
    row = NS(**dict(_state_columns("", first.state),
                    filled_contracts=first.state.filled_contracts))
    resumed = _state_of(row, "")
    second = run(o, state=resumed,
                 prints=[e[3] for e in events[half:] if e[1] == _PRINT],
                 deltas=[e[3] for e in events[half:] if e[1] == _DELTA])
    one = run(o, prints=prints, deltas=deltas)
    assert first.fills + second.fills == one.fills
    assert second.state == one.state
