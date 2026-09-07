from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, Response
from sqlalchemy import desc
from sqlalchemy.orm import sessionmaker

from harness.db.models import Run

STALE_AFTER_S = 20 * 60


def compute_health(session_factory: sessionmaker, now: datetime) -> tuple[dict, int]:
    """Shared by the `/healthz` route and the dashboard's Health section.

    Returns (body, status_code) so callers that need the HTTP status (e.g. the dashboard's
    own `/healthz`) and callers that only want the JSON body (the dashboard page) can each
    take what they need without recomputing the staleness/error logic themselves.
    """
    with session_factory() as s:
        last = s.query(Run).order_by(desc(Run.started_at)).first()
    if last is None:
        return ({"status": "error", "last_run_at": None, "last_status": None, "seconds_since": None,
                 "credits_remaining": None}, 503)
    since = (now - last.started_at).total_seconds()
    status = "stale" if since > STALE_AFTER_S else ("error" if last.status == "error" else "ok")
    code = 200 if status == "ok" else 503
    return ({"status": status, "last_run_at": last.started_at.isoformat(), "last_status": last.status,
             "seconds_since": int(since), "credits_remaining": last.odds_remaining}, code)


def create_app(session_factory: sessionmaker, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="harness")

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        body, code = compute_health(session_factory, clock())
        response.status_code = code
        return body

    return app
