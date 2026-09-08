"""The paper executor's loop against a real database: intake, books, fills, actions, heartbeat.

Every test seeds one tick of the real pipeline (`tests/test_pipeline._seed` plus the `tiny`
primary and `price_and_signal`), keeps exactly one candidate signal, and then shapes the tape
under it. The loop is driven with an explicit clock, so `now` is a value in the test rather
than wall time, and one step is one commit.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from harness.config.settings import get_settings
from harness.db.models import (
    Fill,
    Intent,
    KillSwitch,
    Ledger,
    Order,
    OrderEvent,
    OrderbookEvent,
    Signal,
    VenueMarket,
    VenueTrade,
)
from harness.execution import EXECUTOR_VERSION
from harness.execution.loop import ExecStats, Executor
from harness.strategy.pipeline import price_and_signal
from harness.strategy.variants import load_variants, register_variants
from tests.test_pipeline import NOW, VARIANTS_DIR, _seed

COMPOSE = Path(__file__).parent.parent / "docker-compose.yml"

#: The two markets the tests trade. `_seed` + `price_and_signal` make both a candidate for
#: `tiny`; the price targets and sizes below are that run's own output, pinned here.
T2, VM2, TARGET2, SIZE2 = "KXNFL-P-2", 2, Decimal("0.3500"), Decimal("97.00")
T3, VM3, TARGET3, SIZE3 = "KXNFL-P-3", 3, Decimal("0.4500"), Decimal("94.00")
#: The fuzzy-matched market, whose signal is rejected on match_confidence until a test flips it.
VM_FUZZY = 10

KICKOFF = NOW + timedelta(days=2)
runner = CliRunner()


class Clock:
    """An explicit `now` plus an explicit monotonic reading, both advanced by the test."""

    def __init__(self, now: datetime, mono: float = 0.0) -> None:
        self.now, self.mono = now, mono

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds


def _ws_snapshot(session, ticker, ts, yes, no, sid=2, seq=1):
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="snapshot",
                         raw={"market_ticker": ticker,
                              "yes_dollars_fp": [[str(p), str(q)] for p, q in yes],
                              "no_dollars_fp": [[str(p), str(q)] for p, q in no]})
    session.add(row)
    session.flush()
    return row


def _delta(session, ticker, ts, side, price, delta, seq, sid=2):
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="delta", side=side,
                         price=Decimal(str(price)), delta=Decimal(str(delta)), raw={})
    session.add(row)
    session.flush()
    return row


def _gap(session, ts, sid=2):
    row = OrderbookEvent(ticker="", ts=ts, sid=sid, seq=99, kind="gap", raw={"reason": "seq"})
    session.add(row)
    session.flush()
    return row


def _print(session, ticker, ts, yes_price, count, taker_side="no", trade_id=None, source="ws"):
    row = VenueTrade(venue="kalshi", trade_id=trade_id or f"tr-{ticker}-{ts.isoformat()}",
                     ticker=ticker, ts=ts, yes_price=Decimal(str(yes_price)),
                     count=Decimal(str(count)), taker_side=taker_side,
                     taker_outcome_side=taker_side, source=source)
    session.add(row)
    session.flush()
    return row


def _book2(session, ts, yes_size="40.00"):
    """The ordinary book under T2: 40 contracts resting at our 0.35 target.

    Both books quote 0.50 bid / 0.52 ask in YES space, so their midpoint is the 0.51 the gap
    snapshot recorded. A book whose mid disagreed with the snapshot would fire the `venue_move`
    cancel on the loop after placement and no test here would ever reach its second step.
    """
    return _ws_snapshot(session, T2, ts, [("0.50", "20.00"), ("0.35", yes_size)],
                        [("0.48", "50.00")])


def _book3(session, ts):
    return _ws_snapshot(session, T3, ts, [("0.50", "20.00"), ("0.45", "30.00")],
                        [("0.48", "60.00")], seq=1)


@pytest.fixture
def world(env_settings, db_session):
    """One priced tick, with every candidate but T2's rejected."""
    game, run, markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    keep_only(db_session, {VM2})
    db_session.commit()
    return game, run, markets


def keep_only(session, venue_market_ids: set[int]) -> None:
    """Reject every candidate signal outside `venue_market_ids`, so intake is deterministic."""
    for signal in session.query(Signal).filter(Signal.decision == "candidate").all():
        if signal.venue_market_id not in venue_market_ids:
            signal.decision = "rejected"
            signal.rejection_reason = "edge"
    session.flush()


def make_executor(env_settings, db_session, clock, replay=False, variants=("tiny",)):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return Executor(env_settings, factory, clock=lambda: clock.now,
                    monotonic=lambda: clock.mono, replay=replay, variants=list(variants))


def refresh(session):
    """End the reader's transaction so the executor's committed writes are visible."""
    session.commit()
    session.expire_all()


def orders_of(session, ticker=T2):
    return session.query(Order).filter_by(ticker=ticker).order_by(Order.id).all()


def events_of(session, kind=None):
    q = session.query(OrderEvent)
    if kind is not None:
        q = q.filter_by(kind=kind)
    return q.order_by(OrderEvent.id).all()


def fills_of(session, order_id=None, method=None):
    q = session.query(Fill)
    if order_id is not None:
        q = q.filter_by(order_id=order_id)
    if method is not None:
        q = q.filter_by(fill_method=method)
    return q.order_by(Fill.id).all()


# --- version pin ---------------------------------------------------------------------


def test_executor_version_is_bumped_for_the_loop():
    assert EXECUTOR_VERSION == "3.4"


# --- intake, placement, the book ------------------------------------------------------


def test_step_creates_intent_and_order_with_positive_queue_from_ws_snapshot(env_settings, db_session, world):
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)

    stats = make_executor(env_settings, db_session, clock).step()

    assert stats.locked is True
    assert (stats.intents_new, stats.placed) == (1, 0 + 1)
    assert stats.errors == 0
    refresh(db_session)

    intent = db_session.query(Intent).one()
    assert (intent.venue_market_id, intent.side, intent.target_prob) == (VM2, "yes", TARGET2)
    assert intent.target_contracts == SIZE2
    assert intent.kickoff_utc == KICKOFF
    assert intent.fair_row_id is not None
    assert intent.signal_created_at == NOW

    order = orders_of(db_session)[0]
    assert (order.status, order.mode, order.side, order.prob) == ("open", "paper", "yes", TARGET2)
    assert order.contracts == SIZE2
    assert order.queue_ahead_at_place == Decimal("40.00")
    assert order.queue_remaining == Decimal("40.00")
    assert order.book_source == "ws"
    assert order.config_hash and len(order.config_hash) == 64
    assert order.client_order_id == f"paper-{intent.id}-0"
    assert order.expiry == KICKOFF - timedelta(minutes=10)
    assert order.tape_cursor_event_id is not None
    assert order.nw_tape_cursor_event_id == order.tape_cursor_event_id
    assert order.replay is False
    assert order.match_key == db_session.get(VenueMarket, VM2).match_key

    place = [e for e in events_of(db_session) if e.kind == "place"]
    assert len(place) == 1 and place[0].order_id == order.id


def test_no_book_order_is_placed_with_marker_and_fills_only_after_a_snapshot(env_settings, db_session, world):
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)

    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.book_source == "none"
    assert order.queue_ahead_at_place is None
    assert order.tape_cursor_event_id is None and order.nw_tape_cursor_event_id is None
    marker = [e for e in events_of(db_session, "skipped") if e.reason == "no_book"]
    assert len(marker) == 1 and marker[0].intent_id == order.intent_id
    assert marker[0].order_id is None

    # A print in the no-book window is discarded (D7): the queue was unknowable then.
    _print(db_session, T2, NOW + timedelta(seconds=5), "0.35", "500", trade_id="early")
    _book2(db_session, NOW + timedelta(seconds=10))
    db_session.commit()

    clock.advance(15)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.book_source == "ws"
    assert order.queue_ahead_at_place == Decimal("40.00")
    assert order.book_first_seen_at is not None
    assert order.tape_cursor_event_id is not None
    assert fills_of(db_session, order.id) == []
    assert order.filled_contracts == Decimal("0.00")

    # A print after the book was adopted does fill.
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.35", "60", trade_id="late")
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.filled_contracts == Decimal("20.00")


# --- fills ---------------------------------------------------------------------------


def _place_and_print(env_settings, db_session, clock, yes_price="0.35", count="50",
                     trade_id="t1", executor=None):
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = executor or make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    _print(db_session, T2, clock.now + timedelta(seconds=5), yes_price, count, trade_id=trade_id)
    db_session.commit()
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    return executor, stats


def test_print_at_price_fills_after_queue_and_writes_ledger(env_settings, db_session, world):
    clock = Clock(NOW)
    _, stats = _place_and_print(env_settings, db_session, clock)

    order = orders_of(db_session)[0]
    assert order.filled_contracts == Decimal("10.00")
    assert order.status == "partially_filled"
    assert order.queue_remaining == Decimal("0.00")

    watched = fills_of(db_session, order.id, "queue_model")
    assert len(watched) == 1
    assert watched[0].contracts == Decimal("10.00")
    assert watched[0].prob == TARGET2
    assert watched[0].through is False
    assert stats.fills == 1

    ledger = db_session.query(Ledger).filter_by(kind="fill").all()
    assert len(ledger) == 1
    assert ledger[0].fill_id == watched[0].id
    assert ledger[0].order_id == order.id
    assert ledger[0].cash_delta < 0


def test_print_through_price_fills_fully(env_settings, db_session, world):
    clock = Clock(NOW)
    _place_and_print(env_settings, db_session, clock, yes_price="0.30", count="200")

    order = orders_of(db_session)[0]
    assert order.filled_contracts == SIZE2
    assert order.status == "filled"
    watched = fills_of(db_session, order.id, "queue_model")
    assert len(watched) == 1
    assert watched[0].contracts == SIZE2
    assert watched[0].through is True


def test_has_print_reads_100_percent_on_queue_model_fills(env_settings, db_session, world):
    clock = Clock(NOW)
    _place_and_print(env_settings, db_session, clock)

    watched = fills_of(db_session, method="queue_model")
    assert watched
    assert all(f.has_print for f in watched)


def test_fill_row_carries_has_print_fee_fields_through_and_tape_source(env_settings, db_session, world):
    clock = Clock(NOW)
    _place_and_print(env_settings, db_session, clock)

    fill = fills_of(db_session, method="queue_model")[0]
    assert fill.fee_type == "quadratic_with_maker_fees"
    assert fill.fee_multiplier == Decimal("1.0000")
    assert fill.maker_rate == Decimal("0.0175")
    assert fill.fee > 0
    assert fill.tape_source == "ws"
    assert fill.taker_side == "no"
    assert fill.source_trade_id == "t1"
    assert fill.has_print is True
    assert fill.simulated is True
    assert fill.through is False


def test_no_watcher_track_keeps_filling_after_a_cancel(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    db_session.add(KillSwitch(id=1, active=True, reason="test", set_at=NOW))
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.status == "cancelled"
    assert order.cancel_reason == "kill_switch"

    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.35", "60", trade_id="after")
    db_session.commit()
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert fills_of(db_session, order.id, "queue_model") == []
    assert order.filled_contracts == Decimal("0.00")
    nw = fills_of(db_session, order.id, "no_watcher")
    assert len(nw) == 1 and nw[0].contracts == Decimal("20.00")
    assert order.nw_filled_contracts == Decimal("20.00")
    assert stats.nw_fills == 1


def test_nw_fill_writes_no_ledger_or_position_row(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    db_session.add(KillSwitch(id=1, active=True, reason="test", set_at=NOW))
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.35", "60", trade_id="after")
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)

    assert fills_of(db_session, method="no_watcher")
    assert db_session.query(Ledger).count() == 0
    rows = db_session.execute(text("select * from positions")).all()
    assert rows == []


def test_crossed_order_gets_exactly_one_snapshot_cross_fill_with_no_position_or_ledger(
        env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    # The NO bid climbs to 0.70, so the YES ask is 0.30 and the book has crossed our 0.35.
    _delta(db_session, T2, clock.now + timedelta(seconds=5), "no", "0.70", "25.00", seq=2)
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    cross = fills_of(db_session, order.id, "snapshot_cross")
    assert len(cross) == 1
    assert cross[0].contracts == SIZE2
    assert cross[0].source_event_id is not None
    assert order.worst_case_fill is True
    assert order.filled_contracts == Decimal("0.00")
    assert db_session.query(Ledger).count() == 0
    assert db_session.execute(text("select * from positions")).all() == []

    # A later step sees the same crossed book and must not write a second cross.
    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert len(fills_of(db_session, order.id, "snapshot_cross")) == 1


def test_simulation_book_copy_leaves_the_cache_unchanged(env_settings, db_session, world, monkeypatch):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    from harness.execution import loop as loop_module

    real = loop_module.simulate_fills
    poison = Decimal("0.9900")

    def vandal(order, state, book, prints, deltas, deadline, fill_method, **kw):
        if book is not None:
            book.yes_bids[poison] = Decimal("1.00")
            book.no_bids.clear()
        return real(order, state, book, prints, deltas, deadline, fill_method, **kw)

    monkeypatch.setattr(loop_module, "simulate_fills", vandal)
    _delta(db_session, T2, clock.now + timedelta(seconds=5), "yes", "0.34", "3.00", seq=2)
    db_session.commit()
    clock.advance(15)
    executor.step()

    cached = executor.books[T2]
    assert poison not in cached.yes_bids
    assert cached.yes_bids[Decimal("0.3500")] == Decimal("40.00")
    assert cached.no_bids == {Decimal("0.4800"): Decimal("50.00")}


# --- the decision chain, applied ------------------------------------------------------


def test_kill_switch_cancels_all(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    db_session.add(KillSwitch(id=1, active=True, reason="drawdown", set_at=NOW))
    db_session.commit()
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.status == "cancelled"
    assert order.cancel_reason == "kill_switch"
    assert order.cancelled_at == clock.now
    assert stats.cancelled == 1
    cancels = [e for e in events_of(db_session, "cancel") if e.order_id == order.id]
    assert len(cancels) == 1 and cancels[0].reason == "kill_switch"


def test_expiry_expires(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]

    clock.now = order.expiry + timedelta(seconds=1)
    clock.mono += 15
    stats = executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.status == "expired"
    assert stats.expired == 1
    assert [e.kind for e in events_of(db_session) if e.order_id == order.id] == ["place", "expire"]


def test_reprice_cancels_then_places(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    first = orders_of(db_session)[0]

    old = db_session.query(Signal).filter_by(venue_market_id=VM2, decision="candidate").one()
    db_session.add(Signal(run_id=old.run_id + 1000, variant_id=old.variant_id,
                          gap_snapshot_id=old.gap_snapshot_id, venue_market_id=VM2, side="yes",
                          fair_p=old.fair_p, fair_source=old.fair_source,
                          venue_best_bid=old.venue_best_bid, venue_best_ask=old.venue_best_ask,
                          price_target=Decimal("0.3800"), fee_at_target=old.fee_at_target,
                          as_estimate=old.as_estimate, edge=old.edge, edge_min=old.edge_min,
                          stake=old.stake, contracts=90, decision="candidate", labels={},
                          created_at=NOW + timedelta(seconds=10)))
    db_session.commit()

    clock.advance(15)
    stats = executor.step()
    refresh(db_session)

    rows = orders_of(db_session)
    assert len(rows) == 2
    assert rows[0].id == first.id
    assert (rows[0].status, rows[0].cancel_reason) == ("cancelled", "reprice")
    assert rows[1].status == "open"
    assert rows[1].prob == Decimal("0.3800")
    assert rows[1].intent_id != rows[0].intent_id
    assert (stats.cancelled, stats.placed) == (1, 1)


def test_skip_recorded_once_across_two_steps(env_settings, db_session, world):
    # The fuzzy-matched market's signal becomes a candidate: it makes an intent, and every
    # loop the intent chain reports `unmatched` -- which must be written exactly once.
    fuzzy = db_session.query(Signal).filter_by(venue_market_id=VM_FUZZY).one()
    fuzzy.decision, fuzzy.rejection_reason = "candidate", None
    keep_only(db_session, {VM_FUZZY})
    db_session.commit()

    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    first = executor.step()
    refresh(db_session)
    intent = db_session.query(Intent).one()
    assert intent.venue_market_id == VM_FUZZY
    assert orders_of(db_session, "KXNFL-P-10") == []
    skips = [e for e in events_of(db_session, "skipped") if e.reason == "unmatched"]
    assert len(skips) == 1 and skips[0].intent_id == intent.id
    assert first.skipped == 1

    clock.advance(15)
    second = executor.step()
    refresh(db_session)
    assert len([e for e in events_of(db_session, "skipped") if e.reason == "unmatched"]) == 1
    assert second.skipped == 0


def test_cap_gate_event_written_for_a_non_apply_caps_variant(env_settings, db_session, world):
    # `tiny` has apply_caps: false. A stake past per_bet_cap (0.03 * 3000 = 90) must record the
    # cap it would have failed and still place the order.
    signal = db_session.query(Signal).filter_by(venue_market_id=VM2, decision="candidate").one()
    signal.stake = Decimal("500.00")
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()

    clock = Clock(NOW)
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)

    gates = events_of(db_session, "cap_gate")
    assert len(gates) == 1
    assert gates[0].reason == "cap_per_bet"
    assert orders_of(db_session)[0].status == "open"


# --- the book's own failure modes -----------------------------------------------------


def test_dirty_book_holds_and_accumulates_dirty_minutes(env_settings, db_session, world):
    clock = Clock(NOW)
    snapshot = _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    _gap(db_session, NOW + timedelta(seconds=1), sid=snapshot.sid)
    _print(db_session, T2, NOW + timedelta(seconds=5), "0.30", "500", trade_id="dirty")
    db_session.commit()

    for _ in range(4):
        clock.advance(15)
        executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.status == "open"
    assert fills_of(db_session, order.id) == []
    assert order.dirty_seconds == 60
    assert order.dirty_minutes == 1


def test_dead_recorder_marks_every_book_dirty(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    _print(db_session, T2, NOW + timedelta(seconds=5), "0.30", "500", trade_id="stale")
    db_session.commit()
    # The newest tape row is now 200 s old: the recorder is dead, so every book is dirty.
    clock.advance(200)
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.status == "open"
    assert fills_of(db_session, order.id) == []
    heartbeat = db_session.execute(text("select book_dirty_markets from exec_heartbeat")).scalar()
    assert heartbeat >= 1


# --- the loop's own guarantees --------------------------------------------------------


def test_advisory_lock_held_elsewhere_returns_locked_stats(env_settings, db_session, world):
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    engine = db_session.get_bind()
    with engine.connect() as holder:
        got = holder.execute(text("select pg_try_advisory_lock(hashtext('harness.exec'))")).scalar()
        assert got is True
        stats = make_executor(env_settings, db_session, Clock(NOW)).step()
        holder.execute(text("select pg_advisory_unlock(hashtext('harness.exec'))"))
        holder.commit()

    assert stats == ExecStats(locked=False)
    refresh(db_session)
    assert db_session.query(Intent).count() == 0
    assert db_session.query(Order).count() == 0
    assert db_session.execute(text("select count(*) from exec_heartbeat")).scalar() == 0


def test_exception_in_one_order_does_not_abort_the_step(env_settings, db_session, world, monkeypatch):
    keep_only(db_session, {VM2, VM3})
    signal3 = db_session.query(Signal).filter_by(venue_market_id=VM3).one()
    signal3.decision, signal3.rejection_reason = "candidate", None
    _book2(db_session, NOW - timedelta(seconds=5))
    _book3(db_session, NOW - timedelta(seconds=5))
    db_session.commit()

    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    assert len(db_session.query(Order).all()) == 2
    doomed = orders_of(db_session, T2)[0]

    from harness.execution import loop as loop_module

    real = loop_module.simulate_fills

    def explode(order, *args, **kwargs):
        if order.order_id == doomed.id:
            raise RuntimeError("tape blew up")
        return real(order, *args, **kwargs)

    monkeypatch.setattr(loop_module, "simulate_fills", explode)
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.30", "500", trade_id="p2")
    _print(db_session, T3, clock.now + timedelta(seconds=5), "0.40", "500", trade_id="p3")
    db_session.commit()
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)

    assert stats.errors >= 1
    assert fills_of(db_session, doomed.id) == []
    survivor = orders_of(db_session, T3)[0]
    assert survivor.filled_contracts == SIZE3
    # The step committed, but a green heartbeat over a swallowed failure would be a lie.
    heartbeat = db_session.execute(text("select loops, last_error from exec_heartbeat")).one()
    assert heartbeat.loops == 2
    assert "tape blew up" in heartbeat.last_error


def test_heartbeat_fields(env_settings, db_session, world):
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    # 100 s between the two steps at a 15 s period: five loops were missed.
    clock.advance(100)
    stats = executor.step()
    refresh(db_session)

    row = db_session.execute(text("select * from exec_heartbeat")).one()
    assert row.id == 1
    assert row.loops == 2
    assert row.open_orders == 1
    assert row.last_loop_at == clock.now
    assert row.last_error is None
    assert row.last_loop_ms is not None and row.last_loop_ms >= 0
    assert row.p95_loop_ms is not None
    assert row.loops_skipped == 5
    assert row.executor_version == EXECUTOR_VERSION
    assert row.ws_last_event_at is not None
    assert stats.loop_ms >= 0


def test_replay_flag_isolates_intake(env_settings, db_session, world):
    live = db_session.query(Signal).filter_by(venue_market_id=VM2, decision="candidate").one()
    db_session.add(Signal(run_id=live.run_id, variant_id=live.variant_id,
                          gap_snapshot_id=live.gap_snapshot_id, venue_market_id=VM3, side="yes",
                          fair_p=live.fair_p, fair_source=live.fair_source,
                          price_target=TARGET3, as_estimate=live.as_estimate, edge=live.edge,
                          edge_min=live.edge_min, stake=live.stake, contracts=int(SIZE3),
                          decision="candidate", labels={}, replay=True, created_at=NOW))
    _book2(db_session, NOW - timedelta(seconds=5))
    _book3(db_session, NOW - timedelta(seconds=5))
    db_session.commit()

    clock = Clock(NOW)
    stats = make_executor(env_settings, db_session, clock, replay=True).step()
    refresh(db_session)

    assert stats.intents_new == 1
    intent = db_session.query(Intent).one()
    assert (intent.venue_market_id, intent.replay) == (VM3, True)
    order = orders_of(db_session, T3)[0]
    assert order.replay is True
    assert order.client_order_id == f"replay-{intent.id}-0"
    assert orders_of(db_session, T2) == []


# --- CLI and Compose ------------------------------------------------------------------


def test_exec_health_exit_codes(monkeypatch, db_session):
    from harness.cli import app

    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        assert runner.invoke(app, ["exec-health"]).exit_code == 1

        db_session.execute(text(
            "insert into exec_heartbeat (id, last_loop_at, loops, open_orders, loops_skipped, "
            "book_dirty_markets) values (1, now() - interval '5 minutes', 1, 0, 0, 0)"))
        db_session.commit()
        assert runner.invoke(app, ["exec-health"]).exit_code == 1

        db_session.execute(text("update exec_heartbeat set last_loop_at = now()"))
        db_session.commit()
        assert runner.invoke(app, ["exec-health"]).exit_code == 0
    finally:
        get_settings.cache_clear()


def test_compose_app_exec_block_has_no_volumes_or_kalshi_env():
    doc = yaml.safe_load(COMPOSE.read_text())
    service = doc["services"]["app-exec"]

    assert service["command"] == ["exec"]
    assert service["build"] == "."
    assert service["env_file"] == ".env"
    assert service["restart"] == "unless-stopped"
    assert service["stop_grace_period"] == "60s"
    assert service["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert service["user"] == doc["services"]["app-run"]["user"]
    assert service["healthcheck"] == {"test": ["CMD-SHELL", "harness exec-health"],
                                      "interval": "60s", "timeout": "10s", "retries": 3}
    # The executor never loads a credential and never mounts one: no order can leave this box.
    assert "volumes" not in service
    assert not [k for k in (service.get("environment") or {}) if k.startswith("KALSHI_")]
    assert "kalshi" not in yaml.safe_dump(service).lower()


def test_uuid_client_order_id_fits_the_column():
    """`client_order_id` is String(64); the paper/replay prefix plus a uuid plus the counter
    has to fit, or a placement would fail at insert time rather than in review."""
    assert len(f"replay-{uuid.uuid4()}-99") <= 64
