"""Fix 71: `migrate ensure` heals the index a cancelled concurrent build left invalid.

The 2026-09-14 23:34 CT release stopped the apps, then revision `0010_phase46_fun_tickets`
ran `create index concurrently if not exists ix_intents_market_created on intents (...)`; an
orphaned backend's snapshot held it until the migration connection's `lock_timeout = '5s'`
cancelled it (Postgres log `04:36:49 ERROR: canceling statement due to lock timeout`). The
half-built index stayed in the catalogue with `indisvalid = false`, where a retry's
`if not exists` would skip it and the release would ship an index the planner never uses.

These tests run against the branch test database, which carries the
`UPDATE (indisvalid) ON pg_catalog.pg_index` grant that lets a test reproduce that state --
the scratch databases `tests/test_alembic.py` creates do not (a grant is per database).
No DROP anywhere: `REINDEX INDEX` rebuilds an index that is already in the catalogue.
"""
from contextlib import contextmanager

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from harness.db.migrate import heal_invalid_indexes
from migrations.env import BULK_TABLES

#: A small table's index: exactly the one the release left invalid.
SMALL_INDEX, SMALL_TABLE = "ix_intents_market_created", "intents"
#: A bulk table's index: rebuilding one of these is the controller's decision, never automatic.
BULK_INDEX, BULK_TABLE = "ix_odds_fetched_book", "odds_snapshots"


def _url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def _valid(engine, name) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("select indisvalid from pg_index "
                                 "where indexrelid = cast(:name as regclass)"),
                            {"name": name}).scalar()


def _mark(engine, name, value) -> None:
    with engine.begin() as conn:
        conn.execute(text("update pg_catalog.pg_index set indisvalid = :value "
                          "where indexrelid = cast(:name as regclass)"),
                     {"name": name, "value": value})


@contextmanager
def invalid(engine, name):
    """Leave `name` looking like a cancelled concurrent build, and restore it whatever happens."""
    _mark(engine, name, False)
    assert _valid(engine, name) is False, "the fixture grant must allow the invalid state"
    try:
        yield
    finally:
        if _valid(engine, name) is False:
            _mark(engine, name, True)


def test_the_bulk_table_names_are_the_migration_environment_s(_schema):
    assert BULK_TABLE in BULK_TABLES and SMALL_TABLE not in BULK_TABLES


def test_an_invalid_index_on_a_small_table_is_rebuilt_and_reported(_schema):
    with invalid(_schema, SMALL_INDEX):
        healed = heal_invalid_indexes(_url(_schema))
        assert healed == [SMALL_INDEX]
        assert _valid(_schema, SMALL_INDEX) is True


def test_a_healthy_database_heals_nothing(_schema):
    assert heal_invalid_indexes(_url(_schema)) == []


def test_an_invalid_index_on_a_bulk_table_raises_and_is_left_untouched(_schema):
    with invalid(_schema, BULK_INDEX):
        with pytest.raises(RuntimeError) as error:
            heal_invalid_indexes(_url(_schema))
        assert BULK_INDEX in str(error.value) and BULK_TABLE in str(error.value)
        assert _valid(_schema, BULK_INDEX) is False


def test_a_bulk_table_index_stops_the_healer_before_it_rebuilds_a_small_one(_schema):
    """One invalid index the release may not touch aborts the whole heal: the controller
    decides about a bulk rebuild before any of it runs."""
    with invalid(_schema, BULK_INDEX), invalid(_schema, SMALL_INDEX):
        with pytest.raises(RuntimeError):
            heal_invalid_indexes(_url(_schema))
        assert _valid(_schema, SMALL_INDEX) is False


# --- fix 71 narrowing (journal 224 item 9c/9d) ------------------------------------------

#: The shape `harness.db.schema._partition_name` builds, in a week no fixture partition uses:
#: a partition child of an append-only tape is exactly as expensive to rebuild as its parent,
#: and its name is the only thing that says so -- `BULK_TABLES` lists the parents alone.
PARTITION_TABLE = "venue_trades_y2099w01"
PARTITION_INDEX = "ix_venue_trades_y2099w01_scratch"


@contextmanager
def scratch_partition_table(engine):
    """A small table named like a weekly partition child, with one index.

    Created and dropped by this test and nothing else is touched: the drop at the end names
    only the table this block created (the healer itself still drops nothing, ever).
    """
    with engine.begin() as conn:
        conn.execute(text(f"create table {PARTITION_TABLE} (id bigint)"))
        conn.execute(text(f"create index {PARTITION_INDEX} on {PARTITION_TABLE} (id)"))
    try:
        yield
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"drop table if exists {PARTITION_TABLE}"))


def test_an_invalid_index_on_a_partition_child_raises_before_any_rebuild(_schema):
    """Journal 224 item 9c: `BULK_TABLES` names the parents, and `pg_index` reports the child
    the index actually lives on (`venue_trades_y2026w38`), so the deny list alone would let a
    release start a blocking rebuild on a tape partition. Any table matching
    `migrations.env`'s partition suffix is bulk."""
    with scratch_partition_table(_schema):
        with invalid(_schema, PARTITION_INDEX):
            with pytest.raises(RuntimeError) as error:
                heal_invalid_indexes(_url(_schema))
            assert PARTITION_INDEX in str(error.value) and PARTITION_TABLE in str(error.value)
            assert _valid(_schema, PARTITION_INDEX) is False


def test_a_partition_child_index_stops_the_healer_before_it_rebuilds_a_small_one(_schema):
    with scratch_partition_table(_schema):
        with invalid(_schema, PARTITION_INDEX), invalid(_schema, SMALL_INDEX):
            with pytest.raises(RuntimeError):
                heal_invalid_indexes(_url(_schema))
            assert _valid(_schema, SMALL_INDEX) is False


def test_the_partition_predicate_is_the_migration_environment_s():
    """Reused, never copied: one regex decides what a partition child is."""
    from migrations.env import PARTITIONED_TABLES, is_partition_relation

    assert is_partition_relation(PARTITION_TABLE)
    assert is_partition_relation("venue_trades") and "venue_trades" in PARTITIONED_TABLES
    assert not is_partition_relation(SMALL_TABLE)


def test_the_healer_sets_a_lock_timeout_beside_its_statement_timeout(_schema):
    """Journal 224 item 9d: a REINDEX that cannot take its lock in five seconds raises and the
    release rolls back, rather than queueing behind an orphan and blocking every reader of the
    table behind it for the length of the statement timeout."""
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        with invalid(_schema, SMALL_INDEX):
            assert heal_invalid_indexes(_url(_schema)) == [SMALL_INDEX]
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    assert "set statement_timeout = '300s'" in statements
    assert "set lock_timeout = '5s'" in statements
    rebuild = next(i for i, sql in enumerate(statements) if sql.startswith("reindex index"))
    assert statements.index("set lock_timeout = '5s'") < rebuild


# --- fix 76 (roadmap row 76), review minor M3: these engines name their backends too ---------


def test_the_migrate_engines_send_the_service_name(_schema, monkeypatch):
    """`migrate ensure` opens three short-lived engines of its own -- the index healer's, the
    revision read and the `_state` probe -- and they used to be the last client backends in the
    stack with no `application_name`. `scripts/release-omarchy.py` classifies exactly that as
    `unnamed_backends`, and row 76's closing read counts them, so a `harness migrate` running
    beside a release would have tripped it. Same lookup as both engine factories, so there is
    one place a service name comes from."""
    import harness.db.migrate as migrate_mod

    monkeypatch.setenv("HARNESS_SERVICE", "app-run")
    url = _url(_schema)
    captured = []
    real_create_engine = migrate_mod.create_engine

    def recording(engine_url, **kwargs):
        captured.append(kwargs.get("connect_args"))
        return real_create_engine(engine_url, **kwargs)

    monkeypatch.setattr(migrate_mod, "create_engine", recording)
    migrate_mod.current_revision(url)
    migrate_mod._state(url)
    assert heal_invalid_indexes(url) == []      # a healthy database heals nothing

    assert captured == [{"application_name": "app-run"}] * 3

    # And unset -- the suite, a developer shell -- keeps libpq's default, as everywhere else.
    monkeypatch.delenv("HARNESS_SERVICE", raising=False)
    captured.clear()
    migrate_mod.current_revision(url)
    assert captured == [{}]


# --- fix 85 (docket item 22, the user's ruling of 2026-09-18): the migration's own guard -----
#
# "An invalid index is a stop for me, not a retry." `create index concurrently if not exists`
# skips an index that is already in the catalogue, valid or not (that is fix 71's whole story),
# so revision `0014_orders_intent_index` reads `pg_index.indisvalid` immediately after its build
# and raises rather than let a release ship an index the planner will never use. The state is
# reproducible only on this database: the `UPDATE (indisvalid) ON pg_catalog.pg_index` grant is
# per database and `tests/test_alembic.py`'s scratch databases do not carry it.

ORDERS_INTENT_INDEX = "ix_orders_intent"


def _orders_intent_revision():
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
            / "0014_orders_intent_index.py")
    spec = importlib.util.spec_from_file_location("fix85_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(engine, module) -> None:
    """The revision's `upgrade()` on a real connection, outside Alembic's runner.

    `alembic.op` is a proxy to the current `Operations`, so a test can drive one revision and
    see what it does. No transaction may be open at entry: `autocommit_block` commits the one
    it finds and asserts Alembic opened it.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.connect() as conn:
        assert not conn.in_transaction()
        context = MigrationContext.configure(connection=conn,
                                             opts={"transaction_per_migration": True})
        with Operations.context(context):
            module.upgrade()


def test_the_orders_intent_migration_passes_on_a_valid_index(_schema):
    """The index `create_schema` has already built on this database is valid, so the revision's
    check is silent and the build statement is the no-op its `if not exists` promises."""
    assert _valid(_schema, ORDERS_INTENT_INDEX) is True
    _run_upgrade(_schema, _orders_intent_revision())          # must not raise
    assert _valid(_schema, ORDERS_INTENT_INDEX) is True


def test_the_orders_intent_migration_raises_when_the_build_left_the_index_invalid(_schema):
    """The fix 71 state, on fix 85's index: the catalogue carries the name, `if not exists`
    skips it, and without this guard the release would report success on an index no query can
    use. The message names the index so the controller's stop is unambiguous."""
    module = _orders_intent_revision()
    with invalid(_schema, ORDERS_INTENT_INDEX):
        with pytest.raises(RuntimeError) as error:
            _run_upgrade(_schema, module)
        assert ORDERS_INTENT_INDEX in str(error.value)
        assert "indisvalid" in str(error.value)
        # Failing closed, not repairing: the revision touches nothing after the raise.
        assert _valid(_schema, ORDERS_INTENT_INDEX) is False
