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
the account's outcome. `canonical_side` decodes `outcome_side` when present and otherwise
derives the canonical direction from `book_side` (bid on the YES leg is yes; ask is no); it
never falls back to the deprecated `side`/`action` fields, even when both are present (§0.2,
pre-loaded decision 3) -- that is `canonical_side`'s own, pinned contract. The decoders in
this module go one step further for a payload that carries only the legacy pair: `_decode_order`
and `_decode_fill` call `require_side`, which tries `canonical_side`'s two sources first and,
only when both are absent, reads the legacy `side` field as the decoder's last resort (fix
round 1, Important 2) -- and raises `KalshiDecodeError` when nothing resolves at all, so a
payload with no direction anywhere never silently decodes to `None`/`None`.

**Decimals.** Every price, count and rate that Kalshi sends as a fixed-point string is
decoded through `dec`, which returns a `Decimal` or `None` and never a `float`. `dec` also
rejects a non-finite `Decimal` (`NaN`/`Infinity`/`-Infinity`): venue text is untrusted, and a
non-finite value flowing into a later arithmetic decision (Task 6b's pause floor divides by
`read_refill_rate`) is exactly the kind of venue string the global constraints forbid from
reaching a decision (fix round 1, Important 3).

**Non-2xx responses.** Every method raises `KalshiApiError` on a non-2xx `FetchResult`
instead of returning an empty or half-populated result: a 401 must be distinguishable from
"the account has no resting orders" (fix round 1, Important 1). List endpoints raise on the
first non-2xx page rather than stopping the loop silently.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

MAX_PAGES = 20
PAGE_LIMIT = "1000"
#: The venue's `code` field is the only part of an error body ever kept (never the message or
#: the raw body, which may echo request details back); truncated so a long or hostile string
#: can't bloat a log or an exception chain.
_CODE_MAX_LEN = 40


class KalshiDecodeError(ValueError):
    """Untrusted venue text failed to decode into something safe to act on: a non-finite
    `Decimal` (`dec`), or a payload with no `outcome_side`, `book_side` or legacy `side` to
    resolve a direction from (`require_side`). Global constraint: no venue string, and no
    venue silence, ever reaches a decision."""


class KalshiApiError(RuntimeError):
    """A non-2xx response to a signed GET. Carries only the status, the method/path that were
    sent, and the venue's own `code` field (ASCII-escaped, newline-stripped, truncated to
    `_CODE_MAX_LEN` characters) -- never the error message or the raw body."""

    def __init__(self, status: int, method: str, path: str, code: str | None) -> None:
        super().__init__(f"{method} {path} failed with status {status}")
        self.status = status
        self.method = method
        self.path = path
        self.code = code


def _sanitize_code(value) -> str | None:
    """The venue's `code` field, made safe to hold on an exception: ASCII-escaped, newlines
    stripped, truncated. Mirrors the global constraint's treatment of `venue_status.reason`
    and the demo smoke's output, scaled down for an identifier rather than a sentence."""
    if value is None:
        return None
    text = str(value).encode("ascii", "backslashreplace").decode("ascii")
    text = text.replace("\n", "").replace("\r", "")
    return text[:_CODE_MAX_LEN]


def _check_status(result, method: str, path: str) -> None:
    if 200 <= result.status < 300:
        return
    body = result.body if isinstance(result.body, dict) else {}
    raise KalshiApiError(result.status, method, path, _sanitize_code(body.get("code")))


def dec(value) -> Decimal | None:
    """A Kalshi fixed-point string, number or None as a Decimal (or None). Never a float, and
    never a non-finite Decimal: `KalshiDecodeError` on NaN/Infinity/-Infinity, since venue
    text must never reach a numeric decision unfiltered."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    if not d.is_finite():
        raise KalshiDecodeError(f"non-finite decimal from venue text {value!r}")
    return d


def _ts(value) -> datetime | None:
    """An RFC3339 timestamp as read (expiration_time and created_time are RFC3339 on read,
    even though expiration_time is sent as int64 Unix seconds; pre-loaded decision 3). A naive
    result (no offset in the text) is treated as UTC rather than the host's local timezone."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_side(payload: dict) -> str | None:
    """`outcome_side` when present; otherwise `book_side` mapped bid->yes, ask->no; otherwise
    None. Never falls back to the deprecated `side`/`action` (§0.2). This is the pinned public
    contract; `require_side` below is the decoder-only superset that also tries the legacy
    pair before giving up."""
    outcome_side = payload.get("outcome_side")
    if outcome_side:
        return outcome_side
    book_side = payload.get("book_side")
    if book_side == "bid":
        return "yes"
    if book_side == "ask":
        return "no"
    return None


def _resolve_sides(payload: dict) -> tuple[str | None, str | None]:
    """The decoders' own direction resolution: `outcome_side`/`book_side` exactly as
    `canonical_side` reads them, plus one fallback `canonical_side` itself never takes (its
    tests pin that it ignores the deprecated pair): when both current fields are absent, the
    legacy `side` (yes|no) resolves both the outcome and, since nothing else can, the book
    side too. Returns (None, None) when nothing resolves; `require_side` raises instead."""
    outcome_side = payload.get("outcome_side")
    book_side = payload.get("book_side")
    if outcome_side or book_side:
        return (outcome_side or canonical_side(payload)), book_side
    legacy_side = payload.get("side")
    if legacy_side in ("yes", "no"):
        return legacy_side, ("bid" if legacy_side == "yes" else "ask")
    return None, None


def require_side(payload: dict) -> tuple[str, str | None]:
    """`_resolve_sides`, raising `KalshiDecodeError` when no source -- current or legacy --
    resolves a direction at all. Used by `_decode_order` and `_decode_fill`: a decoded order
    or fill with no direction anywhere is not a value this reader hands back silently."""
    outcome_side, book_side = _resolve_sides(payload)
    if outcome_side is None and book_side is None:
        raise KalshiDecodeError("payload has no outcome_side, book_side, or legacy side")
    return outcome_side, book_side


@dataclass(frozen=True)
class Balance:
    balance: Decimal | None       # dollars
    payout: Decimal | None


@dataclass(frozen=True)
class OrderView:
    order_id: str | None
    client_order_id: str | None
    ticker: str | None
    #: The canonical direction: `outcome_side` when the venue sends it, or `book_side`
    #: (bid on the YES leg is yes; ask is no), or, only when both are absent, the deprecated
    #: `side` (§0.2, pre-loaded decision 3; fix round 1, Important 2).
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
    trade_id: str | None
    order_id: str | None
    ticker: str | None
    outcome_side: str | None
    book_side: str | None
    price: Decimal | None
    count: Decimal | None
    is_taker: bool | None
    created_time: datetime | None


@dataclass(frozen=True)
class PositionView:
    ticker: str | None
    position: Decimal | None
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
    outcome_side, book_side = require_side(o)
    return OrderView(
        order_id=o.get("order_id"),
        client_order_id=o.get("client_order_id"),
        ticker=o.get("ticker"),
        outcome_side=outcome_side,
        book_side=book_side,
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
    outcome_side, book_side = require_side(f)
    return VenueFillView(
        trade_id=f.get("trade_id"),
        order_id=f.get("order_id"),
        ticker=f.get("ticker"),
        outcome_side=outcome_side,
        book_side=book_side,
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
        `body["cursor"]`, stop on an empty cursor, cap at `MAX_PAGES`. A non-2xx page raises
        `KalshiApiError` rather than silently ending the page loop (fix round 1, Important 1):
        a 401 midway through paging must surface, not look like "no more results"."""
        items: list[dict] = []
        cursor = ""
        for _ in range(MAX_PAGES):
            call_params = dict(params)
            call_params["limit"] = PAGE_LIMIT
            if cursor:
                call_params["cursor"] = cursor
            result = self._transport.request("GET", path, call_params)
            _check_status(result, "GET", path)
            body = result.body if isinstance(result.body, dict) else {}
            items.extend(body.get(key, []) or [])
            cursor = body.get("cursor", "") or ""
            if not cursor:
                break
        return items

    def get_balance(self) -> Balance:
        result = self._transport.request("GET", "/portfolio/balance")
        _check_status(result, "GET", "/portfolio/balance")
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
        path = f"/portfolio/orders/{quote(str(order_id), safe='')}"
        result = self._transport.request("GET", path)
        _check_status(result, "GET", path)
        body = result.body if isinstance(result.body, dict) else {}
        order = body.get("order", body)
        return _decode_order(order)

    def get_fills(self, since: datetime | None = None) -> list[VenueFillView]:
        params = {"min_ts": str(int(since.timestamp()))} if since is not None else {}
        rows = self._paginate("/portfolio/fills", params, "fills")
        return [_decode_fill(f) for f in rows]

    def get_account_limits(self) -> Limits:
        result = self._transport.request("GET", "/account/limits")
        _check_status(result, "GET", "/account/limits")
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
        _check_status(result, "GET", "/exchange/status")
        return result.body if isinstance(result.body, dict) else {}

    def get_series(self, ticker: str) -> dict:
        path = f"/series/{quote(str(ticker), safe='')}"
        result = self._transport.request("GET", path)
        _check_status(result, "GET", path)
        return result.body if isinstance(result.body, dict) else {}
