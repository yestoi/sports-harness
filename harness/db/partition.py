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
Finishing that by hand is steps (3) and (4) of `_migrate`, in order.

Every scan the migration needs happens in step (1), on the live table, concurrently, holding no
lock a writer waits on. Step (2) takes an AccessExclusiveLock to rename the table but reads only
the tail of it; step (3) scans the legacy heap under a ShareUpdateExclusiveLock, which writers do
not queue behind; step (4) is metadata-only because step (1) already built the key the attach
needs. The CLI still builds its engine with the 900 s batch statement timeout: step (1)'s builds
and step (3)'s scan are minutes of work on a season of tape, far past the 30 s default.

One window is not closed by any of this. Between step (2)'s commit and step (4)'s attach, the
parent's only partition is the cutover week, so a row stamped before the bound -- a delta whose
venue `ts_ms` lags, a REST trade with an older `created_time` -- has nowhere to go and its insert
fails. The live run stops the writers for the migration; that is an operational step, not
something the code can do for itself.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from harness.db.models import Base
from harness.db.schema import (TAPE_NEW_INDEXES, TAPE_TABLES, _partition_name, ensure_partitions,
                               is_partitioned, tape_index_ddl, week_bounds)

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


def _key_columns(table: str) -> tuple[str, ...]:
    """The partitioned parent's primary key: `(id, ts)` / `(venue, trade_id, ts)`, straight off
    the model so the index step (1) builds cannot drift from the key the attach looks for."""
    return tuple(c.name for c in Base.metadata.tables[table].primary_key.columns)


def _validate_legacy_check(engine: Engine, legacy: str) -> None:
    """Step (3). Factored out so a test can write through the parent while it is running: that is
    the window in which the sequence and the partition bounds have to already be right."""
    with engine.begin() as conn:
        conn.execute(text(f"alter table {legacy} validate constraint ck_{legacy}_ts"))


def _migrate(engine: Engine, table: str, now: datetime, cutover: datetime | None) -> None:
    legacy = f"{table}_legacy"
    started = time.monotonic()

    # (1) Build indexes on the live table, CONCURRENTLY, so nothing later has to build one while
    #     holding a lock. CREATE INDEX CONCURRENTLY cannot run inside a transaction block, hence
    #     the AUTOCOMMIT connection. Two kinds:
    #       - the indexes Task 2b adds, already under their post-rename `_legacy` names. One that
    #         create_schema built (the table was empty at some init-db) is left alone: step (2)
    #         renames it instead.
    #       - `<table>_pkey_new`, unique on the *parent's* key columns. Step (4) turns it into the
    #         legacy partition's primary key, so ATTACH PARTITION finds the key it needs already
    #         built and does not scan the heap to build one itself.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name, body in TAPE_NEW_INDEXES[table]:
            if _relation_exists(conn, name):
                continue
            _drop_if_invalid(conn, f"{name}_legacy")
            log.info("building %s_legacy concurrently on %s", name, table)
            conn.execute(text(f"create index concurrently if not exists {name}_legacy {body}"))
        _drop_if_invalid(conn, f"{table}_pkey_new")
        log.info("building %s_pkey_new concurrently on %s", table, table)
        conn.execute(text(f"create unique index concurrently if not exists {table}_pkey_new "
                          f"on {table} ({', '.join(_key_columns(table))})"))
        # The newest ts on the live table, read after the index builds (which take much longer
        # than this) and outside any transaction: it is the lower bound that keeps step (2)'s
        # exact re-read off the whole heap. See `_cutover_bound`.
        approx = conn.execute(text(f"select max(ts) from {table}")).scalar()

    # (2) One short transaction: rename the live table, its indexes and (for orderbook_events) its
    #     sequence aside, create the partitioned parent under the original name, and give the
    #     legacy table a NOT VALID check on ts. Writers wait only for this, and everything they
    #     need must be right by the time it commits -- they resume against the parent immediately,
    #     while steps (3) and (4) are still running.
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

        # orderbook_events only: `create` above emitted a fresh BIGSERIAL whose sequence starts at
        # 1, so the sequence has to be advanced past the legacy maximum before this transaction
        # commits. A row written a millisecond later would otherwise take an id that already
        # exists on the tape -- silently, since the key is `(id, ts)` -- and phase 3's book loader
        # orders deltas by id. `max(id)` is an index-only scan of the renamed legacy key.
        if table == "orderbook_events":
            top_id = conn.execute(text(f"select max(id) from {legacy}")).scalar()
            if top_id is not None:
                conn.execute(text("select setval(pg_get_serial_sequence('orderbook_events', 'id'), :v)"),
                             {"v": top_id})

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
    _validate_legacy_check(engine, legacy)
    log.info("%s: constraint validated (%.1fs)", table, time.monotonic() - started)

    # (4) Attach the legacy table as everything before the cutover, in one transaction.
    #     ATTACH PARTITION gives the partition a primary key matching the parent's, and a table
    #     holds only one, so the legacy key -- `(id)` or `(venue, trade_id)`, renamed aside in
    #     step (2) -- is dropped and replaced by `<table>_pkey_new` from step (1), which already
    #     covers the parent's key columns. Promoting an existing index is metadata-only, and the
    #     attach then matches it instead of building a key over the whole heap. Rows lose nothing:
    #     the wider key covers them.
    with engine.begin() as conn:
        conn.execute(text(f"alter table {legacy} drop constraint if exists {table}_pkey_legacy"))
        conn.execute(text(f"alter table {legacy} add constraint {table}_pkey_legacy "
                          f"primary key using index {table}_pkey_new"))
        conn.execute(text(f"alter table {table} attach partition {legacy} "
                          f"for values from (minvalue) to ('{bound.isoformat()}')"))
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
    # Each migrated table leaves only the partial cutover week. The recorder's tick would fill the
    # rest in, but not before its next run, and a row past this Sunday would have nowhere to go
    # until then. Idempotent, and it covers `raw_responses` too.
    with Session(engine) as session:
        ensure_partitions(session, now)
    return migrated
