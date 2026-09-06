import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient, parse_credit_headers, parse_event_ids_and_times

FIX = json.loads((Path(__file__).parent / "fixtures" / "odds_featured_nfl.json").read_text())


@respx.mock
def test_fetch_featured_builds_request():
    route = respx.get("https://o/v4/sports/americanfootball_nfl/odds").mock(
        return_value=httpx.Response(200, json=FIX, headers={"x-requests-last": "3", "x-requests-used": "10", "x-requests-remaining": "4990"}))
    c = OddsApiClient(HttpClient(1, sleep=lambda s: None), "https://o/v4", "KEY", "pinnacle,lowvig")
    r = c.fetch_featured("americanfootball_nfl")
    q = dict(route.calls[0].request.url.params)
    assert q == {"apiKey": "KEY", "bookmakers": "pinnacle,lowvig", "markets": "h2h,spreads,totals",
                 "oddsFormat": "decimal", "dateFormat": "iso"}
    assert r.body == FIX and "KEY" not in r.url


@respx.mock
def test_fetch_event_alternates_builds_request():
    route = respx.get("https://o/v4/sports/americanfootball_ncaaf/events/abc/odds").mock(
        return_value=httpx.Response(200, json={"id": "abc", "bookmakers": []}))
    c = OddsApiClient(HttpClient(1, sleep=lambda s: None), "https://o/v4", "KEY", "pinnacle")
    c.fetch_event_alternates("americanfootball_ncaaf", "abc")
    assert dict(route.calls[0].request.url.params)["markets"] == "alternate_spreads,alternate_totals"


def test_parse_credit_headers():
    c = parse_credit_headers({"x-requests-last": "3", "x-requests-used": "10", "x-requests-remaining": "4990"})
    assert (c.last, c.used, c.remaining) == (3, 10, 4990)
    z = parse_credit_headers({})
    assert (z.last, z.used, z.remaining) == (0, 0, 0)


def test_parse_event_ids_and_times():
    out = parse_event_ids_and_times(FIX)
    assert out == [("e1f2a3", datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc))]
    assert parse_event_ids_and_times(None) == []
