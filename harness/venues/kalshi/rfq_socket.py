"""The RFQ listener's own WebSocket connection, inside `app-ws` (addendum §1.6, ruling A-C1).

**Why a second socket.** `harness/venues/kalshi/ws.py:32-36` is `should_reconnect(...): if
error_msg: return True` -- any error frame on the socket, from any channel. It is called with the
last error frame seen and a True there drops the socket, resets every sequence number and the gap
state, and re-enters a backoff that doubles to 60 s. A `communications` subscribe refused with
code 9, 10 or 11 would therefore not idle a listener; it would kill the orderbook recorder and
keep killing it. `_resubscribe` compounds it: it names `self._sids` wholesale in every
five-minute `update_subscription`, so the communications sid would ride a frame carrying
`market_tickers` on a channel documented to ignore market specification. One more socket costs
one socket and removes both.

**This module holds no REST transport.** No HTTP client of any kind, no signed-client wrapper --
asserted statically by `tests/test_rfq_refusal.py`. It owns the subscribe frame, so it can write
to its socket; the handler it feeds (`harness/venues/kalshi/rfq.py`) cannot, and receives parsed
frames only.

**Idling, not reconnecting** (0.8, F71). A handshake status other than 101, or a documented
error-frame code in reply to the subscribe, idles the listener for an hour and writes
`venue_status('kalshi_rfq', 'prod', 'unavailable', reason)`. The market channels are on a
different socket in a different thread and are unaffected either way.

**Nothing here is allowed to take `app-ws` down.** Every loop failure is caught and answered
with a backoff, every `venue_status` write is best-effort, and the recorder's own helpers
(`should_reconnect`, `is_stale`, `RECV_TIMEOUT_S`) are *imported* rather than shared: the two
sockets agree on the rules and share no state at all.
"""
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import websocket

from harness.execution.venue import (STATUS_OK, STATUS_UNAVAILABLE, mark_status,
                                     sanitize_venue_text)
from harness.venues.kalshi.auth import sign_request
from harness.venues.kalshi.rfq import CHANNEL, ENV, IDLE_S, VENUE, handle_frame, idle_reason
from harness.venues.kalshi.ws import RECV_TIMEOUT_S, is_stale, should_reconnect

log = logging.getLogger(__name__)

#: Its own subscribe id sequence, independent of the recorder's (addendum §1.6). The recorder
#: uses 1 for its market subscribe on its own socket; these two ids never meet.
SUBSCRIBE_ID = 1
#: The backoff ceiling, the same shape the recorder uses.
BACKOFF_MAX_S = 60.0
#: The signed path of the WebSocket handshake, the same one the recorder signs.
WS_SIGN_PATH = "/trade-api/ws/v2"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RfqListener:
    """One connection, one channel, one handler. Runs on its own thread inside `app-ws`."""

    def __init__(self, settings, session_factory,
                 ws_factory: Callable = websocket.create_connection,
                 clock: Callable[[], datetime] = _utcnow,
                 sleep: Callable[[float], None] | None = None,
                 sign: Callable[..., dict] | None = None) -> None:
        self.s = settings
        self._factory = session_factory
        self._ws_factory = ws_factory
        self._clock = clock
        self._sleep = sleep or time.sleep
        # The signing seam. `None` is production: `sign_request` over the two key files
        # `app-ws` already mounts. A test passes a stub, because `Settings`' defaults point at
        # `/run/secrets/...` and reading them on the Mac raises before the socket is reached.
        self._sign = sign
        self._stop = False
        self._backoff = 1.0
        self.connected = False
        self.idle_until: datetime | None = None
        self.quote_events_dropped = 0
        self.arrivals = 0
        self._sid: int | None = None
        self._subscribed_at: float = 0.0
        self._timeouts = 0
        # The signing clock offset, carried across reconnects. It only ever moves on a 401 that
        # came back with a usable `Date`; there is no HTTP client here to ask for the time.
        self._offset_ms = 0

    # -- lifecycle -----------------------------------------------------------------------

    def stop(self, *_) -> None:
        self._stop = True

    def _open(self, offset_ms: int):
        """One signed handshake at one host, and nothing else."""
        ts_ms = int(self._clock().timestamp() * 1000) + offset_ms
        signer = self._sign
        if signer is None:
            headers = sign_request(self.s.kalshi_key_id(), self.s.kalshi_private_key_pem(),
                                   "GET", WS_SIGN_PATH, ts_ms)
        else:
            headers = signer("GET", WS_SIGN_PATH, ts_ms)
        # One host, always: `Settings.kalshi_ws_url`. `ws.py`'s FALLBACK_URL is
        # `external-api-ws.kalshi.com`, which is not on roadmap invariant 8's list, and a
        # listener is not a good enough reason to reach a host the invariant does not permit.
        ws = self._ws_factory(self.s.kalshi_ws_url,
                              header=[f"{k}: {v}" for k, v in headers.items()],
                              timeout=RECV_TIMEOUT_S)
        self.connected = True
        return ws

    def _offset_from_response_date(self, resp_headers: dict | None) -> int | None:
        """The signing offset implied by the refused handshake's own `Date` header.

        The recorder's 401 recovery (`ws.py:151-186`) needs no HTTP client either: the venue
        tells us its clock in the response that refused us. So the listener can do the same
        while still holding no transport at all.
        """
        if not resp_headers:
            return None
        date_header = resp_headers.get("date") or resp_headers.get("Date")
        if not date_header:
            return None
        try:
            server_dt = parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            return None
        if server_dt.tzinfo is None:
            server_dt = server_dt.replace(tzinfo=timezone.utc)
        return int((server_dt - self._clock()).total_seconds() * 1000)

    def connect(self):
        """The handshake, with one retry on a 401 that carried a usable `Date`.

        A clock skew and a refusal are different failures and must not read the same in
        `venue_status`. Only a 401 we cannot explain by the clock -- or a second one after the
        correction -- reaches the caller, and 0.8 idles on that unchanged.
        """
        try:
            return self._open(self._offset_ms)
        except websocket.WebSocketBadStatusException as exc:
            if getattr(exc, "status_code", None) != 401:
                raise
            offset = self._offset_from_response_date(getattr(exc, "resp_headers", None))
            if offset is None:
                raise
            log.info("rfq listener handshake 401; retrying once with a server-derived offset "
                     "of %d ms", offset)
            self._offset_ms = offset
            return self._open(offset)

    def subscribe(self, ws) -> None:
        """One frame, one channel, no market tickers and no sids (ruling A-I1)."""
        ws.send(json.dumps({"id": SUBSCRIBE_ID, "cmd": "subscribe",
                            "params": {"channels": [CHANNEL]}}))
        self._sid = None
        self._subscribed_at = time.monotonic()
        self._timeouts = 0

    # -- the loop ------------------------------------------------------------------------

    def run_once(self, ws) -> bool:
        """Read one frame and act on it. Returns False when the caller should drop the socket.

        Dropping the socket and idling are different answers and the difference is the point: a
        drop reconnects behind the backoff, an idle stops for an hour. Only a refusal idles.
        """
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            return self._on_timeout()
        self._timeouts = 0
        if not raw:
            return False
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            log.warning("rfq listener received a frame it could not decode; dropped")
            return True
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "subscribed":
            body = msg.get("msg")
            self._sid = body.get("sid") if isinstance(body, dict) else msg.get("sid")
            self._backoff = 1.0
            self._mark(STATUS_OK, None)
            log.info("rfq listener subscribed to %s, sid=%s", CHANNEL, self._sid)
            return True
        if kind == "error":
            reason = idle_reason(None, msg)
            if reason is None:
                log.warning("rfq listener error frame, not an idling code: %s",
                            json.dumps(msg, default=str)[:200])
                return True
            self._idle(reason)
            return False
        if isinstance(kind, str) and kind.lower().startswith("quote"):
            # Quote events reach a quote's creator or an RFQ's creator. We are neither, so this
            # is a fact about the account and not about a position: counted and dropped.
            self.quote_events_dropped += 1
            log.info("rfq listener dropped a quote event; untrusted venue text: type=%r",
                     sanitize_venue_text(kind, 32))
            return True
        with self._factory() as session:
            row = handle_frame(session, msg, self._clock())
            if row is not None:
                session.commit()
                self.arrivals += 1
        return True

    def _on_timeout(self) -> bool:
        """Silence. Two ways it is fatal to this socket and one way it is nothing.

        A subscribe that was never acked inside the window is a dead subscription, and a socket
        that has stayed open while delivering nothing for `ws_stale_s` is half-open. Both drop
        the connection; neither idles, because neither is the venue refusing us.
        """
        self._timeouts += 1
        if should_reconnect(1 if self._sid is not None else 0,
                            time.monotonic() - self._subscribed_at, None):
            log.warning("rfq listener: no subscribe ack inside the window; reconnecting")
            return False
        if is_stale(self._timeouts, RECV_TIMEOUT_S, self.s.ws_stale_s):
            log.warning("rfq listener: no frame for %.0fs; reconnecting",
                        self._timeouts * RECV_TIMEOUT_S)
            return False
        return True

    def run_forever(self) -> None:
        if not self.s.rfq_listener_enabled:
            log.info("rfq listener disabled by rfq_listener_enabled")
            return
        if not self.s.has_kalshi_credentials():
            log.info("rfq listener dormant: no kalshi credentials")
            return
        while not self._stop:
            now = self._clock()
            if self.idle_until is not None and now < self.idle_until:
                self._sleep(min(60.0, (self.idle_until - now).total_seconds()))
                continue
            self.idle_until = None
            ws = None
            try:
                ws = self.connect()
                self.subscribe(ws)
                while not self._stop and self.run_once(ws):
                    pass
                if self.idle_until is None and not self._stop:
                    # A clean drop -- a closed socket, a dead subscription, a half-open
                    # connection -- still goes behind the backoff, or a venue that closes on
                    # accept would have us reconnecting in a tight loop.
                    self._sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, BACKOFF_MAX_S)
            except websocket.WebSocketBadStatusException as exc:
                # 0.8: a handshake status other than 101.
                status = getattr(exc, "status_code", None)
                self._idle(idle_reason(status, None) or f"handshake {status}")
            except Exception as exc:  # noqa: BLE001 - a listener never takes app-ws down
                log.warning("rfq listener loop error: %s; reconnecting in %.0fs",
                            type(exc).__name__, self._backoff)
                self._sleep(self._backoff)
                self._backoff = min(self._backoff * 2, BACKOFF_MAX_S)
            finally:
                self.connected = False
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        log.debug("rfq ws close failed", exc_info=True)

    # -- venue_status --------------------------------------------------------------------

    def _idle(self, reason: str) -> None:
        self.idle_until = self._clock() + timedelta(seconds=IDLE_S)
        log.warning("rfq listener idling for %ds; untrusted venue text: reason=%r",
                    IDLE_S, reason)
        self._mark(STATUS_UNAVAILABLE, reason)

    def _mark(self, status: str, reason: str | None) -> None:
        try:
            with self._factory() as session:
                mark_status(session, VENUE, ENV, status, reason, self._clock())
                session.commit()
        except Exception:  # noqa: BLE001 - a status write never stops the listener
            log.exception("rfq listener could not record venue_status")
