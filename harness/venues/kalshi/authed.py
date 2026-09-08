"""The authenticated Kalshi reader: GET-only V2 decoders.

`KalshiReader` has list and get methods only, every one of them a GET over
`KalshiTransport`. It has no `place_limit`, `amend`, `cancel` or `cancel_group` by
construction: the writer (Task 7) is a separate class built only by `make_writer`
(pre-loaded decision 2), so `hasattr(KalshiReader, ...)` on any of those names is False.

**Naming note.** `FillView` already exists in `harness/execution/plan.py` as the paper
simulator's fill view (a variant id and a stake). This module's `VenueFillView` is a
different shape entirely: the venue's own fill record, with a trade id, a ticker, a
decoded side and a price. The two are kept distinguishable at every import site by name.

**Direction.** V2's `side` in {bid, ask} on the YES leg is a book-side quoting concept, not
the account's outcome. Reads decode `outcome_side` when present and otherwise derive the
canonical direction from `book_side` (bid on the YES leg is yes; ask is no); the legacy
`side`/`action` fields are ignored whenever `outcome_side`/`book_side` are absent, and never
preferred even when both are present (§0.2, pre-loaded decision 3).

**Decimals.** Every price, count and rate that Kalshi sends as a fixed-point string is
decoded through `dec`, which returns a `Decimal` or `None` and never a `float`.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

MAX_PAGES = 20
PAGE_LIMIT = "1000"


def dec(value) -> Decimal | None:
    """A Kalshi fixed-point string, number or None as a Decimal (or None). Never a float."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _ts(value) -> datetime | None:
    """An RFC3339 timestamp as read (expiration_time and created_time are RFC3339 on read,
    even though expiration_time is sent as int64 Unix seconds; pre-loaded decision 3)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def canonical_side(payload: dict) -> str | None:
    """`outcome_side` when present; otherwise `book_side` mapped bid->yes, ask->no; otherwise
    None. Never falls back to the deprecated `side`/`action` (§0.2)."""
    outcome_side = payload.get("outcome_side")
    if outcome_side:
        return outcome_side
    book_side = payload.get("book_side")
    if book_side == "bid":
        return "yes"
    if book_side == "ask":
        return "no"
    return None


@dataclass(frozen=True)
class Balance:
    balance: Decimal              # dollars
    payout: Decimal | None


@dataclass(frozen=True)
class OrderView:
    order_id: str
    client_order_id: str | None
    ticker: str
    #: The canonical direction, from `outcome_side` (yes|no) or, absent that, from `book_side`
    #: (bid on the YES leg is yes; ask is no). Legacy `side`/`action` are ignored when absent
    #: and never preferred (§0.2, pre-loaded decision 3).
    outcome_side: str | None
    book_side: str | None
    price: Decimal | None
    count: Decimal | None
    remaining_count: Decimal | None
    fill_count: Decimal | None
    status: str | None
    order_group_id: str | None
    expiration_time: datetime | None     # RFC3339 on read
    created_time: datetime | None


@dataclass(frozen=True)
class VenueFillView:
    """The venue's own fill record, distinct from `harness.execution.plan.FillView` (the
    paper simulator's variant/stake view)."""
    trade_id: str
    order_id: str | None
    ticker: str
    outcome_side: str | None
    book_side: str | None
    price: Decimal | None
    count: Decimal | None
    is_taker: bool | None
    created_time: datetime | None


@dataclass(frozen=True)
class PositionView:
    ticker: str
    position: Decimal
    market_exposure: Decimal | None
    resting_orders_count: Decimal | None


@dataclass(frozen=True)
class Limits:
    tier: str | None
    read_refill_rate: Decimal | None     # requests per second
    read_capacity: Decimal | None
    write_refill_rate: Decimal | None
    write_capacity: Decimal | None
    raw: dict


def _decode_order(o: dict) -> OrderView:
    return OrderView(
        order_id=o.get("order_id"),
        client_order_id=o.get("client_order_id"),
        ticker=o.get("ticker"),
        outcome_side=o.get("outcome_side"),
        book_side=o.get("book_side"),
        price=dec(o.get("price")),
        count=dec(o.get("count")),
        remaining_count=dec(o.get("remaining_count")),
        fill_count=dec(o.get("fill_count")),
        status=o.get("status"),
        order_group_id=o.get("order_group_id"),
        expiration_time=_ts(o.get("expiration_time")),
        created_time=_ts(o.get("created_time")),
    )


def _decode_fill(f: dict) -> VenueFillView:
    return VenueFillView(
        trade_id=f.get("trade_id"),
        order_id=f.get("order_id"),
        ticker=f.get("ticker"),
        outcome_side=f.get("outcome_side"),
        book_side=f.get("book_side"),
        price=dec(f.get("price")),
        count=dec(f.get("count")),
        is_taker=f.get("is_taker"),
        created_time=_ts(f.get("created_time")),
    )


def _decode_position(p: dict) -> PositionView:
    return PositionView(
        ticker=p.get("ticker"),
        position=dec(p.get("position")),
        market_exposure=dec(p.get("market_exposure")),
        resting_orders_count=dec(p.get("resting_orders_count")),
    )


class KalshiReader:
    """GET-only. It has no `place_limit`, `amend`, `cancel` or `cancel_group` by construction:
    the writer is a separate class built only by `make_writer` (pre-loaded decision 2)."""

    def __init__(self, transport) -> None:
        self._transport = transport

    def _paginate(self, path: str, params: dict, key: str) -> list[dict]:
        """Pages `path` on `cursor` exactly as `KalshiPublic` does: `limit=1000`, follow
        `body["cursor"]`, stop on an empty cursor or a non-200, cap at `MAX_PAGES`."""
        items: list[dict] = []
        cursor = ""
        for _ in range(MAX_PAGES):
            call_params = dict(params)
            call_params["limit"] = PAGE_LIMIT
            if cursor:
                call_params["cursor"] = cursor
            result = self._transport.request("GET", path, call_params)
            body = result.body if isinstance(result.body, dict) else {}
            items.extend(body.get(key, []) or [])
            cursor = body.get("cursor", "") or ""
            if not cursor or result.status != 200:
                break
        return items

    def get_balance(self) -> Balance:
        result = self._transport.request("GET", "/portfolio/balance")
        body = result.body if isinstance(result.body, dict) else {}
        return Balance(balance=dec(body.get("balance")), payout=dec(body.get("payout")))

    def get_positions(self) -> list[PositionView]:
        rows = self._paginate("/portfolio/positions", {}, "market_positions")
        return [_decode_position(p) for p in rows]

    def get_orders(self, status: str | None = None) -> list[OrderView]:
        params = {"status": status} if status else {}
        rows = self._paginate("/portfolio/orders", params, "orders")
        return [_decode_order(o) for o in rows]

    def get_order(self, order_id: str) -> OrderView:
        result = self._transport.request("GET", f"/portfolio/orders/{order_id}")
        body = result.body if isinstance(result.body, dict) else {}
        order = body.get("order", body)
        return _decode_order(order)

    def get_fills(self, since: datetime | None = None) -> list[VenueFillView]:
        params = {"min_ts": str(int(since.timestamp()))} if since is not None else {}
        rows = self._paginate("/portfolio/fills", params, "fills")
        return [_decode_fill(f) for f in rows]

    def get_account_limits(self) -> Limits:
        result = self._transport.request("GET", "/account/limits")
        body = result.body if isinstance(result.body, dict) else {}
        read = body.get("read") or {}
        write = body.get("write") or {}
        return Limits(
            tier=body.get("tier"),
            read_refill_rate=dec(read.get("refill_rate")),
            read_capacity=dec(read.get("capacity")),
            write_refill_rate=dec(write.get("refill_rate")),
            write_capacity=dec(write.get("capacity")),
            raw=body,
        )

    def get_exchange_status(self) -> dict:
        result = self._transport.request("GET", "/exchange/status")
        return result.body if isinstance(result.body, dict) else {}

    def get_series(self, ticker: str) -> dict:
        result = self._transport.request("GET", f"/series/{ticker}")
        return result.body if isinstance(result.body, dict) else {}
