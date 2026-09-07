import contextlib
import json
import time

import websocket
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from email.utils import format_datetime
from sqlalchemy.orm import sessionmaker

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import Game, OrderbookEvent, VenueMarket, VenueQuote, VenueTrade
from harness.feeds.http import FetchError
from harness.recorder.ws_sink import WsSink
from harness.venues.kalshi import ws as ws_module
from harness.venues.kalshi.ws import WsRecorder, diff_subscriptions, is_stale, select_ws_tickers, should_reconnect

NOW = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PEM = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def test_sink_trade_and_delta_and_gap(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    trade = {"type": "trade", "sid": 1, "seq": 1, "msg": {"trade_id": "t-1", "market_ticker": "K1", "yes_price_dollars": "0.3600",
             "no_price_dollars": "0.6400", "count_fp": "136.00", "taker_side": "no", "is_block_trade": False, "ts_ms": 1789234000000}}
    assert sink.handle(trade, NOW) == "trade"
    assert sink.handle(trade, NOW) == "trade"  # duplicate ignored
    assert db_session.query(VenueTrade).filter_by(trade_id="t-1").count() == 1
    snap = {"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "K1", "yes_dollars_fp": [["0.35", "10.00"]], "no_dollars_fp": []}}
    delta = {"type": "orderbook_delta", "sid": 2, "seq": 3, "msg": {"market_ticker": "K1", "price_dollars": "0.3500", "delta_fp": "-4.00", "side": "yes", "ts_ms": 1789234001000}}
    sink.handle(snap, NOW)
    sink.handle(delta, NOW)
    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["snapshot", "gap", "delta"]
    d = db_session.query(OrderbookEvent).filter_by(kind="delta").one()
    assert (d.side, d.price, d.delta) == ("yes", Decimal("0.3500"), Decimal("-4.00"))


def test_diff_subscriptions():
    add, remove = diff_subscriptions(current=["A", "B"], wanted=["B", "C"])
    assert (add, remove) == (["C"], ["A"])


def test_sink_recovers_after_exception(db_session, monkeypatch):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    real_execute = sink._session.execute
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_execute(*a, **kw)

    monkeypatch.setattr(sink._session, "execute", flaky)
    trade = {"type": "trade", "sid": 1, "seq": 1, "msg": {"trade_id": "t-x", "market_ticker": "K1", "yes_price_dollars": "0.3600",
             "no_price_dollars": "0.6400", "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}}
    assert sink.handle(trade, NOW) is None and sink.errors == 1
    assert sink.handle(trade, NOW) == "trade"
    assert db_session.query(VenueTrade).filter_by(trade_id="t-x").count() == 1


def _market(session, ticker: str, game_id: int, last_seen) -> int:
    vm = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="E1", series_ticker="KXNFLGAME", game_id=game_id,
                     market_type="moneyline", match_status="matched", match_confidence=Decimal("1.00"),
                     match_reason="x", first_seen_raw_id=1, last_seen_at=last_seen)
    session.add(vm)
    session.flush()
    return vm.id


def test_select_ws_tickers_orders_by_latest_quote_volume(db_session):
    # I5: last_seen_at is a near-total tie across markets, so the cap must be decided by
    # the latest quote's 24h volume, not by an arbitrary tie-break.
    g = Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=NOW + timedelta(hours=3))
    db_session.add(g)
    db_session.flush()
    quiet = _market(db_session, "K-QUIET", g.id, NOW)
    busy = _market(db_session, "K-BUSY", g.id, NOW)
    _market(db_session, "K-NONE", g.id, NOW)
    for vmid, vol, at in ((quiet, "10.00", NOW), (busy, "1.00", NOW - timedelta(hours=2)),
                          (busy, "900.00", NOW), (quiet, "5000.00", NOW - timedelta(hours=2))):
        db_session.add(VenueQuote(raw_id=vmid * 100 + int(at.hour), run_id=1, venue_market_id=vmid,
                                  volume_24h=Decimal(vol), fetched_at=at))
    db_session.flush()
    assert select_ws_tickers(db_session, NOW, cap=10) == ["K-BUSY", "K-QUIET", "K-NONE"]
    assert select_ws_tickers(db_session, NOW, cap=1) == ["K-BUSY"]


def test_should_reconnect_on_missing_ack_or_error_frame():
    # I6: no `subscribed` ack inside the window, or an error frame, means reconnect.
    assert should_reconnect(0, 20.0, None) is True
    assert should_reconnect(0, 5.0, None) is False
    assert should_reconnect(2, 600.0, None) is False
    assert should_reconnect(2, 1.0, {"type": "error", "msg": {"code": 6}}) is True


def test_is_stale_counts_consecutive_recv_timeouts():
    # I7: 30 s recv timeout, so six consecutive timeouts is three minutes of silence.
    assert is_stale(6, 30.0, 180) is True
    assert is_stale(5, 30.0, 180) is False
    assert is_stale(0, 30.0, 180) is False


def test_select_ws_tickers_includes_market_with_no_quotes_sorted_last(db_session):
    # Candidate-set-first rewrite must still surface a market that has never had a quote
    # (LEFT JOIN LATERAL, not an inner join), and it must sort after every market that has one.
    g = Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=NOW + timedelta(hours=3))
    db_session.add(g)
    db_session.flush()
    quoted = _market(db_session, "K-QUOTED", g.id, NOW)
    _market(db_session, "K-EMPTY", g.id, NOW)
    db_session.add(VenueQuote(raw_id=quoted, run_id=1, venue_market_id=quoted, volume_24h=Decimal("1.00"), fetched_at=NOW))
    db_session.flush()
    assert select_ws_tickers(db_session, NOW, cap=10) == ["K-QUOTED", "K-EMPTY"]


class _FakeWs:
    def __init__(self, messages):
        self._messages = list(messages)

    def send(self, *_a, **_kw):
        pass

    def recv(self):
        if self._messages:
            return self._messages.pop(0)
        raise websocket.WebSocketTimeoutException()

    def close(self):
        pass


class _FakeSettings:
    kalshi_ws_url = "wss://fake.example/ws"
    kalshi_base_url = "https://k"
    ws_max_tickers = 10
    ws_lookahead_hours = 24
    ws_stale_s = 180

    def kalshi_key_id(self) -> str:
        return "kid-123"

    def kalshi_private_key_pem(self) -> bytes:
        return _PEM


class _FakeSink:
    def __init__(self, on_handle):
        self._on_handle = on_handle
        self.closed = False
        self.reset_count = 0
        self.flushes = 0

    def handle(self, msg, received_at):
        self._on_handle(msg, received_at)

    def reset_sequences(self):
        self.reset_count += 1

    def flush(self):
        self.flushes += 1

    def close(self):
        self.closed = True


def test_backoff_reset_deferred_until_first_data_message(monkeypatch):
    # I6/backoff bug: a connection that succeeds but whose subscription is rejected (error
    # frame) twice in a row must sleep 1, 2 (not 1, 1) -- the old code reset backoff to 1.0
    # right after `_subscribe` sent the frame, before the subscription was proven to work.
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: [])

    error = json.dumps({"type": "error", "msg": {"code": 6}})
    subscribed = json.dumps({"type": "subscribed", "msg": {"sid": 1}})
    trade = json.dumps({"type": "trade", "sid": 1, "seq": 1, "msg": {}})

    connect_calls = {"n": 0}

    def ws_factory(url, header, timeout):
        connect_calls["n"] += 1
        # First two connections succeed but the venue immediately rejects the subscription;
        # the third connection's subscription is accepted and one real message follows.
        if connect_calls["n"] <= 2:
            return _FakeWs([error])
        return _FakeWs([subscribed, trade])

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None, ws_factory=ws_factory, clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])

    def on_handle(_msg, _ts):
        recorder.stop()

    recorder.sink = _FakeSink(on_handle)
    recorder.run_forever()

    assert sleeps == [1.0, 2.0]


def test_backoff_resets_after_data_then_doubles_again_from_one(monkeypatch):
    # Once real data has flowed, a fresh failure must sleep 1.0 again, not continue doubling
    # from wherever the pre-data failures had left it.
    sleeps: list[float] = []
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: [])

    error = json.dumps({"type": "error", "msg": {"code": 6}})
    subscribed = json.dumps({"type": "subscribed", "msg": {"sid": 1}})
    trade = json.dumps({"type": "trade", "sid": 1, "seq": 1, "msg": {}})

    connect_calls = {"n": 0}

    def ws_factory(url, header, timeout):
        connect_calls["n"] += 1
        if connect_calls["n"] == 3:
            return _FakeWs([subscribed, trade])
        return _FakeWs([error])

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None, ws_factory=ws_factory, clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])

    state = {"got_data": False}

    def on_handle(_msg, _ts):
        state["got_data"] = True

    recorder.sink = _FakeSink(on_handle)

    def fake_sleep(s):
        sleeps.append(s)
        if state["got_data"] and len(sleeps) >= 3:
            recorder.stop()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    recorder.run_forever()

    assert sleeps[:3] == [1.0, 2.0, 1.0]


def test_ws_sink_reset_sequences_avoids_synthetic_gap_on_reconnect(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    snap = {"type": "orderbook_snapshot", "sid": 2, "seq": 5, "msg": {"market_ticker": "K1"}}
    sink.handle(snap, NOW)
    sink.reset_sequences()
    delta = {"type": "orderbook_delta", "sid": 2, "seq": 1, "msg": {"market_ticker": "K1", "price_dollars": "0.35", "delta_fp": "1.00"}}
    sink.handle(delta, NOW)
    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["snapshot", "delta"]


def test_headers_signs_with_clock_plus_offset():
    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None,
                          ws_factory=lambda *a, **kw: None, clock=lambda: NOW)
    recorder._offset_ms = 480000
    fixed_ms = int(NOW.timestamp() * 1000)
    headers = recorder._headers()
    ts_header = next(h for h in headers if h.startswith("KALSHI-ACCESS-TIMESTAMP"))
    assert ts_header.split(": ", 1)[1] == str(fixed_ms + 480000)


def test_connect_retries_once_on_401_using_date_header_offset(monkeypatch):
    server_date = format_datetime(NOW + timedelta(seconds=480), usegmt=True)
    calls = []

    def ws_factory(url, header, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise websocket.WebSocketBadStatusException(
                "Handshake status 401 Unauthorized", 401, resp_headers={"date": server_date})
        return "CONNECTED"

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None,
                          ws_factory=ws_factory, clock=lambda: NOW)
    ws = recorder._connect()

    assert ws == "CONNECTED"
    assert calls == ["wss://fake.example/ws", "wss://fake.example/ws"]
    assert abs(recorder._offset_ms - 480000) <= 2000


def test_refresh_offset_keeps_previous_value_on_fetch_error():
    class _RaisingHttp:
        def get(self, *_a, **_kw):
            raise FetchError("boom: connection refused")

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None,
                          ws_factory=lambda *a, **kw: None, clock=lambda: NOW, http=_RaisingHttp())
    recorder._offset_ms = 12345

    recorder._refresh_offset()  # must not raise

    assert recorder._offset_ms == 12345


# --- hotfix A3: a dropped socket must not leave the sink's batch open ------------------

class _ClosingWs(_FakeWs):
    """Delivers its messages, then drops the connection the way a revoked key does."""

    def recv(self):
        if self._messages:
            return self._messages.pop(0)
        raise websocket.WebSocketConnectionClosedException("socket is already closed.")


def test_run_forever_flushes_the_sink_when_the_socket_drops(db_session, monkeypatch):
    """A3: `_maybe_commit` only ever ran from `handle` or from `close` at process exit, so a
    batch pending when the socket died stayed in an open transaction for as long as the
    reconnects kept failing. Its row locks then blocked the REST normalizer's unique-index
    probe on `venue_trades` until the 30 s statement timeout cancelled it."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    # Big batch and a long interval, so only an explicit flush can commit this trade.
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: [])

    subscribed = json.dumps({"type": "subscribed", "msg": {"sid": 1}})
    trade = json.dumps({"type": "trade", "sid": 1, "seq": 1, "msg": {
        "trade_id": "t-drop", "market_ticker": "K1", "yes_price_dollars": "0.3600",
        "count_fp": "12.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}})

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), sink,
                          ws_factory=lambda url, header, timeout: _ClosingWs([subscribed, trade]),
                          clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])

    # `close()` at the very end of run_forever would commit regardless, so the count has to be
    # taken while the recorder is between connections -- exactly where the row was stuck.
    seen = {"count": None}

    def fake_sleep(_s):
        with factory() as other:
            seen["count"] = other.query(VenueTrade).filter_by(trade_id="t-drop").count()
        recorder.stop()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    recorder.run_forever()

    assert seen["count"] == 1
