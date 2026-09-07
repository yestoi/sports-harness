from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.execution import EXECUTOR_VERSION
from harness.execution.book import BookState
from harness.execution.fills import (
    FillResult,
    PaperOrder,
    SimFill,
    SimState,
    TapeDelta,
    TapePrint,
    fill_fee_fields,
    has_print,
    hits,
    price_on_side,
    simulate_fills,
)
from harness.pricing.fees import KALSHI_FOOTBALL, FeeModel, ceil_to_centicent

T0 = datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
DEADLINE = T0 + timedelta(hours=1)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def order(side="yes", prob="0.30", contracts="10", queue="0", placed_at=T0) -> PaperOrder:
    return PaperOrder(order_id=1, ticker="K1", side=side, prob=Decimal(prob),
                      contracts=Decimal(contracts), placed_at=placed_at,
                      expiry=DEADLINE, queue_ahead_at_place=None if queue is None else Decimal(queue))


def tprint(secs, yes_price, count, taker_side="no", trade_id=None, source="ws") -> TapePrint:
    return TapePrint(trade_id=trade_id or f"t{secs}", ts=at(secs), yes_price=Decimal(str(yes_price)),
                     count=Decimal(str(count)), taker_side=taker_side, source=source)


def tdelta(secs, side, price, delta, event_id=None, seq=None) -> TapeDelta:
    eid = event_id if event_id is not None else 1000 + int(secs)
    return TapeDelta(event_id=eid, ts=at(secs), side=side, price=Decimal(str(price)),
                     delta=Decimal(str(delta)), sid=2, seq=seq)


def book(yes=None, no=None, as_of=T0, source="ws", event_id=900) -> BookState:
    return BookState(ticker="K1", yes_bids={Decimal(k): Decimal(v) for k, v in (yes or {}).items()},
                     no_bids={Decimal(k): Decimal(v) for k, v in (no or {}).items()},
                     sid=2, seq=5, as_of=as_of, source=source, anchor_id=event_id,
                     last_event_id=event_id)


def run(o, prints=(), deltas=(), bk=None, state=None, fill_method="queue_model",
        deadline=DEADLINE, fee_model=KALSHI_FOOTBALL) -> FillResult:
    return simulate_fills(o, state if state is not None else SimState.initial(o), bk,
                          list(prints), list(deltas), deadline, fill_method, fee_model)


# --- primitives --------------------------------------------------------------


def test_executor_version_is_bumped_for_fills():
    assert EXECUTOR_VERSION == "3.2"


def test_price_on_side_and_hits():
    p = tprint(1, "0.30", "5", taker_side="no")
    assert price_on_side(p, "yes") == Decimal("0.3000")
    assert price_on_side(p, "no") == Decimal("0.7000")
    # A NO taker lifts a resting YES bid, so it hits a YES order and never a NO one.
    assert hits(p, "yes") is True
    assert hits(p, "no") is False


def test_fill_fee_fields():
    assert fill_fee_fields(KALSHI_FOOTBALL) == (
        "quadratic_with_maker_fees", Decimal("1"), Decimal("0.0175"))
    half = FeeModel(Decimal("0"), Decimal("0.07"), Decimal("0.5"))
    assert fill_fee_fields(half) == ("quadratic", Decimal("0.5"), Decimal("0"))


# --- queue model -------------------------------------------------------------


def test_queue_consumed_before_fill_at_price():
    o = order(queue="5")
    res = run(o, prints=[tprint(1, "0.30", "8")])
    assert [f.contracts for f in res.fills] == [Decimal("3.00")]
    f = res.fills[0]
    assert (f.prob, f.through, f.fill_method) == (Decimal("0.3000"), False, "queue_model")
    assert (f.source_trade_id, f.taker_side, f.tape_source) == ("t1", "no", "ws")
    assert f.filled_at == at(1)
    assert f.source_event_id is None
    assert res.state.queue_remaining == Decimal("0.00")
    assert res.state.traded_at_price == Decimal("8.00")
    assert res.state.filled_contracts == Decimal("3.00")
    assert res.crossed is False and res.cross is None


def test_sweep_through_zeroes_queue_and_fills():
    o = order(queue="100")
    res = run(o, prints=[tprint(1, "0.28", "4")])
    assert [(f.contracts, f.through) for f in res.fills] == [(Decimal("4.00"), True)]
    assert res.state.queue_remaining == Decimal("0.00")
    # A sweep through our level says nothing about how much traded *at* our price.
    assert res.state.traded_at_price == Decimal("0.00")
    assert res.state.filled_contracts == Decimal("4.00")


def test_fractional_print_consumes_half_a_contract():
    o = order(queue="0", contracts="10")
    res = run(o, prints=[tprint(1, "0.30", "0.50")])
    assert [f.contracts for f in res.fills] == [Decimal("0.50")]
    assert res.state.filled_contracts == Decimal("0.50")
    assert res.state.traded_at_price == Decimal("0.50")


def test_negative_delta_attributed_to_trades_first_then_cancels():
    o = order(queue="5")
    # The print takes 3 of the 5 ahead of us; the venue's own -4 delta at our price reports
    # the same 3 trades plus 1 genuine cancel, so only that 1 shortens the queue again.
    res = run(o, prints=[tprint(1, "0.30", "3")], deltas=[tdelta(2, "yes", "0.30", "-4")])
    assert res.fills == []
    assert res.state.traded_at_price == Decimal("0.00")
    assert res.state.queue_remaining == Decimal("1.00")
    assert res.state.cursor_event_id == 1002


def test_positive_delta_never_changes_queue():
    o = order(queue="5")
    res = run(o, deltas=[tdelta(1, "yes", "0.30", "50")])
    assert res.state.queue_remaining == Decimal("5.00")
    assert res.state.traded_at_price == Decimal("0.00")
    assert res.state.cursor_event_id == 1001


def test_deltas_off_our_price_or_side_never_touch_the_queue():
    o = order(queue="5")
    res = run(o, deltas=[tdelta(1, "yes", "0.29", "-9"), tdelta(2, "no", "0.30", "-9")])
    assert res.state.queue_remaining == Decimal("5.00")


def test_opposite_side_prints_ignored():
    o = order(queue="5")
    # A YES taker lifts a resting NO bid; it never reaches our YES order.
    res = run(o, prints=[tprint(1, "0.30", "8", taker_side="yes")])
    assert res.fills == []
    assert res.state.queue_remaining == Decimal("5.00")
    assert res.state.traded_at_price == Decimal("0.00")


def test_print_without_side_never_fills():
    o = order(queue="0")
    res = run(o, prints=[tprint(1, "0.30", "8", taker_side=None)])
    assert res.fills == []
    assert res.state.traded_at_price == Decimal("0.00")


def test_prints_above_our_price_never_fill():
    o = order(queue="0")
    res = run(o, prints=[tprint(1, "0.31", "8")])
    assert res.fills == []
    assert res.state.traded_at_price == Decimal("0.00")


def test_no_side_order_fills_on_yes_taker_at_or_above_one_minus_prob():
    o = order(side="no", prob="0.70", contracts="10", queue="2")
    res = run(o, prints=[
        tprint(1, "0.25", "5", taker_side="yes"),   # NO price 0.75, above ours: ignored
        tprint(2, "0.30", "5", taker_side="yes"),   # NO price 0.70, at ours: 2 queued, 3 fill
        tprint(3, "0.35", "4", taker_side="yes"),   # NO price 0.65, through us
    ])
    assert [(f.contracts, f.through) for f in res.fills] == [
        (Decimal("3.00"), False), (Decimal("4.00"), True)]
    assert all(f.prob == Decimal("0.7000") for f in res.fills)
    assert res.state.traded_at_price == Decimal("5.00")
    assert res.state.filled_contracts == Decimal("7.00")


def test_fills_stop_at_the_order_size():
    o = order(queue="0", contracts="10")
    res = run(o, prints=[tprint(1, "0.30", "8"), tprint(2, "0.30", "8")])
    assert [f.contracts for f in res.fills] == [Decimal("8.00"), Decimal("2.00")]
    assert res.state.filled_contracts == Decimal("10.00")
    # Once full, a further print still counts toward the per-price accumulator.
    more = run(o, prints=[tprint(3, "0.30", "5")], state=res.state)
    assert more.fills == []
    assert more.state.traded_at_price == Decimal("21.00")


def test_prints_after_deltas_at_the_same_timestamp():
    o = order(queue="5")
    # If the print were taken first it would consume 3 of the queue and the -3 delta would
    # then be attributed to those trades, leaving the queue at 2. Deltas go first, so the -3
    # is a pure cancel, the queue drops to 2 and the print fills 1 of its 3.
    res = run(o, prints=[tprint(1, "0.30", "3")], deltas=[tdelta(1, "yes", "0.30", "-3")])
    assert [f.contracts for f in res.fills] == [Decimal("1.00")]
    assert res.state.queue_remaining == Decimal("0.00")


# --- cross -------------------------------------------------------------------


def test_cross_recorded_once_and_not_in_fills():
    o = order(queue="0", contracts="10")
    bk = book(yes={"0.29": "100"}, no={"0.68": "50"})   # best_ask(yes) = 0.32, no cross yet
    res = run(o, bk=bk, deltas=[
        tdelta(1, "no", "0.70", "20", event_id=1001),   # best_ask(yes) = 0.30: crosses
        tdelta(2, "no", "0.71", "5", event_id=1002),    # still crossed, no second fill
    ], prints=[tprint(3, "0.30", "10")])
    assert res.crossed is True
    assert res.cross is not None
    assert res.cross.fill_method == "snapshot_cross"
    assert res.cross.contracts == Decimal("10.00")
    assert res.cross.source_event_id == 1001
    assert res.cross.source_trade_id is None
    assert res.cross.filled_at == at(1)
    assert res.cross.through is False
    assert res.cross.tape_source == "ws"
    assert res.cross not in res.fills
    # The cross is a parallel worst case: it never consumes the queue-model size.
    assert [f.contracts for f in res.fills] == [Decimal("10.00")]
    assert all(f.fill_method == "queue_model" for f in res.fills)
    assert res.state.filled_contracts == Decimal("10.00")


def test_initial_crossing_book_sets_crossed():
    o = order(queue="0", contracts="10")
    bk = book(no={"0.72": "40"}, as_of=at(-5), event_id=900)   # best_ask(yes) = 0.28 <= 0.30
    res = run(o, bk=bk)
    assert res.crossed is True
    assert res.cross.contracts == Decimal("10.00")
    assert res.cross.filled_at == at(-5)
    assert res.cross.source_event_id == 900
    assert res.fills == []


def test_cross_size_is_what_is_left_unfilled():
    o = order(queue="0", contracts="10")
    bk = book(yes={"0.29": "100"}, no={"0.68": "50"})
    res = run(o, bk=bk, prints=[tprint(1, "0.30", "4")],
              deltas=[tdelta(2, "no", "0.70", "20", event_id=1002)])
    assert res.state.filled_contracts == Decimal("4.00")
    assert res.cross.contracts == Decimal("6.00")


def test_no_book_means_no_cross():
    o = order(queue="0")
    res = run(o, prints=[tprint(1, "0.30", "4")])
    assert (res.crossed, res.cross) == (False, None)


def test_simulate_fills_does_not_mutate_the_caller_s_book():
    o = order(queue="0", contracts="10")
    bk = book(yes={"0.29": "100"}, no={"0.68": "50"})
    before = (dict(bk.yes_bids), dict(bk.no_bids), bk.seq, bk.dirty, bk.last_event_id, bk.as_of)
    run(o, bk=bk, deltas=[tdelta(1, "no", "0.70", "20", event_id=1001, seq=6),
                          tdelta(2, "yes", "0.29", "-100", event_id=1002, seq=9)])
    assert (bk.yes_bids, bk.no_bids, bk.seq, bk.dirty, bk.last_event_id, bk.as_of) == before


# --- deadline, cursor, fees --------------------------------------------------


def test_events_after_deadline_ignored():
    o = order(queue="0", contracts="10")
    deadline = at(10)
    res = run(o, deadline=deadline,
              prints=[tprint(5, "0.30", "2"), tprint(11, "0.30", "7")],
              deltas=[tdelta(6, "yes", "0.30", "-1", event_id=1006),
                      tdelta(12, "yes", "0.30", "-1", event_id=1012)])
    assert [f.contracts for f in res.fills] == [Decimal("2.00")]
    assert res.state.cursor_event_id == 1006


def test_events_at_or_before_placement_ignored():
    o = order(queue="0", contracts="10", placed_at=at(5))
    res = run(o, prints=[tprint(4, "0.30", "2"), tprint(5, "0.30", "2"), tprint(6, "0.30", "3")],
              deltas=[tdelta(4, "yes", "0.30", "-1", event_id=1004)])
    assert [f.contracts for f in res.fills] == [Decimal("3.00")]
    assert res.state.cursor_event_id is None


def test_deltas_at_or_before_the_cursor_ignored():
    o = order(queue="5")
    state = SimState(queue_remaining=Decimal("5"), traded_at_price=Decimal("0"),
                     filled_contracts=Decimal("0"), cursor_event_id=1002)
    res = run(o, state=state, deltas=[tdelta(1, "yes", "0.30", "-2", event_id=1002),
                                      tdelta(2, "yes", "0.30", "-1", event_id=1003)])
    assert res.state.queue_remaining == Decimal("4.00")
    assert res.state.cursor_event_id == 1003


def test_no_queue_means_no_fills_and_no_cursor_advance():
    o = order(queue=None)
    state = SimState.initial(o)
    assert state.queue_remaining is None
    # R10: an order placed with no book is out of fill simulation entirely until a book first
    # exists, so even a book that crosses our price produces nothing here.
    res = run(o, state=state, bk=book(no={"0.72": "40"}),
              prints=[tprint(1, "0.28", "4")], deltas=[tdelta(2, "yes", "0.30", "-1")])
    assert res.fills == []
    assert (res.crossed, res.cross) == (False, None)
    assert res.state.queue_remaining is None
    assert res.state.cursor_event_id is None
    assert res.state.filled_contracts == Decimal("0.00")


def test_fee_is_centicent_per_fill():
    o = order(queue="5", contracts="10")
    res = run(o, prints=[tprint(1, "0.30", "8")])
    expected = ceil_to_centicent(Decimal("0.0175") * Decimal("1") * Decimal("3")
                                 * Decimal("0.30") * Decimal("0.70"))
    assert expected == Decimal("0.0111")
    assert res.fills[0].fee == expected
    # Two small fills of the same total cost strictly more than one, which is the point of
    # per-fill ceiling: 2 x ceil(0.0018375) = 0.0038 against ceil(0.0036750) = 0.0037.
    o2 = order(queue="0", contracts="1")
    split = run(o2, prints=[tprint(1, "0.30", "0.50"), tprint(2, "0.30", "0.50")])
    assert [f.fee for f in split.fills] == [Decimal("0.0019"), Decimal("0.0019")]


def test_fee_model_multiplier_is_applied():
    half = FeeModel(Decimal("0.0175"), Decimal("0.07"), Decimal("0.5"))
    o = order(queue="0", contracts="3")
    res = run(o, prints=[tprint(1, "0.30", "3")], fee_model=half)
    assert res.fills[0].fee == ceil_to_centicent(
        Decimal("0.0175") * Decimal("0.5") * Decimal("3") * Decimal("0.30") * Decimal("0.70"))


def test_fill_method_is_passed_through_for_the_no_watcher_track():
    o = order(queue="0")
    res = run(o, prints=[tprint(1, "0.30", "4")], fill_method="no_watcher")
    assert [f.fill_method for f in res.fills] == ["no_watcher"]


# --- has_print ---------------------------------------------------------------


def test_has_print_true_and_false():
    o = order(queue="0", contracts="10", placed_at=T0)
    fill = SimFill(prob=Decimal("0.30"), contracts=Decimal("2"), fee=Decimal("0"),
                   filled_at=at(30), fill_method="queue_model", source_trade_id="x",
                   source_event_id=None, taker_side="no", through=False, tape_source="ws")
    assert has_print(fill, o, [tprint(30, "0.30", "2")]) is True
    assert has_print(fill, o, [tprint(85, "0.28", "2")]) is True     # inside filled_at + 60 s
    assert has_print(fill, o, [tprint(91, "0.30", "2")]) is False    # past the window
    assert has_print(fill, o, [tprint(-1, "0.30", "2")]) is False    # before placement
    assert has_print(fill, o, [tprint(30, "0.31", "2")]) is False    # above our price
    assert has_print(fill, o, [tprint(30, "0.30", "2", taker_side="yes")]) is False
    assert has_print(fill, o, [tprint(30, "0.30", "2", taker_side=None)]) is False
    assert has_print(fill, o, []) is False
    assert has_print(fill, o, [tprint(30, "0.30", "2")], window_s=0) is True
    assert has_print(fill, o, [tprint(31, "0.30", "2")], window_s=0) is False


# --- purity ------------------------------------------------------------------


def _busy_tape():
    prints = [tprint(1, "0.30", "3"), tprint(4, "0.28", "2"), tprint(6, "0.30", "1.50"),
              tprint(7, "0.30", "4", taker_side="yes"), tprint(9, "0.30", "2")]
    deltas = [tdelta(2, "yes", "0.30", "-4", event_id=1002),
              tdelta(3, "yes", "0.30", "9", event_id=1003),
              tdelta(5, "no", "0.70", "-3", event_id=1005),
              tdelta(8, "yes", "0.30", "-2", event_id=1008)]
    return prints, deltas


def test_chunking_invariance():
    o = order(queue="6", contracts="10")
    prints, deltas = _busy_tape()
    whole = run(o, prints=prints, deltas=deltas)
    first = run(o, prints=[p for p in prints if p.ts <= at(5)],
                deltas=[d for d in deltas if d.ts <= at(5)], deadline=at(5))
    second = run(o, state=first.state, prints=[p for p in prints if p.ts > at(5)],
                 deltas=[d for d in deltas if d.ts > at(5)])
    assert first.fills + second.fills == whole.fills
    assert second.state == whole.state
    # The tape has to actually do something for the equality to mean anything.
    assert len(whole.fills) >= 3 and first.fills and second.fills
    assert whole.state.filled_contracts > Decimal("0")


def test_deterministic():
    o = order(queue="6", contracts="10")
    prints, deltas = _busy_tape()
    bk = book(yes={"0.29": "100"}, no={"0.68": "50"})
    state = SimState.initial(o)
    a = run(o, prints=prints, deltas=deltas, bk=bk, state=state)
    b = run(o, prints=prints, deltas=deltas, bk=bk, state=state)
    assert a == b
    # Nothing the first run touched leaked into the caller's state or order.
    assert (state.queue_remaining, state.traded_at_price, state.filled_contracts,
            state.cursor_event_id) == (Decimal("6.00"), Decimal("0.00"), Decimal("0.00"), None)
    assert a.state is not state
    assert o.contracts == Decimal("10.00")


def test_input_lists_are_not_reordered_or_consumed():
    o = order(queue="0", contracts="10")
    prints, deltas = _busy_tape()
    p_before, d_before = list(prints), list(deltas)
    run(o, prints=prints, deltas=deltas)
    assert prints == p_before and deltas == d_before
