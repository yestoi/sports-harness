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
