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
import os
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness import telemetry
from harness.ops.checks import run_checks
from harness.recorder.store import get_source_state, set_source_state
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

#: Same convention `harness/dashboard/app.py` uses for a market's sport, since VenueMarket has
#: no `sport` column of its own.
SPORT_PREFIXES = {"nfl": "KXNFL", "ncaaf": "KXNCAAF"}

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

#: Fix 38 (journal 110): how long an unquoted `rfqs` row is kept. The flood that incident wrote
#: (74,608 rows in seven minutes) ages out of the table this way rather than through a one-time
#: cleanup -- there is nothing here that treats today's rows differently from any other day's.
RFQ_RETENTION_DAYS = 7
#: Deleted in batches this size, one batch per housekeeping run -- the same reason every bulk
#: write in this codebase is bounded rather than run whole: a single unbounded `DELETE` against
#: a table the flood left at 128 MB (and, unpruned, growing) is a lock a settlement pass cannot
#: afford to wait behind.
RFQ_PRUNE_BATCH = 5000

#: `pg_total_relation_size` on a partitioned parent (relkind 'p') reports 0 -- the parent owns
#: no storage itself, its partitions do -- so every relation is joined back to its top-level
#: parent through `pg_inherits` (a non-partitioned table, and a partitioned parent's own catalog
#: row, both have no `pg_inherits` row and so keep their own name via the `coalesce`). Grouping
#: on that name rolls every partition's bytes up under the one logical table name an operator
#: cares about, rather than a same-week partition's own name (e.g. `orderbook_events_y2026w37`)
#: or a parent that always reports 0 (fix round 1, M2).
_TABLE_SIZES = text("""
    select coalesce(parent.relname, c.relname) as relname, pg_total_relation_size(c.oid) as bytes
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    left join pg_inherits i on i.inhrelid = c.oid
    left join pg_class parent on parent.oid = i.inhparent
    where n.nspname = 'public' and c.relkind in ('r', 'p')
""")

_DATABASE_SIZE = text("select pg_database_size(current_database())")

#: Fix 38: the bounded retention delete. `not exists (... rfq_quotes ...)` rather than a join,
#: since a row can only ever have at most one `rfq_quotes` row (`uq_rfq_quote_rfq`) and this
#: reads as exactly the rule it is -- "never quoted" -- without a `distinct` to guard against a
#: join fan-out that cannot happen anyway. Postgres has no `DELETE ... LIMIT`; the bounded
#: subquery is the standard way to cap one, and its own `limit` is exactly `RFQ_PRUNE_BATCH`.
_PRUNE_RFQS = text("""
    delete from rfqs
    where id in (
        select r.id
        from rfqs r
        where r.received_at < :cutoff
          and not exists (select 1 from rfq_quotes q where q.rfq_id = r.id)
        limit :batch
    )
""")

#: Every physical BRIN index (never a partitioned parent, `relkind = 'i'` only --
#: `brin_summarize_new_values` is undefined on a partitioned index's own relation, same
#: restriction as the `relkind` guard in `harness/db/schema.py`'s autosummarize sweep).
_BRIN_INDEXES = text("""
    select c.relname
    from pg_class c
    join pg_am am on am.oid = c.relam
    where am.amname = 'brin' and c.relkind = 'i'
""")

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


def _table_sizes_gb(session: Session) -> dict[str, float]:
    """Every table's total size in GB, keyed by its logical (partition-rolled-up) name."""
    totals: dict[str, int] = {}
    for relname, nbytes in session.execute(_TABLE_SIZES).all():
        totals[relname] = totals.get(relname, 0) + nbytes
    return {name: round(nbytes / BYTES_PER_GB, 4) for name, nbytes in totals.items()}


#: A market's own liveness window (mirrors the dashboard's match-report section): only markets
#: this recorder has actually seen recently count towards the rate.
MATCH_WINDOW = timedelta(hours=24)


def match_rates(session: Session, now: datetime) -> dict[str, dict]:
    """Matched share and unmatched count per sport, over `venue_markets` seen in the last 24h --
    the same shape the dashboard's match-report section already computes, kept here too since
    `match.rate{sport}`/`match.unmatched{sport}` (design spec §3.1) is a metric, not a page."""
    cutoff = now - MATCH_WINDOW
    out: dict[str, dict] = {}
    for sport, prefix in SPORT_PREFIXES.items():
        counts = dict(session.execute(text(
            "select match_status, count(*) from venue_markets "
            "where series_ticker like :prefix and last_seen_at >= :cutoff group by match_status"),
            {"prefix": f"{prefix}%", "cutoff": cutoff}).all())
        total = sum(counts.values())
        matched = counts.get("matched", 0) + counts.get("fuzzy", 0) + counts.get("manual", 0)
        unmatched = counts.get("unmatched", 0)
        out[sport] = {"rate": (matched / total) if total else None, "unmatched": unmatched}
    return out


def _host_disk_gb(mount) -> tuple[float | None, float | None, str | None]:
    """`os.statvfs` on the Postgres data mount, as (free_gb, total_gb, note).

    The total is what makes Pulse's 25 % rule (harness.health.DISK_FREE_MIN_FRACTION) a number
    rather than an opinion: free gigabytes alone cannot be turned into a share (ruling A-C2).
    Both come from one statvfs, so there is no window in which they disagree.

    `skip` with a note when the mount is absent (ruling 3: the Mac and every test have no such
    mount, and that is never an error). Both are skipped together: a free reading with no total
    would let the rule divide by nothing.
    """
    try:
        st = os.statvfs(mount)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None, None, "mount absent"
    return ((st.f_bavail * st.f_frsize) / BYTES_PER_GB,
            (st.f_blocks * st.f_frsize) / BYTES_PER_GB,
            None)


def _host_mem_available_mb() -> tuple[float | None, str | None]:
    """`/proc/meminfo`'s `MemAvailable`; absent on macOS and skipped there too (ruling 3)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / 1024, None
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return None, "meminfo absent"
    return None, "MemAvailable not reported"


def _brin_ranges_summarized(session: Session) -> int:
    """Fix 32: the sum of `brin_summarize_new_values` over every physical BRIN index. Belt and
    braces alongside `autosummarize` (`harness/db/schema.py`): once autosummarize keeps up this
    is cheap (nothing left to do, every call returns 0), so it is a fine daily habit even though
    the failure mode it exists for -- a vacuum that fell behind -- should now be rare.

    Each index is summarized in its own SAVEPOINT so one that was dropped between the catalog
    read and the call (a weekly partition rotated out from under this) costs that index's
    contribution to the sum, not the sample, not the stage."""
    total = 0
    for name in session.execute(_BRIN_INDEXES).scalars().all():
        try:
            with session.begin_nested():
                total += session.execute(text(
                    "select brin_summarize_new_values(:name::regclass)"),
                    {"name": name}).scalar() or 0
        except Exception:  # noqa: BLE001 - a BRIN dropped between the list and the call
            log.info("brin_ranges_summarized: skipped %s (no longer exists?)", name)
    return total


def _prune_rfqs(session: Session, now: datetime) -> int:
    """Fix 38 (journal 110): every `rfqs` row older than `RFQ_RETENTION_DAYS` with no
    `rfq_quotes` row, deleted in one bounded batch of `RFQ_PRUNE_BATCH` per housekeeping run.

    A quoted row is retention's whole point (F71: the row **is** the record of what we would
    have answered), so `_PRUNE_RFQS`'s `not exists` guard means this rule can never touch one --
    only an arrival that was declined or never reached `compute_quote` at all. The flood the
    incident wrote (74,608 rows, most never quoted) ages out through this rule once each row
    passes seven days old; there is no one-time cleanup, and after fix 38's boundary filter a
    non-football frame never reaches `rfqs` in the first place, so what this prunes going
    forward is ordinary attrition, not a backlog.
    """
    cutoff = now - timedelta(days=RFQ_RETENTION_DAYS)
    result = session.execute(_PRUNE_RFQS, {"cutoff": cutoff, "batch": RFQ_PRUNE_BATCH})
    return result.rowcount or 0


def record_housekeeping_metrics(session: Session, now: datetime, counts: dict,
                                match_by_sport: dict[str, dict], pg_data_mount) -> int:
    """Every `metric_samples` row housekeeping writes (design spec §3.1), as one batch."""
    samples: list[tuple[str, object, dict]] = [
        ("db.size_gb", counts["size_gb"], {}),
        ("db.brin_ranges_summarized", _brin_ranges_summarized(session), {}),
        ("db.rfqs_pruned", _prune_rfqs(session, now), {}),
    ]
    for table, gb in counts.get("tables_gb", {}).items():
        samples.append(("db.table_gb", gb, {"table": table}))
    if counts.get("growth_gb_per_day") is not None:
        samples.append(("db.growth_gb_per_day", counts["growth_gb_per_day"], {}))
    disk_free_gb, disk_total_gb, disk_note = _host_disk_gb(pg_data_mount)
    if disk_free_gb is not None and disk_total_gb is not None:
        samples.append(("host.disk_free_gb", disk_free_gb, {}))
        samples.append(("host.disk_total_gb", disk_total_gb, {}))
    else:
        log.info("host.disk_free_gb/host.disk_total_gb skipped: %s", disk_note)
    mem_mb, mem_note = _host_mem_available_mb()
    if mem_mb is not None:
        samples.append(("host.mem_available_mb", mem_mb, {}))
    else:
        log.info("host.mem_available_mb skipped: %s", mem_note)
    for sport, rates in match_by_sport.items():
        if rates["rate"] is not None:
            samples.append(("match.rate", rates["rate"], {"sport": sport}))
        samples.append(("match.unmatched", rates["unmatched"], {"sport": sport}))
    return telemetry.record_many(session, "housekeeping", samples, ts=now)


def housekeeping(session: Session, now: datetime, db_budget_gb: int) -> dict:
    """Measure the database, project its growth against `db_budget_gb`, and return the dict the
    settlement job records verbatim as this stage's `StageResult.counts` (and the dashboard's
    database-ceiling section reads back out of the newest such note)."""
    all_tables_gb = _table_sizes_gb(session)
    tables_gb = dict(sorted(all_tables_gb.items(), key=lambda kv: kv[1], reverse=True)[:6])
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
    settlement budget on, so `budget` is accepted only to match `StageFn` and is never checked.

    Task 12b additions, all best-effort (ruling 1: a telemetry failure must never fail this
    stage): the `metric_samples` batch, the Layer 2b check registry (`check_results`, one row
    per check) and one `operator_events(check_failed)` row per failing check.
    """
    del budget
    last = get_source_state(session, JOB_STATE_KEY)
    if not _due(last, now):
        return StageResult("housekeeping", {"skipped": True}, False, None)

    ctx = current_ctx()
    settings = ctx.get("settings")
    db_budget_gb = settings.db_budget_gb if settings is not None else DEFAULT_DB_BUDGET_GB
    pg_data_mount = settings.pg_data_mount if settings is not None else "/pgdata-ro"
    counts = housekeeping(session, now, db_budget_gb)
    set_source_state(session, JOB_STATE_KEY, now)

    try:
        with session.begin_nested():
            match_by_sport = match_rates(session, now)
            record_housekeeping_metrics(session, now, counts, match_by_sport, pg_data_mount)
    except Exception:  # noqa: BLE001 - telemetry must never fail this stage
        log.exception("housekeeping metrics failed")

    try:
        with session.begin_nested():
            results = run_checks(session, now, ctx.get("job_run_id"))
            for result in results:
                if result.status == "fail":
                    telemetry.event(
                        session, "check_failed", result.check_name,
                        ref={"value": None if result.value is None else float(result.value),
                            "threshold": result.threshold}, ts=now)
    except Exception:  # noqa: BLE001 - telemetry must never fail this stage
        log.exception("housekeeping checks failed")

    return StageResult("housekeeping", counts, False, None)


register_stage("housekeeping", housekeeping_stage)
