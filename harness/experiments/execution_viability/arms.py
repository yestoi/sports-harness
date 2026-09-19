"""§1.6: the arms, and the cadence-derived allowance arm B decides under.

Three arms, no search of the six-policy grid: **A** reproduces today's posture, **B** replaces
the executor's pricing-time fair-age allowance with the interval that was actually scheduled
plus the recorder's tick budget, and **C** -- built by T7, with its own preflight -- adds
prospective observations without widening any allowance. `ARMS` holds A and B.

What B is, exactly (§1.6a, ruling I2): `plan._fair_stale` computes
`max(cfg["stale_s"], market.stale_allowance_s)` and, when `policy.cadence_allowance` is set,
**replaces** the second term with this module's number. The pricing-time value is a fixed 120 s
plus the tick budget whatever the schedule was doing; the whole point of B is that the
executor's own freshness test may use the interval that was actually in force. B is therefore
*tighter* than A in the NFL burst regime (180 s against 220 s, where the variant's floor wins),
identical in the sport-wide game window, and wider only outside a window -- which is where the
fill starvation sits (ruling M3).

This module names no other consumer of the allowance: §1.6(c)'s four distinctions are recorded
in `ArmSpec.notes`, hashed into the manifest, and asserted against this file's own source in
`tests/test_exp_arms.py`.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta

from harness.execution.plan import MarketNow
from harness.execution.policy import BASELINE, HoldingPolicy
from harness.experiments.execution_viability.manifest import canonical_json
from harness.recorder.cadence import interval_for

#: `(MarketNow) -> int`: the allowance in whole seconds for the fair row that market carries.
#: `policy.py` annotates its one field `Callable[..., int] | None` (plan choice 9) because a
#: production module may not import this package; this is the narrower alias B is built against.
#: The import runs experiment -> production, which is the direction §0.9 allows.
CadenceAllowance = Callable[[MarketNow], int]

#: I4's fallback: an overnight row with no finite anchor inside the capture window takes the
#: weekday off-window value and is **labelled**, so `exp_result` can count how much of B's
#: overnight behaviour rests on it.
OVERNIGHT_UNANCHORED_S = 1000
OVERNIGHT_UNANCHORED = "overnight_unanchored"
#: The two labels a resolved row can carry instead: the schedule answered at `fair_ts` itself,
#: or it answered at an earlier step the walk-back reached.
SCHEDULED = "scheduled"
WALKED_BACK = "walked_back"


class AllowanceNotBound(RuntimeError):
    """`ARMS["B"]` was stepped without its run's allowance bound to it.

    B's number needs three things this module cannot know at import time: the run's as-of
    kickoff reconstruction, the deployment's tick budget and the capture window. The spec
    therefore carries an unbound placeholder, and `adapter.ArmRunner(cadence_allowance=...)`
    rebinds it once per run -- so a run that forgot fails closed here instead of silently
    deciding under a number nobody chose.
    """


def _unbound(market) -> int:
    raise AllowanceNotBound(
        "arm B's cadence allowance is not bound to this run: pass "
        "`cadence_allowance=arms.cadence_allowance_for(...)` to `adapter.ArmRunner` (§1.6a)")


def _resolver(sport: str, kickoffs_at, tz, *, tick_budget_s: int, exec_period_s: int,
              window_start: datetime, interval_fn=interval_for):
    """The one body `cadence_allowance_for` and `cadence_allowance_with_label` share.

    Memoised on `fair_ts`, which is the only input that varies: `_fair_stale` asks this
    question for every resting order at every retained instant, and the real binding reads the
    as-of kickoff snapshots from the database once per probe. Without the cache an overnight
    row walks the whole capture window back on *every* evaluation; with it, each distinct fair
    row is resolved once per run. The cache is bounded by the run's distinct `fair_ts` values
    and dies with the closure.
    """
    cache: dict = {}

    def resolve(market) -> tuple[int, str]:
        fair_ts = market.fair_ts
        cached = cache.get(fair_ts)
        if cached is not None:
            return cached
        interval = interval_fn(sport, fair_ts, kickoffs_at(fair_ts), tz)
        label = SCHEDULED
        if interval is None:
            # I4's closed form, walking back in `exec_period_s` steps inside the capture window.
            # The walk starts one step **before** `fair_ts`: that instant was just asked and
            # answered `None`, and asking it again is a duplicate reconstruction, not a probe.
            label = WALKED_BACK
            probe = fair_ts - timedelta(seconds=exec_period_s)
            while probe >= window_start:
                interval = interval_fn(sport, probe, kickoffs_at(probe), tz)
                if interval is not None:
                    break
                probe -= timedelta(seconds=exec_period_s)
        answer = ((OVERNIGHT_UNANCHORED_S, OVERNIGHT_UNANCHORED) if interval is None
                  else (int(interval) + int(tick_budget_s), label))
        cache[fair_ts] = answer
        return answer

    return resolve


def cadence_allowance_for(sport: str, kickoffs_at, tz, *, tick_budget_s: int, exec_period_s: int,
                          window_start: datetime, interval_fn=interval_for) -> CadenceAllowance:
    """`kickoffs_at(at)` is `capture.kickoffs_asof` bound to this run: it returns the `Kickoff`
    rows (feeds/espn.py:19) reconstructed **as of** `at`, which is what `interval_for` takes.
    `interval_fn` is injected only so the regime table can be exercised without a feed.

    The callable is evaluated at the market's `fair_ts` -- the fair row's **creation** instant
    (§1.6b) -- so entering a game window cannot retroactively shorten an already-priced row, and
    a fetch that did not happen never extends its own deadline: the number is a function of the
    *scheduled* interval at `fair_ts`, never of the gap to the next actual row (§1.6d).
    """
    resolve = _resolver(sport, kickoffs_at, tz, tick_budget_s=tick_budget_s,
                        exec_period_s=exec_period_s, window_start=window_start,
                        interval_fn=interval_fn)

    def allowance(market) -> int:
        return resolve(market)[0]

    return allowance


def cadence_allowance_with_label(sport: str, kickoffs_at, tz, *, tick_budget_s: int,
                                 exec_period_s: int, window_start: datetime,
                                 interval_fn=interval_for):
    """`(allowance, label)`: the same number, plus how it was reached (I4's labelled fallback).

    The caller records the label on the run's rows; `_fair_stale` reads the number alone. Both
    views close over **one** memoised resolver, so asking for the label costs nothing beyond
    the number the same fair row already resolved.
    """
    resolve = _resolver(sport, kickoffs_at, tz, tick_budget_s=tick_budget_s,
                        exec_period_s=exec_period_s, window_start=window_start,
                        interval_fn=interval_fn)
    return (lambda market: resolve(market)[0]), (lambda market: resolve(market)[1])


#: §1.6(c)'s four distinctions, recorded on the spec and hashed into the manifest. They are
#: statements about what B does **not** touch: the strategy's own source-quote-age filter
#: (`harness/strategy/run.py`'s `not_stale`), repricing (`exec_reprice_fair_move_pts`), the
#: edge-decay cancel and the venue-move check (`exec_cancel_venue_move_pts`).
B_NOTES: tuple[str, ...] = (
    "overrides the executor's calculated-fair age allowance and nothing else; it is not "
    "rest_to_expiry, which bypasses the cancel rules entirely (§1.6c, distinction 1)",
    "does not widen the strategy's own source-quote-age filter (strategy/run.py's not_stale) "
    "(§1.6c, distinction 2)",
    "does not suppress repricing (exec_reprice_fair_move_pts) or the edge-decay cancel "
    "(§1.6c, distinction 3)",
    "does not change the venue-move check (exec_cancel_venue_move_pts) (§1.6c, distinction 4)",
)

A_NOTES: tuple[str, ...] = (
    "today's rule verbatim: now - fair_ts > max(cfg[\"stale_s\"], market.stale_allowance_s), "
    "which is 220 s on a featured order (§1.6's arm table)",
    "the registered configuration, reproduced: BASELINE has every alternative parameter off "
    "and every `if policy.<x>` branch in plan.py is dead under it",
)

#: The two observation sources §1.6 names. C's is the third and is T7's.
RECORDED = "recorded"
RECORDED_PLUS_PROSPECTIVE = "recorded_plus_prospective"


@dataclass(frozen=True, slots=True)
class ArmSpec:
    """One arm, frozen: what it decides under and what that is **not** (§1.6).

    The spec is hashed into the manifest, so a note nobody may quietly drop is a note inside
    the hash rather than beside it.
    """

    arm_id: str
    label: str
    policy: HoldingPolicy
    observation_source: str
    notes: tuple[str, ...]

    def as_manifest_entry(self) -> dict:
        """What `Manifest.arms` holds (plan choice 3): a canonical dict, not this object.

        `policy` is projected by iterating `dataclasses.fields(HoldingPolicy)` rather than by
        a hand-written list: a field added to the policy later is then inside the hash by
        construction instead of being silently absent from it. A callable field is rendered as
        the fact that there **is** one -- the bound allowance is a run-time object and hashing
        its identity would make the manifest of two identical runs differ.
        """
        policy = {}
        for spec_field in fields(self.policy):
            value = getattr(self.policy, spec_field.name)
            policy[spec_field.name] = bool(value) if callable(value) else value
        return {
            "arm_id": self.arm_id,
            "label": self.label,
            "observation_source": self.observation_source,
            "notes": list(self.notes),
            "policy": policy,
        }

    def spec_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.as_manifest_entry()).encode()).hexdigest()


#: §1.6's arms A and B. C is built only by T7, with its own preflight and its own cohort, and
#: is deliberately absent here: nothing in this milestone may step a prospective arm over tape.
ARMS: dict[str, ArmSpec] = {
    "A": ArmSpec(arm_id="A", label="baseline", policy=BASELINE,
                 observation_source=RECORDED, notes=A_NOTES),
    "B": ArmSpec(arm_id="B", label="cadence_allowance",
                 policy=HoldingPolicy(name="cadence_allowance", cadence_allowance=_unbound),
                 observation_source=RECORDED, notes=B_NOTES),
}


def bind(spec: ArmSpec, allowance: CadenceAllowance) -> ArmSpec:
    """`spec` with this run's allowance bound to its policy (§1.6a, M2).

    Only an arm whose policy already takes an allowance can be bound: binding one onto
    `BASELINE` would build a policy no arm of this experiment declares.
    """
    if spec.policy.cadence_allowance is None:
        raise ValueError(
            f"arm {spec.arm_id!r} decides under a policy that takes no cadence allowance; "
            "only §1.6's arm B does (§1.6a)")
    return replace(spec, policy=replace(spec.policy, cadence_allowance=allowance))
