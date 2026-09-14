"""The schema fixture builds the partitions every committed fixture date needs (carried fix 59).

On 2026-09-14 the ISO week rolled over and the fixture, which built only the real current and next
week, stopped building week 37: every tape row dated 2026-09-09 then failed with `CheckViolation:
no partition of relation "orderbook_events" found`. The fixture now builds a fixed span of weeks.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from harness.db.schema import PARTITIONED_TABLES, _partition_name, week_bounds
from tests.conftest import FIXTURE_PARTITION_WEEKS

# One representative date per committed fixture week (grep `datetime(2026,` under tests/).
FIXTURE_DATES = (
    datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc),
    datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc),
)


def test_every_fixture_week_has_its_partitions_whatever_today_is(db_session):
    for when in FIXTURE_DATES:
        start, _ = week_bounds(when)
        for table in PARTITIONED_TABLES:
            name = _partition_name(table, start)
            found = db_session.execute(text("select to_regclass(:name)"), {"name": name}).scalar()
            assert found == name, (when.date(), name)


def test_the_fixture_span_covers_every_committed_fixture_week(db_session):
    # Verify all fixture date weeks' partitions exist.
    for when in FIXTURE_DATES:
        start, _ = week_bounds(when)
        for table in PARTITIONED_TABLES:
            name = _partition_name(table, start)
            found = db_session.execute(text("select to_regclass(:name)"), {"name": name}).scalar()
            assert found == name, (when.date(), name)
    
    # Verify fixture builds exactly weeks {36,37,38,39,41,42,45,46} plus live two.
    # Each FIXTURE_PARTITION_WEEKS entry's ensure_partitions builds two weeks.
    covered = {week + timedelta(days=7 * i) for week in FIXTURE_PARTITION_WEEKS for i in range(2)}
    # Add live two weeks.
    now = datetime.now(timezone.utc)
    live_start, _ = week_bounds(now)
    covered.add(live_start)
    covered.add(live_start + timedelta(days=7))
    
    # Expected: weeks 36,37,38,39,41,42,45,46 + live two = ISO weeks {36,37,38,39,41,42,45,46} union live_week and live_week+1
    expected = {
        datetime(2026, 8, 31, tzinfo=timezone.utc),   # week 36
        datetime(2026, 9, 7, tzinfo=timezone.utc),    # week 37
        datetime(2026, 9, 14, tzinfo=timezone.utc),   # week 38
        datetime(2026, 9, 21, tzinfo=timezone.utc),   # week 39
        datetime(2026, 10, 5, tzinfo=timezone.utc),   # week 41
        datetime(2026, 10, 12, tzinfo=timezone.utc),  # week 42
        datetime(2026, 11, 2, tzinfo=timezone.utc),   # week 45
        datetime(2026, 11, 9, tzinfo=timezone.utc),   # week 46
        live_start,
        live_start + timedelta(days=7),
    }
    assert covered == expected, f"covered {covered} != expected {expected}"
    assert all(week.tzinfo is timezone.utc and week.weekday() == 0 for week in FIXTURE_PARTITION_WEEKS)
