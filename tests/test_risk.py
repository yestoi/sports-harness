"""Section 9.3's drawdown stop: the rule, the three columns, and the proof that the label it
writes is an annotation rather than a filter.

The two things this file exists to pin are negative. The stop never decides a candidate --
`drawdown_stop` is in `ANNOTATION_LABELS`, so it is in neither `FILTER_LABELS` nor
`CAP_LABELS` and `_decide` cannot reach it -- and the paper executor keeps placing orders
while it is tripped (decision 6: in paper the stop is information, not a brake).
"""

import uuid
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text, update

from harness.db.models import EquitySnapshot, Fill, KillSwitch, Order, VenueMarket
from harness.execution import store
from harness.execution.gateway import RECONCILE_FILL_LOOKBACK, PaperGateway
from harness.execution.risk import (
    DRAWDOWN_STOP_PCT,
    DRAWDOWN_WINDOW,
    Drawdown,
    compute_drawdown,
    peak_equity_7d,
    stopped_variants,
)
from harness.strategy.run import run_strategy
from tests.test_exec_loop import (
    NOW,
    Clock,
    _book2,
    make_executor,
    orders_of,
    refresh,
    world,  # noqa: F401 - a fixture used by the executor tests below
)
from tests.test_kalshi_authed import FakeTransport
from tests.test_strategy import gap_row, variant
from tests.test_venue_state import _fresh, _kalshi, _open_order


def _snapshot(session, variant_id, ts, cash="100.00", mtm_open=None, stop=None,
              pct=None, peak=None):
    """One `equity_snapshots` row, with only the columns a risk test cares about."""
    session.add(EquitySnapshot(
        ts=ts, variant_id=variant_id, cash=Decimal(cash), open_stake=Decimal("0"),
        mtm_open=None if mtm_open is None else Decimal(mtm_open), mtm_coverage=None,
        n_open_positions=0, n_open_orders=0,
        peak_equity_7d=None if peak is None else Decimal(peak),
        drawdown_pct=None if pct is None else Decimal(pct),
        drawdown_stop=stop))
    session.flush()


# --- the rule (§9.3) ------------------------------------------------------------------------


@pytest.mark.parametrize("cash,peak,pct,stop", [
    ("100.00", "100.00", "0.0000", False),
    ("90.00",  "100.00", "-0.1000", False),
    ("80.00",  "100.00", "-0.2000", True),      # exactly -20 % trips
    ("79.99",  "100.00", "-0.2001", True),
    ("120.00", "100.00", "0.2000", False),
])
def test_drawdown_rule(cash, peak, pct, stop):
    d = compute_drawdown(Decimal(cash), Decimal(peak))
    assert d.drawdown_pct == Decimal(pct) and d.drawdown_stop is stop


def test_the_threshold_and_window_are_the_registered_ones():
    """Both are §9.3 numbers: a change here is a user decision, not a tuning knob."""
    assert DRAWDOWN_STOP_PCT == Decimal("-0.20")
    assert DRAWDOWN_WINDOW == timedelta(days=7)


def test_no_peak_yields_no_drawdown():
    assert compute_drawdown(Decimal("100"), None).drawdown_stop is False
    assert compute_drawdown(Decimal("100"), None).drawdown_pct is None


def test_a_non_positive_peak_never_trips():
    assert compute_drawdown(Decimal("-5"), Decimal("0")).drawdown_stop is False


def test_the_drawdown_is_a_frozen_value():
    d = compute_drawdown(Decimal("80"), Decimal("100"))
    assert isinstance(d, Drawdown)
    with pytest.raises(FrozenInstanceError):
        d.drawdown_stop = False


def test_peak_is_over_the_trailing_seven_days_only(db_session):
    _snapshot(db_session, "v1", NOW - timedelta(days=8), cash="500.00")   # outside the window
    _snapshot(db_session, "v1", NOW - timedelta(days=3), cash="120.00")
    assert peak_equity_7d(db_session, "v1", NOW, Decimal("100")) == Decimal("120.00")


def test_the_peak_is_this_variants_own(db_session):
    _snapshot(db_session, "v2", NOW - timedelta(days=1), cash="900.00")
    assert peak_equity_7d(db_session, "v1", NOW, Decimal("100")) == Decimal("100")


def test_the_first_sample_is_its_own_peak(db_session):
    assert peak_equity_7d(db_session, "v1", NOW, Decimal("100")) == Decimal("100")


def test_mtm_open_never_enters_the_stop(db_session):
    # A variant deep in unrealised profit is still stopped on cash (decision 6, B-C2).
    _snapshot(db_session, "v1", NOW - timedelta(days=1), cash="100.00", mtm_open="900.00")
    d = compute_drawdown(Decimal("80.00"), peak_equity_7d(db_session, "v1", NOW, Decimal("80")))
    assert d.drawdown_stop is True


def test_stopped_variants_reads_the_newest_snapshot_per_variant(db_session):
    _snapshot(db_session, "v1", NOW - timedelta(hours=2), stop=True)
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=False)   # recovered
    assert stopped_variants(db_session, NOW) == set()


def test_stopped_variants_names_every_stopped_variant(db_session):
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)
    _snapshot(db_session, "v2", NOW - timedelta(minutes=5), stop=False)
    _snapshot(db_session, "v3", NOW - timedelta(minutes=5), stop=True)
    assert stopped_variants(db_session, NOW) == {"v1", "v3"}


def test_a_snapshot_that_never_carried_a_verdict_is_not_a_recovery(db_session):
    """The settler's row and every row written before the gate shipped carry NULL. A NULL is
    "not evaluated", never "recovered", so the newest *verdict* is what counts."""
    _snapshot(db_session, "v1", NOW - timedelta(hours=2), stop=True)
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=None)
    assert stopped_variants(db_session, NOW) == {"v1"}


def test_a_stale_verdict_outside_the_window_does_not_stop(db_session):
    _snapshot(db_session, "v1", NOW - timedelta(days=8), stop=True)
    assert stopped_variants(db_session, NOW) == set()


# --- the executor's three columns ---------------------------------------------------------


def _tiny_variant_id(session) -> str:
    return session.execute(
        text("select variant_id from strategy_variants where name = 'tiny'")).scalar_one()


def _sink_the_variant(db_session) -> str:
    """A peak 25 % above the bankroll inside the window, so the next sample trips the stop."""
    variant_id = _tiny_variant_id(db_session)
    _snapshot(db_session, variant_id, NOW - timedelta(days=1), cash="4000.00")
    db_session.commit()
    return variant_id


def test_the_executor_writes_the_three_columns(env_settings, db_session, world):
    variant_id = _sink_the_variant(db_session)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)

    row = db_session.execute(
        select(EquitySnapshot).where(EquitySnapshot.variant_id == variant_id)
        .order_by(EquitySnapshot.ts.desc()).limit(1)).scalar_one()
    assert row.peak_equity_7d == Decimal("4000.00")
    assert row.drawdown_pct == Decimal("-0.2500")
    assert row.drawdown_stop is True


def test_the_executor_keeps_placing_while_stopped(env_settings, db_session, world):
    """Decision 6: in paper the stop is information. The order still goes out."""
    _sink_the_variant(db_session)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)

    assert db_session.execute(select(func.count()).select_from(Order)).scalar() > 0
    assert orders_of(db_session)


def test_an_unstopped_variant_records_its_drawdown_anyway(env_settings, db_session, world):
    """The columns are written on every sample, not only on the tripped ones."""
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    clock = Clock(NOW)
    make_executor(env_settings, db_session, clock).step()
    refresh(db_session)

    row = db_session.execute(select(EquitySnapshot)
                             .order_by(EquitySnapshot.ts.desc()).limit(1)).scalar_one()
    assert row.drawdown_stop is False
    assert row.drawdown_pct == Decimal("0.0000")   # its own first sample is its own peak
    assert row.peak_equity_7d == row.cash


# --- the annotation never decides (A-C2 / B-C1) --------------------------------------------------


def test_drawdown_stop_is_an_annotation_not_a_filter():
    from harness.strategy.run import ANNOTATION_LABELS, CAP_LABELS, FILTER_LABELS, LABEL_ORDER
    assert "drawdown_stop" in LABEL_ORDER
    assert "drawdown_stop" in ANNOTATION_LABELS
    assert "drawdown_stop" not in FILTER_LABELS and "drawdown_stop" not in CAP_LABELS


def test_no_annotation_is_ever_counted_by_a_decision():
    """The set relation, not the one label: a second annotation added later stays outside."""
    from harness.strategy.run import ANNOTATION_LABELS, CAP_LABELS, FILTER_LABELS
    assert not set(ANNOTATION_LABELS) & set(FILTER_LABELS)
    assert not set(ANNOTATION_LABELS) & set(CAP_LABELS)


def test_a_stopped_signal_with_every_filter_true_is_still_a_candidate():
    signals = run_strategy([gap_row()], variant("sharp_direct"), NOW, stopped=True)
    assert signals and all(s.decision == "candidate" for s in signals)
    assert all(s.labels["drawdown_stop"] is True for s in signals)


def test_drawdown_stop_is_never_a_rejection_reason():
    rows = [gap_row(), gap_row(match_status="fuzzy"), gap_row(fair_p=None, fair_source=None),
            gap_row(volume_24h=0), gap_row(ttk_minutes=1)]
    signals = run_strategy(rows, variant("sharp_direct"), NOW, stopped=True)
    assert any(s.decision == "rejected" for s in signals)   # or this proves nothing
    assert all(s.rejection_reason != "drawdown_stop" for s in signals)


def test_the_annotation_defaults_to_false_for_every_existing_caller():
    (sig,) = run_strategy([gap_row()], variant("sharp_direct"), NOW)
    assert sig.labels["drawdown_stop"] is False


# --- the live response (dormant): the kill switch --------------------------------------------


def test_the_live_gateway_trips_the_kill_switch_for_a_stopped_variant(db_session):
    """Live is the half of decision 6 that is a brake. Reason: `drawdown_stop:<variant>`.

    Read back on a session of its own, because that is the claim: the trip is committed
    outside the caller's transaction and so survives whatever happens to it.
    """
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)
    gateway = _kalshi(FakeTransport(), db_session)

    assert gateway.on_drawdown_stop(db_session, NOW) == {"v1"}

    with _fresh(db_session) as fresh:
        row = fresh.execute(select(KillSwitch)).scalar_one()
        assert row.active is True and row.reason == "drawdown_stop:v1" and row.set_at == NOW


def test_the_live_gateway_names_every_stopped_variant_in_the_reason(db_session):
    _snapshot(db_session, "v2", NOW - timedelta(minutes=5), stop=True)
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)

    _kalshi(FakeTransport(), db_session).on_drawdown_stop(db_session, NOW)

    with _fresh(db_session) as fresh:
        assert fresh.execute(
            select(KillSwitch.reason)).scalar() == "drawdown_stop:v1, drawdown_stop:v2"


def test_the_live_gateway_trips_nothing_when_no_variant_is_stopped(db_session):
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=False)

    assert _kalshi(FakeTransport(), db_session).on_drawdown_stop(db_session, NOW) == set()

    with _fresh(db_session) as fresh:
        assert fresh.execute(select(func.count()).select_from(KillSwitch)).scalar() == 0


def test_the_paper_gateway_holds_no_kill_switch_code_at_all(db_session):
    """The guard is structural: `PaperGateway.on_drawdown_stop` returns an empty set and writes
    nothing, so the paper executor cannot reach the live response even with the stop tripped."""
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)

    assert PaperGateway().on_drawdown_stop(db_session, NOW) == set()

    assert db_session.execute(select(func.count()).select_from(KillSwitch)).scalar() == 0


def test_a_stopped_paper_executor_writes_no_kill_switch(env_settings, db_session, world):
    _sink_the_variant(db_session)
    _book2(db_session, NOW - timedelta(seconds=5))
    db_session.commit()
    make_executor(env_settings, db_session, Clock(NOW)).step()
    refresh(db_session)

    assert db_session.execute(select(func.count()).select_from(KillSwitch)).scalar() == 0
    assert stopped_variants(db_session, NOW)      # the stop really is tripped


# --- Task 11's two carried rulings ------------------------------------------------------------


def _fill(session, order_id, method, filled_at, prob="0.5000", contracts="10.00"):
    session.add(Fill(order_id=order_id, prob=Decimal(prob), contracts=Decimal(contracts),
                     fee=Decimal("0.0000"), filled_at=filled_at, simulated=False,
                     fill_method=method, replay=False))
    session.flush()


def test_the_daily_cap_counts_a_venue_fill(db_session):
    """Ruling: a live fill (`fill_method = 'venue'`) is money put at risk today, so the daily
    stake cap sees it. Leaving it out would let the live path spend the paper path's cap twice.
    """
    order_id = _open_order(db_session, variant_id="v-cap", mode="live")
    _fill(db_session, order_id, "venue", NOW - timedelta(hours=1))

    (view,) = store.load_fills_today(db_session, False, NOW - timedelta(days=1))
    assert view.variant_id == "v-cap" and view.stake == Decimal("5.0000")


def test_the_daily_cap_still_ignores_the_counterfactual_fill_methods(db_session):
    """`no_watcher` and `snapshot_cross` are not money: they are what an order would have done.
    """
    order_id = _open_order(db_session, variant_id="v-cap", mode="live")
    _fill(db_session, order_id, "queue_model", NOW - timedelta(hours=1))
    _fill(db_session, order_id, "no_watcher", NOW - timedelta(hours=1))
    _fill(db_session, order_id, "snapshot_cross", NOW - timedelta(hours=1))

    (view,) = store.load_fills_today(db_session, False, NOW - timedelta(days=1))
    assert view.stake == Decimal("5.0000")   # the queue-model fill alone


def test_the_live_poll_window_reaches_back_to_the_last_known_fill(db_session):
    """Ruling: `reconcile` counts fills and writes none, so the first poll after it must cover
    them. The watermark is the newest live fill we hold, not local midnight."""
    order_id = _open_order(db_session, variant_id="v-cap", mode="live")
    _fill(db_session, order_id, "venue", NOW - timedelta(days=2))

    gateway = _kalshi(FakeTransport(), db_session)
    assert gateway.fills_since(db_session, NOW) == NOW - timedelta(days=2)


def test_the_live_poll_window_falls_back_to_a_bounded_lookback(db_session):
    gateway = _kalshi(FakeTransport(), db_session)
    assert gateway.fills_since(db_session, NOW) == NOW - RECONCILE_FILL_LOOKBACK


def test_the_paper_poll_window_is_never_read(db_session):
    """`PaperGateway.poll_fills` is empty whatever window it is given, so its `fills_since` is
    an answer no caller acts on."""
    assert PaperGateway().fills_since(db_session, NOW) == NOW
    assert PaperGateway().poll_fills(db_session, NOW - timedelta(days=1)) == []


# --- fix round 1: one trip per stop episode, and a standing switch is left alone ---------------


def _kill_switch(session):
    with _fresh(session) as fresh:
        return fresh.execute(select(KillSwitch)).scalar_one_or_none()


def _clear_the_switch(session, now):
    """What `harness kill-switch off` does: the latch goes inactive, the row stays."""
    with _fresh(session) as fresh:
        fresh.execute(update(KillSwitch).values(active=False, reason="operator", set_at=now))
        fresh.commit()


def test_the_kill_switch_trips_once_per_stop_episode(db_session):
    """Three consecutive stopped samples are one episode and one trip. Re-tripping every
    300 s would overwrite `set_at`, so the row would no longer say when trading stopped."""
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)
    gateway = _kalshi(FakeTransport(), db_session)

    for minutes in (0, 5, 10):
        gateway.on_drawdown_stop(db_session, NOW + timedelta(minutes=minutes))

    row = _kill_switch(db_session)
    assert row.active is True and row.reason == "drawdown_stop:v1" and row.set_at == NOW


def test_an_active_kill_switch_keeps_its_own_reason_and_time(db_session):
    """A standing §9.2 budget trip is the operator's record of why and when trading stopped.
    The drawdown stop never overwrites it."""
    earlier = NOW - timedelta(hours=3)
    with _fresh(db_session) as fresh:
        fresh.add(KillSwitch(id=1, active=True, reason="message budget exceeded",
                             set_at=earlier))
        fresh.commit()
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)

    _kalshi(FakeTransport(), db_session).on_drawdown_stop(db_session, NOW)

    row = _kill_switch(db_session)
    assert row.active is True
    assert row.reason == "message budget exceeded" and row.set_at == earlier


def test_an_operator_clear_is_not_re_asserted_while_the_condition_holds(db_session):
    """The stop is a latch, not a lock. Once the operator has seen it and cleared it, the same
    episode does not put it back within 300 s."""
    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=True)
    gateway = _kalshi(FakeTransport(), db_session)
    gateway.on_drawdown_stop(db_session, NOW)
    _clear_the_switch(db_session, NOW + timedelta(minutes=1))

    gateway.on_drawdown_stop(db_session, NOW + timedelta(minutes=5))

    assert _kill_switch(db_session).active is False


def test_a_recovery_and_a_second_stop_trip_again(db_session):
    """A new episode is a new trip: the variant left the stopped set and came back to it."""
    _snapshot(db_session, "v1", NOW - timedelta(minutes=10), stop=True)
    gateway = _kalshi(FakeTransport(), db_session)
    gateway.on_drawdown_stop(db_session, NOW - timedelta(minutes=9))
    _clear_the_switch(db_session, NOW - timedelta(minutes=8))

    _snapshot(db_session, "v1", NOW - timedelta(minutes=5), stop=False)   # recovered
    gateway.on_drawdown_stop(db_session, NOW - timedelta(minutes=4))
    assert _kill_switch(db_session).active is False

    _snapshot(db_session, "v1", NOW - timedelta(minutes=1), stop=True)    # and stopped again
    gateway.on_drawdown_stop(db_session, NOW)

    row = _kill_switch(db_session)
    assert row.active is True and row.reason == "drawdown_stop:v1" and row.set_at == NOW


def test_a_second_variant_stopping_trips_on_its_own_episode(db_session):
    """The first trip names v1 only; v2's later stop is its own episode. The standing switch
    keeps the first reason, because a cleared-then-re-tripped record is the operator's."""
    _snapshot(db_session, "v1", NOW - timedelta(minutes=10), stop=True)
    gateway = _kalshi(FakeTransport(), db_session)
    gateway.on_drawdown_stop(db_session, NOW - timedelta(minutes=9))
    _clear_the_switch(db_session, NOW - timedelta(minutes=8))

    _snapshot(db_session, "v2", NOW - timedelta(minutes=1), stop=True)
    assert gateway.on_drawdown_stop(db_session, NOW) == {"v1", "v2"}

    row = _kill_switch(db_session)
    assert row.active is True and row.reason == "drawdown_stop:v2" and row.set_at == NOW


# --- fix round 1: the exposure reads see a venue fill ------------------------------------------


def _venue_market(session, side_team_id=42) -> int:
    """`store._POSITIONS` joins the market for its `side_team_id`, so a position needs one."""
    market = VenueMarket(venue="kalshi", ticker=f"KXRISK-{uuid.uuid4().hex[:8]}",
                         event_ticker="KXRISK-EVT", series_ticker="KXRISK",
                         game_id=7, market_type="moneyline", side_team_id=side_team_id,
                         first_seen_raw_id=1, last_seen_at=NOW)
    session.add(market)
    session.flush()
    return market.id


def test_load_positions_counts_a_venue_fill(db_session):
    """`rebuild_state` feeds `cap_per_game` and `max_open` off this. A live position that read
    as no position would let those caps be spent twice."""
    order_id = _open_order(db_session, variant_id="v-pos", mode="live",
                           venue_market_id=_venue_market(db_session), game_id=7)
    _fill(db_session, order_id, "venue", NOW, prob="0.4000", contracts="10.00")

    (view,) = store.load_positions(db_session, False)
    assert view.variant_id == "v-pos" and view.stake == Decimal("4.0000")


def test_load_positions_still_ignores_the_counterfactual_fill_methods(db_session):
    order_id = _open_order(db_session, variant_id="v-pos", mode="live",
                           venue_market_id=_venue_market(db_session), game_id=7)
    _fill(db_session, order_id, "no_watcher", NOW, prob="0.4000", contracts="10.00")
    _fill(db_session, order_id, "snapshot_cross", NOW, prob="0.4000", contracts="10.00")

    assert store.load_positions(db_session, False) == []


def test_the_positions_view_counts_a_venue_fill(db_session):
    """The same widening in the view the dashboard, `open_stake` and `mtm_open` read."""
    order_id = _open_order(db_session, variant_id="v-pos", mode="live")
    _fill(db_session, order_id, "venue", NOW, prob="0.4000", contracts="10.00")
    _fill(db_session, order_id, "queue_model", NOW, prob="0.6000", contracts="10.00")
    _fill(db_session, order_id, "no_watcher", NOW, prob="0.9000", contracts="50.00")

    (row,) = db_session.execute(text(
        "select variant_id, open_contracts, avg_price from positions")).all()
    assert row.variant_id == "v-pos" and row.open_contracts == Decimal("20.00")
    assert row.avg_price == Decimal("0.5")
