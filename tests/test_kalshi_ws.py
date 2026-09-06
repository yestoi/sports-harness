from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from harness.db.models import Game, OrderbookEvent, VenueMarket, VenueQuote, VenueTrade
from harness.recorder.ws_sink import WsSink
from harness.venues.kalshi.ws import diff_subscriptions, is_stale, select_ws_tickers, should_reconnect

NOW = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)


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
