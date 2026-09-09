from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, Response
from sqlalchemy import desc
from sqlalchemy.orm import sessionmaker

from harness.config.settings import Settings
from harness.db.models import Run

STALE_AFTER_S = 20 * 60
CREDITS_LOW_FRACTION = 0.2  # U1 2026-09-07: 80% budget alarm, i.e. low below 20% remaining

# --- Pulse's thresholds (phase 4.5, addendum §0.9; spec §1.1) --------------------------------
# Spec §1.1: "thresholds used to colour anything are imported from the code that enforces them,
# never restated in the front end". Before this, four of these existed nowhere and two were
# literals in harness/dashboard/app.py, so the legacy page and the new Pulse surface could
# disagree about the same machine. This module is the one home; the snapshot payload carries the
# threshold beside the value, and the front end never restates a number.
#
# The values are the spec's own (§2.1) and are not tuned here: WATCH at one minute of silence and
# BROKEN at two for both the executor's heartbeat and the WebSocket's last event; credits WATCH
# at 40 % of the monthly budget (BROKEN stays CREDITS_LOW_FRACTION, 20 %); free space on the
# Postgres data mount below a quarter is BROKEN; the database ceiling turns WATCH at 60 % of
# db_budget_gb and BROKEN at 80 %.

#: `exec_heartbeat.last_loop_at` older than this many seconds is a WATCH.
HEARTBEAT_WATCH_S = 60
#: ... and older than this is a BROKEN.
HEARTBEAT_BROKEN_S = 120
#: `exec_heartbeat.ws_last_event_at` older than this many seconds is a WATCH.
WS_EVENT_WATCH_S = 60
#: ... and older than this is a BROKEN.
WS_EVENT_BROKEN_S = 120
#: Odds API credits remaining below this share of the monthly budget is a WATCH; below
#: CREDITS_LOW_FRACTION it is a BROKEN.
CREDITS_WATCH_FRACTION = 0.4
#: Free space on the Postgres data mount below this share of its total is a BROKEN. Computable
#: only once housekeeping has recorded both `host.disk_free_gb` and `host.disk_total_gb`; until
#: then the rule reports `not evaluated`, never FINE.
DISK_FREE_MIN_FRACTION = 0.25
#: Database size above this share of `Settings.db_budget_gb` is a WATCH.
DB_WATCH_FRACTION = 0.6
#: ... and above this share it is a BROKEN. Same number the legacy page's DB_CEILING_RED_PCT
#: renders as 80.0.
DB_BROKEN_FRACTION = 0.8


def compute_health(session_factory: sessionmaker, now: datetime, credits_budget: int) -> tuple[dict, int]:
    """Shared by the `/healthz` route and the dashboard's Health section.

    Returns (body, status_code) so callers that need the HTTP status (e.g. the dashboard's
    own `/healthz`) and callers that only want the JSON body (the dashboard page) can each
    take what they need without recomputing the staleness/error logic themselves.

    `credits_budget` (U1 2026-09-07) is the Odds API monthly-credit tier; callers pass it
    (a `Settings` object's `odds_monthly_credits` is not accepted directly here so this stays
    testable with a bare int) since this module has no settings object of its own.
    """
    # `runs.started_at` has no index, so both lookups order by `id` desc (autoincrement PK,
    # same order as insertion) rather than `started_at`.
    with session_factory() as s:
        last = s.query(Run).order_by(desc(Run.id)).first()
        if last is not None:
            last_with_credits = (
                s.query(Run)
                .filter(Run.odds_remaining.isnot(None))
                .order_by(desc(Run.id))
                .first()
            )
            # Task 6b (ruling A-C3): the tier and buckets the recorder read from
            # `GET /account/limits`. Taken from the newest *non-skipped* run, so a quiet-window
            # heartbeat cannot blank the block, the same way `credits_remaining` already works.
            # A null here in production means `has_kalshi_credentials()` was False in app-run:
            # check the two key mounts, because without them nothing writes a `venue_requests`
            # row and the §3 tripwire is vacuous.
            last_real = s.query(Run).filter(Run.status != "skipped").order_by(desc(Run.id)).first()
            venue_limits = (last_real.notes or {}).get("venue_limits") if last_real else None
        else:
            last_with_credits = None
            venue_limits = None
    if last is None:
        return ({"status": "error", "last_run_at": None, "last_status": None, "seconds_since": None,
                 "credits_remaining": None, "credits_budget": credits_budget, "credits_low": False,
                 "venue_limits": None}, 503)
    since = (now - last.started_at).total_seconds()
    status = "stale" if since > STALE_AFTER_S else ("error" if last.status == "error" else "ok")
    code = 200 if status == "ok" else 503
    credits_remaining = last_with_credits.odds_remaining if last_with_credits is not None else None
    credits_low = credits_remaining is not None and credits_remaining < CREDITS_LOW_FRACTION * credits_budget
    return ({"status": status, "last_run_at": last.started_at.isoformat(), "last_status": last.status,
             "seconds_since": int(since), "credits_remaining": credits_remaining,
             "credits_budget": credits_budget, "credits_low": credits_low,
             "venue_limits": venue_limits}, code)


def create_app(session_factory: sessionmaker, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="harness")
    # U1 2026-09-07: no Settings object is threaded in here, so fall back to reading one.
    credits_budget = Settings().odds_monthly_credits

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        body, code = compute_health(session_factory, clock(), credits_budget)
        response.status_code = code
        return body

    return app
