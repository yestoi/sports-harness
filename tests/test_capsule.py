"""The evidence capsule: bounded reads, a hashed manifest, and the caps that stop a scan.

Every test seeds a small world in the test database and asserts on bounds -- that no row
outside the window reaches a file, that the manifest's counts and digests match the bytes, and
that a capped file says so and makes the command exit 2. No test runs against anything but
`DATABASE_URL_TEST`.
"""

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from harness.capsule import CAPSULE_ROW_CAP, order_slices, period_slices, unverifiable
from harness.cli import app
from harness.db.models import OrderbookEvent

runner = CliRunner()

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
LOWER, UPPER = NOW - timedelta(hours=1), NOW + timedelta(hours=1)
TICKER, OTHER = "KXTEST-A", "KXTEST-B"


def _partitions(session):
    """The weekly partitions this file's fixed clock needs, whatever today's date is.

    `orderbook_events` and `venue_trades` are range-partitioned by week, and `tests/conftest.py`
    builds only the partitions around `datetime.now()`. A file pinned to 2026-09-11 would insert
    happily this week and raise "no partition of relation" from 2026-09-21 on, with 6B and 6C
    still running `make test`. `ensure_partitions(session, now, tables=PARTITIONED_TABLES)`
    (`harness/db/schema.py:52`) creates this week's and next week's for each table and skips
    names that already exist, so calling it for the fixture's own `NOW` is idempotent and the
    file never expires.

    One call covers everything this file writes: the oldest row is `LOWER - 3 days`
    (2026-09-08) and the newest is `UPPER + 2 hours` (2026-09-11), both inside `NOW`'s own ISO
    week 37, which runs Monday 2026-09-07 to Sunday 2026-09-13.
    """
    from harness.db.schema import ensure_partitions

    ensure_partitions(session, NOW)


def _event(session, ticker, ts, kind, *, sid=7, seq=1, raw=None):
    _partitions(session)
    session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind=kind,
                               raw=raw if raw is not None else {}))
    session.flush()


def _trade(session, ticker, ts, trade_id):
    """One `venue_trades` print. Partitioned by week on `ts`, like `orderbook_events`."""
    from harness.db.models import VenueTrade

    _partitions(session)
    session.add(VenueTrade(venue="kalshi", trade_id=trade_id, ticker=ticker, ts=ts,
                           yes_price=Decimal("0.30"), count=Decimal(5), taker_side="no",
                           taker_outcome_side="no", is_block=False, source="ws"))
    session.flush()


def test_the_tape_anchor_read_is_bounded_below(db_session):
    """The snapshot a tape anchors on may be at most two days older than the window.

    `_WS_SNAPSHOT` was bounded above only, so it opened every weekly partition of
    `orderbook_events` looking for the newest snapshot at or before `:upper` (review I3). Two
    days is one partition back at worst, so the read touches two partitions and no more; a
    ticker whose last snapshot is older than that has no anchor and the capsule says so rather
    than scanning the season to find one.
    """
    from harness.fixtures import export_ws_tape

    _event(db_session, TICKER, LOWER - timedelta(days=3), "snapshot", raw={"old": True})
    stale = export_ws_tape(db_session, TICKER, LOWER, UPPER)
    assert stale["snapshot"] is None

    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot", raw={"old": False})
    fresh = export_ws_tape(db_session, TICKER, LOWER, UPPER)
    assert fresh["snapshot"] is not None and fresh["snapshot"]["raw"] == {"old": False}


def _seed_order(session, *, order_id=1, ticker=TICKER):
    """One order with a queue-model fill, a no-watcher fill, two events, a watch sample and a
    ledger row, plus the signal -> gap snapshot -> fair value chain behind it.

    Callable more than once per test with a distinct `order_id`/`ticker` (the two-order test
    below): the dependent rows (venue market, signal, gap snapshot, intent) are keyed off
    `order_id` so a second call gets its own primary keys rather than colliding with the first.
    `config_history` is the one exception -- both orders share the strategy variant "v1", so its
    row is inserted only once.
    """
    from harness.db.models import (
        ConfigHistory, Fill, Intent, Ledger, MarketGapSnapshot, Order, OrderEvent,
        OrderWatchSample, Signal, VenueMarket,
    )
    import uuid

    # The two key spaces the capsule must not confuse: `config_history` is keyed by the 12-hex
    # strategy id (`harness/strategy/variants.py:213`), while the order carries the executor's
    # own 64-hex configuration hash (`harness/execution/plan.py:118`).
    if session.get(ConfigHistory, "v1") is None:
        session.add(ConfigHistory(config_hash="v1", config_json={"stale_s": 180}, first_seen=NOW))
    vm_id, signal_id, gap_id = order_id, 10 * order_id, 20 * order_id
    session.add(VenueMarket(id=vm_id, venue="kalshi", ticker=ticker, event_ticker="E",
                            series_ticker="S", game_id=5, market_type="moneyline",
                            first_seen_raw_id=1, last_seen_at=NOW, match_key=f"k{order_id}"))
    session.add(Signal(id=signal_id, run_id=3, variant_id="v1", venue_market_id=vm_id, side="yes",
                       decision="candidate", labels={}, replay=False, created_at=NOW,
                       gap_snapshot_id=gap_id))
    session.add(MarketGapSnapshot(id=gap_id, run_id=3, venue_market_id=vm_id, fair_value_id=None,
                                 n_groups=1, dow=4, hour_ct=12, created_at=NOW))
    intent_id = uuid.UUID(int=7 * order_id)
    session.add(Intent(id=intent_id, signal_id=signal_id, variant_id="v1", venue="kalshi",
                       venue_market_id=vm_id, ticker=ticker, side="yes",
                       signal_created_at=NOW, created_at=NOW, replay=False))
    session.add(Order(id=order_id, intent_id=intent_id, variant_id="v1", venue="kalshi",
                      client_order_id=f"client-{order_id}",
                      venue_market_id=vm_id, ticker=ticker, side="yes", prob=Decimal("0.30"),
                      contracts=Decimal(10), status="cancelled", placed_at=NOW,
                      replay=False, game_id=5, match_key=f"k{order_id}", config_hash="a" * 64))
    session.add(OrderEvent(order_id=order_id, kind="place", ts=NOW))
    session.add(Fill(order_id=order_id, prob=Decimal("0.30"), contracts=Decimal(1),
                     fee=Decimal("0.01"), filled_at=NOW, simulated=True,
                     fill_method="queue_model", replay=False))
    session.add(Fill(order_id=order_id, prob=Decimal("0.30"), contracts=Decimal(2),
                     fee=Decimal("0.02"), filled_at=NOW, simulated=True,
                     fill_method="no_watcher", replay=False))
    session.add(OrderWatchSample(order_id=order_id, ts=NOW,
                                queue_remaining=Decimal(4), book_dirty=False))
    session.add(Ledger(ts=NOW, variant_id="v1", kind="fill", order_id=order_id,
                       ticker=ticker, side="yes", cash_delta=Decimal("-0.31"), replay=False))
    session.flush()


def test_order_slices_carry_the_order_and_its_own_rows_only(db_session):
    """Every slice of an order capsule is that order's, and the tape window is the order's own.

    The window is 30 min before `placed_at` to 30 min after the last fill or the cancel,
    whichever is later (addendum §0.1): a book anchors on the newest snapshot at or before the
    instant it is asked about, and the fill simulator looks back for prints, so a window clipped
    to the order itself cannot rebuild the book the order rested in.
    """
    _seed_order(db_session)
    _seed_order(db_session, order_id=2, ticker=OTHER)
    slices = {s.table: s for s in order_slices(db_session, 1)}

    assert [r["id"] for r in slices["orders"].rows] == [1]
    assert {r["order_id"] for r in slices["fills"].rows} == {1}
    assert len(slices["fills"].rows) == 2
    assert {r["order_id"] for r in slices["order_events"].rows} == {1}
    assert {r["order_id"] for r in slices["order_watch_samples"].rows} == {1}
    assert {r["order_id"] for r in slices["ledger"].rows} == {1}
    assert [r["id"] for r in slices["signals"].rows] == [10]
    assert [r["id"] for r in slices["market_gap_snapshots"].rows] == [20]
    assert {r["ticker"] for r in slices["venue_markets"].rows} == {TICKER}
    # `config_history` is keyed by the strategy id, not by the order's executor config hash:
    # binding the 64-hex value would match nothing and the slice would be silently empty for
    # every capsule ever taken. The order's own hash is preserved on the orders row.
    assert [r["config_hash"] for r in slices["config_history"].rows] == ["v1"]
    assert slices["orders"].rows[0]["config_hash"] == "a" * 64
    # Every slice names the index it rides, or says the table is walked.
    assert all(s.index_note for s in slices.values())


def test_an_unknown_order_raises(db_session):
    """A capsule of an order that does not exist is an operator error, not an empty capsule."""
    with pytest.raises(ValueError, match="order 999"):
        order_slices(db_session, 999)
