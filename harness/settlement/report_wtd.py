"""The week-to-date report: a provisional rendering of the current ISO week's tables,
persisted (not written to Markdown) so the dashboard can show "how the week looks so far"
without waiting for Monday's `harness report` (design spec §3.7).

Registered on the settlement job after `harness.ops.housekeeping`, like every other stage in
`STAGE_MODULES`. The cadence lives in `job_state('report_wtd_last')` rather than a bespoke
table: `job_state.updated_at` already carries exactly the "when did this last run" the
`report_wtd_period_s` gate needs, the same idea `housekeeping_stage` uses over `source_state`
for its own once-a-day gate, on the table this job already owns.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.report.tables import weekly_tables
from harness.report.weekly import build_meta, persist_report
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage
from harness.weeks import chicago_iso_week

log = logging.getLogger(__name__)

JOB_STATE_KEY = "report_wtd_last"
#: Below this much of the shared settlement budget, `report_wtd` yields rather than rendering
#: ten tables over a week of rows (brief: "yields if the shared budget is below 120s at entry").
MIN_BUDGET_S = 120

_GET_LAST = text("select updated_at from job_state where key = :k")
_SET_LAST = text("""
    insert into job_state (key, value, updated_at) values (:k, 0, :now)
    on conflict (key) do update set updated_at = excluded.updated_at
""")


def _due(last: datetime | None, now: datetime, period: timedelta) -> bool:
    return last is None or (now - last) >= period


def report_wtd_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    """Rebuild the week-to-date tables when `report_wtd_period_s` has elapsed since the last
    rebuild, unless the shared settlement budget is already below `MIN_BUDGET_S`.

    The cadence is a setting rather than a constant, and its default is six hours rather than
    the hourly one the design spec's §3.7 named (final review I6). Two of the ten tables
    materialise a week of rows in Python: `tables._T4_SNAPSHOTS` loads every
    `market_gap_snapshots` row of the ISO week into a list, and `_T5_SNAPSHOTS` parses every
    WebSocket snapshot body for the week's moved tickers into a `BookState`. That is the
    largest allocation this phase adds, it grows all week, and on the NAS it competes with the
    `settle` stage for the same hourly slot on about 1 GB of spare RAM. Nothing downstream
    reads the provisional rows often enough to need them hourly.
    """
    if budget.remaining_s() < MIN_BUDGET_S:
        return StageResult("report_wtd", {"budget_exhausted": True}, True, None)

    settings = current_ctx().get("settings")
    if settings is None:
        # Same defensive fallback as housekeeping_stage: should not happen via Settler.run(),
        # which always threads a Settings through, but a stage called outside a job must not
        # raise for want of one.
        return StageResult("report_wtd", {"skipped": True, "reason": "no settings"}, False, None)

    last = session.execute(_GET_LAST, {"k": JOB_STATE_KEY}).scalar()
    if not _due(last, now, timedelta(seconds=settings.report_wtd_period_s)):
        return StageResult("report_wtd", {"skipped": True}, False, None)

    # Addendum 0.1 / Amendment 5: the provisional run's week is the America/Chicago ISO week.
    # A raw `now.isocalendar()` stamped a Sunday-evening rebuild with the *next* week's number.
    year, week = chicago_iso_week(now)
    tables = weekly_tables(session, year, week, settings, now=now)
    meta = build_meta(session, settings, year, week, now=now)
    report_run_id = persist_report(session, tables, meta, year, week,
                                   provisional=True, markdown=None)
    session.execute(_SET_LAST, {"k": JOB_STATE_KEY, "now": now})
    return StageResult("report_wtd",
                       {"report_run_id": report_run_id, "year": year, "week": week}, False, None)


register_stage("report_wtd", report_wtd_stage)
