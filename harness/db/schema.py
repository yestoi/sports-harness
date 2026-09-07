import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from harness.db.models import Base

log = logging.getLogger(__name__)

#: How long a DDL statement waits for its lock before giving up and being retried once.
#: `init-db` runs while the WebSocket sink is inserting, so a statement that blocks forever
#: takes the sink down with it (carried fix 13).
DDL_LOCK_TIMEOUT = "5s"

#: SQLSTATEs that mean "someone else held the lock", not "the statement is wrong":
#: lock_not_available, query_canceled (statement_timeout) and deadlock_detected.
LOCK_SQLSTATES = frozenset({"55P03", "57014", "40P01"})

#: The append-only tape tables. Their DDL runs last: an ALTER on one takes an
#: AccessExclusiveLock that the WebSocket sink's inserts queue behind, so the less time the
#: rest of init-db spends after that lock is taken, the smaller the window.
TAPE_TABLES = ("orderbook_events", "venue_trades")


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("week_bounds requires a tz-aware datetime")
    now = now.astimezone(timezone.utc)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _partition_name(start: datetime) -> str:
    iso = start.isocalendar()
    return f"raw_responses_y{iso.year}w{iso.week:02d}"


def ensure_partitions(session: Session, now: datetime) -> list[str]:
    created: list[str] = []
    start, _ = week_bounds(now)
    for i in range(2):
        s = start + timedelta(days=7 * i)
        e = s + timedelta(days=7)
        name = _partition_name(s)
        exists = session.execute(text("select 1 from pg_class where relname = :n"), {"n": name}).first()
        if exists:
            continue
        session.execute(text(
            f"create table {name} partition of raw_responses "
            f"for values from ('{s.isoformat()}') to ('{e.isoformat()}')"
        ))
        created.append(name)
    session.commit()
    return created


# --- DDL run by create_schema, in the order it runs -------------------------------------
# create_all only ever creates missing tables, so every column, index and view added after a
# table first shipped is an idempotent statement here.

#: Columns added to tables that are not the bulk tape. Safe to run against a live database:
#: these tables see one writer per tick, not a continuous insert stream.
_COLUMN_DDL = (
    "alter table market_gap_snapshots add column if not exists no_fair_reason varchar(32)",
    # F11 staleness amendment: which feed set the newest book time, how old that poll was, the
    # allowance `not_stale` compares against, and the pricing version the row was computed under.
    "alter table fair_values add column if not exists feed_kind varchar(9)",
    "alter table fair_values add column if not exists feed_lag_s integer",
    "alter table fair_values add column if not exists stale_allowance_s integer",
    "alter table fair_values add column if not exists pricing_version varchar(16)",
    "alter table market_gap_snapshots add column if not exists feed_kind varchar(9)",
    "alter table market_gap_snapshots add column if not exists feed_lag_s integer",
    "alter table market_gap_snapshots add column if not exists stale_allowance_s integer",
    # Phase 3: Kalshi market metadata the executor prices and pays fees from, plus the match key.
    "alter table venue_markets add column if not exists expected_expiration_time timestamptz",
    "alter table venue_markets add column if not exists price_level_structure varchar(48)",
    "alter table venue_markets add column if not exists price_ranges jsonb",
    "alter table venue_markets add column if not exists fee_type varchar(32)",
    "alter table venue_markets add column if not exists fee_multiplier numeric(6,4)",
    "alter table venue_markets add column if not exists exchange_index integer not null default 0",
    "alter table venue_markets add column if not exists match_key varchar(64)",
    "alter table runs add column if not exists build_sha varchar(24)",
    "alter table signals add column if not exists as_measured numeric(6,4)",
)

#: Indexes and constraints Postgres can only express as raw DDL (partial, functional, BRIN).
_INDEX_DDL = (
    "create index if not exists ix_raw_source_fetched on raw_responses (source, fetched_at)",
    "create index if not exists ix_raw_fetched_brin on raw_responses using brin (fetched_at)",
    "create index if not exists ix_raw_run on raw_responses (run_id)",
    "create unique index if not exists uq_odds_snapshot_row on odds_snapshots "
    "(raw_id, book, market_type, coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), coalesce(point, 0))",
    "create unique index if not exists uq_fair_value_row on fair_values "
    "(run_id, game_id, market_type, coalesce(outcome_team_id,-1), coalesce(outcome_side,''), "
    "coalesce(threshold,0), fair_source)",
    # One live order per (venue, ticker, side, variant). Replay orders are exempt so a replay
    # run can re-simulate a market the live executor is working.
    "create unique index if not exists uq_open_order on orders (venue, ticker, side, variant_id) "
    "where status in ('open', 'partially_filled') and replay = false",
    "create index if not exists ix_orders_status on orders (status, replay)",
    "create index if not exists ix_orders_nw on orders (nw_done, expiry) where nw_done = false",
    "create index if not exists ix_orders_game on orders (game_id)",
    "create index if not exists ix_intents_key on intents "
    "(variant_id, venue_market_id, side, signal_created_at desc)",
    "create unique index if not exists uq_order_event on order_events (order_id, kind, ts) "
    "where order_id is not null",
    # A skip has no order, so it is deduped on its intent instead; one skip per reason.
    "create unique index if not exists uq_skip_once on order_events (intent_id, kind, reason) "
    "where kind in ('skipped', 'cap_gate')",
    "create unique index if not exists uq_fill_source on fills "
    "(order_id, fill_method, coalesce(source_trade_id, ''), coalesce(source_event_id, -1))",
    "create index if not exists ix_fills_order on fills (order_id)",
    "create index if not exists ix_fills_filled_at on fills (filled_at)",
    "create unique index if not exists uq_benchmark_row on benchmarks "
    "(game_id, market_type, coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), "
    "coalesce(threshold, 0), benchmark_type)",
    "create unique index if not exists uq_ledger_fill on ledger (fill_id, kind)",
    "create index if not exists ix_job_runs on job_runs (job, started_at desc)",
)

#: Open contracts and their average price per variant, from the queue-model fills of live orders
#: that have not settled yet. snapshot_cross and no_watcher fills are counterfactuals, not positions.
_POSITIONS_VIEW = """
create or replace view positions as
select o.variant_id,
       o.ticker,
       o.side,
       sum(f.contracts) as open_contracts,
       sum(f.contracts * f.prob) / nullif(sum(f.contracts), 0) as avg_price
from fills f
join orders o on o.id = f.order_id
where f.fill_method = 'queue_model'
  and o.replay = false
  and o.status <> 'settled'
group by o.variant_id, o.ticker, o.side
"""

#: Order-level CLV with the order's variant and side joined on, for the dashboard. Task 8 fills
#: order_clv; the view exists from Task 2 so the dashboard can be built against it.
_CLV_VIEW = """
create or replace view clv as
select c.order_id,
       o.variant_id,
       o.side,
       c.benchmark_type,
       c.p_bench,
       c.p_used,
       c.clv_p,
       c.clv_p_net,
       c.clv_roi_net,
       c.stale
from order_clv c
join orders o on o.id = c.order_id
"""

#: A reprice cancels an order and places a replacement; the pair is one economic decision, so
#: scoring counts episodes, not orders. An order joins its predecessor's episode when that
#: predecessor on the same (variant, market, side) was cancelled for 'reprice' no later than
#: this order was placed; otherwise it opens an episode named by its own id.
_ORDER_EPISODES_VIEW = """
create or replace view order_episodes as
with recursive ordered as (
    select o.id,
           o.variant_id,
           o.venue_market_id,
           o.side,
           o.placed_at,
           lag(o.id) over w as prev_id,
           lag(o.cancel_reason) over w as prev_cancel_reason,
           lag(o.cancelled_at) over w as prev_cancelled_at
    from orders o
    window w as (partition by o.variant_id, o.venue_market_id, o.side order by o.placed_at, o.id)
),
linked as (
    select id, variant_id, venue_market_id, side,
           case when prev_cancel_reason = 'reprice' and prev_cancelled_at <= placed_at
                then prev_id end as parent_id
    from ordered
),
chain as (
    select id, id as episode_id, variant_id, venue_market_id, side
    from linked
    where parent_id is null
    union all
    select l.id, c.episode_id, l.variant_id, l.venue_market_id, l.side
    from linked l
    join chain c on l.parent_id = c.id
)
select episode_id,
       variant_id,
       venue_market_id,
       side,
       min(id) as first_order_id,
       max(id) as last_order_id,
       count(*)::int as n_orders
from chain
group by episode_id, variant_id, venue_market_id, side
"""

_VIEW_DDL = (_POSITIONS_VIEW, _CLV_VIEW, _ORDER_EPISODES_VIEW)

#: One additive backfill for the rows that predate `match_key`; the matcher writes it going
#: forward. Composed exactly like the Python side, with NULL parts rendered empty.
_BACKFILL_DDL = (
    "update venue_markets set match_key = "
    "game_id::text || ':' || market_type || ':' || coalesce(side_team_id::text, '') || ':' || "
    "coalesce(side, '') || ':' || coalesce(threshold::text, '') "
    "where match_key is null and game_id is not null",
)

#: Tape-table DDL, run last (see TAPE_TABLES). BRIN keeps "last hour" dashboard scans cheap on
#: append-only, time-ordered tables without a large btree.
_TAPE_DDL = (
    "create index if not exists ix_obe_ts_brin on orderbook_events using brin (ts)",
    "create index if not exists ix_trades_ts_brin on venue_trades using brin (ts)",
    # F5: Kalshi deprecated trades.taker_side in favour of these two.
    "alter table venue_trades add column if not exists taker_outcome_side varchar(4)",
    "alter table venue_trades add column if not exists taker_book_side varchar(4)",
)


def _is_lock_failure(exc: BaseException) -> bool:
    return getattr(getattr(exc, "orig", None), "sqlstate", None) in LOCK_SQLSTATES


def _execute_ddl(conn: Connection, statement: str) -> None:
    """Run one DDL statement, retrying once if another session held the lock.

    The connection is in autocommit, so the statement's locks are taken and released within the
    statement itself. Without that, `init-db` deadlocked against the WebSocket sink: the ALTERs
    on venue_trades hold an AccessExclusiveLock until commit, the later CREATE INDEX on
    orderbook_events waits for a ShareLock behind the sink's open insert batch, and the sink's
    next insert into venue_trades waits on init-db.
    """
    try:
        conn.execute(text(statement))
        return
    except DBAPIError as exc:
        if not _is_lock_failure(exc):
            raise
        log.warning("ddl lock contention, retrying once: %s", " ".join(statement.split())[:80])
    conn.rollback()
    conn.execute(text(statement))


def _model_index_ddl(conn: Connection, tape: bool) -> None:
    """F47: create_all only builds indexes for tables it creates, so a database that predates a
    model index never gets it. Walk every model index instead, tape tables separately so their
    DDL still runs last."""
    for table in Base.metadata.sorted_tables:
        if (table.name in TAPE_TABLES) != tape:
            continue
        for index in table.indexes:
            try:
                index.create(conn, checkfirst=True)
            except DBAPIError as exc:
                if not _is_lock_failure(exc):
                    raise
                log.warning("ddl lock contention, retrying once: index %s", index.name)
                conn.rollback()
                index.create(conn, checkfirst=True)


def create_schema(engine: Engine) -> None:
    """Bring a database up to the current models. Idempotent; the only schema entrypoint
    (`init-db`), so it must be safe to run against the live NAS database on every deploy.

    Every statement after `create_all` runs in its own autocommit transaction under a
    lock_timeout, and the tape tables come last: init-db runs while the WebSocket sink is
    writing, and one long DDL transaction deadlocked a deploy.
    """
    Base.metadata.create_all(engine)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"set lock_timeout = '{DDL_LOCK_TIMEOUT}'"))
        for statement in _COLUMN_DDL + _INDEX_DDL + _VIEW_DDL + _BACKFILL_DDL:
            _execute_ddl(conn, statement)
        _model_index_ddl(conn, tape=False)
        _model_index_ddl(conn, tape=True)
        for statement in _TAPE_DDL:
            _execute_ddl(conn, statement)


def drop_schema(engine: Engine) -> None:
    """Drop every view and every model table. Generated from the metadata so a new model can
    never be left behind in a test database."""
    tables = ", ".join(sorted(Base.metadata.tables))
    with engine.begin() as conn:
        conn.execute(text("drop view if exists positions, clv, order_episodes"))
        conn.execute(text(f"drop table if exists {tables} cascade"))
