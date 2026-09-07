from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from harness.db.schema import create_schema, ensure_partitions, week_bounds


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


def test_create_schema_adds_no_fair_reason_to_an_existing_table(db_session):
    """`init-db` (the only schema entrypoint; no migrations framework) must be safe to rerun
    against a database that predates the no_fair_reason column -- e.g. the NAS deployment,
    which already has `market_gap_snapshots` without it. `create_all` alone would not add the
    column to an existing table, so `create_schema` also runs an idempotent `ALTER TABLE`."""
    engine = db_session.get_bind()

    def has_column() -> bool:
        return db_session.execute(text(
            "select 1 from information_schema.columns "
            "where table_name = 'market_gap_snapshots' and column_name = 'no_fair_reason'"
        )).first() is not None

    assert has_column()  # the db_session fixture already ran create_schema once

    db_session.execute(text("alter table market_gap_snapshots drop column no_fair_reason"))
    db_session.commit()
    assert not has_column()

    create_schema(engine)
    db_session.commit()
    assert has_column()

    # idempotent: rerunning again against a table that already has the column is a no-op.
    create_schema(engine)
    assert has_column()


def test_create_schema_adds_brin_time_indexes(db_session):
    """Dashboard 'last hour' counts on the append-only event/trade tables must not seq-scan."""
    names = {r[0] for r in db_session.execute(text(
        "select indexname from pg_indexes where indexname in ('ix_obe_ts_brin', 'ix_trades_ts_brin')")).all()}
    assert names == {"ix_obe_ts_brin", "ix_trades_ts_brin"}
