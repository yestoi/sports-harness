import json
from pathlib import Path

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.venues.kalshi.public import KalshiPublic

FIX = json.loads((Path(__file__).parent / "fixtures" / "kalshi_events_page.json").read_text())


@respx.mock
def test_fetch_events_all_follows_cursor():
    p1 = {"cursor": "c2", "events": FIX["events"][:1]}
    p2 = {"cursor": "", "events": FIX["events"][1:2]}
    route = respx.get("https://k/events").mock(side_effect=[httpx.Response(200, json=p1), httpx.Response(200, json=p2)])
    c = KalshiPublic(HttpClient(1, sleep=lambda s: None), "https://k", sleep_s=0, sleep=lambda s: None)
    pages = c.fetch_events_all("KXNCAAFGAME")
    assert len(pages) == 2
    q0 = dict(route.calls[0].request.url.params)
    assert q0 == {"series_ticker": "KXNCAAFGAME", "status": "open", "limit": "200"}
    assert dict(route.calls[1].request.url.params)["cursor"] == "c2"
