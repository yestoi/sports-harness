"""Daily database housekeeping: size measurement, the growth/ceiling projection, and the
once-a-day gate that runs it as a settlement stage.

`ceiling_projection` is pure (ruling 1's class of decision): given the trailing sizes a caller
has already read and the size just measured, it is the whole of the arithmetic the brief pins
(100 GB, +10 GB/day, ceiling 2000 gives 190 days), tested with no database at all.
`housekeeping` and `housekeeping_stage` run against the real schema, because `pg_database_size`
and `pg_total_relation_size` are properties of the actual database, not of the Python around
them.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import CheckResult, JobRun, MetricSample, Rfq, RfqQuote
from harness.ops.housekeeping import (
    DUE_HOUR_UTC,
    JOB_STATE_KEY,
    RFQ_PRUNE_BATCH,
    RFQ_RETENTION_DAYS,
    ceiling_projection,
    housekeeping,
    housekeeping_stage,
    match_rates,
    record_housekeeping_metrics,
    _host_disk_gb,
    _host_mem_available_mb,
    _prune_rfqs,
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


# --- fix 38: rfqs retention (journal 110) --------------------------------------------------


def _rfq_row(rid: str, received_at: datetime) -> Rfq:
    return Rfq(id=rid, received_at=received_at, market_ticker=f"MKT-{rid}",
              legs=[], raw={"msg": {}, "truncated": False}, status="open")


def test_prune_rfqs_deletes_only_old_unquoted_rows(db_session):
    """Fix 38: a row older than `RFQ_RETENTION_DAYS` with no `rfq_quotes` row is pruned; an
    equally old row that *was* quoted, and a recent unquoted row, are both left alone -- F71's
    "the row is the record" means a quoted arrival is never in scope for this rule."""
    old_cutoff = NOW - timedelta(days=RFQ_RETENTION_DAYS, hours=1)
    old_unquoted = _rfq_row("RFQ-OLD-UNQUOTED", old_cutoff)
    old_quoted = _rfq_row("RFQ-OLD-QUOTED", old_cutoff)
    recent_unquoted = _rfq_row("RFQ-RECENT", NOW - timedelta(hours=1))
    db_session.add_all([old_unquoted, old_quoted, recent_unquoted])
    db_session.flush()
    db_session.add(RfqQuote(rfq_id=old_quoted.id, computed_at=NOW, legs=1, fair=None,
                            margin_per_leg=Decimal("0.03"), yes_bid=None, no_bid=None,
                            declined_reason="single_leg", unmatched_legs=0))
    db_session.flush()

    pruned = _prune_rfqs(db_session, NOW)
    db_session.flush()

    assert pruned == 1
    remaining = {r.id for r in db_session.query(Rfq).all()}
    assert remaining == {"RFQ-OLD-QUOTED", "RFQ-RECENT"}


def test_prune_rfqs_exactly_at_the_retention_boundary_is_kept(db_session):
    """`RFQ_RETENTION_DAYS` old, to the second, is not yet older than the window -- `<`, not
    `<=`, in `_PRUNE_RFQS`."""
    db_session.add(_rfq_row("RFQ-AT-BOUNDARY", NOW - timedelta(days=RFQ_RETENTION_DAYS)))
    db_session.flush()

    pruned = _prune_rfqs(db_session, NOW)

    assert pruned == 0
    assert db_session.query(Rfq).filter_by(id="RFQ-AT-BOUNDARY").count() == 1


def test_prune_rfqs_is_bounded_to_one_batch_per_run(db_session, monkeypatch):
    """Fix 38: bounded batches, not a whole-table sweep -- an unbounded `DELETE` against a
    flood-sized table is exactly the lock this fix must not reintroduce. `RFQ_PRUNE_BATCH`
    monkeypatched small so the bound is checkable without seeding thousands of rows."""
    from harness.ops import housekeeping as hk

    monkeypatch.setattr(hk, "RFQ_PRUNE_BATCH", 3)
    old = NOW - timedelta(days=RFQ_RETENTION_DAYS, hours=1)
    for i in range(5):
        db_session.add(_rfq_row(f"RFQ-BATCH-{i}", old))
    db_session.flush()

    pruned = hk._prune_rfqs(db_session, NOW)
    db_session.flush()

    assert pruned == 3
    assert db_session.query(Rfq).count() == 2


def test_record_housekeeping_metrics_carries_a_given_rfqs_pruned_count(db_session):
    """Round 1 (review I1): `rfqs_pruned` is supplied by the caller now -- `_prune_rfqs` is no
    longer called from inside `record_housekeeping_metrics` at all, so this pins the value
    through unchanged rather than pinning that the function computes it."""
    record_housekeeping_metrics(db_session, NOW, {"size_gb": 1.0, "tables_gb": {}}, {},
                                pg_data_mount="/no/such/mount", rfqs_pruned=3)
    db_session.flush()

    row = db_session.query(MetricSample).filter_by(
        source="housekeeping", name="db.rfqs_pruned").one()
    assert float(row.value) == 3.0


def test_record_housekeeping_metrics_omits_rfqs_pruned_when_none(db_session):
    """`rfqs_pruned=None` -- the retention step failed, or was never run -- omits the sample
    rather than recording a `0` that would read as "ran, found nothing to prune"."""
    record_housekeeping_metrics(db_session, NOW, {"size_gb": 1.0, "tables_gb": {}}, {},
                                pg_data_mount="/no/such/mount")
    db_session.flush()

    names = {r.name for r in db_session.query(MetricSample).all()}
    assert "db.rfqs_pruned" not in names


def test_a_prune_failure_costs_only_the_prune(db_session, monkeypatch, caplog):
    """Round 1 (review I1): a lock wait, the engine's statement timeout, or a deadlock in the
    prune must cost only the prune -- one WARNING naming the exception's class, `db.rfqs_pruned`
    simply absent from the batch, and every other metric in the same run still written. The
    prune's own savepoint failing must never roll back `db.size_gb` and the rest."""
    from harness.ops import housekeeping as hk

    def _boom(session, now):
        raise RuntimeError("simulated lock wait")

    monkeypatch.setattr(hk, "_prune_rfqs", _boom)

    with caplog.at_level(logging.WARNING):
        result = hk.housekeeping_stage(db_session, NOW, budget=None)
    db_session.flush()

    assert result.counts.get("skipped") is not True
    warnings = [r for r in caplog.records
               if r.levelno == logging.WARNING and "rfqs prune failed" in r.message]
    assert len(warnings) == 1
    assert "RuntimeError" in warnings[0].getMessage()
    names = {r.name for r in db_session.query(MetricSample).filter_by(
        source="housekeeping").all()}
    assert "db.size_gb" in names
    assert "db.rfqs_pruned" not in names
    # The failure did not leave the session's transaction poisoned.
    db_session.execute(text("select 1")).scalar()


def test_a_later_metrics_failure_does_not_undo_a_successful_prune(db_session, monkeypatch):
    """Round 1 (review I1), the other direction: the prune's savepoint is a sibling of the
    metrics batch's savepoint, not nested inside it, so a failure in `match_rates` (raised after
    the prune's own savepoint already released) must not roll the prune itself back -- only the
    metrics batch that would have recorded `db.rfqs_pruned` for it."""
    from harness.ops import housekeeping as hk

    db_session.add(_rfq_row("RFQ-SURVIVES-METRICS-FAILURE",
                            NOW - timedelta(days=RFQ_RETENTION_DAYS, hours=1)))
    db_session.flush()

    def _boom(*_a, **_k):
        raise RuntimeError("simulated telemetry failure")

    monkeypatch.setattr(hk, "match_rates", _boom)

    result = hk.housekeeping_stage(db_session, NOW, budget=None)

    assert result.counts.get("skipped") is not True
    assert db_session.query(Rfq).filter_by(id="RFQ-SURVIVES-METRICS-FAILURE").count() == 0
    names = {r.name for r in db_session.query(MetricSample).filter_by(
        source="housekeeping").all()}
    assert "db.rfqs_pruned" not in names   # the batch that would have recorded it rolled back
