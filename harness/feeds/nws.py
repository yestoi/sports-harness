"""The National Weather Service client: its own read-only httpx client, GET only.

**Why not `HttpClient.get`.** `harness/feeds/http.py:42` is `get(url, params, redact_params)` and
takes no `headers`, and `:29` records that ESPN 403s on custom User-Agents so httpx's default is
deliberately kept there. NWS *requires* a User-Agent. Adding a headers argument would widen
exactly the client that decision D5 and `test_http_client_has_no_write_methods` rest on, so this
module owns its client instead -- the precedent and the reasoning are
`harness/venues/kalshi/http.py:16-30`, and the same `_ReadOnlyClient` shape applies: a bare
`httpx.Client` carries `post`, `put`, `delete` and `patch`, and a read-only feed has no use for a
working write primitive.

**The User-Agent is fixed by R:212 and is a user gate.** `sports-harness/1 (self-hosted research
harness)`, verbatim, from `Settings.nws_user_agent`. If the service ever answers 403 on it or
blocklists it, that stops and reaches the user; the loop never edits it (ruling A-M11).

**The schema is pinned, not guessed** (addendum 0.10). `POINTS_FIELDS` is the list of
`properties` keys a recorded live response actually carried, and `parse_point` refuses a body
missing any of them rather than filling in a default. The hourly period schema is pinned the same
way in `harness/weather/snapshots.py`.

**One retry, on 429 only.** The documentation's own hint is that a rate-limited request "can
typically be retried after five seconds"; that is a venue-side hint read as data, and the one
retry here is ours. Any other non-200 is returned to the caller, recorded, and skipped.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from harness.feeds.http import FetchResult

log = logging.getLogger(__name__)

#: Roadmap invariant 8. The only host this client may reach, asserted on the parsed hostname of
#: every URL -- including the `forecastHourly` URL, which arrives as venue *data* and is
#: therefore exactly the string a host assertion exists for.
NWS_HOST = "api.weather.gov"

#: The `properties` keys a recorded live `/points` response carried (addendum 0.10). A body
#: missing one of these is refused, so a schema change is a recorded failure and never a silent
#: `None` in a column.
POINTS_FIELDS = ("gridId", "gridX", "gridY", "forecastHourly")

#: The venue's own hint, read as data (verified-facts F4): a rate-limited request "can typically
#: be retried after five seconds". One retry, and only on 429.
RETRY_429_S = 5.0


@dataclass(frozen=True)
class PointGrid:
    office: str
    grid_x: int
    grid_y: int
    forecast_hourly_url: str


class NwsClient:
    """GET-only, one httpx client, the two fixed headers on every request."""

    def __init__(self, settings, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._base = settings.nws_base_url.rstrip("/")
        self._headers = {"User-Agent": settings.nws_user_agent,
                         "Accept": "application/geo+json"}
        self._client = httpx.Client(timeout=settings.http_timeout_s)
        self._sleep = sleep
        self._clock = clock

    def _url(self, path_or_url: str) -> str:
        url = path_or_url if path_or_url.startswith("http") else self._base + path_or_url
        host = urlsplit(url).hostname
        if host != NWS_HOST:
            raise ValueError(f"nws client refused a non-{NWS_HOST} host: {host!r}")
        return url

    def get(self, path_or_url: str) -> FetchResult:
        """One GET, with one five-second retry on 429 and on nothing else."""
        url = self._url(path_or_url)
        for attempt in (1, 2):
            started = time.monotonic()
            response = self._client.get(url, headers=self._headers)
            if response.status_code == 429 and attempt == 1:
                self._sleep(RETRY_429_S)
                continue
            try:
                body = response.json() if response.status_code == 200 else None
            except ValueError:
                body = None
            return FetchResult(status=response.status_code,
                               headers={k.lower(): v for k, v in response.headers.items()},
                               body=body, fetched_at=self._clock(), url=url,
                               elapsed_s=time.monotonic() - started)
        raise AssertionError("unreachable")   # the loop returns on its second pass

    def close(self) -> None:
        self._client.close()


def parse_point(body) -> PointGrid | None:
    """A `/points` response as a grid, or None when the recording's schema no longer holds.

    The hourly URL is venue-supplied data and is host-checked here, not only when it is fetched:
    a stored URL is a stored instruction to connect somewhere, and roadmap invariant 8 governs
    it the same way it governs a configured base URL.
    """
    if not isinstance(body, dict):
        return None
    properties = body.get("properties")
    if not isinstance(properties, dict):
        return None
    if any(field not in properties for field in POINTS_FIELDS):
        return None
    hourly = properties["forecastHourly"]
    if not isinstance(hourly, str) or urlsplit(hourly).hostname != NWS_HOST:
        log.warning("nws /points returned a foreign forecastHourly host; refused")
        return None
    try:
        return PointGrid(office=str(properties["gridId"]), grid_x=int(properties["gridX"]),
                         grid_y=int(properties["gridY"]), forecast_hourly_url=hourly)
    except (TypeError, ValueError):
        return None
