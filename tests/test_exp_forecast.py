"""§1.10: the sample-accrual forecast - scenarios and unknowns, never a gate date.

Three quantities stay apart in every case here: **observed watched fills** (live facts),
**stateful exploratory estimates** (this milestone's arm results, labelled counterfactual) and
**conditional scenarios**. A partial-fill row is never an order, a counterfactual fill is never
an observed fill, and the projection covers `sharp_two_sided` alone.
"""
import math
import re

import pytest

from harness.experiments.execution_viability import forecast
from harness.experiments.execution_viability.forecast import (SCENARIOS, project,
                                                              render_forecast)


def _observed(*, orders=20, fills=None, fill_rows=None, distinct_games=None, days=10.0, **kw):
    """One coherent §1.10 observed input.

    The defaults are derived rather than asserted independently: three partial fill rows on one
    order are **one** filled order, and a run cannot have filled more distinct games than it
    filled orders. Clean-book eligibility defaults to the fill count **here**, in the fixture,
    because these cases are about the projection; `clean_book_eligible=None` is the unmeasured
    state and has its own case. A case that wants an incoherent fixture has to say so.
    """
    if fills is None:
        fills = min(orders, 3)
    if fill_rows is None:
        fill_rows = fills
    if distinct_games is None:
        distinct_games = min(fills, 12)
    kw.setdefault("clean_book_eligible", fills)
    kw.setdefault("clean_book_source", "this fixture's own eligibility")
    return forecast.Observed(orders=orders, fills=fills, fill_rows=fill_rows,
                             distinct_games=distinct_games, days=days, **kw)


def _exploratory(*, fills=0, days=10.0, **kw):
    """Arm B's counterfactual result: a stateful exploratory estimate, never a watched fill."""
    kw.setdefault("label", "exp:run=r arm=B manifest=abcdef012345")
    return forecast.Exploratory(fills=fills, days=days, **kw)


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


# --- fix round 1 -------------------------------------------------------------------------------


def test_an_unmeasured_clean_book_eligibility_prints_as_unmeasured():
    # Critical 2: the fill count is not a substitute for a measurement. A `None` eligibility is
    # rendered as `not measured`, never as "every filled order rested on a clean book".
    observed = _observed(orders=20, fills=3, distinct_games=12, clean_book_eligible=None,
                         clean_book_source="")
    scenarios = project(observed, _exploratory())
    assert all(s.clean_book_eligible is None for s in scenarios)
    out = render_forecast(scenarios)
    assert "clean-book-eligible filled orders: not measured" in out
    assert "clean-book-eligible filled orders: 3" not in out


def test_a_measured_clean_book_eligibility_cites_its_source():
    observed = _observed(orders=20, fills=3, distinct_games=12, clean_book_eligible=2,
                         clean_book_source="8 exp_book_health intervals")
    out = render_forecast(project(observed, _exploratory()))
    assert "clean-book-eligible filled orders: 2" in out
    assert "measured against 8 exp_book_health intervals" in out


def test_the_capacity_bound_is_not_computed_without_a_measured_turnover():
    # Important 5: the name and the number have to agree. With no turnover there is no ceiling,
    # so the scenario prints `not computed` rather than arm B's range under another name.
    bound = {s.name: s for s in project(_observed(), _exploratory(fills=11))}["capacity_bound"]
    assert math.isnan(bound.low) and math.isnan(bound.high)
    out = render_forecast([bound])
    assert "capacity_bound: not computed" in out
    assert any(u.startswith("limitation (for the verification rows)") for u in bound.unknowns)


def test_the_capacity_bound_low_edge_is_arm_b_s_upper_edge_and_says_so():
    # Minor 3, non-degenerate: 11 counterfactual fills over 10 days put arm B's upper edge at
    # (11 + sqrt(11)) / 10 = 1.432 a day, well under the 150 x 0.02 = 3.0 ceiling.
    observed = _observed(orders=20, fills=3, distinct_games=12, turnover_per_day=0.02)
    exploratory = _exploratory(fills=11)
    scenarios = {s.name: s for s in project(observed, exploratory)}
    bound, arm_b = scenarios["capacity_bound"], scenarios["arm_b_measured"]
    assert bound.high == 150 * 0.02
    assert bound.low == arm_b.high and 1.4 < bound.low < 1.5
    assert any("arm B's **upper** edge" in u for u in bound.unknowns)


def test_every_counterfactual_range_carries_arm_b_s_exploratory_label():
    # Important 1: a range that is arm B's counterfactual number is labelled where it is
    # printed, not only in the baseline scenario's provenance line.
    label = "exp:run=r arm=B manifest=abcdef012345"
    scenarios = {s.name: s for s in project(_observed(turnover_per_day=0.02),
                                            _exploratory(fills=11, label=label))}
    for name in ("arm_b_measured", "arm_b_plus_arm_c", "capacity_bound"):
        assert any(label in u for u in scenarios[name].unknowns), name
    assert label in render_forecast(list(scenarios.values()))


def test_a_render_with_no_label_says_so_rather_than_printing_an_empty_bracket():
    scenarios = project(_observed(), forecast.Exploratory(fills=11, days=10.0))
    out = render_forecast(scenarios)
    assert forecast.NO_LABEL in out and "[]" not in out


def test_the_time_to_target_headline_is_the_combined_range():
    # Minor 4: 150 orders across 40 games needs both, so the headline is the later component at
    # each edge. 60 fills over 30 days -> 1.742 - 2.258 a day -> 66 - 86 days on orders; 24
    # distinct games over 30 days -> 40 games in 50 days. The headline low edge is therefore 66.
    identified = _observed(orders=400, fills=60, distinct_games=24, days=30.0,
                           mature_outcomes=60, markout_sign="positive")
    line = [u for u in project(identified, _exploratory(fills=80))[0].unknowns
            if u.startswith("time to 150 filled orders")][0]
    assert "66 - 86 elapsed days" in line
    assert "the later of the two components at each edge" in line
    assert "orders 66 - 86" in line and "distinct games 50" in line


def test_an_unread_window_is_not_a_one_day_window():
    # Minor 11: an unread base says so where the accrual sentence is, not only in a footnote.
    observed = forecast.Observed(days=1.0, window_read=False)
    out = render_forecast(project(observed, _exploratory()))
    assert "window: unread" in out
    assert "0 in 1 elapsed days" not in out


def test_a_one_sport_forecast_is_refused():
    # Minor 7: "both sports" is a constraint, not a default.
    with pytest.raises(ValueError, match="both sports"):
        project(_observed(sports=("nfl",)), _exploratory())


def test_the_caller_may_pass_the_deployment_s_effective_slot_cap():
    # Minor 6: the module default is the settings model's; a caller holding live settings passes
    # its own, and the printed ceiling moves with it.
    observed = _observed(orders=20, fills=3, distinct_games=12, turnover_per_day=2.0)
    bound = {s.name: s for s in project(observed, _exploratory(), shared_slots=40)}[
        "capacity_bound"]
    assert bound.high == 40 * 2.0
    assert "40 shared slots" in render_forecast([bound])
