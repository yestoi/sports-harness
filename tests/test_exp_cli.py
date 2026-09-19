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


# --- T7: `exp observe` (fix round 1, Important 8) ----------------------------------------------


def test_observe_is_registered_and_documents_its_two_options():
    assert "observe" in runner.invoke(exp_app, ["--help"]).stdout
    result = runner.invoke(exp_app, ["observe", "--help"])
    assert result.exit_code == 0
    rendered = " ".join(result.stdout.split())
    assert "--run-id" in rendered and "--once" in rendered


def test_observe_refuses_a_run_that_was_never_frozen(db_session, env_settings, monkeypatch):
    """§1.6(h): arm C is reported unavailable with its reason; no tier is bought, no cap raised.

    `_step_zero` is replaced with its pass answer so this case reaches step 4's coverage check:
    the test role's privilege read-back is a different refusal, covered by its own message.
    """
    from contextlib import contextmanager

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    monkeypatch.setattr(cli, "_step_zero", lambda session: None)
    result = runner.invoke(exp_app, ["observe", "--run-id",
                                     "0198e2b0-0000-7000-8000-00000000dead"])
    lines = result.stdout.splitlines()
    assert result.exit_code == 1
    # §0.6: the label first, before any other line the command prints.
    assert lines[0].strip() == cli.EXP_LABEL
    unavailable = [ln for ln in lines if ln.startswith("arm C unavailable:")]
    assert len(unavailable) == 1 and "no frozen exp_run row" in unavailable[0]
    assert any("no tier is bought and no cap is raised" in ln for ln in lines)
    # The activation checklist and the live gate values are printed, and no secret is.
    assert any("activation checklist" in ln for ln in lines)
    assert any("research_worker_enabled=" in ln and "anthropic_key_present=" in ln
               for ln in lines)
    assert "test-key" not in result.stdout
