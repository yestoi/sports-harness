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
#: `alter index` is handled separately, below: fix 32 sanctions exactly one shape of it.
FORBIDDEN = ("drop_index", "create_or_replace", "drop view",
             "drop table", "drop column", "alter column", "rename")

#: The one sanctioned `alter index`: a storage-parameter flip, never a rebuild, never a drop.
_ALLOWED_ALTER_INDEX = re.compile(r"alter index (if exists )?\S+ set \(autosummarize = on\)")


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
    # Scoped to actual DDL (an `op.execute(...)` call), not prose: several migrations now carry
    # comments explaining *why* an ALTER INDEX is or isn't used, and those legitimately contain
    # the words "alter index" without being one.
    for line in body.splitlines():
        if "alter index" in line and "op.execute(" in line:
            assert _ALLOWED_ALTER_INDEX.search(line), f"{path.name}: unexpected alter index: {line}"


@pytest.mark.parametrize("path", VERSIONS, ids=lambda p: p.name)
def test_no_migration_creates_a_bulk_index_outside_concurrent_index(path):
    src = path.read_text()
    for table in BULK_TABLES:
        for match in re.finditer(r"op\.create_index\((.*?)\)", src, re.S):
            assert table not in match.group(1), f"{path.name}: {table} outside concurrent_index"


def test_the_versions_directory_holds_six_revisions():
    assert [p.name for p in VERSIONS] == [
        "0001_baseline.py", "0002_phase45.py", "0003_brin_autosummarize.py",
        "0004_phase5.py", "0005_rfq_lookup.py", "0006_quotes_run_index.py"]


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

def test_quotes_run_index_follows_rfq_lookup_and_is_the_pinned_head():
    from harness.db.migrate import HEAD_REVISION

    module = _load_revision("0006_quotes_run_index.py")
    assert module.revision == "0006_quotes_run_index"
    assert module.down_revision == "0005_rfq_lookup"
    assert HEAD_REVISION == "0006_quotes_run_index"


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

    The model declaration is not as exempt as it looks: `create_all` only builds indexes for
    tables it is creating, but `create_schema` then calls `_model_index_ddl`, which issues a
    plain `index.create(..., checkfirst=True)` for every model index outside `TAPE_TABLES`, and
    `venue_quotes` is a bulk table that is not a tape table. So a *populated* database that
    lacks this index would take a plain, table-locking create there, before the CONCURRENTLY
    entry it would have made a no-op. That hazard predates fix 42 (fix 25's `ix_odds_fetched_book`
    on `odds_snapshots` has the same shape) and is deferred to 6E; the NAS is not exposed because
    the index was built there by hand. Do not read this test as proof that path cannot happen."""
    from harness.db.schema import _CONCURRENT_INDEX_DDL, _INDEX_DDL

    assert not any("ix_quotes_run_market" in s for s in _INDEX_DDL)
    stmt = next(s for s in _CONCURRENT_INDEX_DDL if "ix_quotes_run_market" in s)
    assert stmt.startswith("create index concurrently if not exists")
    src = (ROOT / "migrations" / "versions" / "0006_quotes_run_index.py").read_text()
    assert "autocommit_block" in src
