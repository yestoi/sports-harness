"""Daily database housekeeping: size measurement, the growth/ceiling projection, and the
once-a-day gate that runs it as a settlement stage.

`ceiling_projection` is pure (ruling 1's class of decision): given the trailing sizes a caller
has already read and the size just measured, it is the whole of the arithmetic the brief pins
(100 GB, +10 GB/day, ceiling 2000 gives 190 days), tested with no database at all.
`housekeeping` and `housekeeping_stage` run against the real schema, because `pg_database_size`
and `pg_total_relation_size` are properties of the actual database, not of the Python around
them.
"""

import os
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import CheckResult, JobRun, MetricSample
from harness.ops.housekeeping import (
    DUE_HOUR_UTC,
    JOB_STATE_KEY,
    ceiling_projection,
    housekeeping,
    housekeeping_stage,
    match_rates,
    record_housekeeping_metrics,
    _host_disk_gb,
    _host_mem_available_mb,
    _table_sizes_gb,
)
from harness.recorder.store import get_source_state, set_source_state
from harness.settlement.job import Settler, load_stages, new_ctx, use_ctx

NOW = datetime(2026, 9, 12, 9, 30, tzinfo=timezone.utc)  # at/after 09:00 UTC: due today


class Mono:
    def __init__(self, *values: float) -> None:
        self.values = list(values) or [0.0]
        self.i = 0

    def __call__(self) -> float:
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return value


# --- ceiling_projection: pure, no database -----------------------------------------------


def test_days_to_ceiling_arithmetic_and_partial_flag():
    # One prior note, one day ago, at 90 GB; the database measures 100 GB now -> +10 GB/day.
    prior = [(NOW - timedelta(days=1), 90.0)]
    out = ceiling_projection(prior, NOW, 100.0, 2000)
    assert out["growth_gb_per_day"] == 10.0
    assert out["days_to_ceiling"] == 190.0
    # Only two notes total (this one plus the one prior note): fewer than seven, so partial.
    assert out["partial"] is True


def test_days_to_ceiling_partial_flag_clears_at_seven_days_of_notes():
    # Six prior notes spanning six days, each a day apart, growing 10 GB/day; today's the 7th.
    prior = [(NOW - timedelta(days=d), 100.0 - d * 10) for d in range(6, 0, -1)]
    out = ceiling_projection(prior, NOW, 100.0, 2000)
    assert out["growth_gb_per_day"] == 10.0
    assert out["days_to_ceiling"] == 190.0
    assert out["partial"] is False


def test_ceiling_projection_with_three_days_of_notes_flags_partial():
    prior = [(NOW - timedelta(days=d), 100.0 - d * 10) for d in range(3, 0, -1)]
    out = ceiling_projection(prior, NOW, 100.0, 2000)
    assert out["growth_gb_per_day"] == 10.0
    assert out["partial"] is True


def test_ceiling_projection_first_run_has_no_prior_note():
    out = ceiling_projection([], NOW, 55.0, 2000)
    assert out == {"growth_gb_per_day": None, "days_to_ceiling": None, "partial": True}


def test_ceiling_projection_zero_growth_is_no_projection():
    prior = [(NOW - timedelta(days=1), 100.0)]  # unchanged size -> zero growth
    out = ceiling_projection(prior, NOW, 100.0, 2000)
    assert out["growth_gb_per_day"] == 0.0
    assert out["days_to_ceiling"] is None


# --- housekeeping(): the real database ----------------------------------------------------


def test_housekeeping_measures_sizes_with_no_prior_note(db_session):
    out = housekeeping(db_session, NOW, 2000)
    assert out["size_gb"] > 0
    assert isinstance(out["tables_gb"], dict) and len(out["tables_gb"]) <= 6
    assert out["growth_gb_per_day"] is None
    assert out["days_to_ceiling"] is None
    assert out["partial"] is True


def test_housekeeping_computes_growth_from_a_prior_job_run_note(db_session):
    prior_notes = {"stages": [{"name": "housekeeping",
                              "counts": {"size_gb": 0.000001, "tables_gb": {}, "growth_gb_per_day": None,
                                         "days_to_ceiling": None, "partial": True},
                              "budget_exhausted": False, "error": None}]}
    db_session.add(JobRun(job="settle", started_at=NOW - timedelta(days=1), finished_at=NOW - timedelta(days=1),
                          status="ok", notes=prior_notes))
    db_session.commit()

    out = housekeeping(db_session, NOW, 2000)
    # The real test database is far larger than the microscopic prior note, so growth is
    # strongly positive and a ceiling projection exists.
    assert out["growth_gb_per_day"] > 0
    assert out["days_to_ceiling"] is not None
    assert out["partial"] is True  # only one prior note


# --- housekeeping_stage(): the once-a-day gate --------------------------------------------


def test_housekeeping_stage_skips_before_the_due_hour(db_session):
    before_due = NOW.replace(hour=DUE_HOUR_UTC - 1, minute=0)
    result = housekeeping_stage(db_session, before_due, budget=None)
    assert result.counts == {"skipped": True}
    assert get_source_state(db_session, JOB_STATE_KEY) is None


def test_housekeeping_stage_runs_once_then_skips_the_rest_of_the_day(db_session):
    first = housekeeping_stage(db_session, NOW, budget=None)
    assert first.counts.get("skipped") is not True
    assert "size_gb" in first.counts
    assert get_source_state(db_session, JOB_STATE_KEY) == NOW

    later_same_day = NOW + timedelta(hours=3)
    second = housekeeping_stage(db_session, later_same_day, budget=None)
    assert second.counts == {"skipped": True}


def test_housekeeping_stage_runs_again_the_next_day(db_session):
    set_source_state(db_session, JOB_STATE_KEY, NOW)
    next_day = NOW + timedelta(days=1)
    result = housekeeping_stage(db_session, next_day, budget=None)
    assert result.counts.get("skipped") is not True
    assert get_source_state(db_session, JOB_STATE_KEY) == next_day


# --- integration: registered on the settlement job, settings threaded through ctx --------


def test_housekeeping_runs_on_the_settlement_job_once_per_day(db_session, env_settings):
    names = [name for name, _ in load_stages()]
    assert "housekeeping" in names

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    row = Settler(env_settings, factory, None, clock=lambda: NOW, monotonic=Mono(0.0)).run()

    stage = next(s for s in row.notes["stages"] if s["name"] == "housekeeping")
    assert stage["error"] is None
    assert "size_gb" in stage["counts"]

    # A second run the same day (same "day" in the recorded source_state) skips housekeeping.
    row2 = Settler(env_settings, factory, None, clock=lambda: NOW + timedelta(hours=2),
                   monotonic=Mono(0.0)).run()
    stage2 = next(s for s in row2.notes["stages"] if s["name"] == "housekeeping")
    assert stage2["counts"] == {"skipped": True}


# --- table sizes: partition children roll up to their logical table name (fix round 1, M2) ---


def test_table_sizes_roll_up_partition_children_to_the_logical_table_name(db_session):
    """`pg_total_relation_size` on a partitioned parent (relkind 'p') reports 0 -- it owns no
    storage itself, its children do -- so without a `pg_inherits` rollup a partitioned table's
    real size would either vanish from the list or surface under a same-week partition's own
    name (e.g. `orderbook_events_y2026w37`) instead of the logical table an operator cares
    about. `ensure_partitions` (conftest._schema) guarantees `orderbook_events`, `venue_trades`
    and `raw_responses` each have at least one partition in this test database."""
    sizes = _table_sizes_gb(db_session)

    for logical_name in ("orderbook_events", "venue_trades", "raw_responses"):
        assert logical_name in sizes

    partition_child = re.compile(r"_y\d{4}w\d{2}$")
    assert not any(partition_child.search(name) for name in sizes)


# --- Task 12b telemetry ----------------------------------------------------------------


def test_housekeeping_host_metrics_skip_when_paths_absent(db_session):
    """Ruling 3: `host.disk_free_gb`/`host.disk_total_gb` (no `/pgdata-ro` mount) and
    `host.mem_available_mb` (no `/proc/meminfo`, i.e. every Mac and every test) are skipped, not
    errors -- the rest of the batch (db.*, match.*) still writes."""
    free, total, note = _host_disk_gb("/no/such/mount")
    assert free is None and total is None and note

    mem, mem_note = _host_mem_available_mb()
    # This suite runs on the Mac (and any CI without /proc/meminfo); on real Linux this would
    # be populated, so only assert the "absent" branch when it is actually absent.
    if not os.path.exists("/proc/meminfo"):
        assert mem is None and mem_note

    counts = housekeeping(db_session, NOW, 2000)
    match_by_sport = match_rates(db_session, NOW)
    n = record_housekeeping_metrics(db_session, NOW, counts, match_by_sport, "/no/such/mount")
    db_session.flush()

    names = {r.name for r in db_session.query(MetricSample).filter_by(source="housekeeping").all()}
    assert "db.size_gb" in names
    assert "db.brin_ranges_summarized" in names  # fix 32
    assert "host.disk_free_gb" not in names  # skipped: no mount
    assert "host.disk_total_gb" not in names  # skipped together (ruling A-C2)
    if not os.path.exists("/proc/meminfo"):
        assert "host.mem_available_mb" not in names
    assert n == db_session.query(MetricSample).filter_by(source="housekeeping").count()


# --- fix 32: db.brin_ranges_summarized ----------------------------------------------------

def test_brin_ranges_summarized_counts_real_brin_indexes(db_session):
    """The schema's own BRIN indexes (autosummarize keeps up, so this is typically 0 -- nothing
    left to summarize -- but it must run without error against the real catalog)."""
    from harness.ops.housekeeping import _brin_ranges_summarized

    total = _brin_ranges_summarized(db_session)
    assert isinstance(total, int)
    assert total >= 0


def test_brin_ranges_summarized_survives_a_dropped_index(db_session, monkeypatch):
    """A BRIN the catalog read named but that is gone by the time it is summarized -- a weekly
    partition rotated out from under this -- costs its own contribution, not the whole sample
    (brief: "housekeeping records the sample and survives a BRIN that no longer exists")."""
    from harness.ops import housekeeping as hk

    monkeypatch.setattr(hk, "_BRIN_INDEXES", text(
        "select relname from (values ('ix_raw_fetched_brin'), "
        "('fix_32_does_not_exist_brin_idx')) as t(relname)"))
    total = hk._brin_ranges_summarized(db_session)
    assert isinstance(total, int)
    assert total >= 0
    # The savepoint for the missing index rolled back to a live transaction, not a poisoned one.
    db_session.execute(text("select 1")).scalar()


def test_disk_total_is_recorded_beside_disk_free(db_session, monkeypatch):
    """A percentage is not derivable from free gigabytes alone (ruling A-C2). One additive
    metric name from the same statvfs call."""
    from harness.ops import housekeeping as hk

    monkeypatch.setattr(hk, "_host_disk_gb", lambda mount: (250.0, 1000.0, None))
    now = datetime.now(timezone.utc)
    hk.record_housekeeping_metrics(db_session, now, {"size_gb": 12.5, "tables_gb": {}},
                                   {}, pg_data_mount="/pgdata-ro")
    db_session.flush()

    names = {r.name: float(r.value) for r in db_session.query(MetricSample).all()}
    assert names["host.disk_free_gb"] == 250.0
    assert names["host.disk_total_gb"] == 1000.0


def test_neither_disk_metric_is_written_when_the_mount_is_absent(db_session, monkeypatch):
    """Ruling 3: the Mac and every test have no such mount, and that is a skip, not an error --
    and it must skip *both*, so the Pulse disk rule reads `not evaluated` rather than dividing
    by a total it does not have."""
    from harness.ops import housekeeping as hk

    monkeypatch.setattr(hk, "_host_disk_gb", lambda mount: (None, None, "mount absent"))
    now = datetime.now(timezone.utc)
    hk.record_housekeeping_metrics(db_session, now, {"size_gb": 12.5, "tables_gb": {}},
                                   {}, pg_data_mount="/pgdata-ro")
    db_session.flush()

    names = {r.name for r in db_session.query(MetricSample).all()}
    assert "host.disk_free_gb" not in names
    assert "host.disk_total_gb" not in names


def test_housekeeping_stage_writes_metrics_and_check_results(db_session, env_settings):
    """The stage writes its `metric_samples` batch and the Layer 2b `check_results` registry
    (one row per check) when it actually runs, both best-effort so neither can fail the stage
    the way a raise from `run_checks` would (ruling 1)."""
    job = JobRun(job="settle", started_at=NOW, status="running", notes={})
    db_session.add(job)
    db_session.commit()

    ctx = new_ctx(settings=env_settings)
    ctx["job_run_id"] = job.id
    with use_ctx(ctx):
        result = housekeeping_stage(db_session, NOW, budget=None)
    db_session.commit()

    assert result.counts.get("skipped") is not True
    assert db_session.query(MetricSample).filter_by(source="housekeeping").count() > 0
    from harness.ops.checks import CHECKS

    check_rows = db_session.query(CheckResult).filter_by(job_run_id=job.id).all()
    assert len(check_rows) == len(CHECKS)  # every registered Layer 2b check
