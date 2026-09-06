import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from harness.feeds.http import FetchResult, HttpClient

FOOTBALL_SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL", "KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_DATE_RE = re.compile(r"^[A-Z]+-(\d{2})([A-Z]{3})(\d{2})")


@dataclass(frozen=True)
class MarketSummary:
    ticker: str
    event_ticker: str
    series_ticker: str
    event_date: date | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    volume_fp: Decimal
    close_time: datetime | None


def event_date_from_ticker(event_ticker: str) -> date | None:
    m = _DATE_RE.match(event_ticker or "")
    if not m:
        return None
    yy, mon, dd = m.groups()
    try:
        return date(2000 + int(yy), _MONTHS[mon], int(dd))
    except (KeyError, ValueError):
        return None


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_market_summaries(body: dict | list | None) -> list[MarketSummary]:
    if not isinstance(body, dict):
        return []
    out: list[MarketSummary] = []
    for m in body.get("markets", []):
        ticker = m.get("ticker")
        event_ticker = m.get("event_ticker", "")
        if not ticker:
            continue
        out.append(MarketSummary(
            ticker=ticker,
            event_ticker=event_ticker,
            series_ticker=event_ticker.split("-")[0] if event_ticker else "",
            event_date=event_date_from_ticker(event_ticker),
            yes_bid=_dec(m.get("yes_bid_dollars")),
            yes_ask=_dec(m.get("yes_ask_dollars")),
            volume_fp=_dec(m.get("volume_fp")) or Decimal("0"),
            close_time=_ts(m.get("close_time")),
        ))
    return out


class KalshiPublic:
    def __init__(self, http: HttpClient, base_url: str, sleep_s: float, sleep: Callable[[float], None] = time.sleep):
        self._http = http
        self._base = base_url.rstrip("/")
        self._sleep_s = sleep_s
        self._sleep = sleep

    def _pause(self) -> None:
        if self._sleep_s:
            self._sleep(self._sleep_s)

    def fetch_markets_all(self, series_ticker: str, max_pages: int = 20) -> list[FetchResult]:
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": "open", "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/markets", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages

    def fetch_orderbook(self, ticker: str) -> FetchResult:
        r = self._http.get(f"{self._base}/markets/{ticker}/orderbook", params={"depth": "20"}, redact_params=())
        self._pause()
        return r

    def fetch_trades(self, ticker: str, min_ts: datetime) -> FetchResult:
        r = self._http.get(f"{self._base}/markets/trades",
                           params={"ticker": ticker, "limit": "1000", "min_ts": str(int(min_ts.timestamp()))},
                           redact_params=())
        self._pause()
        return r
