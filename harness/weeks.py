"""The one conversion behind every report and dashboard week key (addendum 0.1).

A week key here is a **measurement** key, not a storage key: it decides which week a number is
reported under, and the pre-registration record's calendar is stated in America/Chicago
(`docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`, "Calendar"). A raw
`datetime.isocalendar()` on a UTC instant disagrees with that for the five hours between 19:00
CT Sunday and midnight UTC Monday, which is exactly the window the Sunday slate lands in.

Pure functions, no I/O and no database, so every consumer -- the dashboard builders, the
settlement stage and the research budget -- can import this without importing each other.
`harness/research/spend.py` re-exports `chicago_day` and `iso_week_bounds` so the budget's
existing callers and their tests are unchanged (D1).

**Unchanged by design.** The weekly *partition* names (`harness/db/schema.py:41`,
`harness/ops/checks.py:53`) stay on UTC: they are storage keys, and renaming a partition is a
migration for no measurement gain. `harness/report/tables.py::week_bounds` is already local and
`harness report --week N --year Y` is already explicit.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

#: The owner's zone. One spelling, imported rather than retyped; `Settings.tz_local` carries the
#: same default and the dashboard builders may pass it through `local_iso_week` instead.
CHICAGO = "America/Chicago"


def _aware(now: datetime) -> datetime:
    """`now`, or `ValueError` if it is naive.

    The same refusal `harness.db.schema.week_bounds` makes, and for the same reason: a naive
    datetime has no week, it only has whatever week the process's own zone would give it.
    """
    if now.tzinfo is None:
        raise ValueError("harness.weeks requires a tz-aware datetime")
    return now


def local_day(now: datetime, tz: str) -> date:
    """The calendar day `now` falls in, in zone `tz`."""
    return _aware(now).astimezone(ZoneInfo(tz)).date()


def local_iso_week(now: datetime, tz: str) -> tuple[int, int]:
    """`(iso_year, iso_week)` of the local calendar day `now` falls in, in zone `tz`.

    The ISO year is not always the calendar year: 2027-01-01 CT is ISO week 53 of 2026, so the
    pair travels together and neither half is ever taken alone.
    """
    day = local_day(now, tz)
    iso = day.isocalendar()
    return iso.year, iso.week


def chicago_day(now: datetime) -> date:
    """The America/Chicago calendar day `now` falls in."""
    return local_day(now, CHICAGO)


def chicago_iso_week(now: datetime) -> tuple[int, int]:
    """`(iso_year, iso_week)` of the America/Chicago day `now` falls in."""
    return local_iso_week(now, CHICAGO)


def iso_week_bounds(day: date) -> tuple[date, date]:
    """[Monday, Sunday] of `day`'s ISO week, inclusive on both ends -- the shape a `between`
    predicate on a `date` column wants."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)
