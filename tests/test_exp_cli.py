"""The `exp` command group exists, is registered once, and its isolation-check prints the C2 pair."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from harness.cli import app
from harness.db.models import Order
from harness.execution.plan import ExecSettings
from harness.experiments.execution_viability import adapter, arms, cli
from harness.experiments.execution_viability.cli import exp_app

runner = CliRunner()


def test_the_exp_group_is_registered_on_the_root_app():
    names = [g.name for g in app.registered_groups]
    assert names.count("exp") == 1
    assert "variants" in names and "migrate" in names        # the existing groups are untouched


def test_the_group_lists_its_commands_without_a_database():
    result = runner.invoke(exp_app, ["--help"])
    assert result.exit_code == 0
    assert "isolation-check" in result.stdout


def test_policy_compare_help_points_at_the_stateful_runner():
    result = runner.invoke(app, ["policy-compare", "--help"])
    assert result.exit_code == 0
    # Typer renders the help at the terminal width, so the pointer is asserted against the
    # whitespace-normalised text rather than against rich's line breaks.
    rendered = " ".join(result.stdout.split())
    assert "admission" in rendered and "harness exp run" in rendered


def test_the_report_command_is_registered_and_takes_a_run_id():
    # Renamed in fix round 1 (Minor 7): this case asserts registration and the option, and the
    # refusal it used to promise is exercised against the command body in
    # `tests/test_exp_report.py::test_the_report_command_refuses_a_run_that_was_never_frozen`.
    result = runner.invoke(exp_app, ["--help"])
    assert "report" in result.stdout
    help_text = runner.invoke(exp_app, ["report", "--help"])
    assert help_text.exit_code == 0
    rendered = " ".join(help_text.stdout.split())
    assert "--run-id" in rendered


WINDOW_START = datetime(2026, 9, 16, 11, 55, tzinfo=timezone.utc)
FAIR_TS = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)     # 07:00 CT, outside any window


def test_run_refuses_an_arm_that_is_not_one_of_the_experiments():
    # §1.6 (fix round 1, Important 3): an unknown arm used to be accepted and stepped under
    # the baseline, so a run labelled with it would have written `exp_order` rows nobody could
    # attribute. The refusal is argument validation: it happens before any database work.
    result = runner.invoke(exp_app, ["run", "--run-id", "0198e2b0-0000-7000-8000-000000000001",
                                     "--arm", "Z", "--since", "2026-09-16T11:00:00+0000",
                                     "--until", "2026-09-16T13:00:00+0000",
                                     "--variants", "v1"])
    assert result.exit_code == 2                    # click's usage error, not a traceback
    # The message itself, from the callback: the runner sends a usage error to stderr, which
    # this click version keeps out of `result.stdout`.
    with pytest.raises(typer.BadParameter) as raised:
        cli.run_cmd(run_id="0198e2b0-0000-7000-8000-000000000001", arm="Z",
                    since=datetime(2026, 9, 16, 11, tzinfo=timezone.utc),
                    until=datetime(2026, 9, 16, 13, tzinfo=timezone.utc), variants="v1",
                    chunk_hours=1.0)
    assert "--arm" in str(raised.value) and "A, B" in str(raised.value)


def _seed_order(session, *, sport="nfl", variant_id="v1"):
    session.add(Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi",
                      client_order_id=f"cli-{uuid.uuid4().hex[:8]}", ticker="KXNFLGAME-X",
                      venue_market_id=1, side="yes", prob=Decimal("0.4800"),
                      contracts=Decimal("10"), status="open", placed_at=FAIR_TS, sport=sport))
    session.flush()


def test_arm_b_is_built_with_a_bound_allowance_and_arm_a_with_none(db_session, env_settings):
    # §1.6(a)/M2: the command binds B's allowance before the runner is used, so a run labelled
    # B can never decide under A's rule. A's runner carries `None` and its branch stays dead.
    _seed_order(db_session)
    kwargs = dict(variant_ids=["v1"], since=WINDOW_START, until=FAIR_TS + timedelta(hours=1),
                  window_start=WINDOW_START)
    allowance = cli._cadence_allowance(db_session, arms.ARMS["B"], env_settings, **kwargs)
    assert allowance is not None
    assert cli._cadence_allowance(db_session, arms.ARMS["A"], env_settings, **kwargs) is None

    exec_settings = ExecSettings.from_settings(env_settings)
    b = adapter.ArmRunner(run_id="0198e2b0-0000-7000-8000-000000000001", arm_id="B",
                          policy=arms.ARMS["B"].policy, variant_cfg={},
                          exec_settings=exec_settings, cadence_allowance=allowance)
    a = adapter.ArmRunner(run_id="0198e2b0-0000-7000-8000-000000000001", arm_id="A",
                          policy=arms.ARMS["A"].policy, variant_cfg={},
                          exec_settings=exec_settings, cadence_allowance=None)
    assert b.policy.cadence_allowance is allowance
    assert b.policy.cadence_allowance is not arms._unbound      # the placeholder was replaced
    assert a.policy.cadence_allowance is None
    # And the bound callable answers against the real reconstruction: an unanchored overnight
    # row (07:00 CT, no kickoff in the tape) takes I4's closed form.
    assert allowance(SimpleNamespace(fair_ts=FAIR_TS)) == arms.OVERNIGHT_UNANCHORED_S


def test_a_window_that_reconstructs_no_sport_is_refused_rather_than_guessed(db_session,
                                                                            env_settings):
    with pytest.raises(typer.BadParameter):
        cli._cadence_allowance(db_session, arms.ARMS["B"], env_settings, variant_ids=["nope"],
                               since=WINDOW_START, until=FAIR_TS + timedelta(hours=1),
                               window_start=WINDOW_START)
