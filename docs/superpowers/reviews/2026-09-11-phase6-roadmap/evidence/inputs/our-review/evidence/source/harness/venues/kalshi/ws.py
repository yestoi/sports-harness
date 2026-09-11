import json
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Sequence

import websocket
from sqlalchemy import select, true
from sqlalchemy.orm import Session, sessionmaker

from harness import telemetry
from harness.config.settings import Settings
from harness.db.models import Game, Order, Signal, StrategyVariant, VenueMarket, VenueQuote
from harness.feeds.http import HttpClient
from harness.logging_setup import redact
from harness.venues.kalshi.auth import sign_request
from harness.venues.kalshi.clock import server_time_offset_ms

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


def select_ws_tickers(session: Session, now: datetime, cap: int, lookahead_hours: int = 72,
                      lookback_hours: int = 8, exec_variant_names: Sequence[str] = ()) -> list[str]:
    lo, hi = now - timedelta(hours=lookback_hours), now + timedelta(hours=lookahead_hours)
    # Task 3b: a market phase 3 already has a live paper order resting on is worth a
    # subscription slot more than whichever book happens to be busiest, no matter how old the
    # order is; a market an exec variant just called earns the same priority, but only for an
    # hour and only when the variant is one the executor is actually running
    # (`exec_variant_names` is Settings.exec_variants, resolved to variant ids here through the
    # registered strategy_variants -- entirely inside this function, so a caller that mocks it
    # out wholesale, as WsRecorder's own tests do, never touches strategy_variants at all).
    # Replay orders/signals are backtest output and a rejection is a market the variant passed
    # on, so neither counts. Replaces the shipped R10 hotfix's 6 h any-variant-signal priority.
    has_open_order = (
        select(Order.id)
        .where(Order.venue_market_id == VenueMarket.id, Order.status.in_(("open", "partially_filled")),
               Order.replay.is_(False))
        .exists()
    )
    exec_variant_ids = select(StrategyVariant.variant_id).where(StrategyVariant.name.in_(exec_variant_names))
    has_candidate = (
        select(Signal.id)
        .where(Signal.venue_market_id == VenueMarket.id, Signal.decision == "candidate",
               Signal.replay.is_(False), Signal.created_at >= now - timedelta(hours=1),
               Signal.variant_id.in_(exec_variant_ids))
        .exists()
    )
    has_priority = (has_open_order | has_candidate)
    # Compute the (small) candidate set first -- markets whose game falls in the window and
    # that are matched -- then pull each candidate's own latest quote via a LATERAL join that
    # rides ix_quotes_market_fetched (venue_market_id, fetched_at). This avoids aggregating
    # over all of venue_quotes (which grows ~1M rows/day) on every call.
    windowed = (
        select(VenueMarket.id.label("vmid"), VenueMarket.ticker.label("ticker"),
               VenueMarket.last_seen_at.label("last_seen_at"), has_priority.label("has_priority"))
        .join(Game, Game.id == VenueMarket.game_id)
        .where(VenueMarket.match_status.in_(("matched", "fuzzy", "manual")), Game.kickoff_utc >= lo, Game.kickoff_utc <= hi)
    )
    # Task 3b fix round 1, Important 2: the brief says a priority market joins the subscription
    # set "(any horizon)", not only when its game happens to fall in the window above. Unioned
    # (not unioned-all) with the windowed set, so a market that is both inside the window and
    # carrying an open order/fresh candidate still contributes exactly one row.
    priority_any_horizon = (
        select(VenueMarket.id.label("vmid"), VenueMarket.ticker.label("ticker"),
               VenueMarket.last_seen_at.label("last_seen_at"), has_priority.label("has_priority"))
        .where(VenueMarket.match_status.in_(("matched", "fuzzy", "manual")), has_priority)
    )
    candidates = windowed.union(priority_any_horizon).subquery("candidates")
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
        .order_by(candidates.c.has_priority.desc(), latest_quote.c.v24.desc().nullslast(),
                  candidates.c.last_seen_at.desc())
    ).scalars().all()
    return list(rows)[:cap]


class WsRecorder:
    def __init__(self, settings: Settings, session_factory: sessionmaker, sink, ws_factory: Callable = websocket.create_connection,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), http: HttpClient | None = None):
        self.s, self.factory, self.sink, self.ws_factory, self.clock = settings, session_factory, sink, ws_factory, clock
        self.http = http
        self._offset_ms = 0
        self._stop = False
        self._sids: list[int] = []
        self._current: list[str] = []
        self._backoff = 1.0
        # Gap recovery bookkeeping, per sid: when the last resubscribe went out (the 60 s rate
        # limit) and when the recent ones did (the 300 s fall-through to a reconnect).
        self._last_recovery: dict[int, float] = {}
        self._recoveries: dict[int, list[float]] = {}
        # Task 12b: reconnects since the last metrics batch, and that batch's own 60 s clock
        # (its own `time.monotonic`, independent of `self.clock`, which tests fix at a
        # constant instant).
        self._reconnects_since = 0
        self._metrics_sampler = telemetry.Sampler(60.0)

    def _headers(self) -> list[str]:
        ts_ms = int(self.clock().timestamp() * 1000) + self._offset_ms
        h = sign_request(self.s.kalshi_key_id(), self.s.kalshi_private_key_pem(), "GET", "/trade-api/ws/v2", ts_ms)
        return [f"{k}: {v}" for k, v in h.items()]

    def _refresh_offset(self) -> None:
        if self.http is None:
            return
        try:
            offset = server_time_offset_ms(self.http, self.s.kalshi_base_url)
        except Exception as e:  # noqa: BLE001
            log.warning("kalshi clock offset refresh failed: %r; keeping %d ms", e, self._offset_ms)
            return
        if offset is not None:
            self._offset_ms = offset

    def _offset_from_response_date(self, resp_headers: dict | None) -> int | None:
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
        return int((server_dt - self.clock()).total_seconds() * 1000)

    def _connect(self):
        self._refresh_offset()
        for url in (self.s.kalshi_ws_url, FALLBACK_URL):
            try:
                ws = self.ws_factory(url, header=self._headers(), timeout=RECV_TIMEOUT_S)
                log.info("ws connected %s", url)
                return ws
            except websocket.WebSocketBadStatusException as e:
                if e.status_code == 401:
                    offset = self._offset_from_response_date(getattr(e, "resp_headers", None))
                    if offset is not None:
                        log.info("kalshi 401; retrying %s with server-derived offset %d ms", url, offset)
                        self._offset_ms = offset
                        try:
                            ws = self.ws_factory(url, header=self._headers(), timeout=RECV_TIMEOUT_S)
                            log.info("ws connected %s", url)
                            return ws
                        except websocket.WebSocketBadStatusException as e2:
                            log.warning("ws handshake failed %s: %s", url, e2)
                            continue
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

    def _recover_gap(self, ws, sid: int, msg_id: int) -> int:
        """A gap leaves every ticker on `sid` with an unknown book until something forces a
        fresh `orderbook_snapshot`, and Kalshi only re-sends one when a market is (re)added to
        a subscription. So delete and re-add the sid's markets. Returns the next free msg id."""
        now = time.monotonic()
        last = self._last_recovery.get(sid)
        if last is not None and now - last < 60:
            log.info("gap on sid %s inside the 60 s recovery window; ignored", sid)
            return msg_id
        recent = [t for t in self._recoveries.get(sid, []) if now - t < 300]
        self._recoveries[sid] = recent
        if len(recent) >= 2:
            # Two resubscribes inside five minutes did not stop the gaps, so the subscription
            # itself is sick and a third would only keep the recorder on a broken socket.
            raise _Reconnect(f"gap recovery failed twice on sid {sid}")
        for action in ("delete_markets", "add_markets"):
            ws.send(json.dumps({"id": msg_id, "cmd": "update_subscription",
                                "params": {"sids": [sid], "market_tickers": self._current, "action": action}}))
            msg_id += 1
        self._last_recovery[sid] = now
        recent.append(now)
        self.sink.clear_sequence(sid)
        log.warning("gap on sid %s: resubscribed %d tickers for fresh snapshots", sid, len(self._current))
        return msg_id

    def stop(self, *_):
        self._stop = True

    def _rollback_sink(self) -> None:
        """Best-effort: roll the sink's session back after one of its telemetry writes fails.
        A `rollback` the sink does not implement (a minimal test double) is not this method's
        problem to solve -- it is still better to try and lose nothing than to skip it."""
        try:
            if self.sink is not None and hasattr(self.sink, "rollback"):
                self.sink.rollback()
        except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails the ws loop
            log.exception("ws sink rollback failed")

    def _write_ws_metrics(self) -> None:
        """`ws.*` metric_samples, once a minute (design spec §3.1), through the sink's own
        session -- best-effort, and a no-op when there is no sink (a unit test that never
        wired one) or the sink is a test double without the Task 12b methods."""
        if self.sink is None:
            return
        try:
            counts = self.sink.drain_counts()
            now = self.clock()
            samples = [
                ("ws.events_per_min", counts["events"], {}),
                ("ws.trades_per_min", counts["trades"], {}),
                ("ws.subscribed_tickers", len(self._current), {}),
                ("ws.reconnects", self._reconnects_since, {}),
                ("ws.gaps", counts["gaps"], {}),
            ]
            lag = self.sink.sink_lag_s(now)
            if lag is not None:
                samples.append(("ws.sink_lag_s", lag, {}))
            self.sink.write_metrics(now, samples)
            self._reconnects_since = 0
        except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails the ws loop
            log.exception("ws metrics failed")
            # Fix round 1, I3: a failed insert leaves the sink's session in a failed
            # transaction; roll it back so the next tape row `handle()` writes does not fail too.
            self._rollback_sink()

    def _recv_loop(self, ws) -> None:
        subscribed_at, msg_id, last_plan = time.monotonic(), 2, time.monotonic()
        timeouts, error_msg = 0, None
        while not self._stop:
            if self._metrics_sampler.due("ws"):
                self._write_ws_metrics()
            try:
                raw = ws.recv()
                timeouts = 0
            except websocket.WebSocketTimeoutException:
                timeouts += 1
                if should_reconnect(len(self._sids), time.monotonic() - subscribed_at, None):
                    raise _Reconnect(f"no subscription ack within {SUBSCRIBE_ACK_TIMEOUT_S:.0f}s") from None
                if is_stale(timeouts, RECV_TIMEOUT_S, self.s.ws_stale_s):
                    raise _Reconnect(f"no message for {timeouts * RECV_TIMEOUT_S:.0f}s") from None
                # A socket that is alive but silent reaches the reconnect above only after
                # ws_stale_s (180 s), while the REST normalizer's statement timeout is 30 s.
                # Commit here too, so silence costs at most one recv timeout of held locks.
                if self.sink is not None:
                    try:
                        self.sink.flush()
                    except Exception:  # noqa: BLE001
                        log.exception("ws sink flush failed")
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
                # The sink records which subscriptions lost events; only the recorder holds the
                # socket that can ask for a replacement snapshot, so drain the set here.
                while self.sink.gap_sids:
                    msg_id = self._recover_gap(ws, self.sink.gap_sids.pop(), msg_id)
            if should_reconnect(len(self._sids), time.monotonic() - subscribed_at, error_msg):
                raise _Reconnect(f"subscription rejected: {json.dumps(error_msg)[:200] if error_msg else 'no ack'}")
            if time.monotonic() - last_plan >= 300:
                with self.factory() as session:
                    wanted = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers,
                                               self.s.ws_lookahead_hours, self.s.ws_lookback_hours, self.s.exec_variants)
                self._resubscribe(ws, wanted, msg_id)
                msg_id, last_plan = msg_id + 1, time.monotonic()

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stop:
            try:
                with self.factory() as session:
                    tickers = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers,
                                                self.s.ws_lookahead_hours, self.s.ws_lookback_hours, self.s.exec_variants)
                ws = self._connect()
                # `_connect` refreshes the offset (and a 401's Date header revises it), so the
                # sink can only learn this connection's offset once the connect has returned.
                if self.sink is not None:
                    self.sink.offset_ms = self._offset_ms
                    try:
                        self.sink.write_event("ws_connect", "connected", ts=self.clock())
                    except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails the ws loop
                        log.exception("ws_connect event failed")
                        self._rollback_sink()
                self._sids, self._current = [], []
                self._last_recovery, self._recoveries = {}, {}
                try:
                    # Each new subscription restarts seq numbering at the venue, so any
                    # sequence numbers remembered from a prior connection must be dropped
                    # first or the next delta looks like a gap. A gap the previous socket
                    # never got to recover from is carried-over state of the same kind: its
                    # sid belongs to a subscription that no longer exists, so resubscribing
                    # it here would name a dead sid to the venue.
                    self.sink.reset_sequences()
                    self.sink.gap_sids.clear()
                    self._subscribe(ws, tickers)
                    self._recv_loop(ws)
                finally:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        log.debug("ws close failed", exc_info=True)
                    # The sink batches, and only `handle` and `close` ever committed it. A
                    # batch pending when the socket died would therefore stay in an open
                    # transaction for the whole reconnect storm, holding row locks that block
                    # the REST normalizer. Flush here, and never let it stop the reconnect.
                    if self.sink is not None:
                        try:
                            self.sink.flush()
                        except Exception:  # noqa: BLE001
                            log.exception("ws sink flush failed")
            except Exception as e:  # noqa: BLE001
                log.warning("ws loop error: %r; reconnecting in %.0fs", e, self._backoff)
                self._reconnects_since += 1
                if self.sink is not None:
                    try:
                        # Final fix wave, M1: redact *then* truncate. `telemetry.sanitize_reason`
                        # strips punctuation but does not apply the F55 patterns, and the
                        # dashboard renders `operator_events.summary` -- this was the one path
                        # where an exception string reached a rendered field without passing
                        # the log redactor.
                        self.sink.write_event("ws_disconnect", redact(repr(e))[:200],
                                              ts=self.clock())
                    except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails the ws loop
                        log.exception("ws_disconnect event failed")
                        self._rollback_sink()
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 60.0)
        self.sink.close()
