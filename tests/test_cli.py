import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.models import Run

runner = CliRunner()


@pytest.fixture
def cli_settings(monkeypatch, db_session):
    """Point `harness.cli`'s own engine at the same Postgres database `db_session` uses.

    `price_once` builds its own engine/session from `get_settings()` rather than the test's
    `db_session`, so committed rows are shared through the real database, not the ORM session.
    """
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_price_once_with_run_id_uses_that_runs_finished_at_not_wall_clock(monkeypatch, cli_settings, db_session):
    started_at = datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc)
    finished_at = started_at + timedelta(seconds=40)
    run = Run(started_at=started_at, finished_at=finished_at, status="ok")
    unfinished = Run(started_at=started_at, status="running")
    db_session.add_all([run, unfinished])
    db_session.commit()

    captured = {}

    def fake_price_and_signal(session, run_id, now, settings, budget_s):
        captured["now"] = now
        captured["run_id"] = run_id
        return {"fake": True}

    # cli.py does `from harness.strategy.pipeline import price_and_signal` inside the command
    # body, so the lookup happens against the pipeline module's namespace on every call.
    monkeypatch.setattr("harness.strategy.pipeline.price_and_signal", fake_price_and_signal)

    result = runner.invoke(app, ["price-once", "--run-id", str(run.id)])
    assert result.exit_code == 0, result.output
    assert captured["run_id"] == run.id
    # The run's own odds are fetched after started_at; finished_at bounds everything it recorded.
    assert captured["now"] == finished_at

    result = runner.invoke(app, ["price-once", "--run-id", str(unfinished.id)])
    assert result.exit_code == 0, result.output
    from harness.config.settings import Settings

    budget = Settings.model_fields["tick_budget_s"].default
    assert captured["now"] == started_at + timedelta(seconds=budget)


def test_price_once_with_unknown_run_id_errors_without_pricing(monkeypatch, cli_settings):
    called = {"n": 0}

    def fake_price_and_signal(*args, **kwargs):
        called["n"] += 1
        return {}

    monkeypatch.setattr("harness.strategy.pipeline.price_and_signal", fake_price_and_signal)

    result = runner.invoke(app, ["price-once", "--run-id", "999999999"])

    assert result.exit_code == 1
    assert called["n"] == 0


def test_price_once_without_run_id_uses_wall_clock_for_the_latest_run(monkeypatch, cli_settings, db_session):
    older = Run(started_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc), status="ok")
    latest = Run(started_at=datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc), status="running")
    db_session.add_all([older, latest])
    db_session.commit()

    captured = {}
    fixed_wall_clock = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_wall_clock

    def fake_price_and_signal(session, run_id, now, settings, budget_s):
        captured["now"] = now
        captured["run_id"] = run_id
        return {}

    monkeypatch.setattr("harness.strategy.pipeline.price_and_signal", fake_price_and_signal)
    monkeypatch.setattr("harness.cli.datetime", FixedDatetime)

    result = runner.invoke(app, ["price-once"])

    assert result.exit_code == 0, result.output
    assert captured["run_id"] == latest.id
    assert captured["now"] == fixed_wall_clock


def test_partition_bulk_tables_is_a_no_op_once_the_tape_is_partitioned(monkeypatch, cli_settings, db_session):
    """The one-off migration is run by hand on the live database and must be safe to re-run: the
    tape tables here are already partitioned, so it changes nothing. It builds its engine with the
    batch statement timeout because validating the legacy CHECK and attaching the partition both
    scan the tape, far past the 30 s default."""
    from harness.db import engine as engine_module
    from harness.db.engine import BATCH_STATEMENT_TIMEOUT_MS

    seen: list[int] = []
    real_make_engine = engine_module.make_engine

    def spy(url, statement_timeout_ms=30000):
        seen.append(statement_timeout_ms)
        return real_make_engine(url, statement_timeout_ms)

    monkeypatch.setattr("harness.cli.make_engine", spy)
    result = runner.invoke(app, ["partition-bulk-tables"])
    assert result.exit_code == 0, result.output
    assert seen == [BATCH_STATEMENT_TIMEOUT_MS]
    partitioned = set(db_session.execute(text(
        "select relname from pg_partitioned_table join pg_class on oid = partrelid")).scalars())
    assert {"raw_responses", "orderbook_events", "venue_trades"} <= partitioned


def test_init_db_runs_its_ddl_under_the_batch_statement_timeout(monkeypatch, cli_settings, db_session):
    """create_schema's DDL can legitimately outrun the 30 s default engine timeout, and
    query_canceled (57014) is in its retry set, so a slow statement under the default would be
    cancelled, retried and cancelled again rather than finishing."""
    from harness.db import engine as engine_module
    from harness.db.engine import BATCH_STATEMENT_TIMEOUT_MS

    seen: list[int] = []
    real_make_engine = engine_module.make_engine

    def spy(url, statement_timeout_ms=30000):
        seen.append(statement_timeout_ms)
        return real_make_engine(url, statement_timeout_ms)

    monkeypatch.setattr("harness.cli.make_engine", spy)
    result = runner.invoke(app, ["init-db"])
    assert result.exit_code == 0, result.output
    assert seen == [BATCH_STATEMENT_TIMEOUT_MS]
