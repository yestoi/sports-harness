"""Programmatic Alembic: the four operations the deploy needs, with no `alembic` binary assumed.

`docker compose run --rm app-run migrate ensure` is a `harness` entrypoint, so nothing here may
depend on a console script being on PATH or on the process's working directory. The `Config` is
built in code and `script_location` is resolved to the packaged `migrations/` directory.

The models and `create_schema` are the schema authority; Alembic records additive history. The
guarded branch in `ensure` is the load-bearing part: the NAS database predates Alembic and is
full, so its first deploy must record the baseline rather than execute it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from harness.db.engine import service_connect_args

log = logging.getLogger(__name__)

#: The revision this checkout carries. `ensure` stamps or upgrades to it **by name**, so a typo
#: in the script directory fails loudly instead of silently stamping nothing -- and so a
#: revision that is not named here is never applied, which is why adding a file is not enough.
#:
#: Phase 4.5 bumped it from "0001_baseline"; fix 32 bumped it to "0003_brin_autosummarize";
#: phase 5 bumped it to "0004_phase5"; fix 35 bumped it to "0005_rfq_lookup"; fix 42 bumped it
#: to "0006_quotes_run_index"; fix 45 bumped it to "0007_raw_events_lookup"; carried fix 56
#: (second row) bumped it to "0008_positions_open_fill"; fix 64 (journal 207) bumped it to
#: "0009_score_correction"; phase 4.6's additive revision bumps it to
#: "0010_phase46_fun_tickets"; phase 6B's additive revision bumps it to
#: "0011_phase6b_execution"; phase 6D's additive revision bumps it to
#: "0012_phase6d_sustained_eval". Those last three numbers are D9 applied at merge time: the
#: plan's "0008" was written before fix 56 took that number on main, the phase branch then
#: carried "0009", and fix 64 took *that* number on main on 2026-09-14, so 4.6's revision was
#: renumbered to "0010" on top of it in the merge commit; 6B's own revision, written as "0008"
#: on its branch, was renumbered to "0011" on top of "0010" when that branch merged main; and
#: 6D's own revision, written as "0009" on its branch, was renumbered to "0012" on top of
#: "0011" when this branch merged main. Two consequences
#: the runbook states and a test pins: only the full `make deploy-nas` runs `migrate ensure`, so
#: a mid-phase app-only deploy leaves the stamp at the prior revision while `create_schema` still
#: creates the new tables and the view; and a database stamped ahead of a checkout that lacks the
#: matching revision file aborts at `ensure`, because its `current` branch calls `upgrade_head`
#: unconditionally.
HEAD_REVISION = "0012_phase6d_sustained_eval"

#: Where the migrations live inside the image. The Dockerfile's `COPY migrations ./migrations`
#: puts them here; the checkout path below is what the test suite and a developer use.
_IMAGE_MIGRATIONS = Path("/app/migrations")


def migrations_dir() -> Path:
    """The packaged `migrations/` directory, from a checkout or from inside the image."""
    checkout = Path(__file__).resolve().parents[2] / "migrations"
    if checkout.is_dir():
        return checkout
    return _IMAGE_MIGRATIONS


def alembic_config(url: str) -> Config:
    """A `Config` built in code: no alembic.ini is read and no binary is assumed.

    `prepend_sys_path` is set to the directory holding `migrations/` so a migration can import
    `migrations.env` for `concurrent_index`, whichever way the process was started.
    """
    directory = migrations_dir()
    config = Config()
    config.set_main_option("script_location", str(directory))
    config.set_main_option("path_separator", "os")
    config.set_main_option("prepend_sys_path", str(directory.parent))
    # A literal '%' in a password would otherwise be read as ConfigParser interpolation.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


#: Every index `pg_index` records as not valid, in the schema the migrations own, with the table
#: it belongs to. A cancelled `create index concurrently` leaves exactly this: the index is in
#: the catalogue, so a retry's `if not exists` skips it, but no plan may use it and no unique
#: constraint it carries is enforced.
_INVALID_INDEX_SQL = (
    "select c.relname, t.relname from pg_index x "
    "join pg_class c on c.oid = x.indexrelid "
    "join pg_class t on t.oid = x.indrelid "
    "join pg_namespace n on n.oid = c.relnamespace "
    "where n.nspname = 'public' and not x.indisvalid order by c.relname"
)


def _bulk_tables() -> tuple[str, ...]:
    """`migrations.env.BULK_TABLES`, the one list of append-only tables.

    Imported here rather than copied, and with the same `sys.path` treatment `alembic_config`
    gives `prepend_sys_path`, so this works from a checkout and from `/app` inside the image.
    """
    directory = migrations_dir()
    if str(directory.parent) not in sys.path:
        sys.path.insert(0, str(directory.parent))
    from migrations.env import BULK_TABLES

    return tuple(BULK_TABLES)


def _is_partition_relation():
    """`migrations.env.is_partition_relation`, the one `<table>_y####w##` predicate.

    Imported exactly the way `_bulk_tables` imports `BULK_TABLES`, and for the same reason:
    the regex that decides what a partition child is lives in one place (fix 71 narrowing,
    journal 224 item 9c).
    """
    directory = migrations_dir()
    if str(directory.parent) not in sys.path:
        sys.path.insert(0, str(directory.parent))
    from migrations.env import is_partition_relation

    return is_partition_relation


def heal_invalid_indexes(url: str) -> list[str]:
    """Rebuild the indexes a cancelled concurrent build left invalid. Returns their names.

    Fix 71 (deploy 2026-09-14 23:34 CT): revision 0010's
    `create index concurrently if not exists ix_intents_market_created on intents (...)` waited on
    the snapshot of a backend orphaned by a stopped app container and was cancelled by the
    migration connection's `lock_timeout = '5s'`
    (`04:36:49 ERROR: canceling statement due to lock timeout`). The half-built index stayed in
    the catalogue with `indisvalid = false`, where the next release's `if not exists` would skip
    it and the deploy would ship an index nothing can use.

    `REINDEX INDEX` and nothing else: no DROP of any kind (roadmap invariant 5) -- the index is
    already in the catalogue and comes back valid in place. Non-concurrent, because the recipe
    has stopped every writer by the time `migrate ensure` runs and these are small tables;
    `statement_timeout` is the 300 s `migrations/env.py` gives a migration.

    An invalid index on one of `migrations.env.BULK_TABLES` -- or on a weekly partition child
    of one, which `migrations.env.is_partition_relation` recognises by name -- raises instead,
    before anything is rebuilt: a blocking rebuild of a multi-gigabyte append-only table is the
    controller's decision, made with the tape's size in front of it, never a step a release
    takes on its own. `pg_index` reports the child an index actually lives on
    (`venue_trades_y2026w38`), and `BULK_TABLES` names only the parents, so the name test is
    what makes the deny list cover the tape it was written for (fix 71 narrowing, journal 224
    item 9c).

    `lock_timeout = '5s'` beside the statement timeout (item 9d): a REINDEX takes an ACCESS
    EXCLUSIVE lock, so one that cannot have it within five seconds is queued behind a session
    this recipe failed to close -- and while it queues, every reader of that table queues
    behind it. Failing there raises, and the release takes its existing rollback path.
    """
    bulk_tables = _bulk_tables()
    is_partition_relation = _is_partition_relation()
    # Fix 76 (roadmap row 76), review minor M3: `migrate ensure` opens its own short-lived
    # engines, and they were the last backends in the stack with no `application_name` -- the
    # release drain reports exactly those as `unnamed_backends`. The timeouts here are still
    # set per connection below, where the healer's recipe wants them.
    engine = create_engine(url, connect_args=service_connect_args())
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("set statement_timeout = '300s'"))
            conn.execute(text("set lock_timeout = '5s'"))
            invalid = [(row[0], row[1]) for row in conn.execute(text(_INVALID_INDEX_SQL))]
            blocked = [f"{name} on {table}" for name, table in invalid
                       if table in bulk_tables or is_partition_relation(table)]
            if blocked:
                raise RuntimeError(
                    "invalid index on a bulk table or a partition of one: rebuild it "
                    f"deliberately, never inside a release ({', '.join(blocked)})")
            healed = []
            for name, table in invalid:
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(text(f"reindex index public.{quoted}"))
                log.info("migrate: rebuilt invalid index %s on %s", name, table)
                healed.append(name)
            return healed
    finally:
        engine.dispose()


def upgrade_head(url: str) -> list[str]:
    """Bring a database up to the pinned head revision, healing invalid indexes first.

    The heal runs before `command.upgrade`, because it is the upgrade's own
    `create index concurrently if not exists` that would otherwise skip an index a previous
    attempt left invalid. Returns the names it rebuilt, for the release receipt and the log.
    """
    healed = heal_invalid_indexes(url)
    command.upgrade(alembic_config(url), HEAD_REVISION)
    return healed


def stamp_head(url: str) -> None:
    """Record the pinned head as applied without executing it."""
    command.stamp(alembic_config(url), HEAD_REVISION)


def current_revision(url: str) -> str | None:
    """The revision the database records, or None when it has never been stamped."""
    engine = create_engine(url, connect_args=service_connect_args())
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _state(url: str) -> tuple[bool, bool]:
    """(alembic_version exists, runs exists) -- one short connection, read from pg_tables."""
    engine = create_engine(url, connect_args=service_connect_args())
    try:
        with engine.connect() as conn:
            present = {row[0] for row in conn.execute(text(
                "select tablename from pg_tables "
                "where schemaname = current_schema() "
                "and tablename in ('alembic_version', 'runs')"))}
    finally:
        engine.dispose()
    return "alembic_version" in present, "runs" in present


def ensure(url: str) -> str:
    """The guarded one-time stamp. Returns which of three branches it took.

    'stamped'  - no alembic_version, `runs` exists: a populated pre-Alembic database, which is
                 what the NAS is on this phase's first deploy. Record head; never execute the
                 baseline against it, because every object it creates is already there.
    'upgraded' - no alembic_version, no `runs`: an empty database. Build it from the baseline.
    'current'  - alembic_version exists. Upgrade from the stored revision; a no-op at head.
    """
    has_version, has_runs = _state(url)
    if not has_version and has_runs:
        stamp_head(url)
        log.info("alembic: stamped %s on a populated pre-Alembic database", HEAD_REVISION)
        return "stamped"
    if not has_version:
        _report(upgrade_head(url))
        log.info("alembic: upgraded an empty database to %s", HEAD_REVISION)
        return "upgraded"
    _report(upgrade_head(url))
    log.info("alembic: at %s", current_revision(url))
    return "current"


def _report(healed: list[str]) -> None:
    """Put the rebuilt index names in the deploy's own output, not only in the log: a release
    that healed an index a cancelled build left behind is a thing the receipt reader must see."""
    for name in healed:
        print(f"migrate: rebuilt invalid index {name}", flush=True)
