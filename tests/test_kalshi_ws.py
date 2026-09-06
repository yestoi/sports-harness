from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from harness.db.models import OrderbookEvent, VenueTrade
from harness.recorder.ws_sink import WsSink
from harness.venues.kalshi.ws import diff_subscriptions

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
