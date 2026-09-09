"""The snapshot scheduler: one APScheduler job per snapshot name, inside `app-serve`.

Spec decision 2: the container that serves is the one that pays, and the recorder's tick budget
is untouched. APScheduler is already a pinned dependency (`apscheduler==3.11.3`), so this adds a
thread pair and no dependency.

Every job is `max_instances=1` and `coalesce=True`, with `misfire_grace_time` equal to its own
cadence: a build that overruns is never overlapped by the next one, and a scheduler that fell
behind catches up with one run rather than a queue of them.

Two cadences are decided per run rather than fixed. **Floor** is 15 s inside a game window and
60 s outside, from `game_window_open`, and backs off to 30 s in-window when its own p95 over the
last ten minutes exceeds its share of the CPU budget -- 250 ms, being 2 s per minute across
about eight builds a minute (spec §6, ruling A-I9). Pulse names that back-off out loud through
its `snapshot_budget` rule. **Ticket** is 15 s in a game window while a card is placed or alive,
and 60 s otherwise, because between cards there is nothing to update.

Every cadence number and the p95 budget are *imported*, never restated: the builder module that
puts the cadence in its own payload is the one home for it, and `FLOOR_P95_BUDGET_MS` lives in
`harness.dashboard.snapshots` because Pulse's `snapshot_budget` rule names the same number. A
scheduler that disagreed with either would be worse than either alone.

This module imports all five builder modules for their `register_builder` side effect. That is
load-bearing rather than tidy: registration happens at import, so without these imports nothing
is registered in a serving process and every job would raise on its first tick, while the
per-builder tests would still pass because each imports its own module.
"""

import logging
from datetime import datetime, timezone

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.dashboard import snapshots
from harness.dashboard.snapshots import FLOOR_P95_BUDGET_MS
# All five, for their `register_builder` side effect as much as for the cadences read below: a
# serving process that imported only some of them would raise on the first tick of the rest.
from harness.dashboard.snapshots import floor as _floor
from harness.dashboard.snapshots import gate as _gate
from harness.dashboard.snapshots import pulse as _pulse
from harness.dashboard.snapshots import study as _study
from harness.dashboard.snapshots import ticket as _ticket
from harness.dashboard.snapshots.pulse import FLOOR_P95_WINDOW
from harness.dashboard.snapshots.study import stale_study_names
from harness.dashboard.window import game_window_open

log = logging.getLogger(__name__)

#: Every cadence comes from the builder that writes it into its own payload, so the number the
#: job runs at and the number the surface reads can never drift apart.
PULSE_S = _pulse.CADENCE_S
GATE_S = _gate.CADENCE_S
STUDY_S = _study.CADENCE_S
FLOOR_IN_WINDOW_S = _floor.CADENCE_IN_WINDOW_S
FLOOR_OUT_S = _floor.CADENCE_OUT_S
TICKET_IN_WINDOW_S = _ticket.CADENCE_IN_WINDOW_S
TICKET_OUT_S = _ticket.CADENCE_OUT_S
#: Half the in-window rate, not a third value to tune: the back-off exists to hand the budget
#: back, and the only honest number to hand back is the one the surface was spending.
FLOOR_BACKOFF_S = FLOOR_IN_WINDOW_S * 2
#: Two threads: one for the job that is running, one for the job that is due.
EXECUTOR_THREADS = 2

#: The starting cadence of each job. Floor and Ticket start at their *out-of-window* rate and
#: are rescheduled by their first run: starting slow and speeding up costs one late build,
#: starting fast and slowing down spends the budget before anything has measured it.
CADENCES = {"pulse": PULSE_S, "gate": GATE_S, "study": STUDY_S,
            "floor": FLOOR_OUT_S, "ticket": TICKET_OUT_S}

#: The four jobs that build one name each. Study is its own job because it builds a list.
SIMPLE_JOBS = ("pulse", "floor", "gate", "ticket")

#: Character-for-character Pulse's own `_FLOOR_MS`, window constant included, because the two
#: must answer the same question: Pulse's `snapshot_budget` rule is the surface saying out loud
#: what this scheduler is doing, and a different `ts` bound here would let the page read WATCH
#: while the jobs ran flat out.
_FLOOR_MS = text("""
    select value from metric_samples
    where name = 'serve.snapshot_ms' and labels->>'name' = 'floor' and ts > :since
    order by value
""")
#: The same "a card that exists to be watched" vocabulary the Ticket builder's `_LIVE_CARDS`
#: head reads: `proposed` is waiting on a hand placement and `cashed`/`busted`/`void` are
#: settled, so neither is a reason to poll every 15 seconds.
_LIVE_CARD = text("""
    select exists (select 1 from parlay_cards where status in ('placed', 'alive'))
""")


class SnapshotScheduler:
    """The five jobs, their cadences and the two that move.

    `session_factory` is bound to the snapshot engine (`make_snapshot_engine`), never to the
    request path's engine: a builder must not be able to spend a request connection.
    """

    def __init__(self, session_factory: sessionmaker, settings: Settings) -> None:
        self._factory = session_factory
        self._settings = settings
        self._scheduler = BackgroundScheduler(
            executors={"default": ThreadPoolExecutor(EXECUTOR_THREADS)},
            timezone=timezone.utc)
        self._cadences: dict[str, int] = {}

    # -- cadence -------------------------------------------------------------------------

    def _floor_p95_ms(self, session: Session, now: datetime) -> float | None:
        """Floor's own build times over the last ten minutes, at the 95th percentile. `None`
        when nothing has been measured yet, which is not the same as "inside budget": a
        scheduler that has just started must not back off on an empty window."""
        values = [float(v) for v in session.execute(
            _FLOOR_MS, {"since": now - FLOOR_P95_WINDOW}).scalars()]
        if not values:
            return None
        return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]

    def cadence_for(self, name: str, session: Session, now: datetime) -> int:
        """The cadence this job should be running at right now."""
        if name == "floor":
            if not game_window_open(session, now):
                return FLOOR_OUT_S
            p95 = self._floor_p95_ms(session, now)
            # Strictly greater, the same comparison Pulse's `snapshot_budget` rule makes, so the
            # surface never says "over budget" while the scheduler is still running flat out or
            # the other way round.
            if p95 is not None and p95 > FLOOR_P95_BUDGET_MS:
                log.info("floor snapshot p95 %.0f ms over budget; backing off to %s s",
                         p95, FLOOR_BACKOFF_S)
                return FLOOR_BACKOFF_S
            return FLOOR_IN_WINDOW_S
        if name == "ticket":
            live = bool(session.execute(_LIVE_CARD).scalar())
            return TICKET_IN_WINDOW_S if live and game_window_open(session, now) \
                else TICKET_OUT_S
        return CADENCES[name]

    # -- jobs ----------------------------------------------------------------------------

    def _reschedule(self, name: str, cadence: int) -> None:
        """Move a job to a new cadence, grace time included. Never raises: a reschedule that
        loses a race with `shutdown` must not take the build that already succeeded with it."""
        if self._cadences.get(name) == cadence or not self._scheduler.running:
            self._cadences[name] = cadence
            return
        try:
            self._scheduler.modify_job(name, misfire_grace_time=cadence)
            self._scheduler.reschedule_job(name, trigger="interval", seconds=cadence)
        except Exception:  # noqa: BLE001 - the job may have gone during shutdown
            log.warning("rescheduling snapshot job %s to %s s failed", name, cadence)
            return
        self._cadences[name] = cadence
        log.info("snapshot job %s rescheduled to %s s", name, cadence)

    def run_once(self, name: str, now: datetime | None = None) -> dict:
        """Build one snapshot and, for the two moving cadences, reschedule the job when the
        cadence flipped. Never raises: `run_builder` records a failure as the row's `error` and
        a scheduler thread that dies takes the surface with it."""
        now = now or datetime.now(timezone.utc)
        try:
            with self._factory() as session:
                cadence = self.cadence_for(name, session, now)
        except Exception:  # noqa: BLE001 - an unreadable window is not a reason to skip a build
            log.exception("deciding the cadence for %s failed; keeping the current one", name)
            cadence = self._cadences.get(name, CADENCES[name])
        try:
            result = snapshots.run_builder(self._factory, name, now, self._settings, cadence)
        except Exception:  # noqa: BLE001 - a job must outlive one bad build
            log.exception("snapshot job %s failed outside run_builder", name)
            return {}
        self._reschedule(name, cadence)
        return result

    def run_study(self, now: datetime | None = None) -> list[str]:
        """The current ISO week, plus every week whose stored snapshot predates its newest
        report run (addendum §0.1). This is what makes "no page view runs a query" hold without
        exception: a closed week's snapshot is the scheduler's job, not a request's."""
        now = now or datetime.now(timezone.utc)
        iso = now.isocalendar()
        # Unpadded, matching `SNAPSHOT_NAME_RE`, `stale_study_names` and the Pulse rule.
        names = [f"study:{iso.year}-{iso.week}"]
        try:
            with self._factory() as session:
                names.extend(n for n in stale_study_names(session, now) if n not in names)
        except Exception:  # noqa: BLE001 - the current week is still worth building
            log.exception("listing the stale study weeks failed")
        for name in names:
            try:
                snapshots.run_builder(self._factory, name, now, self._settings, STUDY_S)
            except Exception:  # noqa: BLE001
                log.exception("study snapshot %s failed", name)
        return names

    # -- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        started_at = datetime.now(timezone.utc)
        for name in SIMPLE_JOBS:
            cadence = CADENCES[name]
            self._cadences[name] = cadence
            self._scheduler.add_job(self.run_once, "interval", seconds=cadence, args=[name],
                                    id=name, max_instances=1, coalesce=True,
                                    misfire_grace_time=cadence, next_run_time=started_at)
        self._scheduler.add_job(self.run_study, "interval", seconds=STUDY_S, id="study",
                                max_instances=1, coalesce=True, misfire_grace_time=STUDY_S,
                                next_run_time=started_at)
        self._cadences["study"] = STUDY_S
        self._scheduler.start()
        log.info("snapshot scheduler started: %s", sorted(self._cadences))

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
