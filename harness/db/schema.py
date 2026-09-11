import logging
import re
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
    "create index if not exists ix_raw_fetched_brin on raw_responses using brin (fetched_at) "
    "with (autosummarize = on)",
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
    # --- phase 5 (addendum §2, ruling B-M13) ------------------------------------------------
    # H7's panel groups by (series, week), which is exactly how the weekly pass writes and how
    # a later report table reads.
    "create index if not exists ix_futures_series_week on futures_snapshots "
    "(series_ticker, snapshot_week)",
    # The veto's feature builder asks for "the newest weather snapshot before the signal" per
    # game, and the tick's fetch order is oldest-snapshot-first per game: both are a head read
    # off (game_id, fetched_at).
    "create index if not exists ix_weather_game_fetched on weather_snapshots "
    "(game_id, fetched_at)",
    # `subject_id` is the join back to the signal, the report run or the card (R:225-229).
    "create index if not exists ix_research_notes_subject on research_notes (subject_id)",
    # The worker's claim reads only unclaimed rows, so the index is partial on exactly that
    # predicate: the queue's claimed tail grows for the season and must never be scanned.
    "create index if not exists ix_veto_queue_open on veto_queue "
    "(bucket_start, game_id, market_type) where claimed_at is null",
    # t7 and the 24 h verification row read the newest decisions.
    "create index if not exists ix_veto_decisions_decided on veto_decisions (decided_at desc)",
    # T19 fix round 1: t7's window filter reads `veto_decisions.signal_created_at` (through the
    # `veto_h9` view, which carries no time filter of its own), and this is the only column that
    # bounds it -- without an index here, every weekly report does a sequential scan of the
    # whole, ever-growing table.
    "create index if not exists ix_veto_decisions_signal_created on veto_decisions "
    "(signal_created_at desc)",
    # The report's arrival counts and the verification row read the newest RFQs.
    "create index if not exists ix_rfqs_received on rfqs (received_at desc)",
    # One quote per RFQ: the listener computes once, on arrival. This is also what gives the
    # "no quote without an rfq" invariant a partner that a re-delivery cannot break.
    "create unique index if not exists uq_rfq_quote_rfq on rfq_quotes (rfq_id)",
    # Pulse's research section bounds on this column (review T16 Important 1): without this,
    # `rfq_quotes`, a BOUNDED_TABLES table, is filtered on `computed_at` after a sequential scan.
    "create index if not exists ix_rfq_quotes_computed on rfq_quotes (computed_at desc)",
)

#: Carried fix 16. BRIN on `fair_values(created_at)` so the bounded staleness check
#: (harness/ops/checks.py) can prune to the last 24 h instead of scanning 475 MB. CONCURRENTLY
#: because `fair_values` takes a write on every pricing tick and init-db runs on every deploy;
#: the connection is already AUTOCOMMIT, which is what CONCURRENTLY requires.
_CONCURRENT_INDEX_DDL = (
    "create index concurrently if not exists ix_fair_created_brin "
    "on fair_values using brin (created_at) with (autosummarize = on)",
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
    # Fix 35 (journal 109's 03:15-03:45 CT incident), corrected in round 1 (review Important 1):
    # the RFQ quote's fair-value lookup (`_LEG` in `harness/venues/kalshi/rfq_quote.py`, and
    # `_CLOSING_LEG` in `harness/settlement/rfq_grade.py`) reads `fair_values` on this five-column
    # shape under this partial predicate -- but the first cut indexed the three nullable columns
    # bare while both reads compared them with `is not distinct from`, which Postgres cannot
    # turn into an index condition, so the index was never chosen (measured: both reads kept
    # walking `ix_fair_game_type_created (game_id, market_type, created_at)` backwards and
    # rechecking every candidate row by hand). `coalesce(...)` on the three nullable columns here
    # and the identical `coalesce(...) = coalesce(...)` in both reads make the comparison a
    # plain equality the planner can use -- the same shape `uq_fair_value_row` already uses for
    # the same three columns, just with `threshold`'s sentinel at -9999 rather than 0 so a real
    # 0.0 spread/total line is never conflated with "no threshold on this leg" the way a shared
    # sentinel with that unique index would. Without it, both walked the wider
    # `ix_fair_game_type_created (game_id, market_type, created_at)` and re-checked the rest of
    # the predicates row by row. CONCURRENTLY because `fair_values` takes a write on every
    # pricing tick and init-db runs on every deploy; the connection is already AUTOCOMMIT, which
    # is what CONCURRENTLY requires. `migrations/versions/0005_rfq_lookup.py` mirrors it, and the
    # two must land together or the catalogue diff fails.
    "create index concurrently if not exists ix_fair_leg_lookup "
    "on fair_values (game_id, market_type, coalesce(outcome_team_id, -1), "
    "coalesce(outcome_side, ''), coalesce(threshold, -9999), created_at desc) "
    "where fair_source = 'direct'",
    # Fix 42 (the 13:03 CT 2026-09-11 pricing outage): `build_gap_snapshots`
    # (`harness/pricing/gaps.py`) reads a whole run's quotes -- `venue_quotes.run_id = :run_id`
    # joined to the matched markets of games kicking off inside the window -- and no index on
    # this 3.58 M row, 773 MB table led with `run_id` (`pkey`, `uq_quote_raw_market` and
    # `ix_quotes_market_fetched` are the other three). The live plan was a nested loop: an index
    # scan of `ix_quotes_market_fetched` for each of the 1,871 matched markets with `run_id` only
    # a *filter*, so every quote ever recorded for the market was walked to keep the ~3 of this
    # run -- cost 52,000, past the 30 s statement timeout on every run, `runs.status = degraded`
    # and no fair values at all. `(run_id, venue_market_id)` makes `run_id` the scan key.
    # CONCURRENTLY because `venue_quotes` is a bulk table taking the recorder's inserts and
    # init-db runs on every deploy -- fix 25's F65 rule is that every index on a bulk table is
    # built CONCURRENTLY with no carve-out; the connection is already AUTOCOMMIT, which is what
    # CONCURRENTLY requires. Also declared on `VenueQuote.__table_args__` so `create_all` gives
    # it to fresh databases (the test database included); this entry is what gets it onto the
    # populated production database on the next init-db -- with one caveat a reader needs:
    # `_model_index_ddl` runs before this tuple and would issue a plain, table-locking
    # `create index` for the model declaration on a populated database that lacks the index,
    # since it excludes only `TAPE_TABLES` and `venue_quotes` is a bulk table that is not a tape
    # table. Fix 42 round 1 (review Important 1) closes that: `_model_indexes` now skips any
    # index this tuple builds, so on a populated database the only statement that creates this
    # index -- or fix 25's `ix_odds_fetched_book`, which had the same shape -- is the
    # CONCURRENTLY one here.
    # `migrations/versions/0006_quotes_run_index.py` mirrors it, and the two must land together
    # or the catalogue diff fails.
    "create index concurrently if not exists ix_quotes_run_market "
    "on venue_quotes (run_id, venue_market_id)",
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

#: H9's population, defined once (ruling B-M2). t7 and the phase's own invariant read this view
#: rather than re-deriving its three filters, because every place that re-derived them would be
#: a place that could quietly disagree: the shadow's row is recorded and never used, the study's
#: frozen replays must never join into H9, and only the three *decided* labels are outcomes --
#: `veto_skipped_budget` and `veto_error` are the budget's and the machine's, not the model's.
_VETO_H9_VIEW = """
create or replace view veto_h9 as
select d.signal_id,
       d.call_id,
       d.decision,
       d.confidence,
       d.from_cache,
       d.signal_created_at,
       d.decided_at,
       n.model,
       n.effort,
       n.prompt_hash,
       n.cost_usd,
       n.latency_ms
from veto_decisions d
join research_notes n on n.call_id = d.call_id
where n.kind = 'veto'
  and n.replay = false
  and n.model = 'claude-opus-5'
  and d.decision in ('proceed', 'reduce', 'veto')
"""

_VIEW_DDL = (_POSITIONS_VIEW, _CLV_VIEW, _ORDER_EPISODES_VIEW, _VETO_H9_VIEW)

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
    "orderbook_events": ("create index if not exists ix_obe_ts_brin on orderbook_events "
                         "using brin (ts) with (autosummarize = on)",),
    "venue_trades": ("create index if not exists ix_trades_ts_brin on venue_trades "
                     "using brin (ts) with (autosummarize = on)",),
}

#: Tape-table DDL, run last (see TAPE_TABLES).
_TAPE_DDL = _TAPE_BRIN_DDL["orderbook_events"] + _TAPE_BRIN_DDL["venue_trades"] + (
    # F5: Kalshi deprecated trades.taker_side in favour of these two.
    "alter table venue_trades add column if not exists taker_outcome_side varchar(4)",
    "alter table venue_trades add column if not exists taker_book_side varchar(4)",
)

# --- fix 32: every BRIN index autosummarizes ---------------------------------------------
# None of the nine production BRIN indexes had `autosummarize` set: a BRIN bitmap scan returns
# every *unsummarized* block range as a match, so a read bounded by a BRIN column walked every
# page inserted since the last vacuum of that table -- gigabytes on the weekly `orderbook_events`
# partition. The four CREATE statements above now declare `with (autosummarize = on)`, which
# covers every database built from here on; this section retrofits one already running.

#: The four BRIN indexes this schema declares by name (the same ones the CREATE statements above
#: now build `with (autosummarize = on)`). Named so a reader can see exactly what create_schema
#: means to cover, independent of the `pg_class` sweep below.
BRIN_AUTOSUMMARIZE = (
    "ix_raw_fetched_brin",
    "ix_fair_created_brin",
    "ix_obe_ts_brin",
    "ix_trades_ts_brin",
)

#: Every physical BRIN index (`relkind = 'i'`) still missing the option. Restricted to `'i'`
#: because a *partitioned* BRIN index (`relkind = 'I'` -- what `ix_obe_ts_brin`/`ix_trades_ts_brin`
#: become once `partition-bulk-tables` has run) holds no pages of its own: `ALTER INDEX ... SET`
#: on one is a Postgres error ("not supported for partitioned indexes"), not a no-op. A partitioned
#: index gets the option from its own CREATE instead (it is set at creation time above and in
#: `tape_index_ddl`), and that is enough -- a partition attached under `create table ... partition
#: of ...` inherits its parent index's reloptions automatically. This sweep is what reaches the
#: indexes a name can't: the weekly partitions PostgreSQL names itself
#: (`orderbook_events_y2026w37_ts_idx`) and the pre-partition `_legacy` ones.
_BRIN_SWEEP_SQL = """
    select c.relname
    from pg_class c
    join pg_am am on am.oid = c.relam
    where am.amname = 'brin'
      and c.relkind = 'i'
      and (c.reloptions is null or not (c.reloptions @> array['autosummarize=on']))
"""


def _brin_needs_autosummarize(conn: Connection, name: str) -> bool:
    """True if `name` is a physical BRIN index (`relkind = 'i'`) that does not already have the
    option. False for a missing relation (nothing to alter yet, e.g. a fresh database before
    this call's CREATE statements have run) and for a partitioned index (see `_BRIN_SWEEP_SQL`)."""
    row = conn.execute(text(
        "select c.relkind, c.reloptions from pg_class c "
        "where c.relname = :n"), {"n": name}).first()
    if row is None or row[0] != "i":
        return False
    return "autosummarize=on" not in (row[1] or [])


def _set_brin_autosummarize(conn: Connection) -> None:
    """Alter every BRIN index `BRIN_AUTOSUMMARIZE` names that still needs the option, then sweep
    `pg_class` for the rest. Idempotent: on a database that already has it set everywhere (the
    common case on every deploy after the first), this alters nothing and logs nothing."""
    altered = [name for name in BRIN_AUTOSUMMARIZE if _brin_needs_autosummarize(conn, name)]
    for name in altered:
        _execute_ddl(conn, f"alter index if exists {name} set (autosummarize = on)")
    swept = conn.execute(text(_BRIN_SWEEP_SQL)).scalars().all()
    for name in swept:
        _execute_ddl(conn, f'alter index if exists "{name}" set (autosummarize = on)')
    altered += swept
    if altered:
        log.info("brin autosummarize set: %s", ", ".join(altered))


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


#: Identifier in a DDL statement: bare, or double-quoted (fix 37's parser lower-cases the whole
#: statement first, so a quoted identifier's case is not preserved here -- none of the DDL this
#: file writes needs that, and the parser exists only to recognize *this file's own* statements).
_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)'

#: `alter table <t> add column if not exists <c> ...`. The trailing `(?=\s|$)` stands in for a
#: word boundary: a quoted identifier ends on `"`, which is not a word character, so `\b` right
#: after it never matches.
_ADD_COLUMN_RE = re.compile(
    rf"^alter\s+table\s+({_IDENT})\s+add\s+column\s+if\s+not\s+exists\s+({_IDENT})(?=\s|$)")

#: `create [unique] index [concurrently] if not exists <name> ...`
_CREATE_INDEX_RE = re.compile(
    rf"^create\s+(?:unique\s+)?index\s+(?:concurrently\s+)?if\s+not\s+exists\s+({_IDENT})(?=\s|$)")


def ddl_target(statement: str) -> tuple[str, str, str] | tuple[str, str] | None:
    """What `statement` adds, for the two shapes `create_schema` can skip once the target
    already exists (fix 37, journal 110/112): `("column", table, column)` for an
    `add column if not exists`, `("index", name)` for a `create [unique] index [concurrently]
    if not exists`. `None` for anything else -- a view, a backfill, an `alter ... alter column`,
    a `set` -- which always runs exactly as before; only these two shapes ask for a lock a
    no-op does not need.
    """
    normalized = " ".join(statement.split()).lower()
    m = _ADD_COLUMN_RE.match(normalized)
    if m:
        return ("column", m.group(1).strip('"'), m.group(2).strip('"'))
    m = _CREATE_INDEX_RE.match(normalized)
    if m:
        return ("index", m.group(1).strip('"'))
    return None


def _exists(conn: Connection, target: tuple[str, ...]) -> bool:
    """Whether `target` (from `ddl_target`) is already there, so the DDL that would add it is a
    genuine no-op that does not need to ask for its lock."""
    if target[0] == "column":
        _, table, column = target
        return conn.execute(text(
            "select 1 from information_schema.columns where table_schema = current_schema() "
            "and table_name = :t and column_name = :c"),
            {"t": table, "c": column}).first() is not None
    _, name = target  # target[0] == "index"
    # relkind 'i' is a plain index; 'I' is the parent index of a partitioned table (e.g. every
    # index create_schema builds on raw_responses, orderbook_events or venue_trades, which are
    # partitioned from the model itself) -- either one means the index is already there.
    return conn.execute(text(
        "select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where c.relkind in ('i', 'I') and n.nspname = current_schema() and c.relname = :n"),
        {"n": name}).first() is not None


def _execute_ddl(conn: Connection, statement: str) -> bool:
    """Run one DDL statement, unless `ddl_target` recognizes its shape and the target it would
    add already exists -- a no-op `add column if not exists` still asks for an AccessExclusive
    lock, and a no-op `create index if not exists` still asks for a Share lock, before Postgres
    notices there is nothing to do (fix 37: this is what queued a deploy's schema step behind a
    live snapshot builder for 236-286s and hit the 5s DDL_LOCK_TIMEOUT). Returns whether the
    statement was skipped, so a caller can tally them."""
    target = ddl_target(statement)
    if target is not None and _exists(conn, target):
        log.debug("init-db: skipping no-op ddl: %s", " ".join(statement.split())[:80])
        return True
    _retry_once(conn, lambda: conn.execute(text(statement)), " ".join(statement.split())[:80])
    return False


def _concurrent_index_names() -> frozenset[str]:
    """The index names `_CONCURRENT_INDEX_DDL` builds, read out of the statements themselves
    through fix 37's `ddl_target` so there is one parser here and no second list to keep in step.

    `_model_indexes` subtracts these. An index declared on a model *and* listed in that tuple
    (fix 25's `ix_odds_fetched_book`, fix 42's `ix_quotes_run_market`) would otherwise be built
    twice on one `create_schema` run, and the first of the two is `_model_index_ddl`'s plain
    `create index`, which holds a ShareLock against the recorder for the whole build on a
    populated bulk table -- exactly what CONCURRENTLY is there to avoid, and it would leave the
    CONCURRENTLY statement a no-op (review Important 1, fix 42 round 1). A fresh database is
    unaffected: `create_all` builds a model index as part of creating its table, before any of
    this runs.

    Strict on purpose, twice over. An entry `ddl_target` does not read as an index, or one
    written without CONCURRENTLY, raises at import rather than dropping out of this set and
    re-opening the hazard for the index it names.
    """
    names = set()
    for statement in _CONCURRENT_INDEX_DDL:
        target = ddl_target(statement)
        normalized = " ".join(statement.split()).lower()
        if target is None or target[0] != "index" or " concurrently " not in normalized:
            raise ValueError(
                f"_CONCURRENT_INDEX_DDL entry is not a `create index concurrently if not "
                f"exists` statement: {statement!r}")
        names.add(target[1])
    return frozenset(names)


_CONCURRENT_INDEX_NAMES = _concurrent_index_names()


def _model_indexes() -> list[Index]:
    """The model indexes create_schema builds (F47): create_all only builds indexes for tables it
    creates, so a database that predates a model index never gets it.

    Two exclusions, both for the same reason -- a plain CREATE INDEX on a populated bulk table
    holds a ShareLock against the writer for the whole build:

    * The tape tables. Their indexes are Task 2b's and are built CONCURRENTLY further down
      `create_schema`.
    * Any index `_CONCURRENT_INDEX_DDL` already builds. `ix_odds_fetched_book` (fix 25) and
      `ix_quotes_run_market` (fix 42) are declared on their models *and* listed there, and this
      loop runs first, so without the subtraction the plain create would win the race and the
      CONCURRENTLY statement would find the index already present (review Important 1, fix 42
      round 1).
    """
    return [index for table in Base.metadata.sorted_tables if table.name not in TAPE_TABLES
            for index in table.indexes if index.name not in _CONCURRENT_INDEX_NAMES]


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
    skipped = 0
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"set lock_timeout = '{DDL_LOCK_TIMEOUT}'"))
        for statement in _COLUMN_DDL + _INDEX_DDL + _VIEW_DDL + _BACKFILL_DDL:
            skipped += _execute_ddl(conn, statement)
        _model_index_ddl(conn)
        for statement in _CONCURRENT_INDEX_DDL:
            skipped += _execute_ddl(conn, statement)
        for statement in _TAPE_DDL:
            skipped += _execute_ddl(conn, statement)
        for table in TAPE_TABLES:
            if not _takes_new_indexes(conn, table):
                log.info("skipping %s btree indexes: table is live and not partitioned yet", table)
                continue
            for name, body in TAPE_NEW_INDEXES[table]:
                skipped += _execute_ddl(conn, f"create index if not exists {name} {body}")
        _set_brin_autosummarize(conn)
    log.info("init-db: %d no-op DDL statements skipped", skipped)


def drop_schema(engine: Engine) -> None:
    """Drop every view and every model table. Generated from the metadata so a new model can
    never be left behind in a test database."""
    tables = ", ".join(sorted(Base.metadata.tables))
    with engine.begin() as conn:
        conn.execute(text("drop view if exists positions, clv, order_episodes, veto_h9"))
        conn.execute(text(f"drop table if exists {tables} cascade"))
