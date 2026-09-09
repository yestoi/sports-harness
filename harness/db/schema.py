import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import Connection, Engine, Index, text
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

#: Every table partitioned by range on a timestamp, in the order `ensure_partitions` walks them.
#: `raw_responses` partitions on `fetched_at`, the two tape tables on `ts`.
PARTITIONED_TABLES = ("raw_responses", "orderbook_events", "venue_trades")


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("week_bounds requires a tz-aware datetime")
    now = now.astimezone(timezone.utc)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _partition_name(table: str, start: datetime) -> str:
    iso = start.isocalendar()
    return f"{table}_y{iso.year}w{iso.week:02d}"


def is_partitioned(executor, table: str) -> bool:
    """Whether `table` is a partitioned parent. Takes a Session or a Connection."""
    return executor.execute(text(
        "select 1 from pg_partitioned_table p join pg_class c on c.oid = p.partrelid "
        "where c.relname = :t"), {"t": table}).first() is not None


def ensure_partitions(session: Session, now: datetime,
                      tables: tuple[str, ...] = PARTITIONED_TABLES) -> list[str]:
    """Create this week's and next week's `[week_start, week_end)` partition for each table.

    Names that already exist are skipped, so the partial cutover-week partition the one-off
    migration leaves behind (same name, a range starting mid-week) keeps its own bounds.

    A table that is not a partitioned parent yet is skipped rather than raising. That is the state
    of the tape tables between the deploy that ships this code and the one-off
    `partition-bulk-tables` run, and the recorder's tick calls this every time it runs.
    """
    created: list[str] = []
    start, _ = week_bounds(now)
    for table in tables:
        if not is_partitioned(session, table):
            log.warning("%s is not partitioned yet, skipping its weekly partitions "
                        "(run `harness partition-bulk-tables`)", table)
            continue
        for i in range(2):
            s = start + timedelta(days=7 * i)
            e = s + timedelta(days=7)
            name = _partition_name(table, s)
            exists = session.execute(text("select 1 from pg_class where relname = :n"), {"n": name}).first()
            if exists:
                continue
            session.execute(text(
                f"create table {name} partition of {table} "
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
    # Task 6: the rest of the fill simulator's per-track state, persisted on the order so a
    # restart resumes the same simulation, plus the dirty accumulator in seconds.
    "alter table orders add column if not exists crossed boolean not null default false",
    "alter table orders add column if not exists last_print_ts timestamptz",
    "alter table orders add column if not exists last_print_ids jsonb",
    "alter table orders add column if not exists nw_crossed boolean not null default false",
    "alter table orders add column if not exists nw_last_print_ts timestamptz",
    "alter table orders add column if not exists nw_last_print_ids jsonb",
    "alter table orders add column if not exists dirty_seconds integer not null default 0",
    # Phase 4 §7: the risk gate's drawdown fields and the dormant live path's venue columns.
    "alter table equity_snapshots add column if not exists peak_equity_7d numeric(12,2)",
    "alter table equity_snapshots add column if not exists drawdown_pct numeric(6,4)",
    "alter table equity_snapshots add column if not exists drawdown_stop boolean",
    "alter table orders add column if not exists venue_order_id varchar(64)",
    "alter table orders add column if not exists order_group_id varchar(64)",
    "alter table orders add column if not exists exchange_index_at_place integer",
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
    # Task 9 fix round 1, M1: supports as_measured_table's scan (anchor = 'nw_fill' and
    # horizon = '30m' and fair_changed = true and at_ts >= :since), a sequential scan of
    # `markouts` otherwise on a hot pricing-tick read.
    "create index if not exists ix_markouts_as_measured on markouts (anchor, horizon, at_ts)",
    # One gate report per (evaluation, variant): `harness gate` shares one `evaluated_at` across
    # the rows of a run, so re-running the same evaluation is idempotent while a later one still
    # appends (Task 11 fix round 1, P1). Rows are never rewritten.
    "create unique index if not exists uq_gate_report on gate_reports (evaluated_at, variant_id)",
    # Task 12b telemetry (U6, dashboard design spec §3): every read the dashboard and the
    # report do against these tables is "the newest/last N by time", so each gets one
    # descending-time index; `desc()` on a model-level Index needs a real column object at
    # class-body time, so -- like every other non-trivial index in this file -- these are raw
    # DDL rather than a model __table_args__ entry.
    "create index if not exists ix_metric_samples_name_ts on metric_samples (name, ts desc)",
    "create index if not exists ix_operator_events_ts on operator_events (ts desc)",
    "create index if not exists ix_game_score_events_game_ts on game_score_events (game_id, ts desc)",
    "create index if not exists ix_check_results_ts on check_results (ts desc)",
    "create index if not exists ix_report_runs_week on report_runs (year, week, generated_at desc)",
    # Phase 4 §7: venue_requests is append-only and read by newest-first (outage/rate checks).
    "create index if not exists ix_venue_requests_ts on venue_requests (ts desc)",
    # Phase 4 §9.3: the risk gate reads equity_snapshots per variant over a trailing window on
    # every pricing tick (`risk.stopped_variants`, `risk.peak_equity_7d`) and once per equity
    # sample. A plain btree on (variant_id, ts) serves both; the table is small (one row per
    # exec variant per 300 s), so this is about keeping a hot per-tick read off a sort rather
    # than about the table's size (Task 11 fix round 1).
    "create index if not exists ix_equity_variant_ts on equity_snapshots (variant_id, ts)",
)

#: Carried fix 16. BRIN on `fair_values(created_at)` so the bounded staleness check
#: (harness/ops/checks.py) can prune to the last 24 h instead of scanning 475 MB. CONCURRENTLY
#: because `fair_values` takes a write on every pricing tick and init-db runs on every deploy;
#: the connection is already AUTOCOMMIT, which is what CONCURRENTLY requires.
_CONCURRENT_INDEX_DDL = (
    "create index concurrently if not exists ix_fair_created_brin "
    "on fair_values using brin (created_at)",
    # Fix 25 (F65: every index on a bulk table -- odds_snapshots included -- is created
    # CONCURRENTLY, no carve-out): the dashboard's odds-staleness read (`_data_quality`)
    # filters odds_snapshots by fetched_at alone; the only existing index leads with
    # game_id/market_type, so this was a seq scan. A covering index on (fetched_at, book,
    # book_last_update) turns it into an index-only range scan. Also declared on
    # OddsSnapshot.__table_args__ so create_all gives it to fresh databases (including the
    # test database); this entry is what gets it onto the populated production database on
    # the next init-db.
    "create index concurrently if not exists ix_odds_fetched_book "
    "on odds_snapshots (fetched_at, book, book_last_update)",
    # Phase 4.5 (addendum §0.4a): the corrected `intents_without_order_or_skip` (T1) asks, per
    # candidate intent, whether an order was working on its (variant_id, venue_market_id, side)
    # key at the intent's own created_at. `orders` carries no index on that triple and none on
    # any time column, so without this the added clause is a correlated sequential scan and the
    # check degrades to a daily `skip`. CONCURRENTLY because `orders` takes writes on the
    # executor's 15 s loop and init-db runs on every deploy; the connection is already
    # AUTOCOMMIT, which is what CONCURRENTLY requires. `migrations/versions/0002_phase45.py`
    # mirrors it, and the two must land together or the catalogue diff fails.
    "create index concurrently if not exists ix_orders_key_placed "
    "on orders (variant_id, venue_market_id, side, placed_at)",
)

#: Open contracts and their average price per variant, from the fills of live orders that have
#: not settled yet, on either fill method that is money: `queue_model` (inferred against the
#: recorded tape) and `venue` (the venue's own report). snapshot_cross and no_watcher fills are
#: counterfactuals, not positions. The list matches `store.MONEY_FILL_METHODS` exactly -- the
#: view and `store._POSITIONS` answer the same question and must not diverge on which fills are
#: real (Task 11 fix round 1, Important 2). Widening a `create or replace view` is additive:
#: the column list is unchanged, so nothing that reads it needs to know.
_POSITIONS_VIEW = """
create or replace view positions as
select o.variant_id,
       o.ticker,
       o.side,
       sum(f.contracts) as open_contracts,
       sum(f.contracts * f.prob) / nullif(sum(f.contracts), 0) as avg_price
from fills f
join orders o on o.id = f.order_id
where f.fill_method in ('queue_model', 'venue')
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
#: Replay orders are excluded, like they are from `positions`: a replay run re-simulates a
#: market the live executor is working, and its orders must not splice into a live episode.
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
    where o.replay = false
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

#: BRIN keeps "last hour" dashboard scans cheap on append-only, time-ordered tables without a
#: large btree, and is cheap enough to build on a live table (it stores one summary per block
#: range, not one entry per row), so it is not behind `_takes_new_indexes`.
_TAPE_BRIN_DDL = {
    "orderbook_events": ("create index if not exists ix_obe_ts_brin on orderbook_events using brin (ts)",),
    "venue_trades": ("create index if not exists ix_trades_ts_brin on venue_trades using brin (ts)",),
}

#: Tape-table DDL, run last (see TAPE_TABLES).
_TAPE_DDL = _TAPE_BRIN_DDL["orderbook_events"] + _TAPE_BRIN_DDL["venue_trades"] + (
    # F5: Kalshi deprecated trades.taker_side in favour of these two.
    "alter table venue_trades add column if not exists taker_outcome_side varchar(4)",
    "alter table venue_trades add column if not exists taker_book_side varchar(4)",
)

#: (name, body) of the btree indexes Task 2b adds to the tape, per table. The phase 3 book loader
#: reads the newest snapshot per ticker, applies deltas by id and looks for a later gap on the
#: anchor's sid; the REST trade writer dedupes on `(venue, trade_id)`.
#:
#: Two spellings of each: `create_schema` builds `<name>` on the table itself, and the one-off
#: partition migration builds `<name>_legacy` CONCURRENTLY on the live table before renaming it
#: aside, so the attach finds a matching index instead of building one under an exclusive lock.
TAPE_NEW_INDEXES: dict[str, tuple[tuple[str, str], ...]] = {
    "orderbook_events": (
        ("ix_obe_snapshot", "on orderbook_events (ticker, ts desc) where kind = 'snapshot'"),
        ("ix_obe_ticker_id", "on orderbook_events (ticker, id)"),
        ("ix_obe_gap", "on orderbook_events (sid, id) where kind = 'gap'"),
    ),
    "venue_trades": (
        ("ix_trades_venue_trade_id", "on venue_trades (venue, trade_id)"),
    ),
}


def tape_index_ddl(table: str) -> tuple[str, ...]:
    """Every index statement one tape table needs, for a caller that has just created the table
    itself (the partition migration's fresh parent). create_schema splits them instead: the BRIN
    runs unconditionally, the btrees only under `_takes_new_indexes`."""
    return _TAPE_BRIN_DDL[table] + tuple(f"create index if not exists {name} {body}"
                                         for name, body in TAPE_NEW_INDEXES[table])


def _takes_new_indexes(conn: Connection, table: str) -> bool:
    """Whether `create_schema` may build TAPE_NEW_INDEXES[table] itself.

    A non-concurrent CREATE INDEX on a populated tape table holds a ShareLock for the whole build,
    which locks the WebSocket sink out of the table -- and `init-db` runs on every deploy, with the
    sink writing. So create_schema builds these only where the build cannot hurt: on a table that
    is still empty, or on a partitioned parent, where the migration has already built them (with
    the parent holding nothing but the empty cutover week) and `if not exists` makes the statement
    a no-op. A CREATE INDEX on a parent is not metadata-only -- it recurses into every partition
    and takes a ShareLock on each -- so what keeps this harmless is the `if not exists`, not the
    partitioning. On the live pre-migration table `partition-bulk-tables` builds them CONCURRENTLY.
    """
    if is_partitioned(conn, table):
        return True
    exists = conn.execute(text(
        "select 1 from pg_class where relname = :t and relkind = 'r'"), {"t": table}).first()
    return bool(exists) and conn.execute(text(f"select 1 from {table} limit 1")).first() is None


def _is_lock_failure(exc: BaseException) -> bool:
    return getattr(getattr(exc, "orig", None), "sqlstate", None) in LOCK_SQLSTATES


def _retry_once(conn: Connection, run: Callable[[], None], label: str) -> None:
    """Run one DDL step, retrying it once if another session held the lock.

    The connection is in autocommit, so each step's locks are taken and released within the step
    itself. Without that, `init-db` deadlocked against the WebSocket sink: the ALTERs on
    venue_trades hold an AccessExclusiveLock until commit, the later CREATE INDEX on
    orderbook_events waits for a ShareLock behind the sink's open insert batch, and the sink's
    next insert into venue_trades waits on init-db.
    """
    try:
        run()
        return
    except DBAPIError as exc:
        if not _is_lock_failure(exc):
            raise
        log.warning("ddl lock contention, retrying once: %s", label)
    conn.rollback()
    run()


def _execute_ddl(conn: Connection, statement: str) -> None:
    _retry_once(conn, lambda: conn.execute(text(statement)), " ".join(statement.split())[:80])


def _model_indexes() -> list[Index]:
    """The model indexes create_schema builds (F47): create_all only builds indexes for tables it
    creates, so a database that predates a model index never gets it.

    The tape tables are excluded. Their indexes are Task 2b's and must be built CONCURRENTLY:
    a non-concurrent CREATE INDEX on a populated orderbook_events or venue_trades locks out the
    WebSocket sink for as long as the build takes.
    """
    return [index for table in Base.metadata.sorted_tables if table.name not in TAPE_TABLES
            for index in table.indexes]


def _model_index_ddl(conn: Connection) -> None:
    for index in _model_indexes():
        _retry_once(conn, lambda index=index: index.create(conn, checkfirst=True),
                    f"index {index.name}")


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
        _model_index_ddl(conn)
        for statement in _CONCURRENT_INDEX_DDL:
            _execute_ddl(conn, statement)
        for statement in _TAPE_DDL:
            _execute_ddl(conn, statement)
        for table in TAPE_TABLES:
            if not _takes_new_indexes(conn, table):
                log.info("skipping %s btree indexes: table is live and not partitioned yet", table)
                continue
            for name, body in TAPE_NEW_INDEXES[table]:
                _execute_ddl(conn, f"create index if not exists {name} {body}")


def drop_schema(engine: Engine) -> None:
    """Drop every view and every model table. Generated from the metadata so a new model can
    never be left behind in a test database."""
    tables = ", ".join(sorted(Base.metadata.tables))
    with engine.begin() as conn:
        conn.execute(text("drop view if exists positions, clv, order_episodes"))
        conn.execute(text(f"drop table if exists {tables} cascade"))
