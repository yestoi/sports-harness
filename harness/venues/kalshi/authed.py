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

**The writer (Task 7).** `KalshiWriter` is the other half of this module and the only thing here
that sends a non-GET. It is dormant in this phase: `__init__` refuses any construction that does
not carry the module-private `_FACTORY_TOKEN`, which only Task 8's `make_writer` holds, so no
production path can build one by accident and the transport underneath refuses a non-GET anyway
while `writes_enabled` is false. Its judgement is the encoder: `orders.prob` is in the order's own
side space (a `no` order at 0.44 means 44 cents for NO) while V2 quotes the YES leg only, so
`encode_side_price` maps `(yes, p)` to a bid at `p` and `(no, p)` to an ask at `1 - p`, snapped to
the market's own `price_ranges` grid, and `decode_side_price` is its exact inverse.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote, urlsplit

from harness.feeds.http import HttpClient
from harness.pricing.fees import FeeModel
from harness.pricing.fees import fee_model_for as build_fee_model
from harness.venues.kalshi.http import DEMO_HOST_SUFFIX, KalshiTransport

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


#: Every C0 control character (0x00-0x1F) and DEL (0x7F), stripped from a venue's `code`
#: field rather than just the newlines (fix round 2, Minor).
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}


def _sanitize_code(value) -> str | None:
    """The venue's `code` field, made safe to hold on an exception: ASCII-escaped, every C0
    control character and DEL stripped, truncated. Mirrors the global constraint's treatment
    of `venue_status.reason` and the demo smoke's output, scaled down for an identifier
    rather than a sentence."""
    if value is None:
        return None
    text = str(value).encode("ascii", "backslashreplace").decode("ascii")
    text = "".join(ch for ch in text if ch not in _CONTROL_CHARS)
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
    legacy `side` (yes|no) and `action` (buy|sell) pair resolves both the outcome and, since
    nothing else can, the book side too. Returns (None, None) when nothing resolves;
    `require_side` raises instead.

    The legacy pair's mapping to outcome (fix round 2, Important -- round 1's version read
    `side` verbatim and ignored `action`, which inverts a sell order): `action == "buy"`
    keeps `side` as the outcome; `action == "sell"` flips it (buy-yes and sell-no both give
    `yes`; buy-no and sell-yes both give `no`). An absent `action` is treated as `buy` --
    the legacy field predates `action`, and unflipped is the common case. `book_side` is then
    the ordinary bid/ask correspondence applied to that resolved outcome, exactly as
    `canonical_side` maps it (yes->bid, no->ask), not to the raw `side` before the flip.
    """
    outcome_side = payload.get("outcome_side")
    book_side = payload.get("book_side")
    if outcome_side or book_side:
        return (outcome_side or canonical_side(payload)), book_side
    legacy_side = payload.get("side")
    if legacy_side in ("yes", "no"):
        flip = payload.get("action") == "sell"
        outcome = ("no" if legacy_side == "yes" else "yes") if flip else legacy_side
        return outcome, ("bid" if outcome == "yes" else "ask")
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


def _bucket_capacity(bucket: dict):
    """One rate bucket's ceiling. The live Trade API sends `bucket_capacity`; `capacity` is the
    older name and is read only when the current one is absent, so a venue that goes back to it
    still decodes (fix 23). Returns the venue's raw value -- `dec` is what makes it a Decimal."""
    value = bucket.get("bucket_capacity")
    return value if value is not None else bucket.get("capacity")


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
        """`GET /account/limits`. The live Trade API names the tier `usage_tier` and each
        bucket's ceiling `bucket_capacity`; `tier` and `capacity` are read only as a fallback
        (fix 23, reference read 2026-09-08). Only `refill_rate` was ever right, which is why a
        production read came back with numeric rates beside a null tier and null capacities."""
        result = self._transport.request("GET", "/account/limits")
        _check_status(result, "GET", "/account/limits")
        body = result.body if isinstance(result.body, dict) else {}
        read = body.get("read") or {}
        write = body.get("write") or {}
        return Limits(
            tier=body.get("usage_tier") or body.get("tier"),
            read_refill_rate=dec(read.get("refill_rate")),
            read_capacity=dec(_bucket_capacity(read)),
            write_refill_rate=dec(write.get("refill_rate")),
            write_capacity=dec(_bucket_capacity(write)),
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


# =================================================================================================
# The writer (Task 7): V2 event orders. Dormant in this phase -- see the module docstring.
# =================================================================================================

#: Section 9.2, per venue. A breach trips the kill switch; the writer refuses the message first.
ORDER_MESSAGES_PER_MINUTE = 60

ORDERS_PATH = "/portfolio/events/orders"
ORDER_GROUPS_PATH = "/portfolio/order_groups"
#: Creating a group is a POST to its own `/create` sub-path, not to the collection
#: (Trade API reference, read 2026-09-08). The DELETE is on the collection member.
ORDER_GROUPS_CREATE_PATH = "/portfolio/order_groups/create"

#: Section 5.2's probability band, in our own side space, checked before the encoder runs.
MIN_PROB = Decimal("0.01")
MAX_PROB = Decimal("0.99")

#: How long the caller freezes the market after the venue echoes something we did not send.
ECHO_FREEZE_MINUTES = 15

#: Prices are Decimals at 4 places everywhere in this harness (global constraints, units).
PRICE_QUANTUM = Decimal("0.0001")
_ZERO = Decimal(0)
_ONE = Decimal(1)

#: The linear cent grid, used whenever a market's `price_ranges` is absent or unparseable.
_CENT_GRID = tuple(Decimal(f"0.{n:02d}").quantize(PRICE_QUANTUM) for n in range(1, 100))

#: A hostile or malformed `price_ranges` must not be able to make this process build a
#: multi-million-entry list. `price_ranges` is venue text like any other field.
_GRID_MAX_STEPS = 10_000

#: The series prefixes the phase 3 D14 comparison pinned to Kalshi's football fee schedule.
FOOTBALL_SERIES_PREFIXES = ("KXNFL", "KXNCAAF")

#: Only Task 8's `make_writer` holds this. See `KalshiWriter.__init__`.
_FACTORY_TOKEN = object()


class EchoMismatch(RuntimeError):
    """The venue echoed a price or a count we did not send. The writer has already cancelled
    the order and asked its caller to freeze the market for 15 minutes (a `venue_status` row
    `frozen`, reason `echo_mismatch`). `cancel_error` holds the class name of whatever went
    wrong during that cancel, or None: the class name only, never the exception, because a
    transport exception's `.request` carries the live signed headers."""

    def __init__(self, order_id: str | None, reason: str, freeze_minutes: int,
                 field: str | None = None, cancel_error: str | None = None) -> None:
        super().__init__(
            f"venue echo mismatch on {field or 'the order'} for order {order_id!r}: "
            f"cancelled, freeze {freeze_minutes} min")
        self.order_id = order_id
        self.reason = reason
        self.freeze_minutes = freeze_minutes
        self.field = field
        self.cancel_error = cancel_error


class MessageBudgetExceeded(RuntimeError):
    """More than 60 order messages in a minute (section 9.2). The caller trips the kill switch."""


class PreSendInvariantFailed(RuntimeError):
    """One of section 5.2's five pre-send checks failed. Raised before the token bucket and
    before any transport call, so a rejected order never becomes a message."""


class PriceOffGrid(ValueError):
    """The intent's own-side probability is below the lowest price this market quotes, so there
    is no allowed price at or below it. Refused rather than snapped: the only price below the
    market's first tick is zero, and an order at zero is not the order that was intended."""


class FeeModelMismatch(AssertionError):
    """A football series did not price at Kalshi's football fee schedule (maker 0.0175, taker
    0.07, multiplier 1), or sent no `fee_type` at all. Subclasses `AssertionError` because that
    is what the phase 3 D14 comparison raises, but it is an explicit `raise`: a bare `assert`
    disappears under `python -O`, and this guard is the only thing standing between a repriced
    series and a silently wrong realised P&L."""


@dataclass(frozen=True)
class OrderIntent:
    """One order to send. `prob` is in the order's own side space, exactly as `orders.prob` is
    (an order on side `no` at 0.44 means 44 cents for NO), and the encoder is what turns that
    into the YES-leg bid/ask Kalshi accepts."""
    client_order_id: str          # the intent uuid, as a string
    ticker: str
    side: str                     # yes|no
    prob: Decimal
    contracts: Decimal
    expiration_time: datetime     # kickoff - 10 min (R8), set once, never renewed
    exchange_index: int
    order_group_id: str
    price_ranges: list | dict | None


@dataclass(frozen=True)
class VenueOrder:
    order_id: str
    client_order_id: str | None
    ticker: str
    side: str                     # yes|no, decoded back into our space
    prob: Decimal | None          # decoded back into our space
    contracts: Decimal | None
    remaining_count: Decimal | None
    fill_count: Decimal | None
    status: str | None
    order_group_id: str | None
    raw: dict


@dataclass(frozen=True)
class CancelResult:
    """The V2 cancel response is not an order (A-I7): it carries only these four fields."""
    order_id: str
    client_order_id: str | None
    reduced_by: Decimal | None
    ts_ms: int | None


class TokenBucket:
    """Section 9.2's message budget: `rate_per_minute` tokens, refilled continuously at
    `rate_per_minute / 60` a second and capped at `rate_per_minute`. Starts full, so a burst of
    the whole minute's budget is allowed and the next message waits for a refill.

    `take()` raises rather than sleeping: a writer that blocked here would hold the execution
    cycle past its deadline, and the caller's answer to a breach is the kill switch, not a wait.
    """

    def __init__(self, rate_per_minute: int, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._capacity = float(rate_per_minute)
        self._per_second = float(rate_per_minute) / 60.0
        self._monotonic = monotonic
        self._tokens = float(rate_per_minute)
        self._last = monotonic()

    def take(self) -> None:
        now = self._monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._per_second)
            self._last = now
        if self._tokens < 1.0:
            raise MessageBudgetExceeded(
                f"more than {int(self._capacity)} order messages in a minute")
        self._tokens -= 1.0

    @property
    def tokens(self) -> float:
        return self._tokens


def _as_decimal(value) -> Decimal:
    """One of our own numbers as a Decimal. Unlike `dec`, this is for values the harness owns
    (an intent's prob, a cap from Settings), so a bad one is a programming error, not venue text."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _range_number(value) -> Decimal | None:
    """A number out of a `price_ranges` entry: venue text, so anything unparseable or non-finite
    is None and sends the whole grid to the fallback rather than raising."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def grid_steps(price_ranges) -> list[Decimal]:
    """Every allowed YES price from a market's `price_ranges`
    ([{"start": 0, "end": 1, "step": 0.01}]), as Decimals at 4 places. Falls back to the linear
    cent grid 0.01..0.99 when price_ranges is absent or unparseable.

    0 and 1 are dropped: they are not tradable prices, which is also what makes the parsed cent
    grid identical to the fallback. Several ranges are unioned, so a market that quotes finer
    increments near one edge gets both sets of steps.
    """
    ranges = [price_ranges] if isinstance(price_ranges, dict) else price_ranges
    if not isinstance(ranges, (list, tuple)) or not ranges:
        return list(_CENT_GRID)
    steps: set[Decimal] = set()
    for entry in ranges:
        if not isinstance(entry, dict):
            return list(_CENT_GRID)
        start = _range_number(entry.get("start"))
        end = _range_number(entry.get("end"))
        step = _range_number(entry.get("step"))
        if start is None or end is None or step is None or step <= 0 or end <= start:
            return list(_CENT_GRID)
        count = int((end - start) / step)
        if count > _GRID_MAX_STEPS:
            return list(_CENT_GRID)
        for i in range(count + 1):
            value = (start + step * i).quantize(PRICE_QUANTUM)
            if _ZERO < value < _ONE:
                steps.add(value)
    if not steps:
        return list(_CENT_GRID)
    return sorted(steps)


def snap_to_grid(p: Decimal, price_ranges) -> Decimal:
    """The greatest allowed price at or below `p`: a floor, not a nearest.

    `p` is in whatever side space it arrives in, and the caller is expected to pass the order's
    *own-side* probability (fix round 1, Important 2). Flooring there is what makes the snap
    conservative on both legs -- we never pay more than we meant to for a YES *or* for a NO --
    and it matches `harness.strategy.run.snap_to_grid`, which floors the price the strategy
    already sized its edge against. A nearest-with-ties-down snap rounds a non-tie up, so the
    writer could re-round a floored strategy price back above the edge it was chosen for.

    A `p` below the market's lowest tick raises `PriceOffGrid` rather than snapping to zero.
    """
    grid = grid_steps(price_ranges)
    target = _as_decimal(p)
    best = None
    for candidate in grid:              # ascending
        if candidate > target:
            break
        best = candidate
    if best is None:
        raise PriceOffGrid(
            f"probability {target} is below this market's lowest price {grid[0]}")
    return best.quantize(PRICE_QUANTUM)


def snap_intent(side: str, prob: Decimal, price_ranges) -> tuple[str, Decimal, Decimal]:
    """The whole encode in one pass: `(book_side, yes_price, own_prob)`.

    `own_prob` is the floored price in *our* side space -- what this order actually pays if it
    fills -- and it is the value the echo check compares the venue's echo against. `yes_price`
    is the same number expressed on the YES leg, which is the only leg V2 quotes. Building the
    grid once here keeps `encode_side_price`'s pinned two-value signature intact without
    parsing `price_ranges` twice per order.
    """
    if side not in ("yes", "no"):
        raise ValueError(f"side must be 'yes' or 'no', not {side!r}")
    own_prob = snap_to_grid(_as_decimal(prob), price_ranges)
    if side == "yes":
        return "bid", own_prob, own_prob
    return "ask", (_ONE - own_prob).quantize(PRICE_QUANTUM), own_prob


def encode_side_price(side: str, prob: Decimal, price_ranges) -> tuple[str, Decimal]:
    """(side=yes, p) -> ("bid", floor(p)); (side=no, p) -> ("ask", 1 - floor(p)). The snap is
    applied to `p` in the order's own side space and the result is converted to the YES leg
    afterwards, so the price moves in our favour on both sides. The returned price is always a
    YES-leg price."""
    book_side, yes_price, _ = snap_intent(side, prob, price_ranges)
    return book_side, yes_price


def decode_side_price(book_side: str, price: Decimal) -> tuple[str, Decimal]:
    """The exact inverse: ("bid", q) -> ("yes", q); ("ask", q) -> ("no", 1 - q)."""
    q = _as_decimal(price).quantize(PRICE_QUANTUM)
    if book_side == "bid":
        return "yes", q
    if book_side == "ask":
        return "no", (_ONE - q).quantize(PRICE_QUANTUM)
    raise KalshiDecodeError(f"venue sent an unknown book side {book_side!r}")


def fixed_point(value: Decimal, places: int = 4) -> str:
    """A Kalshi fixed-point string: prices at 4 places ("0.5600"), counts at 2 ("10.00")."""
    quantum = Decimal(1).scaleb(-places)
    return str(_as_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def _decode_cancel(body: dict) -> CancelResult:
    """The V2 cancel response, which is not an order (A-I7): four fields and no ticker, price,
    count or status. Decoding it as an order would put `None` in every one of those and invite a
    caller to act on them."""
    return CancelResult(
        order_id=body.get("order_id"),
        client_order_id=body.get("client_order_id"),
        reduced_by=dec(body.get("reduced_by")),
        ts_ms=int(body["ts_ms"]) if body.get("ts_ms") is not None else None,
    )


def _book_side_of(view: OrderView) -> str | None:
    """The echoed order's YES-leg book side: the venue's own `book_side` when it sent one, else
    the ordinary correspondence from the canonical outcome (yes -> bid, no -> ask)."""
    if view.book_side in ("bid", "ask"):
        return view.book_side
    if view.outcome_side == "yes":
        return "bid"
    if view.outcome_side == "no":
        return "ask"
    return None


def _venue_order(view: OrderView, raw: dict, side: str, prob: Decimal) -> VenueOrder:
    """An `OrderView` (the venue's own YES-leg shape) turned back into our side space.

    `side` and `prob` are passed in already decoded and already compared against what was sent,
    never re-derived here: an approved `VenueOrder` therefore carries `yes` or `no` and a
    `Decimal`, and can never carry raw venue text in `side` or `None` in `prob` (fix round 1,
    Important 1). An echo this pair cannot be built from is a mismatch, not an order.
    """
    return VenueOrder(
        order_id=view.order_id,
        client_order_id=view.client_order_id,
        ticker=view.ticker,
        side=side,
        prob=prob,
        contracts=view.count,
        remaining_count=view.remaining_count,
        fill_count=view.fill_count,
        status=view.status,
        order_group_id=view.order_group_id,
        raw=raw,
    )


def _require_football_fees(series: str, fee_type: str | None, model: FeeModel) -> None:
    """Kalshi's football schedule, checked explicitly rather than with a bare `assert`, which
    `python -O` strips (fix round 1, Minor 2).

    `fee_type` must be present: `harness.pricing.fees.fee_model_for` answers a missing one with
    the football model itself, so an empty `/series/` body would otherwise walk straight through
    the guard the D14 comparison rests on (fix round 1, Minor 1). The multiplier is pinned too:
    a series repriced at 2x carries the same two rates and charges twice.
    """
    if not fee_type:
        raise FeeModelMismatch(
            f"{series}: the series carried no fee_type, so its maker and taker rates are unknown")
    if model.maker_rate != Decimal("0.0175"):
        raise FeeModelMismatch(
            f"{series}: maker rate {model.maker_rate} is not the football 0.0175")
    if model.taker_rate != Decimal("0.07"):
        raise FeeModelMismatch(
            f"{series}: taker rate {model.taker_rate} is not the football 0.07")
    if model.multiplier != _ONE:
        raise FeeModelMismatch(
            f"{series}: fee multiplier {model.multiplier} is not 1")


class KalshiWriter:
    """Built only by `make_writer` (Task 8). Direct construction outside the factory raises.

    Every method that sends a message goes through the same three gates in the same order:
    section 5.2's pre-send invariant, then the 60-a-minute token bucket, then the transport. The
    order matters -- a rejected order must never spend a token, and neither may reach the wire --
    so `place_limit` is written as that sequence and nothing else.
    """

    def __init__(self, transport, reader, *, per_bet_cap_dollars: Decimal,
                 contract_cap: Decimal, kill_switch_active: Callable[[], bool],
                 writes_allowed: bool, _factory_token: object = None) -> None:
        if _factory_token is not _FACTORY_TOKEN:
            raise RuntimeError("KalshiWriter is built only by make_writer")
        self._transport = transport
        self._reader = reader
        self._per_bet_cap_dollars = _as_decimal(per_bet_cap_dollars)
        self._contract_cap = _as_decimal(contract_cap)
        self._kill_switch_active = kill_switch_active
        self._writes_allowed = bool(writes_allowed)
        self._bucket = TokenBucket(ORDER_MESSAGES_PER_MINUTE)
        self._fee_models: dict[str, tuple[str | None, FeeModel]] = {}

    @property
    def reader(self) -> KalshiReader:
        """The GET-only reader this writer was built with, so a caller holding a writer does
        not have to build a second one over the same transport (Task 9's `KalshiGateway`).
        Read-only: the reader is chosen by `make_writer` and cannot be swapped afterwards."""
        return self._reader

    # -- section 5.2, independent of the encoder ----------------------------------------------

    def _require_mode(self) -> None:
        """The one check every message shares: this writer's mode allows writes at all. A cancel
        does not go past this either -- a paper writer sends nothing, of any verb."""
        if not self._writes_allowed:
            raise PreSendInvariantFailed("mode does not allow writes")

    def _check_caps(self, prob: Decimal, contracts: Decimal) -> None:
        """The four risk checks, in an order chosen so each test's failure is the one named: a
        stake over the cap is reported as `per_bet_cap` and not as an out-of-band probability."""
        if self._kill_switch_active():
            raise PreSendInvariantFailed("kill switch is active")
        prob = _as_decimal(prob)
        contracts = _as_decimal(contracts)
        if not MIN_PROB <= prob <= MAX_PROB:
            raise PreSendInvariantFailed(f"prob {prob} is outside [{MIN_PROB}, {MAX_PROB}]")
        stake = prob * contracts
        if stake > self._per_bet_cap_dollars:
            raise PreSendInvariantFailed(
                f"per_bet_cap exceeded: stake {stake} > {self._per_bet_cap_dollars}")
        if contracts > self._contract_cap:
            raise PreSendInvariantFailed(
                f"contract_cap exceeded: {contracts} > {self._contract_cap}")

    def _pre_send(self, prob: Decimal, contracts: Decimal) -> None:
        self._require_mode()
        self._check_caps(prob, contracts)

    # -- writes ---------------------------------------------------------------------------------

    def create_group(self, contracts_limit: Decimal, exchange_index: int = 0) -> str:
        """`POST /portfolio/order_groups/create`. `contracts_limit` on the wire is an int64
        whole-contract count and `contracts_limit_fp` is its fixed-point string form (Trade API
        reference, read 2026-09-08); this harness's contract quantities are `Numeric(14,2)` and
        may be fractional, so the fixed-point field is the one that can carry them (fix round 1,
        Critical 1). `exchange_index` is sent only when it is not the default shard, so a
        group on shard 0 stays byte-identical to the documented minimal body."""
        self._require_mode()
        self._bucket.take()
        body = {"contracts_limit_fp": fixed_point(contracts_limit, 2)}
        if exchange_index:
            body["exchange_index"] = exchange_index
        result = self._transport.request("POST", ORDER_GROUPS_CREATE_PATH, json=body)
        _check_status(result, "POST", ORDER_GROUPS_CREATE_PATH)
        payload = result.body if isinstance(result.body, dict) else {}
        group_id = payload.get("order_group_id")
        if not group_id:
            raise KalshiDecodeError("create_group response carried no order_group_id")
        return str(group_id)

    def place_limit(self, intent: OrderIntent) -> VenueOrder:
        self._pre_send(intent.prob, intent.contracts)
        # The encode sits between the invariant and the bucket: it can still refuse (a price
        # below the market's lowest tick, a side that is not ours), and a refused order must no
        # more spend a token than a capped one.
        book_side, yes_price, own_prob = snap_intent(intent.side, intent.prob,
                                                     intent.price_ranges)
        self._bucket.take()
        body = {
            "ticker": intent.ticker,
            "side": book_side,                       # bid|ask on the YES leg
            "price": fixed_point(yes_price, 4),      # "0.5600"
            "count": fixed_point(intent.contracts, 2),
            "client_order_id": intent.client_order_id,
            "order_group_id": intent.order_group_id,
            "time_in_force": "good_till_canceled",
            "expiration_time": int(intent.expiration_time.timestamp()),   # int64 Unix seconds
            "post_only": True,
            "cancel_order_on_pause": True,
            "self_trade_prevention_type": "maker",
            "exchange_index": intent.exchange_index,
        }
        result = self._transport.request("POST", ORDERS_PATH, json=body)
        _check_status(result, "POST", ORDERS_PATH)
        return self._checked_echo(result, intent.ticker, intent.exchange_index,
                                  intent.side, own_prob, _as_decimal(intent.contracts))

    def amend(self, order_id, prob, contracts, client_order_id,
              updated_client_order_id, ticker, side, exchange_index, price_ranges) -> VenueOrder:
        """Exactly the seven fields V2 takes, and no expiry parameter of any kind (R8): the
        expiration is set once when the order is placed and is never renewed per cycle.

        An amend can raise the stake, so it re-runs section 5.2 on its own numbers, and it runs
        the same echo check -- a venue that amends to a price we did not send is the same
        failure as a venue that places one.
        """
        self._pre_send(prob, contracts)
        book_side, yes_price, own_prob = snap_intent(side, prob, price_ranges)
        self._bucket.take()
        path = f"{ORDERS_PATH}/{quote(str(order_id), safe='')}/amend"
        body = {
            "ticker": ticker,
            "side": book_side,
            "price": fixed_point(yes_price, 4),
            "count": fixed_point(contracts, 2),
            "client_order_id": client_order_id,
            "updated_client_order_id": updated_client_order_id,
            "exchange_index": exchange_index,
        }
        result = self._transport.request("POST", path, json=body)
        _check_status(result, "POST", path)
        return self._checked_echo(result, ticker, exchange_index, side, own_prob,
                                  _as_decimal(contracts))

    def cancel(self, order_id: str, ticker: str, exchange_index: int) -> CancelResult:
        """`exchange_index` and `market_ticker` travel in the query, and are therefore outside
        the signature (section 1.1). The kill switch does not block a cancel: it stops new risk,
        and stranding a resting order would be the opposite of that."""
        self._require_mode()
        self._bucket.take()
        path = f"{ORDERS_PATH}/{quote(str(order_id), safe='')}"
        params = {"exchange_index": str(exchange_index), "market_ticker": str(ticker)}
        result = self._transport.request("DELETE", path, params)
        _check_status(result, "DELETE", path)
        body = result.body if isinstance(result.body, dict) else {}
        return _decode_cancel(body)

    def cancel_group(self, group_id: str) -> None:
        self._require_mode()
        self._bucket.take()
        path = f"{ORDER_GROUPS_PATH}/{quote(str(group_id), safe='')}"
        result = self._transport.request("DELETE", path)
        _check_status(result, "DELETE", path)

    # -- the echo check ---------------------------------------------------------------------------

    def _checked_echo(self, result, ticker: str, exchange_index: int, sent_side: str,
                      sent_prob: Decimal, sent_count: Decimal) -> VenueOrder:
        """The venue's echo, decoded into *our* side space and compared there.

        Three comparisons, all as Decimals or canonical `yes`/`no` strings, never as venue text:
        the decoded side equals the side we sent, the decoded own-side probability equals the
        one we sent (the floored `own_prob`, which is the price this order actually pays -- not
        the caller's pre-snap request, which would mismatch on every off-grid intent), and
        `remaining_count + fill_count` equals the count we sent ("10" and "10.00" are the same
        count; a string compare would call that a mismatch).

        Comparing in our own space is what catches a flipped book side (fix round 1, Important
        1): a NO order echoed back as `bid` at the same YES price is the exact inverse of the
        order, and comparing the raw YES-leg price alone waves it through.

        A missing price, a missing count, or an echo with no direction this decoder recognises
        is a mismatch too: an echo that does not say what was accepted has confirmed nothing. On
        any mismatch the order is cancelled and `EchoMismatch` is raised, so the caller freezes
        the market for 15 minutes with reason `echo_mismatch`.
        """
        body = result.body if isinstance(result.body, dict) else {}
        payload = body.get("order") if isinstance(body.get("order"), dict) else body
        view = _decode_order(payload)
        book_side = _book_side_of(view)
        field = side = prob = None
        if view.price is None or view.remaining_count is None or view.fill_count is None:
            field = "the echoed price and counts"
        elif book_side is None:
            field = "side"                      # the venue's direction is not one we recognise
        else:
            side, prob = decode_side_price(book_side, view.price)
            if side != sent_side:
                field = "side"
            elif prob != sent_prob:
                field = "prob"
            elif view.remaining_count + view.fill_count != sent_count:
                field = "count"
        if field is None:
            return _venue_order(view, payload, side, prob)

        cancel_error = None
        if view.order_id:
            try:
                self.cancel(view.order_id, ticker=ticker, exchange_index=exchange_index)
            except Exception as exc:      # a failed cancel must not mask the mismatch
                # The class name only: a transport exception's `.request` holds live signed
                # headers, so the exception itself is never carried forward (see http.py).
                cancel_error = type(exc).__name__
        else:
            cancel_error = "no order_id to cancel"
        raise EchoMismatch(order_id=view.order_id, reason="echo_mismatch",
                           freeze_minutes=ECHO_FREEZE_MINUTES, field=field,
                           cancel_error=cancel_error)

    # -- fees -------------------------------------------------------------------------------------

    def fee_model_for(self, ticker: str) -> FeeModel:
        """The market's fee model, from `GET /series/{series}` once per series and cached.

        Football is checked rather than trusted: the phase 3 D14 comparison rests on maker
        0.0175 and taker 0.07 at multiplier 1, and a series that quietly moved off that schedule
        would only show up in the realised P&L. The GET is the reader's -- limits and series are
        read there, not here (section 1.3), so this class holds no read method of its own. The
        `fee_type` is cached alongside the model so the guard is the same on a cache hit.
        """
        series = str(ticker).split("-")[0]
        cached = self._fee_models.get(series)
        if cached is None:
            body = self._reader.get_series(series)
            payload = body.get("series") if isinstance(body.get("series"), dict) else body
            fee_type = payload.get("fee_type")
            cached = (fee_type, build_fee_model(fee_type, payload.get("fee_multiplier")))
            self._fee_models[series] = cached
        fee_type, model = cached
        if series.startswith(FOOTBALL_SERIES_PREFIXES):
            _require_football_fees(series, fee_type, model)
        return model


# =================================================================================================
# The live guard (Task 8): the only constructor of a write-capable KalshiWriter.
# =================================================================================================

#: The four conditions, in the order the guard reports them. The first missing one names the
#: refusal, so the message is stable and testable for every subset. `make_writer` raises these
#: strings verbatim (fix round 1, M4): this is the one place they are spelled, so the documented
#: order and the enforced order cannot drift apart.
LIVE_CONDITIONS = ("LIVE_TRADING=1", "mode=live", "passing gate report", "secrets/legal_decision")


class LiveGuardRefused(RuntimeError):
    """`make_writer` refused to build a writer for `env` because `missing` does not hold. Never
    raised for `demo` once both secret files exist and the host resolves to `demo.kalshi.co`;
    for `prod`, raised today unconditionally -- none of the four conditions holds."""

    def __init__(self, env: str, missing: str) -> None:
        super().__init__(f"refusing a {env} writer: {missing} is missing")
        self.env, self.missing = env, missing


def _has_passing_gate_report(session, gate_variant_name: str) -> bool:
    """Resolves `gate_variant_name` to a `variant_id` through `strategy_variants`, then asks
    whether the *newest* evaluation -- `max(evaluated_at)` across all of `gate_reports`, since
    one evaluation stores one row per exec variant sharing that timestamp -- has a row for that
    `variant_id` with `gate_variant = true` and `passed = true` (fix round 1 ruling: stricter
    than "ever passed"). An older passing row for the same variant does not count once a newer
    evaluation exists, and a later failing evaluation revokes one: only the latest evaluation's
    say on the gate variant is live. Dormant today regardless -- condition 3 is one of four, and
    the other three are unconditionally false in the deployed posture. A `None` session cannot
    prove the condition, which is a refusal, not a pass.
    """
    if session is None:
        return False
    from sqlalchemy import text as _sa_text

    variant_id = session.execute(
        _sa_text("select variant_id from strategy_variants where name = :n"),
        {"n": gate_variant_name}).scalar()
    if not variant_id:
        return False
    passed = session.execute(
        _sa_text(
            "select passed from gate_reports "
            "where evaluated_at = (select max(evaluated_at) from gate_reports) "
            "and variant_id = :v and gate_variant = true"),
        {"v": variant_id}).scalar()
    return bool(passed)


def make_writer(settings, env: str, session=None, *, per_bet_cap_dollars: Decimal | None = None,
                contract_cap: Decimal | None = None,
                kill_switch_active: Callable[[], bool] | None = None) -> KalshiWriter:
    """The only way to build a KalshiWriter. Raises `LiveGuardRefused` unless every condition for
    `env` holds. Nothing in phase 4 calls this with env='prod' outside tests.

    `per_bet_cap_dollars`, `contract_cap` and `kill_switch_active` are not `Settings` fields
    (they come from a variant's config or the live kill switch -- see the addendum); a caller
    that has resolved them passes them through here. Absent a caller-supplied value the writer
    is built with a zero cap and an always-open kill switch, so a writer built without them can
    place nothing of consequence rather than something under an unreviewed default.
    """
    if env == "demo":
        if not settings.has_kalshi_demo_credentials():
            raise LiveGuardRefused("demo", "kalshi_demo key files")
        host = urlsplit(settings.kalshi_demo_base_url).hostname or ""
        if not (host == DEMO_HOST_SUFFIX or host.endswith("." + DEMO_HOST_SUFFIX)):
            raise LiveGuardRefused("demo", f"a host ending in {DEMO_HOST_SUFFIX}")
        http = HttpClient(settings.http_timeout_s)
        transport = KalshiTransport(
            http, settings.kalshi_demo_base_url, "demo",
            settings.kalshi_demo_key_id(), settings.kalshi_demo_private_key_pem(),
            timeout_s=settings.http_timeout_s, writes_enabled=True)
    elif env == "prod":
        if int(settings.live_trading) != 1:
            raise LiveGuardRefused("prod", LIVE_CONDITIONS[0])
        if settings.mode != "live":
            raise LiveGuardRefused("prod", LIVE_CONDITIONS[1])
        if not _has_passing_gate_report(session, settings.gate_variant):
            raise LiveGuardRefused("prod", LIVE_CONDITIONS[2])
        if not settings.legal_decision_file.exists():
            raise LiveGuardRefused("prod", LIVE_CONDITIONS[3])
        # Fix round 1, M6: the four conditions above are the ones the addendum names, but a
        # missing production key file is a fifth way to be unable to build a real writer, and
        # letting it surface as a bare FileNotFoundError from kalshi_key_id()/
        # kalshi_private_key_pem() below would leak a stack trace instead of a clean refusal.
        # Unreachable today like the rest of this branch (all four conditions above are false in
        # the deployed posture); this only matters once they are not.
        if not settings.has_kalshi_credentials():
            raise LiveGuardRefused("prod", "kalshi production key files")
        http = HttpClient(settings.http_timeout_s)
        transport = KalshiTransport(
            http, settings.kalshi_base_url, "prod",
            settings.kalshi_key_id(), settings.kalshi_private_key_pem(),
            timeout_s=settings.http_timeout_s, writes_enabled=True)
    else:
        raise ValueError(f"unknown env {env!r}")

    reader = KalshiReader(transport)
    return KalshiWriter(
        transport, reader,
        per_bet_cap_dollars=_as_decimal(per_bet_cap_dollars) if per_bet_cap_dollars is not None
                            else _ZERO,
        contract_cap=_as_decimal(contract_cap) if contract_cap is not None else _ZERO,
        kill_switch_active=kill_switch_active if kill_switch_active is not None
                          else (lambda: False),
        writes_allowed=True,
        _factory_token=_FACTORY_TOKEN)
