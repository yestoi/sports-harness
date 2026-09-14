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


@respx.mock
def test_the_prop_request_asks_for_nine_markets_with_links_and_sids():
    """Expected: the per-event path, the nine market keys, `includeLinks`/`includeSids` true,
    and the bookmakers string untouched (gate 5).

    Computed independently: addendum §3.1 names five families plus the `_alternate` variants of
    the four yardage/count families -- five plus four is nine. The cost is `unique markets
    returned x regions`, so nine is also the per-call credit ceiling.
    """
    route = respx.get("https://o/v4/sports/americanfootball_nfl/events/evt-1/odds").mock(
        return_value=httpx.Response(200, json={"id": "evt-1", "bookmakers": []}))
    c = OddsApiClient(HttpClient(1, sleep=lambda s: None), "https://o/v4", "KEY",
                      "draftkings,pinnacle")
    c.fetch_event_props("americanfootball_nfl", "evt-1")
    q = dict(route.calls[0].request.url.params)
    assert q["markets"].split(",") == [
        "player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions",
        "player_anytime_td", "player_pass_yds_alternate", "player_rush_yds_alternate",
        "player_reception_yds_alternate", "player_receptions_alternate"]
    assert q["includeLinks"] == "true" and q["includeSids"] == "true"
    assert q["bookmakers"] == "draftkings,pinnacle"
    assert q["oddsFormat"] == "decimal" and q["dateFormat"] == "iso"


@respx.mock
def test_the_featured_and_alternates_requests_are_unchanged():
    """Gate 5: this task adds a call, it does not alter one. Neither existing request carries
    the link flags."""
    featured = respx.get("https://o/v4/sports/americanfootball_nfl/odds").mock(
        return_value=httpx.Response(200, json=[]))
    alternates = respx.get("https://o/v4/sports/americanfootball_nfl/events/evt-1/odds").mock(
        return_value=httpx.Response(200, json={"id": "evt-1", "bookmakers": []}))
    c = OddsApiClient(HttpClient(1, sleep=lambda s: None), "https://o/v4", "KEY", "draftkings")
    c.fetch_featured("americanfootball_nfl")
    assert dict(featured.calls[0].request.url.params) == {
        "apiKey": "KEY", "bookmakers": "draftkings", "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal", "dateFormat": "iso"}
    c.fetch_event_alternates("americanfootball_nfl", "evt-1")
    q = dict(alternates.calls[0].request.url.params)
    assert q["markets"] == "alternate_spreads,alternate_totals"
    assert "includeLinks" not in q and "includeSids" not in q
