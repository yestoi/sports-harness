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


def test_benchmarks_cmd_computes_and_is_idempotent(cli_settings, db_session):
    from decimal import Decimal

    from harness.db.models import Benchmark, Game, OddsSnapshot, VenueMarket

    kickoff = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc) - timedelta(hours=4)
    game = Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kickoff, status="final")
    db_session.add(game)
    db_session.flush()
    db_session.add(VenueMarket(venue="kalshi", ticker="T-ML-HOME", event_ticker="E", series_ticker="S",
                               game_id=game.id, market_type="moneyline", side_team_id=14,
                               match_confidence=Decimal("1.00"), match_status="matched",
                               first_seen_raw_id=1, last_seen_at=kickoff))
    # Fix round 1, M6: a real pinnacle moneyline pair fetched inside the kickoff - 5 min
    # lookback, so the CLI is shown actually computing something, not just running cleanly
    # against an empty game.
    fetched_at = kickoff - timedelta(minutes=6)
    db_session.add(OddsSnapshot(raw_id=1, run_id=1, book="pinnacle", game_id=game.id,
                               market_type="h2h", outcome_team_id=14, price_decimal=Decimal("1.6500"),
                               book_last_update=fetched_at, fetched_at=fetched_at))
    db_session.add(OddsSnapshot(raw_id=2, run_id=1, book="pinnacle", game_id=game.id,
                               market_type="h2h", outcome_team_id=19, price_decimal=Decimal("2.4000"),
                               book_last_update=fetched_at, fetched_at=fetched_at))
    db_session.commit()

    result = runner.invoke(app, ["benchmarks", "--game-id", str(game.id)])
    assert result.exit_code == 0, result.output
    assert f"game_id={game.id}" in result.output
    row = db_session.query(Benchmark).filter_by(game_id=game.id, benchmark_type="pinnacle_t5").one()
    from harness.pricing.devig import devig

    assert row.p == devig([Decimal("1.6500"), Decimal("2.4000")], method="power")[0]

    again = runner.invoke(app, ["benchmarks", "--game-id", str(game.id)])
    assert again.exit_code == 0, again.output
    assert db_session.query(Benchmark).filter_by(game_id=game.id, benchmark_type="pinnacle_t5").count() == 1


def test_benchmarks_cmd_exits_1_for_an_unknown_game(cli_settings, db_session):
    result = runner.invoke(app, ["benchmarks", "--game-id", "999999"])
    assert result.exit_code == 1


# --- Task 12b: `harness note` -----------------------------------------------------------


def test_note_cli_writes_event(cli_settings, db_session):
    from harness.db.models import OperatorEvent

    result = runner.invoke(app, ["note", "--kind", "verify_pass", "week 38 dashboard check OK"])
    assert result.exit_code == 0, result.output

    row = db_session.query(OperatorEvent).one()
    assert row.kind == "verify_pass"
    assert row.summary == "week 38 dashboard check OK"
    assert str(row.id) in result.output.strip()


def test_note_cli_rejects_an_unknown_kind(cli_settings, db_session):
    from harness.db.models import OperatorEvent

    result = runner.invoke(app, ["note", "--kind", "not-a-real-kind", "text"])
    assert result.exit_code == 1
    assert db_session.query(OperatorEvent).count() == 0


def test_runbook_export_fixture_paragraph_matches_the_shipped_command():
    """Final review I5: the runbook's `export-fixture` paragraph had drifted from the CLI --
    it described a fixture *directory* "the same shape as tests/fixtures/day_2026-09-13/",
    named none of the required options, and pointed at a path that does not exist, so an
    operator following it got a Typer usage error. This pins the two ways it drifted: every
    long option the paragraph names is a real option of the command, and every fixture path
    it names is a real path in the tree.
    """
    import re
    from pathlib import Path

    import typer

    root = Path(__file__).parent.parent
    runbook = (root / "docs" / "runbooks" / "phase0-deploy.md").read_text()
    para = next(p for p in runbook.split("\n- **")
                if p.startswith("`harness export-fixture")).split("\n\n")[0]

    command = typer.main.get_command(app).commands["export-fixture"]
    declared = {opt for param in command.params for opt in param.opts}
    assert set(re.findall(r"--[a-z][a-z-]*", para)) <= declared

    for path in re.findall(r"tests/fixtures/[\w./*-]+", para):
        assert list(root.glob(path)), f"{path} does not exist"
