"""`persist_report` (harness/report/weekly.py) and the `report_wtd` settlement stage: the
report's tables as rows in `report_runs`/`report_cells`, not only as rendered Markdown.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import ReportCell, ReportRun
from harness.report.tables import PLACEHOLDER, Table, weekly_tables
from harness.report.weekly import build_meta, persist_report
from harness.settlement.job import Budget, new_ctx, use_ctx
from harness.settlement.report_wtd import report_wtd_stage
from tests.test_report import _seed_declined_week

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)

META = {"year": 2026, "week": 38, "generated_at": NOW.isoformat(), "build_sha": "abc123",
       "criteria_hash": "h" * 64, "config_hashes": ["cfg1"]}


def _tables() -> dict[str, Table]:
    t1 = Table("Table 1", "header", ["variant", "value"],
              [["sharp_direct", (Decimal("0.0250"), 40, 35, Decimal("0.0100"), Decimal("0.0400"))],
               ["constrained", PLACEHOLDER]])
    return {"t1": t1}


def test_report_cells_round_trip_text_and_second_run_appends(db_session):
    tables = _tables()
    run_id = persist_report(db_session, tables, META, 2026, 38, provisional=False,
                            markdown="# report\n")
    db_session.flush()

    run = db_session.get(ReportRun, run_id)
    assert (run.year, run.week, run.provisional) == (2026, 38, False)
    assert run.build_sha == "abc123" and run.criteria_hash == "h" * 64
    assert run.markdown == "# report\n" and run.markdown_sha256

    cells = db_session.query(ReportCell).filter_by(report_run_id=run_id).order_by(
        ReportCell.row_key, ReportCell.col_key).all()
    assert len(cells) == 4  # two rows x two columns

    ci_cell = next(c for c in cells if c.row_key == "sharp_direct" and c.col_key == "value")
    assert ci_cell.estimate == Decimal("0.0250")
    assert ci_cell.n_obs == 40 and ci_cell.n_clusters == 35
    assert ci_cell.lo == Decimal("0.0100") and ci_cell.hi == Decimal("0.0400")
    assert ci_cell.text  # the rendered string, same shape render_markdown prints
    assert ci_cell.flags == {"greyed": False, "flagged": False, "not_collected": False}

    placeholder_cell = next(c for c in cells if c.row_key == "constrained" and c.col_key == "value")
    assert placeholder_cell.estimate is None
    assert placeholder_cell.text == PLACEHOLDER
    assert placeholder_cell.flags["not_collected"] is False

    # A second run of the same week appends a new report_runs row rather than overwriting.
    run_id_2 = persist_report(db_session, tables, META, 2026, 38, provisional=True, markdown=None)
    assert run_id_2 != run_id
    assert db_session.query(ReportRun).filter_by(year=2026, week=38).count() == 2
    assert db_session.query(ReportCell).filter_by(report_run_id=run_id_2).count() == 4


def test_report_wtd_stage_hourly_provisional_and_yields_on_low_budget(db_session, env_settings):
    ctx = new_ctx(settings=env_settings)
    with use_ctx(ctx):
        low_budget = Budget(100, lambda: 0.0)  # remaining_s() == 100 < 120
        result = report_wtd_stage(db_session, NOW, low_budget)
        assert result.budget_exhausted is True
        assert result.counts == {"budget_exhausted": True}
        assert db_session.query(ReportRun).count() == 0

        ok_budget = Budget(300, lambda: 0.0)  # remaining_s() == 300 >= 120
        result = report_wtd_stage(db_session, NOW, ok_budget)
        db_session.commit()
        assert result.budget_exhausted is False
        rows = db_session.query(ReportRun).all()
        assert len(rows) == 1
        assert rows[0].provisional is True
        assert rows[0].markdown is None

        # Not due again inside the period -- an hour later, where it used to run again.
        result2 = report_wtd_stage(db_session, NOW + timedelta(hours=1, minutes=1), ok_budget)
        db_session.commit()
        assert result2.counts.get("skipped") is True
        assert db_session.query(ReportRun).count() == 1

        # Due again six hours after the first run (final review I6).
        result3 = report_wtd_stage(db_session, NOW + timedelta(hours=6, minutes=1), ok_budget)
        db_session.commit()
        assert result3.counts.get("skipped") is not True
        assert db_session.query(ReportRun).count() == 2


def test_report_wtd_cadence_comes_from_the_setting(db_session, env_settings):
    """Final review I6: `report_wtd_period_s` gates the stage, so an operator who wants the
    week-to-date tables refreshed more often (or not at all until Monday) moves one setting
    rather than a module constant."""
    hourly = env_settings.model_copy(update={"report_wtd_period_s": 3600})
    with use_ctx(new_ctx(settings=hourly)):
        ok_budget = Budget(300, lambda: 0.0)
        report_wtd_stage(db_session, NOW, ok_budget)
        db_session.commit()
        assert db_session.query(ReportRun).count() == 1

        result = report_wtd_stage(db_session, NOW + timedelta(hours=1, minutes=1), ok_budget)
        db_session.commit()
        assert result.counts.get("skipped") is not True
        assert db_session.query(ReportRun).count() == 2


def test_report_wtd_stamps_t13_with_the_run_s_own_generated_at(db_session, env_settings):
    """Design review I2: `report_wtd_stage` used to call `weekly_tables` with no `now`, so
    t13's "this run generated at" row was `datetime.now(timezone.utc)` taken inside
    `weekly_tables`, not the injected clock the stage and `build_meta` were run under -- an
    injected clock makes the two disagree by months rather than by however long the render
    took. `weekly_tables` and `build_meta` now share the one `now` the stage passes in, so the
    persisted `report_runs.generated_at` and t13's own row read the same instant back."""
    with use_ctx(new_ctx(settings=env_settings)):
        result = report_wtd_stage(db_session, NOW, Budget(300, lambda: 0.0))
        db_session.commit()

    run = db_session.get(ReportRun, result.counts["report_run_id"])
    assert run.generated_at == NOW
    cell = db_session.query(ReportCell).filter_by(
        report_run_id=run.id, table_key="t13", row_key="this run generated at",
        col_key="value").one()
    assert cell.text == run.generated_at.isoformat() == NOW.isoformat()


def test_t12_cells_round_trip_with_their_composite_row_keys(db_session, env_settings):
    """The cells the Study surface reads are keyed table -> row -> col; a positional row key
    would make the surface key a row on an index (ruling B-I5)."""
    _seed_declined_week(db_session)
    tables = weekly_tables(db_session, 2026, 37, env_settings)
    meta = build_meta(db_session, env_settings, 2026, 37, now=NOW)
    run_id = persist_report(db_session, tables, meta, 2026, 37, provisional=False,
                            markdown="# w37")
    db_session.flush()

    cells = db_session.query(ReportCell).filter(ReportCell.report_run_id == run_id,
                                                ReportCell.table_key == "t12").all()
    assert cells
    assert not any("#" in c.row_key for c in cells), "a t12 row key was disambiguated by index"
    keys = {c.row_key for c in cells}
    assert "sharp_direct/rejected:edge" in keys
    by_col = {c.col_key for c in cells}
    assert "clv_rejected_gap_outcomes" in by_col


#: Sunday 2026-09-13 20:00 CT. UTC has not rolled over yet, but `datetime.isocalendar()` on the
#: UTC instant says week 38 while Chicago is still in week 37 (addendum 1.2).
SUNDAY_20_CT = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def test_the_provisional_run_is_stamped_with_the_chicago_week(db_session, env_settings):
    """Addendum 0.1: at Sunday 20:00 CT the week-to-date run belongs to week 37, not 38."""
    assert SUNDAY_20_CT.isocalendar()[:2] == (2026, 38)
    with use_ctx(new_ctx(settings=env_settings)):
        result = report_wtd_stage(db_session, SUNDAY_20_CT, Budget(300, lambda: 0.0))
        db_session.commit()
    assert (result.counts["year"], result.counts["week"]) == (2026, 37)
    row = db_session.query(ReportRun).one()
    assert (row.year, row.week) == (2026, 37)


# --- fix 75 (roadmap row 75): the rebuild yields inside the stream, not only at its entry ----


class _Mono:
    """A monotonic clock the test steps by hand; the last value repeats forever."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.i = 0

    def __call__(self) -> float:
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return value


def test_report_wtd_records_a_budget_exhausted_rebuild_and_stays_due(db_session, env_settings):
    """Row 75: the shared settle budget used to be read once, at this stage's entry, so a
    rebuild that went long held the hourly settle slot (`max_instances=1`) until it finished --
    3,518 s on settle run 203, and two later runs discarded inside the stage. A budget spent
    mid-rebuild now ends the stage within one page: it records `budget_exhausted` with the
    tables it did complete, it persists nothing, and -- the half that makes the next run pick
    it up -- it does not stamp `report_wtd_last`, so the report is still due an hour later.
    The stage *returns* rather than raising, so the remaining settle stages still run.
    """
    from harness.settlement.report_wtd import JOB_STATE_KEY, _GET_LAST

    # The budget is read once by `Budget.__init__`, once by the `MIN_BUDGET_S` guard at entry
    # (which passes: 300 s left), and then once before each table is built. It runs out on the
    # fourth of those, so t1, t2 and t3 are finished and table 4 is never started.
    budget = Budget(300, _Mono(0.0, 0.0, 0.0, 0.0, 0.0, 10_000.0))
    with use_ctx(new_ctx(settings=env_settings)):
        result = report_wtd_stage(db_session, NOW, budget)
    # What `Settler._run_stage` does after every stage, and the point of the exercise: the
    # transaction whose server-side cursor was closed mid-rebuild is handed back and committed.
    db_session.commit()

    assert result.budget_exhausted is True
    assert result.error is None
    assert result.counts == {"budget_exhausted": True, "tables_completed": 3}
    assert db_session.query(ReportRun).count() == 0
    assert db_session.execute(_GET_LAST, {"k": JOB_STATE_KEY}).scalar() is None

    # Still due: the next hour's settle run rebuilds it with a budget of its own.
    with use_ctx(new_ctx(settings=env_settings)):
        again = report_wtd_stage(db_session, NOW + timedelta(hours=1), Budget(300, lambda: 0.0))
    db_session.commit()
    assert again.budget_exhausted is False
    assert db_session.query(ReportRun).count() == 1
    assert db_session.execute(_GET_LAST, {"k": JOB_STATE_KEY}).scalar() is not None


def test_a_rebuild_that_has_no_budget_left_at_all_completes_no_table(db_session, env_settings):
    """Review minor M2/M1: the budget is observable *between* tables as well as inside the three
    streamed reads, so `tables_completed` reports where the rebuild actually stopped rather than
    only ever 3, 4 or 5. A budget that is gone before the first table yields immediately and
    completes none of them -- and still leaves the report due."""
    from harness.settlement.report_wtd import JOB_STATE_KEY, _GET_LAST

    # 0.0: Budget.__init__. 0.0: the entry guard, which passes. Then nothing is left.
    budget = Budget(300, _Mono(0.0, 0.0, 10_000.0))
    with use_ctx(new_ctx(settings=env_settings)):
        result = report_wtd_stage(db_session, NOW, budget)
    db_session.commit()

    assert result.budget_exhausted is True
    assert result.counts == {"budget_exhausted": True, "tables_completed": 0}
    assert db_session.query(ReportRun).count() == 0
    assert db_session.execute(_GET_LAST, {"k": JOB_STATE_KEY}).scalar() is None
