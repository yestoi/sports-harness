from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, Response
from sqlalchemy import desc
from sqlalchemy.orm import sessionmaker

from harness.db.models import Run

STALE_AFTER_S = 20 * 60


def create_app(session_factory: sessionmaker, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="harness")

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        with session_factory() as s:
            last = s.query(Run).order_by(desc(Run.started_at)).first()
        now = clock()
        if last is None:
            response.status_code = 503
            return {"status": "error", "last_run_at": None, "last_status": None, "seconds_since": None,
                    "credits_remaining": None}
        since = (now - last.started_at).total_seconds()
        status = "stale" if since > STALE_AFTER_S else ("error" if last.status == "error" else "ok")
        if status != "ok":
            response.status_code = 503
        return {"status": status, "last_run_at": last.started_at.isoformat(), "last_status": last.status,
                "seconds_since": int(since), "credits_remaining": last.odds_remaining}

    return app
