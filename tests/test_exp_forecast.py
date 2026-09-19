"""§1.10: the sample-accrual forecast - scenarios and unknowns, never a gate date.

Three quantities stay apart in every case here: **observed watched fills** (live facts),
**stateful exploratory estimates** (this milestone's arm results, labelled counterfactual) and
**conditional scenarios**. A partial-fill row is never an order, a counterfactual fill is never
an observed fill, and the projection covers `sharp_two_sided` alone.
"""
import re

import pytest

from harness.experiments.execution_viability import forecast
from harness.experiments.execution_viability.forecast import (SCENARIOS, project,
                                                              render_forecast)


def _observed(*, orders=20, fills=None, fill_rows=None, distinct_games=None, days=10.0, **kw):
    """One coherent §1.10 observed input.

    The defaults are derived rather than asserted independently: three partial fill rows on one
    order are **one** filled order, and a run cannot have filled more distinct games than it
    filled orders. A case that wants an incoherent fixture has to say so explicitly.
    """
    if fills is None:
        fills = min(orders, 3)
    if fill_rows is None:
        fill_rows = fills
    if distinct_games is None:
        distinct_games = min(fills, 12)
    return forecast.Observed(orders=orders, fills=fills, fill_rows=fill_rows,
                             distinct_games=distinct_games, days=days, **kw)


def _exploratory(*, fills=0, orders=0, days=10.0, **kw):
    """Arm B's counterfactual result: a stateful exploratory estimate, never a watched fill."""
    return forecast.Exploratory(fills=fills, orders=orders, days=days, **kw)


def test_the_five_scenarios_are_fixed_before_the_run():
    assert SCENARIOS == ("baseline_continuation", "arm_b_measured", "arm_b_plus_arm_c",
                         "capacity_bound", "pessimistic_markout")


def test_eight_distinct_filled_games_in_ten_days_refuses_to_name_a_date():
    out = render_forecast(project(_observed(distinct_games=8, days=10), _exploratory()))
    assert "accrual unidentified: fewer than 10 distinct filled games observed" in out
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", out)     # no date anywhere in the output


def test_only_sharp_two_sided_is_projected_and_nothing_is_pooled():
    scenarios = project(_observed(), _exploratory())
    assert all(s.sports == ("nfl", "ncaaf") for s in scenarios)
    assert "sharp_direct" not in render_forecast(scenarios)


def test_a_partial_fill_row_is_not_counted_as_an_order():
    # §1.10 projects *actual filled orders*: three partial fills on one order are one
    # order, and 150 is a count of orders, never of fill rows.
    observed = _observed(orders=1, fill_rows=3)
    assert project(observed, _exploratory())[0].unknowns
    assert render_forecast(project(observed, _exploratory())).count("orders=1") == 1


def test_a_counterfactual_fill_is_never_counted_as_an_observed_fill():
    # The three quantities stay apart: observed watched fills, stateful exploratory estimates,
    # conditional scenarios (§1.10).
    observed = _observed(orders=20, fills=3, distinct_games=12)
    exploratory = _exploratory(fills=11)                # arm B's counterfactual fills
    scenarios = {s.name: s for s in project(observed, exploratory)}
    assert scenarios["baseline_continuation"].clean_book_eligible == 3
    assert scenarios["arm_b_measured"].clean_book_eligible == 3      # still the observed count
    out = render_forecast(list(scenarios.values()))
    assert "observed fills: 3" in out and "exploratory (arm B, counterfactual): 11" in out
    assert "observed fills: 11" not in out


def test_the_capacity_bound_scenario_is_bounded_by_slots_times_turnover():
    # §5.3's caveat: 150 shared slots x the measured turnover, never more.
    observed = _observed(orders=20, fills=3, distinct_games=12, turnover_per_day=2.0)
    bound = {s.name: s for s in project(observed, _exploratory())}["capacity_bound"]
    assert bound.high <= 150 * 2.0
    assert "150 shared slots" in render_forecast([bound])


def test_every_scenario_states_its_distinct_game_count_and_its_unknowns():
    for s in project(_observed(), _exploratory()):
        assert s.distinct_games >= 0 and s.unknowns


# --- the same rules, from the other side -------------------------------------------------------


def test_the_shared_slot_count_is_the_executor_s_own_cap_not_a_literal():
    # The bound is `exec_max_open_orders` (D10: shared across the executed variants), read from
    # the settings model rather than re-typed here, so a cap change moves the ceiling with it.
    from harness.config.settings import Settings

    assert forecast.SHARED_SLOTS == Settings.model_fields["exec_max_open_orders"].default


def test_another_portfolio_is_refused_rather_than_projected():
    # §1.10 projects `sharp_two_sided` alone: no pooling with the primary, and no silent
    # relabelling of one portfolio's accrual as another's.
    with pytest.raises(ValueError, match="sharp_two_sided"):
        project(_observed(), _exploratory(), variant="constrained")


def test_an_empty_outcome_table_is_immaturity_and_never_a_zero_markout():
    # Nothing calls `outcomes.record_outcomes` on a real run yet (plan gap G2): an empty
    # `exp_outcome` means the markout is **unavailable**, which is not the same as zero.
    scenarios = project(_observed(distinct_games=12), _exploratory())
    out = render_forecast(scenarios)
    assert all("unavailable" in s.maturity for s in scenarios)
    assert "economics unidentified" in out
    assert "markout sign: 0" not in out


def test_a_time_to_target_is_projected_only_when_accrual_and_economics_are_identified():
    # Identified on both counts: a range of *elapsed days* is projected. Still no calendar
    # date - the gate date follows the user's dated decision (§0.14a) and 6F's (§0.14b).
    identified = _observed(orders=400, fills=60, distinct_games=24, days=30.0,
                           mature_outcomes=60, markout_sign="positive")
    out = render_forecast(project(identified, _exploratory(fills=80)))
    assert "elapsed days" in out
    assert "not projected while accrual or economics is unidentified" not in out
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", out)


def test_a_negative_markout_sign_makes_the_pessimistic_scenario_zero_productive_accrual():
    observed = _observed(orders=400, fills=60, distinct_games=24, days=30.0,
                         mature_outcomes=60, markout_sign="negative")
    pessimistic = {s.name: s for s in project(observed, _exploratory())}["pessimistic_markout"]
    assert pessimistic.low == 0.0 and pessimistic.high == 0.0
    assert any("not economically productive" in u for u in pessimistic.unknowns)


def test_arm_c_that_could_not_be_measured_is_named_rather_than_invented():
    exploratory = _exploratory(fills=11,
                               arm_c="unavailable: budget preflight refused, §1.6(h)")
    combined = {s.name: s for s in project(_observed(), exploratory)}["arm_b_plus_arm_c"]
    assert any("unavailable: budget preflight refused" in u for u in combined.unknowns)
    # Arm C contributes no invented freshness effect, so the range is arm B's own.
    arm_b = {s.name: s for s in project(_observed(), exploratory)}["arm_b_measured"]
    assert (combined.low, combined.high) == (arm_b.low, arm_b.high)


def test_every_scenario_carries_both_sports_and_the_observed_distinct_game_count():
    observed = _observed(orders=20, fills=3, distinct_games=12)
    for s in project(observed, _exploratory(fills=11)):
        assert s.sports == ("nfl", "ncaaf")
        assert s.distinct_games == 12          # the observed count, never a counterfactual one
