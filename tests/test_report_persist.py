"""`persist_report` (harness/report/weekly.py) and the `report_wtd` settlement stage: the
report's tables as rows in `report_runs`/`report_cells`, not only as rendered Markdown.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import ReportCell, ReportRun
from harness.report.tables import PLACEHOLDER, Table
from harness.report.weekly import persist_report
from harness.settlement.job import Budget, new_ctx, use_ctx
from harness.settlement.report_wtd import report_wtd_stage

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

        # Not due again inside the same hour.
        result2 = report_wtd_stage(db_session, NOW + timedelta(minutes=30), ok_budget)
        db_session.commit()
        assert result2.counts.get("skipped") is True
        assert db_session.query(ReportRun).count() == 1

        # Due again an hour after the first run.
        result3 = report_wtd_stage(db_session, NOW + timedelta(hours=1, minutes=1), ok_budget)
        db_session.commit()
        assert result3.counts.get("skipped") is not True
        assert db_session.query(ReportRun).count() == 2
