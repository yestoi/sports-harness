"""6D.1 §1.8's pacing arithmetic: pure, dormant by default, and never a cap.

**What this module is for.** The shadow veto spends its $25 day before 09:00 CT every day
(§0's live facts: $24.40-$24.56 spent by 09:00, 3,149 decided of 178,618 queued in seven days,
**none** of them inside 5.7 h of kickoff), so the signals closest to kickoff - the ones a
post-hoc judgement is most informative about - are never reached. A pacing profile holds part of
the day's cap back for near-kickoff work and claims by kickoff proximity instead of by arrival
order. It changes **when** the cap binds. It never changes the cap: `veto_daily_usd_cap` ($25)
and `veto_weekly_usd_cap` ($150) are roadmap invariant 7 and U4, and nothing here reads or writes
them (§1.8, addendum invariant 7).

**Dormant by default, and that is the contract.** `Settings.veto_pacing_profile` is `None`
(T1's default), `load_profile(None)` is `None`, `reserved_floor(None, ...)` and
`weekly_floor(None, ...)` are `Decimal("0")` and `near_kickoff(None, ...)` is `False`, so
`reserve_spend`'s arithmetic and `_OLDEST_BUCKET`'s claim order are today's, byte for byte.
Activation is §0.14c's dated user decision and §0.8's amendment instant; nothing in this module
performs it.

**Why this lives in `harness/research/` and not in the experiment package.** §1.8's file list puts
the profile in `harness/experiments/execution_viability/`, but `harness/research/spend.py` is a
**production** module and §0.4 (with T1's `tests/test_exp_isolation.py`) forbids a production
module importing `harness/experiments/`. The pure arithmetic therefore lives here, in a leaf that
imports only the standard library and `harness.weeks`, so `spend.py` and `veto.py` can both use it
without a cycle and without crossing the isolation boundary. Everything that is genuinely
experiment-side - building a candidate profile, the preflight over stored arrivals, the §0.8
amendment record and §0.14c's question - stays in
`harness/experiments/execution_viability/veto_profile.py`.

**No SQLAlchemy import, deliberately.** This module decides money arithmetic inside the
reservation's advisory lock; it must not be able to issue a statement of its own.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from zoneinfo import ZoneInfo

from harness.weeks import CHICAGO, chicago_day

#: The reservation is money, and money is cents. Every floor is quantized before it is compared
#: with a cap so a repeating fraction can never make the comparison depend on the decimal context.
_CENT = Decimal("0.01")

#: ISO order, Monday first, so "the remaining days of this week" is a slice rather than a search.
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

__all__ = ["WEEKDAYS", "Window", "PacingProfile", "PROFILE_NAMES", "load_profile",
           "reserved_floor", "weekly_floor", "released", "near_kickoff"]


@dataclass(frozen=True, slots=True)
class Window:
    """One reservation bucket: a sport and how long before kickoff it starts (§1.8b).

    `reserved_fraction` is a fraction **of the day's cap**, not a dollar amount, so a re-fitted
    cap needs no profile change and a profile can never name a number larger than the cap.
    """

    sport: str
    hours_before_kickoff: int
    reserved_fraction: Decimal


@dataclass(frozen=True, slots=True)
class PacingProfile:
    """A frozen profile: the reservation table, the weekly allocation, the release rule and the
    claim order (§1.8b). Serialised and hashed so §0.8's amendment record can name exactly the
    one that was activated.
    """

    name: str
    windows: tuple[Window, ...]
    #: Weekday name (lower case, `WEEKDAYS`) -> fraction of the **weekly** cap held for that day.
    #: The NFL/NCAAF slate is Saturday, Sunday and Monday, which is what this exists to protect.
    weekday_allocation: dict[str, Decimal]
    release_hour_ct: int
    kickoff_first: bool

    def as_json(self) -> str:
        """The one serialisation the hash is taken over: sorted keys, no whitespace, `Decimal`
        as its own string. Byte-identical to `manifest.canonical_json` of the same object - which
        `tests/test_veto_pacing.py` asserts - without this leaf importing the experiment package.
        """
        return json.dumps({
            "name": self.name,
            "windows": [{"sport": w.sport,
                         "hours_before_kickoff": w.hours_before_kickoff,
                         "reserved_fraction": str(w.reserved_fraction)} for w in self.windows],
            "weekday_allocation": {k: str(v)
                                   for k, v in sorted(self.weekday_allocation.items())},
            "release_hour_ct": self.release_hour_ct,
            "kickoff_first": self.kickoff_first,
        }, sort_keys=True, separators=(",", ":"))

    def profile_hash(self) -> str:
        """The 64-character sha256 of `as_json()` - §0.8's \"profile hash\" field."""
        return hashlib.sha256(self.as_json().encode()).hexdigest()


#: The module's own frozen registry. A profile is a *named* object, so the setting carries a name
#: rather than a JSON blob and the same string in the amendment record, the preflight and the
#: running process cannot drift apart.
_PROFILES: dict[str, PacingProfile] = {
    # §1.8's own worked example, and the one §0.14c asks about: half of each day held for
    # signals inside 6 h of kickoff ($12.50/day at the unchanged $25 cap = 147 pairs at $0.085),
    # a quarter of the week held for Saturday and a quarter for Sunday, the unspent near-kickoff
    # reserve released at 21:00 CT, and the kickoff-first claim order.
    "near_kickoff_50": PacingProfile(
        name="near_kickoff_50",
        windows=(Window("nfl", 6, Decimal("0.50")), Window("ncaaf", 6, Decimal("0.50"))),
        weekday_allocation={"saturday": Decimal("0.25"), "sunday": Decimal("0.25")},
        release_hour_ct=21,
        kickoff_first=True),
}
PROFILES = MappingProxyType(_PROFILES)
PROFILE_NAMES = tuple(sorted(_PROFILES))


def load_profile(name: str | None) -> PacingProfile | None:
    """The named profile, or `None` - which is dormancy, today's behaviour exactly.

    A name that is not in the registry raises `KeyError`: a typo in `veto_pacing_profile` must
    fail the first reservation loudly rather than silently leave pacing off while the operator
    believes it is on.
    """
    if name is None:
        return None
    try:
        return _PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown veto pacing profile {name!r}; known profiles are "
                       f"{', '.join(PROFILE_NAMES)} (§1.8)") from None


def released(profile: PacingProfile, now: datetime) -> bool:
    """Has this America/Chicago day's unspent near-kickoff reserve returned to the general pool?

    §1.8(b)'s release rule: at `release_hour_ct` CT (21:00 by default) whatever the reserve did
    not need stops being held back, so a quiet Wednesday evening still spends its whole cap.
    """
    return now.astimezone(ZoneInfo(CHICAGO)).hour >= profile.release_hour_ct


def _reserved_fraction(profile: PacingProfile, sport: str | None) -> Decimal:
    """The fraction of the day held back, over the windows that apply.

    **The windows describe one shared pool, so they take a maximum, not a sum** (a stated
    deviation from the plan's prose, whose own worked example requires it): a profile that
    reserves 50 % for `nfl` inside 6 h and 50 % for `ncaaf` inside 6 h holds back **half a day**
    for near-kickoff work that either sport may draw on - §1.8's \"$12.50/day / $0.085 = 147
    pairs\" - not a whole day. Summing would also let two windows reserve more than the cap,
    making `daily_cap - floor` negative and refusing every call; the `min(..., 1)` below is the
    same guard for a single over-large fraction.
    """
    applicable = [w.reserved_fraction for w in profile.windows
                  if sport is None or w.sport == sport]
    if not applicable:
        return Decimal("0")
    return min(max(applicable), Decimal("1"))


def reserved_floor(profile: PacingProfile | None, now: datetime, daily_cap: Decimal,
                   *, near_kickoff: bool, sport: str | None = None) -> Decimal:
    """How much of `daily_cap` is held back from **this** caller (§1.8c).

    Zero, and therefore a no-op, in three cases: no profile (dormant, the default); a
    near-kickoff caller, which is who the reserve is *for*; and after the release hour, when the
    day's unspent reserve has returned to the general pool. Otherwise the profile's applicable
    fraction of the day's cap, in whole cents.

    The cap itself is never read or changed here: the caller compares `daily_cap - floor` and
    then `daily_cap`, so an active profile can only make the day's cap bind **earlier** for a
    far-from-kickoff call, never later, and never above $25.
    """
    if profile is None or near_kickoff:
        return Decimal("0")
    if released(profile, now):
        return Decimal("0")
    return (daily_cap * _reserved_fraction(profile, sport)).quantize(_CENT, ROUND_HALF_UP)


def weekly_floor(profile: PacingProfile | None, now: datetime,
                 weekly_cap: Decimal) -> Decimal:
    """How much of `weekly_cap` the **remaining** days of this ISO week have claimed (§1.8b).

    Strictly the days after `now`'s America/Chicago day: today's own allocation is what today is
    spending, so what must be held back from today is the future days' share. A busy Saturday
    therefore cannot consume an explicitly reserved Sunday allocation - $150 x 0.25 = $37.50 held
    for Sunday on Saturday, under §1.8's example profile.

    Arithmetic only, as §1.8's one reservation check is the daily one: `reserve_spend` does not
    yet consult this, and the preflight is where the weekly allocation is reported (see the task
    report's carry-forward).
    """
    if profile is None:
        return Decimal("0")
    today = WEEKDAYS[chicago_day(now).weekday()]
    remaining = WEEKDAYS[WEEKDAYS.index(today) + 1:]
    share = sum((profile.weekday_allocation.get(day, Decimal("0")) for day in remaining),
                Decimal("0"))
    return (weekly_cap * min(share, Decimal("1"))).quantize(_CENT, ROUND_HALF_UP)


def near_kickoff(profile: PacingProfile | None, now: datetime, kickoff: datetime | None,
                 *, sport: str | None = None) -> bool:
    """Is this signal inside one of the profile's kickoff windows, as of `now`?

    `False` while the profile is `None` (dormancy), for a signal whose game has no kickoff, and
    for a kickoff that has already **passed**: §1.8(a) protects the hours *before* kickoff, and a
    passed kickoff is the stale backlog the claim order deliberately puts last. `sport` is
    optional because `QueuedSignal` carries no sport; with no sport every window applies, which
    for §1.8's profile (`nfl` and `ncaaf`, both 6 h) is the same answer. The preflight, which
    reads `games.sport` beside every arrival, passes it.
    """
    if profile is None or kickoff is None:
        return False
    delta = kickoff - now
    if delta.total_seconds() < 0:
        return False
    return any(delta.total_seconds() <= w.hours_before_kickoff * 3600
               for w in profile.windows if sport is None or w.sport == sport)
