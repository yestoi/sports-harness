"""§1.10: the sample-accrual forecast for `sharp_two_sided`, and nothing else.

What this module refuses to do is the point of it:

* **No pooling.** Only `sharp_two_sided` is projected. The primary's accrual is a different
  portfolio identity, and `report.sum_rows` already refuses to add two of them together (§1.5);
  this module refuses to *project* a second one at all.
* **No partial-fill row counted as an order.** `Observed.fills` is a count of **filled orders**;
  `Observed.fill_rows` is beside it so the distinction is visible, never so it can be summed.
  Three partial fills on one order are one order, and the 150-order target is a count of orders.
* **No counterfactual fill counted as an observed fill.** The three quantities §1.10 names stay
  apart in the types themselves: `Observed` holds the **watched** facts, `Exploratory` holds
  this milestone's **arm results** (stateful, labelled, counterfactual), and `Scenario` holds a
  **conditional** projection built from them. Every scenario's `clean_book_eligible` and
  `distinct_games` are observed counts, whatever the scenario's range was computed from.
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

#: §1.10: both sports, never one of them.
SPORTS: tuple[str, ...] = ("nfl", "ncaaf")

#: The one portfolio §1.10 projects. `project` refuses any other rather than relabelling.
PROJECTED_VARIANT: str = "sharp_two_sided"

#: §5.3's caveat is bounded by the executor's **shared** order slots (D10: shared across the
#: executed variants), read from the settings model rather than re-typed, so a cap change moves
#: the ceiling with it.
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


@dataclass(frozen=True, slots=True)
class Observed:
    """§1.10's first quantity: **observed watched fills**, live facts only.

    `fills` counts filled **orders**; `fill_rows` counts the fill rows those orders took, and
    exists so a reader can see that three partial fills are one order rather than three.
    `clean_book_eligible` defaults to `fills` - the filled orders whose book the health
    classification could not fault - and is carried onto every scenario unchanged.
    """

    orders: int = 0
    fills: int = 0
    fill_rows: int = 0
    distinct_games: int = 0
    days: float = 1.0
    sports: tuple[str, ...] = SPORTS
    clean_book_eligible: int | None = None
    #: Measured turnovers per shared slot per day. `None` means it was not measured, and the
    #: capacity ceiling is then **not computed** rather than guessed.
    turnover_per_day: float | None = None
    mature_outcomes: int = 0
    markout_sign: str | None = None

    def clean(self) -> int:
        return self.fills if self.clean_book_eligible is None else self.clean_book_eligible


@dataclass(frozen=True, slots=True)
class Exploratory:
    """§1.10's second quantity: this milestone's **arm results** - stateful and counterfactual.

    `fills` is arm B's counterfactual filled-order count. It is labelled everywhere it is
    printed and is never added to, or substituted for, `Observed.fills`. `arm_c` carries arm C's
    measured freshness effect or the explicit reason it could not be measured; `arm_c_multiplier`
    is the only way a C effect enters a range, and it is `None` unless C actually ran.
    """

    arm_id: str = "B"
    fills: int = 0
    orders: int = 0
    days: float = 1.0
    label: str = ""
    arm_c: str = ARM_C_UNMEASURED
    arm_c_multiplier: float | None = None


@dataclass(frozen=True, slots=True)
class Scenario:
    """One conditional projection: a **range** of filled orders a day, with its unknowns named.

    `low`/`high` are filled orders a day for `sharp_two_sided`; `distinct_games` and
    `clean_book_eligible` are the run's **observed** counts, whatever the range was computed
    from; `maturity` states what the outcome record can and cannot answer; and `unknowns` is
    never empty - a scenario with nothing unknown about it is not a scenario.
    """

    name: str
    distinct_games: int
    sports: tuple[str, ...]
    clean_book_eligible: int
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


def _rate_phrase(low: float, high: float) -> str:
    return f"{low:.3f} - {high:.3f} filled orders a day"


def _time_to_target(low: float, high: float, games_per_day: float, *, target_orders: int,
                    target_games: int, identified: bool) -> str:
    """The §1.10 headline, in **elapsed days** and never as a calendar date."""
    head = f"time to {target_orders} filled orders across {target_games} distinct games"
    if not identified:
        return (f"{head}: not projected while accrual or economics is unidentified; §1.10 "
                "publishes scenarios and unknowns rather than a gate date")
    if high <= 0:
        return (f"{head}: not projected - the upper edge of this scenario's range is zero "
                "filled orders a day, so no elapsed time follows from it")
    fastest = target_orders / high
    slowest = None if low <= 0 else target_orders / low
    games = None if games_per_day <= 0 else target_games / games_per_day
    tail = (f"{fastest:.0f} elapsed days at the upper edge" if slowest is None
            else f"{fastest:.0f} - {slowest:.0f} elapsed days")
    if slowest is None:
        tail += " and unbounded at the lower edge, which is zero"
    if games is not None:
        tail += f", against {games:.0f} elapsed days on the distinct-game count"
    else:
        tail += ", with no distinct-game rate to compare it against"
    return (f"{head}: {tail}, whichever binds later; no calendar date is named here - the gate "
            "date follows the user's dated decision (§0.14a) and 6F's dates (§0.14b)")


def _maturity(observed: Observed) -> str:
    if observed.mature_outcomes <= 0:
        return ("outcome maturity: unavailable - no mature markout outcome for these fills; "
                "nothing has recorded this run's outcomes yet, and an absent outcome is not a "
                "zero one (§1.9a)")
    sign = observed.markout_sign or "unmeasured"
    return (f"outcome maturity: {observed.mature_outcomes} mature markout outcomes, "
            f"post-repair sign {sign}")


def project(observed: Observed, exploratory: Exploratory, *, target_orders: int = TARGET_ORDERS,
            target_games: int = TARGET_GAMES,
            variant: str = PROJECTED_VARIANT) -> list[Scenario]:
    """The five scenarios of `SCENARIOS`, in that order, for `sharp_two_sided` alone.

    Every scenario is built from the same observed base; only the **range** differs, and each
    range says in its unknowns which quantity it rests on. Nothing here sums an observed count
    and a counterfactual one, and nothing invents a number the record does not carry.
    """
    if variant != PROJECTED_VARIANT:
        raise ValueError(
            f"§1.10 projects {PROJECTED_VARIANT} alone: refusing to project {variant!r}, which "
            "is a different portfolio identity and would be pooling, not forecasting")
    if observed.days <= 0:
        raise ValueError("observed.days must be positive: an accrual rate needs an elapsed "
                         "window to be a rate of")
    if observed.markout_sign is not None and observed.markout_sign not in MARKOUT_SIGNS:
        raise ValueError(f"markout_sign must be one of {MARKOUT_SIGNS} or None (unmeasured), "
                         f"not {observed.markout_sign!r}")

    clean = observed.clean()
    maturity = _maturity(observed)
    accrual_ok = observed.distinct_games >= IDENTIFIED_MIN_GAMES
    economics_ok = observed.mature_outcomes > 0 and observed.markout_sign is not None
    identified = accrual_ok and economics_ok
    games_per_day = observed.distinct_games / observed.days

    common: list[str] = []
    if not accrual_ok:
        common.append(f"{ACCRUAL_UNIDENTIFIED} ({observed.distinct_games} in "
                      f"{observed.days:g} elapsed days)")
    if not economics_ok:
        common.append(ECONOMICS_UNIDENTIFIED)
    common.append("the interval is a Poisson count band on the fills the record holds, not a "
                  "fitted model; it widens with nothing but the count")
    common.append("770 independent counterfactual tracks are never turned into a portfolio "
                  "forecast: this range is one portfolio identity's (§1.5, §1.10)")

    provenance = (
        f"observed base: orders={observed.orders} placed, {observed.fill_rows} partial-fill "
        f"rows on {observed.fills} filled orders (a partial-fill row is never an order)",
        f"observed fills: {observed.fills} (watched, live facts)",
        f"exploratory (arm B, counterfactual): {exploratory.fills} filled orders"
        f"{' ' + exploratory.label if exploratory.label else ''}, labelled and never added to "
        "the observed count")

    o_low, o_high = _band(observed.fills, observed.days)
    b_low, b_high = _band(exploratory.fills, exploratory.days or observed.days)

    baseline = Scenario(
        name="baseline_continuation", distinct_games=observed.distinct_games,
        sports=observed.sports, clean_book_eligible=clean, maturity=maturity,
        low=o_low, high=o_high,
        unknowns=(*provenance,
                  "today's posture continued unchanged: the observed watched fills carried "
                  "forward at the rate they arrived",
                  _time_to_target(o_low, o_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    arm_b = Scenario(
        name="arm_b_measured", distinct_games=observed.distinct_games, sports=observed.sports,
        clean_book_eligible=clean, maturity=maturity, low=b_low, high=b_high,
        unknowns=(f"exploratory (arm B, counterfactual): {exploratory.fills} filled orders over "
                  f"{exploratory.days:g} elapsed days; arm B's measured admission and fill rate "
                  "carried forward at the observed distinct-game rate",
                  f"observed fills: {observed.fills} - the observed count is unchanged by this "
                  "scenario; only the projected rate is arm B's",
                  "arm B's fills are counterfactual: they were never resting in the venue's "
                  "book and no other participant traded against them",
                  _time_to_target(b_low, b_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    if exploratory.arm_c_multiplier is None:
        c_low, c_high = b_low, b_high
        c_unknowns = (f"arm C: {exploratory.arm_c}",
                      "arm C contributes no freshness effect to this range: an arm reported "
                      "unavailable with its reason is a complete input (§9), and zero "
                      "faster-history observations are invented for it")
    else:
        multiplier = float(exploratory.arm_c_multiplier)
        c_low, c_high = b_low * multiplier, b_high * multiplier
        c_unknowns = (f"arm C: {exploratory.arm_c}",
                      f"arm C's measured freshness effect applied as x{multiplier:g} on arm B's "
                      "range; the effect was measured on observations, not on fills, so it is "
                      "an assumption about admission and not a measured fill rate")
    arm_bc = Scenario(
        name="arm_b_plus_arm_c", distinct_games=observed.distinct_games, sports=observed.sports,
        clean_book_eligible=clean, maturity=maturity, low=c_low, high=c_high,
        unknowns=(*c_unknowns,
                  _time_to_target(c_low, c_high, games_per_day, target_orders=target_orders,
                                  target_games=target_games, identified=identified),
                  *common))

    if observed.turnover_per_day is None:
        cap_low, cap_high = b_low, b_high
        cap_unknowns = (
            f"the {SHARED_SLOTS} shared slots saturate under a resting policy, but this run "
            "supplies no measured turnover per slot per day, so the slots x turnover ceiling is "
            "not computed here (§5.3's caveat); the range shown is arm B's, with no ceiling",)
    else:
        ceiling = SHARED_SLOTS * float(observed.turnover_per_day)
        cap_low, cap_high = min(b_high, ceiling), ceiling
        cap_unknowns = (
            f"bounded by {SHARED_SLOTS} shared slots x measured turnover "
            f"{observed.turnover_per_day:g} a day = {ceiling:g} filled orders a day; the honest "
            "post-change fill count is never above it (§5.3's caveat)",
            f"the {SHARED_SLOTS} shared slots are shared across the executed variants (D10), so "
            "this ceiling is not sharp_two_sided's alone and the scenario is an upper bound")
    capacity = Scenario(
        name="capacity_bound", distinct_games=observed.distinct_games, sports=observed.sports,
        clean_book_eligible=clean, maturity=maturity, low=cap_low, high=cap_high,
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
        sports=observed.sports, clean_book_eligible=clean, maturity=maturity,
        low=p_low, high=p_high,
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
    "fill counted as an observed fill.",
    "  Ranges and named unknowns only. No gate date is announced here: the gate date follows "
    "the user's dated decision (§0.14a) and 6F's dates (§0.14b).")


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
                     f"clean-book-eligible filled orders: {scenario.clean_book_eligible}")
        lines.append(f"    {scenario.maturity}")
        for unknown in scenario.unknowns:
            lines.append(f"    unknown: {unknown}")
    return "\n".join(lines)
