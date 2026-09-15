"""Re-scores recorded gap snapshots under a variant, and re-runs the paper executor over them.

`replay` walks a run range that was already priced (its `market_gap_snapshots` exist),
rebuilds the same `GapRow`s the live pipeline used, and scores them under one variant --
either an already-registered one, addressed by name, or a one-off YAML forced onto the
`replay` tier. The resulting `signals` rows carry `replay=True`, so they share the table
with live signals but never collide with them (spec: `uq_signal_key` includes `replay`).

Each run's signals are stamped with that run's *own* pricing clock -- `pricing_clock_for_run`,
the same instant `harness price-once --run-id` would have used -- rather than with wall time.
That is what makes the range a timeline instead of a heap: with `execute=True` the executor is
stepped once per `exec_period_s` from the first run's clock to the last's, and at each instant
it sees exactly the signals, gap snapshots and tape rows that existed by then (Task 13, R14).

`StrategyState` is created once per `replay()` call and threaded across every run in the
range in ascending order, the same way the live pipeline's per-tick state would accumulate
over a trading day -- caps see the whole range's exposure, not each run's in isolation.

Nothing here opens a socket. The executor it builds is a replay executor: `mode` is `paper`,
every row it writes is tagged `replay = true`, it holds its own advisory lock and it writes no
heartbeat and no telemetry, so a replayed day never touches a live row or the live service.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.corrections import CORRECTIONS
from harness.db.models import MarketGapSnapshot, Run, StrategyVariant
from harness.execution import store
from harness.execution.loop import Executor
from harness.strategy.as_measured import as_measured_table
from harness.strategy.pipeline import _insert_signals, _load_gap_rows, pricing_clock_for_run
from harness.strategy.run import StrategyState, run_strategy
from harness.strategy.variants import Variant, register_variants, variant_from_config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayCounts:
    runs: int = 0
    signals_candidate: int = 0
    signals_rejected: int = 0
    inserted: int = 0
    #: Replay orders and replay fills now on the record, zero unless `execute=True`. The
    #: record's totals rather than this call's: a rerun writes nothing and still reports them.
    orders: int = 0
    fills: int = 0
    #: C6. `population` is the set resolved from the range, `today_variants` what the executor
    #: is configured with now, and the two correction id tuples say which repairs were in force
    #: on each side. `grid_steps` and `live_steps` are published instead of a parity verdict,
    #: which is suspended while the timing policies differ (D15): the grid steps exactly
    #: `exec_period_s` where the live loop ran 27 steps in the sampled hour. `live_steps` stays
    #: None until 6D instruments the live loop's own step count; None is "not measured here",
    #: never "zero", and the CLI prints it only when it has one.
    population: tuple[str, ...] = ()
    today_variants: tuple[str, ...] = ()
    corrections_replayed: tuple[str, ...] = ()
    corrections_live: tuple[str, ...] = ()
    grid_steps: int = 0
    live_steps: int | None = None
    mode: str = "single_variant"
    #: Distinct replay intents this range skipped for `exec_capacity` -- the record's own
    #: count, since `uq_skip_once` keys an `order_events` skip on `(intent_id, kind, reason)`
    #: and one intent skipped at six consecutive instants leaves one row. It is **not** the
    #: planner-action count spec 1.6 also names: the occurrences exist only inside
    #: `plan_actions`'s return value (`ExecStats.skipped` is incremented only when the row was
    #: written, and carries no reason), and surfacing them would mean a new per-reason counter
    #: in `harness/execution/loop.py`, which this task's scope fences off. The occurrences are
    #: asserted in `tests/test_replay.py` through the loop module's own `plan_actions`, and the
    #: two quantities are asserted separately there so they are never confused.
    capacity_skips: int = 0


class ReplayStepError(RuntimeError):
    """A replay grid step did not run cleanly, so the replay's counts mean nothing.

    `Executor.step` never raises: the live loop has to survive one bad step, and a failure is
    rolled back, recorded on the heartbeat and stepped over. A replay has no next loop to be
    saved by -- a swallowed statement timeout would come out as `orders=0`, which the Monday
    replay-versus-live duty would read as a total divergence with no visible cause -- so the
    replay driver turns a step's own error, or a step it never got to run, into a failure of
    the whole command (fix round 1, I2).
    """


class PopulationError(RuntimeError):
    """The replayed range has no single executed population, so it has no baseline.

    Raised before anything is written. A range spanning a registered change of the executed set
    -- or the 6B deploy boundary, where the simulator's own arithmetic changed -- is two
    experiments, and one number over both would be their average rather than either (ruling
    IM-5). The caller splits the range by hand.
    """


#: Which variants actually placed an order in each run of the range. Split by run, not summed,
#: because a *change* of the set is what the refusal is about and a union would hide one.
#: Bounded by the run range on `uq_signal_key`, whose leading column is `run_id`
#: (`migrations/versions/0001_baseline.py`), with `limit :cap` so the read can never become
#: unbounded however wide a range is asked for.
_POPULATION_BY_RUN = text("""
select s.run_id, o.variant_id
from orders o
join intents i on i.id = o.intent_id
join signals s on s.id = i.signal_id
where o.replay = false and s.replay = false
  and s.run_id >= :first and s.run_id <= :last
group by s.run_id, o.variant_id
order by s.run_id, o.variant_id
limit :cap
""")


def resolve_population(session: Session, first_run: int, last_run: int,
                       boundary_run_id: int | None = None, cap: int = 10_000) -> list[str]:
    """The executed set in force over `[first_run, last_run]`, or a refusal.

    Resolved from the record rather than from `Settings.exec_variants` (spec 0.11): today's
    setting is what the executor runs *now*, and a replay of a past range has to reproduce the
    set that range actually ran under. Pre-registration Amendments 2, 3 and 4 changed that set
    during the paper run, so this is not a hypothetical.

    Read off the `orders -> intents -> signals` chain and not off `config_history`, which
    cannot answer it: that table is `(config_hash, config_json, first_seen)` with no run column
    (`harness/db/models.py`), so any join to `runs` would be a cross join and would return
    every row for any non-empty range. The chain carries a run -- `Signal.run_id` -- and an
    order is in the executed set only if it was placed.

    Two refusals, both total and both before anything is written (ruling IM-5).

    The first is a **change of the executed set** inside the range. Every run that placed an
    order contributes the set of variants that placed one; if two runs in the range contribute
    different sets, the range is two experiments and one number over it would be their average
    rather than either. Runs that placed nothing contribute nothing and are skipped, because a
    quiet hour is not a change of the set.

    The second is the **6B deploy boundary**, where the simulator's own arithmetic changed. It
    is a number only the controller knows, so it arrives as an argument and is never hard-coded
    here; None means the caller did not supply one and the check does not run.
    """
    rows = session.execute(
        _POPULATION_BY_RUN, {"first": first_run, "last": last_run, "cap": cap}).all()
    if len(rows) == cap:
        # A truncated read is indistinguishable from a complete one, and what `limit` cuts is
        # the *tail* -- exactly where a later change of the executed set would be. Answering
        # from it would replay a wide range under the population of its earlier half and say
        # nothing about having done so, so reaching the cap is refused like any other range
        # with no single answer. Reaching it exactly, with nothing cut, is refused too: the
        # query cannot tell that case apart, and the operator's move is the same either way.
        raise PopulationError(
            f"runs {first_run}-{last_run} reached the {cap}-row population cap: the read may "
            f"be truncated, and a change of the executed set after the cut would be invisible. "
            f"Replay the range in narrower pieces.")
    if not rows:
        raise PopulationError(
            f"runs {first_run}-{last_run} placed no non-replay order: there is no executed set "
            f"to reproduce")
    by_run: dict[int, set[str]] = {}
    for row in rows:
        by_run.setdefault(row.run_id, set()).add(row.variant_id)
    sets = {frozenset(variants) for variants in by_run.values()}
    if len(sets) > 1:
        seen = {run_id: sorted(v) for run_id, v in sorted(by_run.items())}
        raise PopulationError(
            f"runs {first_run}-{last_run} span a change of the executed set: "
            f"{seen}. Split the range at the change and replay each side.")
    if boundary_run_id is not None and first_run <= boundary_run_id < last_run:
        raise PopulationError(
            f"runs {first_run}-{last_run} span the 6B deploy boundary at run "
            f"{boundary_run_id}: the simulator's arithmetic differs on the two sides. Replay "
            f"each side separately.")
    return sorted(next(iter(sets)))


def _parse_run_range(text_range: str) -> tuple[int, int | None] | None:
    """`"1234-5678"` as a closed pair, `"> 1234"` as an open-ended one, or None for anything else.

    `"> N"` (spaces around the number are tolerated, `">N"` too) parses to `(N + 1, None)`,
    where `None` is the unbounded-upper marker `_corrections_for` treats as "covers every run
    from the lower bound on". Anything that is neither form is the controller's prose
    placeholder ("all runs through the 6B deploy"), which `_corrections_for` treats as covering
    the range rather than as covering nothing.
    """
    text_range = text_range.strip()
    if text_range.startswith(">"):
        try:
            n = int(text_range[1:].strip())
        except ValueError:
            return None
        return n + 1, None
    parts = text_range.split("-")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None


def _corrections_for(first_run: int, last_run: int) -> tuple[str, ...]:
    """The manifest ids whose `affected_run_id_range` overlaps the replayed range.

    An **unfilled** range counts as covering it. C0's is still prose -- "all runs through the
    6B deploy" -- and reporting "no corrections in force" from a field nobody has filled in
    would turn a missing value into a measurement claim, which is the one reading the manifest
    exists to prevent. A closed numeric `A-B` is parsed and tested against both ends; an
    open-ended `"> N"` (C1-C6's shape today) is parsed to `(N + 1, None)` and tested against its
    lower bound only, since it has no upper end to overlap against. The day the controller fills
    a closed range in, this narrows without another change here.
    """
    ids = []
    for correction in CORRECTIONS:
        bounds = _parse_run_range(correction.affected_run_id_range)
        if bounds is None:
            ids.append(correction.id)
            continue
        low, high = bounds
        if high is None:
            covers = low <= last_run
        else:
            covers = low <= last_run and high >= first_run
        if covers:
            ids.append(correction.id)
    return tuple(ids)


class _Grid:
    """The replay executor's clock: one instant per `exec_period_s`, advanced by the caller.

    Injected as `clock=grid.now`, so every decision the loop makes is stamped with a grid
    instant and nothing in it can read wall time (the phase's "pure decisions, explicit `now`"
    rule). `monotonic` is deliberately left alone: it measures how long a step took, which is
    a real duration even when the step is replaying a Tuesday in September.
    """

    def __init__(self, start: datetime, period_s: int) -> None:
        self._now = start
        self._step = timedelta(seconds=period_s)

    def now(self) -> datetime:
        return self._now

    def advance(self) -> None:
        self._now += self._step


def _tick_budget_s(settings: Settings | None) -> float:
    """`tick_budget_s` without forcing a caller to build a `Settings` it does not otherwise need.

    Only an unfinished run consults it (`pricing_clock_for_run` falls back to
    `started_at + tick_budget_s`), and the field's default is the value every deployment runs,
    so reading the declared default is honest here and does not touch the environment.
    """
    if settings is not None:
        return settings.tick_budget_s
    return Settings.model_fields["tick_budget_s"].default


class RegisteredVariantError(ValueError):
    """`--file` was passed for a name that is a registered live variant.

    `register_variants` treats a supplied config for an existing name as a config change and
    renames the live row aside, deactivating it (the Amendment 3 incident, 2026-09-08). A
    registered variant is replayed by name; `--file` is for out-of-band replay variants only.
    """


def _resolve_variant(
    session: Session, variant_name: str, variant_file: Path | None, now: datetime
) -> Variant:
    if variant_file is not None:
        registered = session.execute(
            select(StrategyVariant).where(StrategyVariant.name == variant_name)
        ).scalar_one_or_none()
        if registered is not None and registered.tier != "replay":
            raise RegisteredVariantError(
                f"{variant_name!r} is a registered {registered.tier} variant "
                f"({registered.variant_id}); replay it by name, without --file. Passing --file "
                f"for a registered name renames the live row aside and deactivates it."
            )
        config = dict(yaml.safe_load(Path(variant_file).read_text()))
        # --variant is the operator's stated intent; a mismatch usually means the wrong file
        # was passed (or vice versa), so this fails loudly before anything is registered
        # rather than silently replaying whatever the file happens to contain.
        if config.get("name") != variant_name:
            raise ValueError(
                f"--variant {variant_name!r} does not match file name {config.get('name')!r}"
            )
        # Replay variants are out-of-band (spec §6.7): forcing the tier here means the
        # variant_id -- which hashes the whole config, tier included -- always reflects it,
        # even when the source YAML was written (or copied) with a different tier.
        config["tier"] = "replay"
        variant = variant_from_config(config, str(variant_file))
        register_variants(session, [variant], now, prune=False)
        return variant

    row = session.execute(
        select(StrategyVariant).where(StrategyVariant.name == variant_name)
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"no variant registered with name {variant_name!r}")
    return Variant(name=row.name, tier=row.tier, config=row.config_json, variant_id=row.variant_id)


def _variant_by_id(session: Session, variant_id: str) -> Variant:
    """The registered variant behind an id `resolve_population` read off an order.

    A missing row raises rather than being skipped: the id came from an order that was
    actually placed, so a population it cannot score is a record the replay does not
    understand, not a variant to drop quietly from the set.
    """
    row = session.execute(
        select(StrategyVariant).where(StrategyVariant.variant_id == variant_id)
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(
            f"the range placed orders under variant {variant_id!r}, which is not registered: "
            f"it cannot be re-scored")
    return Variant(name=row.name, tier=row.tier, config=row.config_json, variant_id=row.variant_id)


def replay(
    session: Session,
    settings: Settings,
    first_run: int,
    last_run: int,
    *,
    variant_name: str | None = None,
    variant_file: Path | None = None,
    population: str | None = None,
    boundary_run_id: int | None = None,
    now: datetime | None = None,
    execute: bool = False,
) -> ReplayCounts:
    """Re-score every run in `[first_run, last_run]` that has gap snapshots, under one variant
    or under the set the range itself executed.

    Runs with no `market_gap_snapshots` in range are skipped rather than erroring, so a
    sparse or partially-recorded range still replays what it has. Each run is scored and its
    signals stamped with `pricing_clock_for_run(run)` -- the instant that run's own pricing
    happened -- and committed individually. `now` is still the registration clock for a
    `--file` variant and the fallback for a run row that has gone missing; it is deliberately
    not what a signal carries, because a signal's time is the market's, not the operator's.

    With `execute=True` the replayed signals are then handed to a replay `Executor` stepped
    once per `settings.exec_period_s` across the range (`_execute`). Nothing is filtered out
    of the record on either pass: rejected signals are inserted like candidates, and the
    executor writes its intents, skips and cap gates exactly as the live loop does.

    `population="range"` replaces the single variant with the set the range itself executed
    (C6, spec 1.6): every run is scored under every variant of that set and **one** executor is
    built over all of them, so `plan_actions` applies the one shared `max_open_orders` counter
    the live loop applied. A range with no single such set is refused by `resolve_population`
    before the first `session.commit()`. `settings` is no longer optional, and is second: a
    replay that cannot read `exec_period_s` cannot resolve a range's population either.
    """
    if population is None and variant_name is None:
        raise ValueError("replay needs --variant, or --population range to resolve the set "
                         "from the record")
    if population not in (None, "range"):
        raise ValueError(f"unknown population {population!r}; the only value is 'range'")
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    if population == "range":
        # Resolved before the scoring loop's first `session.commit()`, so a refused range
        # leaves nothing behind: a half-written replay is worse than none, because its rows
        # look like every other replay row.
        variant_ids = resolve_population(session, first_run, last_run, boundary_run_id)
        variants = [_variant_by_id(session, variant_id) for variant_id in variant_ids]
        mode = "range_population"
    else:
        variants = [_resolve_variant(session, variant_name, variant_file, resolved_now)]
        variant_ids = [variants[0].variant_id]
        mode = "single_variant"

    run_ids = (
        session.execute(
            select(MarketGapSnapshot.run_id)
            .where(MarketGapSnapshot.run_id >= first_run)
            .where(MarketGapSnapshot.run_id <= last_run)
            .distinct()
            .order_by(MarketGapSnapshot.run_id)
        )
        .scalars()
        .all()
    )

    counts = ReplayCounts()
    # One `StrategyState` per variant, threaded across the whole range: caps are a property of
    # a variant's own book over the day, and pooling three variants' exposure into one state
    # would make the replay's caps bind on a total no live variant ever saw.
    states = {variant.variant_id: StrategyState() for variant in variants}
    clocks: list[datetime] = []
    for run_id in run_ids:
        rows = _load_gap_rows(session, run_id)
        run_row = session.get(Run, run_id)
        clock = (pricing_clock_for_run(run_row, _tick_budget_s(settings))
                 if run_row is not None else resolved_now)
        clocks.append(clock)

        # `as_measured` is a label, never a decision input (F56), and the live pipeline reads
        # it once per run at the same clock -- so replaying without it would leave the replay's
        # signals carrying a NULL where their live twins carry a bucket. It is read once for
        # the run and shared by the whole population: it is a property of the market at that
        # instant, not of the variant reading it.
        #
        # `stopped` (§9.3's drawdown annotation) is deliberately *not* replayed, which is the
        # opposite call for the opposite reason: it is a property of the live equity curve at
        # that instant, not of the market being re-simulated, and a replay run keeps no equity
        # curve of its own (`equity_snapshots` has no replay partition and the replay executor
        # writes no telemetry at all). Replayed signals therefore always read
        # `drawdown_stop = false`. Nothing is lost: the annotation decides nothing, and a
        # replay that read the live curve would make a re-run's output depend on when it was
        # run rather than on the window it covers.
        as_measured = as_measured_table(session, clock)
        candidate = rejected = inserted = 0
        for variant in variants:
            signals = run_strategy(rows, variant, clock, state=states[variant.variant_id],
                                   as_measured=as_measured)
            scored = sum(1 for s in signals if s.decision == "candidate")
            candidate += scored
            rejected += len(signals) - scored
            inserted += _insert_signals(session, run_id, variant, clock, signals, replay=True)
        session.commit()

        # `runs` counts runs, not (run, variant) pairs; the signal counts are the population's
        # total, which for a single-variant replay is exactly what it always was.
        counts = ReplayCounts(
            runs=counts.runs + 1,
            signals_candidate=counts.signals_candidate + candidate,
            signals_rejected=counts.signals_rejected + rejected,
            inserted=counts.inserted + inserted,
        )

    if execute and clocks:
        orders, fills, grid_steps, capacity_skips = _execute(session, settings, variant_ids,
                                                             clocks[0], clocks[-1])
        counts = replace(counts, orders=orders, fills=fills, grid_steps=grid_steps,
                         capacity_skips=capacity_skips)
    # Published, never compared (D15): `live_steps` stays None until 6D measures it.
    return replace(counts, population=tuple(variant_ids),
                   today_variants=tuple(settings.exec_variants),
                   corrections_replayed=_corrections_for(first_run, last_run),
                   corrections_live=tuple(c.id for c in CORRECTIONS),
                   mode=mode)


def _execute(session: Session, settings: Settings, variant_ids: list[str],
             first: datetime, last: datetime) -> tuple[int, int, int, int]:
    """Step one replay executor over every variant in the population, once per `exec_period_s`.

    One executor, not one per variant (spec 0.11): `plan_actions` applies a single shared
    `max_open_orders` counter, which is what the live loop did, and a per-variant executor
    would give each variant the whole capacity. Returns the orders, the fills and the number of
    grid steps and the capacity skips the record holds, the third of which the caller publishes
    beside the live step count instead of a parity verdict.

    The executor gets its own session factory bound to the caller's engine, because a step owns
    its connection and its transaction: it takes an advisory lock, commits per step and must
    not inherit whatever transaction the caller is sitting in. The caller's own session is only
    used afterwards, to count what was written.

    The grid ends at the last run's pricing clock rather than running on to some later horizon:
    past that instant the range has no more pricing to offer, and an order still resting there
    is exactly what the live executor would have been holding.
    """
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    grid = _Grid(first, settings.exec_period_s)
    # Addressed by `variant_id`, not by name: `resolve_variants` accepts either, and a `--file`
    # variant is registered without pruning, so a name can stand for two registered
    # configurations at once (the live one and this replay's). The ids are the ones this call
    # actually resolved and scored, so they are what the executor is given.
    executor = Executor(settings, factory, clock=grid.now, replay=True, variants=variant_ids)
    steps = 0
    while grid.now() <= last:
        instant, steps = grid.now(), steps + 1
        stats = executor.step()
        if not stats.locked:
            raise ReplayStepError(
                f"replay step {steps} at {instant.isoformat()} never ran: another replay holds "
                f"the {store.REPLAY_LOCK_KEY} lock")
        # Fix 66, M2: a book-read failure is tolerated live (fix 60) but must fail a
        # replay -- an order held all day on a ticker whose reads kept timing out
        # would otherwise finish the step "successfully" and only show up later as an
        # unexplained live/replay mismatch.
        if stats.errors or stats.book_errors:
            raise ReplayStepError(
                f"replay step {steps} at {instant.isoformat()} failed: "
                f"{stats.last_error or 'see the executor log'}")
        grid.advance()
    log.info("replay executor stepped %s times over [%s, %s]", steps, first, last)
    orders, fills, capacity_skips = _replay_row_counts(session, variant_ids, first, last)
    return orders, fills, steps, capacity_skips


_REPLAY_COUNTS = text("""
select (select count(*) from orders o
        where o.replay = true and o.variant_id = any(:v)
          and o.placed_at >= :first and o.placed_at <= :last) as orders,
       (select count(*) from fills f join orders o on o.id = f.order_id
        where o.replay = true and o.variant_id = any(:v)
          and o.placed_at >= :first and o.placed_at <= :last) as fills,
       (select count(*) from order_events e
        join intents i on i.id = e.intent_id
        where e.replay = true and e.kind = 'skipped' and e.reason = 'exec_capacity'
          and i.variant_id = any(:v)
          and e.ts >= :first and e.ts <= :last) as capacity_skips
""")


def _replay_row_counts(session: Session, variant_ids: list[str], first: datetime,
                       last: datetime) -> tuple[int, int, int]:
    """This range's replay orders, their fills and its capacity skips, as the record holds them.

    Scoped to the whole population rather than to one variant (C6): the number the caller
    publishes is what the range's executed set placed, and a per-variant count would be a
    different quantity under the same name. The third count is the `exec_capacity` skip
    *rows* -- one per intent the counter turned away, however many instants it turned it away
    at -- which is the quantity the record can answer and the one the Monday duty can
    reproduce from it (spec 1.6 asks for the planner-action count beside it; see
    `ReplayCounts.capacity_skips`).

    Counted rather than accumulated from `ExecStats`: a second replay over the same range
    re-derives every decision and inserts nothing (`on conflict do nothing`), and what the
    caller wants to hear is what stands, not what this call was optimistic about. Scoped to the
    replayed variant and the grid's own window, so replaying a second range afterwards reports
    that range rather than the season's running total.

    Read through a session of its own (fix round 1, M1). The executor committed on its own
    connection, so the count has to be taken outside whatever snapshot the caller is holding --
    and committing the caller's transaction to get there would be an undocumented side effect
    of asking for a number.
    """
    with Session(bind=session.get_bind()) as reader:
        row = reader.execute(_REPLAY_COUNTS,
                             {"v": list(variant_ids), "first": first, "last": last}).one()
    return int(row.orders), int(row.fills), int(row.capacity_skips)
