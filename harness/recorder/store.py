from datetime import datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import RawResponse, Run, SourceState, TradeWatermark
from harness.feeds.http import FetchResult


def start_run(session: Session, now: datetime) -> Run:
    run = Run(started_at=now, status="running")
    session.add(run)
    session.commit()
    return run


def store_raw(session: Session, run_id: int, source: str, endpoint: str, params: dict, result: FetchResult) -> int:
    row = RawResponse(run_id=run_id, source=source, endpoint=endpoint, params=params,
                      fetched_at=result.fetched_at, http_status=result.status, body=result.body)
    session.add(row)
    session.flush()
    return row.id


def finish_run(session: Session, run: Run, status: str, error: str | None = None, *, n_requests: int = 0,
               credits_used: int = 0, odds_remaining: int | None = None, budget_exhausted: bool = False,
               notes: dict | None = None, finished_at: datetime | None = None) -> None:
    run.status = status
    run.error = error
    run.n_requests = n_requests
    run.credits_used = credits_used
    run.odds_remaining = odds_remaining
    run.budget_exhausted = budget_exhausted
    run.notes = notes or {}
    run.finished_at = finished_at or datetime.now(tz=run.started_at.tzinfo)
    session.commit()


def get_source_state(session: Session, key: str) -> datetime | None:
    row = session.get(SourceState, key)
    return row.last_fetched_at if row else None


def set_source_state(session: Session, key: str, ts: datetime) -> None:
    stmt = insert(SourceState).values(key=key, last_fetched_at=ts)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"last_fetched_at": ts})
    session.execute(stmt)


def get_watermarks(session: Session) -> dict[str, TradeWatermark]:
    return {w.ticker: w for w in session.query(TradeWatermark).all()}


def upsert_watermark(session: Session, ticker: str, last_ts: datetime, last_volume_fp: Decimal) -> None:
    stmt = insert(TradeWatermark).values(ticker=ticker, last_ts=last_ts, last_volume_fp=last_volume_fp)
    stmt = stmt.on_conflict_do_update(index_elements=["ticker"],
                                      set_={"last_ts": last_ts, "last_volume_fp": last_volume_fp})
    session.execute(stmt)
