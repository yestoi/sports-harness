import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.venues.kalshi.public import (FOOTBALL_SERIES, KalshiPublic, MarketSummary,
                                          event_date_from_ticker, parse_market_summaries)

FIX = json.loads((Path(__file__).parent / "fixtures" / "kalshi_markets_page.json").read_text())


def test_event_date_from_ticker():
    assert event_date_from_ticker("KXNFLGAME-26SEP21NYGLAR") == date(2026, 9, 21)
    assert event_date_from_ticker("KXNCAAFSPREAD-26SEP12BUFFFIU") == date(2026, 9, 12)
    assert event_date_from_ticker("WEIRD") is None


def test_parse_market_summaries():
    out = parse_market_summaries(FIX)
    assert len(out) == 3
    m = out[0]
    assert m == MarketSummary("KXNFLGAME-26SEP21NYGLAR-NYG", "KXNFLGAME-26SEP21NYGLAR", "KXNFLGAME", date(2026, 9, 21),
                              Decimal("0.2200"), Decimal("0.2300"), Decimal("1234.00"),
                              datetime(2026, 9, 24, 0, 15, tzinfo=timezone.utc))
    assert out[2].yes_bid is None and out[2].volume_fp == Decimal("0") and out[2].event_date is None


@respx.mock
def test_fetch_markets_all_follows_cursor():
    page1 = {"cursor": "abc", "markets": [{"ticker": "A", "event_ticker": "A"}]}
    page2 = {"cursor": "", "markets": [{"ticker": "B", "event_ticker": "B"}]}
    route = respx.get("https://k/markets").mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    c = KalshiPublic(HttpClient(1, sleep=lambda s: None), "https://k", sleep_s=0, sleep=lambda s: None)
    pages = c.fetch_markets_all("KXNFLGAME")
    assert [p.body["markets"][0]["ticker"] for p in pages] == ["A", "B"]
    q0 = dict(route.calls[0].request.url.params)
    q1 = dict(route.calls[1].request.url.params)
    assert q0 == {"series_ticker": "KXNFLGAME", "status": "open", "limit": "1000"}
    assert q1["cursor"] == "abc"


@respx.mock
def test_fetch_orderbook_and_trades_params():
    ob = respx.get("https://k/markets/T1/orderbook").mock(return_value=httpx.Response(200, json={"orderbook_fp": {}}))
    tr = respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    c = KalshiPublic(HttpClient(1, sleep=lambda s: None), "https://k", sleep_s=0, sleep=lambda s: None)
    c.fetch_orderbook("T1")
    c.fetch_trades("T1", datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc))
    assert dict(ob.calls[0].request.url.params) == {"depth": "20"}
    assert dict(tr.calls[0].request.url.params) == {"ticker": "T1", "limit": "1000", "min_ts": "1788710400"}


def test_football_series_constant():
    assert "KXNCAAFTOTAL" in FOOTBALL_SERIES and len(FOOTBALL_SERIES) == 6
