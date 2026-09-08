"""The settlement job: a shared wall-clock budget, a stage registry, and the settler that runs
the stages under one budget and records what happened in `job_runs`.

Three things shape this module.

* **The registry is a list of module names, not of imports.** `STAGE_MODULES` names the modules
  that own stages; `load_stages()` imports each one and each module registers its stages at
  import. A later task appends its module name here and never imports a module that does not
  exist yet, so `job.py` is the one file every settlement task touches and the merge conflict is
  one line long.
* **One budget across every stage.** `settle_budget_s` is the whole job's wall clock, not each
  stage's, so a stage that finds it spent records `budget_exhausted` and yields rather than
  running the job past its period. Every write in every stage is idempotent, so the next run
  simply resumes.
* **A stage failure is not a job failure.** A raising stage rolls the session back, is recorded
  in that stage's `error`, and the next stage still runs. A settlement that cannot reach Kalshi
  must not stop the benchmarks from being computed.

The stage signature is `(session, now, budget) -> StageResult`. Anything else a stage needs --
the Kalshi client, the warnings and errors the job run collects -- travels in the job context
(`current_ctx()`), which the settler sets for the length of one run. The context is a
`ContextVar`, so a settler running on the scheduler's thread pool cannot see another's.
"""

import contextlib
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from importlib import import_module

from sqlalchemy.orm import Session

from harness import telemetry
from harness.db.models import JobRun

log = logging.getLogger(__name__)


class Budget:
    """A wall-clock allowance, checked between units of work.

    Pure in the sense the addendum means: it reads no clock of its own, only the `monotonic`
    it was handed, so a test can spend a budget exactly where it wants to. `remaining_s()` goes
    negative once the deadline is past; callers only ever compare it against a floor.
    """

    def __init__(self, seconds: float, monotonic: Callable[[], float]) -> None:
        self._mono = monotonic
        self._deadline = monotonic() + seconds

    def ok(self) -> bool:
        return self._mono() < self._deadline

    def remaining_s(self) -> float:
        return self._deadline - self._mono()


@dataclass
class StageResult:
    """What one stage did. `counts` goes into `job_runs.notes` verbatim, so it must be JSON."""

    name: str
    counts: dict = field(default_factory=dict)
    budget_exhausted: bool = False
    error: str | None = None

    def as_note(self) -> dict:
        return {"name": self.name, "counts": self.counts,
                "budget_exhausted": self.budget_exhausted, "error": self.error}


StageFn = Callable[[Session, datetime, Budget], StageResult]

#: Every registered stage, in registration order. Populated at import of the stage modules.
STAGES: list[tuple[str, StageFn]] = []

#: The modules `load_stages()` imports. A later task appends its own module name; Task 7 owns
#: the first entry. Never import a stage module from here directly: the import happens at run
#: time so a module that does not exist yet cannot break this one.
STAGE_MODULES: list[str] = [
    "harness.settlement.settle", "harness.settlement.benchmarks", "harness.settlement.order_clv",
    "harness.settlement.markouts", "harness.ops.housekeeping", "harness.settlement.report_wtd",
]

_CTX: ContextVar[dict | None] = ContextVar("settlement_job_ctx", default=None)


def register_stage(name: str, fn: StageFn) -> None:
    """Append one stage. Registering a name twice is a no-op, so a module imported again (or a
    `load_stages()` called twice) cannot double-run a stage."""
    if any(existing == name for existing, _ in STAGES):
        return
    STAGES.append((name, fn))


def load_stages() -> list[tuple[str, StageFn]]:
    """Import every module in `STAGE_MODULES` and return the registry. Idempotent: Python caches
    the import and `register_stage` refuses a duplicate name."""
    for module in STAGE_MODULES:
        import_module(module)
    return STAGES


def new_ctx(kalshi=None, settings=None) -> dict:
    """The per-run context every stage shares: the venue client, the warnings and errors that
    end up in `job_runs.notes`, (additive, Task 12) the job's `Settings`, for the stages that
    need a setting no other stage reads, and (additive, Task 12b) `job_run_id`, which
    `harness.ops.housekeeping` needs to key its `check_results` rows to. `Settler.run()` fills
    `job_run_id` in once the row has one; a stage called outside a job sees `None`."""
    return {"kalshi": kalshi, "warnings": [], "errors": [], "settings": settings, "job_run_id": None}


def current_ctx() -> dict:
    """The running job's context, or a throwaway one when a stage is called outside a job."""
    ctx = _CTX.get()
    return new_ctx() if ctx is None else ctx


@contextlib.contextmanager
def use_ctx(ctx: dict):
    token = _CTX.set(ctx)
    try:
        yield ctx
    finally:
        _CTX.reset(token)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Settler:
    """One settlement job. `run()` is the whole of it; the scheduler only calls it.

    The session comes from the factory the settler was built with, never from the recorder's:
    the settler runs on its own scheduler slot, on its own connection, with the batch statement
    timeout, and `job_runs` is its own row so nothing it writes can lose an update against the
    tick's `runs` row.
    """

    def __init__(self, settings, session_factory, kalshi=None,
                 clock: Callable[[], datetime] = _utcnow,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.s = settings
        self._factory = session_factory
        self._kalshi = kalshi
        self._clock = clock
        self._monotonic = monotonic

    def run(self) -> JobRun:
        from harness.settlement.settle import stale_unsettled

        stages = load_stages()
        now = self._clock()
        with self._factory() as session:
            row = JobRun(job="settle", started_at=now, status="running", notes={})
            session.add(row)
            session.commit()

            budget = Budget(self.s.settle_budget_s, self._monotonic)
            ctx = new_ctx(self._kalshi, self.s)
            # Task 12b: the one stage that writes check_results (harness.ops.housekeeping)
            # needs this run's own id to key its rows to.
            ctx["job_run_id"] = row.id
            results: list[StageResult] = []
            with use_ctx(ctx):
                for name, fn in stages:
                    result = self._run_stage(session, name, fn, now, budget)
                    results.append(result)
                    if name == "settle":
                        # Task 12b: one equity_snapshots row per exec variant right after the
                        # stage that can move the ledger, whether or not it errored -- the
                        # settler has no live book, so mtm_open is always None here (the
                        # executor's own sample is the one that can mark against a book).
                        try:
                            _write_settle_equity_snapshots(session, now, self.s)
                            session.commit()
                        except Exception:  # noqa: BLE001 - ruling 1
                            log.exception("settle equity snapshots failed")
                            session.rollback()

            try:
                for result in results:
                    if result.error is not None:
                        telemetry.event(session, "settle_error", f"{result.name}: {result.error}",
                                        ref={"stage": result.name}, ts=now)
                    if result.budget_exhausted:
                        telemetry.event(session, "budget_exhausted", result.name,
                                        ref={"stage": result.name}, ts=now)
                session.commit()
            except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails the job
                log.exception("settle telemetry events failed")
                session.rollback()

            stale = self._stale(session, stale_unsettled, now, ctx)
            row.finished_at = self._clock()
            row.status = _status(results, ctx["errors"])
            row.budget_exhausted = any(r.budget_exhausted for r in results)
            row.notes = {"stages": [r.as_note() for r in results],
                         "stale_unsettled": stale,
                         "warnings": ctx["warnings"], "errors": ctx["errors"]}
            session.commit()
            log.info("settle job %s status=%s budget_exhausted=%s stages=%s",
                     row.id, row.status, row.budget_exhausted,
                     ", ".join(r.name for r in results))
            return row

    def _run_stage(self, session: Session, name: str, fn: StageFn, now: datetime,
                   budget: Budget) -> StageResult:
        try:
            result = fn(session, now, budget)
            session.commit()
            return result
        except Exception as exc:  # noqa: BLE001 - one stage must not take the job down
            session.rollback()
            log.exception("settlement stage %s failed", name)
            return StageResult(name=name, error=f"{type(exc).__name__}: {exc}"[:2000])

    def _stale(self, session: Session, fn, now: datetime, ctx: dict) -> int | None:
        """`stale_unsettled` is a counter on the job run, not a stage; a failure there must not
        cost the job the notes of every stage that did run."""
        try:
            return fn(session, now)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            log.exception("stale_unsettled failed")
            ctx["errors"].append({"stale_unsettled": f"{type(exc).__name__}: {exc}"[:500]})
            return None


def _write_settle_equity_snapshots(session: Session, now: datetime, settings) -> None:
    """One `equity_snapshots` row per exec variant, right after the `settle` stage (design
    spec §3.4). `mtm_open` is always None here and `mtm_coverage` is always 0: the settler has
    no live book at all, unlike the executor's own periodic sample."""
    from harness.execution import store as exec_store

    variant_ids = exec_store.resolve_variants(session, settings.exec_variants)
    if not variant_ids:
        return
    cfg = exec_store.variant_configs(session, variant_ids)
    for variant_id in variant_ids:
        bankroll = Decimal(str(cfg.get(variant_id, {}).get("bankroll", 0)))
        cash = bankroll + exec_store.ledger_cash_delta(session, variant_id)
        positions = [p for p in exec_store.positions_for_variant(session, variant_id)
                    if p.open_contracts and p.avg_price is not None]
        open_stake = sum((p.open_contracts * p.avg_price for p in positions), Decimal("0"))
        n_open_orders = exec_store.count_variant_open_orders(session, variant_id, False)
        exec_store.insert_equity_snapshot(
            session, ts=now, variant_id=variant_id, cash=cash, open_stake=open_stake,
            mtm_open=None, mtm_coverage=Decimal("0"), n_open_positions=len(positions),
            n_open_orders=n_open_orders)


def _status(results: list[StageResult], ctx_errors: list) -> str:
    """`error` only when every stage failed: one broken stage beside four healthy ones is a
    degraded job, and the dashboard's red is reserved for a job that achieved nothing.

    `ctx_errors` is what keeps a pass that settled nothing from reading `ok`. A stage that
    isolates its own failures -- `settle` per game, `venue_result` per ticker -- returns without
    an `error` however many units failed inside it, so the units' errors are the only signal
    that something went wrong, and `verify.md` switches on this column rather than on `notes`.
    A failing `stale_unsettled` lands in the same list and degrades the run for the same reason.
    """
    failed = [r for r in results if r.error is not None]
    if results and len(failed) == len(results):
        return "error"
    return "degraded" if failed or ctx_errors else "ok"
