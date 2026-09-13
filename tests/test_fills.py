from datetime import datetime, timedelta, timezone
from decimal import Decimal
from decimal import Decimal as D
from types import SimpleNamespace as NS

from harness.execution import EXECUTOR_VERSION
from harness.execution.book import BookState
from harness.execution.fills import (
    _DELTA,
    _PRINT,
    FillResult,
    PaperOrder,
    SimFill,
    SimState,
    TapeDelta,
    TapePrint,
    _merge_events,
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


def book(yes=None, no=None, as_of=T0, source="ws", event_id=900, last_event_id=None) -> BookState:
    return BookState(ticker="K1", yes_bids={Decimal(k): Decimal(v) for k, v in (yes or {}).items()},
                     no_bids={Decimal(k): Decimal(v) for k, v in (no or {}).items()},
                     sid=2, seq=5, as_of=as_of, source=source, anchor_id=event_id,
                     last_event_id=event_id if last_event_id is None else last_event_id)


def run(o, prints=(), deltas=(), bk=None, state=None, fill_method="queue_model",
        deadline=DEADLINE, fee_model=KALSHI_FOOTBALL, cancel_policy="ahead") -> FillResult:
    return simulate_fills(o, state if state is not None else SimState.initial(o), bk,
                          list(prints), list(deltas), deadline, fill_method, fee_model,
                          cancel_policy)


# --- primitives --------------------------------------------------------------


def test_executor_version_is_bumped_for_fills():
    assert EXECUTOR_VERSION == "4.4"


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
    assert res.state.print_unmatched == Decimal("8.00")
    assert res.state.filled_contracts == Decimal("3.00")
    assert res.crossed is False and res.cross is None


def test_sweep_through_zeroes_queue_and_fills():
    o = order(queue="100")
    res = run(o, prints=[tprint(1, "0.28", "4")])
    assert [(f.contracts, f.through) for f in res.fills] == [(Decimal("4.00"), True)]
    assert res.state.queue_remaining == Decimal("0.00")
    # A sweep through our level says nothing about how much traded *at* our price.
    assert res.state.print_unmatched == Decimal("0.00")
    assert res.state.filled_contracts == Decimal("4.00")


def test_fractional_print_consumes_half_a_contract():
    o = order(queue="0", contracts="10")
    res = run(o, prints=[tprint(1, "0.30", "0.50")])
    assert [f.contracts for f in res.fills] == [Decimal("0.50")]
    assert res.state.filled_contracts == Decimal("0.50")
    assert res.state.print_unmatched == Decimal("0.50")


def test_negative_delta_attributed_to_trades_first_then_cancels():
    o = order(queue="5")
    # The print takes 3 of the 5 ahead of us; the venue's own -4 delta at our price reports
    # the same 3 trades plus 1 genuine cancel, so only that 1 shortens the queue again. Under
    # 6B's ledger the 3 the print already explained are its `print_unmatched`, which the delta
    # matches and spends, and the 1 left over is a pending bucket: unclaimed decrement volume
    # that took queue ahead of us and has not yet aged out of the horizon.
    res = run(o, prints=[tprint(1, "0.30", "3")], deltas=[tdelta(2, "yes", "0.30", "-4")])
    assert res.fills == []
    assert res.state.print_unmatched == Decimal("0.00")
    assert res.state.pending_unmatched == Decimal("1.00")
    assert res.state.queue_remaining == Decimal("1.00")
    assert res.state.cursor_event_id == 1002


def test_positive_delta_never_changes_queue():
    o = order(queue="5")
    res = run(o, deltas=[tdelta(1, "yes", "0.30", "50")])
    assert res.state.queue_remaining == Decimal("5.00")
    assert res.state.print_unmatched == Decimal("0.00")
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
    assert res.state.print_unmatched == Decimal("0.00")


def test_print_without_side_never_fills():
    o = order(queue="0")
    res = run(o, prints=[tprint(1, "0.30", "8", taker_side=None)])
    assert res.fills == []
    assert res.state.print_unmatched == Decimal("0.00")


def test_prints_above_our_price_never_fill():
    o = order(queue="0")
    res = run(o, prints=[tprint(1, "0.31", "8")])
    assert res.fills == []
    assert res.state.print_unmatched == Decimal("0.00")


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
    assert res.state.print_unmatched == Decimal("5.00")
    assert res.state.filled_contracts == Decimal("7.00")


def test_fills_stop_at_the_order_size():
    o = order(queue="0", contracts="10")
    res = run(o, prints=[tprint(1, "0.30", "8"), tprint(2, "0.30", "8")])
    assert [f.contracts for f in res.fills] == [Decimal("8.00"), Decimal("2.00")]
    assert res.state.filled_contracts == Decimal("10.00")
    # Once full, a further print is still print volume whose decrement has not arrived.
    more = run(o, prints=[tprint(3, "0.30", "5")], state=res.state)
    assert more.fills == []
    assert more.state.print_unmatched == Decimal("21.00")


def test_deltas_are_still_merged_before_prints_at_the_same_timestamp():
    """The merge order is unchanged; what it used to imply is not (6B §0.5, §1.3).

    This case asserted the arithmetic that order produced -- the -3 read as a pure cancel, the
    queue to 0 and 1 contract crossing into our own order -- which is the double count C3
    repairs and which `test_a_print_and_its_matching_delta_move_the_queue_once` now owns. The
    ordering itself is kept and is asserted here directly, on `_merge_events`, because the
    ledger's answer must no longer depend on it: the four feeds of
    `test_a_split_delta_and_a_split_print_reconcile_to_the_same_answer` are the proof that it
    does not.
    """
    o = order(queue="5")
    merged = _merge_events([tprint(1, "0.30", "3")], [tdelta(1, "yes", "0.30", "-3")],
                           o.placed_at, DEADLINE, None, None)
    assert [kind for _ts, kind, _i, _event in merged] == [_DELTA, _PRINT]


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
    # best_ask(yes) = 0.28 <= 0.30. The anchor is row 900 and the book has since been advanced
    # to row 955; the cross is stamped with the anchor, which does not move between loops.
    bk = book(no={"0.72": "40"}, as_of=at(-5), event_id=900, last_event_id=955)
    res = run(o, bk=bk)
    assert res.crossed is True
    assert res.cross.contracts == Decimal("10.00")
    assert res.cross.filled_at == at(-5)
    assert res.cross.source_event_id == 900
    assert res.state.crossed is True
    assert res.fills == []


def test_initial_cross_is_emitted_once_across_calls():
    o = order(queue="0", contracts="10")
    first = run(o, bk=book(no={"0.72": "40"}, as_of=at(-5), event_id=900, last_event_id=955))
    assert first.cross.source_event_id == 900
    # The next loop hands over the same anchor advanced further along the tape. Re-emitting
    # here would write a second worst-case fill for one crossing under a new event id.
    later = book(no={"0.72": "40"}, as_of=at(20), event_id=900, last_event_id=1200)
    second = run(o, bk=later, state=first.state)
    assert second.cross is None
    assert second.crossed is True
    assert second.fills == []


def test_cross_from_a_delta_is_emitted_once_across_calls():
    o = order(queue="0", contracts="10")
    bk = book(yes={"0.29": "100"}, no={"0.68": "50"})
    first = run(o, bk=bk, deltas=[
        tdelta(1, "no", "0.60", "20", event_id=5),    # best_ask(yes) still 0.32
        tdelta(2, "no", "0.70", "20", event_id=7),    # best_ask(yes) = 0.30: crosses
        tdelta(3, "no", "0.71", "5", event_id=9),     # still crossed
    ])
    assert first.cross.source_event_id == 7
    assert first.state.crossed is True
    advanced = book(yes={"0.29": "100"}, no={"0.68": "50", "0.70": "20", "0.71": "5"})
    second = run(o, bk=advanced, state=first.state,
                 deltas=[tdelta(4, "no", "0.72", "5", event_id=11)])
    assert second.cross is None
    assert second.crossed is True


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
    state = SimState(queue_remaining=Decimal("5"), filled_contracts=Decimal("0"),
                     cursor_event_id=1002)
    res = run(o, state=state, deltas=[tdelta(1, "yes", "0.30", "-2", event_id=1002),
                                      tdelta(2, "yes", "0.30", "-1", event_id=1003)])
    assert res.state.queue_remaining == Decimal("4.00")
    assert res.state.cursor_event_id == 1003


def test_cursor_is_the_highest_event_id_seen():
    o = order(queue="5")
    # `event_id` is the recorder's insertion order and the venue's `ts` need not agree with it,
    # so the cursor has to be the high-water mark rather than whatever came last in `ts` order.
    res = run(o, deltas=[tdelta(1, "yes", "0.30", "-1", event_id=1009),
                         tdelta(2, "yes", "0.30", "-1", event_id=1004)])
    assert res.state.queue_remaining == Decimal("3.00")
    assert res.state.cursor_event_id == 1009


# --- print idempotence: the floor and the id set (§0.6) ----------------------


def test_refeeding_the_same_prints_changes_nothing():
    # The executor has no print cursor: every loop rescans from `placed_at - 60 s` (§1), so the
    # same trade arrives again and again and must not be applied twice.
    o = order(queue="5", contracts="10")
    prints = [tprint(1, "0.30", "3"), tprint(2, "0.28", "3")]
    first = run(o, prints=prints)
    assert [f.contracts for f in first.fills] == [Decimal("3.00")]
    again = run(o, prints=prints, state=first.state)
    assert again.fills == []
    assert again.state == first.state


def test_two_prints_at_one_timestamp_both_apply_once():
    o = order(queue="0", contracts="10")
    a = tprint(1, "0.30", "2", trade_id="a")
    b = tprint(1, "0.30", "3", trade_id="b")
    first = run(o, prints=[a])
    assert first.state.trade_ids == ((at(1), "a"),)
    second = run(o, prints=[a, b], state=first.state)
    assert [f.contracts for f in second.fills] == [Decimal("3.00")]
    assert second.state.trade_ids == ((at(1), "a"), (at(1), "b"))
    assert second.state.filled_contracts == Decimal("5.00")
    assert run(o, prints=[a, b], state=second.state).fills == []


def test_the_id_set_keeps_every_print_in_the_track_s_window():
    """6B §0.6 replaces the timestamp watermark: the id set governs everything above the floor.

    The old shape kept only the ids stamped at exactly `last_print_ts` and dropped the rest when
    the timestamp moved on, because the timestamp itself was the skip test. With that skip test
    gone -- it is what made a late REST backfill unusable -- identity is the whole answer, so
    every print inside the window the executor re-reads has to stay recognisable. The window
    starts at `placed_at - 60 s`, so all three ids here are inside it and none is dropped, and no
    floor is set because nothing anchored.
    """
    o = order(queue="0", contracts="10")
    res = run(o, prints=[tprint(1, "0.30", "1", trade_id="a"),
                         tprint(2, "0.30", "1", trade_id="b"),
                         tprint(2, "0.30", "1", trade_id="c")])
    assert res.state.trade_ids == ((at(1), "a"), (at(2), "b"), (at(2), "c"))
    assert res.state.print_floor is None


def test_a_late_rest_backfill_above_the_floor_is_applied_not_skipped():
    """The behaviour §0.6 reverses, and the floor that replaces it.

    A REST backfill can land after a WS print with an earlier `ts`. The pre-6B watermark skipped
    it, which is what silently did the re-anchor's work and what made a real backfilled trade
    unusable; with the ledger, a decrement already folded in is remembered as a bucket or as
    `print_unmatched` and cannot be charged twice, so the backfill is applied on its own merits.
    Below a `print_floor` -- the only bound that remains -- it is still dropped, because a print
    stamped before an anchoring book's instant is already inside the queue that book established.
    """
    o = order(queue="0", contracts="10")
    first = run(o, prints=[tprint(5, "0.30", "2")])
    late_print = tprint(3, "0.30", "4", trade_id="late", source="rest")
    late = run(o, state=first.state, prints=[late_print])
    assert [f.contracts for f in late.fills] == [Decimal("4.00")]
    assert late.state.filled_contracts == Decimal("6.00")

    anchored = first.state._copy()
    anchored.print_floor = at(4)
    assert run(o, state=anchored, prints=[late_print]).fills == []


def test_a_print_that_never_hit_us_still_joins_the_id_set():
    # A print we ignored is still recorded, so a later loop cannot replay it either.
    o = order(queue="0", contracts="10")
    first = run(o, prints=[tprint(1, "0.30", "4", taker_side="yes")])
    assert first.state.trade_ids == ((at(1), "t1"),)
    assert run(o, prints=[tprint(1, "0.30", "4", taker_side="yes")], state=first.state).fills == []


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
    assert (res.state.print_floor, res.state.trade_ids) == (None, ())
    assert res.state.crossed is False
    assert res.state == state


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
    # `SimState` is a dataclass, so the equality above already covers the ledger; these four
    # name which part of it diverged instead of printing two whole states (6B §1.3).
    assert second.state.buckets == whole.state.buckets
    assert second.state.trade_ids == whole.state.trade_ids
    assert second.state.print_unmatched == whole.state.print_unmatched
    assert second.state.cancels_ahead == whole.state.cancels_ahead
    # The tape has to actually do something for the equality to mean anything.
    assert len(whole.fills) >= 3 and first.fills and second.fills
    assert whole.state.filled_contracts > Decimal("0")


def test_chunking_invariance_with_a_book():
    """The same equality with the book half of the walk live: a cross lands in the second
    chunk, so the split has to agree about the crossing event as well as the fills."""
    o = order(queue="4", contracts="10")
    prints = [tprint(1, "0.30", "6"), tprint(6, "0.30", "2"), tprint(9, "0.28", "1")]
    deltas = [tdelta(2, "yes", "0.30", "-3", event_id=1002),
              tdelta(5, "no", "0.69", "20", event_id=1005),
              tdelta(7, "no", "0.70", "15", event_id=1007),   # best_ask(yes) = 0.30: crosses
              tdelta(8, "no", "0.71", "5", event_id=1008)]
    split = at(5)

    def fresh():
        return book(yes={"0.29": "100"}, no={"0.68": "50"})

    whole = run(o, prints=prints, deltas=deltas, bk=fresh())
    # Between chunks the caller advances its own book, which is what `advance_book` does.
    carried = fresh()
    for d in (d for d in deltas if d.ts <= split):
        carried.apply_delta(d.side, d.price, d.delta, None, d.ts, d.event_id)
    first = run(o, bk=fresh(), deadline=split,
                prints=[p for p in prints if p.ts <= split],
                deltas=[d for d in deltas if d.ts <= split])
    second = run(o, bk=carried, state=first.state,
                 prints=[p for p in prints if p.ts > split],
                 deltas=[d for d in deltas if d.ts > split])
    assert first.fills + second.fills == whole.fills
    assert second.state == whole.state
    assert (first.cross, first.crossed) == (None, False)
    assert second.cross == whole.cross
    assert whole.cross.source_event_id == 1007
    assert whole.cross.contracts == Decimal("6.00")
    assert len(whole.fills) == 3 and first.fills and second.fills


def test_deterministic():
    o = order(queue="6", contracts="10")
    prints, deltas = _busy_tape()
    bk = book(yes={"0.29": "100"}, no={"0.68": "50"})
    state = SimState.initial(o)
    a = run(o, prints=prints, deltas=deltas, bk=bk, state=state)
    b = run(o, prints=prints, deltas=deltas, bk=bk, state=state)
    assert a == b
    # Nothing the first run touched leaked into the caller's state or order.
    assert (state.queue_remaining, state.print_unmatched, state.filled_contracts,
            state.cursor_event_id) == (Decimal("6.00"), Decimal("0.00"), Decimal("0.00"), None)
    assert a.state is not state
    assert o.contracts == Decimal("10.00")


def test_input_lists_are_not_reordered_or_consumed():
    o = order(queue="0", contracts="10")
    prints, deltas = _busy_tape()
    p_before, d_before = list(prints), list(deltas)
    run(o, prints=prints, deltas=deltas)
    assert prints == p_before and deltas == d_before


# --- the reconciliation ledger (6B §1.3) -------------------------------------


def test_a_print_and_its_matching_delta_move_the_queue_once():
    """Expected queue 2, fill 0, `cancels_ahead` 0 -- in either arrival order.

    Derived from the tape, not from the code: 5 contracts rest ahead of us at 0.30. One real
    trade of 3 lifts 3 of them, leaving 2 ahead and nothing for us. The book delta of -3 at the
    same price is the exchange reporting that same trade; a cancellation and a trade cannot both
    be the whole of a -3 that a print of 3 already explains. The queue therefore moves once, by
    3, and the 2 still ahead of us are still ahead of us. Nothing was cancelled, so
    `cancels_ahead` is 0.
    """
    for label, events in (("delta first", dict(prints=[tprint(1, ".30", "3")],
                                               deltas=[tdelta(1, "yes", ".30", "-3")])),
                          ("print first", dict(prints=[tprint(1, ".30", "3", trade_id="p")],
                                               deltas=[tdelta(2, "yes", ".30", "-3")]))):
        result = run(order(queue="5"), **events)
        assert result.state.queue_remaining == D(2), label
        assert result.state.filled_contracts == D(0), label
        assert result.state.cancels_ahead == D(0), label


def test_a_split_delta_and_a_split_print_reconcile_to_the_same_answer():
    """Expected queue 2, fill 0 for each of the four splittings.

    Derived independently: the venue may report one trade of 3 as one delta of -3 or as -2 then
    -1, and may print it as one row of 3 or as 2 then 1. None of that changes what happened --
    three contracts ahead of us traded -- so all four feeds must land on queue 2 and fill 0. An
    implementation that matched whole events rather than volume would pass one and fail three.
    """
    feeds = [
        ([tprint(1, ".30", "3")], [tdelta(1, "yes", ".30", "-2", event_id=1),
                                   tdelta(1, "yes", ".30", "-1", event_id=2)]),
        ([tprint(1, ".30", "2", trade_id="a"), tprint(1, ".30", "1", trade_id="b")],
         [tdelta(1, "yes", ".30", "-3")]),
        ([tprint(1, ".30", "2", trade_id="a"), tprint(2, ".30", "1", trade_id="b")],
         [tdelta(1, "yes", ".30", "-2", event_id=1), tdelta(2, "yes", ".30", "-1", event_id=2)]),
        ([tprint(2, ".30", "3")], [tdelta(1, "yes", ".30", "-3")]),
    ]
    for i, (prints, deltas) in enumerate(feeds):
        result = run(order(queue="5"), prints=prints, deltas=deltas)
        assert result.state.queue_remaining == D(2), i
        assert result.state.filled_contracts == D(0), i


def test_a_decrement_beyond_the_queue_reaches_us_when_its_print_arrives():
    """Expected queue 0, fill 3 -- in either arrival order (the `pending_surplus` path).

    Derived independently: 2 rest ahead of us and a trade of 5 goes off at our price. Two of
    those five were the contracts ahead of us; the other three had to come from somewhere, and
    the only resting size left at that price is ours. So we trade 3 and the queue is empty. When
    the matching delta of -5 arrives first, the 3 beyond the queue are decrement volume the
    queue cannot explain -- `pending_surplus` -- and the print that follows is what turns them
    into our fill.
    """
    for label, (prints, deltas) in (
            ("print alone", ([tprint(1, ".30", "5")], [])),
            ("delta first", ([tprint(1, ".30", "5")], [tdelta(1, "yes", ".30", "-5")]))):
        result = run(order(queue="2"), prints=prints, deltas=deltas)
        assert result.state.queue_remaining == D(0), label
        assert result.state.filled_contracts == D(3), label


def test_a_genuine_cancellation_outside_the_horizon_does_not_absorb_a_later_trade():
    """Expected queue 0, fill 3, `cancels_ahead` 2 (the case a horizon-less ledger breaks).

    Derived independently: 2 rest ahead of us and both are cancelled at T+1 -- no print
    accompanies them, and none arrives within `store.PRINT_LOOKBACK` (60 s), so they left the
    book rather than trading. We are now at the front of the queue. Ten minutes later a real
    trade of 3 goes off at our price; with nothing ahead of us all 3 are ours. A ledger that let
    the ten-minute-old decrement be claimed by that print would report fill 1, which is what
    today's `traded_at_price` accumulator would do if the horizon were removed.
    """
    result = run(order(queue="2"),
                 prints=[tprint(600, ".30", "3")],
                 deltas=[tdelta(1, "yes", ".30", "-2")])
    assert result.state.queue_remaining == D(0)
    assert result.state.filled_contracts == D(3)
    assert result.state.cancels_ahead == D(2)


def test_a_print_through_our_price_still_sweeps_the_queue():
    """Expected queue 0, fill 4 -- unchanged from the pre-6B rule.

    Derived independently: a trade at 0.25 against a YES order at 0.30 happened past our level,
    so everything resting between is gone by definition and our size trades up to the print's
    own count. It says nothing about volume *at* our price, so no ledger term moves.
    """
    result = run(order(queue="5"), prints=[tprint(1, ".25", "4")])
    assert result.state.queue_remaining == D(0)
    assert result.state.filled_contracts == D(4)
    assert result.state.print_unmatched == D(0)


def test_the_ledger_survives_a_persisted_loop_boundary():
    """Expected: the same queue 2 and fill 0 as the one-call feed.

    Derived independently: the loop persists a track's state between steps and reads it back, so
    a delta in one loop and its matching print in the next must reconcile exactly as they do
    inside one call. The state that crosses the boundary here carries one `pending` bucket of 3;
    if the buckets did not persist, the print would arrive against an empty ledger and take the
    queue a second time, to 0, with 1 contract crossing into our order -- the original defect,
    reappearing one loop later.
    """
    from harness.execution.state import _state_columns, _state_of

    first = run(order(queue="5"), deltas=[tdelta(1, "yes", ".30", "-3")], deadline=at(2))
    assert first.state.pending_unmatched == D(3)
    row = NS(**dict(_state_columns("", first.state),
                    filled_contracts=first.state.filled_contracts))
    resumed = _state_of(row, "")
    second = run(order(queue="5"), prints=[tprint(1, ".30", "3")], state=resumed)
    assert second.state.queue_remaining == D(2)
    assert second.state.filled_contracts == D(0)


def test_the_behind_policy_agrees_with_ahead_whenever_nothing_is_retired():
    """Expected: identical queue and fills when `cancels_ahead` is 0; a smaller fill under
    `behind` when it is not (ruling I-10: the implication only).

    Derived independently: the two policies differ only about decrement volume nobody claimed.
    When every decrement is explained by a print inside the horizon, both policies agree that
    those contracts traded and both move the queue by the same amount -- `ahead` at the delta,
    `behind` at the print -- so the two answers are identical.

    When a decrement retires unclaimed they part. In the second case 2 contracts leave the level
    at T+1 with no print, and 10 minutes later a real trade of 3 goes off at our price. Under
    `ahead` those 2 were in front of us, so we were at the head of the queue and all 3 are ours.
    Under `behind` they were never in front of us, so 2 still are, 2 of the 3 go to them and 1 is
    ours. Both end with an empty queue; the disagreement is in the fills, which is why the point
    estimate is the optimistic one about cancellations and the band says by how much (D3).
    """
    claimed = dict(prints=[tprint(1, ".30", "3")], deltas=[tdelta(1, "yes", ".30", "-3")])
    ahead = run(order(queue="5"), **claimed)
    behind = run(order(queue="5"), cancel_policy="behind", **claimed)
    assert ahead.state.cancels_ahead == D(0)
    assert (behind.state.queue_remaining, behind.state.filled_contracts) == (
        ahead.state.queue_remaining, ahead.state.filled_contracts)

    # The band's second half, and the reason it is an implication: a retired bucket is where
    # the two policies part.
    retired = dict(prints=[tprint(600, ".30", "3")], deltas=[tdelta(1, "yes", ".30", "-2")])
    ahead = run(order(queue="2"), **retired)
    behind = run(order(queue="2"), cancel_policy="behind", **retired)
    assert ahead.state.cancels_ahead == D(2) and behind.state.cancels_ahead == D(2)
    assert ahead.state.filled_contracts == D(3)
    assert behind.state.filled_contracts == D(1)


def test_the_trade_id_set_stays_inside_the_tracks_own_window():
    """Expected: an id stamped before `placed_at - RECON_HORIZON` is dropped on the next write,
    an id inside that window is kept, and the floor does not move (ruling IM-12, §2's "pruned to
    the horizon and the track window on every write").

    Derived independently: the executor re-reads prints from `placed_at - PRINT_LOOKBACK` to the
    loop instant every loop (`harness/execution/loop.py:729`), so the window's lower bound is
    fixed at `placed_at - 60 s` while its upper bound grows with the loop. An id below that
    bound can never be offered to this track again and keeping it can only cost memory. An id
    above it is re-offered on every later loop however old it is, so dropping it would let its
    print apply a second time -- the double count this task exists to remove. The pruning is
    therefore the window's lower bound and nothing narrower, and `TRADE_ID_CAP` is what bounds
    the set for an order resting for hours on a busy ticker, with `print_floor` rising to the
    oldest retained id as the cap's own remedy (§0.6, D5).

    Deviation from the brief, reported to the controller: the brief's version of this case fed a
    print an hour after `placed_at` and expected the earlier id to be pruned. That id is still
    inside `[placed_at - 60 s, now]`, so the executor still offers it on every loop, and pruning
    it while the floor stays where it was -- which the brief's own last assertion pins -- would
    re-apply its print. Spec §0.6 is the rule followed here: the set is "bounded by the track's
    own window (`placed_at - PRINT_LOOKBACK` to the deadline) rather than by 60 seconds, because
    `loop.py:717` re-reads the order's whole resting print history every loop".
    """
    o = order(queue="0", contracts="10", placed_at=at(1000))
    stale = (at(900), "stale")      # below `placed_at - 60 s`: never offered to us again
    recent = (at(1005), "early")    # inside the window: offered again on every later loop
    state = SimState(queue_remaining=D(0), filled_contracts=D(0), cursor_event_id=None,
                     trade_ids=(stale, recent))
    second = run(o, state=state, prints=[tprint(4600, ".30", "1", trade_id="late")],
                 deadline=at(4610))
    assert [tid for _ts, tid in second.state.trade_ids] == ["early", "late"]
    assert second.state.print_floor is None
    # The id the window kept is still recognised as seen, so its print cannot apply twice: the
    # one contract `second` took from the print of its own loop is all that was ever filled.
    again = run(o, state=second.state, prints=[tprint(1005, ".30", "1", trade_id="early")],
                deadline=at(4610))
    assert again.fills == [] and again.state == second.state
