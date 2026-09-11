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
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import websocket

from harness.execution.venue import (STATUS_OK, STATUS_UNAVAILABLE, mark_status,
                                     sanitize_venue_text)
from harness.venues.kalshi.auth import sign_request
from harness.venues.kalshi.rfq import (CHANNEL, DROP_NONFOOTBALL, ENV, IDLE_S, VENUE,
                                       handle_frame, idle_reason)
from harness.venues.kalshi.ws import RECV_TIMEOUT_S, is_stale, should_reconnect

log = logging.getLogger(__name__)

#: Its own subscribe id sequence, independent of the recorder's (addendum §1.6). The recorder
#: uses 1 for its market subscribe on its own socket; these two ids never meet.
SUBSCRIBE_ID = 1
#: The backoff ceiling, the same shape the recorder uses.
BACKOFF_MAX_S = 60.0
#: Fix 35 round 1 (review Critical 1, Important 2). The venue replays the whole open RFQ set on
#: every subscribe -- 2,613 distinct RFQs across 4,902 frames over ten reconnects in the
#: 03:15-03:45 CT incident (journal 109). The first cut of this fix spent a per-connection frame
#: budget on every `rfq_created` frame, dedupe hits (item 2's "already quoted" skip) included --
#: which meant a later reconnect's replay of the *same* already-quoted set spent the whole budget
#: on frames that do no `fair_values` work at all, silently starving every genuinely new RFQ that
#: arrived afterward on that connection. A rate limit on `compute_quote` calls themselves, in a
#: sliding window rather than a per-connection counter, does not have that failure mode: a
#: dedupe hit or an `rfq_deleted` frame (`_try_quote` is never even called for either) costs
#: nothing, and a long-lived connection keeps quoting new RFQs indefinitely once an initial burst
#: is behind it -- there is no reset to miss on `subscribe()` and no lifetime cap to blow through.
RFQ_QUOTE_RATE_MAX = 500
#: The sliding window `RFQ_QUOTE_RATE_MAX` is measured over.
RFQ_QUOTE_RATE_WINDOW_S = 60.0
#: Fix 35 round 1 (review I3). A per-frame log line at burst volume (roughly 490 per reconnect
#: in the incident) is itself a cost; this is how long a gap between frames must be before the
#: listener treats a burst as over and logs one INFO summary (`replayed=<n> quoted=<n>
#: skipped_rate=<n>`, fix 38: plus the four boundary-filter counters) instead of a line per
#: frame.
RFQ_BURST_SILENCE_S = 5.0
#: Fix 38 (journal 110): the flood that started this fix never goes quiet -- 11,000-14,000
#: frames a minute sustained -- so a summary gated only on `RFQ_BURST_SILENCE_S` would never
#: fire during exactly the traffic its counters exist to show. This is the other trigger: the
#: summary also flushes after this long of continuous flow, so it logs at least once a minute
#: (and at most once, per `_maybe_flush_burst_summary`'s own throttle) whether or not the
#: connection ever goes quiet.
RFQ_SUMMARY_PERIOD_S = 60.0
#: Fix 38 (journal 110): the rate limiter itself flapped at the window boundary -- engage/release
#: pairs 100 ms apart. The limit's own behaviour is unchanged; only how often each direction may
#: *log* a transition is bounded, independently, to at most once per this many seconds.
RFQ_RATE_LOG_COOLDOWN_S = 30.0
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
                 sign: Callable[..., dict] | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.s = settings
        self._factory = session_factory
        self._ws_factory = ws_factory
        self._clock = clock
        self._sleep = sleep or time.sleep
        # The signing seam. `None` is production: `sign_request` over the two key files
        # `app-ws` already mounts. A test passes a stub, because `Settings`' defaults point at
        # `/run/secrets/...` and reading them on the Mac raises before the socket is reached.
        self._sign = sign
        # Fix 35 round 1: the seam the quote rate limit's sliding window and the burst-summary
        # silence check are measured against. Production is `time.monotonic`; a test injects a
        # fake one so it can slide the window without a real sleep.
        self._monotonic = monotonic
        self._stop = False
        self._backoff = 1.0
        self.connected = False
        self.idle_until: datetime | None = None
        self.quote_events_dropped = 0
        self.arrivals = 0
        #: Fix 35, item 2: an `rfq_created` for an id already quoted -- stored, never recomputed.
        self.replayed = 0
        #: Fix 35 round 1 (C1/I2): an `rfq_created` that would have been quoted but the sliding
        #: rate window (`RFQ_QUOTE_RATE_MAX` per `RFQ_QUOTE_RATE_WINDOW_S`) had no room left --
        #: stored as an arrival like any other, never quoted.
        self.quotes_skipped_rate = 0
        #: Fix 38 (journal 110): every frame handed to `handle_frame` -- the denominator
        #: `frames_stored` is measured against. That is every frame left after the ack, error
        #: and quote-event branches above, so it counts an `rfq_created` or `rfq_deleted`
        #: whatever the boundary filter then decides, and also a frame `parse_rfq_frame`
        #: rejects outright (an undocumented `type`, a body that is not an object). Those last
        #: are neither stored nor counted as a drop, so the four counters do not sum to this
        #: one; it is a denominator, not a partition.
        self.frames_seen = 0
        #: Fix 38: every frame that cleared the boundary filter and was written, replays and
        #: unmatched deletes included -- the same event `self.arrivals` already counts, kept
        #: under this name too since it is what the burst/periodic summary and the verify row
        #: name.
        self.frames_stored = 0
        #: Fix 38: an `rfq_created` counted and dropped before `store_rfq` because no leg (and
        #: not the RFQ's own top-level ticker) touched a football series.
        self.dropped_nonfootball = 0
        #: Fix 38: an `rfq_deleted` counted and dropped before `store_rfq` because its id was
        #: never stored here -- the flood's other three quarters (journal 110).
        self.dropped_unknown_delete = 0
        self._sid: int | None = None
        self._subscribed_at: float = 0.0
        self._timeouts = 0
        #: Fix 35 round 1: monotonic timestamps of the last `RFQ_QUOTE_RATE_MAX` (or fewer)
        #: `compute_quote` attempts, oldest first -- `_try_quote` prunes anything older than
        #: `RFQ_QUOTE_RATE_WINDOW_S` before checking whether there is room for one more.
        self._quote_times: deque[float] = deque()
        #: Whether the rate limit is currently engaged, so the WARNING/INFO pair logs only on an
        #: actual transition rather than once per frame.
        self._rate_limited = False
        #: Fix 38 (journal 110): each direction's transition log is *also* bounded to at most
        #: once per `RFQ_RATE_LOG_COOLDOWN_S`, on top of the transition guard above -- the
        #: incident's rate limiter flapped engage/release pairs 100 ms apart at the window
        #: boundary, and a transition guard alone still logs every one of those (each flip is a
        #: real transition). `float("-inf")` so the very first occurrence in each direction
        #: always logs.
        self._last_engage_log_at: float = float("-inf")
        self._last_release_log_at: float = float("-inf")
        #: Fix 35 round 1 (review I3): a burst summary in place of a log line per replayed
        #: frame. Counts since the last flush; `_maybe_flush_burst_summary` logs and zeroes them
        #: once `RFQ_BURST_SILENCE_S` has passed since the last frame this connection processed,
        #: or (fix 38) once `RFQ_SUMMARY_PERIOD_S` has passed since the last flush, whichever
        #: comes first -- a continuous flood never goes quiet, so the silence trigger alone would
        #: never fire during exactly the traffic these counters exist to show.
        self._burst_replayed = 0
        self._burst_quoted = 0
        self._burst_skipped_rate = 0
        self._burst_frames_seen = 0
        self._burst_frames_stored = 0
        self._burst_dropped_nonfootball = 0
        self._burst_dropped_unknown_delete = 0
        self._burst_last_frame_at: float = 0.0
        self._last_summary_at: float = 0.0
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
        # Fix 35 round 1: the quote rate limit is a sliding window, not a per-connection budget,
        # so nothing about it resets here -- a connection that has been up for a while keeps
        # whatever headroom its last 60 s of `compute_quote` calls left it. The burst-summary
        # counters do reset: whatever a prior connection had not yet flushed is not this
        # connection's story to tell. (Fix 38: the rate-log cooldowns are not reset here either,
        # the same reason -- a reconnect inside `RFQ_RATE_LOG_COOLDOWN_S` of the last engage or
        # release log is not a reason to log again immediately.)
        self._burst_replayed = self._burst_quoted = self._burst_skipped_rate = 0
        self._burst_frames_seen = self._burst_frames_stored = 0
        self._burst_dropped_nonfootball = self._burst_dropped_unknown_delete = 0
        self._burst_last_frame_at = self._monotonic()
        self._last_summary_at = self._monotonic()

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
        # Fix 35 round 1 (I3): a gap since the previous frame is exactly the "burst is over"
        # signal -- checked before this frame updates the timestamp, so it reflects the *prior*
        # silence, not this arrival.
        self._maybe_flush_burst_summary()
        self._burst_last_frame_at = self._monotonic()
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
        # Fix 35 round 1 (C1/I2): `_try_quote` is the sliding-window rate gate. It is called
        # from inside `handle_frame`, and only at the one point a quote would actually be
        # attempted -- a dedupe hit or an `rfq_deleted` frame never reaches it, so neither ever
        # spends the budget. Fix 38: `_count_dropped` is the boundary filter's counter, called
        # from inside `handle_frame` for a frame counted and dropped before `store_rfq`.
        self.frames_seen += 1
        self._burst_frames_seen += 1
        with self._factory() as session:
            row = handle_frame(session, msg, self._clock(), on_replay=self._count_replay,
                               allow_quote=self._try_quote, on_dropped=self._count_dropped)
            if row is not None:
                session.commit()
                self.arrivals += 1
                self.frames_stored += 1
                self._burst_frames_stored += 1
        return True

    def _count_replay(self) -> None:
        self.replayed += 1
        self._burst_replayed += 1

    def _count_dropped(self, reason: str) -> None:
        """Fix 38 (journal 110): a frame counted and dropped at the boundary, before
        `store_rfq` -- never stored, never quoted, and (the whole point at flood volume) never
        logged per frame. `reason` is `rfq.DROP_NONFOOTBALL` or `rfq.DROP_UNKNOWN_DELETE`."""
        if reason == DROP_NONFOOTBALL:
            self.dropped_nonfootball += 1
            self._burst_dropped_nonfootball += 1
        else:
            self.dropped_unknown_delete += 1
            self._burst_dropped_unknown_delete += 1

    def _try_quote(self) -> bool:
        """Fix 35 round 1 (C1/I2): whether `handle_frame` may run `compute_quote` right now --
        at most `RFQ_QUOTE_RATE_MAX` calls in any trailing `RFQ_QUOTE_RATE_WINDOW_S`, a sliding
        window rather than a per-connection counter. Called from inside `handle_frame` only when
        it has already determined this frame is not a dedupe hit, so neither a replay nor an
        `rfq_deleted` frame ever reaches here or spends any of the budget.

        Fix 38 (journal 110): the engage/release log lines still fire only on an actual
        transition (`self._rate_limited`, as before), but each direction's transition is now
        *also* throttled to at most once per `RFQ_RATE_LOG_COOLDOWN_S`
        (`_last_engage_log_at`/`_last_release_log_at`). The incident's rate limiter flapped --
        engage/release pairs 100 ms apart at the window boundary -- and every one of those is a
        real transition, so the transition guard alone still logged all of them; the cooldown is
        what keeps the log quiet through a flapping burst without changing when the limit itself
        engages or releases.
        """
        now = self._monotonic()
        window_start = now - RFQ_QUOTE_RATE_WINDOW_S
        while self._quote_times and self._quote_times[0] < window_start:
            self._quote_times.popleft()
        if len(self._quote_times) >= RFQ_QUOTE_RATE_MAX:
            self.quotes_skipped_rate += 1
            self._burst_skipped_rate += 1
            if not self._rate_limited:
                self._rate_limited = True
                if now - self._last_engage_log_at >= RFQ_RATE_LOG_COOLDOWN_S:
                    self._last_engage_log_at = now
                    log.warning(
                        "rfq listener: quote rate limit engaged (%d compute_quote calls in "
                        "the last %.0fs); rfq_created frames store only until it releases",
                        RFQ_QUOTE_RATE_MAX, RFQ_QUOTE_RATE_WINDOW_S)
            return False
        self._quote_times.append(now)
        self._burst_quoted += 1
        if self._rate_limited:
            self._rate_limited = False
            if now - self._last_release_log_at >= RFQ_RATE_LOG_COOLDOWN_S:
                self._last_release_log_at = now
                log.info("rfq listener: quote rate limit released")
        return True

    def _maybe_flush_burst_summary(self) -> None:
        """Fix 35 round 1 (I3): one INFO line per burst instead of one per replayed frame. A
        burst is "over" once `RFQ_BURST_SILENCE_S` has passed since the last frame this
        connection processed; there is nothing to say when nothing happened, so an all-zero
        burst logs nothing.

        Fix 38 (journal 110): the other trigger -- `RFQ_SUMMARY_PERIOD_S` since the last flush,
        whichever of the two comes first. The flood that started this fix never goes quiet, so
        the silence trigger alone would never fire while it was happening; this is what makes
        the summary a heartbeat (at least once a minute) instead of something that only speaks
        up once traffic has already stopped.
        """
        counts = (self._burst_replayed, self._burst_quoted, self._burst_skipped_rate,
                 self._burst_frames_seen, self._burst_frames_stored,
                 self._burst_dropped_nonfootball, self._burst_dropped_unknown_delete)
        if not any(counts):
            return
        now = self._monotonic()
        silent = (now - self._burst_last_frame_at) >= RFQ_BURST_SILENCE_S
        due = (now - self._last_summary_at) >= RFQ_SUMMARY_PERIOD_S
        if not (silent or due):
            return
        log.info("rfq listener: replayed=%d quoted=%d skipped_rate=%d frames_seen=%d "
                 "frames_stored=%d dropped_nonfootball=%d dropped_unknown_delete=%d", *counts)
        (self._burst_replayed, self._burst_quoted, self._burst_skipped_rate,
        self._burst_frames_seen, self._burst_frames_stored, self._burst_dropped_nonfootball,
        self._burst_dropped_unknown_delete) = (0, 0, 0, 0, 0, 0, 0)
        self._last_summary_at = now

    def _on_timeout(self) -> bool:
        """Silence. Two ways it is fatal to this socket and one way it is nothing.

        A subscribe that was never acked inside the window is a dead subscription, and a socket
        that has stayed open while delivering nothing for `ws_stale_s` is half-open. Both drop
        the connection; neither idles, because neither is the venue refusing us.

        Fix 35 round 1: also where a burst's silence is noticed when no further frame ever
        arrives to trigger the check in `run_once` -- a real `recv()` timeout is `RECV_TIMEOUT_S`
        (30 s), well past `RFQ_BURST_SILENCE_S`, so any pending summary flushes here first.
        """
        self._maybe_flush_burst_summary()
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
