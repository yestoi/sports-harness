from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.venues.kalshi import clock as clock_module
from harness.venues.kalshi.clock import server_time_offset_ms

LOCAL_NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return LOCAL_NOW


@respx.mock
def test_server_time_offset_ms_returns_offset_from_date_header(monkeypatch):
    monkeypatch.setattr(clock_module, "datetime", _FixedDatetime)
    server_now = LOCAL_NOW + timedelta(seconds=480)
    respx.get("https://k/exchange/status").mock(
        return_value=httpx.Response(200, json={}, headers={"date": format_datetime(server_now, usegmt=True)}))
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: LOCAL_NOW)
    offset = server_time_offset_ms(http, "https://k")
    assert offset is not None
    assert abs(offset - 480000) <= 2000


@respx.mock
def test_server_time_offset_ms_returns_none_when_date_header_missing():
    respx.get("https://k/exchange/status").mock(return_value=httpx.Response(200, json={}))
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: LOCAL_NOW)
    assert server_time_offset_ms(http, "https://k") is None


@respx.mock
def test_server_time_offset_ms_returns_none_on_non_2xx():
    respx.get("https://k/exchange/status").mock(
        return_value=httpx.Response(500, json={}, headers={"date": format_datetime(LOCAL_NOW, usegmt=True)}))
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: LOCAL_NOW)
    assert server_time_offset_ms(http, "https://k") is None


@respx.mock
def test_server_time_offset_ms_returns_none_on_unparseable_date_header():
    respx.get("https://k/exchange/status").mock(
        return_value=httpx.Response(200, json={}, headers={"date": "not-a-date"}))
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: LOCAL_NOW)
    assert server_time_offset_ms(http, "https://k") is None
