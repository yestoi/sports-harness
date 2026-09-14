import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from harness.feeds.espn import EspnClient, Kickoff, parse_kickoffs
from harness.feeds.http import HttpClient

FIX = json.loads((Path(__file__).parent / "fixtures" / "espn_nfl_scoreboard.json").read_text())


def test_parse_kickoffs_skips_malformed():
    out = parse_kickoffs("nfl", FIX)
    assert out == [Kickoff("nfl", "401872656", datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc),
                           "Seattle Seahawks", "New England Patriots", "STATUS_SCHEDULED")]


@respx.mock
def test_fetch_scoreboard_urls():
    r1 = respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=FIX))
    r2 = respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    c.fetch_scoreboard("nfl")
    c.fetch_scoreboard("ncaaf")
    assert r1.called and dict(r2.calls[0].request.url.params) == {"groups": "80", "limit": "400"}


@respx.mock
def test_fetch_scoreboard_dates_param_added_alongside_existing_params():
    """Fix 14: the dated re-fetch adds `dates=YYYYMMDD` without dropping a sport's other
    params (ncaaf's `groups`/`limit`), and adds it alone for nfl (which has none)."""
    r1 = respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    r2 = respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    c.fetch_scoreboard("nfl", dates="20260908")
    c.fetch_scoreboard("ncaaf", dates="20260908")
    assert dict(r1.calls[0].request.url.params) == {"dates": "20260908"}
    assert dict(r2.calls[0].request.url.params) == {"groups": "80", "limit": "400", "dates": "20260908"}


@respx.mock
def test_the_three_new_fetchers_use_the_recorder_s_own_host_and_paths():
    """Invariant 8: the recorder's own ESPN host and no other. The sport segment is ESPN's own
    (`nfl` / `college-football`), the same mapping `fetch_scoreboard` already uses."""
    summary = respx.get("https://e/nfl/summary").mock(return_value=httpx.Response(200, json={}))
    roster = respx.get("https://e/college-football/teams/99/roster").mock(
        return_value=httpx.Response(200, json={}))
    gamelog = respx.get("https://e/nfl/athletes/4426348/gamelog").mock(
        return_value=httpx.Response(200, json={}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    c.fetch_summary("nfl", "401671789")
    c.fetch_roster("ncaaf", "99")
    c.fetch_gamelog("nfl", "4426348")
    assert dict(summary.calls[0].request.url.params) == {"event": "401671789"}
    assert roster.called and gamelog.called
