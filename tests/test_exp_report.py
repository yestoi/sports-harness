"""§1.9: two tables, two captions, and a number no reader can mistake for another."""
import pytest

from harness.experiments.execution_viability import exp_label, report
from harness.experiments.execution_viability.report import PortfolioSumRefused

RUN = "0198e2b0-0000-7000-8000-000000000001"
HASH = "a" * 64
ROWS = [{"arm_id": "A", "portfolio": "sharp_two_sided", "orders": 40, "fills": 3,
         "markout_1800": "0.0120", "source_age_s": 45, "registered": True},
        {"arm_id": "B", "portfolio": "sharp_two_sided", "orders": 40, "fills": 11,
         "markout_1800": "0.0090", "source_age_s": 45, "registered": True},
        {"arm_id": "B", "portfolio": "wide_edge", "orders": 12, "fills": 4,
         "markout_1800": "0.0300", "source_age_s": 45, "registered": False}]


def test_two_portfolios_produce_two_rows_and_no_summed_row():
    out = report.arm_table([r for r in ROWS if r["arm_id"] == "B"], run_id=RUN,
                           manifest_hash=HASH)
    assert out.count("sharp_two_sided") == 1 and out.count("wide_edge") == 1
    assert "total" not in out.lower() and "combined" not in out.lower()


def test_summing_two_portfolio_identities_raises():
    with pytest.raises(PortfolioSumRefused):
        report.sum_rows([r for r in ROWS if r["arm_id"] == "B"])


def test_registered_and_exploratory_results_are_in_separate_tables():
    out = report.render([r for r in ROWS if r["registered"]],
                        [r for r in ROWS if not r["registered"]], run_id=RUN, manifest_hash=HASH)
    registered_at, exploratory_at = out.index("Registered results"), out.index("Exploratory")
    assert registered_at < exploratory_at
    assert out.index("wide_edge") > exploratory_at      # the one exploratory row is below it


def test_every_exploratory_cell_carries_the_exp_label():
    out = report.render([], [r for r in ROWS if not r["registered"]], run_id=RUN,
                        manifest_hash=HASH)
    label = exp_label(RUN, "B", HASH)
    assert out.count(label) == 1
    for cell in ("0.0300", "12", "4"):
        line = [ln for ln in out.splitlines() if cell in ln][0]
        assert label in line


def test_the_order_weighted_result_is_primary_and_the_sensitivities_sit_beside_it():
    out = report.arm_table(ROWS, run_id=RUN, manifest_hash=HASH)
    header = [ln for ln in out.splitlines() if "markout" in ln][0]
    assert header.index("order-weighted") < header.index("game-weighted")
    assert "primary" in header and header.count("primary") == 1


def test_source_age_travels_with_every_outcome_number():
    out = report.arm_table(ROWS, run_id=RUN, manifest_hash=HASH)
    for line in [ln for ln in out.splitlines() if "0.0" in ln]:
        assert "45 s" in line          # §1.9(d): no outcome number without its source age


def test_the_episode_rule_and_its_parameters_are_printed_before_any_arm_outcome():
    # §1.9(b): the rule, its parameters and the re-entry statement, above the first number.
    out = report.render([r for r in ROWS if r["registered"]], [], run_id=RUN,
                        manifest_hash=HASH)
    assert out.index("re-entry") < out.index("Registered results")
    assert "same episode" in out and "gap_rule_s" in out


def test_a_one_cluster_interval_is_nan_and_is_printed_as_one():
    # `cluster_ci`'s one-cluster convention is kept intact (§1.9c): a single game has no
    # between-cluster variation, and the report says so rather than printing a zero-width band.
    rows = [{"arm_id": "B", "portfolio": "wide_edge", "orders": 2, "fills": 2,
             "markout_1800": "0.0300", "source_age_s": 45, "registered": False,
             "values": [0.03, 0.03], "clusters": [7, 7]}]
    out = report.arm_table(rows, run_id=RUN, manifest_hash=HASH)
    assert "nan" in out


def test_the_concentration_and_charter_lines_are_never_silently_dropped():
    # §1.9(c)/(f): a run that did not supply them says so; it does not omit them.
    out = report.render([], [], run_id=RUN, manifest_hash=HASH)
    assert "Concentration" in out and "Charter status" in out
    supplied = report.render([], [], run_id=RUN, manifest_hash=HASH,
                             concentration={"max_game_share": "96 of 191 observations",
                                            "allocated_vs_requested": "310 of 900 contracts"},
                             charter=("mispricing map: 31 cells covered",),
                             instants=8123, live_loop_estimate=4800)
    assert "96 of 191 observations" in supplied and "310 of 900 contracts" in supplied
    assert "8123" in supplied and "4800" in supplied
    assert "mispricing map: 31 cells covered" in supplied


def test_the_report_commands_own_statements_run_against_the_schema(db_session):
    # The CLI connects as §1.1(b)'s least-privileged role, which no test holds, so the
    # statements it issues are exercised here against the test schema instead: a column this
    # milestone's tables do not have is a failure now rather than at the first hand run.
    from harness.experiments.execution_viability import cli

    for statement in (cli._REPORT_ARMS, cli._REPORT_OUTCOMES, cli._REPORT_HEALTH,
                      cli._REPORT_SPACING, cli._REPORT_GAME_SHARE, cli._REPORT_ALLOCATION):
        assert db_session.execute(statement, {"r": RUN}).all() is not None
