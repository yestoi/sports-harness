import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.db.models import OrderbookEvent, OrderbookSnapshot, VenueMarket
from harness.execution import EXECUTOR_VERSION
from harness.execution.book import (
    BookState,
    advance_book,
    advance_book_at,
    book_age_s,
    book_at,
    load_book,
    load_book_at,
    opp,
    side_p,
)

NOW = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
# The `orderbook_snapshot` message body the recorder stores in `orderbook_events.raw`
# (tests/test_kalshi_ws.py), plus the recorder offset the F58 hotfix added.
WS_RAW = {"market_ticker": "K1", "yes_dollars_fp": [["0.35", "10.00"]], "no_dollars_fp": [], "recorder_offset_ms": 12}


def _rest_ladders():
    body = json.loads((Path(__file__).parent / "fixtures" / "kalshi_orderbook.json").read_text())
    ob = body["orderbook_fp"]
    return ob["yes_dollars"], ob["no_dollars"]


def test_executor_version_is_pinned():
    assert EXECUTOR_VERSION == "4.1"


def test_from_ws_raw_builds_ladder_and_resting_at():
    b = BookState.from_ws_raw("K1", WS_RAW, sid=2, seq=1, as_of=NOW, event_id=7)
    assert b.source == "ws"
    assert (b.sid, b.seq, b.anchor_id, b.last_event_id, b.dirty) == (2, 1, 7, 7, False)
    assert b.yes_bids == {Decimal("0.3500"): Decimal("10.00")}
    assert b.no_bids == {}
    assert b.resting_at("yes", Decimal("0.35")) == Decimal("10.00")
    assert b.resting_at("yes", Decimal("0.34")) == Decimal("0.00")
    assert b.best_bid("yes") == Decimal("0.3500")
    assert b.best_bid("no") is None
    assert b.best_ask("no") == Decimal("0.6500")
    assert b.best_ask("yes") is None
    assert b.mid() is None


def test_from_levels_rest_fixture():
    yes, no = _rest_ladders()
    b = BookState.from_levels("K1", yes, no, sid=0, seq=0, as_of=NOW, source="rest", anchor_id=0)
    assert b.yes_bids == {Decimal("0.2000"): Decimal("501.98")}
    assert b.no_bids == {Decimal("0.7500"): Decimal("2692.00")}
    assert b.best_bid("yes") == Decimal("0.2000")
    assert b.best_ask("yes") == Decimal("0.2500")
    assert b.mid() == Decimal("0.2250")
    assert b.resting_at("no", Decimal("0.75")) == Decimal("2692.00")


def test_from_ws_raw_raises_without_keys():
    with pytest.raises(ValueError, match="snapshot without yes_dollars_fp"):
        BookState.from_ws_raw("K1", {"market_ticker": "K1"}, sid=2, seq=1, as_of=NOW, event_id=7)


def test_delta_add_remove_clamp_prune():
    b = BookState.from_ws_raw("K1", WS_RAW, sid=2, seq=1, as_of=NOW, event_id=7)
    t1 = NOW + timedelta(seconds=1)
    b.apply_delta("yes", Decimal("0.34"), Decimal("5.00"), seq=2, ts=t1, event_id=8)
    assert b.yes_bids[Decimal("0.3400")] == Decimal("5.00")
    assert (b.seq, b.as_of, b.last_event_id, b.dirty) == (2, t1, 8, False)
    b.apply_delta("yes", Decimal("0.34"), Decimal("-2.00"), seq=3, ts=t1, event_id=9)
    assert b.yes_bids[Decimal("0.3400")] == Decimal("3.00")
    # An oversized removal floors at 0 and prunes the level rather than going negative.
    b.apply_delta("yes", Decimal("0.34"), Decimal("-9.00"), seq=4, ts=t1, event_id=10)
    assert Decimal("0.3400") not in b.yes_bids
    assert b.resting_at("yes", Decimal("0.34")) == Decimal("0.00")
    assert b.dirty is False
    b.apply_delta("no", Decimal("0.60"), Decimal("4.00"), seq=5, ts=t1, event_id=11)
    assert b.no_bids == {Decimal("0.6000"): Decimal("4.00")}


def test_seq_gap_marks_dirty():
    b = BookState.from_ws_raw("K1", WS_RAW, sid=2, seq=1, as_of=NOW, event_id=7)
    b.apply_delta("yes", Decimal("0.35"), Decimal("1.00"), seq=2, ts=NOW, event_id=8)
    assert b.dirty is False
    b.apply_delta("yes", Decimal("0.35"), Decimal("1.00"), seq=4, ts=NOW, event_id=9)
    assert b.dirty is True
    assert b.seq == 4
    assert b.yes_bids[Decimal("0.3500")] == Decimal("12.00")


def test_best_ask_no_is_one_minus_best_bid_yes():
    b = BookState.from_levels("K1", [["0.35", "10.00"], ["0.30", "5.00"]], [["0.61", "8.00"]],
                              sid=2, seq=1, as_of=NOW, source="ws", anchor_id=1)
    assert b.best_bid("yes") == Decimal("0.3500")
    assert b.best_bid("no") == Decimal("0.6100")
    assert b.best_ask("no") == Decimal("0.6500")
    assert b.best_ask("yes") == Decimal("0.3900")
    assert b.mid() == Decimal("0.3700")


def test_resting_at_no_side():
    b = BookState.from_levels("K1", [], [["0.61", "8.00"]], sid=2, seq=1, as_of=NOW, source="ws", anchor_id=1)
    assert b.resting_at("no", Decimal("0.61")) == Decimal("8.00")
    assert b.resting_at("no", Decimal("0.6100")) == Decimal("8.00")
    assert b.resting_at("yes", Decimal("0.61")) == Decimal("0.00")
    c = b.copy()
    c.apply_delta("no", Decimal("0.61"), Decimal("-8.00"), seq=2, ts=NOW, event_id=2)
    assert b.no_bids == {Decimal("0.6100"): Decimal("8.00")}
    assert c.no_bids == {}


def test_opp_and_side_p():
    assert (opp("yes"), opp("no")) == ("no", "yes")
    with pytest.raises(ValueError):
        opp("maybe")
    assert side_p(Decimal("0.3300"), "yes") == Decimal("0.3300")
    assert side_p(Decimal("0.3300"), "no") == Decimal("0.6700")


# --- DB readers -----------------------------------------------------------------

def _market(session, ticker):
    vm = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="E1", series_ticker="KXNFLGAME",
                     market_type="moneyline", first_seen_raw_id=1, last_seen_at=NOW)
    session.add(vm)
    session.flush()
    return vm


def _ws_snapshot(session, ticker, ts, sid=2, seq=1, yes=(("0.35", "10.00"),), no=()):
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="snapshot",
                         raw={"market_ticker": ticker, "yes_dollars_fp": [list(l) for l in yes],
                              "no_dollars_fp": [list(l) for l in no], "recorder_offset_ms": 3})
    session.add(row)
    session.flush()
    return row


def _delta(session, ticker, ts, side, price, delta, sid=2, seq=2):
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="delta", side=side,
                         price=Decimal(price), delta=Decimal(delta),
                         raw={"market_ticker": ticker, "side": side, "price_dollars": price, "delta_fp": delta})
    session.add(row)
    session.flush()
    return row


def _gap(session, sid, exposed_by, ts=NOW):
    row = OrderbookEvent(ticker="", ts=ts, sid=sid, seq=9, kind="gap",
                         raw={"sid": sid, "expected": 2, "got": 9, "exposed_by": exposed_by})
    session.add(row)
    session.flush()
    return row


def _rest_snapshot(session, vm, fetched_at, raw_id):
    yes, no = _rest_ladders()
    row = OrderbookSnapshot(raw_id=raw_id, venue_market_id=vm.id, fetched_at=fetched_at, yes_bids=yes, no_bids=no)
    session.add(row)
    session.flush()
    return row


def test_load_book_anchors_on_the_fresher_source(db_session):
    vm_a = _market(db_session, "A")
    vm_b = _market(db_session, "B")
    # A: the REST ladder is the fresher of the two.
    _ws_snapshot(db_session, "A", NOW - timedelta(seconds=60))
    _rest_snapshot(db_session, vm_a, NOW - timedelta(seconds=10), raw_id=1)
    # B: the WS snapshot is the fresher of the two.
    _ws_snapshot(db_session, "B", NOW - timedelta(seconds=10), sid=3)
    _rest_snapshot(db_session, vm_b, NOW - timedelta(seconds=60), raw_id=2)

    a = load_book(db_session, "A", NOW)
    assert a.source == "rest"
    assert (a.sid, a.seq) == (0, 0)
    assert a.as_of == NOW - timedelta(seconds=10)
    assert a.best_bid("yes") == Decimal("0.2000")
    assert book_age_s(a, NOW) == 10

    b = load_book(db_session, "B", NOW)
    assert b.source == "ws"
    assert (b.sid, b.seq) == (3, 1)
    assert b.best_bid("yes") == Decimal("0.3500")
    assert b.dirty is False


def test_load_book_applies_deltas_after_each_anchor(db_session):
    vm = _market(db_session, "A")
    _rest_snapshot(db_session, vm, NOW - timedelta(seconds=30), raw_id=1)
    # Before the REST fetch: must not be applied.
    _delta(db_session, "A", NOW - timedelta(seconds=40), "yes", "0.2000", "-100.00", sid=0, seq=1)
    d2 = _delta(db_session, "A", NOW - timedelta(seconds=20), "yes", "0.2000", "-1.98", sid=0, seq=7)
    book = load_book(db_session, "A", NOW)
    assert book.source == "rest"
    assert book.yes_bids[Decimal("0.2000")] == Decimal("500.00")
    assert book.last_event_id == d2.id
    # A REST anchor has no seq to continue, so an out-of-order seq must not dirty it.
    assert book.dirty is False

    # The same book advances on the REST branch: no seq to continue, cursor by id.
    d3 = _delta(db_session, "A", NOW - timedelta(seconds=15), "yes", "0.2000", "-500.00", sid=0, seq=99)
    out = advance_book(db_session, book, NOW)
    assert out.source == "rest"
    assert out.yes_bids == {}
    assert out.last_event_id == d3.id
    assert out.dirty is False
    assert book.last_event_id == d2.id  # the caller's book is untouched


def test_load_book_none_without_sources(db_session):
    _market(db_session, "A")
    assert load_book(db_session, "A", NOW) is None
    _ws_snapshot(db_session, "B", NOW)
    assert load_book(db_session, "A", NOW) is None


def test_dirty_by_sid_across_tickers(db_session):
    _ws_snapshot(db_session, "A", NOW - timedelta(seconds=5), sid=2, seq=1)
    _ws_snapshot(db_session, "B", NOW - timedelta(seconds=5), sid=2, seq=1)
    _ws_snapshot(db_session, "C", NOW - timedelta(seconds=5), sid=4, seq=1)
    _gap(db_session, sid=2, exposed_by="A")

    assert load_book(db_session, "B", NOW).dirty is True
    assert load_book(db_session, "A", NOW).dirty is True
    assert load_book(db_session, "C", NOW).dirty is False


def test_advance_book_applies_new_rows_in_id_order_without_upper_ts_bound(db_session):
    snap = _ws_snapshot(db_session, "A", NOW - timedelta(seconds=10), sid=2, seq=1)
    book = load_book(db_session, "A", NOW)
    assert book.last_event_id == snap.id
    # Two rows in id order; the second carries a venue timestamp ahead of `now`, which the
    # live path must still apply (no upper `ts` bound).
    _delta(db_session, "A", NOW - timedelta(seconds=1), "yes", "0.3500", "-4.00", sid=2, seq=2)
    d2 = _delta(db_session, "A", NOW + timedelta(seconds=2), "yes", "0.3400", "7.00", sid=2, seq=3)

    out = advance_book(db_session, book, NOW)
    assert out.yes_bids == {Decimal("0.3500"): Decimal("6.00"), Decimal("0.3400"): Decimal("7.00")}
    assert out.last_event_id == d2.id
    assert (out.seq, out.dirty) == (3, False)
    assert book.last_event_id == snap.id  # the caller's book is untouched

    # A gap on the anchor's sid dirties the book; a newer clean snapshot re-anchors it.
    _gap(db_session, sid=2, exposed_by="A")
    dirty = advance_book(db_session, out, NOW)
    assert dirty.dirty is True
    _ws_snapshot(db_session, "A", NOW + timedelta(seconds=3), sid=5, seq=1, yes=(("0.31", "2.00"),))
    fresh = advance_book(db_session, dirty, NOW)
    assert fresh.dirty is False
    assert fresh.yes_bids == {Decimal("0.3100"): Decimal("2.00")}
    assert fresh.sid == 5


def test_book_at_excludes_later_rows_and_returns_none_when_stale(db_session):
    t0 = NOW
    _ws_snapshot(db_session, "A", t0, sid=2, seq=1)
    _delta(db_session, "A", t0 + timedelta(seconds=1), "yes", "0.3500", "-4.00", sid=2, seq=2)
    _delta(db_session, "A", t0 + timedelta(seconds=30), "yes", "0.3500", "-6.00", sid=2, seq=3)

    at10 = book_at(db_session, "A", t0 + timedelta(seconds=10))
    assert at10.yes_bids == {Decimal("0.3500"): Decimal("6.00")}
    assert at10.as_of == t0 + timedelta(seconds=1)

    at60 = book_at(db_session, "A", t0 + timedelta(seconds=60))
    assert at60.yes_bids == {}
    assert at60.best_bid("yes") is None

    # A gap recorded after the instant says nothing about the book at the instant; only a
    # seq break inside the replayed range does.
    _gap(db_session, sid=2, exposed_by="A")
    assert book_at(db_session, "A", t0 + timedelta(seconds=10)).dirty is False

    # F36: the newest event at or before the instant is more than 120 s old.
    assert book_at(db_session, "A", t0 + timedelta(seconds=200)) is None
    # No snapshot at or before the instant.
    assert book_at(db_session, "A", t0 - timedelta(seconds=1)) is None
    assert book_at(db_session, "ZZZ", t0) is None


def test_a_malformed_delta_row_dirties_instead_of_raising(db_session):
    _ws_snapshot(db_session, "A", NOW - timedelta(seconds=5), sid=2, seq=1)
    _delta(db_session, "A", NOW, "yes", "0.3500", "-4.00", sid=2, seq=2)
    # The venue frame carried no side, so the recorder taped NULLs. The book cannot be
    # advanced past it, but one bad row must not take the executor loop down. It comes last
    # here, so the sequence it carried is the only thing that can advance `seq`.
    db_session.add(OrderbookEvent(ticker="A", ts=NOW, sid=2, seq=3, kind="delta",
                                  side=None, price=None, delta=None, raw={"market_ticker": "A"}))
    db_session.flush()

    book = load_book(db_session, "A", NOW)
    assert book.dirty is True
    assert book.yes_bids == {Decimal("0.3500"): Decimal("6.00")}  # the sound row still applied
    assert book.seq == 3  # the unusable row still advanced the sequence it carried


def test_rest_anchor_ignores_sid_zero_gaps_older_than_its_tape_position(db_session):
    vm = _market(db_session, "A")
    # An unattributable sink exception mark parked on sid 0 before the REST fetch. It says
    # nothing about a ladder fetched afterwards, so the book must load clean -- otherwise one
    # such row would dirty every REST-anchored book for the rest of the tape.
    _gap(db_session, sid=0, exposed_by="sink_exception", ts=NOW - timedelta(seconds=60))
    _rest_snapshot(db_session, vm, NOW - timedelta(seconds=10), raw_id=1)

    book = load_book(db_session, "A", NOW)
    assert book.source == "rest"
    assert book.dirty is False
    assert book.anchor_id == 0  # the delta cursor still starts from the beginning


def test_rest_anchor_dirties_on_a_sid_zero_gap_after_its_tape_position(db_session):
    vm = _market(db_session, "A")
    _rest_snapshot(db_session, vm, NOW - timedelta(seconds=60), raw_id=1)
    _gap(db_session, sid=0, exposed_by="sink_exception", ts=NOW - timedelta(seconds=10))

    book = load_book(db_session, "A", NOW)
    assert book.source == "rest"
    assert book.dirty is True


def test_rest_anchor_dirties_on_a_backwards_delta_ts(db_session):
    vm = _market(db_session, "A")
    _rest_snapshot(db_session, vm, NOW - timedelta(seconds=30), raw_id=1)
    _delta(db_session, "A", NOW - timedelta(seconds=10), "yes", "0.2000", "-1.00", sid=3, seq=5)
    # Taped after, but stamped with an earlier venue clock. The scan's `ts` floor moves
    # forward with `as_of`, so the next scan would miss anything this far back: a REST anchor
    # has no seq to catch it, so the book says so instead of reading clean.
    _delta(db_session, "A", NOW - timedelta(seconds=25), "yes", "0.2000", "-1.00", sid=3, seq=6)

    book = load_book(db_session, "A", NOW)
    assert book.dirty is True
    assert book.yes_bids[Decimal("0.2000")] == Decimal("499.98")  # both rows still applied


def test_ws_anchor_backwards_delta_ts_is_governed_by_seq(db_session):
    _ws_snapshot(db_session, "A", NOW - timedelta(seconds=30), sid=2, seq=1)
    _delta(db_session, "A", NOW - timedelta(seconds=10), "yes", "0.3500", "-1.00", sid=2, seq=2)
    _delta(db_session, "A", NOW - timedelta(seconds=25), "yes", "0.3500", "-1.00", sid=2, seq=3)

    book = load_book(db_session, "A", NOW)
    assert book.source == "ws"
    assert book.dirty is False  # contiguous seq: the venue clock going backwards is not a gap
    assert book.yes_bids[Decimal("0.3500")] == Decimal("8.00")


def test_advance_book_does_not_reload_onto_a_gapped_snapshot(db_session):
    snap = _ws_snapshot(db_session, "A", NOW - timedelta(seconds=10), sid=2, seq=1)
    book = load_book(db_session, "A", NOW)
    _gap(db_session, sid=2, exposed_by="A")
    dirty = advance_book(db_session, book, NOW)
    assert dirty.dirty is True

    # The only newer snapshot is itself gapped, so it is no place to re-anchor.
    _ws_snapshot(db_session, "A", NOW + timedelta(seconds=1), sid=5, seq=1, yes=(("0.31", "2.00"),))
    _gap(db_session, sid=5, exposed_by="A")
    still = advance_book(db_session, dirty, NOW)
    assert still.dirty is True
    assert still.anchor_id == snap.id

    clean = _ws_snapshot(db_session, "A", NOW + timedelta(seconds=2), sid=6, seq=1, yes=(("0.29", "3.00"),))
    out = advance_book(db_session, still, NOW)
    assert out.dirty is False
    assert out.anchor_id == clean.id
    assert out.yes_bids == {Decimal("0.2900"): Decimal("3.00")}


def test_book_age_s_never_negative():
    b = BookState.from_levels("A", [], [], sid=2, seq=1, as_of=NOW + timedelta(seconds=3),
                              source="ws", anchor_id=1)
    assert book_age_s(b, NOW) == 0
    assert book_age_s(b, NOW + timedelta(seconds=9)) == 6


# --- fix round 1: the past-instant book is the live book, bounded ----------------------


def test_book_at_applies_the_live_delta_set(db_session):
    """A delta stamped just before its snapshot is applied at a past instant, exactly as live.

    Snapshots carry the recorder's clock and deltas the venue's, so a delta that genuinely
    follows a snapshot can be stamped a little before it -- which is what `DELTA_LOOKBACK` is
    for. Scanning by `ts >` alone would drop it in replay and leave the level wrong until the
    next snapshot, so `book_at` takes the live delta set (`id > anchor and ts >= anchor ts -
    DELTA_LOOKBACK`) with the instant as its upper bound.
    """
    snap = _ws_snapshot(db_session, "A", NOW, sid=2, seq=1)
    early = _delta(db_session, "A", NOW - timedelta(seconds=2), "yes", "0.3500", "6.00",
                   sid=2, seq=2)
    assert early.id > snap.id
    _delta(db_session, "A", NOW + timedelta(seconds=1), "yes", "0.3500", "-4.00", sid=2, seq=3)

    live = load_book(db_session, "A", NOW + timedelta(seconds=10))
    at = book_at(db_session, "A", NOW + timedelta(seconds=10))

    assert live.yes_bids == {Decimal("0.3500"): Decimal("12.00")}
    assert at.yes_bids == live.yes_bids
    assert (at.last_event_id, at.as_of, at.dirty) == (live.last_event_id, live.as_of, live.dirty)


def test_book_at_anchors_on_a_rest_ladder_when_it_is_the_fresher_source(db_session):
    """A ticker the WebSocket never snapshotted still has a book at a past instant.

    `load_book` picks the fresher of the newest WS snapshot and the newest REST ladder, so a
    replay that only looked at WS snapshots would hand the executor no book at all for a
    REST-anchored ticker -- every order on it placed under `no_book` and never filled.
    """
    vm = _market(db_session, "R")
    _rest_snapshot(db_session, vm, NOW - timedelta(seconds=10), raw_id=41)

    at = book_at(db_session, "R", NOW)
    live = load_book(db_session, "R", NOW)

    assert at is not None and at.source == "rest"
    assert (at.sid, at.seq) == (0, 0)
    assert at.yes_bids == live.yes_bids and at.no_bids == live.no_bids
    assert at.gap_check_id == live.gap_check_id
    # And the fresher of the two sources still wins, at the instant.
    _ws_snapshot(db_session, "R", NOW - timedelta(seconds=2), sid=7)
    assert book_at(db_session, "R", NOW).source == "ws"
    assert book_at(db_session, "R", NOW - timedelta(seconds=5)).source == "rest"


def test_advance_book_at_matches_advance_book_bounded_at_the_instant(db_session):
    """`advance_book_at` is `advance_book` with an upper `ts` bound: same book, incrementally.

    A replay steps a whole day 15 s at a time, so rebuilding each book from its anchor at every
    step is quadratic in the day's tape. Advancing the cached book has to land on exactly the
    book a rebuild would have produced.
    """
    _ws_snapshot(db_session, "A", NOW, sid=2, seq=1)
    _delta(db_session, "A", NOW + timedelta(seconds=1), "yes", "0.3500", "-4.00", sid=2, seq=2)
    _delta(db_session, "A", NOW + timedelta(seconds=20), "yes", "0.3500", "-3.00", sid=2, seq=3)
    _delta(db_session, "A", NOW + timedelta(seconds=40), "yes", "0.3400", "9.00", sid=2, seq=4)

    book = book_at(db_session, "A", NOW + timedelta(seconds=10))
    for step in (25, 45, 60):
        book = advance_book_at(db_session, book, NOW + timedelta(seconds=step))
        rebuilt = book_at(db_session, "A", NOW + timedelta(seconds=step))
        assert book.yes_bids == rebuilt.yes_bids, step
        assert (book.as_of, book.last_event_id, book.seq) == (
            rebuilt.as_of, rebuilt.last_event_id, rebuilt.seq), step


def test_load_book_at_dirties_on_a_gap_inside_the_range_only(db_session):
    """`load_book_at` is `load_book`'s gap check bounded at the instant.

    A gap taped after the instant says nothing about the book at the instant -- reusing the
    live `id > anchor` test unbounded would dirty every historical book on that subscription
    forever. A gap recorded before it is a lost frame the live loop would have seen.
    """
    _ws_snapshot(db_session, "A", NOW, sid=2, seq=1)
    _delta(db_session, "A", NOW + timedelta(seconds=1), "yes", "0.3500", "-4.00", sid=2, seq=2)
    assert load_book_at(db_session, "A", NOW + timedelta(seconds=10)).dirty is False

    _gap(db_session, sid=2, exposed_by="A", ts=NOW + timedelta(seconds=30))
    assert load_book_at(db_session, "A", NOW + timedelta(seconds=10)).dirty is False
    assert load_book_at(db_session, "A", NOW + timedelta(seconds=40)).dirty is True


# --- Final fix wave, I3: the REST anchor's gap-check id is bounded below --------------------

def test_rest_anchor_gap_check_id_reads_the_window_before_the_fetch(db_session):
    """Final fix wave, I3. `max(id) where ts <= :fetched_at` had no lower bound, so inside the
    partition holding the fetch the plan filtered every row taped after it -- the same
    partition-scan shape as I2, on the branch `book_at` takes for every markout horizon with
    no nearby quote. Bounded to the window before the fetch it still answers the same id, and
    the twenty rows taped after the fetch are pruned rather than filtered.
    """
    vm = _market(db_session, "R")
    fetched_at = NOW - timedelta(seconds=10)
    _delta(db_session, "OTHER", fetched_at - timedelta(minutes=5), "yes", "0.35", "1.00")
    inside = _delta(db_session, "OTHER", fetched_at - timedelta(minutes=1), "yes", "0.35", "1.00")
    for i in range(20):
        _delta(db_session, "OTHER", fetched_at + timedelta(seconds=i + 1), "yes", "0.35", "1.00")
    _rest_snapshot(db_session, vm, fetched_at, raw_id=91)

    book = load_book(db_session, "R", NOW)
    assert book.source == "rest"
    assert book.gap_check_id == inside.id


def test_rest_anchor_gap_check_id_widens_once_then_stops(db_session):
    """The window widens once to 24 h and stops there: a fetch whose newest preceding tape row
    is older than a day takes gap-check id 0, which reads every gap on sid 0 as after the
    ladder -- the conservative direction (a dirty book blocks decisions; a clean one would let
    a ladder that may have missed frames look tradeable, F36)."""
    vm = _market(db_session, "R")
    fetched_at = NOW - timedelta(seconds=10)
    ancient = _delta(db_session, "OTHER", fetched_at - timedelta(hours=30), "yes", "0.35", "1.00")
    _rest_snapshot(db_session, vm, fetched_at, raw_id=92)

    assert load_book(db_session, "R", NOW).gap_check_id == 0

    # Inside the widened 24 h window, it is found again.
    within = _delta(db_session, "OTHER", fetched_at - timedelta(hours=6), "yes", "0.35", "1.00")
    assert within.id > ancient.id
    assert load_book(db_session, "R", NOW).gap_check_id == within.id


# --- Final fix wave, I7: BookWalker ---------------------------------------------------------

def test_book_walker_matches_a_per_instant_rebuild_with_deltas_between_the_instants(db_session):
    """The whole point of the walker: the same book `book_at` builds, without re-anchoring and
    replaying from the snapshot at every instant. The fixture puts deltas *between* the
    instants, which is the case a cached book would get wrong if it did not advance.
    """
    from harness.execution.book import BookWalker

    _ws_snapshot(db_session, "W", NOW - timedelta(minutes=10), sid=2, seq=1,
                 yes=(("0.40", "10.00"),), no=(("0.55", "10.00"),))
    instants = [NOW - timedelta(minutes=m) for m in (9, 7, 5, 3, 1)]
    seq = 2
    for i, instant in enumerate(instants):
        # Two deltas before each instant, so every step has movement to fold in.
        _delta(db_session, "W", instant - timedelta(seconds=30), "yes", "0.40", "1.00", seq=seq)
        _delta(db_session, "W", instant - timedelta(seconds=5), "no", "0.55", f"{i + 1}.00",
               seq=seq + 1)
        seq += 2
    db_session.flush()

    walker = BookWalker(db_session, "W")
    for instant in instants:
        walked = walker.at(instant)
        rebuilt = book_at(db_session, "W", instant)
        assert walked is not None and rebuilt is not None
        assert walked.yes_bids == rebuilt.yes_bids
        assert walked.no_bids == rebuilt.no_bids
        assert walked.mid() == rebuilt.mid()
        assert walked.as_of == rebuilt.as_of
        assert book_age_s(walked, instant) == book_age_s(rebuilt, instant)


def test_book_walker_re_anchors_when_a_new_snapshot_lands_between_instants(db_session):
    """A snapshot between two instants is the one thing that changes which anchor `book_at`
    would pick, so the walker rebuilds rather than folding deltas onto the stale ladder."""
    from harness.execution.book import BookWalker

    first, second = NOW - timedelta(minutes=5), NOW
    _ws_snapshot(db_session, "W", first - timedelta(seconds=10), sid=2, seq=1,
                 yes=(("0.40", "10.00"),), no=(("0.55", "10.00"),))
    _delta(db_session, "W", first - timedelta(seconds=5), "yes", "0.40", "5.00", sid=2, seq=2)
    # The resubscribe: a fresh snapshot on a new sid with a different ladder entirely.
    _ws_snapshot(db_session, "W", second - timedelta(seconds=10), sid=3, seq=1,
                 yes=(("0.30", "7.00"),), no=(("0.60", "8.00"),))
    db_session.flush()

    walker = BookWalker(db_session, "W")
    assert walker.at(first).yes_bids == book_at(db_session, "W", first).yes_bids
    walked, rebuilt = walker.at(second), book_at(db_session, "W", second)
    assert walked.yes_bids == rebuilt.yes_bids == {Decimal("0.3000"): Decimal("7.00")}
    assert (walked.sid, walked.anchor_id) == (rebuilt.sid, rebuilt.anchor_id)


def test_book_walker_reports_a_stale_instant_and_keeps_walking_afterwards(db_session):
    """A quiet stretch answers None (F36) exactly as `book_at` does, and the cached book
    survives it: the instant after fresh deltas arrive advances rather than rebuilding."""
    from harness.execution.book import BookWalker

    base = NOW - timedelta(minutes=20)
    _ws_snapshot(db_session, "W", base, sid=2, seq=1, yes=(("0.40", "10.00"),),
                 no=(("0.55", "10.00"),))
    quiet = base + timedelta(minutes=10)  # nothing taped within BOOK_MAX_AGE of this
    later = base + timedelta(minutes=15)
    _delta(db_session, "W", later - timedelta(seconds=5), "yes", "0.40", "3.00", sid=2, seq=2)
    db_session.flush()

    walker = BookWalker(db_session, "W")
    assert walker.at(base + timedelta(seconds=30)) is not None
    assert walker.at(quiet) is None and book_at(db_session, "W", quiet) is None
    walked, rebuilt = walker.at(later), book_at(db_session, "W", later)
    assert walked is not None and rebuilt is not None
    assert walked.yes_bids == rebuilt.yes_bids == {Decimal("0.4000"): Decimal("13.00")}
