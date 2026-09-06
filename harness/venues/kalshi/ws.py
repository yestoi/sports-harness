import json
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import websocket
from sqlalchemy import select, true
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.db.models import Game, VenueMarket, VenueQuote
from harness.venues.kalshi.auth import sign_request

log = logging.getLogger(__name__)
CHANNELS = ["trade", "orderbook_delta"]
FALLBACK_URL = "wss://external-api-ws.kalshi.com/"
RECV_TIMEOUT_S = 30.0
SUBSCRIBE_ACK_TIMEOUT_S = 15.0


class _Reconnect(Exception):
    """Raised inside the recv loop to fall through to the shared backoff path."""


def should_reconnect(acks_received: int, elapsed_s: float, error_msg: dict | None) -> bool:
    """A subscription that was rejected, or never acked inside the window, is dead."""
    if error_msg:
        return True
    return acks_received == 0 and elapsed_s >= SUBSCRIBE_ACK_TIMEOUT_S


def is_stale(consecutive_timeouts: int, recv_timeout_s: float, stale_s: float) -> bool:
    """A half-open socket delivers nothing while staying open; count the silence."""
    return consecutive_timeouts * recv_timeout_s >= stale_s


def diff_subscriptions(current: list[str], wanted: list[str]) -> tuple[list[str], list[str]]:
    c, w = set(current), set(wanted)
    return sorted(w - c), sorted(c - w)


def select_ws_tickers(session: Session, now: datetime, cap: int, lookahead_hours: int = 24) -> list[str]:
    lo, hi = now - timedelta(hours=4), now + timedelta(hours=lookahead_hours)
    # Compute the (small) candidate set first -- markets whose game falls in the window and
    # that are matched -- then pull each candidate's own latest quote via a LATERAL join that
    # rides ix_quotes_market_fetched (venue_market_id, fetched_at). This avoids aggregating
    # over all of venue_quotes (which grows ~1M rows/day) on every call.
    candidates = (
        select(VenueMarket.id.label("vmid"), VenueMarket.ticker.label("ticker"),
               VenueMarket.last_seen_at.label("last_seen_at"))
        .join(Game, Game.id == VenueMarket.game_id)
        .where(VenueMarket.match_status.in_(("matched", "fuzzy", "manual")), Game.kickoff_utc >= lo, Game.kickoff_utc <= hi)
        .subquery("candidates")
    )
    # last_seen_at is a near-total tie (every market is refreshed in the same tick), so it
    # cannot decide which markets make the cap. Order by the latest quote's 24h volume.
    latest_quote = (
        select(VenueQuote.volume_24h.label("v24"))
        .where(VenueQuote.venue_market_id == candidates.c.vmid)
        .order_by(VenueQuote.fetched_at.desc())
        .limit(1)
        .correlate(candidates)
        .lateral("latest_quote")
    )
    rows = session.execute(
        select(candidates.c.ticker)
        .select_from(candidates)
        .outerjoin(latest_quote, true())
        .order_by(latest_quote.c.v24.desc().nullslast(), candidates.c.last_seen_at.desc())
    ).scalars().all()
    return list(rows)[:cap]


class WsRecorder:
    def __init__(self, settings: Settings, session_factory: sessionmaker, sink, ws_factory: Callable = websocket.create_connection,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.s, self.factory, self.sink, self.ws_factory, self.clock = settings, session_factory, sink, ws_factory, clock
        self._stop = False
        self._sids: list[int] = []
        self._current: list[str] = []
        self._backoff = 1.0

    def _headers(self) -> list[str]:
        ts_ms = int(self.clock().timestamp() * 1000)
        h = sign_request(self.s.kalshi_key_id(), self.s.kalshi_private_key_pem(), "GET", "/trade-api/ws/v2", ts_ms)
        return [f"{k}: {v}" for k, v in h.items()]

    def _connect(self):
        for url in (self.s.kalshi_ws_url, FALLBACK_URL):
            try:
                ws = self.ws_factory(url, header=self._headers(), timeout=RECV_TIMEOUT_S)
                log.info("ws connected %s", url)
                return ws
            except websocket.WebSocketBadStatusException as e:
                log.warning("ws handshake failed %s: %s", url, e)
        raise RuntimeError("ws connect failed on all urls")

    def _subscribe(self, ws, tickers: list[str]) -> None:
        ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {"channels": CHANNELS, "market_tickers": tickers}}))
        self._current = list(tickers)

    def _resubscribe(self, ws, wanted: list[str], msg_id: int) -> None:
        add, remove = diff_subscriptions(self._current, wanted)
        if not add and not remove:
            self._current = list(wanted)
            return
        sent = 0
        for action, tickers in (("add_markets", add), ("delete_markets", remove)):
            if tickers and self._sids:
                ws.send(json.dumps({"id": msg_id, "cmd": "update_subscription",
                                    "params": {"sids": self._sids, "market_tickers": tickers, "action": action}}))
                sent += 1
        # Without a sid nothing was sent, so the venue still holds the old set. Advancing
        # _current here would make every later diff empty and silence the recorder for good.
        if sent:
            self._current = list(wanted)

    def stop(self, *_):
        self._stop = True

    def _recv_loop(self, ws) -> None:
        subscribed_at, msg_id, last_plan = time.monotonic(), 2, time.monotonic()
        timeouts, error_msg = 0, None
        while not self._stop:
            try:
                raw = ws.recv()
                timeouts = 0
            except websocket.WebSocketTimeoutException:
                timeouts += 1
                if should_reconnect(len(self._sids), time.monotonic() - subscribed_at, None):
                    raise _Reconnect(f"no subscription ack within {SUBSCRIBE_ACK_TIMEOUT_S:.0f}s") from None
                if is_stale(timeouts, RECV_TIMEOUT_S, self.s.ws_stale_s):
                    raise _Reconnect(f"no message for {timeouts * RECV_TIMEOUT_S:.0f}s") from None
                continue
            if not raw:
                return
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "subscribed":
                sid = (msg.get("msg") or {}).get("sid", msg.get("sid"))
                if sid is not None:
                    self._sids.append(sid)
            elif mtype == "error":
                error_msg = msg
                log.warning("ws error frame: %s", json.dumps(msg)[:500])
            else:
                self.sink.handle(msg, self.clock())
                # Only a real data message proves the subscription actually works; resetting
                # backoff any earlier (e.g. right after sending the subscribe frame) means a
                # persistently rejected subscription reconnects at the minimum interval forever.
                self._backoff = 1.0
            if should_reconnect(len(self._sids), time.monotonic() - subscribed_at, error_msg):
                raise _Reconnect(f"subscription rejected: {json.dumps(error_msg)[:200] if error_msg else 'no ack'}")
            if time.monotonic() - last_plan >= 300:
                with self.factory() as session:
                    wanted = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers, self.s.ws_lookahead_hours)
                self._resubscribe(ws, wanted, msg_id)
                msg_id, last_plan = msg_id + 1, time.monotonic()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stop:
            try:
                with self.factory() as session:
                    tickers = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers, self.s.ws_lookahead_hours)
                ws = self._connect()
                self._sids, self._current = [], []
                try:
                    # Each new subscription restarts seq numbering at the venue, so any
                    # sequence numbers remembered from a prior connection must be dropped
                    # first or the next delta looks like a gap.
                    self.sink.reset_sequences()
                    self._subscribe(ws, tickers)
                    self._recv_loop(ws)
                finally:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        log.debug("ws close failed", exc_info=True)
            except Exception as e:  # noqa: BLE001
                log.warning("ws loop error: %r; reconnecting in %.0fs", e, self._backoff)
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60.0)
        self.sink.close()
