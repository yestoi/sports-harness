"""Daily database housekeeping: table and database sizes, and a projection of how many days
are left before the harness's 2 TB storage ceiling (`Settings.db_budget_gb`, D9/U3).

`ceiling_projection` is the one pure decision (ruling 1's class): given the trailing sizes a
caller has already read from prior housekeeping notes and the size just measured, it is the
whole of the growth-rate arithmetic, and is tested without a database at all. `housekeeping`
does the measuring -- `pg_total_relation_size` for the six largest tables, `pg_database_size`
for the whole database -- and reads its own prior notes back out of `job_runs` (the same table
`Settler.run()` already writes one row of `notes` to per pass), so the projection has something
to compare against without a dedicated table of its own.

`housekeeping_stage` is the once-a-day gate around `housekeeping`, registered on the settlement
job like every other stage in `STAGE_MODULES`. "Once a day" is a cadence over the same
`source_state` mechanism the recorder already uses for its own per-source intervals
(`harness/recorder/store.py` `get_source_state`/`set_source_state`) rather than a bespoke
mechanism or the `job_state` table (which is shaped for a resumable integer cursor, not a
last-run timestamp) -- one key, `housekeeping_last`, under the same table every other cadence in
the harness already reads and writes.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.recorder.store import get_source_state, set_source_state
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

#: The `source_state` key this stage's once-a-day gate reads and writes.
JOB_STATE_KEY = "housekeeping_last"
#: The setting's own default (Settings.db_budget_gb), used only when a stage runs with no
#: settings in its context (should not happen via Settler.run(), which always threads one
#: through; a defensive fallback all the same).
DEFAULT_DB_BUDGET_GB = 2000
#: Housekeeping is due at or after this UTC hour, once per calendar day (brief: "runs once per
#: day at or after 09:00 UTC").
DUE_HOUR_UTC = 9
#: How far back `housekeeping` looks for its own prior notes to measure growth against.
LOOKBACK = timedelta(days=7)
#: Fewer than this many notes (the one being written included) makes the projection `partial`.
FULL_WINDOW_NOTES = 7
BYTES_PER_GB = 1024 ** 3

_LARGEST_TABLES = text("""
    select c.relname, pg_total_relation_size(c.oid) as bytes
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
    order by bytes desc
    limit 6
""")

_DATABASE_SIZE = text("select pg_database_size(current_database())")

#: Bounded to `LOOKBACK`: a week of hourly settlement passes is at most a few hundred rows, and
#: only the ones carrying a (non-skipped) housekeeping note contribute a data point.
_TRAILING_JOB_RUNS = text("""
    select started_at, notes
    from job_runs
    where job = 'settle' and started_at >= :since and started_at < :now
    order by started_at asc
""")


def ceiling_projection(prior: list[tuple[datetime, float]], now: datetime, size_gb: float,
                       db_budget_gb: int) -> dict:
    """The growth-rate projection from today's database size and its prior sizes.

    `prior` is `(started_at, size_gb)` pairs from earlier housekeeping notes, oldest first,
    already bounded to the trailing window by the caller. The rate is a straight line through
    the oldest prior point and today's measurement -- the two ends of whatever window is
    available -- not a fit through every point, so one prior note is enough to produce a rate.

    `partial` counts today's note among the total: with no prior note at all (the very first
    run), there is nothing to compare against, so `growth_gb_per_day` and `days_to_ceiling` are
    both None and the day is trivially partial; `days_to_ceiling` is also None whenever the
    computed growth is exactly zero (the ratio is undefined, not infinite).
    """
    partial = (len(prior) + 1) < FULL_WINDOW_NOTES
    if not prior:
        return {"growth_gb_per_day": None, "days_to_ceiling": None, "partial": partial}
    oldest_at, oldest_size_gb = prior[0]
    elapsed_days = (now - oldest_at).total_seconds() / 86400
    if elapsed_days <= 0:
        return {"growth_gb_per_day": None, "days_to_ceiling": None, "partial": partial}
    growth_gb_per_day = (size_gb - oldest_size_gb) / elapsed_days
    days_to_ceiling = ((db_budget_gb - size_gb) / growth_gb_per_day) if growth_gb_per_day else None
    return {"growth_gb_per_day": growth_gb_per_day, "days_to_ceiling": days_to_ceiling, "partial": partial}


def _prior_sizes(session: Session, now: datetime) -> list[tuple[datetime, float]]:
    """Every prior day's `size_gb`, oldest first, from this stage's own notes on `job_runs`
    inside the trailing window. A run whose housekeeping stage was skipped that day carries no
    `size_gb` in its counts and contributes nothing."""
    rows = session.execute(_TRAILING_JOB_RUNS, {"since": now - LOOKBACK, "now": now}).all()
    out: list[tuple[datetime, float]] = []
    for started_at, notes in rows:
        for stage in (notes or {}).get("stages", []):
            if stage.get("name") != "housekeeping":
                continue
            size_gb = (stage.get("counts") or {}).get("size_gb")
            if size_gb is not None:
                out.append((started_at, size_gb))
    return out


def housekeeping(session: Session, now: datetime, db_budget_gb: int) -> dict:
    """Measure the database, project its growth against `db_budget_gb`, and return the dict the
    settlement job records verbatim as this stage's `StageResult.counts` (and the dashboard's
    database-ceiling section reads back out of the newest such note)."""
    tables_gb = {name: round(nbytes / BYTES_PER_GB, 4)
                 for name, nbytes in session.execute(_LARGEST_TABLES).all()}
    db_bytes = session.execute(_DATABASE_SIZE).scalar_one()
    size_gb = db_bytes / BYTES_PER_GB

    projection = ceiling_projection(_prior_sizes(session, now), now, size_gb, db_budget_gb)
    return {"size_gb": round(size_gb, 4), "tables_gb": tables_gb, **projection}


def _due(last: datetime | None, now: datetime) -> bool:
    if now.hour < DUE_HOUR_UTC:
        return False
    return last is None or last.date() < now.date()


def housekeeping_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    """The once-a-day gate: a handful of catalog queries never worth spending the shared
    settlement budget on, so `budget` is accepted only to match `StageFn` and is never checked."""
    del budget
    last = get_source_state(session, JOB_STATE_KEY)
    if not _due(last, now):
        return StageResult("housekeeping", {"skipped": True}, False, None)

    settings = current_ctx().get("settings")
    db_budget_gb = settings.db_budget_gb if settings is not None else DEFAULT_DB_BUDGET_GB
    counts = housekeeping(session, now, db_budget_gb)
    set_source_state(session, JOB_STATE_KEY, now)
    return StageResult("housekeeping", counts, False, None)


register_stage("housekeeping", housekeeping_stage)
