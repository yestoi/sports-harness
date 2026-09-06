import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from harness.db.models import OrderbookEvent, VenueTrade

log = logging.getLogger(__name__)


def _dec(v):
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _ts(ms, fallback: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return fallback


class WsSink:
    def __init__(self, session_factory: sessionmaker, commit_every: int = 100, commit_interval_s: float = 2.0):
        self._factory = session_factory
        self._session = session_factory()
        self._pending = 0
        self._last_commit = time.monotonic()
        self._commit_every, self._interval = commit_every, commit_interval_s
        self._last_seq: dict[int, int] = {}

    def _maybe_commit(self, force: bool = False) -> None:
        if force or self._pending >= self._commit_every or time.monotonic() - self._last_commit >= self._interval:
            self._session.commit()
            self._pending, self._last_commit = 0, time.monotonic()

    def _check_seq(self, sid: int, seq: int, ticker: str, ts: datetime) -> None:
        last = self._last_seq.get(sid)
        if last is not None and seq != last + 1:
            log.warning("seq gap sid=%s expected=%s got=%s", sid, last + 1, seq)
            self._session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="gap", raw={"expected": last + 1, "got": seq}))
            self._pending += 1
        self._last_seq[sid] = seq

    def handle(self, msg: dict, received_at: datetime) -> str | None:
        kind, body = msg.get("type"), msg.get("msg") or {}
        sid, seq = msg.get("sid"), msg.get("seq")
        ticker = body.get("market_ticker", "")
        if kind == "trade":
            price, count = _dec(body.get("yes_price_dollars")), _dec(body.get("count_fp"))
            if body.get("trade_id") and ticker and price is not None and count is not None:
                stmt = insert(VenueTrade).values(venue="kalshi", trade_id=body["trade_id"], ticker=ticker, ts=_ts(body.get("ts_ms"), received_at),
                                                 yes_price=price, count=count, taker_side=body.get("taker_side") or "yes",
                                                 is_block=bool(body.get("is_block_trade")), source="ws", raw_id=None
                                                 ).on_conflict_do_nothing().returning(VenueTrade.trade_id)
                # psycopg3 reports rowcount -1 for ON CONFLICT DO NOTHING; count returned rows instead (Task 6 ruling)
                self._pending += len(self._session.execute(stmt).fetchall())
        elif kind == "orderbook_snapshot":
            if sid is not None and seq is not None:
                self._last_seq[sid] = seq
            self._session.add(OrderbookEvent(ticker=ticker, ts=received_at, sid=sid or 0, seq=seq or 0, kind="snapshot", raw=body))
            self._pending += 1
        elif kind == "orderbook_delta":
            ts = _ts(body.get("ts_ms"), received_at)
            if sid is not None and seq is not None:
                self._check_seq(sid, seq, ticker, ts)
            self._session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid or 0, seq=seq or 0, kind="delta", side=body.get("side"),
                                             price=_dec(body.get("price_dollars")), delta=_dec(body.get("delta_fp")), raw=body))
            self._pending += 1
        else:
            return None
        self._maybe_commit()
        return kind

    def close(self) -> None:
        self._maybe_commit(force=True)
        self._session.close()
