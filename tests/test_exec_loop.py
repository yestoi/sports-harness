"""The paper executor's loop against a real database: intake, books, fills, actions, heartbeat.

Every test seeds one tick of the real pipeline (`tests/test_pipeline._seed` plus the `tiny`
primary and `price_and_signal`), keeps exactly one candidate signal, and then shapes the tape
under it. The loop is driven with an explicit clock, so `now` is a value in the test rather
than wall time, and one step is one commit.
"""

import importlib.util
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from psycopg.errors import QueryCanceled, UndefinedColumn
from psycopg.types.json import Jsonb
from sqlalchemy import event, func, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

import harness.execution.loop
from harness.config.settings import get_settings
from harness.db.models import (
    EquitySnapshot,
    Fill,
    Game,
    Intent,
    KillSwitch,
    Ledger,
    MarketDirtyInterval,
    MarketObservationInterval,
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
from harness.execution.loop import ExecStats, Executor, _clamped
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


def dirty_intervals_of(session, venue_market_id):
    return (session.query(MarketDirtyInterval)
            .filter_by(venue_market_id=venue_market_id)
            .order_by(MarketDirtyInterval.id).all())


# --- version pin ---------------------------------------------------------------------


def test_executor_version_is_bumped_for_the_loop():
    assert EXECUTOR_VERSION == "4.5"


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
    # D7 is enforced by the print floor the anchoring book sets, so the rule is pinned on the
    # floor itself and not only on its consequence (6B §0.4, §0.6, round 1 minor): the book was
    # taken at NOW + 10 s, the floor is that instant less `DELTA_LOOKBACK`, and `_merge_events`
    # admits only prints strictly above it -- which is why the print at exactly NOW + 5 s, inside
    # the no-book window, is discarded rather than applied against the queue this book
    # established. `last_print_ts` carries the same instant for any reader still on that column.
    floor = NOW + timedelta(seconds=5)
    assert datetime.fromisoformat(order.recon_state["print_floor"]) == floor
    assert order.last_print_ts == floor

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


def test_the_ledger_columns_are_written_and_traded_at_price_stays_null(env_settings, db_session,
                                                                      world):
    """6B §1.3 and §2: the ledger's own columns carry the state, `traded_at_price` carries NULL.

    Computed independently: 40 rest ahead of us and a print of 50 goes off at our price, so 40
    clears the queue and 10 is ours -- and the whole 50 is print volume whose decrement has not
    arrived, because this tape has no delta at our level at all. So `print_unmatched` is 50,
    both bucket sums are 0 (nothing unclaimed came off the book), `cancels_ahead` is 0 (nothing
    retired) and `recon_state` holds the print's own id with an empty bucket list.

    `traded_at_price` must be NULL on both tracks: it is C0's charge-against quantity, not a
    ledger term, and §3's boundary query reads exactly that nullness to tell a post-6B order
    from a pre-6B one (ruling CR-3). It is NULL from placement, not merely after the first
    simulation, so an order placed and never stepped cannot read as pre-boundary either.
    """
    clock = Clock(NOW)
    _place_and_print(env_settings, db_session, clock)

    order = orders_of(db_session)[0]
    assert order.filled_contracts == Decimal("10.00")
    assert order.traded_at_price is None and order.nw_traded_at_price is None
    assert order.print_unmatched == Decimal("50.00")
    assert order.pending_unmatched == Decimal("0.00")
    assert order.pending_surplus == Decimal("0.00")
    assert order.cancels_ahead == Decimal("0.00")
    assert order.recon_state["buckets"] == []
    assert [tid for _ts, tid in order.recon_state["trade_ids"]] == ["t1"]
    assert order.recon_state["print_floor"] is None
    # The counterfactual ran the same tape on its own columns (F3).
    assert order.nw_print_unmatched == Decimal("50.00")
    assert order.nw_recon_state["trade_ids"] == order.recon_state["trade_ids"]
    # §2's invariant, on this row: the two scalars are the surviving buckets' sums.
    sums = sum(Decimal(size) for _ts, _kind, size in order.recon_state["buckets"])
    assert order.pending_unmatched + order.pending_surplus == sums


def test_a_placed_order_carries_no_traded_at_price_before_any_simulation(env_settings, db_session,
                                                                        world):
    """The same boundary rule at placement: the insert leaves both columns NULL (§1.3, §2).

    Computed independently: the column's meaning is "the C0 quantity this build charged prints
    against", and this build charges nothing against it. An order placed after the 6B deploy and
    cancelled before its first fill step would otherwise carry a 0 there and read as a pre-6B row
    in §3's boundary query, which is the one thing that query is for.
    """
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.filled_contracts == Decimal("0.00")
    assert order.traded_at_price is None and order.nw_traded_at_price is None


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


def _bystander(session, ticker="KXNFL-P-BYSTANDER"):
    """A finished order the step never selects: `status = 'expired'` and `nw_done = true`, which
    is exactly what `store.working_orders` filters out. It carries the pre-repair twins a
    pre-boundary row has, so the assertion that it keeps a NULL version is the "never
    backfilled" half of amendment 0.17."""
    from uuid import uuid4

    order = Order(
        intent_id=uuid4(), variant_id="p00000000001", venue="kalshi",
        client_order_id=f"bystander-{uuid4()}", ticker=ticker, venue_market_id=99,
        side="yes", prob=Decimal("0.30"), contracts=Decimal("10.00"), status="expired",
        placed_at=NOW - timedelta(hours=1), expiry=NOW - timedelta(minutes=30),
        filled_contracts=Decimal("0.00"), nw_filled_contracts=Decimal("5.00"),
        nw_print_unmatched=Decimal("3.00"), nw_dirty_seconds=15, nw_done=True, replay=False)
    session.add(order)
    session.flush()
    return order


def test_a_counterfactual_step_stamps_the_executor_version_and_backfills_nothing(
        env_settings, db_session, world):
    """Spec amendment 0.17 (roadmap row 72): every counterfactual write carries its writer's
    version, and no statement touches the column on a row it is not otherwise writing.

    The stepped order's counterfactual state is written by `_state_columns("nw_", ...)` through
    `store.update_order`, so that is where the stamp has to be -- not at a call site, which the
    next `nw_` writer would forget. The bystander is a finished pre-boundary-shaped row with
    non-null twins: it must still read NULL afterwards, because that combination is what the
    narrowed §2 invariant calls an anomaly.

    The column is nulled between the two steps (review I-1). Placement stamps the row itself,
    and `update_order` never writes a NULL, so without that the second assertion would read the
    value the *insert* wrote and the test would stay green with the stamp taken out of
    `update_order` -- the one writer `_state_columns("nw_", ...)`, `nw_filled_contracts` and
    `nw_done` all flow through."""
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    bystander = _bystander(db_session)
    db_session.commit()

    executor = make_executor(env_settings, db_session, clock)
    executor.step()                      # placement writes the counterfactual's opening state
    refresh(db_session)
    placed = orders_of(db_session)[0]
    assert placed.nw_executor_version == Decimal(EXECUTOR_VERSION)

    db_session.query(Order).filter_by(id=placed.id).update({"nw_executor_version": None})
    db_session.commit()

    clock.advance(15)
    executor.step()                      # the first step of both tracks, through `update_order`
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.nw_done is False
    assert order.nw_executor_version == Decimal(EXECUTOR_VERSION)
    assert db_session.get(Order, bystander.id).nw_executor_version is None


def test_the_counterfactual_dirty_and_backoff_writers_stamp_the_version_too(db_session):
    """The two `nw_` writers that do not go through `_state_columns`: §1.5's own nominal accrual
    (`orders.nw_dirty_seconds`, a raw UPDATE) and §0.14's retry bookkeeping. Both write an
    `nw_` column on the counterfactual's behalf, so both carry the version; the watched
    accrual, which writes no `nw_` column, leaves it alone."""
    order = _bystander(db_session, ticker="KXNFL-P-WRITERS")
    db_session.commit()

    store.add_dirty_seconds(db_session, order.id, 15, watched=True)
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(Order, order.id).nw_executor_version is None

    store.add_dirty_seconds(db_session, order.id, 15, watched=False)
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(Order, order.id).nw_executor_version == Decimal(EXECUTOR_VERSION)

    db_session.query(Order).filter_by(id=order.id).update({"nw_executor_version": None})
    db_session.flush()
    store.set_nw_backoff(db_session, order.id, attempts=1,
                         next_attempt_at=NOW + timedelta(seconds=30))
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(Order, order.id).nw_executor_version == Decimal(EXECUTOR_VERSION)


def test_the_executor_version_stays_a_decimal_numeral_while_the_column_is_numeric():
    """Review M-1: `orders.nw_executor_version` is `numeric` (spec amendment 0.17), so the stamp
    parses `EXECUTOR_VERSION` as a `Decimal`. A bump to `4.5.1` or `4.6-rc1` would raise
    `decimal.InvalidOperation` inside every order insert and every counterfactual step, so the
    constraint is pinned here -- next to the writers that depend on it -- and stated beside the
    constant itself. `executor_version_numeric` raises a named error rather than the driver's."""
    from harness.execution import store as store_mod

    assert store_mod.executor_version_numeric() == Decimal(EXECUTOR_VERSION)
    assert str(EXECUTOR_VERSION) == EXECUTOR_VERSION


def test_a_non_numeric_executor_version_fails_loudly_and_says_why(monkeypatch):
    """Not `decimal.InvalidOperation` from inside a write: the message names the constant, the
    column and the amendment, so the next person to bump the version reads what to do."""
    import harness.execution as execution_pkg
    from harness.execution import store as store_mod

    monkeypatch.setattr(execution_pkg, "EXECUTOR_VERSION", "4.6-rc1")
    with pytest.raises(ValueError) as caught:
        store_mod.executor_version_numeric()
    message = str(caught.value)
    assert "EXECUTOR_VERSION" in message and "4.6-rc1" in message
    assert "nw_executor_version" in message


def test_no_watcher_fills_stop_ten_minutes_before_kickoff(env_settings, db_session, world):
    """The user's ruling of 2026-09-14 15:38 CT (journal 206): the counterfactual's deadline is
    bounded by `kickoff - 10 minutes`, the same instant `fills_outside_placement_window` calls
    the end of the placement window. The watched track is not bounded by it.

    Derived independently of the loop. The order rests with 40 contracts ahead of it at 0.35.
    A print of 60 lands three seconds after placement -- inside the bound -- so 40 of it is the
    queue and 20 reaches the order on both tracks. A print of 500 lands eight seconds after
    placement, three seconds past `kickoff - 10 min`, and is tape the counterfactual never sees:
    it fills the watched track to its whole 97 contracts and leaves the counterfactual at 20.

    The order's kickoff and expiry are moved after placement, because an order placed normally
    expires at `kickoff - exec_kickoff_cutoff_min` already (`plan.py:564`) -- the case the bound
    exists for is the one where they differ: a kickoff moved after placement, or an order whose
    expiry outlives the window.
    """
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.queue_ahead_at_place == Decimal("40.00")
    # `kickoff - 10 min` is NOW + 5 s; the expiry is deliberately far past it.
    db_session.query(Order).filter_by(id=order.id).update(
        {"kickoff_utc": NOW + timedelta(minutes=10, seconds=5),
         "expiry": NOW + timedelta(minutes=20)})
    db_session.commit()

    inside = NOW + timedelta(seconds=3)
    outside = NOW + timedelta(seconds=8)
    _print(db_session, T2, inside, "0.35", "60", trade_id="inside-the-window")
    _print(db_session, T2, outside, "0.35", "500", trade_id="past-the-window")
    db_session.commit()

    clock.advance(15)
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    watched = fills_of(db_session, order.id, "queue_model")
    nw = fills_of(db_session, order.id, "no_watcher")
    assert [f.filled_at for f in nw] == [inside]
    assert order.nw_filled_contracts == Decimal("20.00")
    assert [f.filled_at for f in watched] == [inside, outside]
    assert order.filled_contracts == SIZE2
    assert order.status == "filled"


def test_a_counterfactual_with_no_kickoff_keeps_the_expiry_deadline(env_settings, db_session,
                                                                   world):
    """The bound is `min(deadline, kickoff - 10 min)` only when the order carries a kickoff.
    With no kickoff there is no window to bound to, so the counterfactual stops at the expiry
    exactly as it did before -- the same tape as the test above, scored the other way.
    """
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    db_session.query(Order).filter_by(id=order.id).update(
        {"kickoff_utc": None, "expiry": NOW + timedelta(minutes=20)})
    db_session.commit()

    _print(db_session, T2, NOW + timedelta(seconds=3), "0.35", "60", trade_id="inside-nk")
    _print(db_session, T2, NOW + timedelta(seconds=8), "0.35", "500", trade_id="later-nk")
    db_session.commit()

    clock.advance(15)
    executor.step()
    refresh(db_session)

    order = orders_of(db_session)[0]
    assert order.nw_filled_contracts == SIZE2
    assert order.filled_contracts == SIZE2


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


def test_a_rejected_latest_decision_writes_one_skip_row_and_no_order(env_settings, db_session,
                                                                     world):
    """Review round 1, Important 2 (C4): the spec's idempotence claim -- one `order_events` row,
    not just a stable planner action -- for the `signal_rejected` reason, modelled on
    `test_skip_recorded_once_across_two_steps` above.

    Derived independently: VM2's own candidate signal is what `insert_intents` turns into the
    one intent this test acts on, but a *newer* signal for the same `(variant, venue_market,
    side)` key already exists with decision `rejected` before the loop ever runs -- exactly a
    strategy that withdrew its own signal before the executor got to it. `load_intents` reads
    `latest_decision` from that newer row (`store.py`'s `_NEWEST_DECISIONS`), so the intent is
    skipped with reason `signal_rejected` on its very first loop, and every loop after, and
    `uq_skip_once` (partial on `kind in ('skipped', 'cap_gate')`) is what keeps the second and
    later loops' writes from adding a second row.
    """
    old = db_session.query(Signal).filter_by(venue_market_id=VM2, decision="candidate").one()
    db_session.add(Signal(run_id=old.run_id + 1000, variant_id=old.variant_id,
                          gap_snapshot_id=old.gap_snapshot_id, venue_market_id=VM2, side="yes",
                          fair_p=old.fair_p, fair_source=old.fair_source,
                          venue_best_bid=old.venue_best_bid, venue_best_ask=old.venue_best_ask,
                          price_target=old.price_target, fee_at_target=old.fee_at_target,
                          as_estimate=old.as_estimate, edge=old.edge, edge_min=old.edge_min,
                          stake=old.stake, contracts=old.contracts, decision="rejected",
                          rejection_reason="edge", labels={},
                          created_at=old.created_at + timedelta(seconds=5)))
    db_session.commit()

    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    first = executor.step()
    refresh(db_session)
    intent = db_session.query(Intent).one()
    assert intent.venue_market_id == VM2
    assert orders_of(db_session, T2) == []
    skips = [e for e in events_of(db_session, "skipped") if e.reason == "signal_rejected"]
    assert len(skips) == 1 and skips[0].intent_id == intent.id
    assert first.skipped == 1

    clock.advance(15)
    second = executor.step()
    refresh(db_session)
    assert len([e for e in events_of(db_session, "skipped")
               if e.reason == "signal_rejected"]) == 1
    assert second.skipped == 0
    assert orders_of(db_session, T2) == []


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


def test_a_ticker_whose_book_cannot_load_does_not_abort_the_step(
        env_settings, db_session, world, monkeypatch, caplog):
    """Fix 60: one ticker's book failure is one ticker's failure, not the step's.

    The executor spent Sunday and Monday losing every step to the first ticker whose newest
    snapshot could not be read, so its heartbeat never went green, no order was ever cancelled
    at kickoff and 3,426 loops were skipped. Here T2's book raises and T3's does not: the step
    still completes with a null `last_error`, T3's book advances and its order fills, and T2's
    order holds where it is -- no fill, no cancel -- because a book we could not read says
    nothing about the queue.
    """
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

    real = executor._book_now

    def explode(session, ticker, now, cached, ws_connect_at=None):
        # 6B §0.3 gave `_book_now` the reconnect instant; the stub takes it and hands it on.
        if ticker == T2:
            raise ValueError("snapshot without yes_dollars_fp")
        return real(session, ticker, now, cached, ws_connect_at)

    monkeypatch.setattr(executor, "_book_now", explode)
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.30", "500", trade_id="p2")
    _print(db_session, T3, clock.now + timedelta(seconds=5), "0.40", "500", trade_id="p3")
    # A delta on T3's untouched NO side: it lands only if T3's book was advanced this step.
    _delta(db_session, T3, clock.now + timedelta(seconds=6), "no", "0.48", "-10.00", seq=2)
    db_session.commit()
    clock.advance(15)
    with caplog.at_level("WARNING", logger="harness.execution.loop"):
        stats = executor.step()
    refresh(db_session)

    # The step completed and its heartbeat is green: no other ticker failed.
    heartbeat = db_session.execute(text("select loops, last_error from exec_heartbeat")).one()
    assert heartbeat.loops == 2
    assert heartbeat.last_error is None
    assert stats.book_errors == 1
    # The healthy ticker advanced and filled.
    assert executor.books[T3].no_bids == {Decimal("0.4800"): Decimal("50.00")}
    assert orders_of(db_session, T3)[0].filled_contracts == SIZE3
    # The broken ticker held: no fill, no cancel, and the hold is recorded as dirty time.
    broken = orders_of(db_session, T2)[0]
    assert broken.status == "open"
    assert broken.filled_contracts == Decimal("0")
    assert fills_of(db_session, broken.id) == []
    assert [e for e in events_of(db_session, "cancel") if e.order_id == broken.id] == []
    assert broken.dirty_seconds == 15
    # The log line names the ticker and the exception class.
    assert any(T2 in r.getMessage() and "ValueError" in r.getMessage()
               for r in caplog.records)


def test_a_ticker_unreadable_with_a_fresh_cached_book_opens_a_book_unreadable_dirty_interval(
        env_settings, db_session, world, monkeypatch):
    """Row 69 (6B merge review, carried item 2): T2's book raises on the second step while its
    step-1 cached book is still fresh (age 15 s, well under `book_max_age_s`). `_market_now`
    already marked this market `book_dirty` here (fix 60's guard), which makes `MarketNow.dirty`
    true, but `_advance_books` opened no `market_dirty_intervals` row for it at all -- no cause
    named "could not read" existed, so the interval ledger under-reported this dirtiness. A
    `book_unreadable` row now opens at the guard site and closes on the next successful read.
    """
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
    # The first step's book is clean: no dirty row yet.
    assert dirty_intervals_of(db_session, VM2) == []

    real = executor._book_now

    def explode(session, ticker, now, cached, ws_connect_at=None):
        # 6B §0.3 gave `_book_now` the reconnect instant; the stub takes it and hands it on.
        if ticker == T2:
            raise ValueError("snapshot without yes_dollars_fp")
        return real(session, ticker, now, cached, ws_connect_at)

    monkeypatch.setattr(executor, "_book_now", explode)
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    assert stats.book_errors == 1

    rows = dirty_intervals_of(db_session, VM2)
    assert [(r.cause, r.ended_at) for r in rows] == [("book_unreadable", None)]

    # The next step reads T2's book again: the interval closes.
    monkeypatch.setattr(executor, "_book_now", real)
    clock.advance(15)
    executor.step()
    refresh(db_session)

    rows = dirty_intervals_of(db_session, VM2)
    assert [(r.cause, r.ended_at) for r in rows] == [("book_unreadable", clock.now)]


def test_a_step_that_raises_after_advance_books_does_not_orphan_a_departed_markets_rows(
        env_settings, db_session, world, monkeypatch):
    """Fix 70 leak (journal 224 item 4): `gone` is now read from the database, not from
    `self._market_ids`, an in-memory map a raised step or a process restart can leave stale.

    Step 1 places an order on T2 and opens its observation row. A gap makes the book dirty at
    step 2, opening its dirty row too -- both steps commit normally. Step 3 raises inside
    `_simulate`, after `_advance_books` has already returned and updated whatever in-memory
    state is about to be thrown away; `_locked_step` rolls that step's own writes back, which
    costs T2's rows nothing since they were already open before the step ran. A fresh `Executor`
    then models a process restart: its `self.books` and `self._dirty_tickers` both start over
    at empty, and there is no `self._market_ids` left to carry anything across steps at all.
    T2's order is then closed out and its intent aged past the TTL, so it is absent from every
    set the next step builds; that step must still close both of T2's rows, because the
    database -- not memory -- is what says they are open.
    """
    snapshot = _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()

    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.status == "open"
    assert dirty_intervals_of(db_session, VM2) == []

    _gap(db_session, NOW + timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()

    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert [(r.cause, r.ended_at) for r in dirty_intervals_of(db_session, VM2)] == [
        ("gap", None)]

    def explode(*a, **kw):
        raise ValueError("simulate blew up")

    monkeypatch.setattr(executor, "_simulate", explode)
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    assert stats.errors == 1
    # The raise rolled that step's own writes back; T2's rows are exactly where step 2 left
    # them, open and undisturbed.
    assert [(r.cause, r.ended_at) for r in dirty_intervals_of(db_session, VM2)] == [
        ("gap", None)]

    # Close the order out from under the loop and let its intent age past the TTL, so the next
    # step's `rows` names nothing on T2 at all.
    order.status, order.nw_done = "expired", True
    db_session.commit()

    # A fresh `Executor`: no cached book, no dirty ticker, and (since the attribute is gone)
    # nothing at all standing in for "what was open last step".
    restarted = make_executor(env_settings, db_session, clock)
    clock.advance(1970)  # well past `exec_intent_ttl_s` (900 s), so the old intent ages out too
    stats = restarted.step()
    refresh(db_session)
    assert stats.errors == 0

    assert [r.ended_at for r in dirty_intervals_of(db_session, VM2)] == [clock.now]
    observed = (db_session.query(MarketObservationInterval)
                .filter_by(venue_market_id=VM2)
                .order_by(MarketObservationInterval.id).all())
    assert [r.ended_at for r in observed] == [clock.now]


def _book_error_samples(session):
    """`exec.book_errors` in write order, as plain ints."""
    return [int(r.value) for r in session.query(MetricSample)
            .filter_by(source="exec", name="exec.book_errors")
            .order_by(MetricSample.id).all()]


def test_a_ticker_whose_book_cannot_load_reports_the_exec_book_errors_metric(
        env_settings, db_session, world, monkeypatch):
    """Fix 66, M1: `stats.book_errors` used to be counted and read by nothing but the test
    itself (fix 60) -- a ticker whose book had been unreadable for days was invisible to
    verify.md and the dashboard, and an operator would have had to grep container logs for
    "book read failed". `exec.book_errors` is now written beside `exec.tape_lag_tickers` in the
    same metric batch, so the step from
    `test_a_ticker_whose_book_cannot_load_does_not_abort_the_step` also leaves a gauge behind.
    """
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
    # The first step is a fresh Sampler's first call, always due: a clean batch, zero errors.
    assert _book_error_samples(db_session) == [0]

    real = executor._book_now

    def explode(session, ticker, now, cached, ws_connect_at=None):
        # 6B §0.3 gave `_book_now` the reconnect instant; the stub takes it and hands it on.
        if ticker == T2:
            raise ValueError("snapshot without yes_dollars_fp")
        return real(session, ticker, now, cached, ws_connect_at)

    monkeypatch.setattr(executor, "_book_now", explode)
    _print(db_session, T2, clock.now + timedelta(seconds=5), "0.30", "500", trade_id="p2")
    _print(db_session, T3, clock.now + timedelta(seconds=5), "0.40", "500", trade_id="p3")
    db_session.commit()
    # `metric_sample_s = 60`: advanced a full period so the second batch is due on this step too.
    clock.advance(60)
    stats = executor.step()
    refresh(db_session)

    assert stats.book_errors == 1
    assert _book_error_samples(db_session) == [0, 1]


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
    # 40 resting, a -10 delta, then a print of 60 three seconds later. 6B §1.3 reconciles the two
    # instead of counting both: the -10 took 10 off the queue when it arrived, and the print
    # claims those 10 as the trade it is reporting, so they neither move the queue again nor
    # fill. 50 of the print's 60 is volume no ledger term explains -- 30 clears the rest of the
    # queue and 20 reaches us. (Pre-6B the decrement was read as a cancellation and the whole 60
    # moved the queue a second time, which is the double count C3 removes; that answer was 30.)
    assert order.queue_remaining == Decimal("0.00")
    assert order.filled_contracts == Decimal("20.00")


# --- fix 22: the delta read ------------------------------------------------------------


@contextmanager
def capture_sql(session):
    """Every statement the engine actually sends, with its parameters, for the block.

    The parameters are captured as well as the text because the text alone cannot tell a scan
    that followed the live track from one dragged back to a frozen cursor: both spell the same
    `id > :cursor`, and only the bound value says which cursor it was.
    """
    seen: list[tuple[str, object]] = []
    engine = session.get_bind()

    def hook(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", hook)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", hook)


def _delta_read(seen) -> tuple[str, dict]:
    """The one `orderbook_events` delta read in `seen`, lowercased, with its parameters."""
    reads = [(q, p) for q, p in seen if "orderbook_events" in q and "'delta'" in q]
    assert len(reads) == 1, [q for q, _ in reads] or [q for q, _ in seen]
    return reads[0][0].lower(), reads[0][1]


def _delta_where(seen) -> str:
    """The `where` clause of that read."""
    body, _ = _delta_read(seen)
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
    body, params = _delta_read(seen)
    assert "ts >=" in _delta_where(seen)
    assert "limit" in body
    assert params["cursor"] == 0

    with capture_sql(db_session) as seen:
        store.load_deltas(db_session, T2, 1234, lower)
    body, params = _delta_read(seen)
    live = _delta_where(seen)
    assert "ts" not in live
    assert "id >" in live
    assert "limit" in body
    assert params["cursor"] == 1234

    # The replay path is untouched: both `ts` bounds, ordered by (ts, id), no limit.
    with capture_sql(db_session) as seen:
        store.load_deltas(db_session, T2, 1234, lower, at=NOW)
    body, _ = _delta_read(seen)
    replay = _delta_where(seen)
    assert "ts >=" in replay and "ts <=" in replay
    assert "order by ts, id" in body
    assert "limit" not in body


def _lag_samples(session):
    """`exec.tape_lag_tickers` in write order, as plain ints."""
    return [int(r.value) for r in session.query(MetricSample)
            .filter_by(source="exec", name="exec.tape_lag_tickers")
            .order_by(MetricSample.id).all()]


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
    # The first loop has no working order and so no tape to be behind on.
    assert _lag_samples(db_session) == [0]

    backlog = [_delta(db_session, T2, NOW + timedelta(seconds=n), "yes", "0.35", "-1.00",
                      seq=1 + n) for n in range(1, 13)]
    db_session.commit()

    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    # Two of the twelve, and the cursor sits on the last row consumed -- not on the tape's head.
    assert order.tape_cursor_event_id == backlog[1].id
    assert order.nw_tape_cursor_event_id == backlog[1].id
    assert order.queue_remaining == Decimal("38.00")
    # Running behind the tape is not a failure and never reaches `last_error`, whose null
    # verify.md's heartbeat row depends on (fix 22 round 1, I1).
    assert stats.last_error is None
    assert stats.errors == 0
    assert db_session.execute(text("select last_error from exec_heartbeat")).scalar() is None

    clock.advance(15)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.tape_cursor_event_id == backlog[3].id
    assert order.queue_remaining == Decimal("36.00")

    clock.advance(15)
    executor.step()
    refresh(db_session)
    assert orders_of(db_session)[0].tape_cursor_event_id == backlog[5].id

    # `metric_sample_s = 60`: this is the loop the second batch is written from, and the
    # ticker is still four rows behind, so the lag is reported as a metric.
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.tape_cursor_event_id == backlog[7].id
    assert order.queue_remaining == Decimal("32.00")
    assert _lag_samples(db_session) == [0, 1]
    assert stats.last_error is None
    assert db_session.execute(text("select last_error from exec_heartbeat")).scalar() is None


def test_a_truncated_loop_does_not_close_a_track_at_its_expiry(env_settings, db_session, world,
                                                               monkeypatch):
    """Fix 22 round 1, I2: a track must not finish over tape it has not been fed.

    The order's expiry falls inside a loop whose delta read truncated, so the deltas and prints
    between its cursor and now are still held back. Closing the track there would put fills in
    the replay that the live record never saw, so expiry waits for the loop that catches up.
    """
    monkeypatch.setattr(store, "DELTA_BATCH_LIMIT", 2)
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    db_session.query(Order).filter_by(id=order.id).update(
        {"expiry": NOW + timedelta(seconds=10)})
    db_session.commit()

    # Three deltas, the first of which clears our queue. The print that then fills us is
    # stamped past where a two-row batch can reach, so it is held back with them.
    for n, (price, size) in enumerate([("0.35", "-40.00"), ("0.50", "-1.00"),
                                       ("0.50", "-1.00")], start=1):
        _delta(db_session, T2, NOW + timedelta(seconds=n), "yes", price, size, seq=1 + n)
    _print(db_session, T2, NOW + timedelta(seconds=5), "0.35", "500", trade_id="held-back")
    db_session.commit()

    clock.advance(15)  # now is past the order's expiry
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.queue_remaining == Decimal("0.00")   # the two deltas we did read were applied
    assert order.status == "open"                     # but neither track is finished
    assert order.nw_done is False
    assert events_of(db_session, "expire") == []
    assert fills_of(db_session, order.id) == []

    # The next loop reads the rest of the tape, so the print is fed to a track still open to it.
    clock.advance(15)
    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.filled_contracts == SIZE2
    assert order.nw_filled_contracts == SIZE2
    assert order.nw_done is True
    assert order.status == "filled"


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


# --- fix 26: the delta batch adapts to what a read can finish inside the timeout ------------


def _statement_timeout() -> OperationalError:
    """The engine's 10 s statement timeout exactly as it reaches the loop: psycopg raises
    `QueryCanceled` (SQLSTATE 57014) and SQLAlchemy hands it on wrapped in `OperationalError`
    with the driver's exception on `.orig`. Both are constructible without a connection, so no
    test has to hold a real statement open for ten seconds to produce the failure that matters.
    """
    return OperationalError("select ... from orderbook_events", {},
                            QueryCanceled("canceling statement due to statement timeout"))


def _programming_error() -> ProgrammingError:
    """A failure that is not the timeout: the batch size is not what is wrong with it."""
    return ProgrammingError("select nope from orderbook_events", {},
                            UndefinedColumn('column "nope" does not exist'))


def _two_working_tickers(env_settings, db_session, clock):
    """One loop run, T2 and T3 both placed and working, each with a book under it."""
    keep_only(db_session, {VM2, VM3})
    signal3 = db_session.query(Signal).filter_by(venue_market_id=VM3).one()
    signal3.decision, signal3.rejection_reason = "candidate", None
    _book2(db_session, NOW - timedelta(seconds=5))
    _book3(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    assert len(db_session.query(Order).all()) == 2
    return executor


def _read_tape(executor, db_session, clock):
    """One `_tape` pass over every working order, the way a step makes it.

    Four members since 6B §1.5: the tape, `unread`, `lagging` and `deferred` -- the tickers the
    counterfactual backoff kept this step from reading at all, which is deliberately not folded
    into `unread`, whose length feeds `stats.errors` (review CR-2).
    """
    return executor._tape(db_session, store.working_orders(db_session, False), clock.now,
                          {"last_error": None, "tape_lag": []})


def test_a_statement_timeout_shrinks_that_tickers_batch_alone_and_stops_at_the_floor(
        env_settings, db_session, world, monkeypatch):
    """Fix 26 (a). On the NAS five tickers a million ids behind timed out every loop forever:
    20 000 rows of cold pages cannot be read in 10 s, so the cursor never moved and the watch
    never ended. The batch is quartered on each timeout until it is small enough to finish, and
    stops at `DELTA_BATCH_FLOOR` -- below that the ticker could not walk off its backlog before
    the market settled even if every read succeeded. It is per ticker: the one that timed out
    is the one that reads less, and a healthy ticker beside it keeps the full cap.
    """
    clock = Clock(NOW)
    executor = _two_working_tickers(env_settings, db_session, clock)
    real = store.load_deltas
    asked: list[tuple[str, int]] = []

    def explode(session, ticker, *args, **kwargs):
        asked.append((ticker, kwargs["limit"]))
        if ticker == T3:
            raise _statement_timeout()
        return real(session, ticker, *args, **kwargs)

    monkeypatch.setattr(store, "load_deltas", explode)
    sizes = []
    for _ in range(6):
        _, unread, _, _ = _read_tape(executor, db_session, clock)
        assert unread == {T3}
        sizes.append(executor._delta_batch[T3])

    assert sizes == [5_000, 1_250, 312, 250, 250, 250]
    assert store.DELTA_BATCH_FLOOR == 250
    # Each read asked for what the previous timeout left behind, and the floor is a floor.
    assert [n for t, n in asked if t == T3] == [20_000, 5_000, 1_250, 312, 250, 250]
    # T3's trouble never touched T2, which read cleanly at the cap and carries no entry at all.
    assert T2 not in executor._delta_batch
    assert [n for t, n in asked if t == T2] == [store.DELTA_BATCH_LIMIT] * 6


def test_a_non_timeout_failure_leaves_the_batch_size_alone(env_settings, db_session, world,
                                                           monkeypatch):
    """Fix 26 (e). A bad statement, a dropped connection or a serialization failure is not a
    read that was too big to finish, and shrinking on it would quietly cripple a ticker whose
    reads were never the problem. The failure is still recorded exactly as before."""
    clock = Clock(NOW)
    executor = _two_working_tickers(env_settings, db_session, clock)
    real = store.load_deltas

    def explode(session, ticker, *args, **kwargs):
        if ticker == T3:
            raise _programming_error()
        return real(session, ticker, *args, **kwargs)

    monkeypatch.setattr(store, "load_deltas", explode)
    heartbeat = {"last_error": None, "tape_lag": []}
    _, unread, _, _ = executor._tape(db_session, store.working_orders(db_session, False),
                                     clock.now, heartbeat)
    assert unread == {T3}
    assert executor._delta_batch == {}
    assert "ProgrammingError" in heartbeat["last_error"]

    # And a ticker already shrunk by a real timeout keeps the size it earned.
    executor._delta_batch[T3] = 250
    executor._tape(db_session, store.working_orders(db_session, False), clock.now,
                   {"last_error": None, "tape_lag": []})
    assert executor._delta_batch == {T3: 250}


def test_a_full_batch_doubles_a_shrunk_ticker_back_and_never_past_the_cap(
        env_settings, db_session, world, monkeypatch):
    """Fix 26 (b). Once the pages are warm the same read costs 6 ms, so a ticker held at the
    floor for the rest of a game would walk its backlog off far slower than it could. Every
    read that comes back full -- proof this size finished inside the timeout -- doubles it, so
    a recovered ticker is back at `DELTA_BATCH_LIMIT` within a few loops and stops there."""
    clock = Clock(NOW)
    executor = _two_working_tickers(env_settings, db_session, clock)
    real = store.load_deltas
    asked: list[tuple[str, int]] = []

    def full(session, ticker, *args, **kwargs):
        asked.append((ticker, kwargs["limit"]))
        # The rows are the real ones; only the "there is more behind this" flag is forced, so
        # the doubling is exercised without seeding 250 deltas per loop.
        return store.DeltaBatch(real(session, ticker, *args, **kwargs).deltas, True)

    monkeypatch.setattr(store, "load_deltas", full)
    executor._delta_batch[T3] = store.DELTA_BATCH_FLOOR
    sizes = []
    for _ in range(4):
        _, _, lagging, _ = _read_tape(executor, db_session, clock)
        assert T3 in lagging
        sizes.append(executor._delta_batch[T3])
    assert sizes == [500, 1_000, 2_000, 4_000]
    assert [n for t, n in asked if t == T3] == [250, 500, 1_000, 2_000]

    # The cap holds: a full batch one doubling away from it lands on it, not past it.
    executor._delta_batch[T3] = store.DELTA_BATCH_LIMIT - 1
    _read_tape(executor, db_session, clock)
    assert executor._delta_batch[T3] == store.DELTA_BATCH_LIMIT
    _read_tape(executor, db_session, clock)
    assert executor._delta_batch[T3] == store.DELTA_BATCH_LIMIT


def test_a_caught_up_ticker_at_the_cap_and_a_settled_one_stop_costing_an_entry(
        env_settings, db_session, world, monkeypatch):
    """Fix 26: the dict is bounded. A ticker reading short at the full cap has nothing left to
    remember, and one with no working order will not be read again at all -- a season of
    tickers would otherwise accumulate in a process that never restarts."""
    clock = Clock(NOW)
    executor = _two_working_tickers(env_settings, db_session, clock)
    executor._delta_batch = {T2: store.DELTA_BATCH_LIMIT, T3: 250, "KXNFL-P-9": 250}

    _read_tape(executor, db_session, clock)
    # T2 read short at the cap, so its entry is dropped; T3 keeps the size it earned, because
    # one short read says its backlog is gone, not that its pages are warm; the ticker with no
    # working order is gone.
    assert executor._delta_batch == {T3: 250}


def _batch_min_samples(session):
    """`exec.tape_batch_min` in write order, as plain ints."""
    return [int(r.value) for r in session.query(MetricSample)
            .filter_by(source="exec", name="exec.tape_batch_min")
            .order_by(MetricSample.id).all()]


def test_tape_batch_min_reports_the_smallest_batch_in_force(env_settings, db_session, world,
                                                            monkeypatch):
    """Fix 26 (d). Beside `exec.tape_lag_tickers` this separates the two ways of being behind:
    lag at the cap is a backlog being walked off, lag on the floor is a ticker whose reads keep
    timing out. A fresh executor reports the cap, because nothing is shrunk."""
    clock = Clock(NOW)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    executor = make_executor(env_settings, db_session, clock)
    executor.step()
    refresh(db_session)
    assert _batch_min_samples(db_session) == [store.DELTA_BATCH_LIMIT]

    def explode(session, ticker, *args, **kwargs):
        raise _statement_timeout()

    monkeypatch.setattr(store, "load_deltas", explode)
    # `metric_sample_s = 60`: the fourth loop from here is the one that writes the next batch,
    # and four timeouts have taken the batch 20 000 -> 5 000 -> 1 250 -> 312 -> the floor by
    # then. The shrink happens in the step's own tape pass, so the batch it reports is the one
    # this loop settled on, not the one it started with.
    for _ in range(4):
        clock.advance(15)
        executor.step()
        refresh(db_session)
    assert executor._delta_batch == {T2: store.DELTA_BATCH_FLOOR}
    assert _batch_min_samples(db_session) == [store.DELTA_BATCH_LIMIT, 250]

    # The smallest in force, not the newest: a second ticker on the floor is what is reported.
    executor._delta_batch = {T2: 1_000, T3: store.DELTA_BATCH_FLOOR}
    assert executor._tape_batch_min() == 250
    executor._delta_batch = {}
    assert executor._tape_batch_min() == store.DELTA_BATCH_LIMIT


def test_load_deltas_honours_the_limit_it_is_given(env_settings, db_session, world):
    """Fix 26 (c). The cap is the caller's to choose, `truncated` is measured against the limit
    the read actually ran with, and the default is still `DELTA_BATCH_LIMIT`."""
    _book2(db_session, NOW - timedelta(seconds=5))
    for n in range(1, 5):
        _delta(db_session, T2, NOW + timedelta(seconds=n), "yes", "0.35", "-1.00", seq=1 + n)
    db_session.commit()
    lower = NOW - timedelta(hours=9)

    with capture_sql(db_session) as seen:
        batch = store.load_deltas(db_session, T2, 0, lower, limit=2)
    body, params = _delta_read(seen)
    assert params["limit"] == 2
    assert len(batch.deltas) == 2
    assert batch.truncated is True

    with capture_sql(db_session) as seen:
        batch = store.load_deltas(db_session, T2, 0, lower, limit=500)
    assert _delta_read(seen)[1]["limit"] == 500
    assert 0 < len(batch.deltas) < 500
    assert batch.truncated is False

    with capture_sql(db_session) as seen:
        store.load_deltas(db_session, T2, 0, lower)
    assert _delta_read(seen)[1]["limit"] == store.DELTA_BATCH_LIMIT

    # The replay path still reads a closed range and takes no limit at all.
    with capture_sql(db_session) as seen:
        assert store.load_deltas(db_session, T2, 0, lower, at=NOW, limit=2).truncated is False
    assert "limit" not in _delta_read(seen)[0]


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

    executor.step()
    refresh(db_session)
    order = orders_of(db_session)[0]
    assert order.status == "cancelled"
    # The cancelled track stayed where it stopped; the counterfactual walked on past it. Only
    # now do the two cursors differ, which is what makes the next assertion able to fail.
    assert order.tape_cursor_event_id == frozen
    assert order.nw_tape_cursor_event_id == later.id
    assert frozen < later.id

    working = store.working_orders(db_session, False)
    with capture_sql(db_session) as seen:
        executor._tape(db_session, working, clock.now,
                       {"last_error": None, "tape_lag": []})
    where, params = _delta_where(seen), _delta_read(seen)[1]
    assert "ts" not in where
    # The scan starts at the live track's cursor, not at the frozen one it shares the ticker
    # with. Folding the frozen cursor in would re-read everything between them every loop, and
    # under `DELTA_BATCH_LIMIT` could hand back a batch the live tracks have already consumed.
    assert params["cursor"] == later.id


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
    assert {"exec.loop_ms", "exec.open_orders", "exec.dirty_markets", "exec.tape_lag_tickers",
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


def test_exec_writes_fair_age_and_signal_to_order_ms_from_the_placement_it_just_made(
        env_settings, db_session, world):
    """Fix round 1, I1(a): a wiring test for `_body`'s `fair_age_s` feed and `_place`'s
    `signal_to_order_ms` feed into the batch `_write_metric_batch` actually writes -- the plan's
    own tests exercise `_MetricsAcc`/`_percentile` as units and `_pricing_samples` separately,
    but nothing before this test called `executor.step()` and read `exec.fair_age_s` or
    `exec.signal_to_order_ms` back out of `metric_samples`. Deleting any of the three wiring
    sites (the `_body` reading, the `_place` reading, or the two `_write_metric_batch` loops)
    leaves this test failing while every other test in this suite stays green (fix round 1
    finding I1, confirmed by mutation).

    Computed by hand: `world`'s `tiny` signal for T2 is created at `NOW`
    (`price_and_signal(db_session, run.id, NOW, ...)`); the executor's clock reads `NOW + 12s`
    for this one step, so the one market the loop prices (T2, the only surviving candidate)
    has `fair_age_s(now) = 12`, giving p50 = p95 = 12 over the single observation, and the
    placement's `signal_to_order_ms` is `(NOW + 12s) - NOW = 12,000` ms for the `tiny` variant.
    The Sampler's first call is always due (established by
    `test_exec_writes_metric_batch_every_metric_sample_s_not_every_loop` above), so this single
    step both places the order and writes the batch that samples it -- a second step would
    reset the accumulator before writing nothing for `signal_to_order_ms` (fix round 1, I4)
    since no further placement occurs, so this test deliberately reads the first step's batch.
    """
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW + timedelta(seconds=12))
    executor = make_executor(env_settings, db_session, clock)

    stats = executor.step()
    refresh(db_session)
    assert stats.placed == 1
    order = orders_of(db_session)[0]

    rows = {(r.name, r.labels.get("q"), r.labels.get("variant")): r.value
           for r in db_session.query(MetricSample).filter(
               MetricSample.name.in_(["exec.fair_age_s", "exec.signal_to_order_ms"])).all()}
    assert rows[("exec.fair_age_s", "p50", None)] == 12
    assert rows[("exec.fair_age_s", "p95", None)] == 12
    assert rows[("exec.signal_to_order_ms", "p50", order.variant_id)] == 12_000


def _ws_age_samples(env_settings, db_session, clock, ws_last):
    """One `_write_metric_batch` with a hand-built heartbeat, returning the WS-clock pair
    `(exec.ws_event_age_s, exec.ws_event_ahead_s)` it wrote."""
    executor = make_executor(env_settings, db_session, clock)
    heartbeat = {"book_dirty_markets": 0, "ws_last_event_at": ws_last, "tape_lag": []}
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
    # 6D 1.2: an empty `metric_samples` cannot prove the latency accumulator was left alone --
    # a replay executor never reaches `_write_metric_batch` at all (loop.py:325). It never
    # reaches `reset()` either, so a fed list would grow for the whole replay.
    # `fair_age_s` is a bounded deque (fix round 1, I3); compare by contents, not container type.
    assert list(executor._metrics_acc.fair_age_s) == []
    assert executor._metrics_acc.signal_to_order_ms == {}


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


# ---- fix 57, round 1: the executor's own readers honour the unsynced-run key --------------
#
# Ruling 1 (journal 184): the in-game readers that reach the gap snapshots through
# `signals.run_id` must exclude a run recorded under an unsynchronized clock, or the annotation
# on run 14485 is documentation rather than an exclusion.

def test_an_unsynced_run_is_excluded_from_candidates_intents_and_decisions(db_session):
    import uuid as _uuid
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from datetime import timezone as _tz
    from decimal import Decimal as _D

    from harness.db.models import Intent, Run
    from harness.execution.store import _NEWEST_DECISIONS, candidate_signals, load_intents
    from harness.ops.clock import UNSYNCED_NOTE
    from tests.conftest import _make_leg

    now = _dt(2026, 9, 18, 20, 0, tzinfo=_tz.utc)
    made = _dt(2026, 9, 18, 19, 30, tzinfo=_tz.utc)

    def leg():
        return _make_leg(db_session, game_id=9001, market_type="moneyline", side_team_id=None,
                         side=None, threshold=None, fair_p=_D("0.55"), edge=_D("0.05"),
                         created_at=made)

    bad, annotated_ok, no_run_row = leg(), leg(), leg()
    # The run of `bad` carries the key; `annotated_ok`'s run row exists with no key at all;
    # `no_run_row`'s run was never written (a fixture, a backfill) -- both must be kept.
    db_session.add(Run(id=bad.signal.run_id, started_at=made, status="ok",
                       notes=dict(UNSYNCED_NOTE)))
    db_session.add(Run(id=annotated_ok.signal.run_id, started_at=made, status="ok", notes={}))
    for made_leg in (bad, annotated_ok, no_run_row):
        db_session.add(Intent(id=_uuid.uuid4(), signal_id=made_leg.signal.id, variant_id="tiny",
                              venue="kalshi", venue_market_id=made_leg.venue_market.id,
                              ticker=made_leg.venue_market.ticker, side="yes",
                              signal_created_at=made, created_at=made))
    db_session.commit()

    lower = now - _td(hours=6)
    # `_CANDIDATES` skips a signal that already has an intent, so candidates are read on a
    # fresh pair with no intents.
    bad2, good2 = leg(), leg()
    db_session.add(Run(id=bad2.signal.run_id, started_at=made, status="ok",
                       notes=dict(UNSYNCED_NOTE)))
    db_session.commit()
    got = {row.signal_id for row in candidate_signals(db_session, ["tiny"], lower, False)}
    assert good2.signal.id in got and bad2.signal.id not in got

    views, _extras = load_intents(db_session, ["tiny"], lower, False)
    signal_ids = {v.signal_id for v in views}
    assert annotated_ok.signal.id in signal_ids and no_run_row.signal.id in signal_ids
    assert bad.signal.id not in signal_ids

    decisions = db_session.execute(
        _NEWEST_DECISIONS, {"replay": False, "variants": ["tiny"], "lower": lower}).all()
    market_ids = {row.venue_market_id for row in decisions}
    assert annotated_ok.venue_market.id in market_ids
    assert bad.venue_market.id not in market_ids


# --- fix 78: the pending counterfactual population, written set-based -------------------
#
# The production backlog this fix exists for: 5,055 cancelled orders whose `no_watcher` track
# is still running (133 tickers, 56 games; 4,822 of them on dirty markets at 13:50 CT on
# 2026-09-15), each costing one savepoint and one UPDATE every 15 s loop. The cases below are
# the acceptance the user's ruling of 13:55 CT asked for: the same columns as the per-row path
# row for row, a statement count that does not grow with the population, and the SQL clamp
# compared against `_clamped` itself for every shape of row it can meet.

#: Far above the sequence the loop's own placements draw from, so a fixed id here cannot
#: collide with one the executor inserts.
PENDING_BASE_ID = 900_000
#: A fourth market of `_seed`, on its own subscription, so a print can move one order's tape
#: without touching the quiet population's trade-id set on T3. The fifth has no book and no
#: other order, so one row inside its counterfactual backoff defers the whole ticker.
T4, VM4 = "KXNFL-P-4", 4
T5, VM5 = "KXNFL-P-5", 5
#: The print that reaches the T4 row in the measured step. Its id is fixed so the reset can
#: take it out again between the two runs.
LATE_PRINT = "fix78-late-print"
#: Where each shape sits in `_mixed_shapes`, so an assertion can name the row it is about. The
#: first six are the dirty market's clamp; then the clean ticker's, three of which a loop owes
#: nothing and four of which it still owes a write; then the three populations the user's
#: ruling protects from this fix entirely -- two open orders and a deferred ticker.
FAR, INSIDE, FRACTION, SUBSECOND, NO_EXPIRY, PAST = range(6)
QUIET_FAR, QUIET_NONE, NO_BOOK, EXPIRES_BETWEEN, STALE_VERSION, TAPE_MOVES = range(6, 12)
QUIET_LEDGER, LEDGER_CLOSES, OPEN_HELD, OPEN_EXPIRES, DEFERRED = range(12, 17)
#: Fix 78b's own population: a clean-ticker row whose columns move but whose step inserts
#: nothing, so its whole write is one row of the batched `UPDATE ... FROM (VALUES ...)`.
BATCHED_MOVES = (EXPIRES_BETWEEN, STALE_VERSION, LEDGER_CLOSES)


def _pending_book(session, ticker, ts, sid):
    """A book with our own price resting on it, on a subscription of its own."""
    return _ws_snapshot(session, ticker, ts, [("0.50", "20.00"), ("0.35", "40.00")],
                        [("0.48", "50.00")], sid=sid)


def _pending_order(session, index, *, now, ticker, vm, expiry, queue=Decimal("5.00"),
                   version=None, prob=Decimal("0.3500"), status="cancelled", side="yes",
                   next_attempt_at=None, attempts=None, recon_state=None, variant_id="tiny",
                   cursor=None):
    """One order whose counterfactual is still running, as the backlog's 5,055 rows are.

    Cancelled by default: the watched track is over -- the order left the market when it was
    cancelled, which is why it accrues no watched dirty seconds (§0.9) -- and `nw_done` is
    false, so `working_orders` keeps returning it every loop until its own expiry. `status`
    makes an **open** one instead, which this fix must leave entirely on the per-row path; an
    open order has no `cancelled_at`, and a stale one would clamp its watched accrual to zero
    and hide exactly the accrual the case is checking.

    `side` is only ever moved for a second *open* order on one ticker: `uq_open_order`
    (`harness/db/schema.py:229`) is one live order per `(venue, ticker, side, variant_id)`, and
    it is partial on the open statuses -- which is why the cancelled backlog can pile up on one
    ticker and two resting orders cannot.

    `cursor` is the tape position both tracks sit at, and `seed_pending` passes the head of the
    ticker's own tape for every row that has a queue. The loop writes the queue and the cursor
    together and never one without the other (`_place`, `_anchor_tracks`, `SimState.anchor`), so
    a seeded row with a queue and a null cursor is a shape production does not have -- and the
    one the delta window's `min()` has to reach past (review rev-fix-78c, I1). A row with no
    queue keeps the null cursor, because that is exactly the shape it has until its first book.
    """
    resting = status in ("open", "partially_filled")
    row = Order(id=PENDING_BASE_ID + index, intent_id=uuid.UUID(int=PENDING_BASE_ID + index),
                variant_id=variant_id,
                venue="kalshi", mode="paper", client_order_id=f"fix78-{index}", ticker=ticker,
                venue_market_id=vm, side=side, prob=prob, contracts=Decimal("10.00"),
                status=status, placed_at=now - timedelta(minutes=5),
                cancelled_at=None if resting else now - timedelta(minutes=4), expiry=expiry,
                queue_ahead_at_place=queue, queue_remaining=queue, nw_queue_remaining=queue,
                tape_cursor_event_id=cursor, nw_tape_cursor_event_id=cursor,
                nw_filled_contracts=Decimal("0.00"), nw_done=False,
                nw_recon_state=recon_state, nw_next_attempt_at=next_attempt_at,
                nw_attempts=attempts, nw_executor_version=version, replay=False)
    session.add(row)
    return row


def _written_ledger(t0):
    """A counterfactual ledger as §1.3 writes one: two live decrement buckets, one unmatched
    print claim and two seen trade ids, all inside the row's own window.

    A quiet row carrying this has to come out of the measured loop with the document byte for
    byte as it went in -- nothing on its ticker moved, so nothing ages, is claimed or is
    retired -- which is what exercises `_unchanged`'s JSONB equality on a non-empty document
    rather than on an empty one (round 1, M4).
    """
    def at(seconds):
        return (t0 - timedelta(seconds=seconds)).isoformat()

    return {"buckets": [[at(30), "pending", "3.00"], [at(20), "surplus", "2.00"]],
            "prints": [[at(25), "1.50"]],
            "trade_ids": [[at(25), "seeded-print-1"], [at(24), "seeded-print-2"]],
            "print_floor": at(60)}


def _dirty_shapes(t0):
    """The clamp's every shape on a dirty market, as `_clamped(15, row, t0, watched=False)`
    reads them: no expiry at all (the whole period), one further off than the period, one
    inside it, one whose fraction has to truncate, one under a second, and one already past."""
    return [
        {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(hours=1)},
        {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(seconds=7)},
        {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(seconds=15, microseconds=500_000)},
        {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(milliseconds=400)},
        {"ticker": T2, "vm": VM2, "expiry": None},
        {"ticker": T2, "vm": VM2, "expiry": t0 - timedelta(minutes=1)},
    ]


def _quiet_shapes(t0):
    """Clean-ticker rows with nothing past their cursors: the population a loop owes nothing."""
    return [
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1)},
        {"ticker": T3, "vm": VM3, "expiry": None},
    ]


def _moving_shapes(t0):
    """Clean-ticker rows whose write moves a column on the measured loop and inserts nothing.

    Fix 78b's own population. The expiry of each falls between the two loops, so the second --
    the measured one -- closes the track (`nw_done`) and rewrites the counterfactual's state
    columns with it, exactly as the per-row path would; nothing on T3's tape moved, so no fill
    and no crossing is inserted for any of them and the whole population is one statement.
    """
    return [
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(seconds=5)},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(seconds=7)},
    ]


def _mixed_shapes(t0):
    """Both populations, every row that must still take the per-row path, and the three the
    ruling keeps off this fix altogether.

    Still per-row because a column moves: one that has never had a book (it anchors on the
    first one), one whose expiry falls between the two steps (`nw_done` moves), one stamped by
    an older build (the version moves) and one whose ticker gets a print between the steps (the
    tape moves). Quiet but not trivial: one carrying a written ledger, which has to survive the
    loop unchanged.

    Fix 78b: the three rows whose columns move without an insert -- the expiry that falls
    between the steps, the stale stamp, and a fourth added here, a row carrying a **written**
    ledger whose expiry also falls between the steps -- are the batched cursor-advance
    population, and the last of them is what puts a non-empty JSONB document, a `numeric` and a
    NULL through the `VALUES` list in one row. The row that fills and the one that has never had
    a book still insert or re-anchor, so they keep their savepoint.

    Kept off the fix entirely (round 1, M4): two **open** orders on the dirty market -- one
    that holds there and goes on accruing watched dirty seconds, one whose expiry falls between
    the steps so the second loop expires it -- and one row on a ticker of its own inside its
    counterfactual backoff, which ruling CR-4 says must sit the loop out with its cursors where
    they are rather than be closed.
    """
    return _dirty_shapes(t0) + _quiet_shapes(t0) + [
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1), "queue": None},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(seconds=5)},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1),
         "version": Decimal("4.4")},
        {"ticker": T4, "vm": VM4, "expiry": t0 + timedelta(hours=1)},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1),
         "recon_state": _written_ledger(t0)},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(seconds=5),
         "recon_state": _written_ledger(t0)},
        {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(hours=1), "status": "open"},
        {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(seconds=5), "status": "open",
         "side": "no"},
        {"ticker": T5, "vm": VM5, "expiry": t0 + timedelta(hours=1),
         "next_attempt_at": t0 + timedelta(hours=1), "attempts": 2},
    ]


def _tape_head(session, ticker):
    """The event a live book for this ticker would be anchored at: the head of its tape.

    The live book is read from the head with no upper bound at all, so this is the
    `last_event_id` the first loop's `base` will carry -- and therefore the cursor a row that
    has been walked once already holds (review rev-fix-78c, I1). None when the ticker has no
    tape yet, which is the no-book shape.
    """
    return session.execute(text("select max(id) from orderbook_events where ticker = :t"),
                           {"t": ticker}).scalar()


def seed_pending(session, t0, n, shapes):
    """`n` rows cycling `shapes`, so the same mix is present at every population size.

    The variant is resolved through the loop's own resolver rather than spelled `tiny`: signals
    and orders carry the 12-hex `variant_id`, and a row carrying the *name* is dropped from the
    decision chain by `_with_config` -- which would quietly take the open orders below out of
    `plan_actions` and with them the expiry this case is watching for.

    Every row with a queue is seeded at the head of its ticker's tape, because that is the pair
    the loop itself writes: a queue without a cursor is a shape no code path here produces
    (review rev-fix-78c, I1).
    """
    variant_id = store.resolve_variants(session, ["tiny"])[0]
    heads: dict[str, object] = {}
    ids = []
    for i in range(n):
        shape = shapes[i % len(shapes)]
        ticker = shape["ticker"]
        if ticker not in heads:
            heads[ticker] = _tape_head(session, ticker)
        cursor = None if shape.get("queue", Decimal("5.00")) is None else heads[ticker]
        ids.append(_pending_order(session, i, now=t0, variant_id=variant_id, cursor=cursor,
                                  **shape).id)
    session.commit()
    return ids


def reset_pending(session):
    """Everything one step of the loop writes, so two runs start from the same database.

    The interval tables and the heartbeat are in the list because the loop's own statement
    count depends on them -- an observation row that is already open is not opened again -- and
    the comparison below is of statement counts as much as of columns.
    """
    for table in ("fills", "ledger", "order_events", "order_watch_samples", "equity_snapshots",
                  "orders", "market_dirty_intervals", "market_observation_intervals",
                  "exec_heartbeat", "metric_samples"):
        session.execute(text(f"delete from {table}"))  # noqa: S608 - a fixed literal list
    session.execute(text("delete from venue_trades where trade_id = :t"), {"t": LATE_PRINT})
    session.commit()


def pending_columns(session, ids):
    """Every column of the population, by id: the comparison is column for column, so it is
    read as `select *` rather than as a list this test would have to keep up to date."""
    rows = session.execute(text("select * from orders where id = any(:ids) order by id"),
                           {"ids": ids}).mappings().all()
    return [dict(row) for row in rows]


def written_rows(session, table, order_by, skip=("id",)):
    """Every row one of the loop's other writers left, without the surrogate keys.

    `orders` alone would catch a diverging fill only through `nw_filled_contracts` (round 1,
    M5), so `fills`, `ledger` and `order_events` are compared too. The sequence is not reset
    between the two runs, so `id` -- and `ledger.fill_id`, which points at one -- is dropped:
    it is the one column that must differ for a reason the fix has nothing to do with.
    """
    rows = session.execute(text(f"select * from {table} order by {order_by}")).mappings().all()
    return [{k: v for k, v in row.items() if k not in skip} for row in rows]


def loop_writes(session):
    """The three tables beside `orders` that one loop over this population can write."""
    return {"fills": written_rows(session, "fills", "order_id, filled_at, id"),
            "ledger": written_rows(session, "ledger", "order_id, ts, id",
                                   skip=("id", "fill_id")),
            "order_events": written_rows(session, "order_events", "order_id, ts, id")}


def run_pending_steps(env_settings, session, t0, ids, shapes, *, batched):
    """Two loops over the seeded population, with the fix on or off, and the columns after.

    The second loop is the one that matters: by then every row has been written once, so a
    clean ticker with nothing new on its tape is a row whose write would move nothing -- which
    is the case the fix skips. The print and the stale version stamp are introduced between the
    steps, identically for both runs, so that the *measured* loop still contains rows the fix
    must refuse to skip.

    Returns the columns, the rows the loop wrote to `fills`, `ledger` and `order_events`, two
    costs of that second loop -- how many statements it sent (the identity only means something
    between two paths that are actually different, and with the fix off this population costs a
    savepoint, an UPDATE and a release per row) and how many times it walked a tape, which
    round 1's M2 requires the fix not to increase -- and the statements themselves, so a case
    can ask *which* statement wrote a given row (fix 78b).
    """
    clock = Clock(t0)
    executor = make_executor(env_settings, session, clock)
    executor._batch_pending_writes = batched
    executor.step()
    refresh(session)
    _print(session, T4, t0 + timedelta(seconds=14), "0.35", "500", trade_id=LATE_PRINT)
    session.execute(
        text("update orders set nw_executor_version = 4.4 where id = any(:ids)"),
        {"ids": [oid for i, oid in enumerate(ids) if i % len(shapes) == STALE_VERSION]})
    session.commit()
    clock.advance(15)
    with patch("harness.execution.loop.simulate_fills",
               wraps=harness.execution.loop.simulate_fills) as walks:
        with capture_sql(session) as seen:
            executor.step()
    refresh(session)
    return (pending_columns(session, ids), loop_writes(session), len(seen),
            walks.call_count, list(seen))


def test_the_set_based_pending_writes_match_the_per_row_path_column_for_column(
        env_settings, db_session, world):
    """Fix 78 acceptance (a): no measured value changes.

    The same population, the same tape and the same clock are run twice -- once with the
    per-row savepoint and UPDATE this fix replaces, once with the set-based writes -- and every
    column of every row is compared. `select *` is the comparison, so `nw_dirty_seconds`,
    `nw_done`, `nw_executor_version` and each of the counterfactual's state columns
    (`nw_queue_remaining`, `nw_tape_cursor_event_id`, `nw_last_print_ts`, `nw_last_print_ids`,
    `nw_recon_state`, the three ledger sums, `nw_cancels_ahead`, `nw_next_attempt_at`,
    `nw_attempts`) are in it by construction rather than by enumeration.

    The clamp's own arithmetic is asserted separately below, from values computed here rather
    than from the code: the loop runs at `t0` and `t0 + 15 s` with a 15 s period, so an order
    expiring an hour out accrues the whole period twice, one expiring 7 s after the first loop
    accrues 7 and then nothing, one expiring 15.5 s after it accrues 15 (the fraction truncates
    toward zero, as `int()` does) and then nothing, and one expiring 0.4 s after it accrues
    nothing at all -- so its column is never written and stays NULL rather than becoming 0.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    snapshot = _pending_book(db_session, T2, NOW - timedelta(seconds=5), sid=2)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _pending_book(db_session, T4, NOW - timedelta(seconds=5), sid=4)
    # T5 deliberately has no book at all: its one population is a deferred row, which is never
    # read and never simulated, so a book for it would be a book nothing looks at.
    # The gap is what makes T2's book -- and only T2's -- unreadable for the rest of the test.
    _gap(db_session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    shapes = _mixed_shapes(t0)
    # One row per shape: the two open ones cannot be duplicated on their ticker (`uq_open_order`
    # is one resting order per venue/ticker/side/variant), and a shape's second copy would
    # prove nothing its first does not -- the statement-count case below is where the population
    # size varies.
    n = len(shapes)

    ids = seed_pending(db_session, t0, n, shapes)
    per_row, per_row_writes, per_row_statements, per_row_walks, _ = run_pending_steps(
        env_settings, db_session, t0, ids, shapes, batched=False)
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, n, shapes)
    set_based, set_based_writes, set_based_statements, set_based_walks, _ = run_pending_steps(
        env_settings, db_session, t0, ids, shapes, batched=True)

    assert len(set_based) == n
    assert set_based == per_row
    # Round 1, M5: and the same rows in the three tables beside `orders`, so a fill one path
    # inserted and the other did not could not hide behind `nw_filled_contracts` alone.
    assert set_based_writes == per_row_writes
    # Not a vacuous comparison: the measured loop really did write to two of the three. The
    # ledger is the third and is empty by construction -- `_persist_track` passes
    # `ledger=False` for the counterfactual, and no watched track filled on a dirty market --
    # which is itself the invariant this pins.
    assert len(set_based_writes["fills"]) == 1
    assert {row["fill_method"] for row in set_based_writes["fills"]} == {"no_watcher"}
    assert [row["kind"] for row in set_based_writes["order_events"]] == ["expire"]
    assert set_based_writes["ledger"] == []
    # The identity is between two different paths, not between one path and itself.
    assert set_based_statements < per_row_statements
    # Round 1, M2: the pre-check decides the skip from a real simulation, and the row it does
    # not skip is handed that same simulation rather than made to repeat it -- so the fix walks
    # the tape exactly as often as the path it replaces, not once more per clean pending row.
    assert set_based_walks == per_row_walks

    row = dict(enumerate(set_based))
    assert row[FAR]["nw_dirty_seconds"] == 30         # an hour out: the whole period, twice
    assert row[INSIDE]["nw_dirty_seconds"] == 7       # 7 s left, then none
    assert row[FRACTION]["nw_dirty_seconds"] == 15    # 15.5 s truncates to 15, then none
    assert row[SUBSECOND]["nw_dirty_seconds"] is None  # 0.4 s truncates to 0: never written
    assert row[NO_EXPIRY]["nw_dirty_seconds"] == 30   # no expiry: no interval to clamp to
    assert row[PAST]["nw_dirty_seconds"] is None      # already expired: nothing to accrue
    # The close is the same batch's other statement, on the same predicate as the per-row one.
    assert [row[i]["nw_done"] for i in (FAR, INSIDE, FRACTION, SUBSECOND, NO_EXPIRY, PAST)] == [
        False, True, False, True, False, True]
    # Four clean-ticker rows the fix must not skip, each because one column would move: the
    # expiry that falls between the two steps closes the track, the stamp set back to an older
    # build is re-stamped, the tape that moved fills, and the row that never had a book anchors.
    assert row[EXPIRES_BETWEEN]["nw_done"] is True
    assert row[STALE_VERSION]["nw_executor_version"] == Decimal(EXECUTOR_VERSION)
    assert row[TAPE_MOVES]["nw_filled_contracts"] == Decimal("10.00")
    assert row[NO_BOOK]["queue_ahead_at_place"] == Decimal("40.00")
    # ... and the three beside them that it does skip, which moved nothing. The third carries
    # a written ledger, so the document it comes out with is the one it went in with, and the
    # three derived sums beside it are that document's own (round 1, M4).
    assert row[QUIET_FAR]["nw_filled_contracts"] == Decimal("0.00")
    assert row[QUIET_FAR]["nw_done"] is False
    assert row[QUIET_NONE]["nw_done"] is False
    assert row[FAR]["nw_executor_version"] == Decimal(EXECUTOR_VERSION)
    assert row[QUIET_LEDGER]["nw_recon_state"] == _written_ledger(t0)
    assert row[QUIET_LEDGER]["nw_print_unmatched"] == Decimal("1.50")
    assert row[QUIET_LEDGER]["nw_pending_unmatched"] == Decimal("3.00")
    assert row[QUIET_LEDGER]["nw_pending_surplus"] == Decimal("2.00")
    # Fix 78b: the three rows whose columns moved without an insert were written by the batched
    # `UPDATE ... FROM (VALUES ...)`, and `select *` above has already compared every column of
    # them against the per-row path. Named here so the case fails on the row it is about: the
    # ledger row carries a non-empty JSONB document through the VALUES list and comes out with
    # the document it went in with (nothing on T3 moved, so nothing aged), its three derived
    # sums beside it, and its track closed at the expiry that fell between the two loops.
    assert row[LEDGER_CLOSES]["nw_done"] is True
    assert row[LEDGER_CLOSES]["nw_recon_state"] == _written_ledger(t0)
    assert row[LEDGER_CLOSES]["nw_print_unmatched"] == Decimal("1.50")
    assert row[LEDGER_CLOSES]["nw_pending_unmatched"] == Decimal("3.00")
    assert row[LEDGER_CLOSES]["nw_pending_surplus"] == Decimal("2.00")
    assert row[LEDGER_CLOSES]["nw_executor_version"] == Decimal(EXECUTOR_VERSION)
    # A NULL in the dict writes NULL through the VALUES list rather than a zero or a text cast
    # failure. `nw_traded_at_price` is NULL for every one of them (a post-boundary order never
    # had a C0 quantity, ruling CR-3) and the print watermark is NULL for the two with no
    # ledger, because no print has ever reached T3; the ledger row's watermark is its own
    # document's floor, written back beside the document in the same batched row.
    for i in BATCHED_MOVES:
        assert row[i]["nw_traded_at_price"] is None
        assert row[i]["nw_last_print_ids"] == []
    assert row[EXPIRES_BETWEEN]["nw_last_print_ts"] is None
    assert row[STALE_VERSION]["nw_last_print_ts"] is None
    assert (row[LEDGER_CLOSES]["nw_last_print_ts"].isoformat()
            == _written_ledger(t0)["print_floor"])
    # The three populations the ruling keeps off this fix (round 1, M4). Both open orders took
    # the per-row path and accrued on the *watched* column -- 15 s a loop for the one that held
    # on the dirty market, 5 s for the one whose expiry was 5 s away and which the second loop
    # expired -- and the deferred row sat both loops out exactly as it went in, its
    # counterfactual neither accrued, closed nor stamped (ruling CR-4).
    assert row[OPEN_HELD]["status"] == "open"
    assert row[OPEN_HELD]["dirty_seconds"] == 30
    assert row[OPEN_HELD]["nw_dirty_seconds"] == 30
    assert row[OPEN_EXPIRES]["status"] == "expired"
    assert row[OPEN_EXPIRES]["dirty_seconds"] == 5
    assert row[OPEN_EXPIRES]["nw_done"] is True
    assert row[DEFERRED]["nw_done"] is False
    assert row[DEFERRED]["nw_dirty_seconds"] is None
    assert row[DEFERRED]["nw_executor_version"] is None
    assert row[DEFERRED]["nw_attempts"] == 2


def _pending_statements(env_settings, session, t0, n, shapes) -> int:
    """Statements the second loop over `n` seeded rows actually sends."""
    reset_pending(session)
    seed_pending(session, t0, n, shapes)
    clock = Clock(t0)
    executor = make_executor(env_settings, session, clock)
    executor.step()
    refresh(session)
    clock.advance(15)
    with capture_sql(session) as seen:
        executor.step()
    refresh(session)
    return len(seen)


def test_the_pending_population_costs_a_flat_number_of_statements(env_settings, db_session,
                                                                  world):
    """Fix 78 acceptance (b): the cost of a loop does not scale with the backlog.

    Ten times the population, the same statements. Before the fix each row cost its own
    savepoint, its own UPDATE and its own savepoint release, so the two sizes differed by about
    3 x 45 statements; the dirty population now costs two statements however many rows are in
    it, and a clean row with nothing past its cursors costs none.

    Only the pending population is seeded here, so there is no open order and no per-open-order
    statement to allow for; `reset_pending` puts the interval tables and the heartbeat back so
    that the two measurements differ in nothing but `n`.
    """
    t0 = NOW + timedelta(seconds=10)
    snapshot = _pending_book(db_session, T2, NOW - timedelta(seconds=5), sid=2)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _gap(db_session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    keep_only(db_session, set())
    db_session.commit()

    dirty = [_pending_statements(env_settings, db_session, t0, n, _dirty_shapes(t0))
             for n in (5, 50)]
    quiet = [_pending_statements(env_settings, db_session, t0, n, _quiet_shapes(t0))
             for n in (5, 50)]
    # Fix 78b: and the population whose columns *do* move, which cost one savepoint and one
    # UPDATE each until this part batched them.
    moving = [_pending_statements(env_settings, db_session, t0, n, _moving_shapes(t0))
              for n in (5, 50)]

    assert dirty[0] == dirty[1], f"dirty population: {dirty}"
    assert quiet[0] == quiet[1], f"clean no-change population: {quiet}"
    assert moving[0] == moving[1], f"clean cursor-advance population: {moving}"


def test_the_batched_clamp_equals_the_per_row_clamp_for_every_row_shape(db_session):
    """Fix 78 acceptance: the set-based accrual is `_clamped(period, row, now, watched=False)`.

    One row per shape the clamp can meet -- no expiry at all, long past, a fraction of a second
    past, exactly now, fractions on either side of the period's own boundary, the period
    exactly, an hour out and the year 9999 -- and the column each one ends with is compared
    against the Python function the per-row path calls, computed here from the same row and the
    same instant. A clamp of zero is not a write at all, so its column stays NULL rather than
    becoming 0, which is `add_dirty_seconds`'s early return.

    The year 9999 row is the cast's own case: `extract(epoch from ...)` on that interval is
    about 2.5e11 seconds, which is why the `::int` cast is applied after the period caps it.
    The row with a `cancelled_at` inside the period is the other track's case: the
    counterfactual's interval is `[placed_at, expiry]` and knows nothing about when the order
    we placed was cancelled (§0.10), so its clamp must ignore it.
    """
    now = NOW + timedelta(seconds=10)
    period = 15
    offsets = [None, timedelta(days=-365), timedelta(seconds=-15.5), timedelta(milliseconds=-1),
               timedelta(0), timedelta(milliseconds=400), timedelta(seconds=1),
               timedelta(seconds=7), timedelta(seconds=14, milliseconds=999),
               timedelta(seconds=15), timedelta(seconds=15, milliseconds=500),
               timedelta(seconds=16), timedelta(hours=1)]
    rows = [_pending_order(db_session, i, now=now, ticker=T2, vm=VM2,
                           expiry=None if offset is None else now + offset)
            for i, offset in enumerate(offsets)]
    # The far-future shape, and one whose watched interval closed inside the period.
    rows.append(_pending_order(db_session, len(offsets), now=now, ticker=T2, vm=VM2,
                               expiry=datetime(9999, 1, 1, tzinfo=timezone.utc)))
    rows.append(_pending_order(db_session, len(offsets) + 1, now=now, ticker=T2, vm=VM2,
                               expiry=now + timedelta(hours=1)))
    rows[-1].cancelled_at = now + timedelta(seconds=3)
    db_session.commit()
    expected = {row.id: (_clamped(period, row, now, watched=False) or None) for row in rows}

    store.add_nw_dirty_seconds_batch(db_session, [row.id for row in rows], period, now)
    db_session.commit()
    db_session.expire_all()

    assert {row.id: row.nw_dirty_seconds
            for row in db_session.query(Order).order_by(Order.id).all()} == expected
    # Computed here, not read off the code: 1 s, 7 s and 14.999 s left clamp to 1, 7 and 14,
    # everything from the period's own boundary outwards to 15, and everything at or before
    # `now` to nothing at all.
    assert sorted(set(expected.values()), key=lambda v: (v is None, v)) == [1, 7, 14, 15, None]


# --- fix 78b: the cursor-advance writes, batched, and the loop's phase tape ---------------
#
# The residual after fix 78's first part (journal 244): median loop 7.6 s, p95 13,850 ms at
# 6,387 pending tracks. The user's ruling of 16:57 CT (packet item 17, option d) batches the
# clean-market cursor-advance writes as well and publishes where the rest of a loop's time
# goes. The cases below are that acceptance: the same columns as `store.update_order` writes
# for the same dict, a statement count flat in the population (above), and the phase metrics.


def _nw_write(*, filled=Decimal("0.00"), done=False, queue=None, traded=None, cursor=None,
              crossed=False, print_unmatched=None, pending_unmatched=None,
              pending_surplus=None, cancels=None, recon=None, print_ts=None, print_ids=None,
              worst_case=None) -> dict:
    """One counterfactual step's write, spelled out rather than taken from `_nw_columns`.

    The keys are `orders`' own counterfactual columns, so this case compares the two writers
    rather than one writer with itself: a change to `_nw_columns` cannot make both sides of the
    comparison move together. `worst_case_fill` is in the dict only for a track that has
    crossed, which is what makes the batched writer meet a second column set.
    """
    values = {"nw_filled_contracts": filled, "nw_done": done,
              "nw_queue_remaining": queue, "nw_traded_at_price": traded,
              "nw_tape_cursor_event_id": cursor, "nw_crossed": crossed,
              "nw_print_unmatched": print_unmatched,
              "nw_pending_unmatched": pending_unmatched,
              "nw_pending_surplus": pending_surplus, "nw_cancels_ahead": cancels,
              "nw_recon_state": recon, "nw_last_print_ts": print_ts,
              "nw_last_print_ids": [] if print_ids is None else print_ids}
    if worst_case is not None:
        values["worst_case_fill"] = worst_case
    return values


def test_the_values_list_update_writes_exactly_what_update_order_writes(db_session):
    """Fix 78b acceptance: the batched writer is `store.update_order`, row for row.

    Every shape the batch can meet is written twice -- once through the per-row statement this
    replaces, once through the `UPDATE ... FROM (VALUES ...)` -- on two rows seeded identically,
    and the two rows are then compared column for column (`select *`, so nothing is compared by
    enumeration). The shapes are chosen for the way a `VALUES` list is typed:

    * every nullable column NULL, which is the case a bare parameter cannot survive -- a column
      whose every row is NULL resolves to `text` in a `VALUES` list and fails against a
      `numeric`, `jsonb` or `timestamptz` column, so this row is the cast's own test;
    * a non-empty JSONB document beside real `numeric` sums, a `bigint` cursor, a
      `timestamptz` with microseconds and both booleans set;
    * a third row that also writes `worst_case_fill`, which is a *different column set* and so
      a second statement -- the grouping's own case.

    `nw_executor_version` is written by neither dict: both writers add it themselves through
    `stamp_nw_writer`, and the comparison would fail if only one of them did.
    """
    now = NOW + timedelta(seconds=10)
    ledger = _written_ledger(now)
    shapes = [
        _nw_write(),
        _nw_write(filled=Decimal("3.50"), done=True, queue=Decimal("12.25"),
                  traded=Decimal("4.75"), cursor=987_654_321, crossed=True,
                  print_unmatched=Decimal("1.50"), pending_unmatched=Decimal("3.00"),
                  pending_surplus=Decimal("2.00"), cancels=Decimal("0.00"), recon=ledger,
                  print_ts=now - timedelta(seconds=7, microseconds=123_456),
                  print_ids=["a", "b"]),
        _nw_write(done=True, recon=ledger, worst_case=True),
    ]
    pairs = []
    for i, values in enumerate(shapes):
        per_row = _pending_order(db_session, 2 * i, now=now, ticker=T2, vm=VM2,
                                 expiry=now + timedelta(hours=1))
        batched = _pending_order(db_session, 2 * i + 1, now=now, ticker=T2, vm=VM2,
                                 expiry=now + timedelta(hours=1))
        pairs.append((per_row.id, batched.id, values))
    db_session.commit()

    for per_row_id, _, values in pairs:
        store.update_order(db_session, per_row_id, values)
    statements = store.update_orders_batch(
        db_session, [(batched_id, values) for _, batched_id, values in pairs])
    db_session.commit()

    # Two column sets among three rows, so two statements however many rows there are.
    assert statements == 2
    rows = {row["id"]: row for row in
            pending_columns(db_session, [i for pair in pairs for i in pair[:2]])}
    skip = ("id", "client_order_id", "intent_id")
    for per_row_id, batched_id, _ in pairs:
        left = {k: v for k, v in rows[per_row_id].items() if k not in skip}
        right = {k: v for k, v in rows[batched_id].items() if k not in skip}
        assert left == right
    # Not a vacuous comparison: the values really landed, NULLs included.
    written = rows[pairs[1][1]]
    assert written["nw_recon_state"] == ledger
    assert written["nw_pending_unmatched"] == Decimal("3.00")
    assert written["nw_tape_cursor_event_id"] == 987_654_321
    assert written["nw_last_print_ts"] == now - timedelta(seconds=7, microseconds=123_456)
    assert written["nw_executor_version"] == Decimal(EXECUTOR_VERSION)
    empty = rows[pairs[0][1]]
    assert empty["nw_queue_remaining"] is None and empty["nw_recon_state"] is None
    assert empty["nw_last_print_ts"] is None and empty["nw_traded_at_price"] is None
    assert rows[pairs[2][1]]["worst_case_fill"] is True
    assert rows[pairs[0][1]]["worst_case_fill"] is False
    # The count is one statement per column set per `chunk` rows, which is what makes the
    # population's cost flat in N below `VALUES_BATCH_ROWS` and `ceil(N / 500)` above it. The
    # loop case above only ever meets N under the chunk, so the arithmetic is pinned here: the
    # same three rows (two column sets, two of them sharing one) sent a row at a time are three
    # statements, not one and not three per column set. Rewriting the same values to the same
    # rows changes nothing that was compared above.
    assert store.update_orders_batch(
        db_session, [(batched_id, values) for _, batched_id, values in pairs], chunk=1) == 3


def _param_ids(parameters) -> set:
    """Every integer bound into one captured statement, id arrays flattened.

    An order id can reach a statement as a scalar (`where orders.id = %(id)s`), inside an array
    (the dirty batch's `= any(:ids)`) or as one cell of a `VALUES` row, so all three shapes are
    walked. Booleans are not ids and `Decimal` columns are not integers, so neither can be
    mistaken for one.
    """
    found: set = set()

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item)
        elif isinstance(value, int) and not isinstance(value, bool):
            found.add(value)

    walk(parameters)
    return found


def _orders_writes(seen):
    """The statements of one loop that wrote `orders`, split by which writer sent them."""
    per_row, id_lists, values_lists = [], [], []
    for statement, parameters in seen:
        flat = " ".join(statement.split()).lower()
        if not flat.startswith("update orders"):
            continue
        if "from (values" in flat:
            values_lists.append((flat, parameters))
        elif "= any(" in flat:
            id_lists.append((flat, parameters))
        else:
            per_row.append((flat, parameters))
    return per_row, id_lists, values_lists


def test_a_batched_pending_row_is_not_also_written_per_row(env_settings, db_session, world):
    """Fix 78b acceptance: one write per row per loop, and it is the batch's.

    The same mixed population as the identity case, run with the fix on, with every statement
    of the measured loop captured. The three rows whose columns move without an insert are
    bound into the one `UPDATE ... FROM (VALUES ...)` that loop sends, and their ids appear in
    no other `orders` write at all -- not in a per-row `update_order`, not in the dirty-market
    batch's id array. The row that fills and the row that has never had a book are in the
    per-row writes, where the ruling leaves them, and neither is in the VALUES list.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    snapshot = _pending_book(db_session, T2, NOW - timedelta(seconds=5), sid=2)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _pending_book(db_session, T4, NOW - timedelta(seconds=5), sid=4)
    _gap(db_session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    shapes = _mixed_shapes(t0)
    ids = seed_pending(db_session, t0, len(shapes), shapes)

    *_, seen = run_pending_steps(env_settings, db_session, t0, ids, shapes, batched=True)

    per_row, id_lists, values_lists = _orders_writes(seen)
    # One VALUES list for the whole moving population: those three rows write the same columns
    # (none of them crossed), so they are one column set and one statement.
    assert len(values_lists) == 1
    bound = _param_ids(values_lists[0][1])
    elsewhere = {value for _, parameters in per_row + id_lists
                 for value in _param_ids(parameters)}
    for shape in BATCHED_MOVES:
        assert ids[shape] in bound, shape
        assert ids[shape] not in elsewhere, shape
    # ... and the rows the ruling keeps per-row are written per-row and are not in the batch:
    # the one whose late print filled it, which inserts a fill, and the two open orders, which
    # are still resting and accrue on the watched column.
    for shape in (TAPE_MOVES, OPEN_HELD, OPEN_EXPIRES):
        assert ids[shape] not in bound, shape
        assert ids[shape] in elsewhere, shape
    # The rows this loop owes nothing are in no write at all, not even a no-op one: the three
    # quiet ones, the deferred ticker that sat the loop out (ruling CR-4), and the row that had
    # never had a book, which anchored on the first loop and has moved nothing since.
    for shape in (QUIET_FAR, QUIET_NONE, QUIET_LEDGER, NO_BOOK, DEFERRED):
        assert ids[shape] not in bound and ids[shape] not in elsewhere, shape


def _ticking(step=0.001):
    """A monotonic clock that advances by `step` on every reading, so a phase whose start and
    end are read either side of real work measures `step` rather than zero."""
    ticks = {"t": 0.0}

    def mono():
        ticks["t"] += step
        return ticks["t"]

    return mono


def test_the_loop_publishes_the_phase_its_time_went_to(env_settings, db_session, world):
    """Fix 78b's second half: `exec.phase_*` and `exec.per_row_n`, once per written batch.

    Each is a gauge of the loop that wrote the batch, exactly as `exec.loop_ms` is -- the
    sampler writes one batch every `metric_sample_s`, and the first call of a new executor is
    always due, so this single step both measures and publishes. Each name appears once, with a
    value that is a real measurement rather than a sum over the sampling window.

    The population is two clean-ticker rows the loop writes set-based, one that has never had a
    book -- which anchors, and since fix 78c is batched with them rather than taking a savepoint
    -- and one **open** order, which the user's ruling keeps on the per-row path on every loop:
    so `exec.per_row_n` is exactly 1 and is a measurement of the path taken, not a row count.
    The executor's monotonic clock is a ticking one, so each phase's two readings differ and the
    published milliseconds are positive; with the suite's usual frozen clock they would all be a
    legitimate zero, which would not prove the timers are wired to anything.
    """
    keep_only(db_session, set())
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    t0 = NOW + timedelta(seconds=10)
    reset_pending(db_session)
    shapes = _quiet_shapes(t0) + [
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1), "queue": None},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1), "status": "open"}]
    seed_pending(db_session, t0, len(shapes), shapes)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    executor = Executor(env_settings, factory, clock=lambda: t0, monotonic=_ticking(),
                        variants=["tiny"])

    stats = executor.step()
    refresh(db_session)

    names = [row.name for row in db_session.query(MetricSample).filter_by(source="exec").all()]
    samples = {row.name: row.value
               for row in db_session.query(MetricSample).filter_by(source="exec").all()}
    phases = ("exec.phase_tape_ms", "exec.phase_walk_ms", "exec.phase_batch_ms",
              "exec.phase_per_row_ms", "exec.per_row_n")
    for name in phases:
        assert names.count(name) == 1, name
        assert samples[name] is not None and samples[name] >= 0, name
    # The published value is the step's own reading, not a running total of the sampling window.
    assert samples["exec.per_row_n"] == stats.per_row_n == 1
    assert float(samples["exec.phase_tape_ms"]) == pytest.approx(stats.phase_tape_ms, abs=1e-3)
    for name in phases[:4]:
        assert samples[name] > 0, name
    # Every phase of the loop is inside the loop it was measured in.
    assert sum(float(samples[name]) for name in phases[:4]) <= stats.loop_ms + 1


# --- fix 82: additive phase timers and counts, no behaviour change -----------------------
#
# The user's ruling of 2026-09-17 (journal 262): a timers-only release of additive metrics --
# row 82's placement timer plus load, books, decide, intake, samples, previous-commit timers
# and tape/working/expiring counts -- measurement only, no behaviour change. The cases below
# are that acceptance: every name arrives once on a live loop and never on a replay, the six
# new phases plus the fix 78b four come within a tight tolerance of `exec.loop_ms`,
# `exec.phase_commit_prev_ms` is genuinely the *previous* loop's own commit block, the counts
# are the fixture's known values rather than a guess read off the code, and the diff changes no
# statement and no stored value at all.

FIX82_PHASES = ("exec.phase_intake_ms", "exec.phase_load_ms", "exec.phase_books_ms",
                "exec.phase_decide_ms", "exec.phase_place_ms", "exec.phase_samples_ms")
FIX82_COMMIT_PREV = "exec.phase_commit_prev_ms"
FIX82_COUNTS = ("exec.working_rows", "exec.tape_tickers_n", "exec.tape_print_rows",
                "exec.tape_delta_rows", "exec.expiring_n")
#: Fix round 1 (controller finding, before commit): the identity case below must compare fix
#: 82 against the commit its diff was written on top of, never against `HEAD` -- the
#: controller commits this diff right after review, and from that moment `HEAD` *is* the fix,
#: so a base pinned to `HEAD` would compare the fix with itself and pass vacuously. Pinned by
#: hash rather than re-derived, so a rebase or a later commit on this branch cannot move it.
FIX82_BASE = "bf51d01"
#: Set to run the one-time identity case; see its own docstring and skip reason.
FIX82_IDENTITY_ENV = "FIX82_IDENTITY"


def _fix82_fixture(session, t0):
    """Two clean T3 rows, one that has never had a book and one open order -- the same
    population `test_the_loop_publishes_the_phase_its_time_went_to` uses, because it already
    exercises `_tape`, `_advance_books`, `plan_actions` and `_apply` in one step."""
    reset_pending(session)
    shapes = _quiet_shapes(t0) + [
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1), "queue": None},
        {"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1), "status": "open"}]
    seed_pending(session, t0, len(shapes), shapes)


def test_fix82_phase_and_count_names_are_published_once_live_never_replay(
        env_settings, db_session, world):
    """Fix 82 acceptance (a): every new name arrives once on a live loop, with a non-negative
    value, and none of them on a replay -- which never reaches `_write_metric_batch` at all
    (`loop.py`'s `_locked_step`), exactly as the fix 78b four do not."""
    keep_only(db_session, set())
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    t0 = NOW + timedelta(seconds=10)
    _fix82_fixture(db_session, t0)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    executor = Executor(env_settings, factory, clock=lambda: t0, monotonic=_ticking(),
                        variants=["tiny"])

    stats = executor.step()
    refresh(db_session)

    names = [row.name for row in db_session.query(MetricSample).filter_by(source="exec").all()]
    samples = {row.name: row.value
              for row in db_session.query(MetricSample).filter_by(source="exec").all()}
    every_new_name = FIX82_PHASES + (FIX82_COMMIT_PREV,) + FIX82_COUNTS
    for name in every_new_name:
        assert names.count(name) == 1, name
        assert samples[name] is not None and samples[name] >= 0, name
    # A fresh executor's first loop has no previous commit block to report.
    assert samples[FIX82_COMMIT_PREV] == 0
    assert stats.phase_commit_prev_ms == 0
    assert executor._last_commit_ms >= 0
    before_count = len(names)

    replay_factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    replay = Executor(env_settings, replay_factory, clock=lambda: NOW, monotonic=_ticking(),
                      replay=True, variants=["tiny"])
    replay.step()
    refresh(db_session)
    assert db_session.query(MetricSample).filter_by(source="exec").count() == before_count
    for name in every_new_name:
        assert db_session.query(MetricSample).filter_by(
            source="exec", name=name).count() == 1, name


def test_fix82_phases_sum_within_tolerance_of_loop_ms(env_settings, db_session, world):
    """Fix 82 acceptance (b): the six new phases plus the fix 78b four come within a small,
    exact tolerance of `exec.loop_ms` on each of two loops.

    The monotonic clock auto-ticks on every read (`_ticking()`, shared across both loops, so it
    never resets) and the wall clock is held fixed for the duration of each step and then moved
    forward by the exec period, exactly as `_moving_run` does. What is left over -- `loop_ms`
    minus the ten timed phases -- is the handful of `_monotonic()`-free statements between the
    named boundaries (`resolve_variants`, the startup check, `gateway.observe_tape`,
    `dead_recorder`, the `fair_age_s` extend): a few ticks at 1 ms a call, never tens of
    milliseconds, so a genuinely unwired phase would fail this by an order of magnitude.
    """
    keep_only(db_session, set())
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    t0 = NOW + timedelta(seconds=10)
    _fix82_fixture(db_session, t0)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    now_box = {"t": t0}
    executor = Executor(env_settings, factory, clock=lambda: now_box["t"], monotonic=_ticking(),
                        variants=["tiny"])
    all_ms = ("exec.phase_tape_ms", "exec.phase_walk_ms", "exec.phase_batch_ms",
             "exec.phase_per_row_ms") + FIX82_PHASES

    for _ in range(2):
        stats = executor.step()
        refresh(db_session)
        samples = {row.name: row.value for row in db_session.query(MetricSample)
                  .filter_by(source="exec", ts=now_box["t"]).all()}
        total = sum(float(samples[name]) for name in all_ms)
        # Every phase is inside the loop it was measured in (as the fix 78b test checks)...
        assert total <= stats.loop_ms + 1
        # ...and accounts for essentially all of it: the untimed gaps are a few `_monotonic()`
        # calls' worth, never the whole residual the fix exists to attribute.
        assert stats.loop_ms - total < 20, (stats.loop_ms, total)
        now_box["t"] = now_box["t"] + timedelta(seconds=15)
        executor._metric_sampler.forget("metrics")


def test_fix82_phase_commit_prev_ms_is_the_previous_loops_commit_block(
        env_settings, db_session, world):
    """Fix 82 acceptance (c): `exec.phase_commit_prev_ms` on loop N is loop N-1's own
    `count_open_orders` + metric batch + `write_heartbeat` + `session.commit()` duration --
    held on the executor because a loop cannot measure a block that is what publishes the
    measurement -- and it is exactly 0 on an executor's first loop.
    """
    keep_only(db_session, set())
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    t0 = NOW + timedelta(seconds=10)
    _fix82_fixture(db_session, t0)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    now_box = {"t": t0}
    executor = Executor(env_settings, factory, clock=lambda: now_box["t"], monotonic=_ticking(),
                        variants=["tiny"])

    stats1 = executor.step()
    refresh(db_session)
    samples1 = {row.name: row.value for row in db_session.query(MetricSample)
               .filter_by(source="exec", ts=now_box["t"]).all()}
    assert samples1[FIX82_COMMIT_PREV] == 0
    assert stats1.phase_commit_prev_ms == 0
    loop1_commit_ms = executor._last_commit_ms
    # The ticking clock again: a block that ran a real statement and a real commit takes a
    # real (fake) tick, so a `_last_commit_ms` of exactly 0 here would mean it was never timed.
    assert loop1_commit_ms > 0

    now_box["t"] = now_box["t"] + timedelta(seconds=15)
    executor._metric_sampler.forget("metrics")
    stats2 = executor.step()
    refresh(db_session)
    samples2 = {row.name: row.value for row in db_session.query(MetricSample)
               .filter_by(source="exec", ts=now_box["t"]).all()}
    assert float(samples2[FIX82_COMMIT_PREV]) == pytest.approx(loop1_commit_ms, abs=1e-3)
    assert float(samples2[FIX82_COMMIT_PREV]) == pytest.approx(
        stats2.phase_commit_prev_ms, abs=1e-3)


def test_fix82_counts_match_the_seeded_tape(env_settings, db_session, world):
    """Fix 82 acceptance (d): `working_rows`, `tape_tickers_n`, `tape_print_rows`,
    `tape_delta_rows` and `expiring_n` are the fixture's own known values.

    Three pending counterfactuals on one ticker (T3, none open, none deferred, none carrying a
    queue's cursor), so `_tape` reads exactly one ticker's window from `placed_at - 60 s`: two
    prints and three deltas inside it, and one row whose expiry has already fallen at `t0`.
    `world`'s own T2/VM2 candidate places nothing until this same step's `_apply`, so
    `working_orders` at the point `_body` reads it is exactly the three seeded rows.
    """
    keep_only(db_session, {VM2})
    _book2(db_session, NOW - timedelta(seconds=5))
    t0 = NOW + timedelta(seconds=10)
    _pending_book(db_session, T3, t0 - timedelta(seconds=5), sid=3)
    reset_pending(db_session)
    _pending_order(db_session, 0, now=t0, ticker=T3, vm=VM3, expiry=t0)
    _pending_order(db_session, 1, now=t0, ticker=T3, vm=VM3, expiry=t0 + timedelta(hours=1))
    _pending_order(db_session, 2, now=t0, ticker=T3, vm=VM3, expiry=t0 + timedelta(hours=1))
    _print(db_session, T3, t0 - timedelta(seconds=30), "0.35", "10")
    _print(db_session, T3, t0 - timedelta(seconds=20), "0.35", "5")
    _delta(db_session, T3, t0 - timedelta(seconds=40), "yes", "0.35", "5", seq=2, sid=3)
    _delta(db_session, T3, t0 - timedelta(seconds=35), "yes", "0.35", "-2", seq=3, sid=3)
    _delta(db_session, T3, t0 - timedelta(seconds=20), "no", "0.48", "3", seq=4, sid=3)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    executor = Executor(env_settings, factory, clock=lambda: t0, monotonic=_ticking(),
                        variants=["tiny"])

    stats = executor.step()
    refresh(db_session)

    samples = {row.name: row.value
              for row in db_session.query(MetricSample).filter_by(source="exec").all()}
    assert stats.working_rows == 3
    assert stats.tape_tickers_n == 1
    assert stats.tape_print_rows == 2
    assert stats.tape_delta_rows == 3
    assert stats.expiring_n == 1
    assert samples["exec.working_rows"] == 3
    assert samples["exec.tape_tickers_n"] == 1
    assert samples["exec.tape_print_rows"] == 2
    assert samples["exec.tape_delta_rows"] == 3
    assert samples["exec.expiring_n"] == 1


def _pre_fix82_executor_cls():
    """The `Executor` class exactly as commit `FIX82_BASE` (bf51d01, the commit fix 82's own
    diff was written on top of) defines it -- i.e. without fix 82 -- loaded straight from that
    commit's blob, never from `HEAD`: `HEAD` is only bf51d01 until the controller commits this
    diff, after which it *is* the fix, and comparing the fix with `HEAD` would then compare it
    with itself (fix round 1, controller finding, before commit).

    Raises `RuntimeError` if git is not on `PATH` or `FIX82_BASE` is not a commit this
    checkout can read (a shallow clone, a mirror without this branch's history, or simply no
    git at all); the caller turns that into a skip rather than an error, since none of those
    are this test's own business to diagnose.
    """
    root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "show", f"{FIX82_BASE}:harness/execution/loop.py"], cwd=root,
            capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"git is not available: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"git show {FIX82_BASE}:harness/execution/loop.py failed (commit unreachable in "
            f"this checkout?): {result.stderr.strip() or result.returncode}")
    spec = importlib.util.spec_from_loader("tests._loop_pre_fix82", loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        exec(compile(result.stdout, f"<{FIX82_BASE}:harness/execution/loop.py>", "exec"),
             module.__dict__)
    finally:
        sys.modules.pop(spec.name, None)
    return module.Executor


def _normalize_params(value):
    """Bound parameters, with any `psycopg.types.json.Jsonb` wrapper unwrapped to its plain
    object: `Jsonb` defines no `__eq__` (`Jsonb({"x": 1}) != Jsonb({"x": 1})`, by identity), so
    a raw `==` of two captured parameter dicts would fail on a JSONB column even when the
    document is byte for byte the same one -- the failure fix 78b's own `_param_ids` sidesteps
    by comparing only the ids it needs rather than a whole parameter dict.
    """
    if isinstance(value, Jsonb):
        return value.obj
    if isinstance(value, dict):
        return {k: _normalize_params(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_params(v) for v in value]
    return value


def _non_metric_statements(seen):
    return [(" ".join(q.split()), _normalize_params(p))
            for q, p in seen if "metric_samples" not in q.lower()]


@pytest.mark.skipif(
    os.environ.get(FIX82_IDENTITY_ENV) != "1",
    reason=(f"one-time fix 82 identity check against {FIX82_BASE}, not a regression test "
            f"(fix round 1): run explicitly with {FIX82_IDENTITY_ENV}=1 make test "
            f"TEST_ARGS='tests/test_exec_loop.py::"
            f"test_fix82_changes_no_statement_and_no_stored_value'"))
def test_fix82_changes_no_statement_and_no_stored_value(env_settings, db_session, world,
                                                         monkeypatch):
    """One-time before/after identity evidence for fix 82 against `FIX82_BASE` (bf51d01) --
    not part of the regular suite.

    Fix 78b's own mixed population, run twice through `run_pending_steps` -- once through this
    worktree's `Executor` (fix 82's timers on) and once through the `Executor` commit
    `FIX82_BASE` defines (fix 82 absent, loaded from that commit's own blob, never from `HEAD`:
    see `_pre_fix82_executor_cls`) -- and compared exactly as `run_pending_steps`'s own two
    paths are: `orders` column for column, `fills`/`ledger`/`order_events` row for row, and the
    measured loop's full captured statement list with every `metric_samples` statement set
    aside. Everything else -- text and bound parameters both, in order -- must be identical,
    and the only statements fix 82 is allowed to add are its own extra `metric_samples` rows.

    Skipped unless `FIX82_IDENTITY=1` is set (see the marker above) and skipped with its own
    reason if git or `FIX82_BASE` is unavailable in this checkout (`_pre_fix82_executor_cls`).
    Meant to be run once by the implementer and once by the reviewer, with the variable set, as
    the before/after evidence for this diff -- not on every suite run, and not indefinitely: it
    is expected to be retired, or to go legitimately stale and need a new base, the next time
    `loop.py`'s own statements change (fix 79's print-cache rescan touches `_tape` directly).
    """
    try:
        old_cls = _pre_fix82_executor_cls()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    keep_only(db_session, set())
    # The metric batch is due every loop rather than once every `metric_sample_s` (default
    # 60 s, longer than the 15 s the measured loop's own clock advances by), so the one
    # statement fix 82 is allowed to add actually appears on the measured step in both runs.
    env_settings.metric_sample_s = 1
    t0 = NOW + timedelta(seconds=10)
    snapshot = _pending_book(db_session, T2, NOW - timedelta(seconds=5), sid=2)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _pending_book(db_session, T4, NOW - timedelta(seconds=5), sid=4)
    _gap(db_session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    shapes = _mixed_shapes(t0)
    n = len(shapes)

    ids = seed_pending(db_session, t0, n, shapes)
    after_cols, after_writes, _, _, after_seen = run_pending_steps(
        env_settings, db_session, t0, ids, shapes, batched=True)

    monkeypatch.setattr(sys.modules[__name__], "Executor", old_cls)
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, n, shapes)
    before_cols, before_writes, _, _, before_seen = run_pending_steps(
        env_settings, db_session, t0, ids, shapes, batched=True)
    monkeypatch.undo()

    # No stored value moved: `orders` column for column, the other three writers row for row.
    assert after_cols == before_cols
    assert after_writes == before_writes

    before_rest = _non_metric_statements(before_seen)
    after_rest = _non_metric_statements(after_seen)
    assert [q for q, _ in after_rest] == [q for q, _ in before_rest]
    assert [p for _, p in after_rest] == [p for _, p in before_rest]
    # The metric batch is one `INSERT ... VALUES` statement regardless of row count
    # (`telemetry.record_many`), so fix 82's extra rows show up as more bound parameters on
    # that one statement, never as an extra statement in the list.
    before_metric = [(q, p) for q, p in before_seen if "metric_samples" in q.lower()]
    after_metric = [(q, p) for q, p in after_seen if "metric_samples" in q.lower()]
    assert len(before_metric) == len(after_metric) == 1
    assert before_metric[0][0].strip().lower().startswith("insert")
    assert after_metric[0][0].strip().lower().startswith("insert")

    def _names(params):
        return {v for k, v in params.items() if k.startswith("name_")}

    before_names = _names(before_metric[0][1])
    after_names = _names(after_metric[0][1])
    assert before_names <= after_names
    assert after_names - before_names == set(
        FIX82_PHASES + (FIX82_COMMIT_PREV,) + FIX82_COUNTS)


def _moving_run(env_settings, session, t0, shapes, *, break_batch):
    """Two loops over the moving population, the second with the cursor batch refusing or not.

    The second loop is the measured one: by then every row has been written once, and its own
    expiry has fallen, so the whole population is fix 78b's batched write.
    """
    reset_pending(session)
    ids = seed_pending(session, t0, len(shapes), shapes)
    clock = Clock(t0)
    executor = make_executor(env_settings, session, clock)
    executor.step()
    refresh(session)
    clock.advance(15)
    if break_batch:
        with patch("harness.execution.store.update_orders_batch",
                   side_effect=RuntimeError("batch refused")):
            stats = executor.step()
    else:
        stats = executor.step()
    refresh(session)
    return pending_columns(session, ids), stats


def test_a_failed_cursor_batch_counts_one_error_and_falls_back_to_the_per_row_path(
        env_settings, db_session, world):
    """Fix 78b: a batch that raises costs its savepoint and then today's per-row path.

    The same population is run twice, once with `store.update_orders_batch` raising on the
    measured loop. The rows come out column for column as they do when the batch works -- they
    fell back to their own savepoints and their own `update_order`, with the walk the pre-check
    had already made -- and the step counts exactly one error for the batch, not one per row,
    and names it on the heartbeat. A step that hit a database failure therefore never publishes
    `exec.errors = 0` because the fallback happened to succeed (the first part's M1, applied to
    the second batch).
    """
    keep_only(db_session, set())
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    t0 = NOW + timedelta(seconds=10)
    shapes = _moving_shapes(t0)

    healthy, healthy_stats = _moving_run(env_settings, db_session, t0, shapes,
                                         break_batch=False)
    fallback, fallback_stats = _moving_run(env_settings, db_session, t0, shapes,
                                           break_batch=True)

    assert len(healthy) == len(shapes) and healthy == fallback
    assert [row["nw_done"] for row in fallback] == [True] * len(shapes)
    assert healthy_stats.errors == 0
    assert fallback_stats.errors == 1
    assert "nw cursor batch: RuntimeError: batch refused" == fallback_stats.last_error


# --- fix 78c: the per-loop walk budget, the batched anchor rows, the per-cause counters ------


#: A budget no test loop can spend. Every case that is not about the budget itself runs with
#: this rather than with a second code path: part 1's class attribute stays the only way to the
#: old per-row path (fix 78c acceptance (g)).
UNBUDGETED_MS = 10 * 60 * 1000


def _budget_executor(env_settings, session, clock, budget_ms, *, monotonic=None, sample_s=None):
    """An executor whose walk budget is `budget_ms`, driven by a clock that actually moves.

    The suite's usual `Clock` hands one frozen monotonic reading to a whole step, so every row's
    walk measures zero and no budget is ever spent -- which is why the fix leaves every existing
    case exactly as it was. A case about the budget needs readings that differ, and `_ticking()`
    gives each one its own millisecond, so one walked row costs exactly one millisecond.
    """
    update = {"exec_nw_budget_ms": budget_ms}
    if sample_s is not None:
        update["metric_sample_s"] = sample_s
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    return Executor(env_settings.model_copy(update=update), factory,
                    clock=lambda: clock.now, monotonic=monotonic or (lambda: clock.mono),
                    variants=["tiny"])


def _rebudget(executor, env_settings, budget_ms):
    """The budget in force for the next step: `exec_nw_budget_ms` is read once per step, so a
    case changes it between two steps exactly as a settings change in production would."""
    executor.settings = env_settings.model_copy(update={"exec_nw_budget_ms": budget_ms})


def _clear_future_tape(session, t0):
    """Put the tape back to what both runs of a case start from: nothing stamped at or after t0.

    A live book is loaded and advanced from the head of the tape with no upper bound at all
    (`book.load_book` / `advance_book`; only a replay bounds them at its instant), so an event
    stamped for a later loop that is *already* in the table is folded into the book of the very
    first one. Every case below therefore stamps its tape loop by loop as the clock reaches it,
    and wipes what an earlier run of the same case stamped. `reset_pending` deliberately leaves
    the tape alone -- the pending cases share one seeded book -- so this is its counterpart for
    the cases that move a book between loops.
    """
    session.execute(text("delete from orderbook_events where ts >= :ts"), {"ts": t0})
    session.execute(text("delete from venue_trades where ts >= :ts"), {"ts": t0})
    # ... and the id the next event will take, so the second run of a case stamps the same tape
    # with the same ids as the first: `orders.nw_tape_cursor_event_id` and `fills.source_event_id`
    # are those ids, and a comparison of the two runs column for column would otherwise differ
    # in exactly the two columns that say the walk got where it was going.
    session.execute(text(
        "select setval(pg_get_serial_sequence('orderbook_events', 'id'), "
        "coalesce((select max(id) from orderbook_events), 1))"))
    session.commit()


def _budget_shapes(t0):
    """Five cancelled pending rows over three clean tickers, and two on a dirty one.

    The five are the budget's own population: the rotation reaches them a ticker at a time and
    defers the rest. The two on the dirty market are the population the ruling keeps out of the
    budget entirely -- their accrual and their close run on every loop whatever it says.
    """
    far = t0 + timedelta(hours=1)
    return [{"ticker": T3, "vm": VM3, "expiry": far},
            {"ticker": T3, "vm": VM3, "expiry": far},
            {"ticker": T4, "vm": VM4, "expiry": far},
            {"ticker": T4, "vm": VM4, "expiry": far},
            {"ticker": T5, "vm": VM5, "expiry": far},
            {"ticker": T2, "vm": VM2, "expiry": far},
            {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(seconds=40)}]


def _budget_world(session, t0):
    """The books every clean ticker starts from, and the gap that keeps T2 dirty throughout."""
    snapshot = _pending_book(session, T2, NOW - timedelta(seconds=5), sid=2)
    for i, ticker in enumerate((T3, T4, T5)):
        _pending_book(session, ticker, NOW - timedelta(seconds=5), sid=3 + i)
    _gap(session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    session.commit()


def _budget_tape(session, loop, t0):
    """One decrement and one print on each clean ticker, stamped between the first two loops.

    Both runs are handed them at the same loop, so the only difference between the runs is
    *when* each row's walk consumes them. T4's print is large enough to take the whole queue and
    fill the order, so a deferred row's fill lands on the loop that finally walks it rather than
    not at all.
    """
    if loop != 0:
        return
    for i, ticker in enumerate((T3, T4, T5)):
        _delta(session, ticker, t0 + timedelta(seconds=8), "yes", "0.35", "-2", 2, sid=3 + i)
        _print(session, ticker, t0 + timedelta(seconds=12), "0.35",
               "50" if ticker == T4 else "1", trade_id=f"budget-{ticker}")
    session.commit()


def _budget_run(env_settings, session, t0, shapes, budgets, tape=None):
    """One run over the seeded population, one loop per entry of `budgets`, on the 15 s grid.

    Returns the columns every row ends with, the rows the run wrote beside `orders`, the
    `nw_dirty_seconds` of every row after *each* loop (the column the ruling says the budget may
    never move), the counterfactual cursor after each loop, and each loop's stats.
    """
    _clear_future_tape(session, t0)
    reset_pending(session)
    ids = seed_pending(session, t0, len(shapes), shapes)
    clock = Clock(t0)
    executor = _budget_executor(env_settings, session, clock, budgets[0], monotonic=_ticking())
    accrual, cursors, stats = [], [], []
    for loop, budget in enumerate(budgets):
        _rebudget(executor, env_settings, budget)
        stats.append(executor.step())
        refresh(session)
        rows = pending_columns(session, ids)
        accrual.append([row["nw_dirty_seconds"] for row in rows])
        cursors.append([row["nw_tape_cursor_event_id"] for row in rows])
        if tape is not None:
            tape(session, loop)
        clock.advance(15)
    return pending_columns(session, ids), loop_writes(session), accrual, cursors, stats


def test_a_budgeted_rotation_reaches_every_row_and_changes_no_column(env_settings, db_session,
                                                                     world):
    """Fix 78c acceptance (a): the budget moves *when* a row walks, never what it writes.

    The same population, the same seeded tape and the same clock are run twice over ten loops:
    once with a budget no loop can spend, once with a budget that pays for exactly one row's
    walk a loop, so several loops are needed for the rotation to reach every ticker. After
    enough loops for the rotation to come round, every column of every row -- `select *`, so the
    cursor, the print watermark, the ledger document, `nw_done`, `nw_filled_contracts` and the
    version stamp are all in the comparison by construction -- and every row the loops wrote to
    `fills`, `ledger` and `order_events` are identical, and `nw_dirty_seconds` is identical
    after *every* loop, not only at the end: the dirty-market accrual is never budgeted.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _budget_world(db_session, t0)
    shapes = _budget_shapes(t0)
    loops = 10

    tape = lambda session, loop: _budget_tape(session, loop, t0)  # noqa: E731 - one expression
    unbudgeted, unbudgeted_writes, unbudgeted_accrual, unbudgeted_cursors, _ = _budget_run(
        env_settings, db_session, t0, shapes, [UNBUDGETED_MS] * loops, tape)
    budgeted, budgeted_writes, budgeted_accrual, budgeted_cursors, stats = _budget_run(
        env_settings, db_session, t0, shapes, [1] * loops, tape)

    assert budgeted == unbudgeted
    assert budgeted_writes == unbudgeted_writes
    assert budgeted_accrual == unbudgeted_accrual
    # Not a vacuous comparison: the budget really did defer rows, and on the first loop that
    # had a tape to read the deferred ones really were behind the rows the unbudgeted run had
    # already walked. (Loop 0 runs before the tape is stamped, so no cursor moves in either
    # run; loop 1 is where the unbudgeted run takes all five and the budget pays for one.)
    assert sum(step.walk_deferred_n for step in stats) > 0
    assert budgeted_cursors[1] != unbudgeted_cursors[1]
    # The fill the big print makes landed in both runs, on whichever loop each one walked it.
    assert len(unbudgeted_writes["fills"]) == 2
    assert {row["fill_method"] for row in budgeted_writes["fills"]} == {"no_watcher"}


def test_a_deferred_row_is_untouched_and_an_expiring_one_is_still_closed(env_settings,
                                                                        db_session, world):
    """Fix 78c acceptance (d): a spent budget defers everything except the expiry.

    Two clean-ticker rows and one whose expiry falls between the two loops, all on markets that
    are perfectly readable. The measured loop runs with a zero budget, so the rotation reaches
    nobody -- and the expiring row is walked first, ahead of the rotation, so `nw_done` lands on
    the same loop it lands on today. The other two come out of that loop with every column
    exactly as it went in: `select *` before and after, not a stamp and not an accrual moved.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _pending_book(db_session, T4, NOW - timedelta(seconds=5), sid=4)
    db_session.commit()
    far = t0 + timedelta(hours=1)
    shapes = [{"ticker": T3, "vm": VM3, "expiry": far},
              {"ticker": T3, "vm": VM3, "expiry": far},
              {"ticker": T4, "vm": VM4, "expiry": t0 + timedelta(seconds=5)}]
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, len(shapes), shapes)
    clock = Clock(t0)
    executor = _budget_executor(env_settings, db_session, clock, UNBUDGETED_MS)
    executor.step()
    refresh(db_session)
    before = pending_columns(db_session, ids)

    _rebudget(executor, env_settings, 0)
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    after = pending_columns(db_session, ids)

    assert before[:2] == after[:2]
    assert after[2]["nw_done"] is True
    assert before[2]["nw_done"] is False
    assert stats.walk_deferred_n == 2


def test_a_zero_budget_still_writes_open_orders_and_the_dirty_population(env_settings,
                                                                        db_session, world):
    """Fix 78c acceptance (e): the ruling's three unbudgeted populations.

    An open order goes on accruing its watched dirty seconds, the cancelled rows on the dirty
    market go on accruing theirs and closing at their expiry, and the tape read and the books
    happen as they always did -- all on a loop whose walk budget is zero and which therefore
    defers every clean-ticker row it has.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    snapshot = _pending_book(db_session, T2, NOW - timedelta(seconds=5), sid=2)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _gap(db_session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    far = t0 + timedelta(hours=1)
    shapes = [{"ticker": T2, "vm": VM2, "expiry": far},
              {"ticker": T2, "vm": VM2, "expiry": t0 + timedelta(seconds=5)},
              {"ticker": T2, "vm": VM2, "expiry": far, "status": "open"},
              {"ticker": T3, "vm": VM3, "expiry": far}]
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, len(shapes), shapes)
    clock = Clock(t0)
    executor = _budget_executor(env_settings, db_session, clock, 0)
    executor.step()
    refresh(db_session)
    before = pending_columns(db_session, ids)
    clock.advance(15)
    stats = executor.step()
    refresh(db_session)
    rows = pending_columns(db_session, ids)

    assert [row["nw_dirty_seconds"] for row in rows[:2]] == [30, 5]
    assert rows[1]["nw_done"] is True
    assert rows[2]["dirty_seconds"] == 30
    # ... and the one clean-ticker row sat both loops out with every column where it was.
    assert before[3] == rows[3]
    assert rows[3]["nw_executor_version"] is None
    assert stats.walk_deferred_n == 1


def _onset_world(session, t0):
    """The two books the onset case starts from: T3 the budget's hog, T4 the case's own row.

    T4 also gets one delta at a price that is not ours before the first loop: it moves no queue
    and fills nothing, and its only job is to leave the row's counterfactual with a cursor. A
    track that has consumed no delta at all has none, and its position is its placement --
    five minutes before this book exists, so there is no book to walk that stretch from. That is
    the ordinary production shape (a row walks every loop and carries a cursor), and it is the
    shape the dirty-onset condition is about.
    """
    _pending_book(session, T3, NOW - timedelta(seconds=5), sid=3)
    _pending_book(session, T4, NOW - timedelta(seconds=5), sid=4)
    _delta(session, T4, NOW - timedelta(seconds=3), "yes", "0.50", "-1", 2, sid=4)
    session.commit()


def _onset_tape(session, loop, t0):
    """T4's clean stretch, the gap that ends it, and the book the recovery anchors on.

    Stamped loop by loop, so each loop's book is the book the live loop would have had. The
    trade of three and the decrement that reports it land inside the stretch the market was
    still clean for; the gap lands before the third loop, which is the loop that *notices* it
    and declares the market dirty; and the snapshot before the fourth is what that loop recovers
    on. It shows 8 resting at our price, so a row that walked its clean stretch first clamps to
    2 and one that threw that stretch away clamps to 5.

    The second decrement is the review's (rev-fix-78c, I2): it lands between the gap and the
    loop that notices it -- the post-gap stretch the dirty verdict exists to distrust. No row
    that walks every loop ever applies it (the loop that could have was the loop that found the
    market dirty, and a dirty market is not walked at all), so a deferred row that applied it
    would be the budget changing what a row writes. It is 2 of the 3 remaining, so applying it
    would take the clamp from 2 to 0 rather than merely changing a cursor.
    """
    if loop == 0:
        _print(session, T4, t0 + timedelta(seconds=5), "0.35", "3", trade_id="onset-print")
        _delta(session, T4, t0 + timedelta(seconds=5), "yes", "0.35", "-3", 2, sid=4)
    elif loop == 1:
        _gap(session, t0 + timedelta(seconds=20), sid=4)
        _delta(session, T4, t0 + timedelta(seconds=22), "yes", "0.35", "-2", 3, sid=4)
    elif loop == 2:
        _ws_snapshot(session, T4, t0 + timedelta(seconds=35),
                     [("0.50", "20.00"), ("0.35", "8.00")], [("0.48", "50.00")], sid=14)
    session.commit()


def test_a_deferred_row_walks_up_to_the_dirty_onset_before_the_re_anchor(env_settings,
                                                                        db_session, world):
    """Fix 78c acceptance (c): the user's condition on the recovery branch.

    A row deferred by the budget keeps a cursor behind the book its market was clean at. If the
    market then goes dirty and recovers, the recovery branch would anchor both tracks to the
    first clean book and discard the tape between the cursor and the anchor -- including the
    clean stretch before the onset, which a row that had not been deferred would have consumed.
    So the deferred row is first simulated from its own cursor up to the dirty onset, and only
    then re-anchored, from the state that walk produced.

    The two runs differ in nothing but the budget of the second and third loops, and the row
    comes out of the fourth loop with the same columns and the same fills either way: a queue
    clamped to the 2 the clean stretch left it with, not to the 5 it would have kept by
    throwing that stretch away.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _onset_world(db_session, t0)
    shapes = [{"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1)},
              {"ticker": T4, "vm": VM4, "expiry": t0 + timedelta(hours=1)}]

    tape = lambda session, loop: _onset_tape(session, loop, t0)  # noqa: E731 - one expression
    unbudgeted, unbudgeted_writes, *_ = _budget_run(
        env_settings, db_session, t0, shapes, [UNBUDGETED_MS] * 4, tape)
    budgeted, budgeted_writes, _, _, stats = _budget_run(
        env_settings, db_session, t0, shapes, [UNBUDGETED_MS, 1, 1, UNBUDGETED_MS], tape)

    assert stats[1].walk_deferred_n == 1
    assert budgeted[1] == unbudgeted[1]
    assert budgeted_writes == unbudgeted_writes
    # Derived from the tape rather than from the code: five rested ahead of us, a trade of
    # three took three of them, and the book we recover on shows eight, so the clamp is 2.
    assert budgeted[1]["nw_queue_remaining"] == Decimal("2.00")


def _anchor_shapes(t0):
    """The two branches this part brings into the batch, on tickers of their own.

    T4's rows have never had a book (`queue_ahead_at_place` null), and its first one arrives
    between the loops, so the measured loop anchors them at the back of it. T3's book gaps and
    resubscribes between the loops, so the measured loop finds the ticker recovering and clamps
    and re-anchors both of its tracks. Neither population has a print to fill on, so every one
    of them is a write of columns and nothing else -- which is exactly what can be batched.
    """
    far = t0 + timedelta(hours=1)
    return [{"ticker": T4, "vm": VM4, "expiry": far, "queue": None},
            {"ticker": T3, "vm": VM3, "expiry": far}]


def _anchor_tape(session, t0):
    """What lands between the two loops: T4's first book ever, and T3's gap and resubscribe.

    Stamped here rather than up front because a live book is read from the head of the tape:
    T4's first book has to be invisible to the first loop, which is the loop where that ticker
    has no book at all.
    """
    _ws_snapshot(session, T4, t0 + timedelta(seconds=5),
                 [("0.50", "20.00"), ("0.35", "40.00")], [("0.48", "50.00")], sid=4)
    _gap(session, t0 + timedelta(seconds=6), sid=3)
    _ws_snapshot(session, T3, t0 + timedelta(seconds=7),
                 [("0.50", "20.00"), ("0.35", "8.00")], [("0.48", "50.00")], sid=13)
    session.commit()


def run_anchor_steps(env_settings, session, t0, ids, *, batched):
    """Two loops over the anchoring population, the second the measured one.

    The first loop is the one where T4 has no book at all and T3's book is the one it was
    placed against; the second is where T4's first book appears and T3's re-anchors, so every
    row of the population takes an anchoring branch on the loop that is compared.
    """
    _clear_future_tape(session, t0)
    clock = Clock(t0)
    executor = _budget_executor(env_settings, session, clock, UNBUDGETED_MS)
    executor._batch_pending_writes = batched
    executor.step()
    refresh(session)
    _anchor_tape(session, t0)
    clock.advance(15)
    with patch("harness.execution.loop.simulate_fills",
               wraps=harness.execution.loop.simulate_fills) as walks:
        with capture_sql(session) as seen:
            executor.step()
    refresh(session)
    return (pending_columns(session, ids), loop_writes(session), len(seen),
            walks.call_count, list(seen))


def test_the_batched_anchor_rows_match_the_per_row_path_column_for_column(env_settings,
                                                                          db_session, world):
    """Fix 78c acceptance (b): the re-anchor and no-book rows write what they always wrote.

    The same population, tape and clock with the fix on and off. Every column of every row and
    every row of `fills`, `ledger` and `order_events` is identical; the batched run sends fewer
    statements and walks the tape no more often than the per-row path does; and the rows that
    were batched are bound into the `UPDATE ... FROM (VALUES ...)` and into no other `orders`
    write of that loop.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    shapes = _anchor_shapes(t0)

    # Cleared before the seeding, not only inside the run: `seed_pending` reads the head of
    # each ticker's tape for the cursor it seeds, and the previous run's leftovers would put
    # that head past the events this run is about to re-create.
    _clear_future_tape(db_session, t0)
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, len(shapes), shapes)
    per_row, per_row_writes, per_row_statements, per_row_walks, _ = run_anchor_steps(
        env_settings, db_session, t0, ids, batched=False)
    _clear_future_tape(db_session, t0)
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, len(shapes), shapes)
    batched, batched_writes, batched_statements, batched_walks, seen = run_anchor_steps(
        env_settings, db_session, t0, ids, batched=True)

    assert batched == per_row
    assert batched_writes == per_row_writes
    assert batched_statements < per_row_statements
    assert batched_walks == per_row_walks
    # The no-book row anchored at the back of the first book it ever saw, and the recovering
    # one clamped to what is actually resting there now: both are writes this part batches.
    assert batched[0]["queue_ahead_at_place"] == Decimal("40.00")
    assert batched[1]["nw_queue_remaining"] == Decimal("5.00")
    per_row_writes_seen, id_lists, values_lists = _orders_writes(seen)
    bound = {value for _, parameters in values_lists for value in _param_ids(parameters)}
    elsewhere = {value for _, parameters in per_row_writes_seen + id_lists
                 for value in _param_ids(parameters)}
    for order_id in ids:
        assert order_id in bound, order_id
        assert order_id not in elsewhere, order_id


def _anchor_statements(env_settings, session, t0, n) -> int:
    """Statements the measured (anchoring) loop over `n` seeded rows actually sends."""
    _clear_future_tape(session, t0)
    reset_pending(session)
    shapes = _anchor_shapes(t0)
    ids = seed_pending(session, t0, n, shapes)
    return run_anchor_steps(env_settings, session, t0, ids, batched=True)[2]


def test_the_anchor_population_costs_a_flat_number_of_statements(env_settings, db_session,
                                                                 world):
    """Fix 78c acceptance (b): ten times the anchoring population, the same statements.

    Both branches used to cost a savepoint, an UPDATE and a release each on the loop they
    anchored on -- the loop where a whole ticker recovers is the loop where every row on it
    re-anchors at once, which is the spike this part removes.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()

    counts = [_anchor_statements(env_settings, db_session, t0, n) for n in (5, 50)]

    assert counts[0] == counts[1], f"anchoring population: {counts}"


def _exec_samples(session):
    return {row.name: row.value
            for row in session.query(MetricSample).filter_by(source="exec").all()}


def test_the_per_cause_counters_sum_to_per_row_n_and_the_budget_is_published(env_settings,
                                                                            db_session, world):
    """Fix 78c acceptance (f): every per-row row is counted under exactly one cause.

    The mixed population's measured loop leaves three rows on the per-row path -- two open
    orders, which the ruling keeps there, and one whose late print fills it -- so the causes are
    not merely equal to `per_row_n` but are the two the loop actually met. The budget's own
    gauges are written once each, with the budget in force and a non-negative deferral count.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    snapshot = _pending_book(db_session, T2, NOW - timedelta(seconds=5), sid=2)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    _pending_book(db_session, T4, NOW - timedelta(seconds=5), sid=4)
    _gap(db_session, NOW - timedelta(seconds=1), sid=snapshot.sid)
    db_session.commit()
    shapes = _mixed_shapes(t0)
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, len(shapes), shapes)
    clock = Clock(t0)
    executor = _budget_executor(env_settings, db_session, clock, 12_345, sample_s=1)
    executor.step()
    refresh(db_session)
    _print(db_session, T4, t0 + timedelta(seconds=14), "0.35", "500", trade_id=LATE_PRINT)
    # Only the measured loop's batch is read below: the sampler is due on every loop here, and
    # a gauge of the first loop carries the first loop's population, not this one's.
    db_session.execute(text("delete from metric_samples"))
    db_session.commit()
    clock.advance(15)

    stats = executor.step()
    refresh(db_session)

    samples = _exec_samples(db_session)
    counted = {name: value for name, value in samples.items()
               if name.startswith("exec.per_row_") and name != "exec.per_row_n"}
    assert sum(int(value) for value in counted.values()) == int(samples["exec.per_row_n"])
    assert int(samples["exec.per_row_n"]) == stats.per_row_n == 3
    assert int(samples["exec.per_row_open"]) == 2
    assert int(samples["exec.per_row_fill"]) == 1
    assert int(samples["exec.walk_budget_ms"]) == 12_345
    assert int(samples["exec.walk_deferred_n"]) >= 0
    assert int(samples["exec.walk_tickers_n"]) >= 1
    names = [row.name for row in db_session.query(MetricSample).filter_by(source="exec").all()]
    for name in ("exec.walk_budget_ms", "exec.walk_deferred_n", "exec.walk_tickers_n",
                 "exec.per_row_open", "exec.per_row_fill", "exec.per_row_reanchor"):
        assert names.count(name) == 1, name


def test_a_never_anchored_row_leaves_its_ticker_s_delta_read_bounded_by_the_cursor(
        env_settings, db_session, world):
    """Review rev-fix-78c I1: a track with no queue must not widen its ticker's delta scan.

    Two rows on one ticker: one walked already, sitting at the head of the tape, and one that
    has never had a book -- no queue, no cursor. The second consumes no delta whatever it is
    handed (`simulate_fills` returns before it reads an event when the queue is None), so
    counting its null cursor as position zero would buy nothing and would put the whole ticker
    back on fix 22's `ts`-bounded plan: `id > 0 and ts >= min(placed_at) - 60 s` against a
    20,000-row limit, every loop, inside the tape phase the ruling leaves unbudgeted -- and a
    read that came back full would mark the ticker lagging and block `nw_done` for every track
    on it. The loop's one delta read is bounded by the live cursor and by nothing else.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _pending_book(db_session, T3, NOW - timedelta(seconds=5), sid=3)
    db_session.commit()
    far = t0 + timedelta(hours=1)
    shapes = [{"ticker": T3, "vm": VM3, "expiry": far},
              {"ticker": T3, "vm": VM3, "expiry": far, "queue": None}]
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, len(shapes), shapes)
    seeded = pending_columns(db_session, ids)
    assert seeded[0]["nw_tape_cursor_event_id"] is not None
    assert seeded[1]["nw_tape_cursor_event_id"] is None
    assert seeded[1]["nw_queue_remaining"] is None
    clock = Clock(t0)
    executor = _budget_executor(env_settings, db_session, clock, UNBUDGETED_MS)

    with capture_sql(db_session) as seen:
        executor.step()

    # The step reads deltas twice -- once to advance the book, once as the tape -- and it is
    # the tape read (`store._DELTAS`, the only one carrying `sid`) this is about.
    reads = [(query.lower(), parameters) for query, parameters in seen
             if "kind = 'delta'" in query and "sid, seq" in query]
    assert len(reads) == 1, [query for query, _ in reads]
    where = reads[0][0].split("where", 1)[1].split("order by", 1)[0]
    assert "ts" not in where, where
    assert "id >" in where, where
    assert reads[0][1]["cursor"] == seeded[0]["nw_tape_cursor_event_id"]


def _two_cycle_tape(session, loop, t0):
    """T4 through two dirty stretches and two recoveries, one loop at a time.

    Loop 0's tape is the clean stretch both runs consume: a trade of three against the five
    resting ahead of us. The first gap lands before loop 2, which finds the market dirty; the
    decrement inside that dirty stretch is the one **no** run may ever apply, and the snapshot
    before loop 3 is the first recovery. The second gap lands before loop 4 and the second
    recovery before loop 5, which is the loop that is measured: it shows 6 resting, so a row
    that walked its clean stretch and stopped at the *first* gap clamps to 2, and one that
    walked through the first dirty stretch as well clamps to 0.
    """
    if loop == 0:
        _print(session, T4, t0 + timedelta(seconds=5), "0.35", "3", trade_id="cycle-print")
        _delta(session, T4, t0 + timedelta(seconds=5), "yes", "0.35", "-3", 2, sid=4)
    elif loop == 1:
        _gap(session, t0 + timedelta(seconds=20), sid=4)
    elif loop == 2:
        _delta(session, T4, t0 + timedelta(seconds=35), "yes", "0.35", "-2", 3, sid=4)
        _ws_snapshot(session, T4, t0 + timedelta(seconds=40),
                     [("0.50", "20.00"), ("0.35", "8.00")], [("0.48", "50.00")], sid=14)
    elif loop == 3:
        _gap(session, t0 + timedelta(seconds=50), sid=14)
    elif loop == 4:
        _ws_snapshot(session, T4, t0 + timedelta(seconds=65),
                     [("0.50", "20.00"), ("0.35", "6.00")], [("0.48", "50.00")], sid=24)
    session.commit()


def test_a_row_deferred_across_two_dirty_cycles_stops_at_the_first_gap(env_settings,
                                                                       db_session, world):
    """Review rev-fix-78c I3: the onset is the first gap after the cursor, never the newest.

    Deferral is not limited to one loop, and a row on a dirty market is not walked at all, so a
    row can keep a cursor from before the *first* of two dirty stretches. Walking it to the
    newest onset would apply every print and delta of the first dirty stretch as though the
    market had been clean there -- tape a row that was never deferred never sees, because it
    re-anchored at the first recovery. The two runs differ in nothing but the budget of the
    middle four loops and come out of the last one identical.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _onset_world(db_session, t0)
    shapes = [{"ticker": T3, "vm": VM3, "expiry": t0 + timedelta(hours=1)},
              {"ticker": T4, "vm": VM4, "expiry": t0 + timedelta(hours=1)}]

    tape = lambda session, loop: _two_cycle_tape(session, loop, t0)  # noqa: E731 - one expr
    unbudgeted, unbudgeted_writes, *_ = _budget_run(
        env_settings, db_session, t0, shapes, [UNBUDGETED_MS] * 6, tape)
    # Zero rather than a millisecond for the middle loops: the rotation would otherwise reach
    # T4 on the loop after the one it stopped on, and this case needs the row left behind for
    # both cycles.
    budgeted, budgeted_writes, _, _, stats = _budget_run(
        env_settings, db_session, t0, shapes, [UNBUDGETED_MS, 0, 0, 0, 0, UNBUDGETED_MS], tape)

    assert sum(step.walk_deferred_n for step in stats[1:5]) > 0
    assert budgeted[1] == unbudgeted[1]
    assert budgeted_writes == unbudgeted_writes
    # Five rested ahead of us, a trade of three took three of them inside the first clean
    # stretch, and the book the second recovery anchors on shows six: the clamp is 2, and the
    # two the first dirty stretch's decrement would have taken are not taken.
    assert budgeted[1]["nw_queue_remaining"] == Decimal("2.00")


class _WorkClock:
    """A monotonic that moves only when the work under test says it did.

    `_ticking()` charges every *reading*, which is what makes it right for the phase timers and
    wrong for a case about a budget: a phase there measures twice what it charges, because the
    two readings around a row cost a tick each. Here the readings are free and the walk and the
    per-row step each charge what the case says they cost, so the budget arithmetic in the
    assertions is the loop's own.
    """

    def __init__(self) -> None:
        self.t = 0.0

    def read(self) -> float:
        return self.t

    def charge(self, ms) -> None:
        self.t += ms / 1000.0


def test_the_cancelled_per_row_residual_is_charged_to_the_budget_and_deferred(env_settings,
                                                                              db_session, world):
    """Review rev-fix-78c I4(a): the per-row residual is budgeted in time, not only in population.

    The user's ruling budgets "the clean-ticker walk **and the cancelled per-row residual**". A
    cancelled row whose walk finds a fill has to take a savepoint and an UPDATE of its own, and
    that is the 40-110 ms a row the measured loops spent 0-47 s on; so it is charged to the same
    budget, and the rows the budget can no longer afford are deferred exactly as the rotation
    defers the ones it never reached -- walked or not. Deferring a walked row costs the walk and
    nothing else: nothing of it has been written, and the next loop walks the same tape from the
    same cursor and finds the same fill.

    Five rows on one clean ticker, all of which the late print fills. The walk is cheap (1 ms a
    row) and the per-row step is expensive (50 ms), so a 10 ms budget pays for every row's walk
    and for exactly one row's per-row step, and the loop's whole budgeted time is the budget plus
    that one row -- rather than five of them, which is what an unbudgeted residual would cost.
    """
    keep_only(db_session, set())
    t0 = NOW + timedelta(seconds=10)
    _pending_book(db_session, T4, NOW - timedelta(seconds=5), sid=4)
    db_session.commit()
    far = t0 + timedelta(hours=1)
    shapes = [{"ticker": T4, "vm": VM4, "expiry": far}]
    reset_pending(db_session)
    ids = seed_pending(db_session, t0, 5, shapes)
    work = _WorkClock()
    clock = Clock(t0)
    executor = _budget_executor(env_settings, db_session, clock, 10, monotonic=work.read)
    executor.step()
    refresh(db_session)
    _print(db_session, T4, t0 + timedelta(seconds=14), "0.35", "500", trade_id=LATE_PRINT)
    db_session.commit()
    before = pending_columns(db_session, ids)
    clock.advance(15)

    per_row = executor._simulate_order

    def charged(*args, **kwargs):
        work.charge(50)
        return per_row(*args, **kwargs)

    executor._simulate_order = charged
    real = harness.execution.loop.simulate_fills

    def walked(*args, **kwargs):
        work.charge(1)
        return real(*args, **kwargs)

    with patch("harness.execution.loop.simulate_fills", side_effect=walked):
        stats = executor.step()
    refresh(db_session)
    after = pending_columns(db_session, ids)

    assert stats.walk_budget_ms == 10
    assert stats.per_row_n == 1
    assert stats.walk_deferred_n == 4
    assert stats.phase_walk_ms + stats.phase_per_row_ms <= stats.walk_budget_ms + 50
    # The one row the budget could afford wrote its fill; the four it could not are untouched,
    # column for column, and their fill lands on a later loop rather than not at all.
    assert len(loop_writes(db_session)["fills"]) == 1
    assert before[1:] == after[1:]
    assert after[0]["nw_filled_contracts"] == Decimal("10.00")

