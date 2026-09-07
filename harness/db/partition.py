"""The one-off migration that turns the live bulk tape tables into weekly range partitions.

Run once, by hand, through `harness partition-bulk-tables` in a quiet window (addendum §6, F19).
`orderbook_events` takes up to 4M rows an hour at peak; a season of tape only fits the NAS if it
can be archived and dropped a week at a time, and every phase 3 read of the tape is a "newest rows
for this ticker" query that a weekly partition answers without touching the season.

The migration preserves every row and rewrites none of them: the live table is renamed aside, a
partitioned parent is created in its place, and the old table is attached as the pre-cutover
partition. It is the only place in the harness that renames anything on the production database
(design addendum §9 decision D15).

Idempotent in the sense the plan asks for: a table already in `pg_partitioned_table` is skipped,
so running the command twice migrates nothing the second time. That also means a failure after
step (2) has committed leaves the table half-migrated and a re-run will not resume it -- the
parent is live and taking writes, but the legacy rows are still in a detached `<table>_legacy`.
Finishing that by hand is steps (3) to (5) of `_migrate`, in order.

Writers are blocked only for step (2), which takes an AccessExclusiveLock to rename the table and
does no scanning. The two scans (validating the CHECK and building the parent's primary-key index
on the attached partition) happen in steps (3) and (4) under weaker locks, which is why the CLI
builds its engine with the 900 s batch statement timeout rather than the 30 s default.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import Connection, Engine, text

from harness.db.models import Base
from harness.db.schema import (TAPE_NEW_INDEXES, TAPE_TABLES, _partition_name, is_partitioned,
                               tape_index_ddl, week_bounds)

log = logging.getLogger(__name__)

#: The names the legacy table has to give up to the new parent, per table: its primary key and the
#: indexes `create_schema` builds under those names. Each is renamed to `<name>_legacy`, which is
#: also the name step (1) builds the new indexes under, so the attach in (4) matches them.
_RENAMED_INDEXES = {
    "orderbook_events": ("orderbook_events_pkey", "ix_obe_ticker_ts", "ix_obe_ts_brin"),
    "venue_trades": ("venue_trades_pkey", "ix_trades_ticker_ts", "ix_trades_ts_brin"),
}


def _relation_exists(conn: Connection, name: str) -> bool:
    return conn.execute(text("select 1 from pg_class where relname = :n"), {"n": name}).first() is not None


def _drop_if_invalid(conn: Connection, index: str) -> None:
    """CREATE INDEX CONCURRENTLY leaves an invalid index behind when it fails. `if not exists`
    would then skip the rebuild and the attach would find nothing to match, so clear it first."""
    invalid = conn.execute(text(
        "select 1 from pg_index i join pg_class c on c.oid = i.indexrelid "
        "where c.relname = :n and not i.indisvalid"), {"n": index}).first()
    if invalid:
        log.warning("dropping %s: left invalid by a failed concurrent build", index)
        conn.execute(text(f"drop index concurrently if exists {index}"))


def _cutover_bound(conn: Connection, legacy: str, approx: datetime | None,
                   fallback: datetime) -> datetime:
    """The first instant that belongs to the partitioned side: `max(ts) + 1 ms`.

    `approx` is the maximum read before the rename, outside any transaction. Re-reading the
    maximum inside the rename's transaction is what makes the bound exact -- rows kept arriving
    while the pre-read ran -- but `max(ts)` alone is a full heap scan, and this transaction holds
    the lock that stops the WebSocket sink. Bounding the re-read below by the pre-read lets the
    BRIN index on `ts` prune it to the last few block ranges of an append-ordered table, so the
    lock is held for the rename and little else.
    """
    if approx is None:
        top = conn.execute(text(f"select max(ts) from {legacy}")).scalar()
    else:
        top = conn.execute(text(f"select max(ts) from {legacy} where ts >= :since"),
                           {"since": approx}).scalar()
    if top is None:
        return fallback.astimezone(timezone.utc)
    return top.astimezone(timezone.utc) + timedelta(milliseconds=1)


def _migrate(engine: Engine, table: str, now: datetime, cutover: datetime | None) -> None:
    legacy = f"{table}_legacy"
    started = time.monotonic()

    # (1) Build the indexes Task 2b adds, on the live table, CONCURRENTLY and already under their
    #     post-rename `_legacy` names. CREATE INDEX CONCURRENTLY cannot run inside a transaction
    #     block, hence the AUTOCOMMIT connection. An index create_schema already built (the table
    #     was empty at some init-db) is left alone: step (2) renames it instead.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name, body in TAPE_NEW_INDEXES[table]:
            if _relation_exists(conn, name):
                continue
            _drop_if_invalid(conn, f"{name}_legacy")
            log.info("building %s_legacy concurrently on %s", name, table)
            conn.execute(text(f"create index concurrently if not exists {name}_legacy {body}"))
        # The newest ts on the live table, read after the index builds (which take much longer
        # than this) and outside any transaction: it is the lower bound that keeps step (2)'s
        # exact re-read off the whole heap. See `_cutover_bound`.
        approx = conn.execute(text(f"select max(ts) from {table}")).scalar()

    # (2) One short transaction: rename the live table, its indexes and (for orderbook_events) its
    #     sequence aside, create the partitioned parent under the original name, and give the
    #     legacy table a NOT VALID check on ts. Writers wait only for this.
    with engine.begin() as conn:
        conn.execute(text(f"alter table {table} rename to {legacy}"))
        for name in _RENAMED_INDEXES[table] + tuple(n for n, _ in TAPE_NEW_INDEXES[table]):
            if _relation_exists(conn, name):
                conn.execute(text(f"alter index {name} rename to {name}_legacy"))
        if table == "orderbook_events":
            conn.execute(text(f"alter sequence {table}_id_seq rename to {legacy}_id_seq"))

        bound = _cutover_bound(conn, legacy, approx, cutover or now)
        Base.metadata.tables[table].create(conn)
        for statement in tape_index_ddl(table):
            conn.execute(text(statement))

        # The cutover week is partial: it starts at the bound, not at the Monday, because
        # everything earlier lives in the legacy partition. `ensure_partitions` skips the name
        # from here on, so the partial range stands.
        week_start, week_end = week_bounds(bound)
        cutover_partition = _partition_name(table, week_start)
        conn.execute(text(f"create table {cutover_partition} partition of {table} "
                          f"for values from ('{bound.isoformat()}') to ('{week_end.isoformat()}')"))
        conn.execute(text(f"alter table {legacy} add constraint ck_{legacy}_ts "
                          f"check (ts < '{bound.isoformat()}') not valid"))
    log.info("%s: renamed aside, cutover at %s (%.1fs)", table, bound.isoformat(),
             time.monotonic() - started)

    # (3) Validate the check. A full scan of the legacy heap, but under a ShareUpdateExclusiveLock,
    #     so the writers that are now going to the parent keep running. Doing it here lets the
    #     attach in (4) trust the constraint instead of scanning again to prove the same thing.
    with engine.begin() as conn:
        conn.execute(text(f"alter table {legacy} validate constraint ck_{legacy}_ts"))
    log.info("%s: constraint validated (%.1fs)", table, time.monotonic() - started)

    # (4) Attach the legacy table as everything before the cutover. Postgres builds the parent's
    #     primary key on it -- the legacy key was `(id)` or `(venue, trade_id)`, the parent's
    #     carries `ts` too -- which is the longest step of the migration. A table may hold only
    #     one primary key, so the renamed legacy one is dropped first; its rows lose nothing,
    #     the index the attach builds covers them under the wider key.
    with engine.begin() as conn:
        conn.execute(text(f"alter table {legacy} drop constraint if exists {table}_pkey_legacy"))
        conn.execute(text(f"alter table {table} attach partition {legacy} "
                          f"for values from (minvalue) to ('{bound.isoformat()}')"))
    log.info("%s: legacy partition attached (%.1fs)", table, time.monotonic() - started)

    # (5) orderbook_events only: the parent got a fresh sequence, so continue it from the legacy
    #     maximum rather than restarting ids at 1. Phase 3's book loader orders deltas by id.
    if table == "orderbook_events":
        with engine.begin() as conn:
            top_id = conn.execute(text(f"select max(id) from {legacy}")).scalar()
            if top_id is not None:
                conn.execute(text("select setval(pg_get_serial_sequence('orderbook_events', 'id'), :v)"),
                             {"v": top_id})
    log.info("%s: partitioned in %.1fs", table, time.monotonic() - started)


def partition_bulk_tables(engine: Engine, now: datetime,
                          cutover: datetime | None = None) -> list[str]:
    """Partition `orderbook_events` and `venue_trades` in place. Returns the tables it migrated.

    Idempotent: a table already in `pg_partitioned_table` is skipped, so a second run returns [].
    `cutover` names the first partitioned instant for a table that has no rows to bound it;
    `now` is used when it is not given.
    """
    migrated: list[str] = []
    for table in TAPE_TABLES:
        with engine.connect() as conn:
            if is_partitioned(conn, table):
                log.info("%s is already partitioned, skipping", table)
                continue
        _migrate(engine, table, now, cutover)
        migrated.append(table)
    return migrated
