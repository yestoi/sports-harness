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


class ReplayStepError(RuntimeError):
    """A replay grid step did not run cleanly, so the replay's counts mean nothing.

    `Executor.step` never raises: the live loop has to survive one bad step, and a failure is
    rolled back, recorded on the heartbeat and stepped over. A replay has no next loop to be
    saved by -- a swallowed statement timeout would come out as `orders=0`, which the Monday
    replay-versus-live duty would read as a total divergence with no visible cause -- so the
    replay driver turns a step's own error, or a step it never got to run, into a failure of
    the whole command (fix round 1, I2).
    """


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


def _resolve_variant(
    session: Session, variant_name: str, variant_file: Path | None, now: datetime
) -> Variant:
    if variant_file is not None:
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


def replay(
    session: Session,
    from_run: int,
    to_run: int,
    variant_name: str,
    variant_file: Path | None = None,
    now: datetime | None = None,
    execute: bool = False,
    settings: Settings | None = None,
) -> ReplayCounts:
    """Re-score every run in `[from_run, to_run]` that has gap snapshots, under one variant.

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
    """
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    if execute and settings is None:
        raise ValueError("replay(execute=True) needs settings: the executor is configured by them")
    variant = _resolve_variant(session, variant_name, variant_file, resolved_now)

    run_ids = (
        session.execute(
            select(MarketGapSnapshot.run_id)
            .where(MarketGapSnapshot.run_id >= from_run)
            .where(MarketGapSnapshot.run_id <= to_run)
            .distinct()
            .order_by(MarketGapSnapshot.run_id)
        )
        .scalars()
        .all()
    )

    counts = ReplayCounts()
    state = StrategyState()
    clocks: list[datetime] = []
    for run_id in run_ids:
        rows = _load_gap_rows(session, run_id)
        run_row = session.get(Run, run_id)
        clock = (pricing_clock_for_run(run_row, _tick_budget_s(settings))
                 if run_row is not None else resolved_now)
        clocks.append(clock)

        # `as_measured` is a label, never a decision input (F56), and the live pipeline reads
        # it once per run at the same clock -- so replaying without it would leave the replay's
        # signals carrying a NULL where their live twins carry a bucket.
        signals = run_strategy(rows, variant, clock, state=state,
                               as_measured=as_measured_table(session, clock))
        candidate = sum(1 for s in signals if s.decision == "candidate")
        rejected = len(signals) - candidate
        inserted = _insert_signals(session, run_id, variant, clock, signals, replay=True)
        session.commit()

        counts = ReplayCounts(
            runs=counts.runs + 1,
            signals_candidate=counts.signals_candidate + candidate,
            signals_rejected=counts.signals_rejected + rejected,
            inserted=counts.inserted + inserted,
        )

    if execute and clocks:
        orders, fills = _execute(session, settings, variant, clocks[0], clocks[-1])
        counts = replace(counts, orders=orders, fills=fills)
    return counts


def _execute(session: Session, settings: Settings, variant: Variant,
             first: datetime, last: datetime) -> tuple[int, int]:
    """Step a replay executor once per `exec_period_s` from `first` to `last`, inclusive.

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
    # configurations at once (the live one and this replay's). The id is the one this call
    # actually resolved and scored, so it is the one the executor is given.
    executor = Executor(settings, factory, clock=grid.now, replay=True,
                        variants=[variant.variant_id])
    steps = 0
    while grid.now() <= last:
        instant, steps = grid.now(), steps + 1
        stats = executor.step()
        if not stats.locked:
            raise ReplayStepError(
                f"replay step {steps} at {instant.isoformat()} never ran: another replay holds "
                f"the {store.REPLAY_LOCK_KEY} lock")
        if stats.errors:
            raise ReplayStepError(
                f"replay step {steps} at {instant.isoformat()} failed: "
                f"{stats.last_error or 'see the executor log'}")
        grid.advance()
    log.info("replay executor stepped %s times over [%s, %s]", steps, first, last)
    return _replay_row_counts(session, variant.variant_id, first, last)


_REPLAY_COUNTS = text("""
select (select count(*) from orders o
        where o.replay = true and o.variant_id = :v
          and o.placed_at >= :first and o.placed_at <= :last) as orders,
       (select count(*) from fills f join orders o on o.id = f.order_id
        where o.replay = true and o.variant_id = :v
          and o.placed_at >= :first and o.placed_at <= :last) as fills
""")


def _replay_row_counts(session: Session, variant_id: str, first: datetime,
                       last: datetime) -> tuple[int, int]:
    """This range's replay orders and their fills, as the record holds them.

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
                             {"v": variant_id, "first": first, "last": last}).one()
    return int(row.orders), int(row.fills)
