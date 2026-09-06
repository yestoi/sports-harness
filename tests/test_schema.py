from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from harness.db.schema import ensure_partitions, week_bounds


def test_week_bounds_monday_to_monday_utc():
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wednesday
    start, end = week_bounds(now)
    assert start == datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 14, tzinfo=timezone.utc)


def test_week_bounds_rejects_naive_datetime():
    with pytest.raises(ValueError):
        week_bounds(datetime(2026, 9, 9, 23, 0))


def test_ensure_partitions_creates_two_weeks(db_session):
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    created = ensure_partitions(db_session, now)
    assert created == ["raw_responses_y2026w37", "raw_responses_y2026w38"]
    again = ensure_partitions(db_session, now)
    assert again == []
    names = db_session.execute(
        text("select inhrelid::regclass::text from pg_inherits where inhparent = 'raw_responses'::regclass")
    ).scalars().all()
    assert set(names) >= {"raw_responses_y2026w37", "raw_responses_y2026w38"}


def test_raw_insert_roundtrip(db_session):
    from harness.db.models import RawResponse, Run

    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    ensure_partitions(db_session, now)
    run = Run(started_at=now, status="running")
    db_session.add(run)
    db_session.flush()
    row = RawResponse(run_id=run.id, source="kalshi", endpoint="/markets", params={"series_ticker": "KXNFLGAME"},
                      fetched_at=now, http_status=200, body={"markets": []})
    db_session.add(row)
    db_session.flush()
    got = db_session.get(RawResponse, (row.id, row.fetched_at))
    assert got.body == {"markets": []}
