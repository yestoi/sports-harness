"""Daily database housekeeping: size measurement, the growth/ceiling projection, and the
once-a-day gate that runs it as a settlement stage.

`ceiling_projection` is pure (ruling 1's class of decision): given the trailing sizes a caller
has already read and the size just measured, it is the whole of the arithmetic the brief pins
(100 GB, +10 GB/day, ceiling 2000 gives 190 days), tested with no database at all.
`housekeeping` and `housekeeping_stage` run against the real schema, because `pg_database_size`
and `pg_total_relation_size` are properties of the actual database, not of the Python around
them.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from harness.db.models import JobRun
from harness.ops.housekeeping import (
    DUE_HOUR_UTC,
    JOB_STATE_KEY,
    ceiling_projection,
    housekeeping,
    housekeeping_stage,
)
from harness.recorder.store import get_source_state, set_source_state
from harness.settlement.job import Settler, load_stages

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
