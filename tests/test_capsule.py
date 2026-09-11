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
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind=kind,
                         raw=raw if raw is not None else {})
    session.add(row)
    session.flush()
    return row.id


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


def _seed_order(session, *, order_id=1, ticker=TICKER, ts=NOW):
    """One order with a queue-model fill, a no-watcher fill, two events, a watch sample and a
    ledger row, plus the signal -> gap snapshot -> fair value chain behind it.

    Callable more than once per test with a distinct `order_id`/`ticker` (the two-order test
    below): the dependent rows (venue market, signal, gap snapshot, intent) are keyed off
    `order_id` so a second call gets its own primary keys rather than colliding with the first.
    `config_history` is the one exception -- both orders share the strategy variant "v1", so its
    row is inserted only once. `ts` defaults to `NOW` but can be moved outside a test's window,
    so the period test can seed a whole order (and its fills) that a window bound must exclude
    (review M5).
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
                       decision="candidate", labels={}, replay=False, created_at=ts,
                       gap_snapshot_id=gap_id))
    session.add(MarketGapSnapshot(id=gap_id, run_id=3, venue_market_id=vm_id, fair_value_id=None,
                                 n_groups=1, dow=4, hour_ct=12, created_at=ts))
    intent_id = uuid.UUID(int=7 * order_id)
    session.add(Intent(id=intent_id, signal_id=signal_id, variant_id="v1", venue="kalshi",
                       venue_market_id=vm_id, ticker=ticker, side="yes",
                       signal_created_at=ts, created_at=ts, replay=False))
    session.add(Order(id=order_id, intent_id=intent_id, variant_id="v1", venue="kalshi",
                      client_order_id=f"client-{order_id}",
                      venue_market_id=vm_id, ticker=ticker, side="yes", prob=Decimal("0.30"),
                      contracts=Decimal(10), status="cancelled", placed_at=ts,
                      replay=False, game_id=5, match_key=f"k{order_id}", config_hash="a" * 64))
    session.add(OrderEvent(order_id=order_id, kind="place", ts=ts))
    session.add(Fill(order_id=order_id, prob=Decimal("0.30"), contracts=Decimal(1),
                     fee=Decimal("0.01"), filled_at=ts, simulated=True,
                     fill_method="queue_model", replay=False))
    session.add(Fill(order_id=order_id, prob=Decimal("0.30"), contracts=Decimal(2),
                     fee=Decimal("0.02"), filled_at=ts, simulated=True,
                     fill_method="no_watcher", replay=False))
    session.add(OrderWatchSample(order_id=order_id, ts=ts,
                                queue_remaining=Decimal(4), book_dirty=False))
    session.add(Ledger(ts=ts, variant_id="v1", kind="fill", order_id=order_id,
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
    from harness.db.models import FairValue, MarketGapSnapshot, MetricSample, OperatorEvent

    _seed_order(db_session)
    # A whole second order outside the window -- its own placed_at, fills, gap snapshot and
    # signal are all outside [LOWER, UPPER] -- so the orders/fills bound assertions below have
    # an actual candidate to exclude rather than passing vacuously (review M5).
    _seed_order(db_session, order_id=2, ticker=OTHER, ts=UPPER + timedelta(hours=2))
    # A second gap snapshot on TICKER's own market (venue_market_id=1, same as order 1's),
    # outside the window: `_GAPS_BY_MARKET` is a `created_at` range on that market, and without
    # this row the "one gap snapshot" assertion below would hold even with no bound at all.
    # uq_gap_run_market keys on (run_id, venue_market_id), not created_at, so this row needs its
    # own run_id to coexist with order 1's gap snapshot (run_id=3) on the same market.
    db_session.add(MarketGapSnapshot(id=99, run_id=4, venue_market_id=1, fair_value_id=None,
                                     n_groups=1, dow=4, hour_ct=12,
                                     created_at=UPPER + timedelta(hours=2)))
    # Two fair_values rows on TICKER's own game/market_type (game_id=5, "moneyline", from
    # _seed_order's venue market): one inside the window, one outside. uq_fair_value_row keys on
    # (run_id, game_id, market_type, outcome_team_id, outcome_side, threshold, fair_source), not
    # created_at, so the two rows need distinct run_ids to coexist.
    db_session.add(FairValue(run_id=3, game_id=5, market_type="moneyline", fair_p=Decimal("0.55"),
                             fair_source="model", created_at=NOW))
    db_session.add(FairValue(run_id=4, game_id=5, market_type="moneyline", fair_p=Decimal("0.60"),
                             fair_source="model", created_at=UPPER + timedelta(hours=2)))
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
    # Order 2 (placed_at outside the window) must not reach either file: an "all()" bound alone
    # would hold even with no filter at all if nothing outside the window existed to exclude
    # (review M5).
    assert [r["id"] for r in slices["orders"].rows] == [1]
    assert all(LOWER <= r["placed_at"] <= UPPER for r in slices["orders"].rows)
    assert {r["order_id"] for r in slices["fills"].rows} == {1}
    assert len(slices["fills"].rows) == 2
    assert {r["ticker"] for r in slices["venue_markets"].rows} == {TICKER}

    # TICKER's own market has two gap snapshots, one inside the window (id 20, from
    # _seed_order) and one outside (id 99); only the inside one reaches the file.
    assert [r["id"] for r in slices["market_gap_snapshots"].rows] == [20]
    assert all(LOWER <= r["created_at"] <= UPPER for r in slices["market_gap_snapshots"].rows)
    # Two fair_values rows share TICKER's game/market_type; only the in-window one reaches the
    # file.
    assert len(slices["fair_values"].rows) == 1
    assert all(LOWER <= r["created_at"] <= UPPER for r in slices["fair_values"].rows)

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


def test_the_orderbook_events_bookmark_is_the_highest_delta_not_the_snapshot(db_session):
    """`last_id` must point at a delta row: a resume reads deltas from it, never a snapshot.

    `events` is built deltas-then-snapshot per ticker, so the naive `events[-1]["id"]` is always
    the snapshot -- usually the *oldest* row in the slice, since a book anchors on the newest
    snapshot at or before the window's start (review I2). Seeded so the snapshot's id is lower
    than both deltas', which the naive bookmark would still misreport regardless of insertion
    order, since it always picks the last-appended element of a deltas-then-snapshot list.
    """
    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, TICKER, NOW, "delta", seq=2)
    last_delta_id = _event(db_session, TICKER, NOW + timedelta(minutes=1), "delta", seq=3)

    slices = {s.table: s for s in period_slices(db_session, [TICKER], LOWER, UPPER)}
    assert slices["orderbook_events"].last_id == last_delta_id


def test_the_orderbook_events_bookmark_is_none_across_several_tickers(db_session):
    """One integer cannot bookmark two tickers' independent truncation points (review I2): a
    resume from either ticker's own last id would silently skip the other's remaining deltas.
    """
    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, TICKER, NOW, "delta", seq=2)
    _event(db_session, OTHER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, OTHER, NOW, "delta", seq=2)

    slices = {s.table: s for s in period_slices(db_session, [TICKER, OTHER], LOWER, UPPER)}
    assert slices["orderbook_events"].last_id is None


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
    manifest = write_capsule(slices, str(tmp_path / "c"), {"selector": {"order": 1}}, cap=1)
    assert "fills" in manifest["truncated"]
    assert manifest["row_cap"] == 1


def test_the_manifest_records_the_cap_actually_in_force_not_the_module_default(db_session,
                                                                               tmp_path):
    """`row_cap` must be the cap the reads ran under, or a reader concludes the wrong ceiling
    was hit (review I1): a one-row file under `--cap 1` must not be reported beside `150000`.
    """
    from harness.capsule import write_capsule

    _seed_order(db_session)
    slices = order_slices(db_session, 1, cap=1)
    manifest = write_capsule(slices, str(tmp_path / "capped"), {"selector": {"order": 1}}, cap=1)
    assert manifest["row_cap"] == 1

    slices = order_slices(db_session, 1)
    manifest = write_capsule(slices, str(tmp_path / "default"), {"selector": {"order": 1}})
    assert manifest["row_cap"] == CAPSULE_ROW_CAP


def test_write_capsule_streams_one_member_at_a_time(db_session, tmp_path, monkeypatch):
    """The writer must not hold every slice's gzipped bytes before writing any of them
    (review I3): the cap bounds one slice, not the capsule, and a period capsule over several
    tickers can hold hundreds of megabytes if every member is gzipped before the first is
    written.

    Instruments `_jsonl_gz` and the per-file write to record call order. A batching
    implementation calls `_jsonl_gz` for every slice before writing any of them; a streaming one
    interleaves gz(A), write(A), gz(B), write(B), ... -- each gzip call immediately followed by
    its own write, never by another gzip call.
    """
    import harness.capsule as capsule_mod

    _seed_order(db_session)
    slices = order_slices(db_session, 1)
    assert len(slices) > 3  # the order chain has several tables; the order matters

    calls: list[str] = []
    real_jsonl_gz = capsule_mod._jsonl_gz

    def spy_jsonl_gz(rows):
        calls.append("gz")
        return real_jsonl_gz(rows)

    monkeypatch.setattr(capsule_mod, "_jsonl_gz", spy_jsonl_gz)

    from pathlib import Path
    real_write_bytes = Path.write_bytes

    def spy_write_bytes(self, data):
        if self.suffix == ".gz":
            calls.append("write")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", spy_write_bytes)

    capsule_mod.write_capsule(slices, str(tmp_path / "stream"), {"selector": {"order": 1}})

    assert calls, "the spies never fired"
    # Every "gz" must be immediately followed by its own "write": never two "gz" calls in a row.
    pairs = [calls[i:i + 2] for i in range(0, len(calls), 2)]
    assert all(pair == ["gz", "write"] for pair in pairs)


def test_capsule_command_writes_a_directory_and_exits_zero(monkeypatch, env_settings,
                                                           db_session, tmp_path):
    """The whole command over one order, on the test database."""
    from harness.config.settings import get_settings

    _seed_order(db_session)
    db_session.commit()
    monkeypatch.setenv("DATABASE_URL", db_session.get_bind().url.render_as_string(
        hide_password=False))
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    get_settings.cache_clear()
    try:
        out = tmp_path / "order-1"
        result = runner.invoke(app, ["capsule", "--order", "1", "--out", str(out),
                                     "--main-sha", "abc1234",
                                     "--healthz-build", "abc1234",
                                     "--worktrees", "/Users/trey/dev/sports  abc1234 [main]"])
        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["selector"] == {"order": 1}
        assert manifest["identity"]["main_sha"] == "abc1234"
        assert manifest["identity"]["build_mismatch"] is False
        assert manifest["counts"]["orders"] == 1
    finally:
        get_settings.cache_clear()


def test_capsule_command_flags_a_healthz_build_mismatch(monkeypatch, env_settings,
                                                         db_session, tmp_path):
    """`build_mismatch` must catch a `/healthz` disagreement too, not only `--main-sha`
    (review M1): the container's env and its own `/healthz` should agree by construction, but a
    capsule taken while they do not must not be marked clean."""
    from harness.config.settings import get_settings

    _seed_order(db_session)
    db_session.commit()
    monkeypatch.setenv("DATABASE_URL", db_session.get_bind().url.render_as_string(
        hide_password=False))
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    get_settings.cache_clear()
    try:
        out = tmp_path / "order-1-healthz-mismatch"
        result = runner.invoke(app, ["capsule", "--order", "1", "--out", str(out),
                                     "--main-sha", "abc1234", "--healthz-build", "def5678"])
        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["identity"]["build_mismatch"] is True
    finally:
        get_settings.cache_clear()


def test_capsule_command_exits_two_when_a_file_hits_the_cap(monkeypatch, env_settings,
                                                            db_session, tmp_path):
    """Exit 2 is the signal to narrow the window, not a crash: the files are still written."""
    from harness.config.settings import get_settings

    _seed_order(db_session)
    db_session.commit()
    monkeypatch.setenv("DATABASE_URL", db_session.get_bind().url.render_as_string(
        hide_password=False))
    get_settings.cache_clear()
    try:
        out = tmp_path / "capped"
        result = runner.invoke(app, ["capsule", "--order", "1", "--out", str(out), "--cap", "1"])
        assert result.exit_code == 2, result.output
        manifest = json.loads((out / "manifest.json").read_text())
        assert "fills" in manifest["truncated"]
        # The manifest must record the cap actually in force, not the module default
        # (review I1): a one-row file under --cap 1 must not be reported beside 150000.
        assert manifest["row_cap"] == 1
    finally:
        get_settings.cache_clear()


def test_capsule_command_exits_one_on_an_unknown_order(monkeypatch, env_settings,
                                                       db_session, tmp_path):
    from harness.config.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", db_session.get_bind().url.render_as_string(
        hide_password=False))
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["capsule", "--order", "999",
                                     "--out", str(tmp_path / "x")])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()


def test_capsule_command_period_branch_writes_the_selector_and_window(monkeypatch, env_settings,
                                                                       db_session, tmp_path):
    """The `--period` branch end to end: argument validation, `_utc` instant parsing, and the
    selector shape task 5's runbook and task 6's verification row both read (review I4). All
    three CLI tests before this one exercise only `--order`.
    """
    from harness.config.settings import get_settings

    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot")
    _event(db_session, TICKER, NOW, "delta", seq=2)
    db_session.commit()
    monkeypatch.setenv("DATABASE_URL", db_session.get_bind().url.render_as_string(
        hide_password=False))
    get_settings.cache_clear()
    try:
        out = tmp_path / "period-clean"
        result = runner.invoke(app, ["capsule", "--period", "clean",
                                     "--from", LOWER.isoformat(), "--to", UPPER.isoformat(),
                                     "--ticker", TICKER, "--out", str(out)])
        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["selector"] == {"period": "clean", "from": LOWER.isoformat(),
                                        "to": UPPER.isoformat(), "tickers": [TICKER]}
        assert manifest["window"] == {"from": LOWER.isoformat(), "to": UPPER.isoformat()}
        assert manifest["counts"]["orderbook_events"] >= 1
    finally:
        get_settings.cache_clear()


def test_capsule_command_period_without_a_ticker_exits_one(monkeypatch, env_settings,
                                                            db_session, tmp_path):
    """`--period` needs `--from`, `--to` and at least one `--ticker`; a run missing `--ticker`
    is an operator error, not an empty capsule (review I4)."""
    from harness.config.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", db_session.get_bind().url.render_as_string(
        hide_password=False))
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["capsule", "--period", "clean",
                                     "--from", LOWER.isoformat(), "--to", UPPER.isoformat(),
                                     "--out", str(tmp_path / "x")])
        assert result.exit_code == 1
    finally:
        get_settings.cache_clear()
