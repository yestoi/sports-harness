"""Task 9: the order gateway split, and the golden replay that proves paper did not move.

Two things are being tested here and they are not the same thing.

The first is the seam itself: `PaperGateway` writes what phase 3 wrote, `KalshiGateway` writes
that plus the three venue columns and additionally sends a message, and the executor picks
between them off `Settings.mode` and cannot pick the live one in this phase.

The second is the golden replay, and it is worth being exact about what it does and does not
establish. `test_golden_replay_is_byte_identical_across_the_seam` runs the committed fixture day
through the shipped executor twice: once with `_LegacyGateway`, whose `place` and `cancel` are
the two `store` calls phase 3 made inline, and once with `PaperGateway`. Both passes run the
*same* loop, so the test cannot see a regression the two passes share -- it pins `PaperGateway`
against the recorded phase-3 write path, not the loop against its own history. That is a
tripwire for Tasks 10 and 11, which edit `gateway.py`: the day it stops being true that
`PaperGateway.place` and `.cancel` are those two calls, the diff fires across `orders`,
`order_events`, `fills`, `ledger`, the heartbeat counters and the metric samples.

The evidence that the *loop* is unchanged is elsewhere and is stronger: the 50 phase-3 executor
tests in `tests/test_exec_loop.py` pass unmodified, and the whole of `_place`'s diff is one
line (review round 1, Important 2).

`_LegacyGateway` is a copy rather than a recorded snapshot on purpose: a committed digest of
"what phase 3 produced" would rot the first time an unrelated task changed the day, and would
stop proving anything the moment it did.

Nothing here opens a socket: the live tests drive Task 6's `FakeTransport`.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import (
    ExecHeartbeat,
    Fill,
    Ledger,
    MetricSample,
    Order,
    OrderEvent,
)
from harness.execution import EXECUTOR_VERSION, store
from harness.execution.gateway import (
    KalshiGateway,
    NoOrderGroup,
    PaperGateway,
    PlacedOrder,
    ReconcileReport,
    orders_by_venue_id,
    uses_the_simulator,
)
from harness.execution.loop import ExecStats, Executor
from harness.execution.plan import Cancel
from harness.venues.kalshi.authed import ORDERS_PATH, KalshiReader, LiveGuardRefused

from tests.test_kalshi_authed import FakeTransport, _ok
from tests.test_kalshi_writer import _echo_of, _writer

NOW = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(hours=12)
CENT_RANGES = [{"start": 0, "end": 1, "step": 0.01}]


# --- helpers --------------------------------------------------------------------------


def _values(**kw) -> dict:
    """The shape `_place` hands a gateway: one complete `orders` row, keyed on its client id."""
    values = dict(
        intent_id=uuid.uuid4(), variant_id="v-t9", venue="kalshi", mode="paper",
        client_order_id=f"paper-{uuid.uuid4()}-0", ticker="KXNFLGAME-X",
        venue_market_id=1, side="yes", prob=Decimal("0.5600"),
        contracts=Decimal("10.00"), status="open", placed_at=NOW,
        expiry=NOW + timedelta(hours=2), replay=False)
    values.update(kw)
    return values


#: `uq_open_order` is unique on (venue, ticker, side, variant_id) across every resting order,
#: so a test that wants two open orders at once has to put them on two tickers.
_TICKERS = iter(f"KXNFLGAME-{n}" for n in range(1, 1000))


def _open_order(session, **kw) -> int:
    kw.setdefault("ticker", next(_TICKERS))
    order_id = store.insert_order(session, _values(**kw))
    session.flush()
    return order_id


class _Market:
    """The `market` argument, reduced to what a gateway reads off it."""

    def __init__(self, price_ranges=None) -> None:
        self.price_ranges = price_ranges


def _factory(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


# --- the seam -------------------------------------------------------------------------


def test_paper_gateway_never_touches_transport():
    g = PaperGateway()
    for name in ("_transport", "_writer", "_reader", "transport", "writer", "reader"):
        assert not hasattr(g, name)


def test_executor_builds_a_paper_gateway_by_default(env_settings, db_session):
    ex = Executor(env_settings, _factory(db_session))
    assert isinstance(ex.gateway, PaperGateway)


def test_executor_in_live_mode_cannot_build_a_gateway(env_settings, db_session):
    live = env_settings.model_copy(update={"mode": "live"})
    with pytest.raises(LiveGuardRefused):
        Executor(live, _factory(db_session))


def test_executor_version_bumped():
    assert EXECUTOR_VERSION == "4.3"      # Task 11 moved it to 4.2, fix 22 to 4.3


# --- PaperGateway ---------------------------------------------------------------------


def test_paper_gateway_place_writes_the_same_order_row(db_session):
    order_id = PaperGateway().place(db_session, _values(), None, None, NOW).order_id
    row = db_session.get(Order, order_id)
    assert row.mode == "paper" and row.venue_order_id is None
    assert row.order_group_id is None and row.exchange_index_at_place is None


def test_a_duplicate_placement_returns_none_and_skips_the_counters(db_session):
    # store.insert_order returns None on the client_order_id conflict; `_place` must return
    # before stats.placed and before the `place` event, exactly as it does today.
    values = _values()
    first = PaperGateway().place(db_session, values, None, None, NOW)
    assert first.order_id is not None
    placed = PaperGateway().place(db_session, values, None, None, NOW)
    assert placed == PlacedOrder(order_id=None)


def test_paper_gateway_cancel_returns_whether_a_row_moved(db_session):
    g = PaperGateway()
    order_id = _open_order(db_session)
    assert g.cancel(db_session, order_id, "reprice", NOW) is True
    assert g.cancel(db_session, order_id, "reprice", NOW) is False   # already cancelled


def test_paper_gateway_cancel_all_closes_every_resting_order(db_session):
    g = PaperGateway()
    ids = [_open_order(db_session) for _ in range(3)]
    g.cancel(db_session, ids[0], "reprice", NOW)
    assert g.cancel_all(db_session, "kickoff", NOW) == 2
    assert {db_session.get(Order, i).status for i in ids} == {"cancelled"}


def test_paper_gateway_has_no_amend_because_phase_3_reprices_by_cancel_and_place(db_session):
    with pytest.raises(NotImplementedError):
        PaperGateway().amend(db_session, _open_order(db_session), Decimal("0.55"),
                             Decimal("10.00"), _Market(), NOW)


def test_paper_gateway_reconcile_counts_what_we_hold(db_session):
    _open_order(db_session)
    assert PaperGateway().reconcile(db_session, NOW) == ReconcileReport(resting=1)


def test_paper_gateway_poll_fills_is_empty_and_is_not_the_daily_caps_read(db_session):
    """Paper has no venue to poll, and the cap's numbers are a different shape entirely: a
    per-variant aggregate from `store`, not the per-trade records `poll_fills` returns in live
    mode (review round 1, Important 5)."""
    order_id = _open_order(db_session)
    store.insert_fill(db_session, order_id=order_id, prob=Decimal("0.5600"),
                      contracts=Decimal("4.00"), fee=Decimal("0.0100"),
                      filled_at=NOW, simulated=True, fill_method="queue_model",
                      source_trade_id="tr-1", through=False, has_print=True, replay=False)
    db_session.flush()

    assert PaperGateway().poll_fills(db_session, SINCE) == []
    assert store.load_fills_today(db_session, replay=False, since=SINCE), \
        "the simulator's fill is still there; it is just not a gateway's to hand back"


def test_a_replay_executors_gateway_is_scoped_to_replay_rows(env_settings, db_session):
    """Review round 1, Important 1: a replay executor whose gateway was scoped to the live book
    would cancel the live book the first time Task 10 wires the kickoff flatten."""
    executor = Executor(env_settings, _factory(db_session), replay=True)
    assert executor.gateway.replay is True
    live = _open_order(db_session)
    replayed = _open_order(db_session, replay=True)

    assert executor.gateway.reconcile(db_session, NOW) == ReconcileReport(resting=1)
    assert executor.gateway.cancel_all(db_session, "kickoff", NOW) == 1

    assert db_session.get(Order, replayed).status == "cancelled"
    assert db_session.get(Order, live).status == "open"


def test_a_gateway_that_declares_no_fill_source_cannot_be_dispatched():
    """A wrapper around `PaperGateway` fails an `isinstance` test and would silently take the
    live branch, which is why the dispatch asks the gateway to declare itself."""

    class _Wrapper:
        def __init__(self, inner) -> None:
            self._inner = inner

    assert uses_the_simulator(PaperGateway()) is True
    with pytest.raises(TypeError):
        uses_the_simulator(_Wrapper(PaperGateway()))


def test_the_cancel_counters_still_move(env_settings, db_session):
    """The two counter updates the `Cancel` branch drives off the gateway's boolean."""
    ex = Executor(env_settings, _factory(db_session))
    order_id = _open_order(db_session)
    stats = ExecStats()
    ex._apply_one(db_session, Cancel(order_id=order_id, reason="reprice"),
                  {}, {}, {}, {}, NOW, stats)

    assert stats.cancelled == 1
    assert ex._metrics_acc.cancelled == {"reprice": 1}
    # The cancel event is written whether or not the row moved, so a re-applied cancel is a
    # no-op on the counters and not on the record.
    stats2 = ExecStats()
    ex._apply_one(db_session, Cancel(order_id=order_id, reason="reprice"),
                  {}, {}, {}, {}, NOW, stats2)
    assert stats2.cancelled == 0
    assert ex._metrics_acc.cancelled == {"reprice": 1}
    assert db_session.query(OrderEvent).filter_by(kind="cancel").count() == 1


# --- the dormant live path, against FakeTransport --------------------------------------


def _kalshi(transport, **kw) -> KalshiGateway:
    return KalshiGateway(_writer(transport), KalshiReader(transport),
                         order_group_id=kw.pop("order_group_id", "g1"), **kw)


def test_kalshi_gateway_place_sends_one_order_and_records_the_venue_ids(db_session):
    t = FakeTransport(queued=[_ok({"order": _echo_of(order_id="ov1", order_group_id="g1")})])
    g = _kalshi(t)
    values = _values(client_order_id="11111111-1111-1111-1111-111111111111",
                     ticker="KXNFLGAME-X", prob=Decimal("0.5600"),
                     contracts=Decimal("10.00"))

    placed = g.place(db_session, values, None, _Market(CENT_RANGES), NOW)

    assert [(c[0], c[1]) for c in t.calls] == [("POST", ORDERS_PATH)]
    assert placed.venue_order_id == "ov1" and placed.order_group_id == "g1"
    row = db_session.get(Order, placed.order_id)
    assert row.exchange_index_at_place == 0
    assert (row.mode, row.venue_order_id, row.order_group_id) == ("live", "ov1", "g1")


def test_kalshi_gateway_place_refuses_without_an_order_group(db_session):
    t = FakeTransport(queued=[])
    with pytest.raises(NoOrderGroup):
        _kalshi(t, order_group_id=None).place(db_session, _values(), None, _Market(), NOW)
    assert t.calls == []


def test_kalshi_gateway_poll_fills_reads_get_fills_not_the_simulator(db_session):
    t = FakeTransport(queued=[_ok({"fills": [{"trade_id": "t1", "order_id": "ov1",
                                              "ticker": "T", "outcome_side": "yes",
                                              "price": "0.5600", "count": "2.00"}],
                                   "cursor": ""})])
    fills = _kalshi(t).poll_fills(db_session, SINCE)
    assert [f.trade_id for f in fills] == ["t1"]
    assert t.calls[0][1] == "/portfolio/fills"


def test_kalshi_gateway_cancel_all_cancels_the_group(db_session):
    t = FakeTransport(queued=[_ok({})])
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1", order_group_id="g1")

    moved = _kalshi(t).cancel_all(db_session, "kickoff", NOW)

    assert t.calls[0][0] == "DELETE" and "order_groups" in t.calls[0][1]
    assert moved == 1 and db_session.get(Order, order_id).status == "cancelled"


def test_kalshi_gateway_cancel_sends_the_venue_cancel_then_closes_our_row(db_session):
    t = FakeTransport(queued=[_ok({"order_id": "ov1", "reduced_by": "10.00"})])
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1",
                           exchange_index_at_place=0)

    assert _kalshi(t).cancel(db_session, order_id, "reprice", NOW) is True
    assert t.calls[0][0] == "DELETE" and t.calls[0][1].endswith("/ov1")
    assert db_session.get(Order, order_id).cancel_reason == "reprice"


def test_kalshi_gateway_amend_chains_a_fresh_client_order_id_and_snaps_to_the_market_grid(
        db_session):
    """Two successive amends. V2 replaces the idempotency key on every amend, so the second one
    has to send the id the first one installed, and the price has to snap to the market's own
    grid rather than the fallback cent one (review round 1, Important 4)."""
    t = FakeTransport(queued=[_ok({"order": _echo_of(price="0.4000")}),
                              _ok({"order": _echo_of(price="0.4000")})])
    g = _kalshi(t)
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1",
                           client_order_id="placed-1", prob=Decimal("0.4500"))
    market = _Market([{"start": 0, "end": 1, "step": 0.05}])

    assert g.amend(db_session, order_id, Decimal("0.4400"), Decimal("10.00"), market, NOW)
    assert g.amend(db_session, order_id, Decimal("0.4400"), Decimal("10.00"), market, NOW)

    first, second = t.calls[0][3], t.calls[1][3]
    # 0.44 is off a 5-cent grid; the writer floors it to 0.40 and sends that, not 0.4400.
    assert first["price"] == "0.4000" and first["count"] == "10.00"
    assert first["client_order_id"] == "placed-1"
    assert second["client_order_id"] == first["updated_client_order_id"]
    assert second["updated_client_order_id"] != first["updated_client_order_id"]
    assert db_session.get(Order, order_id).client_order_id == second["updated_client_order_id"]
    assert db_session.get(Order, order_id).prob == Decimal("0.4400")


def test_kalshi_gateway_amend_returns_false_for_an_order_that_is_not_resting(db_session):
    t = FakeTransport(queued=[])
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1")
    PaperGateway().cancel(db_session, order_id, "reprice", NOW)

    assert _kalshi(t).amend(db_session, order_id, Decimal("0.44"), Decimal("10.00"),
                            _Market(), NOW) is False
    assert t.calls == []


def test_orders_by_venue_id_maps_the_venues_ids_back_to_our_rows(db_session):
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1")
    _open_order(db_session)
    assert list(orders_by_venue_id(db_session, ["ov1", None, "ov-missing"])) == ["ov1"]
    assert orders_by_venue_id(db_session, ["ov1"])["ov1"].id == order_id


def test_the_live_fill_path_writes_fills_and_a_ledger_row_and_never_simulates(
        env_settings, db_session):
    """The executor's live branch: the venue's fills are the only fill source (B-I2)."""
    t = FakeTransport(queued=[_ok({"fills": [{"trade_id": "t1", "order_id": "ov1",
                                              "ticker": "KXNFLGAME-X", "outcome_side": "yes",
                                              "price": "0.5600", "count": "4.00",
                                              "is_taker": False}],
                                   "cursor": ""})])
    ex = Executor(env_settings, _factory(db_session), gateway=_kalshi(t))
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1")
    working = store.working_orders(db_session, replay=False)
    stats = ExecStats()

    outcomes = ex._simulate(db_session, working, {}, {}, set(), NOW, stats, {"last_error": None})

    assert outcomes[order_id] == ("partially_filled", Decimal("4.00"))
    fill = db_session.query(Fill).one()
    assert (fill.fill_method, fill.simulated, fill.source_trade_id) == ("venue", False, "t1")
    assert fill.prob == Decimal("0.5600") and fill.contracts == Decimal("4.00")
    ledger = db_session.query(Ledger).one()
    assert ledger.kind == "fill" and ledger.fill_id == fill.id and ledger.cash_delta < 0
    assert stats.fills == 1


def test_a_late_venue_fill_is_recorded_but_never_resurrects_a_cancelled_order(
        env_settings, db_session):
    """The ordinary live race: our cancel and the venue's fill cross on the wire. The fill and
    its cash are real and are recorded; the order stays cancelled, because putting it back into
    `open_orders` would have the loop cancel it at the venue a second time (review round 1,
    Important 3)."""
    t = FakeTransport(queued=[_ok({"fills": [{"trade_id": "t1", "order_id": "ov1",
                                              "ticker": "KXNFLGAME-X", "outcome_side": "yes",
                                              "price": "0.5600", "count": "4.00"}],
                                   "cursor": ""})])
    ex = Executor(env_settings, _factory(db_session), gateway=_kalshi(t))
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1")
    store.cancel_order(db_session, order_id, "kickoff", NOW)
    working = store.working_orders(db_session, replay=False)

    outcomes = ex._simulate(db_session, working, {}, {}, set(), NOW, ExecStats(),
                            {"last_error": None})

    assert outcomes[order_id] == ("cancelled", Decimal("4.00"))
    row = db_session.get(Order, order_id)
    assert row.status == "cancelled" and row.filled_contracts == Decimal("4.00")
    assert db_session.query(Fill).count() == 1 and db_session.query(Ledger).count() == 1
    late = db_session.query(OrderEvent).filter_by(kind="late_fill").one()
    assert (late.order_id, late.reason, late.contracts) == (order_id, "cancelled",
                                                            Decimal("4.00"))


def test_two_venue_fills_on_one_order_in_one_poll_add_up(env_settings, db_session):
    """The running total is carried in the loop, not read back off the ORM row between fills."""
    t = FakeTransport(queued=[_ok({"fills": [
        {"trade_id": "t1", "order_id": "ov1", "ticker": "KXNFLGAME-X",
         "outcome_side": "yes", "price": "0.5600", "count": "4.00"},
        {"trade_id": "t2", "order_id": "ov1", "ticker": "KXNFLGAME-X",
         "outcome_side": "yes", "price": "0.5600", "count": "2.00"}], "cursor": ""})])
    ex = Executor(env_settings, _factory(db_session), gateway=_kalshi(t))
    order_id = _open_order(db_session, mode="live", venue_order_id="ov1")
    working = store.working_orders(db_session, replay=False)
    stats = ExecStats()

    outcomes = ex._simulate(db_session, working, {}, {}, set(), NOW, stats,
                            {"last_error": None})

    assert outcomes[order_id] == ("partially_filled", Decimal("6.00"))
    assert db_session.get(Order, order_id).filled_contracts == Decimal("6.00")
    assert stats.fills == 2


# --- the golden replay ------------------------------------------------------------------


class _LegacyGateway(PaperGateway):
    """A copy of the pre-gateway write path: the two `store` calls `_place` and `_apply_one`
    made inline before this task, written out again here and shadowing the shipped ones.
    Committed in the test so that the golden diff compares two live implementations rather than
    a snapshot that would rot.

    A `PaperGateway` subclass rather than a bare object because the fill source is part of what
    is being held fixed: before this task the queue-model simulator ran for every order, and a
    gateway the loop did not recognise as the paper one would take the live fill branch and
    change the very behaviour the diff exists to pin.
    """

    def place(self, session, values: dict, action, market, now) -> PlacedOrder:
        return PlacedOrder(order_id=store.insert_order(session, values))

    def cancel(self, session, order_id: int, reason: str, now) -> bool:
        return store.cancel_order(session, order_id, reason, now)


class _Grid:
    """The executor's two clocks, both advanced by the same step, so a replayed day is
    reproducible down to `loop_ms` (which is 0 for every step, deliberately: an injected
    monotonic that only moves when the grid does makes the heartbeat's timings a constant)."""

    def __init__(self, start: datetime, period_s: int) -> None:
        self.now, self.mono, self.period_s = start, 0.0, period_s

    def advance(self) -> None:
        self.now += timedelta(seconds=self.period_s)
        self.mono += self.period_s


#: What the executor writes and the seam could therefore move. Truncated between the two
#: passes of the golden test, which is the same per-test truncate `conftest.db_session` does
#: and acts on the branch test database at localhost:5433 alone.
_EXECUTOR_TABLES = ("intents", "orders", "order_events", "fills", "ledger", "exec_heartbeat",
                    "metric_samples", "order_watch_samples", "equity_snapshots")


def _load_and_price_the_day(session, settings) -> list[datetime]:
    """The committed fixture day's tape, normalized and priced. Returns the pricing clocks,
    which are the grid the executor is then stepped across."""
    from harness.matching.teams import seed_teams_from_espn
    from harness.normalize import runner as runner_mod
    from harness.strategy.pipeline import price_and_signal, pricing_clock_for_run
    from harness.strategy.variants import load_variants, register_variants
    from tests.fixture_day import FIXD, load_rows

    day = json.loads((FIXD / "day_synthetic" / "day.json").read_text())
    runs = load_rows(session, day)
    seed_teams_from_espn(session, "nfl",
                         json.loads((FIXD / "espn_teams_nfl.json").read_text()))
    session.commit()
    runner_mod._EVENTS.clear()
    try:
        runner_mod.normalize_new(session)
        session.commit()
        register_variants(session, load_variants(FIXD / "variants"), runs[0].started_at,
                          prune=True)
        session.commit()
        clocks = []
        for run in runs:
            clock = pricing_clock_for_run(run, settings.tick_budget_s)
            price_and_signal(session, run.id, clock, settings, budget_s=30)
            clocks.append(clock)
        session.commit()
    finally:
        runner_mod._EVENTS.clear()
    return clocks


def _run_fixture_day(session, settings, gateway, clocks: list[datetime]) -> dict:
    """Step one executor across the day's grid with `gateway`, then read back what it wrote."""
    grid = _Grid(clocks[0], settings.exec_period_s)
    executor = Executor(settings, sessionmaker(bind=session.get_bind(), expire_on_commit=False),
                        clock=lambda: grid.now, monotonic=lambda: grid.mono,
                        variants=["tiny"], gateway=gateway)
    steps = 0
    while grid.now <= clocks[-1]:
        stats = executor.step()
        assert stats.locked, "another executor holds the lock"
        assert stats.errors == 0, stats.last_error
        steps += 1
        grid.advance()
    assert steps > 1, "the day has to be more than one step for the diff to mean anything"
    session.commit()
    session.expire_all()
    return _snapshot(session)


def _snapshot(session) -> dict:
    """Everything the seam could have moved, in a form that survives new uuids and new row ids.

    `intents.id` is a client-side uuid4 and every order's `client_order_id` carries it, so the
    two passes cannot agree on those three values and no honest diff includes them. Every other
    column of the record is in here.
    """
    orders = session.query(Order).order_by(Order.placed_at, Order.ticker, Order.side,
                                           Order.prob).all()
    key = {o.id: (o.ticker, o.side, o.prob, o.placed_at) for o in orders}
    return {
        "orders": [(o.variant_id, o.venue, o.mode, o.ticker, o.venue_market_id, o.side, o.prob,
                    o.contracts, o.status, o.placed_at, o.expiry, o.cancelled_at,
                    o.cancel_reason, o.filled_contracts, o.queue_ahead_at_place,
                    o.queue_remaining, o.traded_at_price, o.book_source, o.book_age_s,
                    o.book_first_seen_at, o.edge_at_place, o.edge_min_at_place,
                    o.staleness_at_place, o.feed_kind, o.config_hash, o.gap_snapshot_id,
                    o.game_id, o.sport, o.kickoff_utc, o.match_key, o.tape_cursor_event_id,
                    o.crossed, o.nw_filled_contracts, o.nw_queue_remaining, o.nw_done,
                    o.nw_tape_cursor_event_id, o.dirty_seconds, o.dirty_minutes,
                    o.worst_case_fill, o.replay, o.venue_order_id, o.order_group_id,
                    o.exchange_index_at_place)
                   for o in orders],
        "order_events": sorted(
            (repr(key.get(e.order_id)), e.kind, e.reason, e.ts.isoformat(), str(e.prob),
             str(e.contracts), str(e.fair_p_at_event), e.replay)
            for e in session.query(OrderEvent).all()),
        "fills": sorted(
            (repr(key.get(f.order_id)), str(f.prob), str(f.contracts), str(f.fee), f.fee_type,
             f.filled_at.isoformat(), f.simulated, f.fill_method, f.source_trade_id,
             f.source_event_id, f.taker_side, f.through, f.tape_source, f.has_print, f.replay)
            for f in session.query(Fill).all()),
        "ledger": sorted(
            (repr(key.get(r.order_id)), r.kind, r.variant_id, r.ticker, r.side,
             str(r.contracts), str(r.price), str(r.fee), str(r.cash_delta), r.ts.isoformat())
            for r in session.query(Ledger).all()),
        "heartbeat": _heartbeat_counters(session),
        "metrics": sorted(
            (m.source, m.name, json.dumps(m.labels, sort_keys=True), str(m.value),
             m.ts.isoformat())
            for m in session.query(MetricSample).all()),
    }


def _heartbeat_counters(session) -> tuple:
    """Read off the model rather than through `store.read_heartbeat`, whose `age_s` is measured
    against `now()` and so differs between two passes by however long the first one took."""
    row = session.get(ExecHeartbeat, 1)
    if row is None:
        return ()
    return (row.loops, row.open_orders, row.loops_skipped, row.book_dirty_markets,
            row.last_error, row.last_loop_ms, row.p95_loop_ms, row.executor_version,
            row.last_loop_at, row.ws_last_event_at)


def _reset_the_order_side(session) -> None:
    """Between the two passes: the tape, the markets and the signals stay, everything the
    executor writes goes. The same truncate `conftest.db_session` runs between tests, against
    the branch test database at localhost:5433 (global constraint: it is the only database any
    test touches)."""
    session.commit()
    session.execute(text(f"truncate {', '.join(_EXECUTOR_TABLES)} restart identity"))
    session.commit()
    session.expire_all()


def test_golden_replay_is_byte_identical_across_the_seam(env_settings, db_session):
    """`PaperGateway` still writes what phase 3 wrote, held against a copy of those two `store`
    calls over the committed fixture day.

    What this proves: the gateway. Both passes drive the shipped `Executor`, so a change inside
    the loop moves both snapshots together and the diff stays empty -- this is a tripwire for
    later edits to `gateway.py` (Tasks 10 and 11), not a re-derivation of phase 3's behaviour.
    That the loop itself is unchanged is shown by the 50 unmodified tests in
    `tests/test_exec_loop.py` and by the one-line `_place` diff (review round 1, Important 2).

    The diff is deliberately wider than `order_events`: a dropped `stats.cancelled` or a missing
    `_metrics_acc` bump shows up only in the heartbeat counters and the metric samples (C6).
    """
    clocks = _load_and_price_the_day(db_session, env_settings)

    baseline = _run_fixture_day(db_session, env_settings, _LegacyGateway(), clocks)
    _reset_the_order_side(db_session)
    current = _run_fixture_day(db_session, env_settings, PaperGateway(), clocks)

    # Without these the comparison could be two empty lists agreeing with each other.
    assert baseline["orders"], "the fixture day has to place at least one order"
    assert baseline["order_events"], "the fixture day has to write order events"
    assert baseline["fills"], "the fixture day has to fill"
    assert baseline["ledger"], "a fill has to move cash"
    assert baseline["metrics"], "the day has to write a metric batch"
    for part in ("order_events", "orders", "fills", "ledger", "heartbeat", "metrics"):
        assert baseline[part] == current[part], part
