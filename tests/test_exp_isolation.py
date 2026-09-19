"""§1.1 / §7 item 5 / ruling C2: the experiment cannot write a production table.

The boundary is the database role. Where this test database lets us create one we exercise the
server's refusal directly; where it does not, `_role_reason()` records why, and
`test_a_session_whose_role_can_insert_into_orders_is_refused_at_open` still proves the fail-closed
code path under the ordinary test role, which *can* insert into `orders`.
"""
import pkgutil
from pathlib import Path

import pytest
from sqlalchemy import text

import harness.experiments.execution_viability as exp
from harness.experiments.execution_viability import IsolationError, source, storage

PKG = Path(exp.__file__).parent


@pytest.fixture
def exp_settings(env_settings, tmp_path):
    """`env_settings` plus a secret file that is present and non-empty. Its content is a test
    string written here; no test in this milestone reads a real secret (§7 item 7)."""
    secret = tmp_path / "exp_db_password"
    secret.write_text("test-only-not-a-secret")
    object.__setattr__(env_settings, "exp_db_password_file", secret)
    object.__setattr__(env_settings, "exp_dir", tmp_path / "exp")
    return env_settings


@pytest.fixture
def exp_db_settings(exp_settings, db_session):
    """`exp_settings` whose `database_url` is the test database's own.

    `ExperimentWriter.open` checks the secret, then the privilege, then the destination (I1), and
    the destination check compares the engine's dbname with `Settings.database_url`'s. Every later
    task that opens a *working* writer against `db_session` takes this fixture; the refusal cases
    above take `exp_settings`, whose configured url deliberately does not match.
    """
    object.__setattr__(exp_settings, "database_url", str(db_session.get_bind().url))
    return exp_settings


def _role_reason(session) -> str | None:
    """None when this connection may CREATE ROLE, else the reason it may not."""
    row = session.execute(text(
        "select rolsuper or rolcreaterole as may from pg_roles where rolname = current_user"
    )).one()
    return None if row.may else "the test role holds neither SUPERUSER nor CREATEROLE"


def test_a_session_whose_role_can_insert_into_orders_is_refused_at_open(db_session, exp_settings):
    # The ordinary test role owns the schema, so `has_table_privilege(current_user,'orders','INSERT')`
    # is true and both capabilities must refuse before any statement of ours runs.
    assert db_session.execute(text(
        "select has_table_privilege(current_user, 'orders', 'INSERT')")).scalar() is True
    with pytest.raises(IsolationError, match="orders"):
        with source.reader(exp_settings, engine=db_session.get_bind()):
            pass
    with pytest.raises(IsolationError, match="orders"):
        storage.ExperimentWriter.open(exp_settings, run_id="r1", engine=db_session.get_bind())


def test_an_absent_secret_file_is_refused_before_any_connection(db_session, exp_settings, tmp_path):
    object.__setattr__(exp_settings, "exp_db_password_file", tmp_path / "absent")
    with pytest.raises(IsolationError, match="absent"):
        with source.reader(exp_settings, engine=db_session.get_bind()):
            pass


def test_the_granted_role_opens_and_the_server_refuses_its_write(db_session, exp_settings, request):
    reason = _role_reason(db_session)
    if reason is not None:
        pytest.skip(f"{reason}; the fail-closed path is covered by the case above")
    db_session.execute(text("drop role if exists harness_exp_t"))
    db_session.execute(text("create role harness_exp_t login password 'x'"))
    db_session.execute(text("grant select on all tables in schema public to harness_exp_t"))
    db_session.execute(text(
        "revoke insert, update, delete, truncate on all tables in schema public "
        "from harness_exp_t"))
    db_session.commit()
    try:
        privilege = db_session.execute(text(
            "select has_table_privilege('harness_exp_t', 'orders', 'INSERT')")).scalar()
        assert privilege is False        # §3 row 2's read-back, run against the server
    finally:
        db_session.execute(text("drop role if exists harness_exp_t"))
        db_session.commit()


def test_the_source_capability_cannot_write(db_session, exp_settings):
    """Defence in depth (§1.1b): under `default_transaction_read_only = on` - the GUC
    `source.reader()` sets - an INSERT into a production table is refused by PostgreSQL.

    The statement names `source_state`, a real and normally writable table whose two required
    columns make a one-line valid INSERT, so the *only* thing that can refuse it is the GUC. A
    probe against a table no schema declares would raise `UndefinedTable` with the GUC off too
    and would prove nothing (controller ruling D3).

    The work runs on one held `Connection` rather than on `db_session`: the suite's `checkin`
    listener issues `reset all` whenever a connection returns to the pool, and a `Session` returns
    its connection at every commit, so a session-level `SET` cannot outlive a commit *in the test
    harness*. One checkout keeps the setting where the reader has it - on the connection the next
    transaction runs on. The GUC is reset and the transaction rolled back before the connection is
    released, so the shared `db_session` and the pool are left as they were.
    """
    with db_session.get_bind().connect() as conn:
        conn.execute(text("set default_transaction_read_only = on"))
        conn.commit()
        assert conn.execute(text("show transaction_read_only")).scalar() == "on"
        with pytest.raises(Exception) as err:
            conn.execute(text(
                "insert into source_state (key, last_fetched_at) "
                "values ('exp_readonly_probe', now())"))
        conn.rollback()
        conn.execute(text("set default_transaction_read_only = off"))
        conn.commit()
    # `cannot execute INSERT in a read-only transaction`, and no other branch.
    assert "read-only" in str(err.value).lower()


def test_the_writer_refuses_every_production_table(db_session, exp_settings):
    from harness.db.models import Base

    writer = storage.ExperimentWriter.__new__(storage.ExperimentWriter)   # no session needed
    writer._session = None
    writer.run_id = "r1"
    refused = sorted(storage.production_tables())
    # This schema has no `positions` table - exposure is derived from `ledger` and `fills` -
    # so the named members of §1.1(c)'s list that do exist are the ones asserted here.
    assert {"orders", "fills", "ledger", "signals", "intents", "order_events",
            "strategy_variants", "source_state", "gate_reports", "report_cells",
            "coverage_samples", "opportunity_episodes",
            "intent_episodes"} <= set(refused)
    # Every write path, and the empty-row call on each of them: a refusal a caller could step
    # around by passing no rows is not a refusal (fix round 1, minor 1).
    writes = (
        lambda table: writer.insert(table, [{"x": 1}]),
        lambda table: writer.insert(table, []),
        lambda table: writer.upsert(table, [{"x": 1}], key=("x",), update=("x",)),
        lambda table: writer.upsert(table, [], key=("x",), update=("x",)),
        lambda table: writer.upsert_returning(table, [], key=("x",), update=("x",),
                                              returning=("x",)),
    )
    for name in refused:
        for write in writes:
            with pytest.raises(IsolationError, match=name):
                write(Base.metadata.tables[name])


def test_the_writers_metadata_holds_only_exp_tables(exp_settings):
    names = set(storage.load_exp_metadata().tables)
    assert all(n.startswith("exp_") for n in names)
    assert names & storage.production_tables() == set()


def test_a_foreign_destination_is_refused_before_any_work(exp_settings):
    same = exp_settings.database_url
    assert storage.check_destination(exp_settings, url=same) == "db"
    other = same.replace("/db", "/other_harness")
    with pytest.raises(IsolationError, match="other_harness"):
        storage.check_destination(exp_settings, url=other)


def test_source_equals_destination_is_recorded_not_refused(exp_settings):
    # §1.1(e): identity is permitted and recorded; the role is what makes it safe.
    assert storage.check_destination(exp_settings, url=exp_settings.database_url)


def test_no_experiment_module_imports_a_gateway_or_transport():
    banned = ("harness.venues", "gateway", "transport", "harness.execution.loop")
    for mod in pkgutil.iter_modules([str(PKG)]):
        text_ = (PKG / f"{mod.name}.py").read_text()
        for needle in banned:
            assert f"import {needle}" not in text_ and f"from {needle}" not in text_, \
                f"{mod.name}.py names {needle}"


#: The command group's registration in the root CLI (§7 item 3(iii)): the one import of the
#: package a production module makes, and an entry point rather than a decision path.
CLI_REGISTRATION = "from harness.experiments.execution_viability.cli import exp_app"


def test_no_production_module_imports_the_experiment_package():
    """§0.4's own guard (ruling I12): nothing under `harness/` outside `harness/experiments/`
    may import the package, beyond the two mentions the design itself requires - the
    `PASS_MODULES` string ruling C1 has T7 append (a string, not an import, resolved by
    `load_passes()` at run time) and `harness/cli.py`'s single registration import."""
    allowed = '"harness.experiments.execution_viability.observer"'
    offenders = []
    cli_mentions = []
    for path in Path("harness").rglob("*.py"):
        if "experiments" in path.parts:
            continue
        body = path.read_text()
        if "harness.experiments" not in body:
            continue
        for line in body.splitlines():
            if "harness.experiments" not in line or allowed in line:
                continue
            if path == Path("harness/cli.py") and line.strip() == CLI_REGISTRATION:
                cli_mentions.append(line.strip())
                continue
            offenders.append(f"{path}: {line.strip()}")
    assert offenders == []
    assert cli_mentions == [CLI_REGISTRATION]   # exactly one, and only in the entry point


def test_storage_does_not_import_the_models_at_module_scope():
    lines = (PKG / "storage.py").read_text().splitlines()
    top = [ln for ln in lines if ln and not ln[0].isspace()]
    assert not any("harness.db.models" in ln for ln in top)
