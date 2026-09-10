"""The NWS client: its own httpx client, the fixed headers, GET only, and the pinned /points
schema. Every response in here is the recorded fixture; nothing in this file makes a call."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from harness.db.models import WeatherPoint
from harness.feeds.nws import POINTS_FIELDS, NwsClient, PointGrid, parse_point
from harness.weather.points import POINT_RERESOLVE_AFTER, resolve_point
from harness.weather.stadiums import Stadium

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)
LSU = Stadium(sport="ncaaf", team_id=99, abbreviation="LSU", name="Tiger Stadium",
              lat=30.4118, lon=-91.1836, roof="open", source="https://example.org")


def _points_body():
    return json.loads((FIXTURES / "nws_points_lsu.json").read_text())


def test_the_recorded_points_body_carries_every_pinned_field():
    """Addendum 0.10 for the /points half: the parser is pinned to the recorded response, and
    this is the assertion that the recording still has what the parser reads."""
    properties = _points_body()["properties"]
    for field in POINTS_FIELDS:
        assert field in properties, f"the recorded /points body has no {field!r}"


def test_parse_point_reads_the_recorded_body():
    grid = parse_point(_points_body())
    assert isinstance(grid, PointGrid)
    assert grid.office and grid.grid_x >= 0 and grid.grid_y >= 0
    assert grid.forecast_hourly_url.startswith("https://api.weather.gov/")
    assert grid.forecast_hourly_url.endswith("/forecast/hourly")


@pytest.mark.parametrize("body", [None, {}, {"properties": {}},
                                  {"properties": {"gridId": "LIX"}}])
def test_a_body_missing_a_pinned_field_parses_to_none(body):
    """The schema pin: a body that lost a field is a recorded refusal, never a guess."""
    assert parse_point(body) is None


def test_a_foreign_hourly_url_is_refused():
    """Roadmap invariant 8: the only host this client may reach is api.weather.gov, and the
    hourly URL is venue-supplied data, not configuration."""
    body = _points_body()
    body["properties"]["forecastHourly"] = "https://evil.example.com/forecast/hourly"
    assert parse_point(body) is None


@respx.mock
def test_the_client_sends_the_fixed_user_agent_and_accept(env_settings):
    route = respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=_points_body()))
    client = NwsClient(env_settings)
    try:
        result = client.get("/points/30.4118,-91.1836")
    finally:
        client.close()
    request = route.calls[0].request
    assert request.headers["user-agent"] == "sports-harness/1 (self-hosted research harness)"
    assert request.headers["accept"] == "application/geo+json"
    assert result.status == 200


@respx.mock
def test_a_429_is_retried_once_after_five_seconds(env_settings):
    slept = []
    route = respx.get("https://api.weather.gov/points/1,2").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=_points_body())])
    client = NwsClient(env_settings, sleep=slept.append)
    try:
        result = client.get("/points/1,2")
    finally:
        client.close()
    assert slept == [5.0]
    assert route.call_count == 2 and result.status == 200


@respx.mock
def test_any_other_non_200_is_returned_not_retried(env_settings):
    route = respx.get("https://api.weather.gov/points/1,2").mock(
        return_value=httpx.Response(503, text="down"))
    client = NwsClient(env_settings)
    try:
        result = client.get("/points/1,2")
    finally:
        client.close()
    assert route.call_count == 1 and result.status == 503 and result.body is None


def test_the_client_has_no_write_verbs(env_settings):
    """Same rule as the Kalshi read client: a bare httpx.Client carries post/put/delete/patch,
    so the process would hold a working write primitive it has no use for."""
    client = NwsClient(env_settings)
    try:
        for verb in ("post", "put", "delete", "patch", "request"):
            assert not hasattr(client, verb)
    finally:
        client.close()


@respx.mock
def test_resolve_point_writes_a_row_and_stores_the_raw_body(db_session, env_settings):
    respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=_points_body()))
    client = NwsClient(env_settings)
    try:
        row = resolve_point(db_session, client, run_id=1, stadium=LSU, now=NOW)
    finally:
        client.close()
    assert isinstance(row, WeatherPoint)
    assert (row.sport, row.team_id) == ("ncaaf", 99)
    stored = db_session.execute(__import__("sqlalchemy").text(
        "select source, http_status from raw_responses where source = 'nws'")).all()
    assert stored == [("nws", 200)]


@respx.mock
def test_a_second_resolve_inside_the_day_reuses_the_row_without_calling(db_session,
                                                                       env_settings):
    route = respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=_points_body()))
    client = NwsClient(env_settings)
    try:
        resolve_point(db_session, client, 1, LSU, NOW)
        again = resolve_point(db_session, client, 1, LSU, NOW + timedelta(hours=6))
    finally:
        client.close()
    assert route.call_count == 1 and again is not None


@respx.mock
def test_a_forced_re_resolution_after_a_day_calls_again(db_session, env_settings):
    """Ruling A-I6: a 404 or 301 on the hourly URL re-resolves /points, at most once per stadium
    per day. The permanent cache the first draft had would blind a stadium for the season when
    an office re-grids."""
    route = respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=_points_body()))
    client = NwsClient(env_settings)
    try:
        resolve_point(db_session, client, 1, LSU, NOW)
        later = NOW + POINT_RERESOLVE_AFTER
        resolve_point(db_session, client, 1, LSU, later, force=True)
    finally:
        client.close()
    assert route.call_count == 2


@respx.mock
def test_a_forced_re_resolution_inside_the_day_does_not_call(db_session, env_settings):
    route = respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=_points_body()))
    client = NwsClient(env_settings)
    try:
        resolve_point(db_session, client, 1, LSU, NOW)
        resolve_point(db_session, client, 1, LSU, NOW + timedelta(hours=2), force=True)
    finally:
        client.close()
    assert route.call_count == 1
