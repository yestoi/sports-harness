"""Live order book per ticker, rebuilt from the recorded tape.

The tape has two anchors and one stream of increments:

* WebSocket `orderbook_events(kind = 'snapshot')` rows, whose `raw` is the venue's
  `orderbook_snapshot` body (`yes_dollars_fp` / `no_dollars_fp`, lists of
  `[price_dollars, count_fp]`), stamped with the recorder's receive time.
* REST `orderbook_snapshots` rows (`yes_bids` / `no_bids`, the same pair shape),
  joined to a ticker through `venue_markets`.
* WebSocket `orderbook_events(kind = 'delta')` rows carrying `side`, `price`, `delta`
  and the venue's own `ts_ms` as `ts`.

Deltas are read by `id` on the live path -- `id` is the recorder's insertion order, the
only monotone quantity here, since a venue `ts_ms` can land ahead of or behind our clock.
The `ts` predicate is a lower bound that keeps the scan short, never an upper bound
(`test_advance_book_applies_new_rows_in_id_order_without_upper_ts_bound`). The
past-instant reader `book_at` is the exception: it bounds on `ts` alone and orders by
`(ts, id)`, because "the book as of an instant" is a statement about venue time.

Both ladders are bid ladders: `yes_bids` maps a YES price to resting contracts and
`no_bids` maps a NO price to resting contracts. There is no ask ladder on Kalshi -- an
ask on one side is a bid on the other, which is what `best_ask` computes.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from sqlalchemy import text

log = logging.getLogger(__name__)

#: Probabilities and prices carry 4 decimal places; contract counts carry 2 (F40).
FOUR = Decimal("0.0001")
QTY = Decimal("0.01")
ZERO = Decimal("0.00")
ONE = Decimal("1")

YES = "yes"
NO = "no"
SIDES = (YES, NO)

#: Slack on the delta scan's lower `ts` bound. A delta's `ts` is the venue's clock and a
#: snapshot's is ours, so a delta that genuinely follows the snapshot can carry a slightly
#: earlier timestamp; 5 s of slack keeps it in the scan. Re-applying a delta the snapshot
#: already contains is prevented by the `id` cursor, not by this bound.
DELTA_LOOKBACK = timedelta(seconds=5)

#: F36: a book whose newest event at the instant is older than this reads as no book at all,
#: so a dead recorder cannot make a stale ladder look tradeable.
BOOK_MAX_AGE = timedelta(seconds=120)

# The live delta scan (`ix_obe_ticker_id`): `id` order, lower `ts` bound only.
_DELTAS_BY_ID = text(
    "select id, side, price, delta, seq, ts from orderbook_events "
    "where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower order by id"
)
# The past-instant scan (`ix_obe_ticker_ts`): bounded on `ts` at both ends, `(ts, id)` order.
_DELTAS_BY_TS = text(
    "select id, side, price, delta, seq, ts from orderbook_events "
    "where ticker = :t and kind = 'delta' and ts > :lower and ts <= :upper order by ts, id"
)
_NEWEST_WS_SNAPSHOT = text(
    "select id, ts, sid, seq, raw from orderbook_events "
    "where ticker = :t and kind = 'snapshot' order by ts desc, id desc limit 1"
)
_NEWEST_WS_SNAPSHOT_AT = text(
    "select id, ts, sid, seq, raw from orderbook_events "
    "where ticker = :t and kind = 'snapshot' and ts <= :instant order by ts desc, id desc limit 1"
)
_NEWEST_REST_SNAPSHOT = text(
    "select s.fetched_at, s.yes_bids, s.no_bids from orderbook_snapshots s "
    "join venue_markets m on m.id = s.venue_market_id "
    "where m.ticker = :t order by s.fetched_at desc limit 1"
)
# A gap is per subscription id and dirties every ticker on that sid (§0.12), so this asks
# only about `sid` -- which works for the hotfix gap rows (`ticker = ''`) and the older ones
# that carried the exposing ticker (`ix_obe_gap`).
_GAP_AFTER = text(
    "select 1 from orderbook_events where kind = 'gap' and sid = :sid and id > :anchor_id limit 1"
)
# A re-anchor target has to be a snapshot that is itself clean: re-anchoring onto a snapshot
# that was already followed by a gap on its own sid would clear `dirty` while the ladders stay
# wrong, and `advance_book` would reload on every call for the rest of the tape.
_CLEAN_SNAPSHOT_AFTER = text(
    "select 1 from orderbook_events s "
    "where s.ticker = :t and s.kind = 'snapshot' and s.id > :anchor_id "
    "and not exists (select 1 from orderbook_events g "
    "                where g.kind = 'gap' and g.sid = s.sid and g.id > s.id) limit 1"
)
# The tape position a REST ladder was fetched at: the id its gap check compares against.
_MAX_EVENT_ID_AT = text("select max(id) from orderbook_events where ts <= :fetched_at")
_NEWEST_EVENT_TS = text(
    "select max(ts) from orderbook_events where ticker = :t and ts <= :instant"
)


def opp(side: str) -> str:
    """The other side of the two-sided market."""
    if side == YES:
        return NO
    if side == NO:
        return YES
    raise ValueError(f"unknown side {side!r}")


def side_p(p: Decimal, side: str) -> Decimal:
    """A YES-space probability expressed on `side`: `p` for YES, `1 - p` for NO."""
    if side not in SIDES:
        raise ValueError(f"unknown side {side!r}")
    value = Decimal(p) if side == YES else ONE - Decimal(p)
    return value.quantize(FOUR, rounding=ROUND_HALF_UP)


def _price(x) -> Decimal:
    return Decimal(str(x)).quantize(FOUR, rounding=ROUND_HALF_UP)


def _qty(x) -> Decimal:
    return Decimal(str(x)).quantize(QTY, rounding=ROUND_HALF_UP)


def _ladder(levels: Iterable | None) -> dict[Decimal, Decimal]:
    """`[price, count]` pairs of strings or Decimals into a pruned price -> size map."""
    out: dict[Decimal, Decimal] = {}
    for level in levels or ():
        price, count = level[0], level[1]
        size = _qty(count)
        if size <= ZERO:
            continue
        key = _price(price)
        out[key] = out.get(key, ZERO) + size
    return out


@dataclass
class BookState:
    """One ticker's two bid ladders plus the tape position they were built from.

    `sid`/`seq` are the WebSocket subscription id and sequence number of the anchor,
    advanced by each delta; a REST anchor has no sequence to continue and carries
    `sid = 0, seq = 0`. `anchor_id` is the `orderbook_events.id` of the snapshot row the
    ladders came from (0 for a REST anchor); `last_event_id` is the cursor -- the newest tape
    row already folded in.

    `gap_check_id` is what the gap test compares against, and it is deliberately not
    `anchor_id`. A REST anchor is not an `orderbook_events` row at all, so its `anchor_id` is 0
    and every gap row on sid 0 would be "after" it forever; its `gap_check_id` is instead the
    tape's position when the ladder was fetched. For a WS anchor the two are the same row, so
    it defaults to `anchor_id` and callers building a book by hand need not think about it.
    """

    ticker: str
    yes_bids: dict[Decimal, Decimal]
    no_bids: dict[Decimal, Decimal]
    sid: int
    seq: int
    as_of: datetime
    source: str
    anchor_id: int
    last_event_id: int
    dirty: bool = False
    gap_check_id: int | None = None

    def __post_init__(self) -> None:
        if self.gap_check_id is None:
            self.gap_check_id = self.anchor_id

    @classmethod
    def from_levels(cls, ticker: str, yes_levels, no_levels, sid: int, seq: int,
                    as_of: datetime, source: str, anchor_id: int) -> "BookState":
        return cls(ticker=ticker, yes_bids=_ladder(yes_levels), no_bids=_ladder(no_levels),
                   sid=int(sid), seq=int(seq), as_of=as_of, source=source,
                   anchor_id=int(anchor_id), last_event_id=int(anchor_id))

    @classmethod
    def from_ws_raw(cls, ticker: str, raw: dict, sid: int, seq: int, as_of: datetime,
                    event_id: int) -> "BookState":
        """Build from a stored `orderbook_snapshot` message body.

        The venue sends `*_dollars_fp` today and sent `*_dollars` before the fractional
        rollout; both shapes are on the tape. One empty side is normal (a one-sided book),
        so only a body with neither key is a broken row.
        """
        yes = raw.get("yes_dollars_fp") or raw.get("yes_dollars")
        no = raw.get("no_dollars_fp") or raw.get("no_dollars")
        if yes is None and no is None:
            raise ValueError("snapshot without yes_dollars_fp")
        return cls.from_levels(ticker, yes or [], no or [], sid, seq, as_of, "ws", event_id)

    def _book(self, side: str) -> dict[Decimal, Decimal]:
        if side == YES:
            return self.yes_bids
        if side == NO:
            return self.no_bids
        raise ValueError(f"unknown side {side!r}")

    def apply_delta(self, side: str, price: Decimal, delta: Decimal, seq: int | None,
                    ts: datetime, event_id: int) -> None:
        """Fold one `orderbook_delta` row in, in place.

        `seq` out of step with the anchor means the tape lost a frame and the ladders can
        no longer be trusted, so the book goes dirty and stays dirty until it is re-anchored.
        `seq = None` skips the check, which is how a REST anchor takes deltas: it has no
        sequence of its own to continue.
        """
        if seq is not None:
            if int(seq) != self.seq + 1:
                self.dirty = True
            self.seq = int(seq)
        book = self._book(side)
        key = _price(price)
        size = book.get(key, ZERO) + _qty(delta)
        if size <= ZERO:
            book.pop(key, None)
        else:
            book[key] = size
        self.as_of = ts
        self.last_event_id = int(event_id)

    def best_bid(self, side: str) -> Decimal | None:
        book = self._book(side)
        return max(book) if book else None

    def best_ask(self, side: str) -> Decimal | None:
        """The cheapest price to buy `side` -- the complement of the other side's best bid."""
        other = self.best_bid(opp(side))
        return None if other is None else (ONE - other).quantize(FOUR)

    def mid(self) -> Decimal | None:
        """Midpoint in YES space; None unless both sides quote."""
        bid, ask = self.best_bid(YES), self.best_ask(YES)
        if bid is None or ask is None:
            return None
        return ((bid + ask) / 2).quantize(FOUR, rounding=ROUND_HALF_UP)

    def resting_at(self, side: str, price: Decimal) -> Decimal:
        """Contracts already resting at `price` on `side` -- the queue we would join."""
        return self._book(side).get(_price(price), ZERO)

    def copy(self) -> "BookState":
        """An independent book: mutating the copy never touches the original's ladders."""
        return BookState(ticker=self.ticker, yes_bids=dict(self.yes_bids), no_bids=dict(self.no_bids),
                         sid=self.sid, seq=self.seq, as_of=self.as_of, source=self.source,
                         anchor_id=self.anchor_id, last_event_id=self.last_event_id,
                         dirty=self.dirty, gap_check_id=self.gap_check_id)


def _apply_rows(book: BookState, rows, check_seq: bool) -> None:
    """Fold tape rows into `book` in the order given.

    A row the recorder taped without a side, price or delta (a malformed venue frame)
    cannot be applied, so the book goes dirty and the scan continues: one bad row must not
    raise out of the executor loop, and a dirty book already blocks every decision that
    would depend on the missing update.

    `check_seq` is false for a REST anchor, which has no sequence of its own to continue.
    That leaves it with no way to notice a lost frame, so it watches the venue clock instead:
    the next scan's `ts` floor is `as_of - DELTA_LOOKBACK`, so a row stamped further back than
    the book has already reached is exactly the row a later scan would drop. It is applied and
    the book says it is no longer trustworthy.
    """
    for row in rows:
        if not check_seq and row.ts < book.as_of:
            log.warning("delta ts behind the book id=%s ticker=%s ts=%s as_of=%s",
                        row.id, book.ticker, row.ts, book.as_of)
            book.dirty = True
        if row.side is None or row.price is None or row.delta is None:
            log.warning("unusable delta row id=%s ticker=%s", row.id, book.ticker)
            book.dirty = True
            book.last_event_id = int(row.id)
            # The row is unusable but its sequence is sound, so the next row's seq check has
            # something contiguous to follow instead of reporting a second, phantom gap.
            if check_seq and row.seq is not None:
                book.seq = int(row.seq)
            continue
        book.apply_delta(row.side, row.price, row.delta, row.seq if check_seq else None,
                         row.ts, row.id)


def _gapped(session, book: BookState) -> bool:
    return session.execute(_GAP_AFTER,
                           {"sid": book.sid, "anchor_id": book.gap_check_id}).first() is not None


def load_book(session, ticker: str, now: datetime) -> BookState | None:
    """Anchor on the fresher of the two snapshot sources, then advance to the tape's head.

    Returns None when the ticker has neither a WebSocket snapshot nor a REST ladder -- the
    executor's `book_source = none` case, which still places but never simulates a fill.

    `now` is the caller's clock, passed explicitly rather than read here so that a replay
    and a live loop take the same path; the anchor decision itself is made on the two
    sources' own timestamps, never on wall time.
    """
    ws = session.execute(_NEWEST_WS_SNAPSHOT, {"t": ticker}).first()
    rest = session.execute(_NEWEST_REST_SNAPSHOT, {"t": ticker}).first()
    if ws is None and rest is None:
        return None
    use_ws = ws is not None and (rest is None or ws.ts >= rest.fetched_at)
    if use_ws:
        book = BookState.from_ws_raw(ticker, ws.raw, ws.sid, ws.seq, ws.ts, ws.id)
        lower = ws.ts - DELTA_LOOKBACK
    else:
        # A REST anchor has no subscription of its own, so it takes sid 0 and its gap check
        # asks about sid 0 -- where `ws_sink` parks an exception mark it cannot attribute to a
        # real subscription. Those marks are rare but permanent, so the check is dated: the
        # gap-check id is the tape's head when the ladder was fetched, and only a mark after
        # that says anything about this ladder. The delta cursor is untouched by this and
        # still starts from 0, because a REST anchor selects its deltas by `ts >= fetched_at`.
        book = BookState.from_levels(ticker, rest.yes_bids, rest.no_bids, sid=0, seq=0,
                                     as_of=rest.fetched_at, source="rest", anchor_id=0)
        book.gap_check_id = session.execute(
            _MAX_EVENT_ID_AT, {"fetched_at": rest.fetched_at}).scalar() or 0
        lower = rest.fetched_at
    rows = session.execute(_DELTAS_BY_ID, {"t": ticker, "cursor": book.anchor_id, "lower": lower}).all()
    _apply_rows(book, rows, check_seq=use_ws)
    if _gapped(session, book):
        book.dirty = True
    return book


def advance_book(session, book: BookState, now: datetime) -> BookState:
    """Fold in every tape row after the book's cursor, returning a new book.

    The argument is never mutated: the executor keeps one book per ticker across loops and
    a half-applied book on an exception would be worse than a stale one. A gap on the
    anchor's sid dirties the result, and a dirty book re-anchors as soon as a clean snapshot
    newer than its anchor exists (that snapshot is what a resubscribe forces, §0.12).
    """
    out = book.copy()
    lower = out.as_of - DELTA_LOOKBACK
    rows = session.execute(_DELTAS_BY_ID,
                           {"t": out.ticker, "cursor": out.last_event_id, "lower": lower}).all()
    _apply_rows(out, rows, check_seq=out.source == "ws")
    if _gapped(session, out):
        out.dirty = True
    if out.dirty and session.execute(_CLEAN_SNAPSHOT_AFTER,
                                     {"t": out.ticker, "anchor_id": out.gap_check_id}).first() is not None:
        reloaded = load_book(session, out.ticker, now)
        if reloaded is not None:
            return reloaded
    return out


def book_at(session, ticker: str, instant: datetime) -> BookState | None:
    """The book as of a past instant, for markouts and replay.

    Bounded on `ts` at both ends and ordered by `(ts, id)`: the caller is asking what the
    venue's book looked like at a moment, not what our recorder had ingested by then.
    Returns None when nothing anchors it, or when the newest event at the instant is more
    than `BOOK_MAX_AGE` old -- a recorder outage must not read as a quiet market (F36).

    Dirtiness here comes only from a seq break among the deltas actually replayed. The live
    path's `gap.id > anchor.id` test cannot be reused: every gap after the instant also has a
    higher id, so hours later it would dirty every historical book on that sid and take the
    markouts with it. A gap inside the replayed range is a missing frame, which is exactly
    what the seq check in `apply_delta` catches.
    """
    ws = session.execute(_NEWEST_WS_SNAPSHOT_AT, {"t": ticker, "instant": instant}).first()
    if ws is None:
        return None
    newest = session.execute(_NEWEST_EVENT_TS, {"t": ticker, "instant": instant}).scalar()
    if newest is None or newest < instant - BOOK_MAX_AGE:
        return None
    book = BookState.from_ws_raw(ticker, ws.raw, ws.sid, ws.seq, ws.ts, ws.id)
    rows = session.execute(_DELTAS_BY_TS, {"t": ticker, "lower": ws.ts, "upper": instant}).all()
    _apply_rows(book, rows, check_seq=True)
    return book


def book_age_s(book: BookState, now: datetime) -> int:
    """Whole seconds between the book's newest applied row and `now`, never negative.

    A delta carries the venue's clock, which can sit ahead of ours, so `as_of` can be in the
    future. Callers compare this against age ceilings (`exec_book_max_age_s`) and store it on
    an order, so a negative age would read as an impossibly fresh book; the floor is 0.
    """
    return max(0, int((now - book.as_of).total_seconds()))
