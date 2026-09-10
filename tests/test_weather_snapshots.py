"""The hourly forecast parser and the source's due/window rules. Every body is the recording."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness.weather.snapshots import (ALLOWED_CADENCES, HOURLY_FIELDS, MIN_TICK_REMAINING_S,
                                       PERIOD_AFTER, PERIOD_BEFORE, REFETCH_AFTER,
                                       SHORT_FORECAST_MAX, parse_hourly, periods_in_window)

FIXTURES = Path(__file__).parent / "fixtures"
HOURLY = json.loads((FIXTURES / "nws_forecast_hourly_lsu.json").read_text())


def test_the_recorded_body_carries_every_pinned_field():
    """Addendum 0.10: the parser is pinned to a recorded live response and this is the assertion
    that the recording still has what it reads. A field that disappears fails here, loudly,
    rather than filling a column with nulls in production."""
    period = HOURLY["properties"]["periods"][0]
    for field in HOURLY_FIELDS:
        assert field in period, f"the recorded hourly body has no {field!r}"


def test_the_recording_parses_into_periods():
    rows = parse_hourly(HOURLY)
    assert rows and len(rows) == len(HOURLY["properties"]["periods"])
    first = rows[0]
    assert first["period_start"].tzinfo is not None
    assert isinstance(first["temperature_f"], int)
    assert isinstance(first["wind_mph"], int)
    assert first["wind_dir"] and len(first["wind_dir"]) <= 8
    assert 0 <= first["precip_pct"] <= 100
    assert len(first["short_forecast"]) <= SHORT_FORECAST_MAX


def test_a_body_missing_a_pinned_field_parses_to_none():
    body = json.loads(json.dumps(HOURLY))
    del body["properties"]["periods"][0][HOURLY_FIELDS[0]]
    assert parse_hourly(body) is None


@pytest.mark.parametrize("body", [None, {}, {"properties": {}},
                                  {"properties": {"periods": "x"}},
                                  {"properties": {"periods": []}}])
def test_an_unusable_body_parses_to_none(body):
    assert parse_hourly(body) is None


def test_the_wind_speed_string_becomes_a_number():
    body = json.loads(json.dumps(HOURLY))
    body["properties"]["periods"][0]["windSpeed"] = "12 to 18 mph"
    assert parse_hourly(body)[0]["wind_mph"] == 18      # the gust end, the one that matters


def test_a_missing_precipitation_probability_is_null_not_zero():
    """A null probability and a zero probability are different facts and a model reading the
    feature block must be able to tell them apart."""
    body = json.loads(json.dumps(HOURLY))
    body["properties"]["periods"][0]["probabilityOfPrecipitation"] = {"value": None}
    assert parse_hourly(body)[0]["precip_pct"] is None


def test_the_short_forecast_is_sanitized_and_capped():
    body = json.loads(json.dumps(HOURLY))
    body["properties"]["periods"][0]["shortForecast"] = "<b>Sunny</b>\x00 " + "x" * 200
    row = parse_hourly(body)[0]
    assert row["short_forecast"].startswith("Sunny")
    assert "<" not in row["short_forecast"] and "\x00" not in row["short_forecast"]
    assert len(row["short_forecast"]) == SHORT_FORECAST_MAX


def test_only_the_kickoff_window_is_kept():
    kickoff = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
    rows = [{"period_start": kickoff + timedelta(hours=h)} for h in range(-4, 9)]
    kept = periods_in_window(rows, kickoff)
    assert kept[0]["period_start"] == kickoff - PERIOD_BEFORE
    assert kept[-1]["period_start"] == kickoff + PERIOD_AFTER


def test_the_window_and_cadence_constants():
    assert ALLOWED_CADENCES == (300, 900)
    assert MIN_TICK_REMAINING_S == 25
    assert PERIOD_BEFORE == timedelta(hours=1) and PERIOD_AFTER == timedelta(hours=4)
    assert REFETCH_AFTER == timedelta(hours=1)
    assert SHORT_FORECAST_MAX == 80
