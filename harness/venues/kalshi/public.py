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


def decode_fixed_point(v) -> Decimal | None:
    """A Kalshi `*_dollars` or `*_fp` fixed-point string as a Decimal, or None.

    Public since phase 5: the futures snapshot writer decodes the same strings from the same
    venue and there is no reason for two copies of the rule in the venue package.
    `harness/normalize/kalshi.py` keeps its own private copy; the normalizer is not this phase's
    file and the two are asserted equal by `test_the_dollars_and_fp_strings_are_decoded`.
    """
    if v in (None, ""):
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
        vol = decode_fixed_point(m.get("volume_fp"))
        out.append(MarketSummary(
            ticker=ticker,
            event_ticker=event_ticker,
            series_ticker=event_ticker.split("-")[0] if event_ticker else "",
            event_date=event_date_from_ticker(event_ticker),
            yes_bid=decode_fixed_point(m.get("yes_bid_dollars")),
            yes_ask=decode_fixed_point(m.get("yes_ask_dollars")),
            volume_fp=vol if vol is not None else Decimal("0"),
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

    def fetch_markets_all(self, series_ticker: str, max_pages: int = 20, status: str = "open",
                          min_settled_ts: int | None = None,
                          extra_params: dict | None = None) -> list[FetchResult]:
        """Every page of `GET /markets` for one series at one `status`.

        `min_settled_ts` (Unix seconds) is the only timestamp filter Kalshi accepts alongside
        `status="settled"`; it is meaningless for `status="open"`, so callers leave it None there.
        `extra_params` is merged into the query as-is, so a caller (the futures pass) can add
        `mve_filter=exclude` without a second method.
        """
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": status, "limit": "1000"}
            if min_settled_ts is not None:
                params["min_settled_ts"] = str(min_settled_ts)
            if extra_params:
                params.update(extra_params)
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/markets", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages

    def fetch_tags_by_categories(self) -> FetchResult:
        """The venue's category-to-tags map (addendum 0.9). `GET /series` has no ticker-prefix
        filter, so discovery has to enumerate categories, and this is the only endpoint that
        names them. Read once per futures pass."""
        r = self._http.get(f"{self._base}/search/tags_by_categories", redact_params=())
        self._pause()
        return r

    def fetch_series_all(self, category: str, max_pages: int = 10) -> list[FetchResult]:
        """Every page of `GET /series` for one category. The response carries no cursor in the
        documented schema, so `max_pages` is a ceiling this loop never normally reaches; it is
        here so a venue that starts paging cannot turn the pass into an unbounded walk."""
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"category": category}
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/series", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages

    def fetch_events_all(self, series_ticker: str, max_pages: int = 10) -> list[FetchResult]:
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": "open", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/events", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages

    def fetch_series(self, series_ticker: str) -> FetchResult:
        """F45/R21: a series' fee shape (`fee_type`/`fee_multiplier`), fetched once a day per
        football series (Recorder._kalshi_series) since it rarely changes."""
        r = self._http.get(f"{self._base}/series/{series_ticker}", redact_params=())
        self._pause()
        return r

    def fetch_market(self, ticker: str) -> FetchResult:
        """A single market's current body. No caller yet (Task 7)."""
        r = self._http.get(f"{self._base}/markets/{ticker}", redact_params=())
        self._pause()
        return r

    def fetch_orderbook(self, ticker: str) -> FetchResult:
        r = self._http.get(f"{self._base}/markets/{ticker}/orderbook", params={"depth": "20"}, redact_params=())
        self._pause()
        return r

    def fetch_trades(self, ticker: str, min_ts: datetime, max_pages: int = 20) -> list[FetchResult]:
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"ticker": ticker, "limit": "1000", "min_ts": str(int(min_ts.timestamp()))}
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/markets/trades", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages
