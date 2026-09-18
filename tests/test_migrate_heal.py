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
from sqlalchemy.exc import OperationalError

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


def _upgrade_on(conn, module) -> None:
    """The revision's `upgrade()` on a connection the caller owns, outside Alembic's runner.

    `alembic.op` is a proxy to the current `Operations`, so a test can drive one revision and
    see what it does. No transaction may be open at entry: `autocommit_block` commits the one
    it finds and asserts Alembic opened it. Taking the connection as an argument is what lets
    the timeout tests below set the session values the release has and read them back after.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    assert not conn.in_transaction()
    context = MigrationContext.configure(connection=conn,
                                         opts={"transaction_per_migration": True})
    with Operations.context(context):
        module.upgrade()


def _run_upgrade(engine, module) -> None:
    """`_upgrade_on` on a connection of its own, for the tests that do not care which."""
    with engine.connect() as conn:
        _upgrade_on(conn, module)


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


# --- fix 85 amendment (the user's ruling of 2026-09-18, second file, §1) ---------------------
#
# The bound that actually cancels a `CREATE INDEX CONCURRENTLY` is the migration connection's
# `lock_timeout = '5s'` (`migrations/env.py:run_migrations_online`), not `statement_timeout`:
# the build waits out every transaction that can see the table, and Postgres bounds a lock wait
# with `lock_timeout`. That is fix 71's error verbatim, and fix 85's review reproduced it on this
# very statement. The ruling raises `lock_timeout` to 120 s for the build statement only,
# read-set-restore in the same `finally` as `statement_timeout`, inside the same autocommit
# block; `migrations/env.py` keeps its 5 s for every other migration and for the healer.

#: What `migrations/env.py:run_migrations_online` puts on the migration connection. The tests
#: below start the session there, so the restored values are the release's own.
RELEASE_LOCK_TIMEOUT, RELEASE_STATEMENT_TIMEOUT = "5s", "300s"


def _setting_ms(conn, name: str) -> int:
    """`pg_settings.setting` for a timeout GUC: milliseconds, the unit the revision restores."""
    return int(conn.execute(text("select setting from pg_settings where name = :name"),
                            {"name": name}).scalar())


@contextmanager
def _release_session(engine):
    """A connection carrying the two timeouts the release's migration connection carries.

    `set` without `local` needs the commit: SQLAlchemy 2.0 opens an implicit transaction for the
    statement, and a rollback would take the setting away with it. `migrations/env.py` commits
    for the same reason (and because `autocommit_block` refuses a transaction it did not open).
    The session fixture's `checkin` listener runs `reset all`, so nothing leaks to the next test.
    """
    with engine.connect() as conn:
        conn.execute(text(f"set lock_timeout = '{RELEASE_LOCK_TIMEOUT}'"))
        conn.execute(text(f"set statement_timeout = '{RELEASE_STATEMENT_TIMEOUT}'"))
        conn.commit()
        yield conn


def test_the_orders_intent_migration_raises_both_timeouts_for_the_build_statement_only(_schema):
    """The ruling's amendment: `lock_timeout` is raised to 120 s beside the 1800 s
    `statement_timeout`, both after the previous values are read and both restored in the same
    `finally` after the build, so the session the rest of the release runs on is unchanged."""
    module = _orders_intent_revision()
    assert module.BUILD_LOCK_TIMEOUT == "120s"
    assert module.BUILD_STATEMENT_TIMEOUT == "1800s"
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with _release_session(_schema) as conn:
        previous_lock_ms = _setting_ms(conn, "lock_timeout")
        previous_statement_ms = _setting_ms(conn, "statement_timeout")
        before = (conn.execute(text("show lock_timeout")).scalar(),
                  conn.execute(text("show statement_timeout")).scalar())
        conn.commit()                       # nothing of ours may be open at `upgrade()`
        event.listen(Engine, "before_cursor_execute", record)
        try:
            _upgrade_on(conn, module)       # must not raise: the index is valid here
        finally:
            event.remove(Engine, "before_cursor_execute", record)
        after = (conn.execute(text("show lock_timeout")).scalar(),
                 conn.execute(text("show statement_timeout")).scalar())
        conn.rollback()

    assert (previous_lock_ms, previous_statement_ms) == (5_000, 300_000)
    build = statements.index(module._INDEX_DDL)
    for raised, restored in ((f"set lock_timeout = '{module.BUILD_LOCK_TIMEOUT}'",
                              f"set lock_timeout = '{previous_lock_ms}'"),
                             (f"set statement_timeout = '{module.BUILD_STATEMENT_TIMEOUT}'",
                              f"set statement_timeout = '{previous_statement_ms}'")):
        assert raised in statements and restored in statements, statements
        assert statements.index(raised) < build < statements.index(restored), statements
    # (b) the session the release keeps running on is exactly the one it had.
    assert after == before == ("5s", "5min")


def test_the_orders_intent_migration_restores_both_timeouts_after_a_cancelled_wait(_schema):
    """Fix 71's failure, on fix 85's index: a second session holds `orders` in ACCESS EXCLUSIVE,
    the build's lock wait is cancelled, and the driver's error raises out of the build statement
    before the `indisvalid` read. The `finally` must still put both settings back -- which is
    also why the two restores are nested rather than sequential: the first must not be able to
    skip the second. The build's own 120 s wait is patched down to 1 s so this test is seconds,
    not two minutes; the wait, the cancellation and the restore are the real thing."""
    module = _orders_intent_revision()
    holder = _schema.connect()
    try:
        holder.execute(text("lock table orders in access exclusive mode"))
        assert holder.in_transaction()
        with _release_session(_schema) as conn:
            previous = (conn.execute(text("show lock_timeout")).scalar(),
                        conn.execute(text("show statement_timeout")).scalar())
            conn.commit()
            assert module.BUILD_LOCK_TIMEOUT == "120s"   # the ruling's value, asserted
            module.BUILD_LOCK_TIMEOUT = "1s"            # then patched: this module object
                                                        # is this test's own load
            with pytest.raises(OperationalError) as error:
                _upgrade_on(conn, module)
            assert "lock timeout" in str(error.value)
            assert (conn.execute(text("show lock_timeout")).scalar(),
                    conn.execute(text("show statement_timeout")).scalar()) == previous
            conn.rollback()
    finally:
        holder.rollback()
        holder.close()
    # The cancelled wait never reached the catalogue: the index is untouched and still valid.
    assert _valid(_schema, ORDERS_INTENT_INDEX) is True
