"""The authenticated Kalshi transport: one door for every signed call, REST and (phase 5) RFQ.

Conformance item 5. `request()` is the paper-posture fence for the whole phase, so the order of
operations inside it is a security contract:

1. Refuse a non-GET when writes are disabled, before signing, before any network I/O and before
   any recording. A paper transport holds no write-capable client: `_write_client` is not
   constructed at all, and `_read_client` is a `_ReadOnlyClient` facade exposing only `get`,
   `head` and `close`, so neither attribute offers a `post`, `put`, `delete`, `patch` or
   `request`.
2. Assert the host on the *parsed* hostname (roadmap invariant 8). A raw-string suffix test
   would accept a URL whose query string merely contains the real host's name while the URL
   itself points at a foreign host.
3. Sign and send, one `venue_requests` row per attempt, never headers and never bodies.

**Why the transport owns its own httpx clients.** Addendum §1.1 says GET and HEAD go through the
shared `HttpClient.get`, but that cannot be implemented literally: `HttpClient.get(url, params,
redact_params)` takes no headers, so a signed GET cannot be expressed through it, and its own
internal retry loop would multiply signed attempts, which ruling A-I5 forbids (every attempt must
be re-signed with a fresh timestamp and recorded as its own row). `harness/feeds/http.py` is not
changed: it is the unauthenticated recorder's client, and giving it a headers parameter would
widen exactly the path that decision D5 and `test_http_client_has_no_write_methods` rest on. So
the transport holds two clients of its own, a read client built unconditionally and a write
client built only when `writes_enabled`, and takes an explicit `timeout_s` that every caller
fills from `Settings.http_timeout_s` (the same value `build_recorder` passes to `HttpClient`).
The read client is wrapped in `_ReadOnlyClient` because a bare `httpx.Client` carries the write
verbs: the original design got "no write-capable object in a paper process" for free by reading
through `HttpClient`, and owning the client back would otherwise have silently dropped it.
The `http: HttpClient` argument stays in the signature for its clock, so `FetchResult.fetched_at`
matches the recorder's clock, and for nothing else.

**The transport never logs a header dict.** It never logs a request body either, and the
`venue_requests` row has no field that could hold one. The roadmap-invariant-4 filter in
`harness/logging_setup.py` redacts a signed value in `HEADER: value` text form but does not match
a dict repr, so this module does not depend on it: it hands the logging layer no header mapping in
the first place. `test_a_signed_request_emits_no_log_record_carrying_a_header_value` asserts that
at every level against the real signature that goes out on the wire. Any future caller that logs a
headers mapping would defeat this, so do not.

A transport error surfaces as `VenueTransportError`, never as the raw `httpx` exception. An httpx
exception carries `.request`, whose headers hold the live signed values, so a caller that logged it
would put a working signature in a log. The wrapper holds the method, the signed path and the name
of the underlying exception class, and nothing else; it is raised clear of the handler so neither
`__cause__` nor `__context__` leads back to the request. Tasks 6 to 8 catch `VenueTransportError`.
"""
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from harness.db.models import VenueRequest
from harness.feeds.http import FetchResult, HttpClient
from harness.venues.kalshi.auth import sign_request

#: Roadmap invariant 8. `prod` may only reach the production REST host; `demo` may only reach a
#: host whose registrable name ends in demo.kalshi.co. Checked on the parsed hostname, never on
#: the URL string: a suffix test on the raw string passes a foreign host whose query string
#: merely happens to contain the real host's name.
PROD_HOSTS = frozenset({"api.elections.kalshi.com"})
DEMO_HOST_SUFFIX = "demo.kalshi.co"

RETRY_429_MAX = 3          # attempts after the first, per §1.1
RETRY_AFTER_DEFAULT_S = 2.0
RETRY_AFTER_CAP_S = 10.0
RETRY_5XX_MAX = 1          # a 5xx is retried once
RETRY_TRANSPORT_MAX = 1    # a transport error is retried once, and only for GET/HEAD
RETRY_BACKOFF_S = 1.0

#: The only methods a paper transport may send, and the only ones that are safe to resend.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD"})


class _ReadOnlyClient:
    """An httpx client with only the safe verbs on it.

    Conformance item 5 requires that a paper transport hold no write-capable client. The transport
    owns its read client (see the module docstring for why), and a bare `httpx.Client` carries
    `post`, `put`, `delete`, `patch` and `request`, so handing one to a paper transport would leave
    a working write primitive sitting in the process. This facade keeps the client private and
    exposes `get`, `head` and `close`, and nothing else.
    """

    __slots__ = ("_client",)

    def __init__(self, timeout_s: float) -> None:
        self._client = httpx.Client(timeout=timeout_s)

    def get(self, url: str, params: dict | None = None,
            headers: dict[str, str] | None = None) -> httpx.Response:
        return self._client.get(url, params=params, headers=headers)

    def head(self, url: str, params: dict | None = None,
             headers: dict[str, str] | None = None) -> httpx.Response:
        return self._client.head(url, params=params, headers=headers)

    def close(self) -> None:
        self._client.close()


class PaperModeViolation(RuntimeError):
    """A non-GET was attempted on a transport whose writes are disabled. Raised before signing
    and before any network I/O, so a paper process cannot even build the request."""

    def __init__(self, method: str, path: str) -> None:
        super().__init__(f"{method} {path} refused: this transport has writes disabled")
        self.method, self.path = method, path


class VenueTransportError(RuntimeError):
    """A network-layer failure, with the venue's request object deliberately left behind.

    `httpx` exceptions carry `.request`, and that request's headers hold the live
    `KALSHI-ACCESS-SIGNATURE` and `KALSHI-ACCESS-KEY` values. The log-redaction filter matches
    `HEADER: value` text and not a repr, so a caller that logged such an exception would put a
    usable signature in a log. This type holds only the method, the signed path and the name of
    the underlying exception class, so there is nothing sensitive to leak.
    """

    def __init__(self, method: str, path: str, cause_class_name: str) -> None:
        super().__init__(f"{method} {path} failed: {cause_class_name}")
        self.method, self.path, self.cause_class_name = method, path, cause_class_name


class HostNotAllowed(RuntimeError):
    """The resolved hostname is not the one this environment is pinned to (invariant 8)."""


@dataclass(frozen=True)
class VenueRequestRow:
    """One attempt. Deliberately has no headers field and no body field (pre-loaded decision 7)."""

    venue: str
    env: str
    method: str
    path: str
    status: int | None
    ts: datetime
    elapsed_ms: int | None


class KalshiTransport:
    """Routes every authenticated Kalshi call. See the module docstring for the contract."""

    def __init__(self, http: HttpClient, base_url: str, env: str, key_id: str,
                 private_key_pem: bytes, timeout_s: float, clock_offset_ms: int = 0,
                 writes_enabled: bool = False,
                 recorder: Callable[[VenueRequestRow], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._env = env
        self._key_id = key_id
        self._pem = private_key_pem
        self._clock_offset_ms = clock_offset_ms
        self._writes_enabled = writes_enabled
        self._recorder = recorder
        self._sleep = sleep
        self._clock = clock
        self._read_client = _ReadOnlyClient(timeout_s)
        # The whole point of conformance item 5: with writes disabled this stays None, so the
        # process holds no object that can send a POST, PUT, PATCH or DELETE.
        self._write_client = httpx.Client(timeout=timeout_s) if writes_enabled else None
        self._counters = {"venue_429": 0, "attempts": 0}

    # -- the fence ---------------------------------------------------------------------------

    def request(self, method: str, path: str, params: dict | None = None,
                json: dict | None = None) -> FetchResult:
        method = method.upper()
        # 1. Nothing above this line does I/O, signing or recording.
        if method not in IDEMPOTENT_METHODS and not self._writes_enabled:
            raise PaperModeViolation(method, path)

        # 2. Host assertion on the parsed hostname, before a signature exists.
        url = self._base_url + path
        self._assert_host(url)

        # 3. Attempt loop. Every attempt is re-signed with a fresh timestamp (A-I5) and
        #    recorded as its own row.
        signed_path = urlsplit(url).path          # the /trade-api/v2 prefix in, the query out
        idempotent = method in IDEMPOTENT_METHODS
        if not idempotent and self._write_client is None:
            raise PaperModeViolation(method, path)   # unreachable; a guard that survives python -O
        n_429 = n_5xx = n_err = 0

        while True:
            now = self._clock()
            ts_ms = int(now.timestamp() * 1000) + self._clock_offset_ms
            headers = sign_request(self._key_id, self._pem, method, signed_path, ts_ms)
            self._counters["attempts"] += 1
            t0 = time.monotonic()
            failure = None
            try:
                resp = self._send(method, url, params, json, headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # Keep the class name, never the exception: its .request holds the signed headers.
                failure = VenueTransportError(method, path, type(exc).__name__)
                self._record(method, path, None, now, _ms_since(t0))

            if failure is not None:
                # A non-idempotent request is never resent blind: the caller reconciles by
                # client_order_id, because the venue may well have accepted the order.
                if idempotent and n_err < RETRY_TRANSPORT_MAX:
                    n_err += 1
                    self._sleep(RETRY_BACKOFF_S)
                    continue
                # Raised clear of the except block, so __context__ is empty too; `from None`
                # alone would leave the httpx exception, and its request, on __context__.
                raise failure from None

            elapsed_ms = _ms_since(t0)
            self._record(method, path, resp.status_code, now, elapsed_ms)

            if resp.status_code == 429:
                # Counts into runs.notes / the heartbeat as venue_429, never into the §9.4
                # outage counter.
                self._counters["venue_429"] += 1
                if idempotent and n_429 < RETRY_429_MAX:
                    n_429 += 1
                    self._sleep(_retry_after_s(resp))
                    continue
            elif 500 <= resp.status_code < 600 and idempotent and n_5xx < RETRY_5XX_MAX:
                n_5xx += 1
                self._sleep(RETRY_BACKOFF_S)
                continue

            return self._fetch_result(resp, elapsed_ms)

    # -- helpers -----------------------------------------------------------------------------

    def _send(self, method: str, url: str, params: dict | None, json: dict | None,
              headers: dict[str, str]) -> httpx.Response:
        """Dispatch by verb. GET and HEAD are the only two the read facade can express, and every
        other verb has already been refused unless `_write_client` exists."""
        if method == "GET":
            return self._read_client.get(url, params=params, headers=headers)
        if method == "HEAD":
            return self._read_client.head(url, params=params, headers=headers)
        return self._write_client.request(method, url, params=params, json=json, headers=headers)

    def _assert_host(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            # A signature in cleartext is a leaked signature. The base URL comes from Settings, not
            # from venue data, so this is a guard against a misconfiguration rather than an attack.
            raise HostNotAllowed(f"only https may be signed, not {parts.scheme!r}")
        host = parts.hostname                     # parsed, never a raw-string suffix test
        if self._env == "prod":
            if host not in PROD_HOSTS:
                raise HostNotAllowed(f"prod may not reach host {host!r}")
        elif self._env == "demo":
            if not host or not (host == DEMO_HOST_SUFFIX
                                or host.endswith("." + DEMO_HOST_SUFFIX)):
                raise HostNotAllowed(f"demo may not reach host {host!r}")
        else:
            raise HostNotAllowed(f"unknown env {self._env!r}")

    def _record(self, method: str, path: str, status: int | None, ts: datetime,
                elapsed_ms: int | None) -> None:
        if self._recorder is None:
            return
        self._recorder(VenueRequestRow("kalshi", self._env, method, path, status, ts, elapsed_ms))

    def _fetch_result(self, resp: httpx.Response, elapsed_ms: int) -> FetchResult:
        try:
            body = resp.json()
        except ValueError:
            body = None
        return FetchResult(
            status=resp.status_code,
            headers={k.lower(): v for k, v in resp.headers.items()},
            body=body,
            # The recorder's clock, so fetched_at lines up with every other stored fetch.
            fetched_at=self._http._clock(),
            url=str(resp.request.url),
            elapsed_s=elapsed_ms / 1000.0,
        )

    @property
    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    def close(self) -> None:
        self._read_client.close()
        if self._write_client is not None:
            self._write_client.close()


def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _retry_after_s(resp: httpx.Response) -> float:
    """Seconds to wait after a 429: Retry-After, defaulting to 2 s and capped at 10 s.

    Retry-After is venue text, so it is untrusted: a date form, a non-number, a NaN or an
    infinity all fall back to the default rather than letting the venue choose how long this
    process sleeps. The result is then clamped into [0, 10].
    """
    try:
        value = float(resp.headers.get("Retry-After"))
    except (TypeError, ValueError):
        value = RETRY_AFTER_DEFAULT_S
    if not math.isfinite(value):
        value = RETRY_AFTER_DEFAULT_S
    return min(max(value, 0.0), RETRY_AFTER_CAP_S)


def session_recorder(session_factory) -> Callable[[VenueRequestRow], None]:
    """A recorder that writes one `venue_requests` row per call on its own short session, so a
    transport call cannot enlist in (or roll back with) a caller's transaction."""

    def record(row: VenueRequestRow) -> None:
        with session_factory() as session:
            session.add(VenueRequest(
                venue=row.venue, env=row.env, method=row.method, path=row.path[:128],
                status=row.status, ts=row.ts, elapsed_ms=row.elapsed_ms,
            ))
            session.commit()

    return record
