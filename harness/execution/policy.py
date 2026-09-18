"""The holding and capacity policy: the baseline written down, the alternatives compared.

Addendum §1.6 and §0.9. 6D **declares** the policy in force and builds the harness that compares
it with the six alternatives decision 4 names. It adopts nothing: which policy governs the
prospective period is the user's dated decision (§0.15a), and the selected one is registered and
versioned as a new hashed `config_history` record or a new variant id by dated amendment before
any prospective period -- never as an edit to a registered id.

**Every parameter defaults to the baseline**, so `plan.py`'s live path is bit-identical when
nothing is passed: each policy branch there reads `if policy.<x>` and `BASELINE` has every one
of them off.

**The comparison run waits for 6B** and is a separate operate duty: stepping a replay at the
recorded loop instants is 6B's carve-out (its D15 -- a 15 s grid against a live loop that ran 27
loops in an hour is a different number of observation opportunities). What lives here is the
policy shape, the comparison itself and its tests on fixtures.

`plan.py` imports this module, so nothing here may import `plan` -- or `store`, which imports it
-- at module scope: `compare` takes both inside the function body, where the cycle cannot form.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.resources import files

import yaml
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.ops.exclusions import EXCLUSION_CLASSES, exclusion_totals

log = logging.getLogger("harness.policy")

#: M6. Printed as the comparison table's caption and on every row.
COUNTERFACTUAL_LABEL = (
    "counterfactual, exploratory: produced under a non-registered parameter set and never a "
    "registered variant's performance. Not a gate input, not a benchmark, not a variant record.")


@dataclass(frozen=True)
class HoldingPolicy:
    """The six parameters the alternatives vary. Every default is the baseline's value, which is
    what makes `HoldingPolicy()` and the live path the same behaviour."""

    name: str = "baseline"
    #: Seconds a fair value may be past its own staleness rule and still be tradeable. None is
    #: the baseline: the rule is `now - fair_ts > max(variant.stale_s, gap.stale_allowance_s)`.
    stale_allowance_s: int | None = None
    #: Hold a resting order to its expiry instead of cancelling it on `fair_stale`.
    rest_to_expiry: bool = False
    #: A fixed per-variant slot count in place of the shared `exec_max_open_orders` pool.
    per_variant_slots: int | None = None
    #: Admit an intent only where the book suggests it could actually fill.
    fillability_admission: bool = False
    #: Join the best bid rather than pricing from fair.
    join_the_bid: bool = False
    #: Place only inside this many minutes of kickoff.
    near_kickoff_only_min: int | None = None


BASELINE = HoldingPolicy()

#: Decision 4's six, each varying exactly one parameter so the comparison attributes a
#: difference to the parameter rather than to a bundle.
ALTERNATIVES: dict[str, HoldingPolicy] = {
    "stale_allowance_900": HoldingPolicy(name="stale_allowance_900", stale_allowance_s=900),
    "rest_to_expiry": HoldingPolicy(name="rest_to_expiry", rest_to_expiry=True),
    "per_variant_slots": HoldingPolicy(name="per_variant_slots", per_variant_slots=25),
    "fillability_admission": HoldingPolicy(name="fillability_admission",
                                           fillability_admission=True),
    "join_the_bid": HoldingPolicy(name="join_the_bid", join_the_bid=True),
    "near_kickoff_only": HoldingPolicy(name="near_kickoff_only", near_kickoff_only_min=180),
}

#: The alternatives this harness **cannot** move, and why (round 1 review, I2). `compare`
#: hands `plan_actions` an empty open-order list and an empty exposure state at every instant,
#: because `store.working_orders` carries no `at` horizon: `_order_action` is therefore never
#: called, so `rest_to_expiry` decides nothing, and `state.open_orders` restarts at 0, so
#: `per_variant_slots` can only bind if one instant places more than its slot count for one
#: variant. Their rows are marked in `render()` and by the CLI: a null delta here is the
#: harness's silence, never evidence that the alternative changes nothing.
NOT_EXERCISED: frozenset[str] = frozenset({"rest_to_expiry", "per_variant_slots"})

#: The sentence those rows carry, wherever they are printed.
NOT_EXERCISED_NOTE = (
    "not exercised by this harness: no resting order is reconstructed (no `at` horizon on "
    "working orders) and every instant starts from zero open orders, so an identical row is "
    "this harness's silence and not evidence that the alternative changes nothing.")

#: Every name `policy-compare` resolves, the baseline included. The baseline is a comparison
#: input like any other here; it is the *declared* policy because §1.6(a) says so, not because
#: this mapping is shaped differently.
POLICIES: dict[str, HoldingPolicy] = {BASELINE.name: BASELINE, **ALTERNATIVES}


# --- §1.6(a): the baseline, read off the files rather than recalled -------------------------


def _settings_or_defaults() -> Settings:
    """`Settings()` where the environment carries one, its declared defaults otherwise.

    `database_url` is the one required field and the baseline record reads none of it, so a
    context with no environment -- a docs build, a test module imported before a fixture sets
    `DATABASE_URL` -- still gets the `exec_*` numbers this deployment runs. Anything actually
    set in the environment or `.env` still wins, which is the point: the record states what the
    deployment is configured with, not what the addendum remembered.
    """
    try:
        return Settings()
    except ValidationError:
        return Settings(database_url="")


def _registered_variant_values(key: str) -> list:
    """One key's value in every registered variant YAML, in filename order.

    Read, never edited (§1.6(a)). Returned as a list rather than reduced here so the record can
    say "180 in all seven" or name the outlier instead of quietly picking one.
    """
    values = []
    for entry in sorted(files("harness.variants").iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".yaml"):
            continue
        config = yaml.safe_load(entry.read_text())
        if isinstance(config, dict) and key in config:
            values.append(config[key])
    return values


def _uniform(key: str) -> str:
    """`"<value> in all N registered variants"`, or every distinct value when they differ.

    A variant that moved one of these numbers is exactly what §1.6(a) says to write down as the
    file states it, so a divergence is reported rather than flattened.
    """
    values = _registered_variant_values(key)
    if not values:
        return f"absent from every registered variant ({key})"
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    if len(counts) == 1:
        return f"{next(iter(counts))} in all {len(values)} registered variants"
    return "; ".join(f"{value} in {count} registered variants"
                     for value, count in sorted(counts.items()))


def _cadence_probe() -> dict[str, int | None]:
    """The recorder's participation windows, obtained by *asking* `cadence.interval_for`.

    §1.6(a) names four cadences and a quiet window. Retyping them would let the recorder and
    the declared baseline drift apart silently, so each regime is probed with a constructed
    clock and kickoff list and the answer is whatever the shipped function returns.
    """
    from harness.feeds.espn import Kickoff
    from harness.recorder.cadence import interval_for

    tz = "America/Chicago"

    def kick(at: datetime) -> Kickoff:
        return Kickoff(sport="nfl", espn_event_id="probe", kickoff_utc=at, home="H", away="A",
                       status="scheduled")

    # 2026-09-13 is a Sunday: 18:00 UTC is 13:00 CT. 2026-09-16 is a Wednesday. 08:00 UTC is
    # 03:00 CT, inside the quiet window. Each probe names one regime and nothing else.
    sunday = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
    wednesday = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
    quiet = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
    return {
        "nfl_near": interval_for("nfl", sunday, [kick(sunday + timedelta(minutes=80))], tz),
        "game_day": interval_for("nfl", sunday, [kick(sunday + timedelta(hours=2))], tz),
        "weekend": interval_for("nfl", sunday, [], tz),
        "weekday": interval_for("nfl", wednesday, [], tz),
        "overnight": interval_for("nfl", quiet, [], tz),
    }


def baseline_record(settings: Settings | None = None) -> str:
    """§1.6(a)'s paragraph, with every number beside the name of the setting it came from.

    Read from `Settings` and the seven registered YAMLs at call time. Nothing here edits a
    registered value and nothing recalls one: a settings change that moved `exec_period_s`
    moves this paragraph, which is what `tests/test_policy_compare.py` asserts against.
    """
    s = settings if settings is not None else _settings_or_defaults()
    cadence = _cadence_probe()
    overnight = "none" if cadence["overnight"] is None else f"{cadence['overnight']} s"
    return (
        "The holding and capacity policy in force (6D §1.6(a), the declared baseline; read from "
        "harness/config/settings.py and the registered variant YAMLs, neither edited).\n"
        f"Fair-value staleness: stale_s = {_uniform('stale_s')}, and the executor's freshness "
        "test is `now - fair_ts > max(variant.stale_s, gap.stale_allowance_s)` "
        "(plan.py::_fair_stale, F36).\n"
        f"Book freshness: exec_book_max_age_s = {s.exec_book_max_age_s} s. WebSocket silence: "
        f"ws_stale_s = {s.ws_stale_s} s. Loop period: exec_period_s = {s.exec_period_s} s.\n"
        f"Capacity: exec_max_open_orders = {s.exec_max_open_orders}, shared across the executed "
        f"variants, with max_open = {_uniform('max_open')}.\n"
        f"Intent TTL: exec_intent_ttl_s = {s.exec_intent_ttl_s} s.\n"
        f"Placement stops at exec_kickoff_cutoff_min = {s.exec_kickoff_cutoff_min} min before "
        f"kickoff and at min_ttk_min = {_uniform('min_ttk_min')}; an order expires at "
        f"kickoff - {s.exec_kickoff_cutoff_min} min and is placed once, never renewed (R8).\n"
        "Participation windows are the recorder's own cadences (recorder/cadence.interval_for, "
        f"probed rather than retyped): {cadence['nfl_near']} s for NFL from T-100 to T-60 min; "
        f"{cadence['game_day']} s from 3 h before a sport's first kickoff of the day through "
        f"its last and while a game is in progress; {cadence['weekend']} s on weekends; "
        f"{cadence['weekday']} s on weekdays; {overnight} from 01:00 to 08:00 CT unless a game "
        "of that sport is in progress.\n"
        "6D declares this baseline and publishes the comparison; it adopts nothing (D7, §0.15a)."
    )


def __getattr__(name: str) -> str:
    """`BASELINE_RECORD`, built on first read so importing `plan.py` reads no file and no
    environment.

    The record is a fact about the deployment's configuration, and building it at import would
    put a `Settings()` construction and seven YAML reads inside every import of the executor's
    decision chain. Read once and cached in the module's own namespace, it is observationally
    the module-level string §1.6 asks for.
    """
    if name == "BASELINE_RECORD":
        value = baseline_record()
        globals()["BASELINE_RECORD"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- the comparison ------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyResult:
    """One policy's row of the comparison table (§1.6(b)). Every field is counterfactual."""

    policy: str
    orders_placed: int
    unique_opportunities: int
    queue_filled_orders: int
    clean_resting_seconds: int
    coverage_completed: int
    coverage_scheduled: int
    #: §1.6's second directional fact: the mean age of the fair value each placement priced
    #: against, in seconds. None when the policy placed nothing.
    mean_fair_age_s: float | None = None
    #: The four classes of §1.4, always all four, annotations excluded (ruling I8).
    exclusions: dict[str, int] = field(default_factory=dict)
    #: M6, carried on the row itself and not only in the caption.
    label: str = COUNTERFACTUAL_LABEL


#: The recorded loop instants: one `exec.loop_ms` sample per executor step (`loop.py`'s
#: telemetry). Rides `ix_metric_samples_name_ts (name, ts desc)`, one range over one name.
_LOOP_INSTANTS = text("""
select ts from metric_samples
where name = 'exec.loop_ms' and ts >= :first and ts <= :last
order by ts
""")

#: The priced runs of the range, which fix the comparison window. Bound: `r.id` between the two
#: run ids. Index: `runs_pkey` for the range, and `uq_gap_run_market (run_id, venue_market_id)`
#: for the `exists`, which stops at the first snapshot of each run rather than counting them.
_RUN_CLOCKS = text("""
select r.id, r.started_at, r.finished_at
from runs r
where r.id >= :from_run and r.id <= :to_run
  and exists (select 1 from market_gap_snapshots g where g.run_id = r.id)
order by r.id
""")

#: The most orders one comparison reads off the tape. A game day places hundreds per variant
#: (journal 136's real breakdown), so the cap is loose by two orders of magnitude and exists
#: only so a mis-specified window cannot turn a read into a table scan; `compare` logs when it
#: binds, because a truncated read would understate every policy's fills equally and silently.
ORDER_SCAN_CAP = 20_000

#: What the tape says about the orders these intents actually placed. `queue_model` is the queue
#: simulator's own fill method: a `snapshot_cross` or a `no_watcher` fill answers a different
#: question and is not what §1.6(b) means by "actual queue-filled orders".
#:
#: Bound: `variant_id` and `placed_at` inside the comparison window, then `order by o.id desc
#: limit :cap`. Index: `ix_orders_key_placed (variant_id, venue_market_id, side, placed_at)` for
#: the variant and time bound, with `orders_pkey` backing the capped `order by id desc`.
#: `orders.intent_id` carries **no** index (fix 51 added `ix_intents_created`, not this one), so
#: the intent list is applied to that bounded set instead of being the driving predicate -- and
#: Task 9 adds no schema, so no index is created here.
#:
#: Each order's resting interval is summed **once**: the fill count is a separate `exists`
#: subquery rather than a `left join`, because joining the fills first multiplied a 100 s order
#: with three fills into 300 clean resting seconds (round 1 review, C1) and ranked the policies
#: by fill fragmentation.
_TAPE_OUTCOMES = text("""
with placed as (
    select o.id, o.placed_at, o.expiry, o.cancelled_at, o.dirty_seconds
    from orders o
    where o.variant_id = :variant
      and o.placed_at >= :first and o.placed_at <= :last
      and o.intent_id = any(:intents)
    order by o.id desc
    limit :cap
)
select (select count(*) from placed) as n_orders,
       (select count(*) from placed p
        where exists (select 1 from fills f
                      where f.order_id = p.id and f.fill_method = 'queue_model')) as filled,
       coalesce((select sum(greatest(
           extract(epoch from (least(coalesce(p.cancelled_at, p.expiry, :last),
                                     coalesce(p.expiry, :last)) - p.placed_at))
           - p.dirty_seconds, 0)) from placed p), 0) as clean_s
""")


def _market_now(row):
    """A `MarketNow` for one recorded market at a replayed instant, with no book.

    A stated limitation of the harness: the comparison reads the gap snapshot's own quote
    (`best_bid`, `best_ask`, `venue_mid`) and leaves `book = None`, because rebuilding each
    ticker's ladder at every recorded instant is 6B's stepping mechanism and not this task's. A
    market with no book is not dirty (R10's `no_book` path), so the `book_dirty` rule is silent
    for every policy equally and `best_ask`/`mid` fall back to the recorded quote.
    """
    from harness.execution.plan import MarketNow, confidently_matched

    return MarketNow(
        venue_market_id=row.venue_market_id, ticker=row.ticker, fair_p=row.fair_p,
        fair_ts=row.fair_ts, fair_row_id=row.fair_value_id, staleness_s=row.staleness_s,
        stale_allowance_s=row.stale_allowance_s, feed_kind=row.feed_kind,
        best_bid_yes=row.best_bid, best_ask_yes=row.best_ask, mid_yes=row.venue_mid,
        book=None, book_dirty=False, matched=confidently_matched(row.match_status),
        match_key=row.match_key)


def _window(session: Session, settings: Settings, from_run: int,
            to_run: int) -> tuple[datetime, datetime]:
    """`[first, last]`: the pricing clocks of the first and last priced run in the range.

    The same window `harness replay --execute` walks (`replay._execute`), so a comparison and a
    replay of one range are talking about the same slice of tape.
    """
    from harness.strategy.pipeline import pricing_clock_for_run

    rows = session.execute(_RUN_CLOCKS, {"from_run": from_run, "to_run": to_run}).all()
    if not rows:
        raise ValueError(f"no priced run with gap snapshots in [{from_run}, {to_run}]")
    clocks = [pricing_clock_for_run(row, settings.tick_budget_s) for row in rows]
    return min(clocks), max(clocks)


@dataclass
class _Tally:
    """One policy's running counts while the slice is walked once for every policy (I3).

    Mutable and private: `PolicyResult` is the frozen thing a caller gets, and this is the
    bookkeeping that produces it. `blocked` is the harness's stand-in for a resting order, and
    `counted` is `uq_skip_once`'s key, so an exclusion is counted once per (intent, reason)
    rather than once per instant.
    """

    policy: HoldingPolicy
    placed: set = field(default_factory=set)
    keys: set = field(default_factory=set)
    blocked: set = field(default_factory=set)
    counted: set = field(default_factory=set)
    reasons: dict = field(default_factory=dict)
    fair_ages: list = field(default_factory=list)
    completed: int = 0


def compare(session: Session, settings: Settings, *, from_run: int, to_run: int, variant: str,
            policies: list[HoldingPolicy], now: datetime | None = None) -> list[PolicyResult]:
    """One `PolicyResult` per policy over the same recorded slice. Writes nothing.

    For each recorded loop instant in `[from_run, to_run]` -- the `exec.loop_ms` samples the
    live executor left behind, which is §1.6(b)'s "stepping at the recorded loop instants" --
    the intents and the markets are read **as of** that instant (`store.load_intents(at=)`,
    `store.market_rows(at=)`) and handed to `plan_actions(..., policy=policy)`. The counts are
    accumulated in memory: no order, no intent, no signal, no metric and no `session.commit()`.

    **The tape is read once per instant, not once per policy** (round 1 review, I3): the reads
    depend on the instant alone, so the instant is the outer loop and the policies the inner
    one. A game-day slice is ~5,760 recorded instants; reading it per policy would be ~40,000
    "newest as of" reads for the six alternatives instead of ~5,760, which is the difference
    between a duty that runs and one that trips a statement budget. Nothing is cached across
    instants, so the memory cost stays one instant's rows.

    `now` **labels the run and does not bound the tape**: every instant comes from the record
    and the window comes from the run range's own pricing clocks (round 1 review, M2). It is
    the clock the comparison was taken at, carried into the log line, and is the brief's own
    parameter name.

    Three stated limitations, which is what "counterfactual" means here concretely:

    * **Each policy starts from an empty set of open orders and an empty exposure state**,
      because `store.working_orders` carries no `at` horizon. The comparison is therefore
      between policies over the same tape and not a reconstruction of the live book. What
      stands in for a resting order is this function's own bookkeeping: an intent a policy
      placed blocks its `(venue_market_id, side)` for the rest of the slice, so one intent is
      one placement, and an exclusion is counted once per `(intent, reason)` exactly as
      `uq_skip_once` records it live. Without that, a key stale at 240 recorded instants would
      read as 240 exclusions.
    * **`rest_to_expiry` and `per_variant_slots` cannot move a result here** (`NOT_EXERCISED`,
      round 1 review I2), and that follows from the line above: with no open orders
      `_order_action` is never called at all, so the holding preference decides nothing, and
      with `state.open_orders` restarting at 0 each instant the slot count can only bind if one
      instant places more than its slots for one variant. Their rows are marked wherever they
      are printed; an identical row is this harness's silence, never a finding.
    * **No book is rebuilt** (`_market_now`), and the kill switch is taken as inactive: the
      recorded kill state at a past instant is not stored, and reading today's would make a
      re-run's answer depend on when it was run rather than on the window it covers.

    `queue_filled_orders` and `clean_resting_seconds` are read from the record, for the orders
    the intents this policy would have placed actually produced. An intent the live policy
    skipped has no order and no fills on the tape, so it contributes nothing: the counterfactual
    cannot know what an order that never existed would have done, and inventing a fill for it
    would be the one number nobody could check.

    `coverage_completed / coverage_scheduled`: `scheduled` is **policy-independent** (round 1
    review, M3) -- the recorded loop instants at which this variant had any intent inside the
    TTL, which is the same denominator for every row -- and `completed` is the instants at
    which the chain reached a decision for every intent owed one, an instant whose keys are all
    held by the harness's stand-in resting orders included. The pair is carried because §1.7's
    contract is stated as completed-over-scheduled and because an instant the harness dropped is
    exactly what it would show.
    """
    from harness.execution import store
    from harness.execution.plan import CapGate, ExecSettings, Place, Skip, plan_actions

    resolved_now = now if now is not None else datetime.now(timezone.utc)
    variant_ids = store.resolve_variants(session, [variant])
    if not variant_ids:
        raise ValueError(f"no variant registered with name or id {variant!r}")
    variant_id = variant_ids[0]
    configs = store.variant_configs(session, [variant_id])
    if variant_id not in configs:
        raise ValueError(f"variant {variant!r} has no stored config")

    first, last = _window(session, settings, from_run, to_run)
    instants = [row.ts for row in
                session.execute(_LOOP_INSTANTS, {"first": first, "last": last}).all()]
    if not instants:
        raise ValueError(
            f"no recorded loop instant (exec.loop_ms) in [{first.isoformat()}, "
            f"{last.isoformat()}]: there is no slice to step. The comparison steps the loop "
            "instants the live executor left behind, never a grid of its own (§1.6(b)).")
    log.info("policy-compare: %d recorded loop instants over [%s, %s], %d policies "
             "(counterfactual, nothing is written)", len(instants), first, last, len(policies))
    for policy in policies:
        if policy.name in NOT_EXERCISED:
            log.warning("policy-compare: %s is %s", policy.name, NOT_EXERCISED_NOTE)

    exec_settings = ExecSettings.from_settings(settings)
    ttl = timedelta(seconds=settings.exec_intent_ttl_s)
    variant_cfg = {variant_id: configs[variant_id]}

    tallies = [_Tally(policy) for policy in policies]
    scheduled = 0
    for instant in instants:
        intents, _ = store.load_intents(session, [variant_id], instant - ttl, replay=False,
                                        at=instant)
        if not intents:
            continue
        scheduled += 1
        rows = store.market_rows(session, {row.venue_market_id for row in intents}, at=instant)
        markets = {vm_id: _market_now(row) for vm_id, row in rows.items()}
        by_id = {row.intent_id: row for row in intents}
        for tally in tallies:
            due = [row for row in intents
                   if (row.venue_market_id, row.side) not in tally.blocked]
            if not due:
                # Every intent owed an evaluation is answered by a standing placement: the
                # instant is covered, and counting it otherwise would make the denominator and
                # the numerator disagree about what "owed" means.
                tally.completed += 1
                continue
            actions = plan_actions(due, [], markets, {}, variant_cfg, False, instant,
                                   exec_settings, policy=tally.policy)
            decided = {action.intent_id for action in actions
                       if getattr(action, "intent_id", None) is not None}
            if len(decided) == len(due):
                tally.completed += 1
            for action in actions:
                if isinstance(action, Place):
                    intent = by_id[action.intent_id]
                    tally.placed.add(action.intent_id)
                    tally.keys.add((intent.venue_market_id, intent.side))
                    tally.blocked.add((intent.venue_market_id, intent.side))
                    market = markets.get(intent.venue_market_id)
                    age = None if market is None else market.fair_age_s(instant)
                    if age is not None:
                        tally.fair_ages.append(age)
                elif isinstance(action, Skip) or (isinstance(action, CapGate)
                                                  and action.blocking):
                    # Once per (intent, reason), which is how `uq_skip_once` stores it live.
                    mark = (action.intent_id, action.reason)
                    if mark not in tally.counted:
                        tally.counted.add(mark)
                        tally.reasons[action.reason] = tally.reasons.get(action.reason, 0) + 1

    results = []
    for tally in tallies:
        filled, clean = _tape_outcomes(session, sorted(tally.placed), variant_id, first, last)
        results.append(PolicyResult(
            policy=tally.policy.name, orders_placed=len(tally.placed),
            unique_opportunities=len(tally.keys), queue_filled_orders=filled,
            clean_resting_seconds=clean, coverage_completed=tally.completed,
            coverage_scheduled=scheduled,
            mean_fair_age_s=((sum(tally.fair_ages) / len(tally.fair_ages))
                             if tally.fair_ages else None),
            exclusions=exclusion_totals(tally.reasons)))
    log.info("policy-compare: finished at %s; no row written", resolved_now.isoformat())
    return results


def _tape_outcomes(session: Session, intent_ids: list, variant_id: str, first: datetime,
                   last: datetime) -> tuple[int, int]:
    """What the record holds for the orders these intents actually placed: queue-model fills,
    and resting seconds on a book that was not dirty (`orders.dirty_seconds` against
    `[placed_at, min(cancelled_at, expiry)]` -- 6B §1.5 owns that interval, 6D reads it).

    Each order contributes its interval exactly once, whatever its fill count (round 1 review,
    C1). The read is bounded to the variant and the comparison window and capped at
    `ORDER_SCAN_CAP`; a cap that binds is logged rather than swallowed.
    """
    if not intent_ids:
        return 0, 0
    row = session.execute(_TAPE_OUTCOMES, {"intents": intent_ids, "variant": variant_id,
                                           "first": first, "last": last,
                                           "cap": ORDER_SCAN_CAP}).one()
    if int(row.n_orders) >= ORDER_SCAN_CAP:
        log.warning("policy-compare: the order read hit ORDER_SCAN_CAP (%d) for variant %s over "
                    "[%s, %s]; fills and clean resting seconds are truncated", ORDER_SCAN_CAP,
                    variant_id, first, last)
    return int(row.filled), int(Decimal(str(row.clean_s)))


def render(results: list[PolicyResult]) -> str:
    """The comparison table: the caption, and the label on every row (M6).

    Formatted here rather than in the CLI so neither the caption nor the per-row label can be
    dropped by a caller who only wanted the numbers.
    """
    head = (f"{'policy':<22}{'placed':>8}{'unique':>8}{'filled':>8}{'clean_s':>10}"
            f"{'coverage':>12}{'mean_age_s':>12}"
            + "".join(f"{name:>18}" for name in EXCLUSION_CLASSES))
    lines = [f"Holding/capacity policy comparison -- {COUNTERFACTUAL_LABEL}", "", head,
             "-" * len(head)]
    for row in results:
        coverage = f"{row.coverage_completed}/{row.coverage_scheduled}"
        age = "-" if row.mean_fair_age_s is None else f"{row.mean_fair_age_s:.1f}"
        lines.append(
            f"{row.policy:<22}{row.orders_placed:>8}{row.unique_opportunities:>8}"
            f"{row.queue_filled_orders:>8}{row.clean_resting_seconds:>10}{coverage:>12}"
            f"{age:>12}"
            + "".join(f"{row.exclusions.get(name, 0):>18}" for name in EXCLUSION_CLASSES))
        lines.append(f"    {row.policy}: {row.label}")
        if row.policy in NOT_EXERCISED:
            lines.append(f"    {row.policy}: {NOT_EXERCISED_NOTE}")
    lines.append("")
    lines.append("Adoption is the user's dated decision (§0.15a); 6D adopts nothing (D7). The "
                 "selected policy is registered as a new config_history hash or a new variant "
                 "id by dated amendment -- never as an edit to a registered id.")
    lines.append("This table is the admission diagnostic (one pass, no arm state); stateful "
                 "fills and conserved liquidity are `harness exp run` (6D.1 §1.1f).")
    return "\n".join(lines)
