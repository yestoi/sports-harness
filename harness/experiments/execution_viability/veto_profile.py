"""§1.8(d) and §0.8: build a pacing profile, preflight it against **stored arrivals**, and render
the amendment record §0.14c's dated decision would activate.

Nothing here activates anything. The command this module backs never writes
`Settings.veto_pacing_profile`, never writes a `veto_decisions` row and never calls a model: the
preflight is arithmetic over rows the harness already recorded, and the amendment record is a
document with its boundary instant deliberately left blank.

**Opportunities, not outcomes** (§1.8d). A preflight counts the calls a profile *would have been
able to fund* on the arrivals that actually happened. It never reuses a historical model answer as
if the new profile had asked a different historical question, so no number here is a veto rate, a
decision or an economic result.

**The caps do not move.** `daily_cap` and `weekly_cap` are parameters so the preflight can be read
against the values in force ($25 and $150, roadmap invariant 7); the profile only decides how much
of them is held back for near-kickoff work and in what order the queue is claimed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from harness.experiments.execution_viability.manifest import canonical_json
from harness.research import pacing
from harness.research.pacing import PacingProfile, Window
from harness.weeks import chicago_day

#: §1.8(d)'s bound: a preflight reads a window of arrivals, and never the whole table.
DEFAULT_ROW_CAP = 200_000

#: Printed above every preflight table, because the number's meaning is the point.
PREFLIGHT_HEADER = ("coverage is reported as opportunities, not outcomes; no historical answer "
                    "is reused as if the profile had asked a different question (§1.8d); the "
                    "arrivals are walked in arrival order, so money spent earlier in a day is "
                    "not available to a later near-kickoff bucket")

#: §0.14c's question, verbatim. Rendered by `exp veto-profile`; answered by the user, and only by
#: the user, with a date.
AMENDMENT_QUESTION = (
    "Profile P (hash …) reserves X % of each day for signals inside 6 h of kickoff and Y for "
    "the weekly slate, releases unused reservations at 21:00 CT, and claims by kickoff proximity "
    "within each stratum. Do you activate it, and from what date?")

#: The claim order in force today, and the one the amendment would replace (§1.8b).
CLAIM_ORDER_BEFORE = "q.bucket_start, q.game_id nulls last, q.market_type"
CLAIM_ORDER_AFTER = ("(g.kickoff_utc is null or g.kickoff_utc <= :now), g.kickoff_utc - :now asc, "
                     "q.bucket_start, q.game_id nulls last, q.market_type")

#: The stored arrivals themselves: one row per queued signal in the window, with the game's sport
#: and kickoff. Bounded by the window and by `:row_cap`, and ordered the way `veto_queue` is
#: already read.
#:
#: `veto_decisions` is deliberately **not** joined. A preflight counts opportunities, and the
#: disposition a row actually received answers a question the profile did not ask (§1.8d);
#: reading it here would be the first step towards reusing a historical answer as if it had.
_ARRIVALS = text("""
    select q.game_id, q.market_type, q.bucket_start, q.signal_id, g.sport, g.kickoff_utc
      from veto_queue q
      left join games g on g.id = q.game_id
     where q.bucket_start >= :since and q.bucket_start < :until
     order by q.bucket_start, q.signal_id
     limit :row_cap
""")


def build_profile(name: str, *, near_kickoff_fraction: Decimal) -> PacingProfile:
    """§1.8(b)'s profile shape, parameterised by the one number §0.14c asks about.

    The sports are the slate the veto actually sees (`nfl`, `ncaaf`), the window is 6 h - the
    review's \"none inside 5.7 h of kickoff\" is what it is sized against - the weekly allocation
    holds a quarter of the week for Saturday and a quarter for Sunday, and the unspent reserve is
    released at 21:00 CT. `pacing.load_profile` is what a *running* process uses; this is how a
    candidate is constructed for the preflight and the amendment record.
    """
    return PacingProfile(
        name=name,
        windows=(Window("nfl", 6, near_kickoff_fraction),
                 Window("ncaaf", 6, near_kickoff_fraction)),
        weekday_allocation={"saturday": Decimal("0.25"), "sunday": Decimal("0.25")},
        release_hour_ct=21,
        kickoff_first=True)


def profile_json(profile: PacingProfile) -> str:
    """The profile's canonical serialisation, taken through the experiment's own
    `canonical_json` so the hash in the amendment record is demonstrably the manifest's form."""
    import json

    return canonical_json(json.loads(profile.as_json()))


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """What the profile would have been able to fund on the arrivals that happened.

    `funded_today` is the same walk with no profile - today's `bucket_start` order and no reserve
    - so the two columns are the comparison §0.14c needs, on one set of stored rows.
    """

    profile_name: str
    profile_hash: str
    since: datetime
    until: datetime
    row_cap: int
    arrivals: int
    buckets: int
    near_kickoff_buckets: int
    buckets_by_window: dict[str, int]
    funded: int
    funded_near_kickoff: int
    uncovered: int
    funded_today: int
    funded_near_kickoff_today: int
    day_totals: dict[date, Decimal]
    week_total: Decimal
    daily_cap: Decimal
    weekly_cap: Decimal
    cost_per_pair: Decimal
    truncated: bool
    header: str = PREFLIGHT_HEADER
    caveats: tuple[str, ...] = field(default_factory=lambda: (
        "one paired call per bucket at the worst-case cost, which is what the reservation takes",
        "a bucket's near-kickoff status is judged as of its own `bucket_start`, never later",
        "arrivals with no game row carry no kickoff and are never counted as near-kickoff",
        "the walk is chronological: the kickoff-first order decides only among buckets claimable "
        "at the same instant, and money spent earlier in the day is gone",
        "the live worker claims one bucket per sweep and carries a backlog this walk does not, "
        "so the ordering term's benefit is a lower bound here while the reserve is measured "
        "in full",
    ))


@dataclass(frozen=True, slots=True)
class _Bucket:
    """One `(game_id, market_type, bucket_start)` arrival group: one paired call's worth."""

    game_id: int | None
    market_type: str
    bucket_start: datetime
    sport: str | None
    kickoff_utc: datetime | None
    signals: int


def _buckets(rows) -> list[_Bucket]:
    grouped: dict[tuple, dict] = {}
    for row in rows:
        key = (row.game_id, row.market_type, row.bucket_start)
        entry = grouped.setdefault(key, {"sport": row.sport, "kickoff_utc": row.kickoff_utc,
                                         "signals": 0})
        entry["signals"] += 1
    return [_Bucket(game_id=key[0], market_type=key[1], bucket_start=key[2],
                    sport=value["sport"], kickoff_utc=value["kickoff_utc"],
                    signals=value["signals"])
            for key, value in grouped.items()]


def _claim_order(bucket: _Bucket, profile: PacingProfile | None):
    """The order the claim statement implements, as a sort key over already-read rows.

    With no profile: `bucket_start` then the addendum's tie-breaks - today's `_OLDEST_BUCKET`.
    With one: future kickoffs first, nearest first, then the same tie-breaks. The `:now` of the
    live statement is the bucket's own `bucket_start` here, because that is the instant the call
    would have been made at.
    """
    game_key = (bucket.game_id is None, bucket.game_id or 0)
    if profile is None:
        return (0, 0, bucket.bucket_start, game_key, bucket.market_type)
    kickoff = bucket.kickoff_utc
    passed = kickoff is None or kickoff <= bucket.bucket_start
    to_kickoff = 0.0 if kickoff is None else (kickoff - bucket.bucket_start).total_seconds()
    return (int(passed), to_kickoff, bucket.bucket_start, game_key, bucket.market_type)


def _simulate(buckets: list[_Bucket], profile: PacingProfile | None, *,
              cost_per_pair: Decimal, daily_cap: Decimal,
              weekly_cap: Decimal) -> tuple[int, int, dict[date, Decimal], Decimal]:
    """Walk the arrivals **chronologically** and spend until a cap or a floor binds.

    Returns `(funded, funded_near_kickoff, day_totals, week_total)`. Every refusal here is the
    one `reserve_spend` would make: `day_total + cost > daily_cap - reserved_floor`, then
    `day_total + cost > daily_cap`, then the week against `weekly_cap` less the remaining days'
    allocation. Nothing is invented: a bucket that could not be funded is simply not counted.

    **Time moves forward, exactly as it does live** (review fix, Important 1). The walk steps
    through the distinct `bucket_start` instants in order, adds the buckets that have arrived by
    each instant to a ready set, and claims from that ready set in `_claim_order` - so the
    profile's kickoff-first order decides only among buckets claimable **at the same instant**,
    which is all the live claim statement can do. Sorting the whole window by kickoff proximity
    and then spending would let a 19:00 near-kickoff arrival take money an 08:00 arrival had
    already spent, roughly doubling the reported near-kickoff coverage - the one number
    §1.8's expected result and the §0.14c decision turn on. The same walk produces the
    `profile=None` comparison column, so both columns are made under one set of assumptions.

    What the chronology leaves out is stated in the report's caveats: the live worker claims one
    bucket per sweep, so a real queue carries a backlog this walk does not (it offers every
    arrival a claim at the instant it arrives). That makes the ordering term's benefit a **lower**
    bound here; the reserve - which is the mechanism §1.8 rests on, and which this walk applies
    at each claim instant - is measured in full.
    """
    day_totals: dict[date, Decimal] = {}
    week_total = Decimal("0")
    funded = funded_near = 0
    arrivals: dict[datetime, list[_Bucket]] = {}
    for bucket in buckets:
        arrivals.setdefault(bucket.bucket_start, []).append(bucket)
    ready: list[_Bucket] = []
    for instant in sorted(arrivals):
        ready.extend(arrivals[instant])
        ready.sort(key=lambda b: _claim_order(b, profile))
        claimable, ready = ready, []
        for bucket in claimable:
            day = chicago_day(instant)
            near = pacing.near_kickoff(profile, instant, bucket.kickoff_utc, sport=bucket.sport)
            floor = pacing.reserved_floor(profile, instant, daily_cap, near_kickoff=near,
                                          sport=bucket.sport)
            day_total = day_totals.get(day, Decimal("0"))
            if floor and day_total + cost_per_pair > daily_cap - floor:
                continue
            if day_total + cost_per_pair > daily_cap:
                continue
            weekly_reserved = pacing.weekly_floor(profile, instant, weekly_cap)
            if week_total + cost_per_pair > weekly_cap - weekly_reserved:
                continue
            day_totals[day] = day_total + cost_per_pair
            week_total += cost_per_pair
            funded += 1
            funded_near += int(near)
    return funded, funded_near, day_totals, week_total


def preflight(session, profile: PacingProfile, *, since: datetime, until: datetime,
              now: datetime, cost_per_pair: Decimal, daily_cap: Decimal, weekly_cap: Decimal,
              row_cap: int = DEFAULT_ROW_CAP) -> PreflightReport:
    """Replay `veto_queue`'s stored arrivals over `[since, until)` against `profile` (§1.8d).

    Read-only: one bounded select, no write of any kind, and no model call. `now` is carried into
    the report so the window a reader sees is the window that was asked about; the funding walk
    uses each bucket's own `bucket_start`, which is the instant its call would have been made at.
    """
    rows = session.execute(_ARRIVALS, {"since": since, "until": until,
                                       "row_cap": row_cap}).all()
    buckets = _buckets(rows)
    by_window: dict[str, int] = {}
    near_kickoff_buckets = 0
    for bucket in buckets:
        near = pacing.near_kickoff(profile, bucket.bucket_start, bucket.kickoff_utc,
                                   sport=bucket.sport)
        near_kickoff_buckets += int(near)
        label = f"{bucket.sport or 'unknown'}:{'inside' if near else 'outside'}"
        by_window[label] = by_window.get(label, 0) + 1
    funded, funded_near, day_totals, week_total = _simulate(
        buckets, profile, cost_per_pair=cost_per_pair, daily_cap=daily_cap,
        weekly_cap=weekly_cap)
    today_funded, today_near, _, _ = _simulate(
        buckets, None, cost_per_pair=cost_per_pair, daily_cap=daily_cap, weekly_cap=weekly_cap)
    return PreflightReport(
        profile_name=profile.name, profile_hash=profile.profile_hash(), since=since, until=until,
        row_cap=row_cap, arrivals=len(rows), buckets=len(buckets),
        near_kickoff_buckets=near_kickoff_buckets, buckets_by_window=by_window,
        funded=funded, funded_near_kickoff=funded_near, uncovered=len(buckets) - funded,
        funded_today=today_funded, funded_near_kickoff_today=today_near,
        day_totals=day_totals, week_total=week_total, daily_cap=daily_cap,
        weekly_cap=weekly_cap, cost_per_pair=cost_per_pair, truncated=len(rows) >= row_cap)


def render_preflight(report: PreflightReport) -> str:
    """The preflight as the command prints it: opportunities, with the caveats attached."""
    lines = [f"preflight {report.profile_name} hash={report.profile_hash[:12]}",
             f"  {report.header}",
             f"  window            {report.since.isoformat()} .. {report.until.isoformat()}",
             f"  arrivals          {report.arrivals}"
             f"{'  (row cap reached)' if report.truncated else ''}",
             f"  buckets           {report.buckets}",
             f"  inside a window   {report.near_kickoff_buckets}",
             f"  cost per pair     {report.cost_per_pair}",
             f"  caps              daily {report.daily_cap} weekly {report.weekly_cap} "
             f"(unchanged)",
             f"  funded (profile)  {report.funded} of which near-kickoff "
             f"{report.funded_near_kickoff}",
             f"  funded (today)    {report.funded_today} of which near-kickoff "
             f"{report.funded_near_kickoff_today}",
             f"  not covered       {report.uncovered}",
             f"  week total        {report.week_total}"]
    for label in sorted(report.buckets_by_window):
        lines.append(f"    {label:<24} {report.buckets_by_window[label]}")
    for day in sorted(report.day_totals):
        lines.append(f"    {day.isoformat():<24} {report.day_totals[day]}")
    for caveat in report.caveats:
        lines.append(f"  caveat: {caveat}")
    return "\n".join(lines)


def amendment_record(profile: PacingProfile, *, prepared_at: datetime,
                     code_sha: str | None = None) -> str:
    """§0.8's amendment record, prepared and **unactivated**.

    Every field §0.8 names is here except one: the activation instant, which is the user's dated
    decision (§0.14c) and is written at activation, not here. The amendment id is derived from the
    profile hash so the same profile always names the same amendment.
    """
    from harness.execution import EXECUTOR_VERSION

    digest = profile.profile_hash()
    fraction = max((w.reserved_fraction for w in profile.windows), default=Decimal("0"))
    weekly = ", ".join(f"{day} {profile.weekday_allocation[day]}"
                       for day in sorted(profile.weekday_allocation)) or "none"
    return "\n".join([
        f"amendment_id:            veto-pacing-{digest[:12]}",
        f"prepared_at:             {prepared_at.isoformat()}",
        f"profile_name:            {profile.name}",
        f"profile_hash:            {digest}",
        f"profile_json:            {profile_json(profile)}",
        f"reserved_fraction:       {fraction} of the day, for signals inside "
        f"{max((w.hours_before_kickoff for w in profile.windows), default=0)} h of kickoff",
        f"weekly_allocation:       {weekly} (of the weekly cap)",
        f"release_rule:            unspent reserve released at {profile.release_hour_ct}:00 "
        f"America/Chicago",
        f"research_build:          EXECUTOR_VERSION={EXECUTOR_VERSION}",
        f"code_sha:                {code_sha or '<the release sha at activation>'}",
        "activation_instant_utc:  <written at activation by the user's dated decision (§0.14c)>",
        "activation_instant_ct:   <written at activation by the user's dated decision (§0.14c)>",
        "population_before:       every decided signal, claimed oldest-bucket-first "
        f"(order by {CLAIM_ORDER_BEFORE})",
        "population_after:        every decided signal, claimed nearest-future-kickoff-first "
        f"(order by {CLAIM_ORDER_AFTER})",
        "h9_definition:           unchanged - H9's decided population is still every decided "
        "signal; its time-to-kickoff mix changes from the amendment instant, and no pre/post "
        "veto value is compared without this boundary printed beside it",
        "labels:                  unchanged - `veto_skipped_budget` stays the label for a "
        "call the budget refused; the one new reason code is `daily_reserved`, which cannot "
        "appear before the activation instant",
        "caps:                    unchanged - $25 a day and $150 an ISO week, enforced by the "
        "same atomic reservation; the profile changes only when the cap binds",
        "registered_ids:          unchanged - no registered id, cap or model provider changes",
    ])
