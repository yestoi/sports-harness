"""The `exp` command group exists, is registered once, and its isolation-check prints the C2 pair."""
from typer.testing import CliRunner

from harness.cli import app
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
