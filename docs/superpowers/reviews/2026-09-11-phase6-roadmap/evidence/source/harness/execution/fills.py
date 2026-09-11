"""Queue-model fill simulation: pure, side-generic, replayable (F1, F3, F40, F41).

The executor never sends an order to the venue in phase 3, so every fill is an inference
drawn from the recorded tape. Two independent things arrive from it:

* **Prints** (`venue_trades`), one row per trade, carrying the *taker's* side. A taker on
  `opp(s)` is exactly the counterparty who lifts a resting bid on `s`, so a print hits our
  order when its canonical taker side is `opp(order.side)`. Its price is quoted in YES
  space, so it is read on our side as `price_on_side` -- a taker-YES trade at 0.03 is a NO
  bid at 0.97 being lifted, which `tests/test_fills_tape.py` pins against the real tape.
* **Deltas** (`orderbook_events`), the level's net change. A negative delta at our price is
  the *same* event the print already reported, plus any genuine cancels. Counting both would
  shorten the queue twice, so the per-price accumulator `traded_at_price` remembers how much
  has already been explained by prints and only the unexplained remainder counts as a cancel.

The queue itself is the pessimistic half of the model: we join behind everything resting at
our price (`queue_ahead_at_place`) and only trade once that is gone. Against it sits the
optimistic half, the cross fill (F41): the first instant the book's own best ask reaches our
price, the whole remaining size is recorded as a `snapshot_cross` fill. It is a worst case
for adverse selection, never a position -- the caller persists it once and keeps it out of
P&L, positions and CLV.

Everything here is a pure function of its arguments. `simulate_fills` walks a `book.copy()`,
returns a new `SimState` and mutates nothing it was given, so the same tape replayed in one
call or in twenty chunks produces the same fills (`test_chunking_invariance`).

Chunking is not enough on its own, because neither stream arrives once. The executor keeps no
print cursor and rescans prints from `placed_at - 60 s` every loop (§1), and a book that has
crossed our price stays crossed on every later loop, so the state carries a print watermark
(`last_print_ts`, `last_print_ids`) and a `crossed` flag and both re-feeds are absorbed. One
consequence is deliberate: a REST print that lands late carrying a `ts` earlier than the
watermark is skipped rather than applied. Its delta has already moved the queue, so applying
it would double count; the only thing given up is a fill at the very front of the queue, and
being wrong in that direction is the conservative one.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from harness.execution.book import FOUR, QTY, SIDES, ZERO, BookState, opp, side_p
from harness.pricing.fees import KALSHI_FOOTBALL, FeeModel, fee_for_order

#: Deltas are folded in before prints at the same instant. The venue publishes the book
#: change and the trade for one event with one timestamp; taking the print first would let
#: it consume queue that the delta is about to report as already traded.
_DELTA, _PRINT = 0, 1

CROSS = "snapshot_cross"


def _p(x) -> Decimal:
    return Decimal(str(x)).quantize(FOUR, rounding=ROUND_HALF_UP)


def _q(x) -> Decimal:
    """A contract quantity: Decimal at two places, because Kalshi counts are fractional (F40)."""
    return Decimal(str(x)).quantize(QTY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PaperOrder:
    """The order under simulation. A subset of the `orders` row, in the order's own side space."""

    order_id: int
    ticker: str
    side: str
    prob: Decimal
    contracts: Decimal
    placed_at: datetime
    expiry: datetime
    #: Contracts resting at our price on our side when we joined. None means the ticker had no
    #: book at placement (R10): the order is out of fill simulation until one first exists.
    queue_ahead_at_place: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"unknown side {self.side!r}")
        object.__setattr__(self, "prob", _p(self.prob))
        object.__setattr__(self, "contracts", _q(self.contracts))
        if self.queue_ahead_at_place is not None:
            object.__setattr__(self, "queue_ahead_at_place", _q(self.queue_ahead_at_place))


@dataclass(frozen=True)
class TapePrint:
    """One `venue_trades` row. `taker_side` is already canonical (`taker_outcome_side or
    taker_side`, resolved by the caller, never defaulted, F5); None means the venue told us
    nothing and the print can never hit us."""

    trade_id: str
    ts: datetime
    yes_price: Decimal
    count: Decimal
    taker_side: str | None
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "yes_price", _p(self.yes_price))
        object.__setattr__(self, "count", _q(self.count))


@dataclass(frozen=True)
class TapeDelta:
    """One `orderbook_events(kind = 'delta')` row. `event_id` is the recorder's insertion
    order, the only monotone quantity on the tape and therefore the cursor."""

    event_id: int
    ts: datetime
    side: str
    price: Decimal
    delta: Decimal
    sid: int
    seq: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _p(self.price))
        object.__setattr__(self, "delta", _q(self.delta))


@dataclass
class SimState:
    """The per-track simulation state, persisted on the order between loops.

    The watched track stores it in `queue_remaining` / `traded_at_price` /
    `filled_contracts` / `tape_cursor_event_id`; the no-watcher track in the `nw_*` columns.

    `crossed` and the print watermark are state, not per-call facts, because both of the
    tape's two streams can be re-fed. `crossed` makes the worst-case fill once per order even
    though the book keeps crossing on every later loop, and `last_print_ts` / `last_print_ids`
    make a print idempotent even though the executor keeps no print cursor and rescans from
    `placed_at - 60 s` every loop (§1). The ids are only those applied at exactly
    `last_print_ts`, which is all that is needed to separate a re-fed trade from a second
    trade stamped the same millisecond.
    """

    queue_remaining: Decimal | None
    traded_at_price: Decimal
    filled_contracts: Decimal
    cursor_event_id: int | None
    crossed: bool = False
    last_print_ts: datetime | None = None
    last_print_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.queue_remaining is not None:
            self.queue_remaining = _q(self.queue_remaining)
        self.traded_at_price = _q(self.traded_at_price)
        self.filled_contracts = _q(self.filled_contracts)
        self.last_print_ids = tuple(self.last_print_ids)

    @classmethod
    def initial(cls, order: PaperOrder) -> "SimState":
        return cls(queue_remaining=order.queue_ahead_at_place, traded_at_price=ZERO,
                   filled_contracts=ZERO, cursor_event_id=None)

    def _copy(self) -> "SimState":
        return SimState(self.queue_remaining, self.traded_at_price, self.filled_contracts,
                        self.cursor_event_id, self.crossed, self.last_print_ts,
                        self.last_print_ids)

    def _seen_print(self, tape_print: "TapePrint") -> bool:
        """Whether this print is already folded in, by the watermark rather than by a cursor.

        Behind the watermark means behind it in venue time, so a late REST backfill stamped
        earlier than a print already applied reads as seen and is skipped (module docstring).
        """
        if self.last_print_ts is None:
            return False
        if tape_print.ts < self.last_print_ts:
            return True
        return tape_print.ts == self.last_print_ts and tape_print.trade_id in self.last_print_ids

    def _mark_print(self, tape_print: "TapePrint") -> None:
        if self.last_print_ts is not None and tape_print.ts == self.last_print_ts:
            self.last_print_ids = self.last_print_ids + (tape_print.trade_id,)
        else:
            self.last_print_ts = tape_print.ts
            self.last_print_ids = (tape_print.trade_id,)


@dataclass(frozen=True)
class SimFill:
    """One simulated fill, the columns of a `fills` row that the simulator can know.

    `fee_type`, `fee_multiplier` and `maker_rate` come from `fill_fee_fields(fee_model)` and
    `has_print` from `has_print(...)`; both are the persisting caller's job, not this one's.
    """

    prob: Decimal
    contracts: Decimal
    fee: Decimal
    filled_at: datetime
    fill_method: str
    source_trade_id: str | None
    source_event_id: int | None
    taker_side: str | None
    through: bool
    tape_source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "prob", _p(self.prob))
        object.__setattr__(self, "contracts", _q(self.contracts))


@dataclass
class FillResult:
    """`fills` are the track's own fills; `cross` is the separate worst-case fill (F41) and is
    deliberately not in `fills` -- it never touches positions, P&L, CLV or the gate."""

    fills: list[SimFill]
    state: SimState
    cross: SimFill | None
    crossed: bool


def price_on_side(tape_print: TapePrint, side: str) -> Decimal:
    """The print's price expressed on `side`: the YES price for YES, its complement for NO.

    The print is quoted in YES space, which is exactly what `side_p` converts (§0.11).
    """
    return side_p(tape_print.yes_price, side)


def hits(tape_print: TapePrint, side: str) -> bool:
    """Whether this print traded against resting size on `side`.

    The taker of a trade is the counterparty of the resting order, so only a taker on
    `opp(side)` can have lifted us. A print the venue gave no side for never hits (F5).
    """
    return tape_print.taker_side is not None and tape_print.taker_side == opp(side)


def fill_fee_fields(fee_model: FeeModel) -> tuple[str, Decimal, Decimal]:
    """The `(fee_type, fee_multiplier, maker_rate)` stored on every fill, so a later reading of
    the tape can reprice it without guessing which schedule was in force."""
    fee_type = "quadratic_with_maker_fees" if fee_model.maker_rate > 0 else "quadratic"
    return fee_type, fee_model.multiplier, fee_model.maker_rate


def has_print(fill: SimFill, order: PaperOrder, prints, window_s: int = 60) -> bool:
    """Whether a hitting print at or through our price sits in `[placed_at, filled_at + 60 s]`.

    F12's sanity assertion: a `queue_model` fill that no print can explain would mean the
    queue arithmetic invented a trade. Asserted on the fills, never gated.
    """
    upper = fill.filled_at + timedelta(seconds=window_s)
    return any(hits(p, order.side) and price_on_side(p, order.side) <= order.prob
               and order.placed_at <= p.ts <= upper
               for p in prints)


def _merge_events(prints, deltas, placed_at: datetime, deadline: datetime,
                  cursor: int | None) -> list[tuple]:
    """Prints and deltas in `ts` order, deltas first at equal `ts`, input order within a kind.

    Dropped here rather than in the walk: anything at or before `placed_at` (we were not in
    the queue yet), anything after `deadline` (the track has stopped), and any delta the
    cursor already covers (it is folded into the state and the book we were handed).
    """
    events: list[tuple] = []
    for i, d in enumerate(deltas):
        if cursor is not None and d.event_id <= cursor:
            continue
        if placed_at < d.ts <= deadline:
            events.append((d.ts, _DELTA, i, d))
    for i, p in enumerate(prints):
        if placed_at < p.ts <= deadline:
            events.append((p.ts, _PRINT, i, p))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    return events


def _apply_print(order: PaperOrder, state: SimState, tape_print: TapePrint, fill_method: str,
                 fee_model: FeeModel) -> SimFill | None:
    """The queue arithmetic for one print; returns the fill it produced, if any."""
    if not hits(tape_print, order.side):
        return None
    price = price_on_side(tape_print, order.side)
    if price > order.prob:
        return None
    remaining = order.contracts - state.filled_contracts
    if price < order.prob:
        # Swept through us: the trade happened past our level, so everything ahead of us is
        # gone by definition and our size trades. It says nothing about volume *at* our price,
        # so the per-price accumulator is untouched.
        state.queue_remaining = ZERO
        contracts = min(tape_print.count, remaining)
        through = True
    else:
        consumed = min(state.queue_remaining, tape_print.count)
        state.queue_remaining -= consumed
        contracts = min(tape_print.count - consumed, remaining)
        state.traded_at_price += tape_print.count
        through = False
    if contracts <= ZERO:
        return None
    state.filled_contracts += contracts
    return SimFill(prob=order.prob, contracts=contracts,
                   fee=fee_for_order(fee_model, "maker", order.prob, contracts),
                   filled_at=tape_print.ts, fill_method=fill_method,
                   source_trade_id=tape_print.trade_id, source_event_id=None,
                   taker_side=tape_print.taker_side, through=through,
                   tape_source=tape_print.source)


def _apply_queue_delta(order: PaperOrder, state: SimState, delta: TapeDelta) -> None:
    """Fold one delta into the queue: trades first, then genuine cancels.

    Only a shrinking level at our own price on our own side can move us up. A positive delta
    is a late joiner, who sits behind us. A negative one is the venue reporting the level's
    net change, which already includes the trades the prints told us about, so it is charged
    against `traded_at_price` first and only what is left is a cancel.
    """
    if delta.side != order.side or delta.price != order.prob or delta.delta >= ZERO:
        return
    size = -delta.delta
    cancels = max(ZERO, size - state.traded_at_price)
    state.traded_at_price = max(ZERO, state.traded_at_price - size)
    state.queue_remaining = max(ZERO, state.queue_remaining - cancels)


def _crosses(book: BookState, order: PaperOrder) -> bool:
    """Whether the book's own best ask on our side has reached our price."""
    ask = book.best_ask(order.side)
    return ask is not None and ask <= order.prob


def _cross_fill(order: PaperOrder, state: SimState, book: BookState, ts: datetime,
                event_id: int, fee_model: FeeModel) -> SimFill | None:
    """The one `snapshot_cross` fill of whatever is still unfilled when the book crosses us."""
    contracts = order.contracts - state.filled_contracts
    if contracts <= ZERO:
        return None
    return SimFill(prob=order.prob, contracts=contracts,
                   fee=fee_for_order(fee_model, "maker", order.prob, contracts),
                   filled_at=ts, fill_method=CROSS, source_trade_id=None,
                   source_event_id=event_id, taker_side=None, through=False,
                   tape_source=book.source)


def simulate_fills(order: PaperOrder, state: SimState, book: BookState | None, prints, deltas,
                   deadline: datetime, fill_method: str,
                   fee_model: FeeModel = KALSHI_FOOTBALL) -> FillResult:
    """Walk the tape from `state`'s cursor to `deadline` and report what would have filled.

    `deadline` is explicit because the two tracks stop at different instants (F3): the watched
    track at cancel or expiry, the no-watcher track always at the order's natural `expiry`.
    `fill_method` is the label those tracks carry into `fills` (`queue_model` / `no_watcher`).

    The caller passes the book as of its own cursor; this walks a copy, so the caller's book
    is untouched and the cross test reads a book advanced by exactly the deltas given here.
    """
    out = state._copy()
    if out.queue_remaining is None:
        # No book existed at placement (R10). Nothing can be said about the queue, so the
        # order stays out of simulation -- no fills, no cross, and the cursor does not move,
        # which is what lets the executor re-simulate from here once a book appears.
        return FillResult(fills=[], state=out, cross=None, crossed=False)

    working = book.copy() if book is not None else None
    fills: list[SimFill] = []
    cross: SimFill | None = None
    if working is not None and not out.crossed and _crosses(working, order):
        # Already crossed before a single new event. The book we were handed is itself the
        # observation, so the cross is stamped with the book's `anchor_id`: `last_event_id`
        # advances every loop and would give one crossing a fresh id each time.
        out.crossed = True
        cross = _cross_fill(order, out, working, working.as_of, working.anchor_id, fee_model)

    for _ts, kind, _i, event in _merge_events(prints, deltas, order.placed_at, deadline,
                                              state.cursor_event_id):
        if kind == _DELTA:
            out.cursor_event_id = (event.event_id if out.cursor_event_id is None
                                   else max(out.cursor_event_id, event.event_id))
            _apply_queue_delta(order, out, event)
            if working is not None:
                # A REST anchor has no sequence of its own to continue, exactly as in
                # `book._apply_rows`; a WS anchor keeps the seq check.
                seq = event.seq if working.source == "ws" else None
                working.apply_delta(event.side, event.price, event.delta, seq, event.ts,
                                    event.event_id)
                if not out.crossed and _crosses(working, order):
                    out.crossed = True
                    cross = _cross_fill(order, out, working, event.ts, event.event_id, fee_model)
        elif not out._seen_print(event):
            fill = _apply_print(order, out, event, fill_method, fee_model)
            out._mark_print(event)
            if fill is not None:
                fills.append(fill)

    # `crossed` is the order's worst-case flag, so it stays true once observed even on a later
    # call whose book no longer crosses; `cross` is the fill, emitted only the first time.
    return FillResult(fills=fills, state=out, cross=cross, crossed=out.crossed)
