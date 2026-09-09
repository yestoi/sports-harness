"""Task 15: the Alembic baseline, the guarded `ensure`, and the packaging that carries them.

The models and `create_schema` stay the schema authority. Alembic records additive history, so
the load-bearing assertion here is catalogue equality: a database built by `create_schema` and a
database built by `upgrade_head` must be the same database. No `pg_dump` -- the Mac has none --
so the comparison is the SQLAlchemy `inspect()` catalogue, with the partitions `ensure_partitions`
creates excluded through `pg_inherits` on both sides.
"""
import importlib.util
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

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
FORBIDDEN = ("drop_index", "create_or_replace", "drop view", "alter index",
             "drop table", "drop column", "alter column", "rename")


# --- scratch databases --------------------------------------------------------------------

def _test_url() -> str:
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    return url


def _scratch_engine(suffix: str):
    """An engine on `<branch test db>_<suffix>`, created on demand and emptied.

    A separate database rather than the session-scoped one: `upgrade_head` has to run against a
    database with nothing in it, and the suite's shared schema is built once per session.
    """
    base = _test_url()
    head, _, name = base.rpartition("/")
    target = f"{name}_{suffix}"[:63]
    admin = head.replace("postgresql+psycopg://", "postgresql://") + "/postgres"
    with psycopg.connect(admin, autocommit=True) as conn:
        if not conn.execute("select 1 from pg_database where datname = %s", (target,)).fetchone():
            conn.execute(f'create database "{target}"')
    engine = create_engine(f"{head}/{target}")
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


# --- migrations never touch views or existing indexes (A-I11) -------------------------------

@pytest.mark.parametrize("path", VERSIONS, ids=lambda p: p.name)
def test_no_migration_drops_or_alters_an_existing_object(path):
    body = path.read_text().lower()
    for word in FORBIDDEN:
        assert word not in body, f"{path.name} contains {word!r}"


@pytest.mark.parametrize("path", VERSIONS, ids=lambda p: p.name)
def test_no_migration_creates_a_bulk_index_outside_concurrent_index(path):
    src = path.read_text()
    for table in BULK_TABLES:
        for match in re.finditer(r"op\.create_index\((.*?)\)", src, re.S):
            assert table not in match.group(1), f"{path.name}: {table} outside concurrent_index"


def test_the_versions_directory_holds_the_baseline_and_phase45():
    assert [p.name for p in VERSIONS] == ["0001_baseline.py", "0002_phase45.py"]


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


def test_pyproject_gains_exactly_one_dependency():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    # >=1.16 is the floor the baseline actually needs: `op.create_table(if_not_exists=...)` and
    # `op.create_index(if_not_exists=...)` arrived there, and every statement in it is IF NOT
    # EXISTS. constraints.txt pins 1.19.2 on top of this.
    assert "alembic>=1.16" in deps
    assert len(deps) == 16          # 15 before this phase, plus alembic


def test_constraints_gains_exactly_two_appended_pins():
    lines = [l for l in (ROOT / "constraints.txt").read_text().splitlines() if l.strip()]
    added = [l for l in lines if l.lower().startswith(("alembic==", "mako=="))]
    assert len(added) == 2
    # appended below the existing lines, not regenerated: the pre-phase tail is intact
    assert lines[-3] == "websocket-client==1.9.2"
    assert lines[-2:] == added


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


def test_phase45_follows_the_baseline_and_is_the_pinned_head():
    from harness.db.migrate import HEAD_REVISION

    module = _load_revision("0002_phase45.py")
    assert module.revision == "0002_phase45"
    assert module.down_revision == "0001_baseline"
    assert HEAD_REVISION == "0002_phase45"


def test_the_phase45_downgrade_is_a_no_op_and_drops_nothing():
    """Conformance item 4: the audit grep for non-additive statements must stay empty, so the
    downgrade is `pass`. The loop never drops; a downgrade is the user's hand action."""
    module = _load_revision("0002_phase45.py")
    assert module.downgrade() is None
    body = (ROOT / "migrations" / "versions" / "0002_phase45.py").read_text().lower()
    for word in ("drop ", "truncate", "delete from"):
        assert word not in body, f"0002_phase45 contains {word!r}"


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
