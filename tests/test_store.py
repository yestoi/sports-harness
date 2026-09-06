from datetime import datetime, timezone
from decimal import Decimal

from harness.db.models import RawResponse, Run
from harness.db.schema import ensure_partitions
from harness.feeds.http import FetchResult
from harness.recorder.store import (finish_run, get_source_state, get_watermarks, set_source_state, start_run,
                                    store_raw, upsert_watermark)

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def _fr(body, status=200):
    return FetchResult(status=status, headers={}, body=body, fetched_at=NOW, url="u", elapsed_s=0.1)


def test_run_and_raw_lifecycle(db_session):
    ensure_partitions(db_session, NOW)
    run = start_run(db_session, NOW)
    assert run.id and run.status == "running"
    rid = store_raw(db_session, run.id, "kalshi", "/markets", {"series_ticker": "KXNFLGAME"}, _fr({"markets": []}))
    assert rid
    finish_run(db_session, run, "ok", n_requests=1, credits_used=0, odds_remaining=None)
    got = db_session.get(Run, run.id)
    assert got.status == "ok" and got.finished_at is not None and got.n_requests == 1
    raw = db_session.query(RawResponse).filter_by(run_id=run.id).one()
    assert raw.endpoint == "/markets" and raw.http_status == 200


def test_source_state_and_watermarks(db_session):
    assert get_source_state(db_session, "odds_featured:nfl") is None
    set_source_state(db_session, "odds_featured:nfl", NOW)
    assert get_source_state(db_session, "odds_featured:nfl") == NOW
    upsert_watermark(db_session, "T1", NOW, Decimal("10.00"))
    upsert_watermark(db_session, "T1", NOW, Decimal("12.00"))
    wm = get_watermarks(db_session)
    assert wm["T1"].last_volume_fp == Decimal("12.00")


def test_finish_run_default_finished_at_is_utc(db_session):
    run = start_run(db_session, NOW)
    finish_run(db_session, run, "ok")
    assert run.finished_at.tzinfo is not None
    assert run.finished_at.utcoffset().total_seconds() == 0
