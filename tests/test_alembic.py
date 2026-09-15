"""Task 15: the Alembic baseline, the guarded `ensure`, and the packaging that carries them.

The models and `create_schema` stay the schema authority. Alembic records additive history, so
the load-bearing assertion here is catalogue equality: a database built by `create_schema` and a
database built by `upgrade_head` must be the same database. No `pg_dump` -- the Mac has none --
so the comparison is the SQLAlchemy `inspect()` catalogue, with the partitions `ensure_partitions`
creates excluded through `pg_inherits` on both sides.
"""
import ast
import importlib.util
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from sqlalchemy import (
    Boolean,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.schema import create_schema, drop_schema, ensure_partitions

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = sorted((ROOT / "migrations" / "versions").glob("*.py"))

BULK_TABLES = ("raw_responses", "orderbook_events", "venue_trades", "venue_quotes",
               "odds_snapshots")

#: A migration that contains one of these is a gate, not a ruling: the loop never writes one.
#: `create_or_replace` is the Alembic op spelling; a view lives in `create_schema` instead.
#: `alter index` is handled separately, below: fix 32 sanctions exactly one shape of it.
#: `alter column` left this tuple in phase 4.6 (addendum 14.4, plan re-check R1): the widening
#: below is sanctioned, and both the revision that runs it and the prose explaining it would trip
#: a grep of the file text. It is checked instead over what a revision *executes*, by
#: `_ALLOWED_ALTER_COLUMN` in `test_no_migration_drops_or_alters_an_existing_object`, so a second
#: real one -- in any revision, on any table -- is still a failure.
FORBIDDEN = ("drop_index", "create_or_replace", "drop view",
             "drop table", "drop column", "rename")

#: The one sanctioned `alter index`: a storage-parameter flip, never a rebuild, never a drop.
_ALLOWED_ALTER_INDEX = re.compile(r"alter index (if exists )?\S+ set \(autosummarize = on\)")

#: The one sanctioned `alter column`: phase 4.6 section 14.4's varchar widening on a small table.
_ALLOWED_ALTER_COLUMN = re.compile(
    r"^alter table parlay_legs alter column market_type type varchar\(12\)$")


# --- scratch databases --------------------------------------------------------------------

def _test_url() -> str:
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    return url


def _with_database(url: str, name: str, *, raw: bool = False) -> str:
    """`url` with its path database swapped for `name`; scheme, netloc and query untouched.

    The sandbox's `SPORTS_TEST_SOCKET` URL form carries the socket directory as a `?host=`
    query (fix 61): rebuilding only the path through `urlsplit`/`urlunsplit`, rather than
    string-splitting on `/`, keeps that query on both the admin DSN and the scratch engine
    URL. `raw=True` also drops the `+psycopg` driver suffix, for a bare `psycopg.connect` DSN.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.replace("+psycopg", "") if raw else parts.scheme
    return urlunsplit((scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def test_the_scratch_url_keeps_the_socket_query():
    url = "postgresql+psycopg://u:p@/harness_test_x?host=/run/sports-test-db"
    assert _with_database(url, "postgres", raw=True) == "postgresql://u:p@/postgres?host=/run/sports-test-db"
    assert (_with_database(url, "harness_test_x_a")
            == "postgresql+psycopg://u:p@/harness_test_x_a?host=/run/sports-test-db")

    tcp = "postgresql+psycopg://u:p@localhost:5433/harness_test_x"
    assert _with_database(tcp, "postgres", raw=True) == "postgresql://u:p@localhost:5433/postgres"
    assert (_with_database(tcp, "harness_test_x_a")
            == "postgresql+psycopg://u:p@localhost:5433/harness_test_x_a")


def _scratch_engine(suffix: str):
    """An engine on `<branch test db>_<suffix>`, created on demand and emptied.

    A separate database rather than the session-scoped one: `upgrade_head` has to run against a
    database with nothing in it, and the suite's shared schema is built once per session.
    """
    base = _test_url()
    name = urlsplit(base).path.lstrip("/")
    target = f"{name}_{suffix}"[:63]
    admin = _with_database(base, "postgres", raw=True)
    with psycopg.connect(admin, autocommit=True) as conn:
        if not conn.execute("select 1 from pg_database where datname = %s", (target,)).fetchone():
            conn.execute(f'create database "{target}"')
    engine = create_engine(_with_database(base, target))
    _empty(engine)
    return engine


def _empty(engine) -> None:
    """Reset a scratch database: `drop_schema` plus Alembic's own bookkeeping table.

    Same sanctioned scope as the suite's `drop_schema`/truncate -- a scratch database on
    localhost:5433, never the NAS.
    """
    drop_schema(engine)
    with engine.begin() as conn:
        conn.execute(text("drop table if exists alembic_version"))


def _url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


@pytest.fixture
def scratch_db():
    engine = _scratch_engine("s")
    yield engine
    engine.dispose()


@pytest.fixture
def two_databases():
    a, b = _scratch_engine("a"), _scratch_engine("b")
    yield a, b
    a.dispose()
    b.dispose()


@pytest.fixture
def frozen_now():
    """A Monday noon, so `week_bounds` lands on the same two weeks for both databases."""
    return datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


# --- the catalogue ------------------------------------------------------------------------

def _norm(value):
    """A stable, order-insensitive rendering of an inspector value (dicts, lists, tuples)."""
    if isinstance(value, dict):
        return tuple(sorted((k, _norm(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_norm(v) for v in value)
    return value


def _catalogue(engine) -> dict:
    """Tables, columns with types and nullability, primary keys, unique constraints, indexes
    with their expressions, the views, and which tables are partitioned parents.

    Relations listed in `pg_inherits` -- the weekly partitions and their indexes -- are excluded
    on both sides: partitions are runtime objects created by `ensure_partitions`, never by a
    migration, and their names carry the week.
    """
    with engine.connect() as conn:
        inherited = {r[0] for r in conn.execute(text(
            "select c.relname from pg_inherits i join pg_class c on c.oid = i.inhrelid"))}
        partitioned = {r[0] for r in conn.execute(text(
            "select c.relname from pg_partitioned_table p "
            "join pg_class c on c.oid = p.partrelid"))}
    insp = inspect(engine)
    tables = {}
    for name in sorted(insp.get_table_names()):
        if name in inherited or name == "alembic_version":
            continue
        tables[name] = {
            # The server default matters as much as the type: `create_schema` and the baseline
            # disagreeing about a SERIAL or a `default 0` is exactly the drift this test exists
            # to catch, and both spell it as a column default.
            "columns": {c["name"]: (str(c["type"]), c["nullable"], c.get("default"))
                        for c in insp.get_columns(name)},
            "pk": tuple(insp.get_pk_constraint(name)["constrained_columns"]),
            "unique": _norm(sorted(insp.get_unique_constraints(name), key=lambda u: u["name"])),
            "checks": _norm(sorted(insp.get_check_constraints(name),
                                   key=lambda c: c["name"] or "")),
            "foreign_keys": _norm(sorted(insp.get_foreign_keys(name),
                                         key=lambda f: f["name"] or "")),
            "indexes": {i["name"]: _norm({k: v for k, v in i.items() if k != "name"})
                        for i in insp.get_indexes(name) if i["name"] not in inherited},
        }
    views = {v: " ".join((insp.get_view_definition(v) or "").split())
             for v in sorted(insp.get_view_names())}
    return {"tables": tables, "views": views, "partitioned": partitioned}


def _diff(a: dict, b: dict) -> str:
    out = []
    for key in ("partitioned", "views"):
        if a[key] != b[key]:
            out.append(f"{key}: {a[key]} != {b[key]}")
    for name in sorted(set(a["tables"]) | set(b["tables"])):
        ta, tb = a["tables"].get(name), b["tables"].get(name)
        if ta == tb:
            continue
        if ta is None or tb is None:
            out.append(f"{name}: present in only one database")
            continue
        for part in ("columns", "pk", "unique", "checks", "foreign_keys", "indexes"):
            if ta[part] != tb[part]:
                out.append(f"{name}.{part}: {ta[part]} != {tb[part]}")
    return "\n".join(out)


# --- catalogue equality (B-C4) --------------------------------------------------------------

def test_a_migrated_database_matches_a_create_schema_database(two_databases, frozen_now):
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        with Session(engine) as s:
            ensure_partitions(s, frozen_now)
    ca, cb = _catalogue(a), _catalogue(b)
    assert ca == cb, _diff(ca, cb)


def test_the_catalogue_covers_columns_types_nullability_keys_and_indexes(two_databases):
    a, _ = two_databases
    create_schema(a)
    cat = _catalogue(a)
    orders = cat["tables"]["orders"]
    assert orders["columns"]["prob"] == ("NUMERIC(6, 4)", False, None)
    assert orders["columns"]["venue_order_id"] == ("VARCHAR(64)", True, None)
    # The server default is part of the column, and the sequence a SERIAL primary key carries is
    # the drift fix 1 of review round 1 caught: `create_schema` and the baseline disagreed about
    # whether `exec_heartbeat.id` had one.
    assert orders["columns"]["id"] == ("BIGINT", False, "nextval('orders_id_seq'::regclass)")
    # `exec_heartbeat.id` is the one integer primary key that is deliberately not a SERIAL: the
    # model gives it a client-side default of 1 (the table holds one row), so neither builder
    # attaches a sequence, and `create_all` emits a plain integer.
    assert cat["tables"]["exec_heartbeat"]["columns"]["id"] == ("INTEGER", False, None)
    assert "uq_open_order" in orders["indexes"]
    # No table in this schema declares a check constraint or a foreign key. The catalogue carries
    # both anyway, so the day one is added, a baseline that missed it fails here.
    assert orders["checks"] == () and orders["foreign_keys"] == ()
    assert all(t["checks"] == () and t["foreign_keys"] == () for t in cat["tables"].values())
    assert cat["partitioned"] == {"raw_responses", "orderbook_events", "venue_trades"}


def test_the_baseline_creates_partitioned_parents_only(two_databases):
    from harness.db.migrate import upgrade_head

    _, b = two_databases
    upgrade_head(_url(b))
    with b.connect() as conn:
        children = conn.execute(text("select count(*) from pg_inherits")).scalar()
    assert children == 0          # partitions are runtime objects, never migration objects


# --- the three ensure branches (A-I11) ------------------------------------------------------

def test_ensure_stamps_a_populated_pre_alembic_database(scratch_db):
    from harness.db.migrate import HEAD_REVISION, current_revision, ensure

    create_schema(scratch_db)                    # `runs` exists, no alembic_version
    with scratch_db.begin() as conn:
        conn.execute(text(
            "insert into runs (started_at, status, n_requests, credits_used, "
            "budget_exhausted, notes) values (now(), 'ok', 0, 0, false, '{}'::jsonb)"))
    assert ensure(_url(scratch_db)) == "stamped"
    assert current_revision(_url(scratch_db)) == HEAD_REVISION
    # Neither migration was executed against it: no duplicate-object error above, and the
    # pre-existing row is untouched.
    with scratch_db.connect() as conn:
        assert conn.execute(text("select count(*) from runs")).scalar() == 1


def test_ensure_upgrades_an_empty_database(scratch_db):
    from harness.db.migrate import HEAD_REVISION, current_revision, ensure

    assert ensure(_url(scratch_db)) == "upgraded"
    assert current_revision(_url(scratch_db)) == HEAD_REVISION
    with scratch_db.connect() as conn:
        assert conn.execute(text(
            "select 1 from pg_tables where tablename = 'orders'")).first()


def test_ensure_is_a_no_op_at_head(scratch_db):
    from harness.db.migrate import ensure

    ensure(_url(scratch_db))
    assert ensure(_url(scratch_db)) == "current"


def test_ensure_is_idempotent_across_all_three_paths(scratch_db):
    from harness.db.migrate import HEAD_REVISION, current_revision, ensure

    create_schema(scratch_db)
    assert ensure(_url(scratch_db)) == "stamped"
    assert ensure(_url(scratch_db)) == "current"
    assert ensure(_url(scratch_db)) == "current"
    assert current_revision(_url(scratch_db)) == HEAD_REVISION


def test_current_revision_is_none_before_anything_ran(scratch_db):
    from harness.db.migrate import current_revision

    assert current_revision(_url(scratch_db)) is None


def test_stamp_head_records_the_revision_without_building_the_schema(scratch_db):
    from harness.db.migrate import HEAD_REVISION, current_revision, stamp_head

    stamp_head(_url(scratch_db))
    assert current_revision(_url(scratch_db)) == HEAD_REVISION
    with scratch_db.connect() as conn:
        assert conn.execute(text(
            "select 1 from pg_tables where tablename = 'orders'")).first() is None


# --- fix 71: the upgrade heals what a cancelled concurrent build left behind -----------------

def _heal_spy(monkeypatch):
    """Record every `heal_invalid_indexes` and `command.upgrade` call, in order, and keep both
    real: the upgrade must still build the database the other tests inspect."""
    from alembic import command

    from harness.db import migrate

    order, real_heal, real_upgrade = [], migrate.heal_invalid_indexes, command.upgrade

    def heal(url):
        order.append("heal")
        return real_heal(url)

    def upgrade(config, revision, **kwargs):
        order.append("upgrade")
        return real_upgrade(config, revision, **kwargs)

    monkeypatch.setattr(migrate, "heal_invalid_indexes", heal)
    monkeypatch.setattr(command, "upgrade", upgrade)
    return order


def test_ensure_heals_invalid_indexes_before_every_upgrade(scratch_db, monkeypatch):
    """Both upgrading branches heal first. A release retries `migrate ensure` after a cancelled
    `create index concurrently`, and `if not exists` would otherwise skip the invalid index."""
    from harness.db.migrate import ensure

    order = _heal_spy(monkeypatch)
    assert ensure(_url(scratch_db)) == "upgraded"
    assert order == ["heal", "upgrade"]
    assert ensure(_url(scratch_db)) == "current"
    assert order == ["heal", "upgrade", "heal", "upgrade"]


def test_the_stamp_branch_never_heals(scratch_db, monkeypatch):
    """A populated pre-Alembic database is recorded, not executed against, so nothing is
    rebuilt on it either."""
    from harness.db.migrate import ensure

    create_schema(scratch_db)
    order = _heal_spy(monkeypatch)
    assert ensure(_url(scratch_db)) == "stamped"
    assert order == []


# --- migrations never touch views or existing indexes (A-I11) -------------------------------

@pytest.mark.parametrize("path", VERSIONS, ids=lambda p: p.name)
def test_no_migration_drops_or_alters_an_existing_object(path):
    body = path.read_text().lower()
    for word in FORBIDDEN:
        assert word not in body, f"{path.name} contains {word!r}"
    # Scoped to actual DDL (an `op.execute(...)` call), not prose: several migrations now carry
    # comments explaining *why* an ALTER INDEX is or isn't used, and those legitimately contain
    # the words "alter index" without being one.
    for line in body.splitlines():
        if "alter index" in line and "op.execute(" in line:
            assert _ALLOWED_ALTER_INDEX.search(line), f"{path.name}: unexpected alter index: {line}"
    # `alter column` is scoped to what the revision runs, never to what it says: a docstring may
    # explain one (0010 does), and a second real one anywhere still fails here.
    for statement in _executable_strings(path):
        for line in statement.lower().splitlines():
            if "alter column" in line:
                assert _ALLOWED_ALTER_COLUMN.search(line.strip()), \
                    f"{path.name}: unexpected alter column: {line}"


#: A create-index statement, with its optional CONCURRENTLY and the table it lands on. Applied
#: to whitespace-collapsed SQL, so a statement the source line-wraps across adjacent string
#: literals still reads as one (Python has already joined those by the time we see them).
_CREATE_INDEX = re.compile(
    r"create index\s+(?P<concurrently>concurrently\s+)?(?:if not exists\s+)?\S+\s+on\s+(?P<table>\w+)",
    re.I)


def _executable_strings(path: Path) -> list[str]:
    """Every string literal in a revision that is not a docstring -- i.e. the SQL it can actually
    run. Parsed rather than grepped for two reasons: `op.execute` of a module-level constant puts
    the statement nowhere near the call (both 0005 and 0006 are written that way, which is the
    gap review Important 3 found), and the prose in these docstrings quotes SQL it does not run.
    """
    tree = ast.parse(path.read_text())
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                       ast.AsyncFunctionDef))
                  and ast.get_docstring(node, clean=False) is not None}
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def _tables_created(path: Path) -> frozenset[str]:
    """The tables a revision creates itself, from its `op.create_table("name", ...)` calls."""
    tree = ast.parse(path.read_text())
    return frozenset(
        node.args[0].value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_table" and node.args
        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str))


def _assert_bulk_indexes_are_concurrent(name: str, statements, created=frozenset()) -> None:
    """Every create-index statement landing on a bulk table must say CONCURRENTLY (F65, fix 25:
    no carve-out), unless the same revision creates that table.

    The carve-out for `created` is the baseline's, and it is not a softening of F65: a plain
    CREATE INDEX is only dangerous on a *populated* table with a writer attached, and a table
    this revision has just made in the same transaction is empty and unreachable. `0001_baseline`
    builds every table and then indexes it that way.

    Factored out of the parametrized test so a bad statement can be fed to it directly -- that
    test only ever sees revisions that pass.
    """
    for statement in statements:
        flat = " ".join(statement.split())
        for match in _CREATE_INDEX.finditer(flat):
            table = match.group("table").lower()
            if table not in BULK_TABLES or table in created:
                continue
            assert match.group("concurrently"), (
                f"{name}: index on bulk table {table} without CONCURRENTLY: {flat}")


@pytest.mark.parametrize("path", VERSIONS, ids=lambda p: p.name)
def test_no_migration_creates_a_bulk_index_outside_concurrent_index(path):
    """Review Important 3: this used to grep `op.create_index(...)` only, and both 0005 and 0006
    build a bulk index from a raw `op.execute` of a module-level string, which that regex could
    not see. Both forms are checked now."""
    src = path.read_text()
    for table in BULK_TABLES:
        for match in re.finditer(r"op\.create_index\((.*?)\)", src, re.S):
            assert table not in match.group(1), f"{path.name}: {table} outside concurrent_index"
    _assert_bulk_indexes_are_concurrent(path.name, _executable_strings(path),
                                        _tables_created(path))


def test_the_bulk_index_check_rejects_a_non_concurrent_statement():
    """The check above is only worth having if it fails on the thing it is looking for. Asserted
    at the string level rather than through a throwaway revision file, so no test writes into
    `migrations/versions/` (where the parametrized tests would then pick it up)."""
    good = "create index concurrently if not exists ix_quotes_run_market on venue_quotes (run_id)"
    _assert_bulk_indexes_are_concurrent("good", [good])                   # no raise

    bad = "create index if not exists ix_quotes_run_market on venue_quotes (run_id)"
    with pytest.raises(AssertionError, match="without CONCURRENTLY"):
        _assert_bulk_indexes_are_concurrent("bad", [bad])
    # Wrapped across source lines, the shape both 0005 and 0006 are written in.
    with pytest.raises(AssertionError, match="without CONCURRENTLY"):
        _assert_bulk_indexes_are_concurrent(
            "bad", ["create index if not exists ix_odds_fetched_book\n  on odds_snapshots "
                    "(fetched_at, book)"])
    # A non-bulk table is not this rule's business.
    _assert_bulk_indexes_are_concurrent("orders", [
        "create index if not exists ix_orders_game on orders (game_id)"])
    # Nor is a bulk table the same revision has just created, which is the baseline's whole shape.
    _assert_bulk_indexes_are_concurrent("baseline-like", [bad], frozenset({"venue_quotes"}))


def test_the_baseline_is_the_only_revision_exempted_by_creating_its_own_tables():
    """The `created` carve-out is meant for `0001_baseline` alone. If a later revision ever
    creates a bulk table, this says so out loud rather than letting the exemption spread
    silently."""
    creators = {p.name: _tables_created(p) & set(BULK_TABLES) for p in VERSIONS}
    assert {name for name, tables in creators.items() if tables} == {"0001_baseline.py"}
    assert creators["0001_baseline.py"] == set(BULK_TABLES)


def test_the_bulk_index_check_reads_a_revisions_constants_and_not_its_prose():
    """`_executable_strings` must see `0006`'s `_INDEX_DDL` -- the statement `op.execute` runs --
    and must not see the SQL its docstring quotes while explaining the outage."""
    strings = _executable_strings(ROOT / "migrations" / "versions" / "0006_quotes_run_index.py")
    assert any("create index concurrently if not exists ix_quotes_run_market" in s
               for s in strings)
    assert not any("ix_quotes_market_fetched" in s for s in strings)     # docstring prose only


def test_the_versions_directory_holds_thirteen_revisions():
    assert [p.name for p in VERSIONS] == [
        "0001_baseline.py", "0002_phase45.py", "0003_brin_autosummarize.py",
        "0004_phase5.py", "0005_rfq_lookup.py", "0006_quotes_run_index.py",
        "0007_raw_events_lookup.py", "0008_positions_open_fill.py",
        "0009_score_correction.py", "0010_phase46_fun_tickets.py",
        "0011_phase6b_execution.py", "0012_phase6d_sustained_eval.py",
        "0013_nw_executor_version.py"]


# --- carried fix 56 (second row): revision 0008 -------------------------------------------------

def test_positions_open_fill_follows_raw_events_lookup():
    """The pinned-head assertion moved off this test when fix 64's `0009_score_correction` and
    then phase 4.6's `0010_phase46_fun_tickets` landed on top of this one; it moved on to
    `test_phase6b_execution_follows_phase46_fun_tickets` when 6B's revision was renumbered
    `0011_phase6b_execution`, and it now lives on
    `test_nw_executor_version_follows_phase6d_and_is_the_pinned_head` (6D's revision, written as
    `0009_phase6d_sustained_eval` on its branch, was renumbered `0012_phase6d_sustained_eval`
    at this merge, D9). The chain assertions stay here, so a revision inserted between this one
    and `0009_score_correction` still fails -- the same trim fix 32/phase 5/fix 35/fix 42/fix 45
    gave the revisions before it."""
    module = _load_revision("0008_positions_open_fill.py")
    assert module.revision == "0008_positions_open_fill"
    assert module.down_revision == "0007_raw_events_lookup"


# --- fix 64 (journal 207): revision 0009 ---------------------------------------------------------

def test_score_correction_follows_positions_open_fill():
    """The pinned-head assertions moved off this test when phase 4.6's revision was renumbered
    `0010_phase46_fun_tickets` on top of this one at merge time (D9) -- the same pattern
    `0008_positions_open_fill` used when this revision landed on top of *it* -- and they moved on
    again to `test_phase6b_execution_follows_phase46_fun_tickets` when 6B merged, and to
    `test_nw_executor_version_follows_phase6d_and_is_the_pinned_head` when 6D did and roadmap
    row 72's `0013_nw_executor_version` landed on top of that. The chain
    assertions stay here, so a revision inserted between the two still fails."""
    module = _load_revision("0009_score_correction.py")
    assert module.revision == "0009_score_correction"
    assert module.down_revision == "0008_positions_open_fill"


def test_the_score_correction_revision_only_adds_the_column_and_undoes_nothing():
    """`upgrade()` runs exactly the one additive `ADD COLUMN IF NOT EXISTS` statement, byte-
    identical to `harness/db/schema.py`'s `_COLUMN_DDL` entry, and `downgrade()` is `pass`
    (roadmap invariant 5): dropping the column would be exactly the data-loss risk that rule
    guards against (unlike `0007_raw_events_lookup`'s index drop), and it would cross the user's
    own "No row changes" ruling (journal 207) if a downgrade ever ran against a database holding
    rows the writer had already marked."""
    from harness.db.schema import _COLUMN_DDL

    module = _load_revision("0009_score_correction.py")
    assert module._COLUMNS == (
        "alter table game_score_events add column if not exists correction boolean not null "
        "default false",)
    assert set(module._COLUMNS) <= set(_COLUMN_DDL)
    assert module.downgrade() is None


def test_the_game_score_events_correction_column_is_in_both_catalogues(two_databases, frozen_now):
    """Belt-and-suspenders on top of the whole-catalogue
    `test_a_migrated_database_matches_a_create_schema_database`: the column's type, nullability
    and (most load-bearing) server default agree between the `create_schema` database (built by
    `create_all` straight from the model, since `game_score_events` predates `0001_baseline`) and
    the migrated one (built from the baseline's frozen shape, then this revision's `ADD COLUMN`)
    -- the parity the server default on both the model and this revision exists to guarantee.
    """
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    cols = {}
    for label, engine in (("a", a), ("b", b)):
        with Session(engine) as s:
            ensure_partitions(s, frozen_now)
        by_name = {c["name"]: c for c in inspect(engine).get_columns("game_score_events")}
        assert "correction" in by_name
        cols[label] = by_name["correction"]
    assert cols["a"]["nullable"] is cols["b"]["nullable"] is False
    assert str(cols["a"]["type"]).upper() == str(cols["b"]["type"]).upper() == "BOOLEAN"
    assert cols["a"]["default"] == cols["b"]["default"]
    assert cols["a"]["default"] is not None      # the server default this fix depends on


def test_the_positions_view_ddl_agrees_between_schema_and_migration():
    """`create_schema` owns the views; this revision exists so a *migrated* database carries the
    same text, because `0001_baseline` holds the view's previous one and
    `test_a_migrated_database_matches_a_create_schema_database` compares view definitions.

    The two copies are compared as the Python string values each module holds, not as raw file
    text -- the revision wraps the long predicate line with a backslash continuation, which does
    not change the value. This is `test_the_quotes_run_index_ddl_agrees_between_schema_and_
    migration`'s shape, applied to a view instead of an index, and it is what makes a later edit
    to `_POSITIONS_VIEW` that forgets this copy fail here rather than in the catalogue diff.
    """
    from harness.db.schema import OPEN_FILL_SQL, _POSITIONS_VIEW

    module = _load_revision("0008_positions_open_fill.py")
    assert module._VIEW_DDL == _POSITIONS_VIEW
    assert OPEN_FILL_SQL in module._VIEW_DDL


def test_the_positions_open_fill_revision_only_issues_the_view_and_undoes_nothing():
    """`upgrade()` runs exactly one statement and it is the view; `downgrade()` is `pass`
    (roadmap invariant 5): re-issuing a view has nothing additive to undo, and the previous text
    is in `0001_baseline`, which a code rollback's `init-db` puts back through `create_schema`.

    Read through the parsed source rather than the lowercased file body, for
    `test_the_raw_events_lookup_downgrade_drops_the_parent_index`'s reason: the module docstring
    quotes SQL it does not run.
    """
    path = ROOT / "migrations" / "versions" / "0008_positions_open_fill.py"
    # `_executable_strings` also returns the revision identifiers, which are not statements; the
    # SQL is whatever starts with a DDL or DML verb.
    verbs = ("create", "drop", "alter", "insert", "update", "delete", "truncate")
    statements = [" ".join(s.split()).lower() for s in _executable_strings(path)]
    sql = [s for s in statements if s.startswith(verbs)]
    assert len(sql) == 1, sql
    assert sql[0].startswith("create or replace view positions as")
    tree = ast.parse(path.read_text())
    upgrade = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    assert len(upgrade.body) == 1
    downgrade = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
    assert all(isinstance(node, ast.Pass) for node in downgrade.body)


# --- 6B §1.3: revision 0011 (written as `0008_phase6b_execution` on the phase branch) --------

def test_phase6b_execution_follows_phase46_fun_tickets():
    """D9 applied at merge time, the third time on this chain.

    The plan's `0008_phase6b_execution` on top of `0007_raw_events_lookup` went stale when fix
    56's `0008_positions_open_fill` took that number on main on 2026-09-13, fix 64's
    `0009_score_correction` took the next one on 2026-09-14, and phase 4.6's revision was
    renumbered `0010_phase46_fun_tickets` on top of *that* -- so this phase's revision is
    `0011_phase6b_execution` on top of 4.6's, renumbered in the merge of `main` into the phase
    branch. These two pinned-head assertions carry over from
    `test_the_phase46_revision_is_the_pinned_head`, which kept its chain assertions under its
    new name, the same pattern 0008, 0009 and 0010 used before it -- and they moved on again to
    6D's own test when its revision, written as `0009_phase6d_sustained_eval` on its branch, was
    renumbered `0012_phase6d_sustained_eval` on top of this one at *its* merge time, and on again
    to `test_nw_executor_version_follows_phase6d_and_is_the_pinned_head` when roadmap row 72's
    `0013_nw_executor_version` landed. The chain assertions stay here, so a revision
    inserted between `0010_phase46_fun_tickets` and this one still fails.
    """
    module = _load_revision("0011_phase6b_execution.py")
    assert module.revision == "0011_phase6b_execution"
    assert module.down_revision == "0010_phase46_fun_tickets"


def test_the_phase6b_ledger_ddl_agrees_between_schema_and_migration():
    """The two copies of 6B's additive DDL must be the same strings, character for character.

    `harness/db/schema.py` stays the schema authority and the revision carries the identical
    statements (spec §2); the catalogue diff above would eventually catch a divergence, but only
    as an unexplained column difference. This names it.
    """
    from harness.db.schema import _COLUMN_DDL

    module = _load_revision("0011_phase6b_execution.py")
    # §1.3's ten ledger statements plus §1.5's three (`nw_dirty_seconds`,
    # `nw_next_attempt_at`, `nw_attempts`). The count is here so a task appending to one
    # copy and not the other is named by this test rather than by a catalogue diff.
    assert len(module._STATEMENTS) == 13
    for statement in module._STATEMENTS:
        assert statement in _COLUMN_DDL, statement


def test_the_phase6b_tables_are_in_both_catalogues(two_databases):
    """§1.5's two interval tables and §1.8's `order_rescores` are models *and* revision
    statements, added in the same task: the catalogue diff above fails if either half lands
    without the other, and this names which table went missing when it does.

    `order_rescores`' composite primary key is asserted by name because it is the whole of the
    table's access path -- it is what makes a resumed run a no-op (§1.8) -- and because a key
    that silently became `(order_id)` would make one order's second correction set overwrite
    its first.
    """
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        insp = inspect(engine)
        names = set(insp.get_table_names())
        assert {"market_dirty_intervals", "market_observation_intervals",
                "order_rescores"} <= names
        assert tuple(insp.get_pk_constraint("order_rescores")["constrained_columns"]) == (
            "order_id", "correction_ids", "cancel_policy")
        # No index of its own: the primary key is the only access path, so the model declares
        # no `__table_args__` and `harness/db/schema.py`'s `_INDEX_DDL` gains nothing.
        assert [i["name"] for i in insp.get_indexes("order_rescores")] == []


def test_the_phase6b_ledger_columns_are_nullable_with_no_default(scratch_db):
    """Spec §2, row 1: nullable with no default, so no pre-6B order is backfilled and the
    boundary invariant (`every ledger column null at or below the boundary order id`) holds by
    construction rather than by a later UPDATE."""
    from harness.db.migrate import upgrade_head

    upgrade_head(_url(scratch_db))
    names = ("print_unmatched", "pending_unmatched", "pending_surplus", "cancels_ahead",
             "recon_state")
    columns = {c["name"]: c for c in inspect(scratch_db).get_columns("orders")}
    for prefix in ("", "nw_"):
        for name in names:
            column = columns[f"{prefix}{name}"]
            assert column["nullable"] is True, name
            assert column["default"] is None, name


# --- 6D: revision 0012 (written as `0009_phase6d_sustained_eval` on the phase branch) ---------

def test_phase6d_follows_phase6b_execution():
    """D9 applied at merge time, the fourth time on this chain.

    The phase branch wrote `0009_phase6d_sustained_eval` on top of
    `0008_positions_open_fill`; fix 64 took 0009 on main, 4.6's revision was renumbered
    `0010_phase46_fun_tickets` and 6B's `0011_phase6b_execution`, so this phase's revision is
    `0012_phase6d_sustained_eval` on top of 6B's, renumbered in the merge of `main` into the
    phase branch. The two pinned-head assertions moved on to
    `test_nw_executor_version_follows_phase6d_and_is_the_pinned_head` (roadmap row 72's
    `0013_nw_executor_version`), the same trim 0008, 0009, 0010 and 0011 took before it; the
    chain assertions stay here, so a revision inserted between this one and
    `0011_phase6b_execution` still fails."""
    module = _load_revision("0012_phase6d_sustained_eval.py")
    assert module.revision == "0012_phase6d_sustained_eval"
    assert module.down_revision == "0011_phase6b_execution"


def test_the_episode_tables_and_their_indexes_are_in_both_catalogues(two_databases):
    """6D §1.7(b)/(c): the two episode tables are declared as models (so `create_schema` builds
    them) and mirrored in this revision with one plain index each, in the same task. The
    catalogue diff would catch a disagreement; this names the tables and all four indexes -- the
    `started_at` pair the readers ride and the `ended_at` pair the writer's openness test rides
    (review Important 1) -- so a half-landed pass says which half is missing.

    Plain indexes, never CONCURRENTLY: both tables are created empty by this revision and have
    no writer attached while it runs, which is the same reading `coverage_samples` took.
    """
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        names = set(inspect(engine).get_table_names())
        assert {"opportunity_episodes", "intent_episodes"} <= names
        indexes = {i["name"] for i in inspect(engine).get_indexes("opportunity_episodes")}
        assert {"ix_opportunity_started", "ix_opportunity_ended"} <= indexes
        assert {"ix_intent_started", "ix_intent_ended"} <= {
            i["name"] for i in inspect(engine).get_indexes("intent_episodes")}
        uniques = {u["name"] for u in inspect(engine).get_unique_constraints(
            "opportunity_episodes")}
        assert "uq_opportunity_episode" in uniques
        assert "uq_intent_episode" in {
            u["name"] for u in inspect(engine).get_unique_constraints("intent_episodes")}


# --- roadmap row 72 / spec amendment 0.17: revision 0013 -------------------------------------

def test_nw_executor_version_follows_phase6d_and_is_the_pinned_head():
    """The additive column amendment 0.17 names, on top of 6D's revision.

    The head moves with the revision or `migrate ensure` upgrades to a revision the checkout
    does not carry. The id is 24 characters, well inside the `String(32)` Alembic creates
    `alembic_version.version_num` as (0012's own docstring records the 33-character revision
    that aborted every upgrade), and the file name equals the id as all twelve before it do."""
    from harness.db.migrate import HEAD_REVISION

    module = _load_revision("0013_nw_executor_version.py")
    assert module.revision == "0013_nw_executor_version"
    assert module.down_revision == "0012_phase6d_sustained_eval"
    assert len(module.revision) <= 32
    assert HEAD_REVISION == "0013_nw_executor_version"
    assert VERSIONS[-1].name == "0013_nw_executor_version.py"


def test_the_nw_executor_version_revision_only_adds_the_column_and_undoes_nothing():
    """One additive `ADD COLUMN IF NOT EXISTS`, byte-identical to `harness/db/schema.py`'s
    `_COLUMN_DDL` entry (the `0009_score_correction` pattern), and `downgrade()` is `pass`
    (roadmap invariant 5). Nullable with no default, so the statement is metadata-only and no
    pre-existing row is backfilled: a row carrying non-null `nw_` twins and a null version is
    exactly the anomaly amendment 0.17 wants visible."""
    from harness.db.schema import _COLUMN_DDL

    module = _load_revision("0013_nw_executor_version.py")
    assert module._COLUMNS == (
        "alter table orders add column if not exists nw_executor_version numeric",)
    for statement in module._COLUMNS:
        assert statement in _COLUMN_DDL
    assert module.downgrade() is None


def test_nw_executor_version_is_in_both_catalogues(two_databases):
    """The model (so `create_schema` builds it) and the revision (so a migrated database has
    it) declare the same column: nullable numeric with no server default."""
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        columns = {c["name"]: c for c in inspect(engine).get_columns("orders")}
        assert "nw_executor_version" in columns, engine.url.database
        column = columns["nw_executor_version"]
        assert str(column["type"]) == "NUMERIC"
        assert column["nullable"] is True
        assert column["default"] is None


def _load_baseline():
    path = ROOT / "migrations" / "versions" / "0001_baseline.py"
    spec = importlib.util.spec_from_file_location("t15_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_baseline_downgrade_raises_instead_of_dropping():
    module = _load_baseline()
    assert module.revision == "0001_baseline"
    assert module.down_revision is None
    with pytest.raises(NotImplementedError):
        module.downgrade()


# --- env.py ---------------------------------------------------------------------------------

def _brin_index():
    return Index("ix_obe_ts_brin", text("ts"), postgresql_using="brin")


def _functional_index():
    return Index("uq_fair_value_row", text("coalesce(outcome_side, '')"), unique=True)


def _plain_index():
    md = MetaData()
    orders = Table("orders", md, Column("status", String(16)), Column("replay", Boolean),
                   Column("game_id", Integer))
    return Index("ix_orders_game", orders.c.game_id)


def test_include_object_excludes_partitioned_tables_and_partitions():
    from migrations.env import include_object

    assert include_object(None, "orderbook_events", "table", True, None) is False
    assert include_object(None, "orderbook_events_y2026w37", "table", True, None) is False
    assert include_object(None, "raw_responses", "table", True, None) is False
    assert include_object(None, "orders", "table", True, None) is True


def test_include_object_excludes_brin_and_functional_indexes():
    from migrations.env import include_object

    assert include_object(_brin_index(), "ix_obe_ts_brin", "index", True, None) is False
    assert include_object(_functional_index(), "uq_fair_value_row", "index", True, None) is False
    assert include_object(_plain_index(), "ix_orders_game", "index", False, None) is True


def test_include_object_excludes_a_reflected_only_index():
    """The raw-DDL indexes `create_schema` builds are not in the models. Autogenerate must
    leave them alone rather than propose taking them away."""
    from migrations.env import include_object

    assert include_object(_plain_index(), "ix_orders_game", "index", True, None) is False


def test_the_migration_connection_sets_both_timeouts():
    src = (ROOT / "migrations" / "env.py").read_text()
    assert "lock_timeout" in src and "'5s'" in src
    assert "statement_timeout" in src and "'300s'" in src


def test_concurrent_index_uses_an_autocommit_block():
    src = (ROOT / "migrations" / "env.py").read_text()
    assert "autocommit_block" in src and "CONCURRENTLY" in src.upper()


def test_env_declares_the_bulk_tables():
    from migrations.env import BULK_TABLES as declared

    assert tuple(declared) == BULK_TABLES


# --- packaging and pins (D7) ----------------------------------------------------------------

def test_the_dockerfile_copies_the_migrations_as_a_directory():
    df = (ROOT / "Dockerfile").read_text()
    assert "COPY alembic.ini ./" in df
    assert "COPY migrations ./migrations" in df
    # A single multi-source COPY would flatten migrations/ into /app.
    assert "COPY alembic.ini migrations ./" not in df


def test_both_deploy_recipes_push_the_migrations_and_the_full_one_runs_ensure():
    """Both tar lists, not just the full deploy's. An app-only deploy that shipped a stale
    migrations directory would build an image whose baseline no longer matches the models."""
    mk = (ROOT / "Makefile").read_text()
    tars = [l for l in mk.splitlines() if l.lstrip().startswith("@tar cf -")]
    assert len(tars) == 2, tars
    for line in tars:
        assert "alembic.ini migrations" in line, line
    assert "docker compose run --rm app-run migrate ensure" in mk
    assert "no migrate command in this build" not in mk, "the Task 14 probe is now the real command"


def test_pyproject_gains_exactly_one_dependency_per_phase():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    # >=1.16 is the floor the baseline actually needs: `op.create_table(if_not_exists=...)` and
    # `op.create_index(if_not_exists=...)` arrived there. constraints.txt pins 1.19.2 on top.
    assert "alembic>=1.16" in deps
    # Phase 5's one new dependency (addendum conformance item 2): the research layer's Claude
    # client. T2 adds it; this is where the count that would otherwise drift is pinned.
    assert any(d.startswith("anthropic") for d in deps)
    assert len(deps) == 17          # 15 through phase 4, plus alembic, plus anthropic


def test_constraints_pins_every_dependency_this_phase_added():
    # Comment lines are prose (phase 5 explains its pin in a comment block); the pins are the
    # non-comment lines.
    lines = [l for l in (ROOT / "constraints.txt").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    phase4 = [l for l in lines if l.lower().startswith(("alembic==", "mako=="))]
    # Phase 5 pins the anthropic SDK plus the six transitive packages it needs (each marked
    # "transitive pin for anthropic"), seven lines: T2 with the controller's ruling of
    # 2026-09-10 (the current release needs its own httpx2 transport).
    phase5 = [l for l in lines
              if l.lower().startswith("anthropic==") or "transitive pin for anthropic" in l]
    assert len(phase4) == 2 and len(phase5) == 7
    assert phase5[0].startswith("anthropic==")
    # Appended below the existing lines, not regenerated: the pre-phase tail is intact and the
    # phases are readable in order.
    assert lines[-10] == "websocket-client==1.9.2"
    assert lines[-9:-7] == phase4
    assert lines[-7:] == phase5


def test_migrate_is_programmatic_and_assumes_no_binary():
    src = (ROOT / "harness" / "db" / "migrate.py").read_text()
    assert "from alembic import command" in src
    assert "subprocess" not in src and "shutil.which" not in src


# --- CLI -------------------------------------------------------------------------------------

@pytest.fixture
def cli_runner(monkeypatch, scratch_db):
    monkeypatch.setenv("DATABASE_URL", _url(scratch_db))
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def test_migrate_current_prints_the_revision(cli_runner, scratch_db):
    from harness.db.migrate import HEAD_REVISION, ensure

    ensure(_url(scratch_db))
    result = cli_runner.invoke(app, ["migrate", "current"])
    assert result.exit_code == 0, result.output
    assert HEAD_REVISION in result.output


def test_migrate_current_prints_none_on_an_unstamped_database(cli_runner):
    result = cli_runner.invoke(app, ["migrate", "current"])
    assert result.exit_code == 0, result.output
    assert "none" in result.output.lower()


def test_migrate_ensure_prints_its_branch(cli_runner, scratch_db):
    result = cli_runner.invoke(app, ["migrate", "ensure"])
    assert result.exit_code == 0, result.output
    assert "upgraded" in result.output
    assert cli_runner.invoke(app, ["migrate", "ensure"]).output.strip().endswith("current")


def test_migrate_upgrade_and_stamp_are_commands(cli_runner, scratch_db):
    assert cli_runner.invoke(app, ["migrate", "upgrade"]).exit_code == 0
    with scratch_db.connect() as conn:
        assert conn.execute(text(
            "select 1 from pg_tables where tablename = 'orders'")).first()
    assert cli_runner.invoke(app, ["migrate", "stamp"]).exit_code == 0


# --- phase 4.5: revision 0002 ---------------------------------------------------------------

def _load_revision(filename: str):
    path = ROOT / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"rev_{filename[:-3]}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase45_follows_the_baseline():
    module = _load_revision("0002_phase45.py")
    assert module.revision == "0002_phase45"
    assert module.down_revision == "0001_baseline"


def test_the_phase45_downgrade_is_a_no_op_and_drops_nothing():
    """Conformance item 4: the audit grep for non-additive statements must stay empty, so the
    downgrade is `pass`. The loop never drops; a downgrade is the user's hand action."""
    module = _load_revision("0002_phase45.py")
    assert module.downgrade() is None
    body = (ROOT / "migrations" / "versions" / "0002_phase45.py").read_text().lower()
    for word in ("drop ", "truncate", "delete from"):
        assert word not in body, f"0002_phase45 contains {word!r}"


# --- fix 32: revision 0003 ------------------------------------------------------------------

def test_brin_autosummarize_follows_phase45():
    """The head assertion this test used to carry moved to
    `test_phase5_follows_brin_autosummarize_and_is_the_pinned_head`: 0003 is a link in the chain
    now, not its end, and exactly one test names the pinned head so a bump has one place to
    land. Phase 4.5's `test_phase45_follows_the_baseline` was trimmed the same way by fix 32."""
    module = _load_revision("0003_brin_autosummarize.py")
    assert module.revision == "0003_brin_autosummarize"
    assert module.down_revision == "0002_phase45"


def test_the_brin_autosummarize_downgrade_is_a_no_op_and_drops_nothing():
    module = _load_revision("0003_brin_autosummarize.py")
    assert module.downgrade() is None
    body = (ROOT / "migrations" / "versions" / "0003_brin_autosummarize.py").read_text().lower()
    for word in ("drop ", "truncate", "delete from"):
        assert word not in body, f"0003_brin_autosummarize contains {word!r}"


def test_ensure_raises_on_a_database_stamped_ahead_of_the_script_directory(scratch_db):
    """The rollback constraint of §8, as behaviour. `ensure`'s `current` branch calls
    `upgrade_head` unconditionally, so a database recording a revision this checkout does not
    carry aborts the deploy rather than no-opping. That is why a rollback goes through
    `make deploy-nas-app`, which never runs `ensure`, and why a later *full* deploy on a
    rolled-back sha needs a hand `alembic stamp 0001_baseline` first."""
    from harness.db.migrate import ensure, upgrade_head

    url = _url(scratch_db)
    upgrade_head(url)
    with scratch_db.begin() as conn:
        conn.execute(text("update alembic_version set version_num = '0003_from_the_future'"))

    with pytest.raises(Exception) as caught:
        ensure(url)
    assert "0003_from_the_future" in str(caught.value)


def test_the_phase45_tables_are_present_after_both_paths(two_databases):
    """The five parlay tables and dashboard_snapshots exist whichever way the database was
    built, which is what makes a mid-phase app-only deploy safe: create_schema is the schema
    authority and init-db is idempotent."""
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    expected = {"dashboard_snapshots", "parlay_cards", "parlay_legs", "parlay_placements",
                "parlay_ledger", "parlay_leg_probs"}
    for engine in (a, b):
        names = set(inspect(engine).get_table_names())
        assert expected <= names


def test_the_orders_key_index_is_in_both_catalogues(two_databases):
    """Step 5 adds it to `create_schema` and Step 6 to this revision, in this one task: the
    catalogue diff fails if either half lands without the other."""
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        indexes = {i["name"] for i in inspect(engine).get_indexes("orders")}
        assert "ix_orders_key_placed" in indexes


# --- phase 5: revision 0004 -----------------------------------------------------------------

def test_phase5_follows_brin_autosummarize():
    """The head assertion this test used to carry moved to
    `test_rfq_lookup_follows_phase5_and_is_the_pinned_head`: 0004 is a link in the chain now,
    not its end, the same trim fix 32 gave 0003 when phase 5 landed."""
    module = _load_revision("0004_phase5.py")
    assert module.revision == "0004_phase5"
    assert module.down_revision == "0003_brin_autosummarize"


def test_the_phase5_downgrade_is_a_no_op_and_drops_nothing():
    module = _load_revision("0004_phase5.py")
    assert module.downgrade() is None
    body = (ROOT / "migrations" / "versions" / "0004_phase5.py").read_text().lower()
    for word in ("drop ", "truncate", "delete from"):
        assert word not in body, f"0004_phase5 contains {word!r}"


def test_the_phase5_tables_and_view_are_present_after_both_paths(two_databases):
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    expected = {"futures_snapshots", "weather_points", "weather_snapshots", "veto_queue",
                "research_notes", "veto_decisions", "research_spend", "report_annotations",
                "rfqs", "rfq_quotes"}
    for engine in (a, b):
        insp = inspect(engine)
        assert expected <= set(insp.get_table_names())
        assert "veto_h9" in set(insp.get_view_names())


def test_the_veto_h9_view_definition_agrees_between_schema_and_migration():
    """Minor 7: `_VETO_H9_VIEW` (`harness/db/schema.py`) and the `create or replace view` block
    inside `migrations/versions/0004_phase5.py` are the same SQL, kept as two copies because
    `create_schema` and `upgrade_head` are two independent paths to the same database (Task 15's
    own module docstring above). `test_the_phase5_tables_and_view_are_present_after_both_paths`
    only asserts the view exists on both paths, not that a hand-edit to one copy did not drift
    from the other -- this is the comparison that catches that."""
    from harness.db.schema import _VETO_H9_VIEW

    migration = (ROOT / "migrations" / "versions" / "0004_phase5.py").read_text()
    assert _VETO_H9_VIEW.strip() in migration


# --- fix 35: revision 0005 -------------------------------------------------------------------

def test_rfq_lookup_follows_phase5():
    """The head assertion this test used to carry moved to
    `test_quotes_run_index_follows_rfq_lookup_and_is_the_pinned_head`: 0005 is a link in the
    chain now, not its end, the same trim fix 32 gave 0003 and phase 5 gave 0004."""
    module = _load_revision("0005_rfq_lookup.py")
    assert module.revision == "0005_rfq_lookup"
    assert module.down_revision == "0004_phase5"


def test_the_rfq_lookup_downgrade_is_a_no_op_and_drops_nothing():
    module = _load_revision("0005_rfq_lookup.py")
    assert module.downgrade() is None
    body = (ROOT / "migrations" / "versions" / "0005_rfq_lookup.py").read_text().lower()
    for word in ("drop ", "truncate", "delete from"):
        assert word not in body, f"0005_rfq_lookup contains {word!r}"


def test_the_rfq_lookup_index_is_in_both_catalogues(two_databases):
    """Fix 35 adds `ix_fair_leg_lookup` to both `create_schema`'s `_CONCURRENT_INDEX_DDL` and
    this revision, in the same commit: the catalogue diff fails if either half lands without the
    other, the same shape `test_the_orders_key_index_is_in_both_catalogues` checks for phase
    4.5's `ix_orders_key_placed`."""
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        indexes = {i["name"] for i in inspect(engine).get_indexes("fair_values")}
        assert "ix_fair_leg_lookup" in indexes


def test_the_rfq_lookup_ddl_agrees_between_schema_and_migration():
    """The two copies (`harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL` entry and this
    revision's `_INDEX_DDL`) must be the identical statement, not just produce indexes with the
    same name: `_LEG` and `_CLOSING_LEG` are keyed on this index's exact column order and
    partial predicate, and a drift between the two copies would only show up as a seq scan on
    whichever path built the database, not as a test failure anywhere else. Compared as the
    Python string values each module actually executes, not as raw file text -- the two copies
    are free to line-wrap differently and still agree."""
    from harness.db.schema import _CONCURRENT_INDEX_DDL

    schema_stmt = next(s for s in _CONCURRENT_INDEX_DDL if "ix_fair_leg_lookup" in s)
    module = _load_revision("0005_rfq_lookup.py")
    assert module._INDEX_DDL == schema_stmt


# --- fix 42: revision 0006 -------------------------------------------------------------------

def test_quotes_run_index_follows_rfq_lookup():
    """The head assertion this test used to carry moved to
    `test_raw_events_lookup_follows_quotes_run_index_and_is_the_pinned_head`: 0006 is a link in
    the chain now, not its end, the same trim fix 32/phase 5/fix 35 gave 0003/0004/0005."""
    module = _load_revision("0006_quotes_run_index.py")
    assert module.revision == "0006_quotes_run_index"
    assert module.down_revision == "0005_rfq_lookup"


def test_the_quotes_run_index_downgrade_is_a_no_op_and_drops_nothing():
    module = _load_revision("0006_quotes_run_index.py")
    assert module.downgrade() is None
    body = (ROOT / "migrations" / "versions" / "0006_quotes_run_index.py").read_text().lower()
    for word in ("drop ", "truncate", "delete from"):
        assert word not in body, f"0006_quotes_run_index contains {word!r}"


def test_the_quotes_run_index_is_in_both_catalogues(two_databases):
    """Fix 42 adds `ix_quotes_run_market` to `VenueQuote.__table_args__`, to `create_schema`'s
    `_CONCURRENT_INDEX_DDL` and to this revision in one commit: the catalogue diff fails if any
    half lands without the others, the same shape
    `test_the_rfq_lookup_index_is_in_both_catalogues` checks for fix 35's `ix_fair_leg_lookup`."""
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        indexes = {i["name"] for i in inspect(engine).get_indexes("venue_quotes")}
        assert "ix_quotes_run_market" in indexes
    # Round 1 (review Important 1) took this index, and fix 25's `ix_odds_fetched_book`, out of
    # `_model_indexes` so the plain `create index` can never beat the CONCURRENTLY one to it.
    # On a database created from nothing that leaves `create_all` and the CONCURRENTLY statement
    # as the builders, and both indexes are still here afterwards.
    assert "ix_odds_fetched_book" in {i["name"] for i in inspect(a).get_indexes("odds_snapshots")}


def test_the_quotes_run_index_ddl_agrees_between_schema_and_migration():
    """The two copies (`harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL` entry and this
    revision's `_INDEX_DDL`) must be the identical statement. Compared as the Python string
    values each module actually executes, not as raw file text -- the two are free to line-wrap
    differently and still agree. Exactly one entry in the tuple names the index, which is also
    what makes a second `create index` on a re-run of `create_schema` impossible: both copies
    carry `if not exists`, and there is no plain-`create` third copy anywhere."""
    from harness.db.schema import _CONCURRENT_INDEX_DDL

    matches = [s for s in _CONCURRENT_INDEX_DDL if "ix_quotes_run_market" in s]
    assert len(matches) == 1, matches
    module = _load_revision("0006_quotes_run_index.py")
    assert module._INDEX_DDL == matches[0]
    assert "concurrently if not exists" in matches[0]


def test_the_quotes_run_index_is_never_built_without_concurrently():
    """F65 (fix 25): neither `_INDEX_DDL` nor the revision carries a plain `create index` for
    this index -- both written copies are CONCURRENTLY.

    The model declaration used to be the hole this docstring warned about: `create_all` only
    builds indexes for tables it is creating, but `create_schema` then called `_model_index_ddl`
    for every model index outside `TAPE_TABLES`, and `venue_quotes` is a bulk table that is not a
    tape table -- so a *populated* database lacking this index would have taken a plain,
    table-locking create there first, leaving the CONCURRENTLY entry a no-op. Round 1 (review
    Important 1) closed it: `_model_indexes` subtracts every name `_CONCURRENT_INDEX_DDL` builds,
    which covers fix 25's `ix_odds_fetched_book` on `odds_snapshots` too.
    `test_model_index_loop_never_rebuilds_a_concurrent_index` in `tests/test_schema.py` is the
    assertion on that helper; this one covers the written statements."""
    from harness.db.schema import _CONCURRENT_INDEX_NAMES, _CONCURRENT_INDEX_DDL, _INDEX_DDL

    assert not any("ix_quotes_run_market" in s for s in _INDEX_DDL)
    stmt = next(s for s in _CONCURRENT_INDEX_DDL if "ix_quotes_run_market" in s)
    assert stmt.startswith("create index concurrently if not exists")
    assert "ix_quotes_run_market" in _CONCURRENT_INDEX_NAMES
    src = (ROOT / "migrations" / "versions" / "0006_quotes_run_index.py").read_text()
    assert "autocommit_block" in src


# --- fix 45: revision 0007 --------------------------------------------------------------------

def test_raw_events_lookup_follows_quotes_run_index():
    """It stopped being the pinned head at carried fix 56 (second row), which added
    `0008_positions_open_fill` on top of it; its place in the chain is what this still pins.

    6B's revision was written as `0008_phase6b_execution` on top of *this* one on the phase
    branch and was renumbered `0011_phase6b_execution` on top of `0010_phase46_fun_tickets` at
    merge time (D9), so nothing in this phase follows 0007 any more."""
    module = _load_revision("0007_raw_events_lookup.py")
    assert module.revision == "0007_raw_events_lookup"
    assert module.down_revision == "0006_quotes_run_index"


def test_the_raw_events_lookup_downgrade_drops_the_parent_index():
    """Unlike every revision since `0002_phase45` (whose `downgrade()` is `pass`, roadmap
    invariant 5), this one actually drops: a plain additive index carries none of the data-loss
    risk that rule guards against, and dropping the parent takes every attached partition child
    with it (intrinsic to a partitioned index in Postgres; no CASCADE needed).

    Round 1 (review Minor 3): read through `_executable_strings`, not the lowercased file body --
    the module docstring quotes this same SQL in prose, so a body-text assertion would still pass
    against a `downgrade()` that dropped nothing.
    `test_the_bulk_index_check_reads_a_revisions_constants_and_not_its_prose` is the precedent."""
    module = _load_revision("0007_raw_events_lookup.py")
    path = ROOT / "migrations" / "versions" / "0007_raw_events_lookup.py"
    strings = [s.lower() for s in _executable_strings(path)]
    assert any("drop index if exists ix_raw_source_endpoint_id" in s for s in strings)
    body = path.read_text().lower()
    # Still none of `test_no_migration_drops_or_alters_an_existing_object`'s FORBIDDEN shapes
    # (that parametrized test already covers this file; this just states the intent locally).
    assert "drop_index" not in body and "drop table" not in body


def test_the_raw_events_lookup_migration_calls_the_shared_partitioned_recipe():
    """Unlike `0005_rfq_lookup`/`0006_quotes_run_index`, there is no single DDL string to compare
    between two copies -- the partitioned recipe loops over `pg_inherits`, so this revision
    imports and calls `harness/db/schema.py`'s `_ensure_partitioned_concurrent_indexes` directly.
    One implementation, not two copies free to drift the way a hand-typed second copy could."""
    from harness.db.schema import _ensure_partitioned_concurrent_indexes

    module = _load_revision("0007_raw_events_lookup.py")
    assert module._ensure_partitioned_concurrent_indexes is _ensure_partitioned_concurrent_indexes


def test_the_raw_source_endpoint_id_index_matches_the_partitioned_concurrent_tuple():
    """The model, `_PARTITIONED_CONCURRENT_INDEXES` and this revision must all name and shape the
    same index or the catalogue diff fails -- the same shape
    `test_the_quotes_run_index_ddl_agrees_between_schema_and_migration` checks for fix 42, applied
    to a tuple entry instead of a DDL string since there is no string here to compare."""
    from harness.db.models import RawResponse
    from harness.db.schema import _PARTITIONED_CONCURRENT_INDEXES

    name, table, cols = next(e for e in _PARTITIONED_CONCURRENT_INDEXES
                             if e[0] == "ix_raw_source_endpoint_id")
    assert table == "raw_responses"
    assert cols == "(source, endpoint, id)"
    index = next(i for i in RawResponse.__table__.indexes if i.name == name)
    assert [c.name for c in index.columns] == ["source", "endpoint", "id"]


def test_the_raw_events_lookup_index_is_in_both_catalogues(two_databases, frozen_now):
    """Fix 45 adds `ix_raw_source_endpoint_id` to `RawResponse.__table_args__`, to
    `harness/db/schema.py`'s `_PARTITIONED_CONCURRENT_INDEXES` and to this revision in one commit:
    the catalogue diff fails if any half lands without the others, the same shape
    `test_the_quotes_run_index_is_in_both_catalogues` checks for fix 42's `ix_quotes_run_market`.

    Round 1 (review Minor 2): `inspect().get_indexes` reports an invalid index the same as a
    valid one, so `ensure_partitions` gives each database at least one partition here and the
    test also checks `pg_index.indisvalid` -- not just that the name is present -- for the
    parent on both paths."""
    from harness.db.migrate import upgrade_head

    a, b = two_databases
    create_schema(a)
    upgrade_head(_url(b))
    for engine in (a, b):
        with Session(engine) as s:
            ensure_partitions(s, frozen_now)
        indexes = {i["name"] for i in inspect(engine).get_indexes("raw_responses")}
        assert "ix_raw_source_endpoint_id" in indexes
        with engine.connect() as conn:
            valid = conn.execute(text(
                "select indisvalid from pg_index i join pg_class c on c.oid = i.indexrelid "
                "where c.relname = 'ix_raw_source_endpoint_id'")).scalar()
        assert valid is True


# --- phase 4.6: the additive revision (addendum 9, 14.4; D9) --------------------------------

def _phase46_module():
    """The revision module, imported by path: its name starts with a digit, so no dotted import
    reaches it. `_load_revision` (defined further down this file) does the same thing; this is
    the plan's own name for it and it keeps this section readable on its own."""
    return _load_revision("0010_phase46_fun_tickets.py")


def test_the_phase46_revision_is_additive_only():
    """Expected: no DROP, RENAME, TRUNCATE, DELETE or backfill anywhere in the revision's
    statements, every ADD COLUMN and CREATE TABLE `if not exists`, exactly one
    `alter column ... type` -- the documented `parlay_legs.market_type` widening (addendum
    14.4) -- and `downgrade()` a pass.

    Computed independently of the code: invariant 5 is a grep over what the revision *runs*, so
    this walks the module's own statement tuples rather than the file text. The prose in the
    docstring names `alter column ... type` too (Step 5 requires it), and a count over the file
    would therefore see two occurrences and fail on its own documentation (plan review IM-1).

    The DML words are matched as a statement's leading verb rather than anywhere in it: this
    revision creates `odds_prop_snapshots`, whose `book_last_update` column carries the literal
    "update " inside a `create table` statement.
    """
    module = _phase46_module()
    statements = [" ".join(s.split()).lower() for s in
                  module._COLUMNS + module._TABLES + module._INDEXES + module._CONCURRENT]
    for forbidden in ("drop table", "drop column", "rename", "truncate", "drop index"):
        assert not any(forbidden in s for s in statements), forbidden
    for verb in ("insert into", "update ", "delete from", "truncate "):
        assert not any(s.startswith(verb) for s in statements), verb
    assert all("if not exists" in s for s in statements if s.startswith("alter table")
               and "add column" in s)
    assert all("create table if not exists" in s for s in statements
               if s.startswith("create table"))
    widenings = [s for s in statements if "alter column" in s]
    assert widenings == ["alter table parlay_legs alter column market_type type varchar(12)"]
    assert module.downgrade() is None
    assert module.revision == "0010_phase46_fun_tickets"
    assert module.down_revision == "0009_score_correction"


def test_the_phase46_revision_follows_score_correction():
    """D9 applied at merge time. The plan's `0008_phase46_fun_tickets` on top of
    `0007_raw_events_lookup` went stale when fix 56's `0008_positions_open_fill` took that number
    on main on 2026-09-13, and the phase branch's own `0009` went stale when fix 64's
    `0009_score_correction` took *that* number on main on 2026-09-14 -- so this phase's revision
    is `0010_phase46_fun_tickets` on top of fix 64's, renumbered in the merge of `main` into the
    phase branch.

    The two pinned-head assertions this test used to carry moved on to
    `test_phase6b_execution_follows_phase46_fun_tickets` when 6B's own revision was renumbered
    `0011_phase6b_execution` on top of this one at *its* merge time --
    the same pattern 0008 and 0009 used before it. The chain assertions stay here, so a revision
    inserted between `0009_score_correction` and this one still fails. 6D's revision was
    renumbered the same way at its own merge time, as `0012_phase6d_sustained_eval` on top of
    `0011_phase6b_execution`."""
    module = _phase46_module()

    assert module.revision == "0010_phase46_fun_tickets"
    assert module.down_revision == "0009_score_correction"


def test_the_one_widening_is_the_only_alter_column_any_revision_carries():
    """The `FORBIDDEN` grep still holds for everything else.

    Computed independently of the code: `_ALLOWED_ALTER_COLUMN` matches one exact statement, so
    a second widening -- or the same one on another table -- is still a failure. The audit's own
    grep (`alter column .* type`) finds the one line and Conformance 4 explains it; this asserts
    the suite agrees with the audit rather than being blind to it.
    """
    # `_ALLOWED_ALTER_COLUMN` is this module's own name (defined beside `_ALLOWED_ALTER_INDEX`).
    assert _ALLOWED_ALTER_COLUMN.pattern.count("alter column") == 1
    assert _ALLOWED_ALTER_COLUMN.search(
        "alter table parlay_legs alter column market_type type varchar(12)")
    assert not _ALLOWED_ALTER_COLUMN.search(
        "alter table parlay_cards alter column status type varchar(12)")
    assert not _ALLOWED_ALTER_COLUMN.search(
        "alter table parlay_legs alter column market_type type varchar(24)")
    # And the one revision that carries it is the only one that does.
    carriers = [p.name for p in VERSIONS
                if any("alter column" in s.lower() for s in _executable_strings(p))]
    assert carriers == ["0010_phase46_fun_tickets.py"]


def test_every_index_on_a_table_taking_live_writes_is_concurrent():
    """CR-4 / D13: `intents`, `order_events`, `fills` and `ledger` all take the executor's
    writes while `init-db` runs on every deploy, and fix 25's F65 rule is every index on a table
    under a live writer CONCURRENTLY, no carve-out.

    Four, not the five the addendum's section 7.2 lists: `ix_gap_outcomes_order on gap_outcomes
    (order_id)` cannot be written, because `gap_outcomes` is keyed `(gap_snapshot_id,
    benchmark_type)` and carries no `order_id` column (`harness/db/models.py`, and the same on
    `phase6b-repair-execution`). Its read reaches a gap outcome through `market_gap_snapshots`,
    whose id is that table's leading primary-key column, so the primary key already serves it.
    Asserted here so the gap is visible rather than silent: the day `gap_outcomes` gains an
    order key, this test is where the fifth index is added.
    """
    module = _phase46_module()
    assert all("create index concurrently" in s or "create unique index concurrently" in s
               for s in (" ".join(x.split()).lower() for x in module._CONCURRENT))
    names = " ".join(module._CONCURRENT)
    for index in ("ix_intents_market_created", "ix_order_events_order_ts", "ix_fills_order_ts",
                  "ix_ledger_order"):
        assert index in names, index
    # And nothing else is in there: the plain tuple's indexes all ride tables this revision
    # creates, or `parlay_placements`, which one hand writes twice a week.
    assert len(module._CONCURRENT) == 4
    from harness.db.models import GapOutcome

    assert "order_id" not in GapOutcome.__table__.columns


def test_the_phase46_revision_leaves_odds_snapshots_untouched():
    """D23 (addendum 9 as amended): a prop outcome is keyed by the player and
    `uq_odds_snapshot_row` carries no `where` clause, so the only fix inside `odds_snapshots`
    would be rebuilding a unique index on a bulk table -- not additive. The props go to their own
    table instead, and neither a column nor an index is added to `odds_snapshots` here."""
    module = _phase46_module()
    statements = [s.lower() for s in
                  module._COLUMNS + module._TABLES + module._INDEXES + module._CONCURRENT]
    assert not any("odds_snapshots" in s for s in statements)
    names = " ".join(module._INDEXES)
    for index in ("uq_odds_prop_row", "ix_odds_prop_lookup", "uq_players_sport_espn",
                  "ix_player_stat_game_player_ts", "ix_parlay_corrections_card_ts",
                  "uq_parlay_placement_confirmation"):
        assert index in names, index


def test_the_phase46_statements_match_create_schema_exactly():
    """The revision and `create_schema` are two copies of one list; the catalogue diff is the
    judge of the result, and this is the judge of the text, so a later edit to one copy fails
    here rather than in a 40-line catalogue diff (the shape of
    `test_the_quotes_run_index_ddl_agrees_between_schema_and_migration`)."""
    from harness.db import schema as schema_module

    module = _phase46_module()
    assert set(module._COLUMNS) <= set(schema_module._COLUMN_DDL)
    assert set(module._INDEXES) <= set(schema_module._INDEX_DDL)
    assert set(module._CONCURRENT) <= set(schema_module._CONCURRENT_INDEX_DDL)
