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
  shorten the queue twice, so 6B §1.3's two-sided ledger reconciles the two streams by volume
  inside `RECON_HORIZON`: `print_unmatched` is print volume whose delta has not arrived and the
  `buckets` are decrement volume no print has claimed, each stamped with its own timestamp.

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
crossed our price stays crossed on every later loop, so the state carries a `crossed` flag and
the two halves of print idempotence and both re-feeds are absorbed. Those halves do different
jobs (§0.6): the `trade_ids` set makes a re-fed print a no-op by identity, which is what keeps
a late REST backfill stamped earlier than a print already applied *usable*; and `print_floor`
is the anchoring bound, below which nothing may be applied at all, because a print from before
an anchoring book's own instant is already inside the queue that book established.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from harness.execution.book import (
    DELTA_LOOKBACK,
    FOUR,
    QTY,
    SIDES,
    ZERO,
    BookState,
    opp,
    side_p,
)
from harness.pricing.fees import KALSHI_FOOTBALL, FeeModel, fee_for_order

#: The tie-break for two events stamped the same instant, and nothing more: deltas fold in
#: first. 6B §1.3's ledger reconciles a print with its own delta in either order, so no answer
#: depends on this ordering any more (`test_a_split_delta_and_a_split_print_...`); it is kept
#: because a stable total order is what makes one call and twenty chunks the same walk.
_DELTA, _PRINT = 0, 1

#: The two kinds of unclaimed decrement volume a bucket can hold. `pending` took queue ahead of
#: us under the point-estimate policy; `surplus` was beyond the queue, so a print that claims it
#: reaches us.
BUCKET_KINDS = PENDING, SURPLUS = ("pending", "surplus")
#: The cancel convention (§0.7, D3). `ahead` is the coded and documented point estimate -- an
#: unmatched decrement at our price rested ahead of us -- and `behind` is the other end of the
#: band, used offline by the re-score and never by the loop (D4).
AHEAD, BEHIND = "ahead", "behind"
#: Bounds on the per-track persisted state: a `jsonb` column on an order that rests for hours
#: must not be able to grow without limit. Both caps are reachable on a busy ticker -- the real
#: 60 s slice in `tests/test_fills_tape.py` ends with 141 live buckets and 196 ids on one order,
#: so a ticker at that rate passes `BUCKET_CAP` inside four minutes and `TRADE_ID_CAP` inside
#: about ten -- which is why neither cap may simply drop what it cannot hold. The values stay as
#: D5 set them; what each does at the cap is the load-bearing part (round 1, I1 and I4):
#:
#: * at `BUCKET_CAP`, and at the same cap for print claims, the two oldest entries of one kind
#:   are **merged** under the older timestamp. Volume is conserved either way, so nothing the
#:   ledger owes is silently unexplained, and the merged entry ages out at the earlier instant.
#:   On the bucket side that is the conservative direction: a cancellation retires into
#:   `cancels_ahead` sooner rather than a trade being claimed later. On the print side the same
#:   older stamp makes a claim *expire* sooner, which is the optimistic direction for the queue --
#:   an expired claim lets the next decrement take queue again -- so the bound is stated rather
#:   than claimed as conservative: it is reached only past 500 live claims, the volume stays in
#:   the list until the horizon takes it, and at 60 s of horizon an expiry the merge brings
#:   forward was seconds from happening anyway.
#: * at `TRADE_ID_CAP` the oldest ids drop and `print_floor` rises to the oldest retained id's
#:   timestamp, so nothing below the floor can re-apply (§0.6, D5). That skips a fill at the very
#:   front of the queue rather than inventing one.
BUCKET_CAP = 500
TRADE_ID_CAP = 2000
#: The reconciliation horizon, applied to **both** sides of the ledger (§0.5, §1.3; round 1, I5):
#: a decrement bucket is claimable only by a print within this of the bucket's own timestamp, and
#: a print claim is matchable only by a decrement within this of the print's own timestamp. The
#: same 60 s as `store.PRINT_LOOKBACK`, which is the window the executor re-reads prints over;
#: defined here rather than imported because `harness.execution.store` imports this module.
RECON_HORIZON = timedelta(seconds=60)

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

    The watched track stores it in `queue_remaining` / `filled_contracts` /
    `tape_cursor_event_id` / the four ledger columns / `recon_state`; the counterfactual in the
    `nw_` twins. C0's `traded_at_price` is not a ledger term and the arithmetic never reads it:
    a post-boundary order leaves that column NULL rather than reusing it, which is how the
    boundary is visible by nullness (ruling CR-3, §2 row 3). A **pre-boundary** order still being
    simulated -- an open pre-6B order, or the `nw_done = false` counterfactual of one -- keeps the
    C0 value it already had: `legacy_traded_at_price` carries it through the state unchanged so
    the writer can put it back, because overwriting it with NULL would both destroy a recorded
    pre-6B quantity and make a pre-boundary row indistinguishable from a post-boundary one
    (round 2, Important A).

    The ledger is two-sided (§0.5), and the horizon applies to **both** sides (§1.3 clarification,
    round 1 I5). `prints` are print volume whose decrement has not arrived, each stamped with the
    print's own timestamp; `buckets` are decrement volume no print has claimed, each stamped with
    the decrement's own timestamp and tagged `pending` (it took queue ahead of us) or `surplus`
    (it was beyond the queue, so a print that claims it reaches us). Inside `RECON_HORIZON` the
    two sides cancel each other out; past it each ages out on its own terms -- an aged `pending`
    bucket retires into `cancels_ahead`, because it was a real cancellation; an aged `surplus`
    bucket is discarded, having never moved the queue; and an aged print claim is discarded, so a
    decrement an hour later is read as the genuine cancellation it is rather than being absorbed
    by a trade that is long over.

    `print_unmatched`, `pending_unmatched` and `pending_surplus` are all **derived** sums of those
    two lists, which is what §2's invariant query checks the scalar columns against; the lists
    themselves are what persists, in `recon_state`.

    `crossed` and the print bookkeeping are state, not per-call facts, because both of the
    tape's streams can be re-fed. `crossed` makes the worst-case fill once per order even though
    the book keeps crossing on every later loop. `print_floor` and `trade_ids` are the two
    halves of print idempotence and do different jobs (§0.6, D5): the floor makes a re-anchor
    sound -- nothing stamped before the anchoring book's own instant may be applied against the
    newly anchored queue -- and the id set makes a late REST backfill *above* the floor usable,
    which a timestamp watermark alone could not.

    Every list is bounded on every write. `buckets` and `prints` are pruned to `RECON_HORIZON` and
    held at `BUCKET_CAP` by merging their two oldest entries of one kind rather than by dropping
    any; `trade_ids` are pruned to the track's own window (`placed_at - RECON_HORIZON` upwards,
    the window `_tape` re-reads prints over) and capped at `TRADE_ID_CAP`, where the oldest ids
    drop and `print_floor` rises to the oldest retained id so nothing below it can re-apply
    (ruling IM-12). The window pruning is defensive: `_merge_events` admits nothing at or below
    `placed_at`, so in practice the cap is what bounds the set.
    """

    queue_remaining: Decimal | None
    filled_contracts: Decimal
    cursor_event_id: int | None
    crossed: bool = False
    cancels_ahead: Decimal = ZERO
    #: C0's `traded_at_price` as the row already held it, or None for an order that never had one
    #: (every post-boundary order). Carried, never read by the arithmetic, never recomputed.
    legacy_traded_at_price: Decimal | None = None
    #: `(ts, size)`, oldest first: print volume whose decrement has not arrived.
    prints: tuple[tuple[datetime, Decimal], ...] = ()
    #: `(ts, kind, size)`, oldest first. `kind` is one of `BUCKET_KINDS`.
    buckets: tuple[tuple[datetime, str, Decimal], ...] = ()
    #: `(ts, trade_id)`, oldest first: the prints already applied above the floor.
    trade_ids: tuple[tuple[datetime, str], ...] = ()
    print_floor: datetime | None = None

    def __post_init__(self) -> None:
        if self.queue_remaining is not None:
            self.queue_remaining = _q(self.queue_remaining)
        self.filled_contracts = _q(self.filled_contracts)
        self.cancels_ahead = _q(self.cancels_ahead)
        self.prints = tuple((ts, _q(size)) for ts, size in self.prints)
        self.buckets = tuple((ts, kind, _q(size)) for ts, kind, size in self.buckets)
        self.trade_ids = tuple((ts, str(tid)) for ts, tid in self.trade_ids)

    @classmethod
    def initial(cls, order: PaperOrder) -> "SimState":
        return cls(queue_remaining=order.queue_ahead_at_place, filled_contracts=ZERO,
                   cursor_event_id=None)

    @property
    def print_unmatched(self) -> Decimal:
        """The surviving print claims' sum -- the scalar column, by construction (§2)."""
        return _q(sum((size for _ts, size in self.prints), ZERO))

    @property
    def pending_unmatched(self) -> Decimal:
        """The surviving `pending` buckets' sum -- the scalar column, by construction (§2)."""
        return _q(sum((size for _ts, kind, size in self.buckets if kind == PENDING), ZERO))

    @property
    def pending_surplus(self) -> Decimal:
        """The surviving `surplus` buckets' sum."""
        return _q(sum((size for _ts, kind, size in self.buckets if kind == SURPLUS), ZERO))

    def _copy(self) -> "SimState":
        return SimState(self.queue_remaining, self.filled_contracts, self.cursor_event_id,
                        self.crossed, self.cancels_ahead, self.legacy_traded_at_price,
                        self.prints, self.buckets, self.trade_ids, self.print_floor)

    def anchor(self, *, queue: Decimal | None, cursor_event_id: int | None,
               anchor_as_of: datetime) -> None:
        """Take this track to a freshly anchored book (§1.2's helper calls this).

        The ledger is emptied rather than carried: its buckets and its unmatched print volume
        describe a queue that no longer exists. `cancels_ahead` survives, being a retired count
        rather than a claim on the current queue, and the trade-id set is pruned to the new
        floor, below which nothing can re-apply anyway.
        """
        self.queue_remaining = None if queue is None else _q(queue)
        self.cursor_event_id = cursor_event_id
        self.print_floor = anchor_as_of - DELTA_LOOKBACK
        self.prints = ()
        self.buckets = ()
        self.trade_ids = tuple((ts, tid) for ts, tid in self.trade_ids
                               if ts > self.print_floor)

    def _seen_ids(self) -> set[str]:
        """The applied trade ids as a set, built once per `simulate_fills` call (round 1, I4).

        The walk tests every print it is handed against this, and the executor re-reads the
        order's whole resting print history every loop, so the test has to be O(1) per print: at
        `TRADE_ID_CAP` the old scan over a tuple cost tens of milliseconds per track per loop.
        """
        return {tid for _ts, tid in self.trade_ids}

    def _mark_prints(self, marked: list[tuple[datetime, str]], window_start: datetime) -> None:
        """Record this call's applied prints, prune to the window, and raise the floor at the cap.

        Once per call rather than once per print (round 1, I4): the pruning and the cap are
        properties of the set, not of any one print, and rebuilding the tuple per print made a
        loop's re-feed quadratic.

        One consequence, stated because it is a real difference from the per-print version: the
        window prune and the cap now move once per call, so a history fed as one call and the same
        history fed in chunks can disagree about `trade_ids` and `print_floor` **at**
        `TRADE_ID_CAP` -- the per-print version could too, at a different point. Both answers are
        bounded by the cap, and the cap's own error is to skip a fill at the front of the queue
        rather than to invent one, so neither can over-fill; the ledger's arithmetic is unaffected,
        because retirement still happens only at event timestamps. Below the cap the two are
        identical, which is what `test_chunking_invariance` and the twenty-chunk tape feed pin.

        Two prunings, and they are different things (ruling IM-12). The **window** pruning is the
        ordinary one: the executor re-reads prints from `placed_at - PRINT_LOOKBACK` every loop,
        so an id stamped before that can never be offered again and keeping it is dead weight.
        The **cap** is the backstop: at it the oldest ids drop, so a print below the new floor
        could no longer be recognised as seen, and raising the floor to the oldest *retained*
        id's timestamp is what keeps it from re-applying instead (D5). Being wrong in that
        direction skips a fill at the very front of the queue rather than inventing one.
        """
        if not marked:
            return
        ids = tuple((ts, tid) for ts, tid in self.trade_ids if ts >= window_start)
        ids = ids + tuple(marked)
        if len(ids) > TRADE_ID_CAP:
            ids = tuple(sorted(ids)[-TRADE_ID_CAP:])
            oldest = ids[0][0]
            self.print_floor = oldest if self.print_floor is None else max(self.print_floor,
                                                                          oldest)
        self.trade_ids = ids


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
                  cursor: int | None, print_floor: datetime | None) -> list[tuple]:
    """Prints and deltas in `ts` order, deltas first at equal `ts`, input order within a kind.

    Dropped here rather than in the walk: anything at or before `placed_at` (we were not in
    the queue yet), anything at or before the `print_floor` (a print from before the anchoring
    book's own instant is already inside the queue it anchored, §0.6), anything after
    `deadline` (the track has stopped), and any delta the cursor already covers (it is folded
    into the state and the book we were handed).

    The `_DELTA` before `_PRINT` ordering at equal timestamps is kept, but it is no longer
    load-bearing: the ledger of §1.3 reconciles a print with its own delta in either order, and
    the four splittings of `test_a_split_delta_and_a_split_print_reconcile_to_the_same_answer`
    are what say so.
    """
    floor = placed_at if print_floor is None else max(placed_at, print_floor)
    events: list[tuple] = []
    for i, d in enumerate(deltas):
        if cursor is not None and d.event_id <= cursor:
            continue
        if placed_at < d.ts <= deadline:
            events.append((d.ts, _DELTA, i, d))
    for i, p in enumerate(prints):
        if floor < p.ts <= deadline:
            events.append((p.ts, _PRINT, i, p))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    return events


def _merge_oldest_pair(entries: list, kind_at) -> bool:
    """Merge the two oldest entries of one kind in place, under the older timestamp.

    `True` when a merge was possible. This is how both bounded lists stay inside their cap
    without losing volume (round 1, I1): dropping the oldest bucket would leave a decrement
    unexplained, and the next print would take the queue a second time -- the very double count
    C3 removes. Merging conserves the volume and keeps the *older* instant, so the merged entry
    ages out earlier: a cancellation retires sooner rather than a trade being claimed later.

    With two kinds and a cap of two or more, a merge is always possible; `False` is the
    degenerate case the callers handle by retiring instead.
    """
    for i, entry in enumerate(entries):
        kind = kind_at(entry)
        for j in range(i + 1, len(entries)):
            if kind_at(entries[j]) == kind:
                merged = list(entry)
                merged[-1] = _q(entry[-1] + entries[j][-1])
                entries[i] = tuple(merged)
                del entries[j]
                return True
    return False


def _bound_buckets(state: SimState, entries: list) -> tuple:
    """`entries` held at `BUCKET_CAP`, conserving volume; a pending entry that cannot be merged
    retires into `cancels_ahead` rather than vanishing."""
    while len(entries) > BUCKET_CAP:
        if _merge_oldest_pair(entries, lambda e: e[1]):
            continue
        _ts, kind, size = entries.pop(0)
        if kind == PENDING:
            state.cancels_ahead = _q(state.cancels_ahead + size)
    return tuple(entries)


def _bound_prints(entries: list) -> tuple:
    """The print side of the same bound. One kind, so a merge is always possible."""
    while len(entries) > BUCKET_CAP and _merge_oldest_pair(entries, lambda _e: PENDING):
        pass
    return tuple(entries)


def _retire(state: SimState, now_ts: datetime) -> None:
    """Age out every entry older than the horizon, as of one event's timestamp.

    A `pending` bucket that no print claimed within `RECON_HORIZON` was a real cancellation of
    size ahead of us: it retires into `cancels_ahead`, which is a count, not a claim. A
    `surplus` bucket that no print claimed never moved the queue and is simply discarded. A print
    claim no decrement matched within the horizon is discarded too (round 1, I5), so a decrement
    an hour after the trade is read as the cancellation it is instead of being absorbed by it.

    Retirement happens at event timestamps only, never at the walk's deadline, so one call and
    twenty chunks of the same history retire exactly the same entries at exactly the same
    points (`test_chunking_invariance`).
    """
    kept: list[tuple[datetime, str, Decimal]] = []
    for ts, kind, size in state.buckets:
        if now_ts - ts <= RECON_HORIZON:
            kept.append((ts, kind, size))
        elif kind == PENDING:
            state.cancels_ahead = _q(state.cancels_ahead + size)
    state.buckets = _bound_buckets(state, kept)
    state.prints = tuple((ts, size) for ts, size in state.prints
                         if now_ts - ts <= RECON_HORIZON)


def _claim(state: SimState, kind: str, want: Decimal, now_ts: datetime) -> Decimal:
    """Take up to `want` from the buckets of `kind` inside the horizon, oldest first.

    Returns what was actually taken. Oldest first because a print explains the decrement that
    has been waiting longest for one, and because it is the only order that makes the walk
    independent of how the venue chose to split its rows.
    """
    taken = ZERO
    kept: list[tuple[datetime, str, Decimal]] = []
    for ts, bucket_kind, size in state.buckets:
        room = want - taken
        if bucket_kind != kind or room <= ZERO or now_ts - ts > RECON_HORIZON:
            kept.append((ts, bucket_kind, size))
            continue
        used = min(size, room)
        taken = _q(taken + used)
        if size - used > ZERO:
            kept.append((ts, bucket_kind, _q(size - used)))
    state.buckets = tuple(kept)
    return taken


def _match_prints(state: SimState, want: Decimal, now_ts: datetime) -> Decimal:
    """Take up to `want` from the print claims inside the horizon, oldest first (round 1, I5).

    The mirror of `_claim`: this is the part of a decrement that a print has already reported,
    which moved the queue when that print was applied and must not move it again.
    """
    taken = ZERO
    kept: list[tuple[datetime, Decimal]] = []
    for ts, size in state.prints:
        room = want - taken
        if room <= ZERO or now_ts - ts > RECON_HORIZON:
            kept.append((ts, size))
            continue
        used = min(size, room)
        taken = _q(taken + used)
        if size - used > ZERO:
            kept.append((ts, _q(size - used)))
    state.prints = tuple(kept)
    return taken


def _bucket(state: SimState, ts: datetime, kind: str, size: Decimal) -> None:
    """Record unclaimed decrement volume, bounded by merging rather than by dropping."""
    if size <= ZERO:
        return
    state.buckets = _bound_buckets(state, [*state.buckets, (ts, kind, _q(size))])


def _claim_print(state: SimState, ts: datetime, size: Decimal) -> None:
    """Record print volume whose decrement has not arrived, under the print's own timestamp."""
    if size <= ZERO:
        return
    state.prints = _bound_prints([*state.prints, (ts, _q(size))])


def _apply_queue_delta(order: PaperOrder, state: SimState, delta: TapeDelta,
                       cancel_policy: str) -> None:
    """Fold one shrinking delta at our own price into the ledger (§1.3's first clause).

    Only a shrinking level at our own price on our own side can say anything about the queue
    ahead of us. A positive delta is a late joiner, who sits behind us.

    `m = min(d, print_unmatched)` is the part of this decrement a print has already reported,
    taken oldest claim first and only from claims inside the horizon; it moved the queue when
    that print was applied and must not move it again. The rest is volume no print has explained
    *yet*: under the point-estimate policy (`ahead`) it rested ahead of us, so
    `consumed = min(queue, rest)` comes off the queue now and the remainder was beyond the queue.
    Under `behind` the same volume is assumed to have rested behind us, so it takes no queue
    here; a print that later claims it is what moves the queue instead, because a claimed
    decrement was a trade rather than a cancellation and the contracts it lifted were ahead of us
    after all.
    """
    if delta.side != order.side or delta.price != order.prob or delta.delta >= ZERO:
        return
    size = -delta.delta
    matched = _match_prints(state, size, delta.ts)
    rest = _q(size - matched)
    if rest <= ZERO:
        return
    if cancel_policy == BEHIND:
        _bucket(state, delta.ts, PENDING, rest)
        return
    consumed = min(state.queue_remaining, rest)
    state.queue_remaining = _q(state.queue_remaining - consumed)
    _bucket(state, delta.ts, PENDING, consumed)
    _bucket(state, delta.ts, SURPLUS, _q(rest - consumed))


def _apply_print(order: PaperOrder, state: SimState, tape_print: TapePrint, fill_method: str,
                 fee_model: FeeModel, cancel_policy: str) -> SimFill | None:
    """The queue arithmetic for one print (§1.3's second clause); returns the fill, if any."""
    if not hits(tape_print, order.side):
        return None
    price = price_on_side(tape_print, order.side)
    if price > order.prob:
        return None
    remaining = order.contracts - state.filled_contracts
    if price < order.prob:
        # Swept through us: the trade happened past our level, so everything ahead of us is
        # gone by definition and our size trades. It says nothing about volume *at* our price,
        # so no ledger term moves.
        state.queue_remaining = ZERO
        contracts = min(tape_print.count, remaining)
        through = True
    else:
        count = tape_print.count
        contracts = ZERO
        # Volume this print explains that was already taken off the queue as a pending
        # decrement: ahead of us and now known to have traded, so no fill and no second move.
        claimed_pending = _claim(state, PENDING, count, tape_print.ts)
        if cancel_policy == BEHIND and claimed_pending > ZERO:
            # Under `behind` the decrement took no queue when it arrived, because it was assumed
            # to rest behind us. A print claiming it says it traded, so those contracts were
            # ahead of us and the queue moves now -- and whatever of them was beyond the queue
            # reaches us, exactly as the surplus path does under `ahead` (round 1, I2). Without
            # that half, `behind` reported no fill at all for a trade bigger than the queue when
            # the delta happened to arrive first.
            consumed = min(state.queue_remaining, claimed_pending)
            state.queue_remaining = _q(state.queue_remaining - consumed)
            taken = min(_q(claimed_pending - consumed), remaining)
            contracts = _q(contracts + taken)
            remaining = _q(remaining - taken)
        # Volume this print explains that was beyond the queue: it reaches us.
        claimed_surplus = _claim(state, SURPLUS, _q(count - claimed_pending), tape_print.ts)
        taken = min(claimed_surplus, remaining)
        contracts = _q(contracts + taken)
        remaining = _q(remaining - taken)
        # What neither ledger term explains: the ordinary case of a print arriving before its
        # own delta. It consumes queue and then reaches us, and is remembered -- under this
        # print's own timestamp, so it ages with the horizon -- so that the delta reporting it
        # cannot move the queue a second time.
        rest = _q(count - claimed_pending - claimed_surplus)
        consumed = min(state.queue_remaining, rest)
        state.queue_remaining = _q(state.queue_remaining - consumed)
        contracts = _q(contracts + min(_q(rest - consumed), remaining))
        _claim_print(state, tape_print.ts, rest)
        through = False
    if contracts <= ZERO:
        return None
    state.filled_contracts = _q(state.filled_contracts + contracts)
    return SimFill(prob=order.prob, contracts=contracts,
                   fee=fee_for_order(fee_model, "maker", order.prob, contracts),
                   filled_at=tape_print.ts, fill_method=fill_method,
                   source_trade_id=tape_print.trade_id, source_event_id=None,
                   taker_side=tape_print.taker_side, through=through,
                   tape_source=tape_print.source)


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
                   fee_model: FeeModel = KALSHI_FOOTBALL,
                   cancel_policy: str = AHEAD) -> FillResult:
    """Walk the tape from `state`'s cursor to `deadline` and report what would have filled.

    `deadline` is explicit because the two tracks stop at different instants (F3): the watched
    track at cancel or expiry, the no-watcher track always at the order's natural `expiry`.
    `fill_method` is the label those tracks carry into `fills` (`queue_model` / `no_watcher`).

    The caller passes the book as of its own cursor; this walks a copy, so the caller's book
    is untouched and the cross test reads a book advanced by exactly the deltas given here.
    """
    if cancel_policy not in (AHEAD, BEHIND):
        # Before the R10 return below, so an unknown policy is a programming error on every
        # order rather than only on the ones that happen to have a queue (round 1, minor).
        raise ValueError(f"unknown cancel policy {cancel_policy!r}")
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

    # One set per call, updated as prints are applied, and written back to `trade_ids` once at
    # the end (round 1, I4): the executor re-reads the order's whole resting print history every
    # loop, so this test runs `prints x ids` times per track per loop and has to be O(1).
    seen = out._seen_ids()
    marked: list[tuple[datetime, str]] = []
    for _ts, kind, _i, event in _merge_events(prints, deltas, order.placed_at, deadline,
                                              state.cursor_event_id, out.print_floor):
        _retire(out, event.ts)
        if kind == _DELTA:
            out.cursor_event_id = (event.event_id if out.cursor_event_id is None
                                   else max(out.cursor_event_id, event.event_id))
            _apply_queue_delta(order, out, event, cancel_policy)
            if working is not None:
                # A REST anchor has no sequence of its own to continue, exactly as in
                # `book._apply_rows`; a WS anchor keeps the seq check.
                seq = event.seq if working.source == "ws" else None
                working.apply_delta(event.side, event.price, event.delta, seq, event.ts,
                                    event.event_id)
                if not out.crossed and _crosses(working, order):
                    out.crossed = True
                    cross = _cross_fill(order, out, working, event.ts, event.event_id, fee_model)
        elif event.trade_id not in seen:
            fill = _apply_print(order, out, event, fill_method, fee_model, cancel_policy)
            seen.add(event.trade_id)
            marked.append((event.ts, event.trade_id))
            if fill is not None:
                fills.append(fill)

    # `order.placed_at - RECON_HORIZON` is the track's own window start, which is exactly the
    # lower bound `_tape` reads prints from (`loop.py:729`, `placed_at - store.PRINT_LOOKBACK`):
    # an id stamped before it can never be offered to this track again.
    out._mark_prints(marked, order.placed_at - RECON_HORIZON)

    # `crossed` is the order's worst-case flag, so it stays true once observed even on a later
    # call whose book no longer crosses; `cross` is the fill, emitted only the first time.
    return FillResult(fills=fills, state=out, cross=cross, crossed=out.crossed)
