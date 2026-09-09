"""The paper executor's loop against a real database: intake, books, fills, actions, heartbeat.

Every test seeds one tick of the real pipeline (`tests/test_pipeline._seed` plus the `tiny`
primary and `price_and_signal`), keeps exactly one candidate signal, and then shapes the tape
under it. The loop is driven with an explicit clock, so `now` is a value in the test rather
than wall time, and one step is one commit.
"""

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import event, func, text
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from harness.config.settings import get_settings
from harness.db.models import (
    EquitySnapshot,
    Fill,
    Game,
    Intent,
    KillSwitch,
    Ledger,
    MetricSample,
    Order,
    OrderEvent,
    OrderbookEvent,
    OrderWatchSample,
    OperatorEvent,
    Signal,
    VenueMarket,
    VenueTrade,
)
from harness.execution import EXECUTOR_VERSION, store
from harness.execution.gateway import PaperGateway
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
    assert EXECUTOR_VERSION == "4.3"


# --- the gateway seam -----------------------------------------------------------------


def test_the_loop_places_through_the_gateway(env_settings, db_session, world):
    """Task 9: `_place` no longer calls `store.insert_order` itself. The spy is a `PaperGateway`
    subclass, so the order it writes and everything downstream of it are unchanged."""
    calls = []

    class _Spy(PaperGateway):
        def place(self, *a, **kw):
            calls.append("place")
            return super().place(*a, **kw)

    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    executor = Executor(env_settings, factory, clock=lambda: clock.now,
                        monotonic=lambda: clock.mono, variants=["tiny"], gateway=_Spy())

    stats = executor.step()

    assert calls == ["place"]
    assert stats.placed == 1
    refresh(db_session)
    assert orders_of(db_session)[0].mode == "paper"


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
    assert db_session.execute(text("select * from positions")).all() == []
    # `rebuild_state` reads `store.load_positions`, not the shipped view, so that is what has to
    # stay empty for the caps to be right.
    assert store.load_positions(db_session, False) == []


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


# --- fix round 1 ----------------------------------------------------------------------


def test_intent_inside_the_kickoff_cutoff_is_skipped_and_recorded(env_settings, db_session, world):
    """R8/F34: an intent too close to kickoff is not placed, and the record says so.

    The intent still reaches `plan_actions`; the kickoff rule is what declines it, so the loop
    writes the `kickoff` skip instead of silently dropping the intent before the chain.
    """
    game = db_session.query(Game).one()
    game.kickoff_utc = NOW + timedelta(minutes=5)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()

    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    stats = executor.step()
    refresh(db_session)

    assert stats.intents_new == 1
    assert orders_of(db_session) == []
    skips = [e for e in events_of(db_session, "skipped") if e.reason == "kickoff"]
    assert len(skips) == 1
    assert skips[0].intent_id == db_session.query(Intent).one().id

    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert len([e for e in events_of(db_session, "skipped") if e.reason == "kickoff"]) == 1


def test_cursor_stops_at_the_last_event_the_simulation_consumed(env_settings, db_session, world):
    """A delta stamped ahead of the loop clock is not consumed, so the cursor must not pass it.

    `advance_book` folds a delta in on `id` with no upper `ts` bound, while the simulation stops
    at its deadline. Writing the cache's head back as the cursor would jump the order over a
    delta it never applied. The next step reaches it through `book_at(ts_of(cursor))`.
    """
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    at_place = order.tape_cursor_event_id

    # The venue's own clock runs ahead of ours: this delta lands on the tape now but is stamped
    # a minute into our future.
    clock.advance(15)
    skewed = _delta(db_session, T2, clock.now + timedelta(seconds=60), "yes", "0.35", "-40.00",
                    seq=2)
    db_session.commit()
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.tape_cursor_event_id == at_place
    assert order.tape_cursor_event_id < skewed.id
    assert order.queue_remaining == Decimal("40.00")

    # Once the clock catches up the delta is consumed, through the `book_at` restart branch.
    clock.advance(120)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.tape_cursor_event_id == skewed.id
    assert order.queue_remaining == Decimal("0.00")


def test_a_fresh_executor_resumes_from_the_persisted_cursor(env_settings, db_session, world):
    """Replay's first step, and every restart: no book cache, so the cursor is behind the head."""
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)

    _delta(db_session, T2, clock.now + timedelta(seconds=2), "yes", "0.35", "-10.00", seq=2)
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.35", "60", trade_id="resume")
    db_session.commit()

    clock.advance(15)
    fresh = make_executor(env_settings, db_session, clock)
    assert fresh.books == {}
    fresh.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    # 40 resting, 10 cancelled by the delta, 60 printed: 30 clears the queue, 30 is ours.
    assert order.queue_remaining == Decimal("0.00")
    assert order.filled_contracts == Decimal("30.00")


# --- fix 22: the delta read ------------------------------------------------------------


@contextmanager
def capture_sql(session):
    """Every statement the engine actually sends, for the length of the block."""
    seen: list[str] = []
    engine = session.get_bind()

    def hook(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", hook)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", hook)


def _delta_where(seen: list[str]) -> str:
    """The `where` clause of the one `orderbook_events` delta read in `seen`."""
    reads = [q for q in seen if "orderbook_events" in q and "'delta'" in q]
    assert len(reads) == 1, reads
    body = reads[0].lower()
    return body.split("where", 1)[1].split("order by", 1)[0]


def test_the_live_delta_read_drops_the_ts_bound_once_a_cursor_exists(env_settings, db_session,
                                                                     world):
    """Fix 22. `id` is monotone, so past a cursor the `ts` bound selects nothing extra -- and on
    the production tape it cost a BitmapAnd against a 1.67M-row index every loop, per ticker."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    lower = NOW - timedelta(hours=9)

    with capture_sql(db_session) as seen:
        store.load_deltas(db_session, T2, 0, lower)
    first = _delta_where(seen)
    assert "ts >=" in first
    assert "limit" in seen[-1].lower()

    with capture_sql(db_session) as seen:
        store.load_deltas(db_session, T2, 1234, lower)
    live = _delta_where(seen)
    assert "ts" not in live
    assert "id >" in live
    assert "limit" in seen[-1].lower()

    # The replay path is untouched: both `ts` bounds, ordered by (ts, id), no limit.
    with capture_sql(db_session) as seen:
        store.load_deltas(db_session, T2, 1234, lower, at=NOW)
    replay = _delta_where(seen)
    assert "ts >=" in replay and "ts <=" in replay
    assert "order by ts, id" in seen[-1].lower()
    assert "limit" not in seen[-1].lower()


def test_a_partial_delta_batch_advances_the_cursor_and_the_next_loop_continues(
        env_settings, db_session, world, monkeypatch):
    """A backlog is walked over several bounded loops, each starting where the last stopped.

    Before fix 22 the read was unbounded, so a ticker far enough behind produced one statement
    that ran past the 30 s timeout, died, advanced nothing, and left the next loop the identical
    read to fail on.
    """
    monkeypatch.setattr(store, "DELTA_BATCH_LIMIT", 2)
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    assert orders_of(db_session)[0].queue_remaining == Decimal("40.00")

    backlog = [_delta(db_session, T2, NOW + timedelta(seconds=n), "yes", "0.35", "-5.00",
                      seq=1 + n) for n in range(1, 6)]
    db_session.commit()

    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    # Two of the five, and the cursor sits on the last row consumed -- not on the tape's head.
    assert order.tape_cursor_event_id == backlog[1].id
    assert order.nw_tape_cursor_event_id == backlog[1].id
    assert order.queue_remaining == Decimal("30.00")
    # A truncated read is the executor running behind the tape, and the heartbeat says so.
    assert "tape_lag" in stats.last_error
    assert T2 in stats.last_error
    heartbeat = db_session.execute(text("select last_error from exec_heartbeat")).scalar()
    assert "tape_lag" in heartbeat

    clock.advance(15)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.tape_cursor_event_id == backlog[3].id
    assert order.queue_remaining == Decimal("20.00")

    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.tape_cursor_event_id == backlog[4].id
    assert order.queue_remaining == Decimal("15.00")


def test_a_failed_tape_read_costs_one_ticker_and_keeps_every_other_cursor(
        env_settings, db_session, world, monkeypatch):
    """Fix 22: the statement timeout we actually saw. It must not roll the step back."""
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
    before = orders_of(db_session, T3)[0].tape_cursor_event_id

    real = store.load_deltas

    def explode(session, ticker, *args, **kwargs):
        if ticker == T3:
            raise RuntimeError("canceling statement due to statement timeout")
        return real(session, ticker, *args, **kwargs)

    monkeypatch.setattr(store, "load_deltas", explode)
    moved = _delta(db_session, T2, clock.now + timedelta(seconds=2), "yes", "0.35", "-40.00",
                   seq=2)
    _print(db_session, T3, clock.now + timedelta(seconds=5), "0.40", "500", trade_id="p3-lost")
    db_session.commit()

    clock.advance(15)
    stats = executor.step()
    refresh(db_session)

    assert stats.errors >= 1
    # T2 read, simulated and committed its cursor in the same step T3's read failed.
    t2 = orders_of(db_session, T2)[0]
    assert t2.tape_cursor_event_id == moved.id
    assert t2.queue_remaining == Decimal("0.00")
    # T3 sat the loop out entirely: no fills off a tape we could not read, cursor untouched.
    t3 = orders_of(db_session, T3)[0]
    assert t3.tape_cursor_event_id == before
    assert fills_of(db_session, t3.id) == []
    heartbeat = db_session.execute(text("select loops, last_error from exec_heartbeat")).one()
    assert heartbeat.loops == 2
    assert "statement timeout" in heartbeat.last_error

    # The next loop reads it fine and the print it missed is still there to be consumed.
    monkeypatch.setattr(store, "load_deltas", real)
    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert orders_of(db_session, T3)[0].filled_contracts == SIZE3


def test_a_finished_track_cursor_does_not_drag_the_ticker_scan_back(env_settings, db_session,
                                                                    world):
    """Fix 22: the read starts at the earliest cursor a track will actually consume from.

    A cancelled order's watched cursor is frozen where that track stopped. Folding it into the
    per-ticker minimum would re-read the whole window behind it on every loop for as long as the
    order's no-watcher counterfactual runs -- up to `intent_ttl_s` of tape, every 15 s.
    """
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    frozen = order.tape_cursor_event_id

    # Cancel the watched track by hand; the no-watcher track runs on.
    db_session.query(Order).filter_by(id=order.id).update({"status": "cancelled"})
    db_session.commit()

    later = _delta(db_session, T2, clock.now + timedelta(seconds=2), "yes", "0.35", "-40.00",
                   seq=2)
    db_session.commit()
    clock.advance(15)

    working = store.working_orders(db_session, False)
    with capture_sql(db_session) as seen:
        executor._tape(db_session, working, clock.now,
                       {"last_error": None, "tape_lag": []})
    where = _delta_where(seen)
    assert "ts" not in where
    reads = [q for q in seen if "orderbook_events" in q and "'delta'" in q]
    assert reads, seen
    refresh(db_session)

    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.status == "cancelled"
    assert order.tape_cursor_event_id == frozen
    assert order.nw_tape_cursor_event_id == later.id


def test_gap_and_resubscribe_inside_one_period_re_anchor_the_queue(env_settings, db_session, world):
    """A WS gap and the resubscribe snapshot that follows it can both land between two steps.

    `advance_book` then re-anchors inside one call and the book is clean at both step
    boundaries, so a recovery detected by comparing dirtiness across steps would miss it and the
    order would keep a queue built from a tape with a hole in it.
    """
    clock = Clock(NOW)
    snapshot = _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    assert orders_of(db_session)[0].queue_remaining == Decimal("40.00")

    _gap(db_session, NOW + timedelta(seconds=1), sid=snapshot.sid)
    fresh = _ws_snapshot(db_session, T2, NOW + timedelta(seconds=5),
                         [("0.50", "20.00"), ("0.35", "5.00")], [("0.48", "50.00")], sid=3)
    db_session.commit()

    clock.advance(15)
    executor.step()
    refresh(db_session)

    assert executor.books[T2].dirty is False
    order = orders_of(db_session)[0]
    assert order.queue_remaining == Decimal("5.00")
    assert order.tape_cursor_event_id == fresh.id
    assert order.nw_tape_cursor_event_id == fresh.id


def test_recovery_after_a_dirty_stretch_clamps_the_queue_and_moves_the_cursors(
        env_settings, db_session, world):
    """The step-boundary case: dirty for a whole loop, then a clean snapshot."""
    clock = Clock(NOW)
    snapshot = _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    _gap(db_session, NOW + timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert orders_of(db_session)[0].queue_remaining == Decimal("40.00")

    clean = _ws_snapshot(db_session, T2, clock.now + timedelta(seconds=5),
                         [("0.50", "20.00"), ("0.35", "8.00")], [("0.48", "50.00")], sid=4)
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.queue_remaining == Decimal("8.00")
    assert order.tape_cursor_event_id == clean.id


def test_expired_order_with_a_dirty_book_is_marked_nw_done(env_settings, db_session, world):
    """Both early returns must still close the no-watcher track, or the order is rescanned for
    the rest of the season and widens every other order's print window on its ticker."""
    clock = Clock(NOW)
    snapshot = _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]

    _gap(db_session, NOW + timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    clock.now = order.expiry + timedelta(seconds=1)
    clock.mono += 15
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.status == "expired"
    assert order.nw_done is True
    assert store.working_orders(db_session, False) == []


def test_expired_order_that_never_got_a_book_is_marked_nw_done(env_settings, db_session, world):
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.book_source == "none"

    clock.now = order.expiry + timedelta(seconds=1)
    clock.mono += 15
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.nw_done is True
    assert store.working_orders(db_session, False) == []


def test_book_cache_evicts_a_ticker_with_nothing_working_on_it(env_settings, db_session, world):
    """One BookState per ticker ever traded would accumulate all season, and a dormant entry is
    later advanced from a very old `as_of`."""
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    assert set(executor.books) == {T2}

    executor.books["KXNFL-P-9"] = executor.books[T2].copy()
    clock.advance(15)
    executor.step()
    assert set(executor.books) == {T2}


def test_step_returns_stats_when_the_heartbeat_write_fails(env_settings, db_session, world,
                                                           monkeypatch):
    """`step()` says it never raises. The scheduler has no error path of its own, so a raise out
    of the heartbeat write would take the whole loop down."""
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)

    from harness.execution import loop as loop_module

    def boom(*_args, **_kwargs):
        raise RuntimeError("heartbeat is down")

    monkeypatch.setattr(loop_module.store, "write_heartbeat", boom)
    stats = executor.step()
    assert isinstance(stats, ExecStats)
    assert stats.errors >= 1

    # The lock was still released, so the next step runs normally.
    monkeypatch.undo()
    clock.advance(15)
    assert executor.step().locked is True


def test_cancelling_or_expiring_an_order_twice_reports_only_the_first(env_settings, db_session,
                                                                     world):
    """`stats.cancelled`/`expired` move on a row the statement actually changed, like `placed`."""
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)
    order = orders_of(db_session)[0]

    assert store.cancel_order(db_session, order.id, "kill_switch", NOW) is True
    assert store.cancel_order(db_session, order.id, "kill_switch", NOW) is False
    assert store.expire_order(db_session, order.id) is False
    db_session.rollback()


def test_fair_books_json_is_null_because_fair_values_has_no_books_column(env_settings,
                                                                        db_session, world):
    """A named gap, not an oversight: `fair_values` carries `model_json` (the margin model's
    parameters, NULL for a direct fair value) and no column holding the devigged per-book inputs
    behind the consensus. Until one exists, `orders.fair_books_json` cannot be filled."""
    from harness.db.models import FairValue

    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    make_executor(env_settings, db_session, Clock(NOW)).step()
    refresh(db_session)

    assert orders_of(db_session)[0].fair_books_json is None
    assert not [c for c in FairValue.__table__.columns if "book" in c.name and c.name != "newest_book_ts"]


def test_cross_is_written_once_when_the_tracks_diverge_after_a_cancel(env_settings, db_session,
                                                                     world):
    """After a cancel the watched track stops and the no-watcher track runs on, so the two
    cursors diverge and `_sim_book` hands them different books. Exactly one cross row."""
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
    assert fills_of(db_session, order.id, "snapshot_cross") == []

    _delta(db_session, T2, clock.now + timedelta(seconds=5), "no", "0.70", "25.00", seq=2)
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert len(fills_of(db_session, order.id, "snapshot_cross")) == 1

    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert len(fills_of(db_session, order.id, "snapshot_cross")) == 1


def test_newest_intent_per_key_breaks_a_timestamp_tie_on_the_signal(env_settings, db_session,
                                                                    world):
    """Two signals for one key stamped the same instant: the later signal is the newer intent."""
    old = db_session.query(Signal).filter_by(venue_market_id=VM2, decision="candidate").one()
    db_session.add(Signal(run_id=old.run_id + 1000, variant_id=old.variant_id,
                          gap_snapshot_id=old.gap_snapshot_id, venue_market_id=VM2, side="yes",
                          fair_p=old.fair_p, fair_source=old.fair_source,
                          price_target=Decimal("0.3300"), as_estimate=old.as_estimate,
                          edge=old.edge, edge_min=old.edge_min, stake=old.stake, contracts=90,
                          decision="candidate", labels={}, created_at=old.created_at))
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()

    stats = make_executor(env_settings, db_session, Clock(NOW)).step()
    refresh(db_session)

    assert stats.intents_new == 2
    assert orders_of(db_session)[0].prob == Decimal("0.3300")


# --- Task 12b telemetry ----------------------------------------------------------------


def test_telemetry_writer_failure_does_not_poison_the_step(env_settings, db_session, world, monkeypatch):
    """Fix round 1, C1: a database-level failure inside a telemetry writer (here,
    `store.insert_order_watch_samples`) must land in its own savepoint, not the step's whole
    transaction -- this step's own fill and heartbeat write must still commit, and `step()`
    must still return normally rather than raising `InFailedSqlTransaction`."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)

    executor.step()  # places the order
    refresh(db_session)
    order = orders_of(db_session)[0]

    # A print that fills the order on the next step, the same step the writer below poisons.
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.35", "50", trade_id="c1-fill")
    db_session.commit()

    def poison(session, rows):
        session.execute(text("select 1/0"))  # a real Postgres-level failure, not just Python's
        return 0

    monkeypatch.setattr(store, "insert_order_watch_samples", poison)

    clock.advance(15)
    stats = executor.step()  # must not raise
    refresh(db_session)

    assert stats.locked is True
    assert len(fills_of(db_session, order.id, "queue_model")) == 1  # this step's fill survived

    from harness.db.models import ExecHeartbeat

    hb = db_session.get(ExecHeartbeat, 1)
    assert hb.last_loop_at == clock.now  # the heartbeat still committed this step


def test_exec_writes_metric_batch_every_metric_sample_s_not_every_loop(env_settings, db_session, world):
    """`metric_sample_s = 60` over a 15 s loop period: one batch on the first loop, none of the
    next three, then a second batch once 60 s of the executor's own clock has passed."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)

    executor.step()
    refresh(db_session)
    first_count = db_session.query(MetricSample).filter_by(source="exec").count()
    assert first_count > 0
    names = {r.name for r in db_session.query(MetricSample).filter_by(source="exec").all()}
    assert {"exec.loop_ms", "exec.open_orders", "exec.dirty_markets",
           "exec.intents_considered", "exec.placed", "exec.filled_contracts"} <= names

    for _ in range(3):
        clock.advance(15)
        executor.step()
        refresh(db_session)
    assert db_session.query(MetricSample).filter_by(source="exec").count() == first_count

    clock.advance(15)  # the executor's own monotonic clock has now advanced 60s since loop 1
    executor.step()
    refresh(db_session)
    assert db_session.query(MetricSample).filter_by(source="exec").count() > first_count


def _ws_age_samples(env_settings, db_session, clock, ws_last):
    """One `_write_metric_batch` with a hand-built heartbeat, returning the WS-clock pair
    `(exec.ws_event_age_s, exec.ws_event_ahead_s)` it wrote."""
    executor = make_executor(env_settings, db_session, clock)
    heartbeat = {"book_dirty_markets": 0, "ws_last_event_at": ws_last}
    wrote = executor._write_metric_batch(db_session, clock.now, ExecStats(loop_ms=7),
                                         heartbeat, open_orders_count=0)
    assert wrote is True  # the first call of a fresh Sampler is always due
    db_session.commit()
    rows = {r.name: r.value for r in db_session.query(MetricSample).filter(
        MetricSample.name.in_(["exec.ws_event_age_s", "exec.ws_event_ahead_s"])).all()}
    assert set(rows) == {"exec.ws_event_age_s", "exec.ws_event_ahead_s"}, (
        "both WS-clock samples are written on every batch, NULL or not")
    return rows["exec.ws_event_age_s"], rows["exec.ws_event_ahead_s"]


def test_ws_event_age_clamped_at_zero_and_skew_recorded_ahead(env_settings, db_session, world):
    """The exchange's clock can be ahead of the executor's, which used to write a negative
    `exec.ws_event_age_s` and break verify.md's `metric_samples.value < 0` invariant. The age
    is now clamped at zero and the skew is kept, non-negative, as `exec.ws_event_ahead_s`."""
    clock = Clock(NOW)
    age, ahead = _ws_age_samples(env_settings, db_session, clock,
                                 clock.now + timedelta(seconds=7))
    assert age == Decimal("0.000000")
    assert ahead == Decimal("7.000000")


def test_ws_event_age_behind_clock_is_the_age_and_ahead_is_zero(env_settings, db_session, world):
    """The ordinary case is unchanged: a 30 s old event is still 30 s old, and nothing is
    ahead of the executor's clock."""
    clock = Clock(NOW)
    age, ahead = _ws_age_samples(env_settings, db_session, clock,
                                 clock.now - timedelta(seconds=30))
    assert age == Decimal("30.000000")
    assert ahead == Decimal("0.000000")


def test_ws_event_age_and_ahead_are_both_null_before_any_event(env_settings, db_session, world):
    """No WS event has ever arrived: both samples are written, both NULL -- so verify.md's
    "every exec.* name younger than 5 minutes" never reads the gap as a missing metric."""
    clock = Clock(NOW)
    age, ahead = _ws_age_samples(env_settings, db_session, clock, None)
    assert age is None
    assert ahead is None


def test_open_order_watch_sample_every_60s_and_terminal_row_on_cancel(env_settings, db_session, world):
    """One periodic `order_watch_samples` row per open order every `watch_sample_s`, plus one
    terminal row the loop it leaves the open set."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)

    executor.step()  # places the order; it is not "working" until the next loop
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert db_session.query(OrderWatchSample).filter_by(order_id=order.id).count() == 0

    def samples():
        return (db_session.query(OrderWatchSample).filter_by(order_id=order.id)
               .order_by(OrderWatchSample.ts).all())

    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert len(samples()) == 1
    assert samples()[0].terminal is None
    assert samples()[0].queue_remaining is not None

    clock.advance(15)  # 15s since the first watch sample: not due again yet
    executor.step()
    refresh(db_session)
    assert len(samples()) == 1

    db_session.add(KillSwitch(id=1, active=True, reason="test", set_at=clock.now))
    db_session.commit()
    clock.advance(15)
    executor.step()
    refresh(db_session)

    order = db_session.get(Order, order.id)
    assert order.status == "cancelled"
    rows = samples()
    assert len(rows) == 2
    assert rows[-1].terminal == "cancelled"

    # Fix round 1, M4: a terminal order's sampler entry is forgotten, not kept forever.
    assert order.id not in executor._watch_sampler._last


def test_equity_snapshot_cash_equals_bankroll_plus_ledger_and_mtm_coverage(env_settings, db_session, world):
    """Bankroll plus the ledger's cash movement, with a mark-to-market that only counts the
    contracts whose ticker has a live book right now (design spec §3.4)."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    # A short equity_sample_s keeps the test from having to run the clock far enough forward
    # that the book/tape look stale to the executor's own staleness rules.
    settings = env_settings.model_copy(update={"equity_sample_s": 5})
    executor = make_executor(settings, db_session, clock)

    executor.step()  # places the order; equity_sample_s's first call is always due, cash=3000
    refresh(db_session)
    order = orders_of(db_session)[0]

    # Fix round 1, I1: no open positions yet -> mtm_coverage is NULL, not a defaulted 1.
    first_snap = (db_session.query(EquitySnapshot).filter_by(variant_id=order.variant_id)
                 .order_by(EquitySnapshot.ts.desc()).first())
    assert first_snap is not None
    assert first_snap.mtm_open is None
    assert first_snap.mtm_coverage is None

    # A print through the resting price fills the order, so there's a position to mark.
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.35", "50", trade_id="fill1")
    db_session.commit()
    clock.advance(15)  # >= equity_sample_s since the first sample: due again
    executor.step()
    refresh(db_session)

    snap = (db_session.query(EquitySnapshot).filter_by(variant_id=order.variant_id)
           .order_by(EquitySnapshot.ts.desc()).first())
    assert snap is not None

    ledger_sum = db_session.query(func.sum(Ledger.cash_delta)).filter_by(
        variant_id=order.variant_id, replay=False).scalar() or Decimal("0")
    assert ledger_sum < 0  # the fill actually moved cash, or this test proves nothing
    assert snap.cash == Decimal("3000") + ledger_sum
    assert snap.mtm_coverage == Decimal("1.0000")  # T2's book is live at sample time
    assert snap.n_open_orders >= 0


def test_replay_writes_no_telemetry(env_settings, db_session, world):
    """A replay executor writes no metric samples, operator events, watch samples or equity
    snapshots -- ever (ruling 1)."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock, replay=True)

    for _ in range(5):
        executor.step()
        clock.advance(60)  # spans every sampler's period at least once
    refresh(db_session)

    assert db_session.query(MetricSample).count() == 0
    assert db_session.query(OperatorEvent).count() == 0
    assert db_session.query(OrderWatchSample).count() == 0
    assert db_session.query(EquitySnapshot).count() == 0


def test_exec_startup_deploy_and_config_change_events(env_settings, db_session, world):
    """A `deploy` event once the heartbeat's executor_version disagrees with this build's, and
    a `config_change` event once a variant's newest open order's config_hash disagrees with
    what this process would place next -- both checked once, at startup."""
    from harness.db.models import ExecHeartbeat

    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.add(ExecHeartbeat(id=1, executor_version="0.1", loops=0, open_orders=0))
    db_session.commit()

    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)

    deploy_events = db_session.query(OperatorEvent).filter_by(kind="deploy").all()
    assert len(deploy_events) == 1
    assert deploy_events[0].ref == {"from": "0.1", "to": EXECUTOR_VERSION}

    # The startup check runs once per Executor instance, not once per loop.
    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert db_session.query(OperatorEvent).filter_by(kind="deploy").count() == 1

    order = orders_of(db_session)[0]
    order.config_hash = "not-the-real-hash"
    db_session.commit()

    clock2 = Clock(NOW + timedelta(seconds=1))
    make_executor(env_settings, db_session, clock2).step()
    refresh(db_session)

    config_events = db_session.query(OperatorEvent).filter_by(kind="config_change").all()
    assert len(config_events) == 1
    assert config_events[0].ref["variant_id"] == order.variant_id
    assert config_events[0].ref["from"] == "not-the-real-hash"


# --- Final fix wave, I2: `store.newest_event_ts(at=...)` is bounded --------------------------

AT = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)


def _tape_row(session, ts, ticker="K1", seq=1):
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=2, seq=seq, kind="delta", side="yes",
                         price=Decimal("0.35"), delta=Decimal("1.00"),
                         raw={"market_ticker": ticker})
    session.add(row)
    session.flush()
    return row


def test_newest_event_ts_at_reads_the_newest_by_ts_not_the_highest_id(db_session):
    """Final fix wave, I2. The replay path asks "how old is the tape's newest row at this
    instant"; a backward walk of the primary key answers "which row was taped last", which is
    a different question the moment the recorder writes out of `ts` order -- and, on a real
    game day, it is a walk over every row taped *after* the instant before it reaches one at
    or before it. Twenty rows after the instant and three before it, the last of which carries
    the highest id and the *oldest* ts, is the shape that separates the two answers.
    """
    from harness.db.schema import ensure_partitions

    ensure_partitions(db_session, AT)
    _tape_row(db_session, AT - timedelta(seconds=300))
    _tape_row(db_session, AT - timedelta(seconds=60))
    # Taped last (highest id) but stamped oldest: `order by id desc` would return this one.
    _tape_row(db_session, AT - timedelta(seconds=600))
    for i in range(20):
        _tape_row(db_session, AT + timedelta(seconds=i + 1))

    assert store.newest_event_ts(db_session, AT) == AT - timedelta(seconds=60)


def test_newest_event_ts_at_widens_once_then_reads_no_tape_at_all(db_session):
    """The 10 minute window is widened once to 24 h, and stops there: a tape whose newest row
    at the instant is older than a day reads as no tape (`None`), which the loop already
    treats exactly as it treats a row older than `book_max_age_s` -- `dead_recorder`."""
    from harness.db.schema import ensure_partitions

    ensure_partitions(db_session, AT - timedelta(hours=30))
    ensure_partitions(db_session, AT)

    _tape_row(db_session, AT - timedelta(hours=30))
    assert store.newest_event_ts(db_session, AT) is None

    # Inside the widened 24 h window, it is found.
    _tape_row(db_session, AT - timedelta(hours=6))
    assert store.newest_event_ts(db_session, AT) == AT - timedelta(hours=6)
