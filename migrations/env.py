"""Alembic's environment, plus the two helpers every migration in this repository uses.

This module is loaded two ways and has to behave differently in each:

* Alembic execs it by path for every `upgrade` and `stamp`. That load configures the migration
  context and runs the migrations.
* `migrations/versions/0001_baseline.py` and `tests/test_alembic.py` import it as
  `migrations.env` for `include_object`, `concurrent_index` and `BULK_TABLES`. An import must
  not start a migration, so the run at the bottom is guarded on `__name__`.

Authority: the SQLAlchemy models and `harness.db.schema.create_schema` are the schema authority.
Alembic records additive history for the migration tool's future use. `include_object` is only
ever autogenerate's filter -- the baseline is hand-written -- and it is deliberately narrow: it
keeps autogenerate away from the partitioned tape, from the raw-DDL indexes `create_schema`
builds that no model knows about, and from the weekly partitions, which are runtime objects.
"""
from __future__ import annotations

import re

from alembic import context, op
from sqlalchemy import Column, engine_from_config, pool, text

from harness.db.models import Base

#: The append-only tables that are too large for a blocking CREATE INDEX. A new index on one of
#: these goes through `concurrent_index` and nothing else; `tests/test_alembic.py` greps for it.
BULK_TABLES = ("raw_responses", "orderbook_events", "venue_trades", "venue_quotes",
               "odds_snapshots")

#: The partitioned parents. Their weekly children are created by `ensure_partitions`, never by a
#: migration, so autogenerate must not see either the parents or the children.
PARTITIONED_TABLES = ("raw_responses", "orderbook_events", "venue_trades")

#: `<table>_y####w##`, the name `harness.db.schema._partition_name` builds.
_PARTITION_SUFFIX = re.compile(r"_y\d{4}w\d{2}$")

target_metadata = Base.metadata


def _is_partition_relation(name: str) -> bool:
    return name in PARTITIONED_TABLES or bool(_PARTITION_SUFFIX.search(name))


def _index_using(obj) -> str:
    return str(getattr(obj, "dialect_kwargs", {}).get("postgresql_using") or "").lower()


def _is_functional_or_partial(obj) -> bool:
    """Whether an index is something autogenerate cannot compare safely.

    A functional index reflects back as text expressions rather than columns, and a partial index
    carries a `postgresql_where`. Autogenerate has no faithful comparison for either, so it
    proposes taking them away and building them again on every run. Every one of ours is
    hand-written raw DDL in `create_schema`.
    """
    expressions = getattr(obj, "expressions", None) or ()
    if any(not isinstance(expr, Column) for expr in expressions):
        return True
    return getattr(obj, "dialect_kwargs", {}).get("postgresql_where") is not None


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Autogenerate's filter. Never used to build the baseline, which is hand-written."""
    name = name or ""
    if type_ == "table":
        return not _is_partition_relation(name)
    if type_ == "index":
        table = getattr(getattr(obj, "table", None), "name", "") or ""
        if _is_partition_relation(table) or _is_partition_relation(name):
            return False
        if _index_using(obj) == "brin" or _is_functional_or_partial(obj):
            return False
        # An index in the database that no model declares is `create_schema`'s, not Alembic's.
        return not (reflected and compare_to is None)
    return not (reflected and compare_to is None)


def concurrent_index(name: str, table: str, cols: list[str], using: str | None = None) -> None:
    """The convenience path for building an index on a bulk table.

    Not the only path, despite what this line used to say: `0005_rfq_lookup` and
    `0006_quotes_run_index` each keep their statement as a module-level string so
    `tests/test_alembic.py` can compare it against `harness/db/schema.py`'s copy, and issue it
    with the same `autocommit_block` directly. What is actually required of every one of them --
    this helper included -- is CONCURRENTLY, and
    `test_no_migration_creates_a_bulk_index_outside_concurrent_index` enforces that over the
    statements a revision executes, whichever way it spells them (review Important 3).

    `autocommit_block` takes the statement out of the migration's transaction, which is what
    CREATE INDEX CONCURRENTLY requires. Note that Postgres 16 cannot build an index
    CONCURRENTLY on a partitioned parent at all, which is the other half of why the tape's
    indexes are `create_schema`'s and the baseline's rather than a later migration's.
    """
    method = f" using {using}" if using else ""
    with op.get_context().autocommit_block():
        op.execute(f"create index concurrently if not exists {name} "
                   f"on {table}{method} ({', '.join(cols)})")


def run_migrations_online() -> None:
    """Run the migrations against a live connection.

    Both timeouts are set on the connection before anything else: a migration that cannot take
    its lock in 5 s must fail fast rather than queue the WebSocket sink's inserts behind it, and
    300 s bounds the statement itself, well above the 30 s the application uses.
    """
    config = context.config
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text("set lock_timeout = '5s'"))
        connection.execute(text("set statement_timeout = '300s'"))
        # Both are session settings, so they survive this commit -- and the commit matters:
        # `autocommit_block` inside a migration asserts that any open transaction is one Alembic
        # itself opened, and the SETs above would otherwise leave SQLAlchemy's implicit one.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            # The pairing Alembic documents for a migration that uses `autocommit_block`.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


#: Alembic execs this file under a module name of its own making; an import for the helpers
#: arrives as `migrations.env`. Only the first should run anything.
if __name__ != "migrations.env":
    if context.is_offline_mode():
        raise RuntimeError("harness migrations run online only: no --sql / offline mode")
    run_migrations_online()
