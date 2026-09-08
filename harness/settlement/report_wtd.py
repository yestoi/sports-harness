"""The hourly week-to-date report: a provisional rendering of the current ISO week's tables,
persisted (not written to Markdown) so the dashboard can show "how the week looks so far"
without waiting for Monday's `harness report` (design spec §3.7).

Registered on the settlement job after `harness.ops.housekeeping`, like every other stage in
`STAGE_MODULES`. The cadence lives in `job_state('report_wtd_last')` rather than a bespoke
table: `job_state.updated_at` already carries exactly the "when did this last run" a once-an-hour
gate needs, the same idea `housekeeping_stage` uses over `source_state` for its own once-a-day
gate, on the table this job already owns.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.report.tables import weekly_tables
from harness.report.weekly import build_meta, persist_report
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

JOB_STATE_KEY = "report_wtd_last"
#: Once an hour (design spec §3.7).
PERIOD = timedelta(hours=1)
#: Below this much of the shared settlement budget, `report_wtd` yields rather than rendering
#: ten tables over a week of rows (brief: "yields if the shared budget is below 120s at entry").
MIN_BUDGET_S = 120

_GET_LAST = text("select updated_at from job_state where key = :k")
_SET_LAST = text("""
    insert into job_state (key, value, updated_at) values (:k, 0, :now)
    on conflict (key) do update set updated_at = excluded.updated_at
""")


def _due(last: datetime | None, now: datetime) -> bool:
    return last is None or (now - last) >= PERIOD


def report_wtd_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    if budget.remaining_s() < MIN_BUDGET_S:
        return StageResult("report_wtd", {"budget_exhausted": True}, True, None)
    last = session.execute(_GET_LAST, {"k": JOB_STATE_KEY}).scalar()
    if not _due(last, now):
        return StageResult("report_wtd", {"skipped": True}, False, None)

    settings = current_ctx().get("settings")
    if settings is None:
        # Same defensive fallback as housekeeping_stage: should not happen via Settler.run(),
        # which always threads a Settings through, but a stage called outside a job must not
        # raise for want of one.
        return StageResult("report_wtd", {"skipped": True, "reason": "no settings"}, False, None)

    iso = now.isocalendar()
    year, week = iso.year, iso.week
    tables = weekly_tables(session, year, week, settings)
    meta = build_meta(session, settings, year, week, now=now)
    report_run_id = persist_report(session, tables, meta, year, week,
                                   provisional=True, markdown=None)
    session.execute(_SET_LAST, {"k": JOB_STATE_KEY, "now": now})
    return StageResult("report_wtd",
                       {"report_run_id": report_run_id, "year": year, "week": week}, False, None)


register_stage("report_wtd", report_wtd_stage)
