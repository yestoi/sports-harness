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

#: Why a book is dirty. One flag with several producers (review I-5): a lost subscription frame
#: (`gap`), a reconnect the book was anchored before (`session_boundary`), a delta the scan's
#: `ts` floor dropped or a REST-anchored book saw arrive behind itself (`event_age`), and a tape
#: row the recorder stored without a side, price or delta (`malformed_row`). There is
#: deliberately no `recovery` cause (ruling IM-11): the recovery branch is the one taken when a
#: market has *stopped* being dirty. §1.5's interval rows carry one of these, and `recorder_dead`
#: beside them, which is the loop's verdict about the recorder rather than this book's about
#: itself.
DIRTY_CAUSES = ("gap", "session_boundary", "event_age", "malformed_row")

# The consumer-side probe of §0.2, on `ix_obe_ticker_id`: is there a delta this ticker taped
# after our cursor that the scan's own `ts` floor excluded? Such a row is applied nowhere and
# leaves no gap row, because nothing was lost in transit -- it arrived stamped further behind
# than the book had already reached. `limit 1`: the question is whether one exists.
_LATE_DELTA = text(
    "select 1 from orderbook_events where ticker = :t and kind = 'delta' "
    "and id > :cursor and ts < :lower limit 1"
)
# `_LATE_DELTA` bounded at a past instant, for the replay path: a row taped after the instant is
# not information the replayed step had. Same index, same `limit 1`.
_LATE_DELTA_AT = text(
    "select 1 from orderbook_events where ticker = :t and kind = 'delta' "
    "and id > :cursor and ts < :lower and ts <= :instant limit 1"
)
# The newest reconnect, bounded at a past instant for the replay path. `operator_events` is
# small (one row per operator-visible event) and read in full with a `limit 1` through the
# ordering; it carries no index on `kind`, so this is a walk of a small table, stated rather
# than claimed otherwise.
_NEWEST_WS_CONNECT = text(
    "select max(ts) from operator_events where kind = 'ws_connect'"
)
_NEWEST_WS_CONNECT_AT = text(
    "select max(ts) from operator_events where kind = 'ws_connect' and ts <= :instant"
)

# The live delta scan (`ix_obe_ticker_id`): `id` order, lower `ts` bound only.
_DELTAS_BY_ID = text(
    "select id, side, price, delta, seq, ts from orderbook_events "
    "where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower order by id"
)
# The past-instant scan (`ix_obe_ticker_ts`): the *same* delta set the live path takes --
# `id > anchor` plus the `DELTA_LOOKBACK` floor -- with the instant as its upper bound, and
# `(ts, id)` order rather than `id` order because the question is what the venue's book looked
# like at a moment. Selecting on `ts >` alone would drop every delta stamped a little before
# its own snapshot, which is exactly the case `DELTA_LOOKBACK` exists for, and would leave the
# level wrong until the next snapshot rather than for five seconds (fix round 1, I1).
_DELTAS_BY_TS = text(
    "select id, side, price, delta, seq, ts from orderbook_events "
    "where ticker = :t and kind = 'delta' and id > :cursor and ts >= :lower and ts <= :upper "
    "order by ts, id"
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
_NEWEST_REST_SNAPSHOT_AT = text(
    "select s.fetched_at, s.yes_bids, s.no_bids from orderbook_snapshots s "
    "join venue_markets m on m.id = s.venue_market_id "
    "where m.ticker = :t and s.fetched_at <= :instant order by s.fetched_at desc limit 1"
)
# A gap is per subscription id and dirties every ticker on that sid (§0.12), so this asks
# only about `sid` -- which works for the hotfix gap rows (`ticker = ''`) and the older ones
# that carried the exposing ticker (`ix_obe_gap`).
_GAP_AFTER = text(
    "select 1 from orderbook_events where kind = 'gap' and sid = :sid and id > :anchor_id limit 1"
)
# The same test bounded at a past instant. A gap taped *after* the instant says nothing about
# the book at it -- reusing the unbounded live test would dirty every historical book on that
# subscription for the rest of the season -- but a gap the live loop had already seen is a lost
# frame the replayed book has to carry too.
_GAP_AFTER_AT = text(
    "select 1 from orderbook_events where kind = 'gap' and sid = :sid and id > :anchor_id "
    "and ts <= :instant limit 1"
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
_CLEAN_SNAPSHOT_AFTER_AT = text(
    "select 1 from orderbook_events s "
    "where s.ticker = :t and s.kind = 'snapshot' and s.id > :anchor_id and s.ts <= :instant "
    "and not exists (select 1 from orderbook_events g "
    "                where g.kind = 'gap' and g.sid = s.sid and g.id > s.id "
    "                  and g.ts <= :instant) limit 1"
)
# Did a new anchor land between two past instants? A WebSocket snapshot or a REST ladder is
# the only thing that can change which anchor `book_at` would pick, so these two probes are
# what let `BookWalker` advance a cached book instead of rebuilding it (final review I7).
_WS_SNAPSHOT_BETWEEN = text(
    "select 1 from orderbook_events where ticker = :t and kind = 'snapshot' "
    "and ts > :lower and ts <= :upper limit 1"
)
_REST_SNAPSHOT_BETWEEN = text(
    "select 1 from orderbook_snapshots s join venue_markets m on m.id = s.venue_market_id "
    "where m.ticker = :t and s.fetched_at > :lower and s.fetched_at <= :upper limit 1"
)
# The tape position a REST ladder was fetched at: the id its gap check compares against.
# Bounded below as well as above (final review I3). `orderbook_events` is weekly-partitioned
# on `ts` with a per-partition PK of `(id, ts)`, so `ts <= :fetched_at` alone prunes only the
# partitions starting after the fetch: inside the one holding it, an unbounded `max(id)`
# filtered every row taped afterwards. That runs on the REST-anchor branch of both `load_book`
# and `book_at`, and `book_at` is what the markouts stage calls for every horizon with no
# nearby quote, at instants hours or days in the past.
_MAX_EVENT_ID_AT = text(
    "select max(id) from orderbook_events where ts <= :fetched_at and ts > :lower")

#: The bounded tape lookback and its one widening, the same pair `store.newest_event_ts` uses.
#: 10 minutes covers any ladder a live loop fetched; 24 h is the backstop for a replay reaching
#: into a quiet stretch. Past that the answer is 0, which makes every gap on sid 0 read as
#: after the ladder -- the conservative direction, since a dirty book blocks decisions while a
#: clean one would let a ladder that may have missed frames look tradeable (F36).
TAPE_WINDOW = timedelta(minutes=10)
TAPE_WINDOW_WIDE = timedelta(hours=24)


def _max_event_id_at(session, fetched_at: datetime) -> int:
    """The tape's head id at `fetched_at`, looked up inside `TAPE_WINDOW`, then once inside
    `TAPE_WINDOW_WIDE`, then 0."""
    for window in (TAPE_WINDOW, TAPE_WINDOW_WIDE):
        got = session.execute(
            _MAX_EVENT_ID_AT, {"fetched_at": fetched_at, "lower": fetched_at - window}).scalar()
        if got is not None:
            return got
    return 0
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
    dirty_cause: str | None = None
    #: The instant these ladders were anchored on, never moved by a delta (review C-1). `as_of`
    #: is "how fresh is this book" and moves with every frame; this is "when was it last rebuilt
    #: from a snapshot", which is the only thing §0.3's session test can be made on.
    anchor_as_of: datetime | None = None

    def __post_init__(self) -> None:
        if self.gap_check_id is None:
            self.gap_check_id = self.anchor_id
        if self.anchor_as_of is None:
            self.anchor_as_of = self.as_of

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

    def mark_dirty(self, cause: str) -> None:
        """Record that this book cannot be trusted, and why.

        The first cause wins. A book that lost a frame and then saw a malformed row is dirty
        for the first reason; overwriting it would make the cause a property of the order the
        checks happen to run in rather than of what went wrong.
        """
        if cause not in DIRTY_CAUSES:
            raise ValueError(f"unknown dirty cause {cause!r}")
        self.dirty = True
        if self.dirty_cause is None:
            self.dirty_cause = cause

    def apply_delta(self, side: str, price: Decimal, delta: Decimal, seq: int | None,
                    ts: datetime, event_id: int) -> None:
        """Fold one `orderbook_delta` row in, in place.

        `seq` is recorded as the last frame this book applied and nothing more (C1, §0.2). It
        counts per *subscription*, and one `sid` carries up to 500 tickers, so a ticker whose
        frames read 1 then 3 lost nothing when frame 2 was another ticker's. The verdict that
        can be made on a subscription is made where the whole subscription is visible --
        `WsSink._check_seq` writes a `gap` row under the sentinel `ticker = ""` -- and this book
        reads it back through `_gapped`. `seq = None` still means "no sequence to record", which
        is how a REST anchor takes deltas.
        """
        if seq is not None:
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
                         dirty=self.dirty, gap_check_id=self.gap_check_id,
                         dirty_cause=self.dirty_cause, anchor_as_of=self.anchor_as_of)


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
            book.mark_dirty("event_age")
        if row.side is None or row.price is None or row.delta is None:
            log.warning("unusable delta row id=%s ticker=%s", row.id, book.ticker)
            book.mark_dirty("malformed_row")
            book.last_event_id = int(row.id)
            # The row is unusable but its sequence is sound, and `seq` is the record of the
            # last frame this book applied (C1), so the unusable row still advances it rather
            # than leaving the book claiming an older frame than it has actually seen.
            if check_seq and row.seq is not None:
                book.seq = int(row.seq)
            continue
        book.apply_delta(row.side, row.price, row.delta, row.seq if check_seq else None,
                         row.ts, row.id)


def _gapped(session, book: BookState) -> bool:
    return session.execute(_GAP_AFTER,
                           {"sid": book.sid, "anchor_id": book.gap_check_id}).first() is not None


def _gapped_at(session, book: BookState, instant: datetime) -> bool:
    """`_gapped` bounded at a past instant (see `_GAP_AFTER_AT`)."""
    return session.execute(_GAP_AFTER_AT,
                           {"sid": book.sid, "anchor_id": book.gap_check_id,
                            "instant": instant}).first() is not None


def _dropped_delta(session, ticker: str, cursor: int, lower: datetime) -> bool:
    """Whether a delta after `cursor` was excluded by the scan's own `ts` floor (§0.2).

    The scan is `id > :cursor and ts >= :lower`. A row that clears the id cursor and fails the
    floor is applied nowhere, and nothing else in the system notices: it arrived in order, so
    the recorder saw no sequence break and wrote no gap row. One bounded `limit 1` probe per
    advance is the consumer's only way to know its ladders are missing a level change.
    """
    return session.execute(_LATE_DELTA,
                           {"t": ticker, "cursor": cursor, "lower": lower}).first() is not None


def _dropped_delta_at(session, ticker: str, cursor: int, lower: datetime,
                      instant: datetime) -> bool:
    """`_dropped_delta` bounded at a past instant: a row taped after the instant is not
    information the replayed step had."""
    return session.execute(
        _LATE_DELTA_AT,
        {"t": ticker, "cursor": cursor, "lower": lower, "instant": instant}).first() is not None


def newest_ws_connect(session, at: datetime | None = None) -> datetime | None:
    """The newest recorded reconnect, or the newest at or before `at` on the replay path.

    Read once per executor step and handed to every `advance_book` call in it (§0.3): a
    per-book read would be one query per ticker per loop for an answer that is the same for all
    of them.
    """
    if at is None:
        return session.execute(_NEWEST_WS_CONNECT).scalar()
    return session.execute(_NEWEST_WS_CONNECT_AT, {"instant": at}).scalar()


def _mark_session_boundary(book: BookState, ws_connect_at: datetime | None) -> None:
    """Dirty a WS-anchored book whose anchor predates the newest reconnect (§0.3).

    Applied to the advanced book *and* to any book a re-anchor reloads, because a re-anchor
    target is only required to be newer than the previous anchor.
    """
    if (ws_connect_at is not None and book.source == "ws"
            and book.anchor_as_of is not None and book.anchor_as_of < ws_connect_at):
        book.mark_dirty("session_boundary")


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
        book.gap_check_id = _max_event_id_at(session, rest.fetched_at)
        lower = rest.fetched_at
    rows = session.execute(_DELTAS_BY_ID, {"t": ticker, "cursor": book.anchor_id, "lower": lower}).all()
    _apply_rows(book, rows, check_seq=use_ws)
    if _gapped(session, book):
        book.mark_dirty("gap")
    return book


def advance_book(session, book: BookState, now: datetime,
                 ws_connect_at: datetime | None = None) -> BookState:
    """Fold in every tape row after the book's cursor, returning a new book.

    The argument is never mutated: the executor keeps one book per ticker across loops and
    a half-applied book on an exception would be worse than a stale one. A gap on the
    anchor's sid dirties the result, so does a delta the scan's `ts` floor dropped (§0.2) and
    an anchor older than the newest reconnect (§0.3), and a dirty book re-anchors as soon as a
    clean snapshot newer than its anchor exists (that snapshot is what a resubscribe forces).

    `ws_connect_at` is the caller's once-per-step read of `newest_ws_connect`; None skips the
    session test, which is what a caller with no operator-event history (a pure unit fixture)
    gets.
    """
    out = book.copy()
    lower = out.as_of - DELTA_LOOKBACK
    cursor = out.last_event_id
    rows = session.execute(_DELTAS_BY_ID,
                           {"t": out.ticker, "cursor": cursor, "lower": lower}).all()
    _apply_rows(out, rows, check_seq=out.source == "ws")
    if _gapped(session, out):
        out.mark_dirty("gap")
    if _dropped_delta(session, out.ticker, cursor, lower):
        out.mark_dirty("event_age")
    # The client dropped its sids and cleared its remembered sequences at the reconnect, so
    # whatever was lost across the outage produced no gap row anywhere. A book anchored before
    # it is folding the new subscription's deltas onto ladders that missed the outage, and only
    # a fresh snapshot can settle that.
    _mark_session_boundary(out, ws_connect_at)
    if out.dirty and session.execute(_CLEAN_SNAPSHOT_AFTER,
                                     {"t": out.ticker, "anchor_id": out.gap_check_id}).first() is not None:
        reloaded = load_book(session, out.ticker, now)
        if reloaded is not None:
            # The re-anchor target only has to be newer than *our* anchor and free of a later
            # gap; it may still predate the reconnect (review CR-7). Re-testing the reloaded
            # book is what stops the branch that clears a genuine re-anchor from also erasing a
            # session verdict -- permanently, on a ticker that is never resubscribed, because
            # the pre-reconnect snapshot satisfies `_CLEAN_SNAPSHOT_AFTER` forever.
            _mark_session_boundary(reloaded, ws_connect_at)
            return reloaded
    return out


def _book_at_unchecked(session, ticker: str, instant: datetime) -> BookState | None:
    """`book_at` without its F36 staleness gate: the anchor at or before `instant` plus every
    delta up to it, whatever the tape's age. Split out for `BookWalker`, which has to keep a
    book across an instant where it reads as stale so the next instant can still advance it
    rather than rebuilding from the anchor."""
    ws = session.execute(_NEWEST_WS_SNAPSHOT_AT, {"t": ticker, "instant": instant}).first()
    rest = session.execute(_NEWEST_REST_SNAPSHOT_AT, {"t": ticker, "instant": instant}).first()
    if ws is None and rest is None:
        return None
    use_ws = ws is not None and (rest is None or ws.ts >= rest.fetched_at)
    if use_ws:
        book = BookState.from_ws_raw(ticker, ws.raw, ws.sid, ws.seq, ws.ts, ws.id)
        lower = ws.ts - DELTA_LOOKBACK
    else:
        # Same bookkeeping as `load_book`'s REST branch: sid 0, and a gap-check id dated to the
        # tape position the ladder was fetched at rather than to the anchor id it does not have.
        book = BookState.from_levels(ticker, rest.yes_bids, rest.no_bids, sid=0, seq=0,
                                     as_of=rest.fetched_at, source="rest", anchor_id=0)
        book.gap_check_id = _max_event_id_at(session, rest.fetched_at)
        lower = rest.fetched_at
    rows = session.execute(_DELTAS_BY_TS, {"t": ticker, "cursor": book.anchor_id,
                                           "lower": lower, "upper": instant}).all()
    _apply_rows(book, rows, check_seq=use_ws)
    return book


def _too_stale(session, book: BookState, instant: datetime) -> bool:
    """F36: the freshest thing known about this ticker at `instant` is older than
    `BOOK_MAX_AGE`, so there is no book -- a recorder outage must not read as a quiet market."""
    newest = session.execute(
        _NEWEST_EVENT_TS, {"t": book.ticker, "instant": instant}).scalar()
    # A REST-anchored ticker can have no tape rows of its own at all, and its ladder's own
    # fetch time is then the only freshness there is to check.
    freshest = max(t for t in (newest, book.as_of) if t is not None)
    return freshest < instant - BOOK_MAX_AGE


def book_at(session, ticker: str, instant: datetime) -> BookState | None:
    """The book as of a past instant: `load_book`'s rules, bounded there.

    Anchors on the fresher of the newest WebSocket snapshot and the newest REST ladder *at or
    before* `instant`, exactly as `load_book` chooses between them, then folds in the live
    delta set (`id > anchor and ts >= anchor ts - DELTA_LOOKBACK`) up to `instant`, ordered by
    `(ts, id)` because the question is what the venue's book looked like at a moment rather
    than what our recorder had ingested by then.

    Returns None when nothing anchors it, or when the freshest thing it knows about at the
    instant is more than `BOOK_MAX_AGE` old -- a recorder outage must not read as a quiet
    market (F36).

    Dirtiness here comes only from a seq break among the deltas actually replayed. The live
    path's `gap.id > anchor.id` test is deliberately not applied: every gap after the instant
    also has a higher id, so hours later it would dirty every historical book on that sid and
    take the markouts with it. A caller that wants the live loop's own gap verdict -- the
    replay executor -- uses `load_book_at`, which adds it bounded at the instant.
    """
    book = _book_at_unchecked(session, ticker, instant)
    if book is None or _too_stale(session, book, instant):
        return None
    return book


def load_book_at(session, ticker: str, instant: datetime) -> BookState | None:
    """`load_book` bounded at a past instant -- the book a replay executor starts a ticker on.

    `book_at` plus the live loop's own gap verdict, bounded: a gap the live loop had already
    seen by `instant` dirtied its book, so it has to dirty the replayed one too, while a gap
    taped afterwards is not information the replayed instant had.
    """
    book = book_at(session, ticker, instant)
    if book is not None and _gapped_at(session, book, instant):
        book.mark_dirty("gap")
    return book


def advance_book_at(session, book: BookState, instant: datetime,
                    ws_connect_at: datetime | None = None) -> BookState:
    """`advance_book` bounded at a past instant. The argument is never mutated.

    A replay executor steps a whole day 15 s at a time. Rebuilding each book from its anchor
    at every step is quadratic in the day's tape -- snapshots arrive only on a (re)subscribe,
    so step *k* would re-apply every delta since the ticker's last one -- and would not finish
    inside the executor's own statement timeout. This is the incremental path the live loop
    already takes, with the instant as its upper bound, and it re-anchors on the same rule:
    only a dirty book with a clean snapshot after it, both at or before the instant.

    It carries `advance_book`'s two other verdicts bounded the same way: the dropped-delta probe
    of §0.2 inside the instant, and `ws_connect_at`, the caller's once-per-step read of
    `newest_ws_connect(session, instant)`. None skips the session test.
    """
    out = book.copy()
    lower = out.as_of - DELTA_LOOKBACK
    cursor = out.last_event_id
    rows = session.execute(_DELTAS_BY_TS, {"t": out.ticker, "cursor": cursor,
                                           "lower": lower, "upper": instant}).all()
    _apply_rows(out, rows, check_seq=out.source == "ws")
    if _gapped_at(session, out, instant):
        out.mark_dirty("gap")
    if _dropped_delta_at(session, out.ticker, cursor, lower, instant):
        out.mark_dirty("event_age")
    _mark_session_boundary(out, ws_connect_at)
    if out.dirty and session.execute(
            _CLEAN_SNAPSHOT_AFTER_AT,
            {"t": out.ticker, "anchor_id": out.gap_check_id, "instant": instant}).first() is not None:
        reloaded = load_book_at(session, out.ticker, instant)
        if reloaded is not None:
            _mark_session_boundary(reloaded, ws_connect_at)
            return reloaded
    return out


class BookWalker:
    """One ticker's book walked forward through a sequence of past instants (final review I7).

    `book_at` re-anchors on the ticker's newest snapshot and replays every delta since it, on
    every call. A caller with many instants on one ticker pays that replay once per instant:
    the markouts stage asks for up to four anchors x six horizons per order, and on a ticker
    that has not been re-subscribed for hours each of those is hours of deltas, under the
    settler's 900 s batch timeout. A markouts stage that keeps running out of budget shows up
    as gate criteria 4 and 5 reading `insufficient` rather than as an error, which is why this
    is worth removing rather than measuring.

    The answer is the same book `book_at` would build. The anchor is reloaded only when a new
    WebSocket snapshot or REST ladder landed between the last instant and this one -- the only
    thing that can change which anchor `book_at` picks -- and otherwise the cached book is
    advanced with `advance_book_at`, whose delta scan starts at the book's own cursor instead
    of at the anchor. The F36 staleness gate is applied at each instant exactly as `book_at`
    applies it, and a stale instant does not discard the cached book, so a quiet stretch
    followed by fresh deltas still advances rather than rebuilding.

    Instants are expected in ascending order (`compute_markouts` sorts each order's horizons
    before walking them); one that goes backwards is answered by a full rebuild rather than
    incorrectly.

    One deliberate difference from `book_at`: `advance_book_at` carries the live loop's own
    gap verdict, so a walked book can be `dirty` where a rebuilt one is not. Nothing that reads
    a walked book consults `dirty` -- `mid()`, `as_of` and therefore `book_age_s` are identical
    either way -- and erring dirty is the safe direction.
    """

    def __init__(self, session, ticker: str) -> None:
        self.session = session
        self.ticker = ticker
        self._book: BookState | None = None
        self._at: datetime | None = None

    def at(self, instant: datetime) -> BookState | None:
        """The book at `instant`, or None when nothing anchors it or it reads as stale."""
        book = self._walk(instant)
        if book is None or _too_stale(self.session, book, instant):
            return None
        return book

    def _walk(self, instant: datetime) -> BookState | None:
        cached, cached_at = self._book, self._at
        if (cached is not None and cached_at is not None and instant >= cached_at
                and not self._new_anchor(cached_at, instant)):
            book = advance_book_at(self.session, cached, instant)
        else:
            book = _book_at_unchecked(self.session, self.ticker, instant)
        self._book, self._at = book, instant
        return book

    def _new_anchor(self, lower: datetime, upper: datetime) -> bool:
        if lower == upper:
            return False
        params = {"t": self.ticker, "lower": lower, "upper": upper}
        return (self.session.execute(_WS_SNAPSHOT_BETWEEN, params).first() is not None
                or self.session.execute(_REST_SNAPSHOT_BETWEEN, params).first() is not None)


def book_age_s(book: BookState, now: datetime) -> int:
    """Whole seconds between the book's newest applied row and `now`, never negative.

    A delta carries the venue's clock, which can sit ahead of ours, so `as_of` can be in the
    future. Callers compare this against age ceilings (`exec_book_max_age_s`) and store it on
    an order, so a negative age would read as an impossibly fresh book; the floor is 0.
    """
    return max(0, int((now - book.as_of).total_seconds()))
