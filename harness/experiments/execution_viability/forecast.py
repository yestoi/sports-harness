"""§1.10: the sample-accrual forecast for `sharp_two_sided`, and nothing else.

What this module refuses to do is the point of it:

* **No pooling.** Only `sharp_two_sided` is projected, and only for both sports. The primary's
  accrual is a different portfolio identity, and `report.sum_rows` already refuses to add two of
  them together (§1.5); this module refuses to *project* a second one at all.
* **No partial-fill row counted as an order.** `Observed.fills` is a count of **filled orders**;
  `Observed.fill_rows` is beside it so the distinction is visible, never so it can be summed.
  Three partial fills on one order are one order, and the 150-order target is a count of orders.
* **No counterfactual fill counted as an observed fill.** The three quantities §1.10 names stay
  apart in the types themselves: `Observed` holds the **watched** facts, `Exploratory` holds
  this milestone's **arm results** (stateful, labelled, counterfactual), and `Scenario` holds a
  **conditional** projection built from them. Every scenario's `distinct_games` is an observed
  count, whatever the scenario's range was computed from, and every scenario whose range is arm
  B's carries arm B's `exp_label` beside it (§0.10, §1.9e).
* **No unmeasured number printed as a measured one.** `clean_book_eligible` is `None` until
  something measures it and prints as `not measured`; a scenario whose ceiling cannot be
  computed prints its range as `not computed` rather than showing a different scenario's
  numbers under its name; an unread observation window says so instead of showing one day.
* **No gate date.** The forecast publishes ranges and named unknowns. A time-to-target in
  *elapsed days* is projected only when accrual **and** economics are identified, and no
  calendar date is ever printed: the gate date follows the user's dated decision (§0.14a) and
  6F's dates (§0.14b), neither of which this module holds.
* **No portfolio forecast built out of independent tracks.** The 770 counterfactual tracks of
  the review are not a portfolio; a range here is one portfolio identity's.

The scenario set is fixed **before** the run (`SCENARIOS`), so a run cannot choose the scenario
that flatters it afterwards.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from harness.config.settings import Settings

#: §1.10: both sports, never one of them. `project` refuses any other pair.
SPORTS: tuple[str, ...] = ("nfl", "ncaaf")

#: The one portfolio §1.10 projects. `project` refuses any other rather than relabelling.
PROJECTED_VARIANT: str = "sharp_two_sided"

#: §5.3's caveat is bounded by the executor's **shared** order slots (D10: shared across the
#: executed variants). This is the settings model's default, used for the module-level text; a
#: caller that holds the deployment's effective settings passes `shared_slots=` to `project`.
SHARED_SLOTS: int = int(Settings.model_fields["exec_max_open_orders"].default)

#: The distinct-game count below which accrual is unidentified and no time-to-target is named.
IDENTIFIED_MIN_GAMES: int = 10

#: §1.10's targets: 150 actual filled orders across 40 games, in both sports.
TARGET_ORDERS: int = 150
TARGET_GAMES: int = 40

#: Fixed before the run (§1.10): (i) baseline continuation, (ii) arm B's measured admission and
#: fill rate at the observed distinct-game rate, (iii) arm B plus arm C's measured freshness
#: effect if C ran, (iv) the capacity bound, (v) the pessimistic markout sign.
SCENARIOS: tuple[str, ...] = ("baseline_continuation", "arm_b_measured", "arm_b_plus_arm_c",
                              "capacity_bound", "pessimistic_markout")

#: The sentence §1.10's expected result fixes, printed instead of a date.
ACCRUAL_UNIDENTIFIED = (f"accrual unidentified: fewer than {IDENTIFIED_MIN_GAMES} distinct "
                        "filled games observed")

#: Its economic twin: a markout nobody has measured is **unavailable**, never zero (plan gap G2
#: - nothing calls `outcomes.record_outcomes` on a real run yet, so an empty `exp_outcome` is an
#: immature measurement and not a finding).
ECONOMICS_UNIDENTIFIED = ("economics unidentified: no mature markout outcome for the observed "
                          "fills; an empty exp_outcome is an unavailable outcome, never a zero "
                          "markout (§1.9a)")

#: Arm C's default standing: unmeasured, and named as such (§1.6h, §9).
ARM_C_UNMEASURED = ("unavailable: arm C was not measured on this run; no faster-history "
                    "observation is invented in its place (§1.6h)")

#: The markout signs this module understands. `None` is the fourth state and means *unmeasured*.
MARKOUT_SIGNS: tuple[str, ...] = ("positive", "negative", "flat")

#: A range that could not be computed. `nan` rather than a number, so no arithmetic downstream
#: can quietly turn "not computed" into a bound, and `render_forecast` prints it as words.
NOT_COMPUTED: float = float("nan")

#: What a scenario with no `exp_label` beside its counterfactual numbers prints instead.
NO_LABEL = "no exp_label was supplied to this render"


@dataclass(frozen=True, slots=True)
class Observed:
    """§1.10's first quantity: **observed watched fills**, live facts only.

    `fills` counts filled **orders**; `fill_rows` counts the fill rows those orders took, and
    exists so a reader can see that three partial fills are one order rather than three.
    `clean_book_eligible` is the filled orders whose book the health classification could not
    fault: it stays `None` until something measures it, and a `None` prints as `not measured`
    rather than as the fill count. `window_read` is false when the record could not be read at
    all, which is not the same as a window with nothing in it.
    """

    orders: int = 0
    fills: int = 0
    fill_rows: int = 0
    distinct_games: int = 0
    days: float = 1.0
    sports: tuple[str, ...] = SPORTS
    clean_book_eligible: int | None = None
    #: Where the eligibility above was measured, cited beside it when it exists.
    clean_book_source: str = ""
    #: Measured turnovers per shared slot per day. `None` means it was not measured, and the
    #: capacity ceiling is then **not computed** rather than guessed.
    turnover_per_day: float | None = None
    mature_outcomes: int = 0
    markout_sign: str | None = None
    window_read: bool = True


@dataclass(frozen=True, slots=True)
class Exploratory:
    """§1.10's second quantity: this milestone's **arm results** - stateful and counterfactual.

    `fills` is arm B's counterfactual filled-order count. It is labelled everywhere it is
    printed - `label` carries `exp_label(run_id, arm_id, manifest_hash)` - and is never added
    to, or substituted for, `Observed.fills`. `arm_c` carries arm C's measured freshness effect
    or the explicit reason it could not be measured; `arm_c_multiplier` is the only way a C
    effect enters a range, and it is `None` unless C actually ran.
    """

    fills: int = 0
    days: float = 1.0
    label: str = ""
    arm_c: str = ARM_C_UNMEASURED
    arm_c_multiplier: float | None = None

    def labelled(self) -> str:
        return self.label or NO_LABEL


@dataclass(frozen=True, slots=True)
class Scenario:
    """One conditional projection: a **range** of filled orders a day, with its unknowns named.

    `low`/`high` are filled orders a day for `sharp_two_sided`, or `NOT_COMPUTED` when the
    scenario's own inputs are missing; `distinct_games` is the run's **observed** count and
    `clean_book_eligible` its observed eligibility or `None` for unmeasured, whatever the range
    was computed from; `maturity` states what the outcome record can and cannot answer; and
    `unknowns` is never empty - a scenario with nothing unknown about it is not a scenario.
    """

    name: str
    distinct_games: int
    sports: tuple[str, ...]
    clean_book_eligible: int | None
    maturity: str
    low: float
    high: float
    unknowns: tuple[str, ...] = field(default_factory=tuple)


def _band(count: float, days: float) -> tuple[float, float]:
    """A Poisson count band (`count ± sqrt(count)`) turned into a per-day rate.

    Deliberately crude and deliberately named as such in every scenario's unknowns: with a
    handful of fills there is no fitted model to report, and a narrow interval would be a
    stronger claim than the record supports.
    """
    if days <= 0:
        raise ValueError("a rate needs a positive elapsed window")
    se = math.sqrt(max(float(count), 0.0))
    return max(float(count) - se, 0.0) / days, (float(count) + se) / days


def _computed(*values: float) -> bool:
    return not any(math.isnan(value) for value in values)


def _rate_phrase(low: float, high: float) -> str:
    if not _computed(low, high):
        return "not computed (this scenario's own input is missing; see its unknowns)"
    return f"{low:.3f} - {high:.3f} filled orders a day"


def _time_to_target(low: float, high: float, games_per_day: float, *, target_orders: int,
                    target_games: int, identified: bool) -> str:
    """The §1.10 headline, in **elapsed days** and never as a calendar date.

    The headline range is the **combined** one - at each edge, the later of the order component
    and the distinct-game component, because 150 orders across 40 games needs both - and the two
    components are printed beneath it so the combination is reconstructable.
    """
    head = f"time to {target_orders} filled orders across {target_games} distinct games"
    if not identified:
        return (f"{head}: not projected while accrual or economics is unidentified; §1.10 "
                "publishes scenarios and unknowns rather than a gate date")
    if not _computed(low, high):
        return f"{head}: not projected - this scenario's range is not computed"
    if high <= 0:
        return (f"{head}: not projected - the upper edge of this scenario's range is zero "
                "filled orders a day, so no elapsed time follows from it")
    orders_fast = target_orders / high
    orders_slow = None if low <= 0 else target_orders / low
    games_days = None if games_per_day <= 0 else target_games / games_per_day
    fast = orders_fast if games_days is None else max(orders_fast, games_days)
    slow = (None if orders_slow is None
            else (orders_slow if games_days is None else max(orders_slow, games_days)))
    headline = (f"{fast:.0f} - {slow:.0f} elapsed days" if slow is not None
                else f"{fast:.0f} elapsed days at the upper edge, unbounded at the lower edge, "
                     "which is zero")
    components = (f"orders {orders_fast:.0f}"
                  + (f" - {orders_slow:.0f}" if orders_slow is not None else " - unbounded"))
    components += (f"; distinct games {games_days:.0f}" if games_days is not None
                   else "; distinct games: no observed rate, so the headline is the order "
                        "component alone")
    return (f"{head}: {headline} - the later of the two components at each edge ({components}); "
            "no calendar date is named here - the gate date follows the user's dated decision "
            "(§0.14a) and 6F's dates (§0.14b)")


def _maturity(observed: Observed) -> str:
    if observed.mature_outcomes <= 0:
        return ("outcome maturity: unavailable - no mature markout outcome for these fills; "
                "nothing has recorded this run's outcomes yet, and an absent outcome is not a "
                "zero one (§1.9a)")
    sign = observed.markout_sign or "unmeasured"
    return (f"outcome maturity: {observed.mature_outcomes} mature markout outcomes for the "
            f"projected portfolio identity's baseline arm, post-repair sign {sign}")


def project(observed: Observed, exploratory: Exploratory, *, target_orders: int = TARGET_ORDERS,
            target_games: int = TARGET_GAMES, variant: str = PROJECTED_VARIANT,
            shared_slots: int = SHARED_SLOTS) -> list[Scenario]:
    """The five scenarios of `SCENARIOS`, in that order, for `sharp_two_sided` alone.

    Every scenario is built from the same observed base; only the **range** differs, and each
    range says in its unknowns which quantity it rests on and, where that quantity is arm B's,
    carries arm B's exploratory label. Nothing here sums an observed count and a counterfactual
    one, and nothing invents a number the record does not carry.
    """
    if variant != PROJECTED_VARIANT:
        raise ValueError(
            f"§1.10 projects {PROJECTED_VARIANT} alone: refusing to project {variant!r}, which "
            "is a different portfolio identity and would be pooling, not forecasting")
    if tuple(observed.sports) != SPORTS:
        raise ValueError(
            f"§1.10 projects both sports {SPORTS}: refusing {tuple(observed.sports)!r}, which "
            "would be a one-sport forecast presented as the milestone's")
    if observed.days <= 0:
        raise ValueError("observed.days must be positive: an accrual rate needs an elapsed "
                         "window to be a rate of")
    if observed.markout_sign is not None and observed.markout_sign not in MARKOUT_SIGNS:
        raise ValueError(f"markout_sign must be one of {MARKOUT_SIGNS} or None (unmeasured), "
                         f"not {observed.markout_sign!r}")

    maturity = _maturity(observed)
    accrual_ok = observed.distinct_games >= IDENTIFIED_MIN_GAMES
    economics_ok = observed.mature_outcomes > 0 and observed.markout_sign is not None
    identified = accrual_ok and economics_ok
    games_per_day = observed.distinct_games / observed.days if observed.window_read else 0.0
    window = (f"{observed.distinct_games} in {observed.days:g} elapsed days"
              if observed.window_read
              else f"{observed.distinct_games} observed; window: unread")

    common: list[str] = []
    if not accrual_ok:
        common.append(f"{ACCRUAL_UNIDENTIFIED} ({window})")
    if not economics_ok:
        common.append(ECONOMICS_UNIDENTIFIED)
    if not observed.window_read:
        common.append("the observation window could not be read from the record, so every rate "
                      "below rests on an unread base: it is empty because it is unread, not "
                      "because it is a measured zero")
    common.append("the interval is a Poisson count band on the fills the record holds, not a "
                  "fitted model; it widens with nothing but the count")
    common.append("770 independent counterfactual tracks are never turned into a portfolio "
                  "forecast: this range is one portfolio identity's (§1.5, §1.10)")

    eligibility = (f"clean-book eligibility: {observed.clean_book_eligible} filled orders"
                   + (f", measured against {observed.clean_book_source}"
                      if observed.clean_book_source else ", with no source cited")
                   if observed.clean_book_eligible is not None else
                   "clean-book eligibility: not measured - nothing in this render classified "
                   "the book each fill rested on, and the fill count is not a substitute")
    provenance = (
        f"observed base: orders={observed.orders} placed, {observed.fill_rows} partial-fill "
        f"rows on {observed.fills} filled orders (a partial-fill row is never an order); "
        "replayed rows are excluded from both halves, so every figure here is a live watched "
        "fact",
        f"observed fills: {observed.fills} (watched, live facts)",
        eligibility,
        f"exploratory (arm B, counterfactual): {exploratory.fills} filled orders "
        f"[{exploratory.labelled()}], labelled and never added to the observed count")

    o_low, o_high = _band(observed.fills, observed.days)
    b_low, b_high = _band(exploratory.fills, exploratory.days or observed.days)
    arm_b_label = (f"this range is arm B's counterfactual fill rate, labelled "
                   f"[{exploratory.labelled()}]: it is not a registered variant's performance "
                   "(§0.10, §1.9e)")

    baseline = Scenario(
        name="baseline_continuation", distinct_games=observed.distinct_games,
        sports=tuple(observed.sports), clean_book_eligible=observed.clean_book_eligible,
        maturity=maturity, low=o_low, high=o_high,
        unknowns=(*provenance,
                  "today's posture continued unchanged: the observed watched fills carried "
                  "forward at the rate they arrived",
                  _time_to_target(o_low, o_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    arm_b = Scenario(
        name="arm_b_measured", distinct_games=observed.distinct_games,
        sports=tuple(observed.sports), clean_book_eligible=observed.clean_book_eligible,
        maturity=maturity, low=b_low, high=b_high,
        unknowns=(arm_b_label,
                  f"exploratory (arm B, counterfactual): {exploratory.fills} filled orders over "
                  f"{exploratory.days:g} elapsed days [{exploratory.labelled()}]; arm B's "
                  "measured admission and fill rate carried forward at the observed "
                  "distinct-game rate",
                  f"observed fills: {observed.fills} - the observed count is unchanged by this "
                  "scenario; only the projected rate is arm B's",
                  "arm B's fills are counterfactual: they were never resting in the venue's "
                  "book and no other participant traded against them",
                  _time_to_target(b_low, b_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    if exploratory.arm_c_multiplier is None:
        c_low, c_high = b_low, b_high
        c_unknowns = (arm_b_label,
                      f"arm C: {exploratory.arm_c}",
                      "arm C contributes no freshness effect to this range: an arm reported "
                      "unavailable with its reason is a complete input (§9), and zero "
                      "faster-history observations are invented for it, so the range is arm "
                      "B's own")
    else:
        multiplier = float(exploratory.arm_c_multiplier)
        c_low, c_high = b_low * multiplier, b_high * multiplier
        c_unknowns = (f"this range is arm B's counterfactual fill rate scaled by arm C's "
                      f"measured effect, labelled [{exploratory.labelled()}]: not a registered "
                      "variant's performance (§0.10, §1.9e)",
                      f"arm C: {exploratory.arm_c}",
                      f"arm C's measured freshness effect applied as x{multiplier:g} on arm B's "
                      "range; the effect was measured on observations, not on fills, so it is "
                      "an assumption about admission and not a measured fill rate")
    arm_bc = Scenario(
        name="arm_b_plus_arm_c", distinct_games=observed.distinct_games,
        sports=tuple(observed.sports), clean_book_eligible=observed.clean_book_eligible,
        maturity=maturity, low=c_low, high=c_high,
        unknowns=(*c_unknowns,
                  _time_to_target(c_low, c_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    if observed.turnover_per_day is None:
        # §1.10 (iv) needs a measured turnover. Without one the scenario has no range of its
        # own, and printing arm B's numbers under the name `capacity_bound` would read as a
        # bound nobody computed - so the range is `not computed` and the gap is a limitation.
        cap_low, cap_high = NOT_COMPUTED, NOT_COMPUTED
        cap_unknowns = (
            f"not computed: the {shared_slots} shared slots saturate under a resting policy, "
            "but this render supplies no measured turnover per slot per day, so the slots x "
            "turnover ceiling has no value (§5.3's caveat)",
            "limitation (for the verification rows): no measured turnover per shared slot per "
            "day exists in the record, so §1.10's scenario (iv) cannot be computed end to end; "
            "it is named here rather than approximated")
    else:
        ceiling = shared_slots * float(observed.turnover_per_day)
        # The low edge is arm B's **upper** edge: the scenario asks what happens between "no
        # better than the best arm B measured" and full saturation of the shared slots.
        cap_low, cap_high = min(b_high, ceiling), ceiling
        cap_unknowns = (
            arm_b_label,
            f"bounded by {shared_slots} shared slots x measured turnover "
            f"{observed.turnover_per_day:g} a day = {ceiling:g} filled orders a day; the honest "
            "post-change fill count is never above it (§5.3's caveat)",
            f"the lower edge of this range is arm B's **upper** edge ({b_high:.3f} a day, capped "
            "at the ceiling): the scenario spans 'no better than the best arm B measured' to "
            "full saturation, and is not a continuation of the observed rate",
            f"the {shared_slots} shared slots are shared across the executed variants (D10), so "
            "this ceiling is not sharp_two_sided's alone and the scenario is an upper bound")
    capacity = Scenario(
        name="capacity_bound", distinct_games=observed.distinct_games,
        sports=tuple(observed.sports), clean_book_eligible=observed.clean_book_eligible,
        maturity=maturity, low=cap_low, high=cap_high,
        unknowns=(*cap_unknowns,
                  _time_to_target(cap_low, cap_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    if observed.markout_sign == "negative":
        p_low, p_high = 0.0, 0.0
        p_unknowns = (
            "the observed post-repair markout sign is negative: at this configuration the "
            "accrual is not economically productive, so the productive rate is zero here. §1.11 "
            "records that as a supported negative finding, not as a failure to complete",)
    elif observed.markout_sign is None:
        p_low, p_high = 0.0, o_low
        p_unknowns = (
            "the post-repair markout sign is not measured on this run, so the pessimistic edge "
            "is the lower edge of the observed band and nothing is claimed about its sign",)
    else:
        p_low, p_high = 0.0, o_low
        p_unknowns = (
            f"the observed post-repair markout sign is {observed.markout_sign}: the pessimistic "
            "edge keeps the accrual but takes the lower edge of the observed band, which is "
            "where a thinner clean book and a slower cadence would put it",)
    pessimistic = Scenario(
        name="pessimistic_markout", distinct_games=observed.distinct_games,
        sports=tuple(observed.sports), clean_book_eligible=observed.clean_book_eligible,
        maturity=maturity, low=p_low, high=p_high,
        unknowns=(*p_unknowns,
                  _time_to_target(p_low, p_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    ordered = {"baseline_continuation": baseline, "arm_b_measured": arm_b,
               "arm_b_plus_arm_c": arm_bc, "capacity_bound": capacity,
               "pessimistic_markout": pessimistic}
    return [ordered[name] for name in SCENARIOS]


#: The header every rendered forecast carries: what is projected, and what is refused.
HEADER: tuple[str, ...] = (
    f"Sample-accrual forecast (§1.10): {PROJECTED_VARIANT} alone.",
    "  No pooling with the primary; no partial-fill row counted as an order; no counterfactual "
    "fill counted as an observed fill; every counterfactual range carries its exploratory "
    "label.",
    "  Ranges and named unknowns only. No gate date is announced here: the gate date follows "
    "the user's dated decision (§0.14a) and 6F's dates (§0.14b).")


def _eligibility_cell(scenario: Scenario) -> str:
    if scenario.clean_book_eligible is None:
        return "clean-book-eligible filled orders: not measured"
    return f"clean-book-eligible filled orders: {scenario.clean_book_eligible}"


def render_forecast(scenarios: Sequence[Scenario]) -> str:
    """The scenarios as the decision report carries them: a range each, with its unknowns.

    Every number printed here is a rate or a count that came from a `Scenario`; this function
    computes nothing, so a reader comparing the report with the record reads one set of numbers.
    """
    lines: list[str] = list(HEADER)
    for scenario in scenarios:
        lines.append("")
        lines.append(f"{scenario.name}: {_rate_phrase(scenario.low, scenario.high)}")
        lines.append(f"    distinct filled games: {scenario.distinct_games}; "
                     f"sports: {', '.join(scenario.sports)}; "
                     f"{_eligibility_cell(scenario)}")
        lines.append(f"    {scenario.maturity}")
        for unknown in scenario.unknowns:
            lines.append(f"    unknown: {unknown}")
    return "\n".join(lines)
