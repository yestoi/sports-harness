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
from sqlalchemy import text

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
