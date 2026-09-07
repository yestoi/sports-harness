import contextlib
import json
import time
import uuid

import websocket
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from email.utils import format_datetime
from sqlalchemy.orm import sessionmaker

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import Game, Order, OrderbookEvent, Signal, StrategyVariant, VenueMarket, VenueQuote, VenueTrade
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
        self.sent: list[str] = []

    def send(self, payload=None, *_a, **_kw):
        self.sent.append(payload)

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
    ws_lookahead_hours = 72
    ws_lookback_hours = 8
    ws_stale_s = 180
    exec_variants = ["sharp_direct", "constrained", "sharp_two_sided"]

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
        self.gap_sids: set[int] = set()
        self.offset_ms = 0
        self.cleared: list[int] = []

    def handle(self, msg, received_at):
        self._on_handle(msg, received_at)

    def reset_sequences(self):
        self.reset_count += 1

    def clear_sequence(self, sid):
        self.cleared.append(sid)

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

    sink = _FakeSink(on_handle)
    recorder.sink = sink
    recorder.run_forever()

    assert sleeps == [1.0, 2.0]
    # Three connections, three disconnects, and every disconnect flushes the pending batch.
    assert sink.flushes == 3


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

    sink = _FakeSink(on_handle)
    recorder.sink = sink

    def fake_sleep(s):
        sleeps.append(s)
        if state["got_data"] and len(sleeps) >= 3:
            recorder.stop()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    recorder.run_forever()

    assert sleeps[:3] == [1.0, 2.0, 1.0]
    # The two rejected connections flush once each on disconnect. The third stays open and
    # goes silent, so it flushes on each of the five recv timeouts that precede is_stale
    # tripping at ws_stale_s (6 x 30 s), and once more on the disconnect itself.
    assert sink.flushes == 8


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


class _TimeoutThenClosingWs(_FakeWs):
    """Delivers its messages, goes silent for one recv timeout, then drops the connection."""

    def __init__(self, messages, on_timeout_survived):
        super().__init__(messages)
        self._on_timeout_survived = on_timeout_survived
        self._timed_out = False

    def recv(self):
        if self._messages:
            return self._messages.pop(0)
        if not self._timed_out:
            self._timed_out = True
            raise websocket.WebSocketTimeoutException()
        # Being back in recv means the timeout branch has finished. run_forever's own flush
        # runs only in the finally below, so whatever is committed now was committed there.
        self._on_timeout_survived()
        raise websocket.WebSocketConnectionClosedException("socket is already closed.")


def test_recv_timeout_flushes_the_sink_before_the_socket_is_declared_stale(db_session, monkeypatch):
    """A socket that stays open but silent only reconnects once `is_stale` trips at
    ws_stale_s (180 s), while the REST normalizer's statement timeout is 30 s. So a batch
    pending when the venue goes quiet has to be committed on each recv timeout as well, not
    only when the connection actually drops."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    # Big batch and a long interval, so only an explicit flush can commit this trade.
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: [])

    subscribed = json.dumps({"type": "subscribed", "msg": {"sid": 1}})
    trade = json.dumps({"type": "trade", "sid": 1, "seq": 1, "msg": {
        "trade_id": "t-silent", "market_ticker": "K1", "yes_price_dollars": "0.4200",
        "count_fp": "7.00", "taker_side": "no", "is_block_trade": False, "ts_ms": 1789234000000}})

    seen = {"count": None}

    def record():
        with factory() as other:
            seen["count"] = other.query(VenueTrade).filter_by(trade_id="t-silent").count()

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), sink,
                          ws_factory=lambda url, header, timeout: _TimeoutThenClosingWs([subscribed, trade], record),
                          clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])
    monkeypatch.setattr(time, "sleep", lambda _s: recorder.stop())

    recorder.run_forever()

    assert seen["count"] == 1


def test_sink_trade_uses_new_taker_side_fields_and_drops_a_sideless_print(db_session, caplog):
    # F5: the WebSocket writer follows the same rule as the REST normalizer. Kalshi's deprecated
    # `taker_side` may vanish from the feed at any time; defaulting to "yes" would mark every print
    # a YES taker. The ws process writes no runs row, so a counter and a log line are its record.
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    assert sink.missing_side == 0
    base = {"market_ticker": "K1", "yes_price_dollars": "0.3600", "no_price_dollars": "0.6400",
            "count_fp": "12.00", "is_block_trade": False, "ts_ms": 1789234000000}
    new_only = {"type": "trade", "sid": 1, "seq": 1,
                "msg": {**base, "trade_id": "t-new", "taker_outcome_side": "no", "taker_book_side": "ask"}}
    assert sink.handle(new_only, NOW) == "trade"
    t = db_session.query(VenueTrade).filter_by(trade_id="t-new").one()
    assert (t.taker_side, t.taker_outcome_side, t.taker_book_side) == ("no", "no", "ask")

    legacy = {"type": "trade", "sid": 1, "seq": 2,
              "msg": {**base, "trade_id": "t-legacy", "taker_side": "yes"}}
    assert sink.handle(legacy, NOW) == "trade"
    t = db_session.query(VenueTrade).filter_by(trade_id="t-legacy").one()
    assert (t.taker_side, t.taker_outcome_side, t.taker_book_side) == ("yes", None, None)

    sideless = {"type": "trade", "sid": 1, "seq": 3, "msg": {**base, "trade_id": "t-noside"}}
    with caplog.at_level("WARNING", logger="harness.recorder.ws_sink"):
        assert sink.handle(sideless, NOW) == "trade"
    assert db_session.query(VenueTrade).filter_by(trade_id="t-noside").count() == 0
    assert sink.missing_side == 1 and sink.errors == 0
    assert any("taker side missing" in r.getMessage() and "K1" in r.getMessage() for r in caplog.records)


# --- hotfix F6: a gap belongs to the subscription, not to the ticker that exposed it ---

def _delta(sid: int, seq: int, ticker: str) -> dict:
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": ticker, "price_dollars": "0.3500", "delta_fp": "1.00",
                    "side": "yes", "ts_ms": 1789234001000}}


def _snapshot(sid: int, seq: int, ticker: str) -> dict:
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": ticker, "yes_dollars_fp": [["0.35", "10.00"]], "no_dollars_fp": []}}


def test_gap_row_is_keyed_to_the_subscription_not_the_exposing_ticker(db_session, caplog):
    """F6: `seq` counts per `sid`, and one `sid` carries up to 500 tickers. Writing the gap
    row under whichever ticker happened to expose it hides that every ticker on that
    subscription lost events, so the row goes in under the `ticker = ""` sentinel with the
    exposing ticker kept in `raw`."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)

    with caplog.at_level("WARNING", logger="harness.recorder.ws_sink"):
        for seq, ticker in ((1, "K-A"), (2, "K-A"), (4, "K-B")):
            assert sink.handle(_delta(7, seq, ticker), NOW) == "orderbook_delta"

    gaps = db_session.query(OrderbookEvent).filter_by(kind="gap").all()
    assert len(gaps) == 1
    (gap,) = gaps
    assert (gap.ticker, gap.sid, gap.seq) == ("", 7, 4)
    assert gap.raw == {"sid": 7, "expected": 3, "got": 4, "exposed_by": "K-B"}
    # The WARNING is the only signal a human sees live, so it names the exposing ticker too.
    assert any("seq gap" in r.getMessage() and "K-B" in r.getMessage() for r in caplog.records)


def test_out_of_sequence_snapshot_writes_a_gap_row_and_then_records_its_seq(db_session):
    """The snapshot branch used to assign `_last_seq[sid]` directly, so a gap that coincided
    with a snapshot was erased without a row. It goes through `_check_seq` instead."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)

    assert sink.handle(_delta(9, 1, "K-A"), NOW) == "orderbook_delta"
    assert sink.handle(_snapshot(9, 3, "K-A"), NOW) == "orderbook_snapshot"
    assert sink.handle(_delta(9, 4, "K-A"), NOW) == "orderbook_delta"

    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["delta", "gap", "snapshot", "delta"]
    gap = db_session.query(OrderbookEvent).filter_by(kind="gap").one()
    assert (gap.ticker, gap.sid, gap.seq) == ("", 9, 3)
    assert gap.raw == {"sid": 9, "expected": 2, "got": 3, "exposed_by": "K-A"}


def test_first_snapshot_on_a_fresh_sid_writes_no_gap(db_session):
    """The normal post-subscribe case: the first message on a `sid` has nothing to follow, so
    routing snapshots through `_check_seq` must not manufacture a gap out of its seq."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)

    assert sink.handle(_snapshot(11, 42, "K-A"), NOW) == "orderbook_snapshot"
    assert sink.handle(_delta(11, 43, "K-A"), NOW) == "orderbook_delta"

    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["snapshot", "delta"]


def test_gap_exposed_by_a_message_with_no_ticker_stores_none_not_the_sentinel(db_session):
    """`ticker = ""` is the gap row's whole-subscription sentinel, so a malformed message
    whose `market_ticker` is missing must record `exposed_by` as null rather than an empty
    string that reads as "the whole subscription exposed it"."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)

    assert sink.handle(_delta(13, 1, "K-A"), NOW) == "orderbook_delta"
    nameless = _delta(13, 3, "K-A")
    del nameless["msg"]["market_ticker"]
    assert sink.handle(nameless, NOW) == "orderbook_delta"

    gap = db_session.query(OrderbookEvent).filter_by(kind="gap").one()
    assert gap.ticker == ""
    assert gap.raw == {"sid": 13, "expected": 2, "got": 3, "exposed_by": None}


# --- hotfix F51: a swallowed sink exception must leave a mark on the tape --------------

def test_sink_exception_writes_a_mark_for_the_rows_it_discards(db_session, monkeypatch):
    """F51: `handle`'s `except` branch rolls the session back and zeroes `_pending`, so up to
    `commit_every` rows that the batch was holding vanish with only a log line as the record.
    Analysis reading the tape saw an unexplained hole. The branch now writes one `gap` row
    under the whole-subscription `ticker = ""` sentinel for each subscription in the batch,
    carrying the discarded count and the failing message's own seq/kind/ticker, and it commits
    those marks in their own transaction so the loss survives whatever happens next."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    # Big batch and a long interval, so the two good deltas are still pending when the third
    # message blows up -- that is the batch the rollback throws away.
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)

    real_execute = sink._session.execute
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_execute(*a, **kw)

    monkeypatch.setattr(sink._session, "execute", flaky)

    for seq in (1, 2):
        assert sink.handle(_delta(21, seq, "K-A"), NOW) == "orderbook_delta"
    assert db_session.query(OrderbookEvent).count() == 0  # still pending, nothing committed

    # A trade is the only branch that reaches `session.execute`, so it is what the flaky
    # patch can break. The seq and ticker on the marks come from this message; the sids come
    # from the batch, which is why sid 22 and the deltas' sid 21 both get a row.
    trade = {"type": "trade", "sid": 22, "seq": 5,
             "msg": {"trade_id": "t-boom", "market_ticker": "K-T", "yes_price_dollars": "0.3600",
                     "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}}
    assert sink.handle(trade, NOW) is None
    assert sink.errors == 1

    marks = db_session.query(OrderbookEvent).filter_by(kind="gap").order_by(OrderbookEvent.id).all()
    # Sid 21's row carries sid 21's own last seq; only the failing subscription's row carries
    # the failing message's seq, so each mark reads against the stream it belongs to.
    assert [(m.ticker, m.sid, m.seq, m.ts) for m in marks] == [("", 21, 2, NOW), ("", 22, 5, NOW)]
    assert all(m.raw == {"discarded": 2, "exposed_by": "sink_exception", "kind": "trade",
                         "ticker": "K-T", "sids": [21, 22]} for m in marks)
    # The rollback took the two pending deltas with it, and the marks are committed on their own.
    assert db_session.query(OrderbookEvent).filter_by(kind="delta").count() == 0
    assert db_session.query(VenueTrade).filter_by(trade_id="t-boom").count() == 0

    # The sink keeps working: seq 3 follows the remembered seq 2, so no gap row of its own.
    assert sink.handle(_delta(21, 3, "K-A"), NOW) == "orderbook_delta"
    sink.flush()
    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["gap", "gap", "delta"]


def test_sink_exception_mark_that_the_database_refuses_does_not_crash_the_recorder(db_session, monkeypatch, caplog):
    """The thing that broke the message is often the database itself, in which case the mark's
    own insert fails too. The failure here is a real one: a seq past what the bigint column
    holds, so Postgres refuses the mark row at flush time and SQLAlchemy deactivates the
    session's transaction, the state a live failure leaves behind, where every later statement
    raises until someone rolls back. That rollback inside the guard is what lets the sink carry
    on. Losing the mark is bad; letting one failed message take down a recorder that is still
    taping is worse."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)

    # Sid 31's last seq is past the ceiling of the `seq` column, so the mark row this
    # subscription earns is one the database will not take.
    assert sink.handle(_delta(31, 2 ** 70, "K-A"), NOW) == "orderbook_delta"

    monkeypatch.setattr(sink._session, "execute", _boom)
    trade = {"type": "trade", "sid": 32, "seq": 5,
             "msg": {"trade_id": "t-nomark", "market_ticker": "K-T", "yes_price_dollars": "0.3600",
                     "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}}
    with caplog.at_level("ERROR", logger="harness.recorder.ws_sink"):
        assert sink.handle(trade, NOW) is None  # must not raise
    assert sink.errors == 1  # the failed mark is not a second error against the message
    assert any("could not write the exception mark" in r.getMessage() for r in caplog.records)
    # Both mark rows go in one flush, so the row sid 32 could have taken is lost with sid 31's.
    assert db_session.query(OrderbookEvent).count() == 0

    # The sink is still usable: the guard's rollback cleared the failed transaction, so this
    # delta commits instead of raising PendingRollbackError on a session nobody reset.
    assert sink.handle(_delta(33, 1, "K-B"), NOW) == "orderbook_delta"
    sink.flush()
    assert [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()] == ["delta"]
    assert sink.errors == 1  # and the commit that followed did not fail either


def test_sink_exception_marks_every_subscription_in_the_discarded_batch(db_session, monkeypatch):
    """One batch spans every subscription the sink has seen since its last commit, so the
    rollback takes rows from all of them. Phase 3's book loader dirties a book on
    `gap.sid = anchor.sid and gap.id > anchor.id`, so a mark filed only under the failing
    message's sid would leave the other subscriptions' books looking clean while their
    deltas were in fact thrown away. Every sid in the batch gets its own mark row, the
    failing message's included even when it had nothing pending, and `raw.sids` carries the
    whole set so a reader sees the shape of the loss from any one row."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)

    real_execute = sink._session.execute
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_execute(*a, **kw)

    monkeypatch.setattr(sink._session, "execute", flaky)

    for seq in (1, 2):
        assert sink.handle(_delta(21, seq, "K-A"), NOW) == "orderbook_delta"
    assert sink.handle(_delta(23, 1, "K-C"), NOW) == "orderbook_delta"

    # Sid 22 had nothing pending: it is the subscription whose message failed.
    trade = {"type": "trade", "sid": 22, "seq": 5,
             "msg": {"trade_id": "t-fanout", "market_ticker": "K-T", "yes_price_dollars": "0.3600",
                     "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}}
    assert sink.handle(trade, NOW) is None
    assert sink.errors == 1

    marks = db_session.query(OrderbookEvent).filter_by(kind="gap").order_by(OrderbookEvent.id).all()
    # Sid 21 last saw seq 2 and sid 23 seq 1, and each mark carries its own subscription's
    # seq. Only sid 22's row, the one whose message failed, carries that message's seq 5.
    assert [(m.sid, m.seq) for m in marks] == [(21, 2), (22, 5), (23, 1)]
    assert all(m.ticker == "" and m.ts == NOW for m in marks)
    assert all(m.raw == {"discarded": 3, "exposed_by": "sink_exception", "kind": "trade",
                         "ticker": "K-T", "sids": [21, 22, 23]} for m in marks)
    assert db_session.query(OrderbookEvent).filter_by(kind="delta").count() == 0

    # The batch is empty again, so the next failure cannot re-mark these subscriptions.
    assert sink._pending_sids == set()
    assert sink.handle(_delta(21, 3, "K-A"), NOW) == "orderbook_delta"
    sink.flush()
    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["gap", "gap", "gap", "delta"]


def _boom(*_a, **_kw):
    raise RuntimeError("boom")


def _sidless_trade(trade_id: str) -> dict:
    """A frame that carries no `sid` at all, the way a malformed or unrouted one does."""
    return {"type": "trade",
            "msg": {"trade_id": trade_id, "market_ticker": "K-T", "yes_price_dollars": "0.3600",
                    "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}}


def test_sink_exception_falls_back_to_sid_zero_only_when_nothing_else_is_at_risk(db_session, monkeypatch):
    """Phase 3 reserves `sid = 0` for REST anchors and dirties a book on
    `gap.sid = anchor.sid and gap.id > anchor.id`, so one mark at sid 0 would dirty every
    REST-anchored book for the rest of the tape. A failing frame with no sid of its own must
    therefore name the subscriptions that actually had rows in the batch, and fall back to
    sid 0 only when the batch was empty and there is nothing else to name."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)

    assert sink.handle(_delta(21, 1, "K-A"), NOW) == "orderbook_delta"
    monkeypatch.setattr(sink._session, "execute", _boom)
    assert sink.handle(_sidless_trade("t-nosid"), NOW) is None

    marks = db_session.query(OrderbookEvent).filter_by(kind="gap").order_by(OrderbookEvent.id).all()
    assert [m.sid for m in marks] == [21]
    assert marks[0].raw["sids"] == [21] and marks[0].raw["discarded"] == 1

    # Same frame, but nothing was pending: now sid 0 is the only thing left to name, and the
    # loss still has to be recorded somewhere.
    empty = WsSink(factory, commit_every=100, commit_interval_s=3600.0)
    monkeypatch.setattr(empty._session, "execute", _boom)
    assert empty.handle(_sidless_trade("t-nosid-2"), NOW) is None

    zero = db_session.query(OrderbookEvent).filter_by(kind="gap", sid=0).one()
    assert zero.raw["sids"] == [0] and zero.raw["discarded"] == 0


def test_sink_exception_mark_skips_a_subscription_whose_rows_never_landed(db_session, monkeypatch):
    """A subscription is only at risk if the batch is actually holding a row for it. A trade
    that `on_conflict_do_nothing` deduplicated, and one dropped for a missing taker side, add
    nothing to the batch, so marking their subscriptions would dirty books that lost nothing."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)

    base = {"market_ticker": "K-T", "yes_price_dollars": "0.3600", "count_fp": "1.00",
            "is_block_trade": False, "ts_ms": 1789234000000}
    dup = {"type": "trade", "sid": 24, "seq": 1, "msg": {**base, "trade_id": "t-dup", "taker_side": "yes"}}
    assert sink.handle(dup, NOW) == "trade"
    sink.flush()  # commits the first copy and empties the batch
    assert sink.handle(dup, NOW) == "trade"  # ON CONFLICT DO NOTHING: nothing pending for sid 24
    sideless = {"type": "trade", "sid": 26, "seq": 1, "msg": {**base, "trade_id": "t-noside-2"}}
    assert sink.handle(sideless, NOW) == "trade"  # dropped for a missing taker side
    assert sink.handle(_delta(21, 1, "K-A"), NOW) == "orderbook_delta"

    monkeypatch.setattr(sink._session, "execute", _boom)
    failing = {"type": "trade", "sid": 25, "seq": 7, "msg": {**base, "trade_id": "t-fail", "taker_side": "yes"}}
    assert sink.handle(failing, NOW) is None

    marks = db_session.query(OrderbookEvent).filter_by(kind="gap").order_by(OrderbookEvent.id).all()
    assert [m.sid for m in marks] == [21, 25]
    assert all(m.raw["sids"] == [21, 25] and m.raw["discarded"] == 1 for m in marks)


def test_sink_exception_mark_survives_a_frame_whose_sid_cannot_be_ordered(db_session, monkeypatch, caplog):
    """The mark's own bookkeeping has to run inside the guard. A frame whose `sid` is a string
    leaves the batch's sid set unorderable, and sorting it outside the guard would raise from
    inside the `except` handler and escape `handle` -- the crash the guard exists to prevent."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=100, commit_interval_s=3600.0)

    assert sink.handle(_delta(21, 1, "K-A"), NOW) == "orderbook_delta"
    assert sink.handle(_delta("s-2", 1, "K-B"), NOW) == "orderbook_delta"

    monkeypatch.setattr(sink._session, "execute", _boom)
    trade = {"type": "trade", "sid": 22, "seq": 5,
             "msg": {"trade_id": "t-unorderable", "market_ticker": "K-T", "yes_price_dollars": "0.3600",
                     "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False, "ts_ms": 1789234000000}}
    with caplog.at_level("ERROR", logger="harness.recorder.ws_sink"):
        assert sink.handle(trade, NOW) is None  # must not raise
    assert sink.errors == 1
    assert any("could not write the exception mark" in r.getMessage() for r in caplog.records)
    assert db_session.query(OrderbookEvent).count() == 0


# --- hotfix F7: a sequence gap must force a fresh snapshot on that subscription ---------

class _ScriptedWs(_FakeWs):
    """Plays a script of frames, advancing a fake monotonic clock at the float entries, then
    drops the connection. Records every frame the recorder sends."""

    def __init__(self, script, clock=None):
        super().__init__([])
        self._script = list(script)
        self._clock = clock

    def recv(self):
        while self._script and isinstance(self._script[0], float):
            self._clock["t"] += self._script.pop(0)
        if self._script:
            return self._script.pop(0)
        raise websocket.WebSocketConnectionClosedException("socket is already closed.")


def _subscription_frames(ws) -> list[dict]:
    return [json.loads(f) for f in ws.sent if json.loads(f).get("cmd") == "update_subscription"]


def test_sequence_gap_resubscribes_the_sid_once_inside_the_recovery_window(db_session, monkeypatch):
    """F7: the sink saw gaps and nobody acted. After a gap every ticker on that sid has an
    unknown book until the next reconnect, so the recorder re-adds the sid's markets to force
    Kalshi to re-send `orderbook_snapshot` -- at most once per sid per 60 s."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: ["K-A", "K-B"])

    script = [json.dumps({"type": "subscribed", "msg": {"sid": 7}})]
    script += [json.dumps(_delta(7, seq, "K-A")) for seq in (1, 2, 5, 6, 9)]
    sockets: list[_ScriptedWs] = []

    def ws_factory(url, header, timeout):
        sockets.append(_ScriptedWs(script if not sockets else []))
        return sockets[-1]

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), sink,
                          ws_factory=ws_factory, clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])
    monkeypatch.setattr(time, "sleep", lambda _s: recorder.stop())

    # The recovery drops the sid's remembered seq so the fresh snapshot is not judged against
    # the pre-gap sequence. That is only observable while the recovery is running.
    cleared: list[tuple[int, dict]] = []
    original_clear = sink.clear_sequence

    def spy(sid):
        original_clear(sid)
        cleared.append((sid, dict(sink._last_seq)))

    sink.clear_sequence = spy
    recorder.run_forever()

    frames = _subscription_frames(sockets[0])
    assert [f["params"]["action"] for f in frames] == ["delete_markets", "add_markets"]
    assert all(f["params"]["sids"] == [7] and f["params"]["market_tickers"] == ["K-A", "K-B"] for f in frames)
    # One id per frame: two `update_subscription` commands sharing an id leave the venue's
    # acks and errors uncorrelatable, so the recorder could not tell which frame failed.
    assert [f["id"] for f in frames] == [2, 3]
    assert cleared == [(7, {})]
    # Both gaps are still on the tape: recovering from one does not hide that it happened.
    gaps = db_session.query(OrderbookEvent).filter_by(kind="gap").order_by(OrderbookEvent.id).all()
    assert [(g.sid, g.raw["expected"], g.raw["got"]) for g in gaps] == [(7, 3, 5), (7, 7, 9)]


def test_third_gap_inside_five_minutes_falls_through_to_a_reconnect(db_session, monkeypatch):
    """Two resubscribes that did not stop the gaps mean the subscription itself is sick, so the
    third gap inside 300 s gives up and takes the shared backoff path instead of resubscribing
    for ever."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: ["K-A"])

    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    script = [json.dumps({"type": "subscribed", "msg": {"sid": 7}}),
              json.dumps(_delta(7, 1, "K-A")), json.dumps(_delta(7, 4, "K-A")),   # gap 1 -> recovery 1
              61.0, json.dumps(_delta(7, 5, "K-A")), json.dumps(_delta(7, 8, "K-A")),   # gap 2 -> recovery 2
              61.0, json.dumps(_delta(7, 9, "K-A")), json.dumps(_delta(7, 12, "K-A"))]  # gap 3 -> reconnect
    sockets: list[_ScriptedWs] = []

    def ws_factory(url, header, timeout):
        sockets.append(_ScriptedWs(script if not sockets else [], clock))
        return sockets[-1]

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), sink,
                          ws_factory=ws_factory, clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])
    # Stop on the second backoff sleep, so the reconnect the third gap asked for actually runs.
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            recorder.stop()

    monkeypatch.setattr(time, "sleep", fake_sleep)

    recorder.run_forever()

    frames = _subscription_frames(sockets[0])
    assert [f["params"]["action"] for f in frames] == ["delete_markets", "add_markets", "delete_markets", "add_markets"]
    assert [f["id"] for f in frames] == [2, 3, 4, 5]  # every frame carries its own id
    # The third gap raised through to the backoff path, which opened one more connection.
    assert len(sockets) == 2
    assert db_session.query(OrderbookEvent).filter_by(kind="gap").count() == 3


# --- hotfix F8/R10: the subscription window and its priority ---------------------------

def _signal(session, vmid: int, created_at, decision: str = "candidate", replay: bool = False,
           variant_id: str = "v1") -> None:
    session.add(Signal(run_id=1, variant_id=variant_id, gap_snapshot_id=1, venue_market_id=vmid, side="yes",
                       decision=decision, labels={}, replay=replay, created_at=created_at))
    session.flush()


def _variant(session, variant_id: str) -> None:
    """A minimal strategy_variants row -- select_ws_tickers resolves Settings.exec_variants
    (names) to variant ids through this table; tests use the same string for both."""
    session.add(StrategyVariant(variant_id=variant_id, name=variant_id, tier="primary", config_json={}, registered_at=NOW))
    session.flush()


def _order(session, vmid: int, ticker: str, status: str, replay: bool = False, variant_id: str = "v1") -> None:
    session.add(Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi", client_order_id=f"co-{uuid.uuid4()}",
                      ticker=ticker, venue_market_id=vmid, side="yes", prob=Decimal("0.50"), contracts=Decimal("10"),
                      status=status, placed_at=NOW, replay=replay))
    session.flush()


def _quote(session, vmid: int, volume: str, at) -> None:
    session.add(VenueQuote(raw_id=vmid * 1000 + int(at.timestamp()) % 1000, run_id=1, venue_market_id=vmid,
                           volume_24h=Decimal(volume), fetched_at=at))
    session.flush()


def test_select_ws_tickers_covers_three_days_ahead_and_eight_hours_back(db_session):
    """F8: paper orders in phase 3 are placed days before kickoff, so a 24 h lookahead taped
    nothing for the book the order was priced against; post-kickoff prints matter to
    settlement and markouts for several hours after the 4 h the window used to allow."""
    far = Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=NOW + timedelta(hours=60))
    past = Game(sport="nfl", home_team_id=20, away_team_id=15, kickoff_utc=NOW - timedelta(hours=6))
    beyond = Game(sport="nfl", home_team_id=21, away_team_id=16, kickoff_utc=NOW + timedelta(hours=80))
    db_session.add_all([far, past, beyond])
    db_session.flush()
    far_id = _market(db_session, "K-FAR", far.id, NOW)
    past_id = _market(db_session, "K-PAST", past.id, NOW)
    _market(db_session, "K-BEYOND", beyond.id, NOW)
    _quote(db_session, far_id, "900.00", NOW)
    _quote(db_session, past_id, "10.00", NOW)

    assert select_ws_tickers(db_session, NOW, cap=10) == ["K-FAR", "K-PAST"]
    assert select_ws_tickers(db_session, NOW, cap=1) == ["K-FAR"]


def test_select_ws_tickers_puts_a_recent_candidate_ahead_of_raw_volume(db_session):
    """Task 3b: the cap is 500 tickers and the busiest markets are not the ones a variant is
    about to trade. A market with a fresh `candidate` signal from an exec variant outranks a
    quieter book's volume order, and the cap applies after that ordering."""
    g = Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=NOW + timedelta(hours=3))
    db_session.add(g)
    db_session.flush()
    busy = _market(db_session, "K-BUSY", g.id, NOW)
    signalled = _market(db_session, "K-SIGNALLED", g.id, NOW)
    stale = _market(db_session, "K-STALE-SIGNAL", g.id, NOW)
    _quote(db_session, busy, "9000.00", NOW)
    _quote(db_session, signalled, "5.00", NOW)
    _quote(db_session, stale, "50.00", NOW)
    _variant(db_session, "v1")
    _signal(db_session, signalled, NOW - timedelta(minutes=30), variant_id="v1")
    # Older than the 1 h priority window, so it ranks on volume like any other market.
    _signal(db_session, stale, NOW - timedelta(hours=2), variant_id="v1")

    assert select_ws_tickers(db_session, NOW, cap=10, exec_variant_names=["v1"]) == ["K-SIGNALLED", "K-BUSY", "K-STALE-SIGNAL"]
    assert select_ws_tickers(db_session, NOW, cap=1, exec_variant_names=["v1"]) == ["K-SIGNALLED"]


def test_select_ws_tickers_ignores_replayed_and_rejected_signals(db_session):
    """Replay signals are backtest output, and a rejected one is a market the variant passed
    on; neither is a reason to spend one of the 500 subscription slots."""
    g = Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=NOW + timedelta(hours=3))
    db_session.add(g)
    db_session.flush()
    busy = _market(db_session, "K-BUSY", g.id, NOW)
    replayed = _market(db_session, "K-REPLAYED", g.id, NOW)
    rejected = _market(db_session, "K-REJECTED", g.id, NOW)
    _quote(db_session, busy, "9000.00", NOW)
    _quote(db_session, replayed, "5.00", NOW)
    _quote(db_session, rejected, "1.00", NOW)
    _variant(db_session, "v1")
    _signal(db_session, replayed, NOW - timedelta(hours=1), replay=True, variant_id="v1")
    _signal(db_session, rejected, NOW - timedelta(hours=1), decision="rejected", variant_id="v1")

    assert select_ws_tickers(db_session, NOW, cap=10, exec_variant_names=["v1"]) == ["K-BUSY", "K-REPLAYED", "K-REJECTED"]


def test_select_ws_tickers_prefers_open_orders_and_exec_candidates(db_session):
    """Task 3b item 5: replaces the shipped 6 h any-variant priority. An open, non-replay
    paper order outranks raw volume with no time bound, and a `candidate` signal only counts
    when it comes from a variant the executor is actually running (Settings.exec_variants,
    resolved to variant ids) and is less than an hour old."""
    g = Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=NOW + timedelta(hours=3))
    db_session.add(g)
    db_session.flush()
    busy = _market(db_session, "K-BUSY", g.id, NOW)
    ordered = _market(db_session, "K-ORDERED", g.id, NOW)
    candidate = _market(db_session, "K-CANDIDATE", g.id, NOW)
    other_variant = _market(db_session, "K-OTHER-VARIANT", g.id, NOW)
    filled = _market(db_session, "K-FILLED", g.id, NOW)
    replay_order = _market(db_session, "K-REPLAY-ORDER", g.id, NOW)
    _quote(db_session, busy, "9000.00", NOW)
    _quote(db_session, ordered, "1.00", NOW)
    _quote(db_session, candidate, "2.00", NOW)
    _quote(db_session, other_variant, "800.00", NOW)
    _quote(db_session, filled, "700.00", NOW)
    _quote(db_session, replay_order, "600.00", NOW)
    _variant(db_session, "v1")
    _order(db_session, ordered, "K-ORDERED", status="open", variant_id="v1")
    _signal(db_session, candidate, NOW - timedelta(minutes=30), variant_id="v1")
    # A candidate from a variant the executor is not running doesn't count.
    _signal(db_session, other_variant, NOW - timedelta(minutes=30), variant_id="v2")
    # A closed order and a replay order don't count either -- an open order has no time bound,
    # but it must be live and real.
    _order(db_session, filled, "K-FILLED", status="filled", variant_id="v1")
    _order(db_session, replay_order, "K-REPLAY-ORDER", status="open", replay=True, variant_id="v1")

    out = select_ws_tickers(db_session, NOW, cap=10, exec_variant_names=["v1"])
    # Priority group first, ordered by volume: K-CANDIDATE (2.00) then K-ORDERED (1.00). Then
    # the rest by volume, none of them carrying a live order or an exec-variant candidate.
    assert out == ["K-CANDIDATE", "K-ORDERED", "K-BUSY", "K-OTHER-VARIANT", "K-FILLED", "K-REPLAY-ORDER"]


def test_settings_ws_window_defaults(env_settings):
    assert env_settings.ws_lookahead_hours == 72
    assert env_settings.ws_lookback_hours == 8
    assert env_settings.ws_max_tickers == 500


# --- hotfix F58: a snapshot's local timestamp needs the clock offset beside it ----------

def test_snapshot_row_records_the_recorder_clock_offset(db_session):
    """F58: deltas carry the venue's own `ts_ms`, but a snapshot is stamped with the
    recorder's local clock. Without the offset to Kalshi's server on the row, that stamp
    cannot be corrected afterwards, and phase 3 anchors books on snapshots."""
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1, offset_ms=-219)

    snap = _snapshot(3, 1, "K1")
    assert sink.handle(snap, NOW) == "orderbook_snapshot"
    assert sink.handle(_delta(3, 2, "K1"), NOW) == "orderbook_delta"

    rows = {e.kind: e for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()}
    assert rows["snapshot"].raw["recorder_offset_ms"] == -219
    assert {k: v for k, v in rows["snapshot"].raw.items() if k != "recorder_offset_ms"} == snap["msg"]
    # The message itself must not have been mutated on the way through.
    assert "recorder_offset_ms" not in snap["msg"]
    assert "recorder_offset_ms" not in rows["delta"].raw


def test_snapshot_offset_defaults_to_zero(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    assert sink.offset_ms == 0
    sink.handle(_snapshot(3, 1, "K1"), NOW)
    assert db_session.query(OrderbookEvent).one().raw["recorder_offset_ms"] == 0


def test_run_forever_hands_each_connections_offset_to_the_sink(monkeypatch):
    """The offset is only known once `_connect` has run (it refreshes at every connect, and a
    401's `Date` header revises it), so the sink has to be told after the connect, not at
    construction time."""
    server_date = format_datetime(NOW + timedelta(seconds=480), usegmt=True)
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: [])
    calls = []

    def ws_factory(url, header, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise websocket.WebSocketBadStatusException(
                "Handshake status 401 Unauthorized", 401, resp_headers={"date": server_date})
        return _FakeWs([])

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None,
                          ws_factory=ws_factory, clock=lambda: NOW)
    seen = {"offset": None}

    def on_handle(_msg, _ts):
        pass

    sink = _FakeSink(on_handle)
    recorder.sink = sink

    def fake_sleep(_s):
        seen["offset"] = sink.offset_ms
        recorder.stop()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    recorder.run_forever()

    assert abs(recorder._offset_ms - 480000) <= 2000
    assert seen["offset"] == recorder._offset_ms


def test_a_stale_gap_sid_does_not_survive_into_the_next_connection(monkeypatch):
    """`gap_sids` is drained after every message, so it is normally empty by the time a
    connection ends -- unless a recovery raised `_Reconnect` while another sid was still in
    the set. That leftover names a subscription of the dead socket, and the venue would be
    sent a resubscribe for a sid it no longer has. The new connection starts the set empty,
    next to the sequence reset that clears the same kind of carried-over state."""
    monkeypatch.setattr(ws_module, "select_ws_tickers", lambda *a, **kw: ["K-A"])

    subscribed = json.dumps({"type": "subscribed", "msg": {"sid": 5}})
    delta = json.dumps(_delta(5, 1, "K-A"))
    observed: dict[str, set[int]] = {}
    sockets: list[_FakeWs] = []

    class _ObservingWs(_ClosingWs):
        def recv(self):
            # The subscribe frame has gone out, so run_forever's connect step is complete.
            observed.setdefault("at_first_recv", set(sink.gap_sids))
            return super().recv()

    def ws_factory(url, header, timeout):
        sockets.append(_ObservingWs([subscribed, delta]))
        return sockets[-1]

    recorder = WsRecorder(_FakeSettings(), lambda: contextlib.nullcontext(None), None,
                          ws_factory=ws_factory, clock=lambda: NOW)
    monkeypatch.setattr(recorder, "_headers", lambda: [])
    monkeypatch.setattr(time, "sleep", lambda _s: recorder.stop())

    sink = _FakeSink(lambda _msg, _ts: None)
    # A sid left over from the previous socket, the way a recovery that raised would leave it.
    sink.gap_sids = {99}
    recorder.sink = sink
    recorder.run_forever()

    assert observed["at_first_recv"] == set()
    assert sink.gap_sids == set()
    assert not [f for f in _subscription_frames(sockets[0]) if 99 in f["params"]["sids"]]
    assert sink.cleared == []
