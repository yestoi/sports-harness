import json
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import websocket
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.db.models import Game, VenueMarket
from harness.venues.kalshi.auth import sign_request

log = logging.getLogger(__name__)
CHANNELS = ["trade", "orderbook_delta"]
FALLBACK_URL = "wss://external-api-ws.kalshi.com/"


def diff_subscriptions(current: list[str], wanted: list[str]) -> tuple[list[str], list[str]]:
    c, w = set(current), set(wanted)
    return sorted(w - c), sorted(c - w)


def select_ws_tickers(session: Session, now: datetime, cap: int, lookahead_hours: int = 24) -> list[str]:
    lo, hi = now - timedelta(hours=4), now + timedelta(hours=lookahead_hours)
    rows = session.execute(
        select(VenueMarket.ticker).join(Game, Game.id == VenueMarket.game_id)
        .where(VenueMarket.match_status.in_(("matched", "fuzzy", "manual")), Game.kickoff_utc >= lo, Game.kickoff_utc <= hi)
        .order_by(VenueMarket.last_seen_at.desc())).scalars().all()
    return list(rows)[:cap]


class WsRecorder:
    def __init__(self, settings: Settings, session_factory: sessionmaker, sink, ws_factory: Callable = websocket.create_connection,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.s, self.factory, self.sink, self.ws_factory, self.clock = settings, session_factory, sink, ws_factory, clock
        self._stop = False
        self._sids: list[int] = []
        self._current: list[str] = []

    def _headers(self) -> list[str]:
        ts_ms = int(self.clock().timestamp() * 1000)
        h = sign_request(self.s.kalshi_key_id(), self.s.kalshi_private_key_pem(), "GET", "/trade-api/ws/v2", ts_ms)
        return [f"{k}: {v}" for k, v in h.items()]

    def _connect(self):
        for url in (self.s.kalshi_ws_url, FALLBACK_URL):
            try:
                ws = self.ws_factory(url, header=self._headers(), timeout=30)
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
        for action, tickers in (("add_markets", add), ("delete_markets", remove)):
            if tickers and self._sids:
                ws.send(json.dumps({"id": msg_id, "cmd": "update_subscription",
                                    "params": {"sids": self._sids, "market_tickers": tickers, "action": action}}))
        self._current = list(wanted)

    def stop(self, *_):
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        backoff = 1.0
        while not self._stop:
            try:
                with self.factory() as session:
                    tickers = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers, self.s.ws_lookahead_hours)
                ws = self._connect()
                self._sids = []
                self._subscribe(ws, tickers)
                backoff, last_plan, msg_id = 1.0, time.monotonic(), 2
                while not self._stop:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") == "subscribed":
                        sid = (msg.get("msg") or {}).get("sid", msg.get("sid"))
                        if sid is not None:
                            self._sids.append(sid)
                    else:
                        self.sink.handle(msg, self.clock())
                    if time.monotonic() - last_plan >= 300:
                        with self.factory() as session:
                            wanted = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers, self.s.ws_lookahead_hours)
                        self._resubscribe(ws, wanted, msg_id)
                        msg_id, last_plan = msg_id + 1, time.monotonic()
                ws.close()
            except Exception as e:  # noqa: BLE001
                log.warning("ws loop error: %r; reconnecting in %.0fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        self.sink.close()
