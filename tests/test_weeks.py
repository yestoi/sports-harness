"""The one conversion every report and dashboard week key goes through (addendum 0.1, 1.1).

Every instant here is constructed tz-aware. The America/Chicago cases are built in that zone
directly; the UTC case at the end is the disagreement this module exists to remove.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from harness.weeks import (CHICAGO, chicago_day, chicago_iso_week, iso_week_bounds, local_day,
                           local_iso_week)

CT = ZoneInfo(CHICAGO)


def _ct(year, month, day, hour=0, minute=0, fold=0):
    return datetime(year, month, day, hour, minute, tzinfo=CT, fold=fold)


def test_a_naive_datetime_is_refused():
    with pytest.raises(ValueError):
        chicago_iso_week(datetime(2026, 9, 13, 19, 0))
    with pytest.raises(ValueError):
        chicago_day(datetime(2026, 9, 13, 19, 0))


def test_sunday_evening_ct_is_still_the_sunday_s_own_week():
    """The whole point of 0.1: 19:00, 20:30 and 23:59 CT on Sunday 2026-09-13 are week 37."""
    assert chicago_iso_week(_ct(2026, 9, 13, 19, 0)) == (2026, 37)
    assert chicago_iso_week(_ct(2026, 9, 13, 20, 30)) == (2026, 37)
    assert chicago_iso_week(_ct(2026, 9, 13, 23, 59)) == (2026, 37)


def test_midnight_ct_rolls_the_week():
    assert chicago_iso_week(_ct(2026, 9, 14, 0, 0)) == (2026, 38)


def test_both_instants_of_the_dst_fall_back_hour_read_the_same_week():
    """2026-11-01 01:30 CT happens twice (CDT then CST). Both are Sunday of week 44."""
    first = _ct(2026, 11, 1, 1, 30, fold=0)
    second = _ct(2026, 11, 1, 1, 30, fold=1)
    assert first.utcoffset() != second.utcoffset()
    assert chicago_iso_week(first) == (2026, 44)
    assert chicago_iso_week(second) == (2026, 44)


def test_the_iso_year_can_trail_the_calendar_year():
    assert chicago_iso_week(_ct(2026, 12, 31, 23, 0)) == (2026, 53)
    assert chicago_iso_week(_ct(2027, 1, 1, 0, 0)) == (2026, 53)
    assert chicago_iso_week(_ct(2027, 1, 4, 0, 0)) == (2027, 1)


def test_a_utc_instant_reads_the_chicago_week_and_disagrees_with_isocalendar():
    """2026-09-14T03:00Z is Sunday 22:00 CT. `datetime.isocalendar()` says week 38; the
    measurement key is week 37. This test states the disagreement it guards."""
    instant = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
    assert instant.isocalendar()[:2] == (2026, 38)
    assert chicago_iso_week(instant) == (2026, 37)


def test_local_functions_take_the_zone_by_name():
    instant = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
    assert local_iso_week(instant, CHICAGO) == (2026, 37)
    assert local_iso_week(instant, "UTC") == (2026, 38)
    assert local_day(instant, CHICAGO) == date(2026, 9, 13)
    assert local_day(instant, "UTC") == date(2026, 9, 14)


def test_chicago_day_is_the_owners_calendar_day():
    assert chicago_day(datetime(2026, 9, 15, 4, 59, tzinfo=timezone.utc)) == date(2026, 9, 14)
    assert chicago_day(datetime(2026, 9, 15, 5, 1, tzinfo=timezone.utc)) == date(2026, 9, 15)


def test_iso_week_bounds_are_inclusive_on_both_ends():
    assert iso_week_bounds(date(2026, 9, 16)) == (date(2026, 9, 14), date(2026, 9, 20))
