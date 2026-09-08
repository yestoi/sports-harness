import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from harness.db.models import Base
from harness.db.schema import (PARTITIONED_TABLES, create_schema, drop_schema, ensure_partitions,
                               week_bounds)

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_week_bounds_monday_to_monday_utc():
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wednesday
    start, end = week_bounds(now)
    assert start == datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 14, tzinfo=timezone.utc)


def test_week_bounds_rejects_naive_datetime():
    with pytest.raises(ValueError):
        week_bounds(datetime(2026, 9, 9, 23, 0))


def test_ensure_partitions_creates_two_weeks(db_session):
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    # The schema is session-scoped now, so an earlier test (or the fixture itself) may already
    # hold this week's partitions; start from a known-empty state for the two weeks under test.
    for name in ("raw_responses_y2026w37", "raw_responses_y2026w38"):
        db_session.execute(text(f"drop table if exists {name}"))
    db_session.commit()
    created = ensure_partitions(db_session, now)
    assert created == ["raw_responses_y2026w37", "raw_responses_y2026w38"]
    again = ensure_partitions(db_session, now)
    assert again == []
    names = db_session.execute(
        text("select inhrelid::regclass::text from pg_inherits where inhparent = 'raw_responses'::regclass")
    ).scalars().all()
    assert set(names) >= {"raw_responses_y2026w37", "raw_responses_y2026w38"}


def test_ensure_partitions_creates_two_weeks_for_three_tables(db_session):
    """Task 2b widens the weekly partitions to the two tape tables. Every partitioned table needs
    this week's and next week's partition ahead of the writers, or an insert fails outright with
    "no partition of relation ... found for row"."""
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    assert PARTITIONED_TABLES == ("raw_responses", "orderbook_events", "venue_trades")
    expected = [f"{table}_y2026w{week}" for table in PARTITIONED_TABLES for week in ("37", "38")]
    for name in expected:
        db_session.execute(text(f"drop table if exists {name}"))
    db_session.commit()

    assert ensure_partitions(db_session, now) == expected
    assert ensure_partitions(db_session, now) == []
    for table in PARTITIONED_TABLES:
        names = db_session.execute(text(
            "select inhrelid::regclass::text from pg_inherits where inhparent = cast(:t as regclass)"),
            {"t": table}).scalars().all()
        assert {f"{table}_y2026w37", f"{table}_y2026w38"} <= set(names), (table, names)


def test_create_schema_creates_the_obe_indexes_on_a_partitioned_table(db_session):
    """The phase 3 book loader reads the newest snapshot per ticker (`ix_obe_snapshot`), applies
    deltas by id (`ix_obe_ticker_id`) and looks for a later gap on the anchor's sid (`ix_obe_gap`);
    the REST trade writer dedupes through `ix_trades_venue_trade_id`. Once the tape tables are
    partitioned, create_schema builds all four; the build recurses into each partition, which is
    why it is guarded on a live unpartitioned table and idempotent everywhere else."""
    engine = db_session.get_bind()
    wanted = {"ix_obe_snapshot", "ix_obe_ticker_id", "ix_obe_gap", "ix_trades_venue_trade_id"}
    for name in sorted(wanted):
        db_session.execute(text(f"drop index if exists {name}"))
    db_session.commit()
    assert not wanted & set(db_session.execute(text(
        "select indexname from pg_indexes where schemaname='public'")).scalars())

    create_schema(engine)

    assert wanted <= set(db_session.execute(text(
        "select indexname from pg_indexes where schemaname='public'")).scalars())


def test_raw_insert_roundtrip(db_session):
    from harness.db.models import RawResponse, Run

    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    ensure_partitions(db_session, now)
    run = Run(started_at=now, status="running")
    db_session.add(run)
    db_session.flush()
    row = RawResponse(run_id=run.id, source="kalshi", endpoint="/markets", params={"series_ticker": "KXNFLGAME"},
                      fetched_at=now, http_status=200, body={"markets": []})
    db_session.add(row)
    db_session.flush()
    got = db_session.get(RawResponse, (row.id, row.fetched_at))
    assert got.body == {"markets": []}


def test_create_schema_adds_no_fair_reason_to_an_existing_table(db_session):
    """`init-db` (the only schema entrypoint; no migrations framework) must be safe to rerun
    against a database that predates the no_fair_reason column -- e.g. the NAS deployment,
    which already has `market_gap_snapshots` without it. `create_all` alone would not add the
    column to an existing table, so `create_schema` also runs an idempotent `ALTER TABLE`."""
    engine = db_session.get_bind()

    def has_column() -> bool:
        return db_session.execute(text(
            "select 1 from information_schema.columns "
            "where table_name = 'market_gap_snapshots' and column_name = 'no_fair_reason'"
        )).first() is not None

    assert has_column()  # the db_session fixture already ran create_schema once

    db_session.execute(text("alter table market_gap_snapshots drop column no_fair_reason"))
    db_session.commit()
    assert not has_column()

    create_schema(engine)
    db_session.commit()
    assert has_column()

    # idempotent: rerunning again against a table that already has the column is a no-op.
    create_schema(engine)
    assert has_column()


def test_create_schema_adds_feed_columns(db_session):
    """The F11 staleness-amendment columns must be addable to a database that predates them,
    the same way `no_fair_reason` is (see the test above)."""
    engine = db_session.get_bind()
    fair_cols = ("feed_kind", "feed_lag_s", "stale_allowance_s", "pricing_version")
    gap_cols = ("feed_kind", "feed_lag_s", "stale_allowance_s")

    def present(table: str, cols: tuple[str, ...]) -> set[str]:
        return set(db_session.execute(text(
            "select column_name from information_schema.columns "
            "where table_name = :t and column_name = any(:cols)"
        ), {"t": table, "cols": list(cols)}).scalars().all())

    assert present("fair_values", fair_cols) == set(fair_cols)
    assert present("market_gap_snapshots", gap_cols) == set(gap_cols)

    db_session.execute(text(
        "alter table fair_values drop column feed_kind, drop column feed_lag_s, "
        "drop column stale_allowance_s, drop column pricing_version"
    ))
    db_session.execute(text(
        "alter table market_gap_snapshots drop column feed_kind, drop column feed_lag_s, "
        "drop column stale_allowance_s"
    ))
    db_session.commit()
    assert present("fair_values", fair_cols) == set()
    assert present("market_gap_snapshots", gap_cols) == set()

    create_schema(engine)
    db_session.commit()
    assert present("fair_values", fair_cols) == set(fair_cols)
    assert present("market_gap_snapshots", gap_cols) == set(gap_cols)

    # idempotent: rerunning again against tables that already have the columns is a no-op.
    create_schema(engine)
    assert present("fair_values", fair_cols) == set(fair_cols)
    assert present("market_gap_snapshots", gap_cols) == set(gap_cols)


def test_create_schema_adds_brin_time_indexes(db_session):
    """Dashboard 'last hour' counts on the append-only event/trade tables must not seq-scan."""
    names = {r[0] for r in db_session.execute(text(
        "select indexname from pg_indexes where indexname in ('ix_obe_ts_brin', 'ix_trades_ts_brin')")).all()}
    assert names == {"ix_obe_ts_brin", "ix_trades_ts_brin"}


# ---------------------------------------------------------------------------
# Phase 3 (addendum §5): execution, settlement, benchmark and CLV tables.
# ---------------------------------------------------------------------------

PHASE3_TABLES = {
    "intents", "orders", "order_events", "fills", "markouts", "settlements", "venue_settlements",
    "benchmarks", "gap_outcomes", "order_clv", "ledger", "exec_heartbeat", "gate_reports",
    "job_runs", "job_state",
}


def _order(session, **kw):
    """Insert one order with the not-null columns filled in; kw overrides any of them."""
    from harness.db.models import Order

    row = dict(intent_id=uuid.uuid4(), variant_id="sharp_direct", venue="kalshi",
               client_order_id=str(uuid.uuid4()), ticker="T", venue_market_id=1, side="yes",
               prob=Decimal("0.5000"), contracts=Decimal("10.00"), status="open", placed_at=NOW)
    row.update(kw)
    order = Order(**row)
    session.add(order)
    session.flush()
    return order


def test_phase3_tables_exist(db_session):
    names = set(db_session.execute(text(
        "select tablename from pg_tables where schemaname='public'")).scalars())
    assert PHASE3_TABLES <= names, sorted(PHASE3_TABLES - names)


def test_uq_open_order_rejects_second_open_and_allows_after_cancel(db_session):
    """One live order per (venue, ticker, side, variant); replays are exempt so a replay run can
    re-simulate a market the live executor is working."""
    _order(db_session)
    with pytest.raises(IntegrityError):
        _order(db_session)
    db_session.rollback()

    first = _order(db_session)
    first.status = "cancelled"
    first.cancelled_at = NOW
    first.cancel_reason = "reprice"
    db_session.flush()
    second = _order(db_session)
    assert second.id != first.id

    _order(db_session, replay=True)
    _order(db_session, replay=True)
    db_session.flush()


def test_benchmark_unique_dedupes_null_bearing_keys(db_session):
    from harness.db.models import Benchmark

    row = dict(game_id=1, market_type="moneyline", outcome_team_id=None, outcome_side=None,
               threshold=None, benchmark_type="close", p=Decimal("0.5500"), target_ts=NOW,
               created_at=NOW)
    db_session.add(Benchmark(**row))
    db_session.flush()
    db_session.add(Benchmark(**row))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(Benchmark(**row))
    db_session.add(Benchmark(**{**row, "benchmark_type": "kickoff"}))
    db_session.flush()


def test_fill_unique_key_dedupes_null_sources(db_session):
    from harness.db.models import Fill

    order = _order(db_session)
    row = dict(order_id=order.id, prob=Decimal("0.5000"), contracts=Decimal("3.00"),
               fee=Decimal("0.0044"), filled_at=NOW, fill_method="queue_model",
               source_trade_id=None, source_event_id=None)
    db_session.add(Fill(**row))
    db_session.flush()
    db_session.add(Fill(**row))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    order = _order(db_session)
    row["order_id"] = order.id
    db_session.add(Fill(**row))
    db_session.add(Fill(**{**row, "source_trade_id": "t1"}))
    db_session.add(Fill(**{**row, "fill_method": "snapshot_cross"}))
    db_session.flush()


def test_skip_once_index_dedupes_by_intent_kind_reason(db_session):
    from harness.db.models import OrderEvent

    intent_id = uuid.uuid4()
    row = dict(order_id=None, intent_id=intent_id, ts=NOW, kind="skipped", reason="no_book")
    db_session.add(OrderEvent(**row))
    db_session.flush()
    db_session.add(OrderEvent(**{**row, "ts": NOW + timedelta(minutes=1)}))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.add(OrderEvent(**row))
    db_session.add(OrderEvent(**{**row, "reason": "stale_book"}))
    db_session.add(OrderEvent(**{**row, "kind": "cap_gate"}))
    # `place` is outside the partial index, so the same (intent, kind, reason) may repeat.
    order = _order(db_session)
    db_session.add(OrderEvent(order_id=order.id, intent_id=intent_id, ts=NOW, kind="place"))
    db_session.add(OrderEvent(order_id=order.id, intent_id=intent_id,
                              ts=NOW + timedelta(seconds=1), kind="place"))
    db_session.flush()


def test_uq_order_event_dedupes_by_order_kind_ts(db_session):
    from harness.db.models import OrderEvent

    order = _order(db_session)
    row = dict(order_id=order.id, intent_id=None, ts=NOW, kind="place")
    db_session.add(OrderEvent(**row))
    db_session.flush()
    db_session.add(OrderEvent(**row))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_views_select_on_empty_tables(db_session):
    for view in ("positions", "clv", "order_episodes"):
        assert db_session.execute(text(f"select * from {view}")).all() == []


def test_positions_view_sums_queue_model_fills_of_live_orders(db_session):
    """The dashboard's open position. snapshot_cross and no_watcher fills are counterfactuals,
    a settled order is no longer a position, and replay orders are a separate book."""
    from harness.db.models import Fill

    live = _order(db_session)
    settled = _order(db_session, ticker="T-SETTLED", status="settled")
    replayed = _order(db_session, ticker="T-REPLAY", replay=True)

    def fill(order, contracts, prob, method="queue_model"):
        db_session.add(Fill(order_id=order.id, prob=Decimal(prob), contracts=Decimal(contracts),
                            fee=Decimal("0.0044"), filled_at=NOW, fill_method=method,
                            source_trade_id=f"{order.id}-{method}-{contracts}"))

    fill(live, "10.00", "0.4000")
    fill(live, "30.00", "0.5000")
    fill(live, "50.00", "0.9000", method="no_watcher")
    fill(settled, "10.00", "0.5000")
    fill(replayed, "10.00", "0.5000")
    db_session.flush()

    rows = db_session.execute(text(
        "select variant_id, ticker, side, open_contracts, avg_price from positions")).all()
    assert [tuple(r) for r in rows] == [
        ("sharp_direct", "T", "yes", Decimal("40.00"), Decimal("0.475")),
    ]


def test_create_schema_is_idempotent(db_session):
    engine = db_session.get_bind()
    db_session.commit()
    create_schema(engine)
    create_schema(engine)
    names = set(db_session.execute(text(
        "select tablename from pg_tables where schemaname='public'")).scalars())
    assert PHASE3_TABLES <= names


def test_create_schema_runs_ddl_in_autocommit_with_lock_timeout(db_session):
    """Carried fix 13, from a deploy that deadlocked. Every DDL statement must run in its own
    autocommit transaction under a lock_timeout: one long transaction holds the AccessExclusiveLock
    that `alter table venue_trades ...` takes until commit, and the later `create index` on
    orderbook_events then queues behind the WebSocket sink's open insert batch, whose next insert
    into venue_trades waits on init-db."""
    from harness.db import schema as schema_mod

    engine = db_session.get_bind()
    db_session.commit()
    seen: list[tuple[str, bool, int]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        raw = conn.connection.dbapi_connection
        seen.append((statement.strip().lower(), bool(raw.autocommit), int(raw.info.transaction_status)))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        create_schema(engine)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert schema_mod.DDL_LOCK_TIMEOUT == "5s"
    assert [s for s, _, _ in seen if s.startswith("set lock_timeout")] == ["set lock_timeout = '5s'"]
    ddl = [(s, autocommit, txn) for s, autocommit, txn in seen
           if s.startswith(("alter table", "create index", "create unique index",
                            "create or replace view", "update venue_markets"))]
    # 24 column ALTERs + 18 indexes + 3 views + 1 match_key backfill + 4 tape statements + the
    # 4 tape indexes Task 2b guards behind "partitioned, or still empty" (both tape tables are
    # partitioned here, so all four run).
    assert len(ddl) == 54, [s for s, _, _ in ddl]
    assert all(autocommit for _, autocommit, _ in ddl), [s for s, a, _ in ddl if not a]
    # psycopg's TransactionStatus.IDLE is 0: no transaction was open as the statement started,
    # so the statement's own locks are released the moment it finishes.
    assert all(txn == 0 for _, _, txn in ddl), [(s, t) for s, _, t in ddl if t != 0]
    # The tape tables come last, so their AccessExclusiveLock is never taken early in the run.
    tape = [i for i, (s, _, _) in enumerate(ddl) if "venue_trades" in s or "orderbook_events" in s]
    assert tape and min(tape) > max(i for i in range(len(ddl)) if i not in tape)


def test_create_schema_retries_a_ddl_statement_after_a_lock_timeout(db_session, monkeypatch):
    """A real AccessExclusiveLock on venue_trades makes the next DDL statement hit lock_timeout
    (55P03). create_schema must retry that one statement rather than abort the whole init-db."""
    from harness.db import schema as schema_mod

    engine = db_session.get_bind()
    db_session.commit()
    monkeypatch.setattr(schema_mod, "DDL_LOCK_TIMEOUT", "300ms")

    blocked: list[str] = []
    hit = threading.Event()
    released = threading.Event()

    def on_error(ctx):
        if getattr(ctx.original_exception, "sqlstate", None) == "55P03" and not hit.is_set():
            blocked.append(str(ctx.statement))
            hit.set()
            released.wait(30)  # hand the lock back before create_schema's retry runs

    result: dict = {}

    def run_create_schema():
        try:
            create_schema(engine)
            result["ok"] = True
        except BaseException as exc:  # reported by the assertion below
            result["exc"] = exc

    event.listen(engine, "handle_error", on_error)
    blocker = engine.connect()
    try:
        blocker.execute(text("lock table venue_trades in access exclusive mode"))
        worker = threading.Thread(target=run_create_schema)
        worker.start()
        assert hit.wait(30), "create_schema never hit the lock timeout"
        blocker.rollback()
        released.set()
        worker.join(60)
        assert not worker.is_alive()
    finally:
        released.set()
        event.remove(engine, "handle_error", on_error)
        blocker.close()

    assert result.get("ok"), result.get("exc")
    assert blocked and "venue_trades" in blocked[0].lower(), blocked


def test_every_model_index_exists_after_create_schema_on_an_existing_database(db_session):
    """F47: create_all only builds indexes for tables it creates, so a database that predates a
    model index never gets it. create_schema walks every model index instead."""
    engine = db_session.get_bind()
    db_session.execute(text("drop index ix_signal_variant_created"))
    db_session.commit()
    assert "ix_signal_variant_created" not in set(db_session.execute(text(
        "select indexname from pg_indexes where schemaname='public'")).scalars())

    create_schema(engine)
    have = set(db_session.execute(text(
        "select indexname from pg_indexes where schemaname='public'")).scalars())
    want = {idx.name for table in Base.metadata.sorted_tables for idx in table.indexes}
    assert want and want <= have, sorted(want - have)


def test_drop_schema_covers_every_model(db_session):
    engine = db_session.get_bind()
    db_session.commit()
    try:
        drop_schema(engine)
        with engine.connect() as conn:
            tables = set(conn.execute(text(
                "select tablename from pg_tables where schemaname='public'")).scalars())
            views = set(conn.execute(text(
                "select viewname from pg_views where schemaname='public'")).scalars())
        assert set(Base.metadata.tables) & tables == set(), sorted(set(Base.metadata.tables) & tables)
        assert {"positions", "clv", "order_episodes"} & views == set()
    finally:
        create_schema(engine)
        ensure_partitions(db_session, datetime.now(timezone.utc))


def test_match_key_backfilled_by_create_schema(db_session):
    """Task 3b's matcher writes match_key going forward; create_schema fills the rows that
    predate the column, and leaves unmatched markets NULL."""
    from harness.db.models import Game, VenueMarket

    game = Game(sport="ncaaf", home_team_id=26, away_team_id=27, kickoff_utc=NOW)
    db_session.add(game)
    db_session.flush()
    matched = VenueMarket(venue="kalshi", ticker="T-MATCHED", event_ticker="E", series_ticker="S",
                          game_id=game.id, market_type="spread", side_team_id=26, side=None,
                          threshold=Decimal("-3.5"), match_status="matched",
                          first_seen_raw_id=1, last_seen_at=NOW)
    unmatched = VenueMarket(venue="kalshi", ticker="T-UNMATCHED", event_ticker="E", series_ticker="S",
                            game_id=None, market_type="moneyline", first_seen_raw_id=1, last_seen_at=NOW)
    db_session.add_all([matched, unmatched])
    db_session.commit()
    assert matched.match_key is None
    db_session.commit()  # release the read lock before create_schema alters venue_markets

    create_schema(db_session.get_bind())
    db_session.expire_all()
    assert matched.match_key == f"{game.id}:spread:26::-3.5"
    assert unmatched.match_key is None

    # The backfill is additive: it never rewrites a key the matcher already set.
    matched.match_key = "hand-written"
    db_session.commit()
    create_schema(db_session.get_bind())
    db_session.expire_all()
    assert matched.match_key == "hand-written"


def test_match_key_backfill_matches_matcher_composition_for_integral_and_half_point(db_session):
    """Controller ruling (Task 3b item 2): the Python composer (`compose_match_key`) must
    render `threshold` exactly as this SQL backfill renders `numeric(6,1)::text` -- an
    integral value keeps its trailing zero (`3` -> `"3.0"`), not bare `"3"` -- or an
    integral-threshold market's open order would compare unequal to its own venue_market's
    key and get cancelled as unmatched the moment the two paths disagree."""
    from harness.db.models import Game, VenueMarket
    from harness.db.schema import create_schema
    from harness.matching.kalshi import compose_match_key

    game = Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=NOW)
    db_session.add(game)
    db_session.flush()
    half = VenueMarket(venue="kalshi", ticker="T-HALF", event_ticker="E", series_ticker="S",
                       game_id=game.id, market_type="spread", side_team_id=19, side=None,
                       threshold=Decimal("6.5"), match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    whole = VenueMarket(venue="kalshi", ticker="T-WHOLE", event_ticker="E", series_ticker="S",
                        game_id=game.id, market_type="spread", side_team_id=19, side=None,
                        threshold=Decimal("3"), match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    # Task 3b fix round 1, Minor 2: numeric(6,1) rounds half away from zero, unlike Python
    # Decimal's default ROUND_HALF_EVEN -- a threshold that lands exactly halfway between two
    # tenths (6.45) is the case that would previously have diverged (Postgres "6.5", Python
    # "6.4"). Football thresholds are always half-points in practice, but this pins the
    # rounding mode itself rather than relying on the data shape to hide the bug.
    midpoint = VenueMarket(venue="kalshi", ticker="T-MIDPOINT", event_ticker="E", series_ticker="S",
                           game_id=game.id, market_type="spread", side_team_id=19, side=None,
                           threshold=Decimal("6.45"), match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    db_session.add_all([half, whole, midpoint])
    db_session.commit()

    create_schema(db_session.get_bind())
    db_session.expire_all()

    assert half.match_key == compose_match_key(game.id, "spread", 19, None, Decimal("6.5"))
    assert whole.match_key == compose_match_key(game.id, "spread", 19, None, Decimal("3"))
    assert whole.match_key.endswith(":3.0")
    assert half.match_key.endswith(":6.5")
    assert midpoint.match_key.endswith(":6.5")
    assert midpoint.match_key == compose_match_key(game.id, "spread", 19, None, Decimal("6.45"))


def test_order_clv_table_and_clv_view(db_session):
    from harness.db.models import OrderClv

    order = _order(db_session)
    db_session.add(OrderClv(order_id=order.id, benchmark_type="close", p_bench=Decimal("0.5500"),
                            p_used=Decimal("0.5000"), p_used_kind="fill", clv_p=Decimal("0.0500"),
                            clv_p_net=Decimal("0.0450"), clv_roi_net=Decimal("0.0900"), stale=False))
    db_session.flush()
    row = db_session.execute(text(
        "select order_id, variant_id, side, benchmark_type, p_bench, p_used, clv_p, clv_p_net, "
        "clv_roi_net, stale from clv")).one()
    assert row.order_id == order.id
    assert (row.variant_id, row.side, row.benchmark_type) == ("sharp_direct", "yes", "close")
    assert (row.p_bench, row.clv_p_net, row.stale) == (Decimal("0.5500"), Decimal("0.0450"), False)

    db_session.add(OrderClv(order_id=order.id, benchmark_type="close", p_bench=Decimal("0.6000")))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_order_episodes_follows_a_reprice_chain(db_session):
    first = _order(db_session, placed_at=NOW, status="cancelled", cancel_reason="reprice",
                   cancelled_at=NOW + timedelta(minutes=1))
    second = _order(db_session, placed_at=NOW + timedelta(minutes=1), status="cancelled",
                    cancel_reason="reprice", cancelled_at=NOW + timedelta(minutes=2))
    third = _order(db_session, placed_at=NOW + timedelta(minutes=2), status="cancelled",
                   cancel_reason="edge_decay", cancelled_at=NOW + timedelta(minutes=3))
    fourth = _order(db_session, placed_at=NOW + timedelta(minutes=4))
    db_session.flush()

    rows = db_session.execute(text(
        "select episode_id, variant_id, venue_market_id, side, first_order_id, last_order_id, "
        "n_orders from order_episodes order by episode_id")).all()
    assert [tuple(r) for r in rows] == [
        (first.id, "sharp_direct", 1, "yes", first.id, third.id, 3),
        (fourth.id, "sharp_direct", 1, "yes", fourth.id, fourth.id, 1),
    ]


def test_order_episodes_excludes_replay_orders(db_session):
    """A replay run re-simulates a market the live executor is working (uq_open_order exempts
    it), so a replay order on the same key must neither join a live episode nor start one."""
    live = _order(db_session, placed_at=NOW, status="cancelled", cancel_reason="reprice",
                  cancelled_at=NOW + timedelta(minutes=1))
    replayed = _order(db_session, placed_at=NOW + timedelta(minutes=1), replay=True,
                      status="cancelled", cancel_reason="reprice",
                      cancelled_at=NOW + timedelta(minutes=2))
    successor = _order(db_session, placed_at=NOW + timedelta(minutes=2))
    db_session.flush()

    rows = db_session.execute(text(
        "select episode_id, first_order_id, last_order_id, n_orders from order_episodes")).all()
    # The live pair is one episode; the replay order neither splices in nor appears alone.
    assert [tuple(r) for r in rows] == [(live.id, live.id, successor.id, 2)]
    assert replayed.id not in {r.episode_id for r in rows}


def test_model_index_loop_never_touches_the_tape_tables(db_session):
    """Task 2b builds the orderbook_events and venue_trades indexes CONCURRENTLY. A
    non-concurrent CREATE INDEX from this loop would lock the WebSocket sink out of a populated
    table for the whole build."""
    from harness.db import schema as schema_module

    tables = {index.table.name for index in schema_module._model_indexes()}
    assert tables and not tables & set(schema_module.TAPE_TABLES), sorted(tables)
    # The skip is real: both tape tables do carry model indexes that the loop leaves alone.
    declared = {t.name for t in Base.metadata.sorted_tables if t.indexes}
    assert set(schema_module.TAPE_TABLES) <= declared


def test_fixture_truncates_between_tests_a(db_session):
    from harness.db.models import Run

    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()
    assert run.id == 1


def test_fixture_truncates_between_tests_b(db_session):
    from harness.db.models import Run

    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()
    assert run.id == 1
