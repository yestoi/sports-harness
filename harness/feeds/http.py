import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class FetchResult:
    status: int
    headers: dict[str, str]
    body: dict | list | None
    fetched_at: datetime
    url: str
    elapsed_s: float


class HttpClient:
    def __init__(self, timeout_s: float, sleep: Callable[[float], None] = time.sleep):
        self._client = httpx.Client(timeout=timeout_s, headers={"User-Agent": "harness-recorder/0.1"})
        self._sleep = sleep

    def _redacted_url(self, resp: httpx.Response, redact_params: tuple[str, ...]) -> str:
        u = resp.request.url
        params = dict(u.params)
        for k in redact_params:
            if k in params:
                params[k] = "[REDACTED]"
        return str(u.copy_with(params=params))

    def get(self, url: str, params: dict | None = None, redact_params: tuple[str, ...] = ("apiKey",)) -> FetchResult:
        attempts = 0
        while True:
            attempts += 1
            t0 = time.monotonic()
            try:
                resp = self._client.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempts >= 2:
                    raise FetchError(f"{url}: {e!r}") from e
                self._sleep(1.0)
                continue
            elapsed = time.monotonic() - t0
            if resp.status_code == 429 and attempts < 2:
                ra = resp.headers.get("Retry-After")
                self._sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2.0)
                continue
            if 500 <= resp.status_code < 600 and attempts < 2:
                self._sleep(1.0)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = None
            return FetchResult(
                status=resp.status_code,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=body,
                fetched_at=datetime.now(timezone.utc),
                url=self._redacted_url(resp, redact_params),
                elapsed_s=elapsed,
            )

    def close(self) -> None:
        self._client.close()
