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


def test_period_slices_stay_inside_the_window_and_name_their_tickers(db_session):
    """No row outside `[lower, upper]` and no ticker outside the named list reaches a file.

    The tape reads are per ticker; the gap read is not, because gap detection is
    subscription-level and a gap row carries `ticker = ''` as its whole-subscription sentinel
    (`harness/recorder/ws_sink.py:94`, review C1). It is bounded by `ts` alone, through the BRIN
    index, which is the same read verify.md's tape-continuity row makes.
    """
    from harness.db.models import MetricSample, OperatorEvent

    _seed_order(db_session)
    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, TICKER, NOW, "delta", seq=2)
    _event(db_session, TICKER, UPPER + timedelta(hours=2), "delta", seq=3)
    _event(db_session, OTHER, NOW, "delta", seq=4)
    _trade(db_session, TICKER, NOW, "inside")
    _trade(db_session, TICKER, UPPER + timedelta(hours=2), "outside")
    _trade(db_session, OTHER, NOW, "other-ticker")
    # The restart and recovery events the addendum requires in every period capsule, and the
    # loop metrics beside them -- one of each inside the window and one outside, so the window
    # bound on both reads is exercised rather than assumed.
    db_session.add(OperatorEvent(ts=NOW, kind="ws_disconnect", summary="inside", ref={}))
    db_session.add(OperatorEvent(ts=NOW + timedelta(minutes=1), kind="ws_connect",
                                 summary="inside", ref={}))
    db_session.add(OperatorEvent(ts=UPPER + timedelta(hours=2), kind="ws_disconnect",
                                 summary="outside", ref={}))
    db_session.add(MetricSample(ts=NOW, source="exec", name="exec.loop_ms",
                                labels={}, value=Decimal("900")))
    db_session.add(MetricSample(ts=UPPER + timedelta(hours=2), source="exec",
                                name="exec.loop_ms", labels={}, value=Decimal("30000")))
    # A name outside CAPSULE_METRIC_NAMES, inside the window: the explicit list is a filter, not
    # a prefix, so this row must not be taken.
    db_session.add(MetricSample(ts=NOW, source="exec", name="report.rows",
                                labels={}, value=Decimal("1")))
    db_session.flush()

    slices = {s.table: s for s in period_slices(db_session, [TICKER], LOWER, UPPER)}

    events = slices["orderbook_events"].rows
    # The anchoring snapshot is deliberately outside the window -- a book anchors on the newest
    # snapshot at or before the window's start -- so the bound is asserted on the deltas.
    assert all(LOWER <= r["ts"] <= UPPER for r in events if r["kind"] == "delta")
    assert [r["ts"] for r in events if r["kind"] == "snapshot"] == [LOWER - timedelta(hours=6)]
    assert {r["ticker"] for r in events} == {TICKER}
    trades = slices["venue_trades"].rows
    assert [r["trade_id"] for r in trades] == ["inside"]
    assert all(LOWER <= r["ts"] <= UPPER for r in trades)
    assert {r["ticker"] for r in trades} == {TICKER}
    assert all(LOWER <= r["placed_at"] <= UPPER for r in slices["orders"].rows)
    assert {r["ticker"] for r in slices["venue_markets"].rows} == {TICKER}

    operator = slices["operator_events"].rows
    assert sorted(r["kind"] for r in operator) == ["ws_connect", "ws_disconnect"]
    assert all(LOWER <= r["ts"] <= UPPER for r in operator)
    assert "outside" not in {r["summary"] for r in operator}

    metrics = slices["metric_samples"].rows
    assert [r["name"] for r in metrics] == ["exec.loop_ms"]
    assert all(LOWER <= r["ts"] <= UPPER for r in metrics)

    assert slices["metric_samples"].index_note.startswith("ix_metric_samples_name_ts")
    assert all(s.index_note for s in slices.values())


def test_a_gap_row_in_the_window_makes_the_slice_unverifiable(db_session):
    """A window whose subscription lost a frame cannot be replayed from its own tape.

    The capsule keeps it and marks it, rather than dropping it: U8's rule is "a slice without
    tape or transitions is marked unverifiable". The entry carries the `sid` and the `ts`,
    because the gap invalidates every ticker on that subscription, not only the one that
    exposed it.
    """
    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, "", NOW, "gap", sid=7, seq=9,
           raw={"sid": 7, "expected": 8, "got": 9, "exposed_by": TICKER})
    slices = period_slices(db_session, [TICKER], LOWER, UPPER)
    entries = unverifiable(slices, [TICKER])
    assert [e["reason"] for e in entries] == ["gap"]
    assert entries[0]["sid"] == 7 and entries[0]["ts"] == NOW


def test_a_ticker_with_no_anchoring_snapshot_is_unverifiable(db_session):
    """No snapshot within two days of the window's start means no book to start from."""
    _event(db_session, TICKER, NOW, "delta", seq=2)
    slices = period_slices(db_session, [TICKER], LOWER, UPPER)
    entries = unverifiable(slices, [TICKER])
    assert [(e["ticker"], e["reason"]) for e in entries] == [(TICKER, "no anchor")]


def test_merge_slices_takes_each_row_once(db_session):
    """The order path's two selectors overlap, and the capsule must not carry the overlap twice.

    An order capsule reads its own order by id and then reads the window's orders by
    `placed_at` — and its own order is inside its own window, as are its fills, its events, its
    ledger rows and its market. Concatenating the two selectors would give every one of those
    rows twice, in a file whose manifest count and sha256 faithfully attest to the duplication.
    The period read stays on the order path: 6B wants the ticker's tape and the other orders
    working the same window, which only that selector brings.
    """
    from harness.capsule import merge_slices, order_window

    _seed_order(db_session)
    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, TICKER, NOW, "delta", seq=2)
    own = order_slices(db_session, 1)
    rows = {s.table: s.rows for s in own}
    lower, upper = order_window(rows["orders"][0], rows["fills"])
    merged = {s.table: s.rows for s in merge_slices(
        own + period_slices(db_session, [TICKER], lower, upper))}

    assert [r["id"] for r in merged["orders"]] == [1]
    assert len(merged["fills"]) == len({r["id"] for r in merged["fills"]}) == 2
    assert len(merged["venue_markets"]) == 1
    for table in ("order_events", "ledger"):
        assert len(merged[table]) == len({r["id"] for r in merged[table]})


def test_write_capsule_hashes_and_counts_every_file(db_session, tmp_path):
    """The manifest is the capsule's own audit: counts and digests must match the bytes.

    A capsule whose manifest says 12 rows and whose file holds 11 is worse than no capsule,
    because 6B would reason from it. The digest is over the gzipped bytes as written.
    """
    from harness.capsule import write_capsule

    _seed_order(db_session)
    slices = order_slices(db_session, 1)
    out = tmp_path / "capsule-order-1"
    manifest = write_capsule(slices, str(out), {"selector": {"order": 1}, "build": "abc1234"})

    assert manifest["build"] == "abc1234"
    assert manifest["row_cap"] == CAPSULE_ROW_CAP
    assert manifest["truncated"] == []
    for entry in manifest["files"]:
        path = out / entry["name"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        lines = gzip.decompress(raw).decode().strip().splitlines()
        assert len(lines) == entry["rows"]
        assert all(json.loads(line) for line in lines) or entry["rows"] == 0
        assert entry["sql"] and entry["index_note"]
    stored = json.loads((out / "manifest.json").read_text())
    assert stored == manifest


def test_a_capped_file_is_marked_truncated_with_its_last_id(db_session, tmp_path):
    """A file that reached its cap is written and says so; it never pretends to be complete."""
    from harness.capsule import write_capsule

    _seed_order(db_session)
    slices = order_slices(db_session, 1, cap=1)
    fills = next(s for s in slices if s.table == "fills")
    assert fills.truncated is True and fills.rows and fills.last_id is not None
    manifest = write_capsule(slices, str(tmp_path / "c"), {"selector": {"order": 1}})
    assert "fills" in manifest["truncated"]
