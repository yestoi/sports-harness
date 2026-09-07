"""Task 2b: the one-off migration that turns the live tape tables into weekly range partitions,
and the two writer changes that partitioning forces.

`ts` joins the primary key of both tape tables (`orderbook_events (id, ts)`,
`venue_trades (venue, trade_id, ts)`) because a partitioned table's unique key must contain the
partition key. That makes the WebSocket and REST writers' timestamps part of a trade's identity:
they are truncated to milliseconds so both feeds agree on one value, and the REST writer skips a
trade whose `(venue, trade_id)` is already on the tape, since the composite key can no longer
dedupe a print the WebSocket recorded one millisecond earlier (addendum §5, finding F19).

`legacy_tape_tables` rebuilds the pre-migration (unpartitioned) shape of the two tables, so these
tests run the migration against the shape the NAS database actually has tonight, and puts the
partitioned shape back afterwards for the rest of the session-scoped schema.
"""

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import Base, VenueTrade
from harness.db.partition import partition_bulk_tables
from harness.db.schema import TAPE_TABLES, create_schema, ensure_partitions, tape_index_ddl
from harness.normalize.kalshi import insert_trades
from harness.recorder.ws_sink import WsSink

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wednesday of ISO week 2026-W37

#: The tape tables exactly as they stand on the live database before the migration: no partition
#: key, `orderbook_events` keyed on `id` alone and `venue_trades` on `(venue, trade_id)`.
LEGACY_DDL = {
    "orderbook_events": (
        "create table orderbook_events (id bigserial primary key, ticker varchar(64) not null, "
        "ts timestamptz not null, sid int not null, seq bigint not null, kind varchar(8) not null, "
        "side varchar(4), price numeric(6,4), delta numeric(14,2), raw jsonb not null)",
        "create index ix_obe_ticker_ts on orderbook_events (ticker, ts)",
        "create index ix_obe_ts_brin on orderbook_events using brin (ts)",
    ),
    "venue_trades": (
        "create table venue_trades (venue varchar(16) not null, trade_id varchar(64) not null, "
        "ticker varchar(64) not null, ts timestamptz not null, yes_price numeric(6,4) not null, "
        "count numeric(12,2) not null, taker_side varchar(4) not null, taker_outcome_side varchar(4), "
        "taker_book_side varchar(4), is_block boolean not null, source varchar(4) not null, "
        "raw_id bigint, primary key (venue, trade_id))",
        "create index ix_trades_ticker_ts on venue_trades (ticker, ts)",
        "create index ix_trades_ts_brin on venue_trades using brin (ts)",
    ),
}

NEW_TAPE_INDEXES = ("ix_obe_snapshot", "ix_obe_ticker_id", "ix_obe_gap", "ix_trades_venue_trade_id")


def _drop_tape_tables(engine) -> None:
    with engine.begin() as conn:
        for table in TAPE_TABLES:
            # A partitioned parent takes its partitions (including the attached legacy table) with
            # it; the `_legacy` drop is for a half-finished migration.
            conn.execute(text(f"drop table if exists {table} cascade"))
            conn.execute(text(f"drop table if exists {table}_legacy cascade"))


@pytest.fixture
def legacy_tape_tables(db_session):
    """Swap the partitioned tape tables for their pre-migration shape, then swap them back."""
    engine = db_session.get_bind()
    db_session.commit()
    _drop_tape_tables(engine)
    with engine.begin() as conn:
        for table in TAPE_TABLES:
            for statement in LEGACY_DDL[table]:
                conn.execute(text(statement))
    yield engine
    _drop_tape_tables(engine)
    with engine.begin() as conn:
        for table in TAPE_TABLES:
            Base.metadata.tables[table].create(conn)
            for statement in tape_index_ddl(table):
                conn.execute(text(statement))
    with sessionmaker(bind=engine)() as session:
        ensure_partitions(session, datetime.now(timezone.utc))


def _seed_legacy(engine) -> None:
    """Three orderbook events and two trades on the legacy tape, oldest first."""
    with engine.begin() as conn:
        for i, ticker in enumerate(("K1", "K2", "K1")):
            conn.execute(text(
                "insert into orderbook_events (ticker, ts, sid, seq, kind, raw) "
                "values (:t, :ts, 1, :s, 'delta', '{}'::jsonb)"),
                {"t": ticker, "ts": NOW - timedelta(minutes=3 - i), "s": i + 1})
        for i in range(2):
            conn.execute(text(
                "insert into venue_trades (venue, trade_id, ticker, ts, yes_price, count, taker_side, "
                "is_block, source) values ('kalshi', :id, 'K1', :ts, 0.2300, 5.00, 'yes', false, 'ws')"),
                {"id": f"t-{i}", "ts": NOW - timedelta(minutes=2 - i)})


def test_partition_bulk_tables_attaches_legacy_rows_and_continues_ids(legacy_tape_tables):
    engine = legacy_tape_tables
    _seed_legacy(engine)

    started = time.monotonic()
    migrated = partition_bulk_tables(engine, NOW)
    elapsed = time.monotonic() - started
    print(f"\npartition_bulk_tables elapsed: {elapsed:.3f}s (5 rows)")

    assert migrated == ["orderbook_events", "venue_trades"]
    with engine.connect() as conn:
        partitioned = set(conn.execute(text(
            "select relname from pg_partitioned_table join pg_class on oid = partrelid")).scalars())
        assert {"orderbook_events", "venue_trades"} <= partitioned

        # Every legacy row still reads through the parent, out of the attached legacy partition.
        events = conn.execute(text(
            "select ticker, tableoid::regclass::text from orderbook_events order by id")).all()
        assert [t for t, _ in events] == ["K1", "K2", "K1"]
        assert {p for _, p in events} == {"orderbook_events_legacy"}
        trades = conn.execute(text(
            "select trade_id, tableoid::regclass::text from venue_trades order by trade_id")).all()
        assert [t for t, _ in trades] == ["t-0", "t-1"]
        assert {p for _, p in trades} == {"venue_trades_legacy"}

        # The CHECK that lets the attach skip its own scan is validated, not left NOT VALID.
        validated = dict(conn.execute(text(
            "select conname, convalidated from pg_constraint where conname like 'ck_%_legacy_ts'")).all())
        assert validated == {"ck_orderbook_events_legacy_ts": True, "ck_venue_trades_legacy_ts": True}

    # The id sequence continues from the legacy maximum, and a row stamped after the cutover
    # lands in the partial cutover-week partition rather than the legacy one.
    with engine.begin() as conn:
        new_id, partition = conn.execute(text(
            "insert into orderbook_events (ticker, ts, sid, seq, kind, raw) "
            "values ('K3', :ts, 1, 9, 'delta', '{}'::jsonb) returning id, tableoid::regclass::text"),
            {"ts": NOW}).one()
    assert new_id == 4
    assert partition == "orderbook_events_y2026w37"

    # Idempotent: a table already in pg_partitioned_table is skipped.
    assert partition_bulk_tables(engine, NOW) == []


def test_partition_bulk_tables_builds_the_new_indexes_on_the_parent(legacy_tape_tables):
    """The three `orderbook_events` indexes and `ix_trades_venue_trade_id` are built CONCURRENTLY
    on the live table under `_legacy` names, so the attach matches them instead of rebuilding, and
    the partitioned parent carries them for every future partition."""
    engine = legacy_tape_tables
    _seed_legacy(engine)
    partition_bulk_tables(engine, NOW)

    with engine.connect() as conn:
        names = set(conn.execute(text(
            "select indexname from pg_indexes where schemaname = 'public'")).scalars())
    assert set(NEW_TAPE_INDEXES) <= names, sorted(names)
    assert {f"{n}_legacy" for n in NEW_TAPE_INDEXES} <= names, sorted(names)


def test_row_with_ts_before_bound_routes_to_the_legacy_partition(legacy_tape_tables):
    """A REST backfill arriving after the cutover carries a `ts` from before it; the parent must
    route it into the legacy partition instead of rejecting it."""
    engine = legacy_tape_tables
    _seed_legacy(engine)
    partition_bulk_tables(engine, NOW)

    with engine.begin() as conn:
        partition = conn.execute(text(
            "insert into venue_trades (venue, trade_id, ticker, ts, yes_price, count, taker_side, "
            "is_block, source) values ('kalshi', 'backfill-1', 'K1', :ts, 0.2300, 5.00, 'yes', "
            "false, 'rest') returning tableoid::regclass::text"),
            {"ts": NOW - timedelta(hours=1)}).scalar_one()
    assert partition == "venue_trades_legacy"


def test_create_schema_skips_the_new_indexes_on_a_live_unpartitioned_table(legacy_tape_tables):
    """`init-db` runs on every deploy while the WebSocket sink is writing. A non-concurrent
    CREATE INDEX on a populated, still-unpartitioned tape table would lock the sink out for the
    length of the build, so create_schema builds these four only once the table is partitioned
    (or is still empty); the migration builds them CONCURRENTLY instead."""
    engine = legacy_tape_tables
    _seed_legacy(engine)

    create_schema(engine)

    with engine.connect() as conn:
        names = set(conn.execute(text(
            "select indexname from pg_indexes where schemaname = 'public'")).scalars())
    assert not set(NEW_TAPE_INDEXES) & names, sorted(set(NEW_TAPE_INDEXES) & names)
    # The unguarded tape DDL still runs: the BRIN indexes and the taker_* columns are cheap.
    assert {"ix_obe_ts_brin", "ix_trades_ts_brin"} <= names


def test_ensure_partitions_skips_a_table_that_is_not_partitioned_yet(legacy_tape_tables, caplog):
    """`make deploy-nas` ships this code and restarts the recorder before the controller runs the
    one-off migration, so the tick calls ensure_partitions against tape tables that are still
    unpartitioned. It has to skip them, not raise and take the tick down with it."""
    engine = legacy_tape_tables
    with sessionmaker(bind=engine)() as session:
        session.execute(text("drop table if exists raw_responses_y2026w37"))
        session.execute(text("drop table if exists raw_responses_y2026w38"))
        session.commit()
        with caplog.at_level("WARNING", logger="harness.db.schema"):
            created = ensure_partitions(session, NOW)

    assert created == ["raw_responses_y2026w37", "raw_responses_y2026w38"]
    skipped = {r.getMessage().split()[0] for r in caplog.records if "not partitioned yet" in r.getMessage()}
    assert skipped == {"orderbook_events", "venue_trades"}


def test_trade_ts_truncated_to_milliseconds(db_session):
    """`ts` is part of a trade's primary key now, so the two writers must agree on it. Kalshi
    reports milliseconds; anything finer is the recorder's own clock leaking into the key."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    received_at = datetime(2026, 9, 9, 22, 58, 0, 123456, tzinfo=timezone.utc)
    base = {"market_ticker": "K1", "yes_price_dollars": "0.3600", "count_fp": "5.00",
            "taker_side": "yes", "is_block_trade": False}
    # A trade the venue timestamped, and one it did not (the sink falls back to its own clock).
    sink.handle({"type": "trade", "sid": 1, "seq": 1,
                 "msg": {**base, "trade_id": "ws-stamped", "ts_ms": 1788994680123}}, received_at)
    sink.handle({"type": "trade", "sid": 1, "seq": 2, "msg": {**base, "trade_id": "ws-fallback"}},
                received_at)

    body = {"cursor": "", "trades": [{
        "trade_id": "rest-1", "ticker": "K1", "created_time": "2026-09-09T22:58:00.123456Z",
        "yes_price_dollars": "0.2300", "count_fp": "5.00", "taker_side": "yes", "is_block_trade": False}]}
    assert insert_trades(db_session, body, raw_id=1) == 1
    db_session.commit()

    stamps = {t.trade_id: t.ts for t in db_session.query(VenueTrade).all()}
    assert set(stamps) == {"ws-stamped", "ws-fallback", "rest-1"}
    for trade_id, ts in stamps.items():
        assert ts.microsecond % 1000 == 0, (trade_id, ts)
    assert stamps["ws-stamped"] == datetime(2026, 9, 9, 22, 58, 0, 123000, tzinfo=timezone.utc)
    assert stamps["ws-fallback"] == datetime(2026, 9, 9, 22, 58, 0, 123000, tzinfo=timezone.utc)
    assert stamps["rest-1"] == datetime(2026, 9, 9, 22, 58, 0, 123000, tzinfo=timezone.utc)


def test_rest_trade_already_recorded_by_ws_is_not_inserted_again(db_session):
    """With `ts` in the key, a REST print whose `created_time` is one millisecond off the
    WebSocket's `ts_ms` no longer collides with it: ON CONFLICT DO NOTHING would let the same
    trade onto the tape twice and double its volume. The writer looks `(venue, trade_id)` up
    through `ix_trades_venue_trade_id` and skips it."""
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-dup", ticker="K1",
                              ts=datetime(2026, 9, 9, 22, 58, 0, 123000, tzinfo=timezone.utc),
                              yes_price=Decimal("0.2300"), count=Decimal("5.00"), taker_side="yes",
                              is_block=False, source="ws", raw_id=None))
    db_session.flush()

    body = {"cursor": "", "trades": [{
        "trade_id": "t-dup", "ticker": "K1", "created_time": "2026-09-09T22:58:00.124Z",
        "yes_price_dollars": "0.2300", "count_fp": "5.00", "taker_side": "yes", "is_block_trade": False}]}
    assert insert_trades(db_session, body, raw_id=2) == 0

    rows = db_session.query(VenueTrade).filter_by(trade_id="t-dup").all()
    assert len(rows) == 1
    assert rows[0].source == "ws"
