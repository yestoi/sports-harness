"""Markouts: how an order's price aged against the sharp fair and the venue's own mid, from
placement and from each fill set.

`markout_at` is the one pure decision (ruling 1): given the fairs and quotes a caller has
already loaded -- and, for a NO-side order, already converted through `side_p` -- it picks the
newest fair whose own book time is at or before the horizon, and a venue mid by the
quote-then-book-then-none chain (F36: `book_mid_fn` already answers None for a book stale by
`BOOK_MAX_AGE`). `compute_markouts` is the settlement stage: for every non-replay order it
walks the anchors that apply (`place` always; `fill`/`nw_fill`/`cross_fill` once their fill
exists) and writes whichever `HORIZONS` rows -- plus `close` (kickoff - 5 min) once the game's
kickoff is known -- are both missing and already due (`horizon_ts <= now`), so an order gains
rows over the two hours after each anchor (and, separately, at kickoff) and a re-run inserts
nothing new.

The D12 `as_measured` bucket table that feeds (but never drives) the strategy's
adverse-selection estimate lives in `harness/strategy/as_measured.py`, not here: it is read on
every pricing tick, and this module owning it would have made importing the strategy's pricing
path register a settlement stage as a side effect (fix round 1, Important 5).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import JobState, Markout
from harness.execution.book import BookWalker, book_age_s, side_p
from harness.pricing.fees import fee_model_for, fee_per_contract
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

#: Fix 47: the last successfully processed order id, so a budget-exhausted run does not
#: always restart the walk at the lowest id. The same `job_state` table/shape `order_clv`'s
#: `gap_outcomes_watermark` uses -- a resumable integer cursor is exactly what that table is
#: for (`harness.db.models.JobState`), so this is a new row, not a new table.
MARKOUTS_CURSOR_KEY = "markouts.cursor"

FOUR = Decimal("0.0001")

#: Seconds after each anchor a markout row is due. `0m` (the anchor instant itself) is only
#: ever written for the `fill`/`nw_fill` anchors, so "fair at fill" is a stored row -- `place`
#: and `cross_fill` skip it (spec F15). `HORIZONS` itself is pinned verbatim to the brief
#: (ruling R1); `close` (fix round 1, I1) is not an offset from the anchor at all -- it is
#: `kickoff_utc - 5 min`, the same instant for every anchor on one order -- so it is handled
#: as its own case throughout rather than folded into this dict.
HORIZONS = {"0m": 0, "1m": 60, "5m": 300, "30m": 1800, "120m": 7200}
ZERO_M_ANCHORS = ("fill", "nw_fill")
CLOSE_OFFSET = timedelta(minutes=5)

#: How close a `venue_quotes` row must be to the horizon instant to be used over the WS book.
#: One-sided (fix round 1, Important 3): a quote is only a candidate at or before the horizon,
#: never after it -- a markout is a statement about what was knowable at that instant, and a
#: quote from after it is exactly the movement a later horizon is supposed to measure.
QUOTE_WINDOW = timedelta(seconds=60)

#: The fee an order's markout is scored net of, at a fixed notional (spec F42's "the
#: 100-contract reference is exact at every size") rather than the order's own `contracts`.
FEE_CONTRACTS = 100


def _horizons_for(anchor: str) -> tuple[str, ...]:
    if anchor in ZERO_M_ANCHORS:
        return tuple(HORIZONS)
    return tuple(h for h in HORIZONS if h != "0m")


# --- the pure decision -------------------------------------------------------------------


@dataclass(frozen=True)
class FairPoint:
    """One `fair_values` row, already in whatever probability space the caller wants scored --
    `compute_markouts` passes it through `side_p` before building this, so `markout_at` itself
    never has to know which side an order is on."""

    row_id: int
    created_at: datetime
    newest_book_ts: datetime | None
    p: Decimal


@dataclass(frozen=True)
class MarkoutPoint:
    fair_p: Decimal | None
    fair_row_id: int | None
    fair_book_ts: datetime | None
    fair_age_s: int | None
    venue_mid: Decimal | None
    mid_age_s: int | None
    source: str  # quote|ws_book|none


def _fair_ts(fair: FairPoint) -> datetime:
    """A fair's own timestamp for staleness purposes: `newest_book_ts` when the fair has one,
    else its own `created_at` (the same fallback `benchmark_at` uses for `source_ts`)."""
    return fair.newest_book_ts if fair.newest_book_ts is not None else fair.created_at


def markout_at(
    anchor_ts: datetime,
    horizon_ts: datetime,
    fairs: list[FairPoint],
    quotes: list[tuple[datetime, Decimal]],
    book_mid_fn: Callable[[datetime], tuple[Decimal, int] | None],
) -> MarkoutPoint:
    """One markout at one horizon instant, pure and unit-tested without a database (ruling 1).

    The fair is the newest one whose own timestamp (`newest_book_ts`, fallback `created_at`)
    is at or before `horizon_ts` -- `None` when nothing qualifies; a tie between two fairs with
    the same effective timestamp is broken by whichever the caller placed earlier in `fairs`
    (fix round 1, Important 6: `compute_markouts` orders its query `newest_book_ts desc,
    created_at desc, id desc`, so `max`'s "first maximal element" rule resolves ties
    deterministically instead of on database row order).

    The venue mid is the last quote at or before `horizon_ts` and no more than `QUOTE_WINDOW`
    behind it (`source = quote`; fix round 1, Important 3 -- no lookahead, so a later quote can
    never leak into an earlier markout); failing that, `book_mid_fn(horizon_ts)` (`source =
    ws_book`, a `(mid, age_s)` pair, already `None` when the book is stale -- F36; fix round 1,
    Minor 2: the age is the book's own age at the horizon, never a stand-in `0`); failing that,
    `source = none`. `anchor_ts` is part of the interface for symmetry with the anchors
    `compute_markouts` builds around, but nothing here uses it: every candidate is already
    scoped to this order's own tape by the caller.
    """
    del anchor_ts

    candidates = [f for f in fairs if _fair_ts(f) <= horizon_ts]
    if candidates:
        chosen = max(candidates, key=_fair_ts)
        fair_p = chosen.p
        fair_row_id = chosen.row_id
        fair_book_ts = _fair_ts(chosen)
        fair_age_s = int((horizon_ts - fair_book_ts).total_seconds())
    else:
        fair_p = fair_row_id = fair_book_ts = fair_age_s = None

    near = [(ts, p) for ts, p in quotes if ts <= horizon_ts and horizon_ts - ts <= QUOTE_WINDOW]
    if near:
        ts, p = max(near, key=lambda item: item[0])
        venue_mid, mid_age_s, source = p, int((horizon_ts - ts).total_seconds()), "quote"
    else:
        book_result = book_mid_fn(horizon_ts)
        if book_result is not None:
            book_mid, age_s = book_result
            venue_mid, mid_age_s, source = book_mid, age_s, "ws_book"
        else:
            venue_mid, mid_age_s, source = None, None, "none"

    return MarkoutPoint(fair_p=fair_p, fair_row_id=fair_row_id, fair_book_ts=fair_book_ts,
                        fair_age_s=fair_age_s, venue_mid=venue_mid, mid_age_s=mid_age_s,
                        source=source)


# --- compute_markouts: the stage ----------------------------------------------------------

#: A non-replay order is a candidate as long as some anchor it could have could still be
#: missing its furthest row -- the same "per missing thing, not per any row exists" shape as
#: Task 8's order_clv candidacy (fix round 1, C1), so an order whose fill lands well after
#: placement is not locked out of its own `fill`/`nw_fill`/`cross_fill` rows forever. "Furthest"
#: is `120m` and, once the game's kickoff is known, `close` too (fix round 1, I1) -- `close`'s
#: instant does not track the anchor, so it can fall either before or after `120m` and both
#: must be checked for an anchor to read as done. An order whose game is unmatched
#: (`kickoff_utc is null`) can never get a `close` row, so that half of the check is skipped
#: for it rather than leaving it a permanent candidate.
#:
#: Fix 47: each branch also carries its own "is anything about it actually due yet" gate, so
#: the walk is not spent on orders that would write nothing at all (a stage's own budget was
#: being spent on the query cost and the fair/fill/quote lookups of orders that could not yet
#: produce a row). `close`'s gate is exactly the brief's `kickoff_utc - :close_offset <= :now`
#: -- unambiguous, since `close` is one instant, not a family of offsets. The `120m`-named
#: branches gate on their anchor's *earliest* horizon instead of literally 120 minutes: `place`
#: and `cross_fill` never get a `0m` row (`ZERO_M_ANCHORS`), so their earliest is `1m` (60 s);
#: `fill`/`nw_fill` do get `0m`, so theirs is due immediately (`filled_at <= :now`, effectively
#: a no-op gate, kept for symmetry). Gating a branch on its own *widest* horizon (120 minutes)
#: instead would silently exclude an order whose nearer horizons (`1m`/`5m`/`30m`) are already
#: due but whose `120m` is not for up to two hours -- exactly the case
#: `test_horizons_beyond_now_are_not_written_yet` pins, and this filter must not delay it.
#: The Python `horizon_ts > now: continue` in `_process_order` stays as the second line of
#: defence: it is what actually decides, per horizon, whether a row is written this call.
_CANDIDATE_ORDERS = text("""
    select o.id as order_id, o.side, o.prob, o.placed_at, o.game_id, o.fair_row_id_at_place,
           o.crossed, o.nw_crossed, o.ticker, o.venue_market_id,
           m.market_type, m.side_team_id, m.side as market_side, m.threshold,
           m.fee_type, m.fee_multiplier, g.kickoff_utc
    from orders o
    join venue_markets m on m.id = o.venue_market_id
    left join games g on g.id = o.game_id
    where o.replay = false
      and (
        (
          o.placed_at + interval '60 seconds' <= :now
          and not exists (
            select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'place' and mk.horizon = '120m'
          )
        )
        or (
          g.kickoff_utc is not null and g.kickoff_utc - :close_offset <= :now
          and not exists (
            select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'place' and mk.horizon = 'close'
          )
        )
        or exists (
          select 1 from fills f where f.order_id = o.id and f.fill_method = 'queue_model'
          and (
            (
              f.filled_at <= :now
              and not exists (
                select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'fill' and mk.horizon = '120m'
              )
            )
            or (
              g.kickoff_utc is not null and g.kickoff_utc - :close_offset <= :now
              and not exists (
                select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'fill' and mk.horizon = 'close'
              )
            )
          )
        )
        or exists (
          select 1 from fills f where f.order_id = o.id and f.fill_method in ('queue_model', 'no_watcher')
          and (
            (
              f.filled_at <= :now
              and not exists (
                select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'nw_fill' and mk.horizon = '120m'
              )
            )
            or (
              g.kickoff_utc is not null and g.kickoff_utc - :close_offset <= :now
              and not exists (
                select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'nw_fill' and mk.horizon = 'close'
              )
            )
          )
        )
        or (
          (o.crossed or o.nw_crossed)
          and exists (
            select 1 from fills f where f.order_id = o.id and f.fill_method = 'snapshot_cross'
            and (
              (
                f.filled_at + interval '60 seconds' <= :now
                and not exists (
                  select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'cross_fill' and mk.horizon = '120m'
                )
              )
              or (
                g.kickoff_utc is not null and g.kickoff_utc - :close_offset <= :now
                and not exists (
                  select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'cross_fill' and mk.horizon = 'close'
                )
              )
            )
          )
        )
      )
    order by o.id
""")

_FILLS_FOR_ORDER = text("""
    select fill_method, filled_at, prob from fills where order_id = :order_id order by filled_at, id
""")

_EXISTING_MARKOUTS = text("select anchor, horizon from markouts where order_id = :order_id")

#: Fix round 1, Important 4: no `fair_source` filter. The shipped `= 'direct'` filter left
#: every derived-shape order (e.g. `sharp_plus_derived`, `sources_allowed: [direct, derived]`)
#: with an empty fair scan forever -- a direct fair is never computed for a shape once a
#: derived one has been (`fair.py`'s `remaining` loop), so `fair_row_id` stayed `None` while
#: `fair_row_id_at_place` did not, and `fair_changed` read `True` permanently. Any fair source
#: for the shape is a candidate now, exactly as the order itself was priced against whichever
#: source its own gap snapshot carried.
#:
#: Fix round 1, Important 6: `order by` makes the tie-break deterministic -- two fair rows
#: from consecutive pricing ticks commonly share a `newest_book_ts` when no book event arrived
#: between them, and without an explicit order the winner would depend on Postgres row order.
#: `newest_book_ts desc nulls last` puts a real book time ahead of a fallback-to-`created_at`
#: row; `created_at desc, id desc` breaks any further tie by recency, matching `markout_at`'s
#: `max(..., key=_fair_ts)`, which keeps the first-seen maximal element.
_FAIR_ROWS_FOR_SHAPE = text("""
    select id, created_at, newest_book_ts, fair_p from fair_values
    where game_id = :game_id and market_type = :market_type
      and coalesce(outcome_team_id, -1) = coalesce(:team_id, -1)
      and coalesce(outcome_side, '') = coalesce(:side, '')
      and coalesce(threshold, 0) = coalesce(:threshold, 0)
    order by newest_book_ts desc nulls last, created_at desc, id desc
""")

#: Fix round 1, Important 3: one-sided (`fetched_at between horizon - 60s and horizon`, never
#: past the horizon); `id desc` breaks a tie between two quotes sharing a `fetched_at` in
#: favour of whichever was recorded later, the same shape as the fair tie-break above.
_QUOTES_NEAR = text("""
    select fetched_at, yes_bid, yes_ask, id from venue_quotes
    where venue_market_id = :venue_market_id and fetched_at between :lower and :upper
      and yes_bid is not null and yes_ask is not null
    order by fetched_at desc, id desc
""")


def _insert_markout(session: Session, **kwargs) -> int:
    stmt = insert(Markout).values(**kwargs).on_conflict_do_nothing().returning(Markout.order_id)
    return len(session.execute(stmt).fetchall())


def _anchors_for(row, fills: list) -> list[tuple[str, datetime, Decimal]]:
    """`(anchor, anchor_ts, p_used)` for every anchor this order currently has -- `place`
    always, the fill anchors once their first fill (by `filled_at`, then `id`, since `fills`
    arrives pre-ordered) exists."""
    anchors: list[tuple[str, datetime, Decimal]] = [("place", row.placed_at, Decimal(row.prob))]

    queue_model = next((f for f in fills if f.fill_method == "queue_model"), None)
    if queue_model is not None:
        anchors.append(("fill", queue_model.filled_at, Decimal(queue_model.prob)))

    nw_fill = next((f for f in fills if f.fill_method in ("queue_model", "no_watcher")), None)
    if nw_fill is not None:
        anchors.append(("nw_fill", nw_fill.filled_at, Decimal(nw_fill.prob)))

    if row.crossed or row.nw_crossed:
        cross = next((f for f in fills if f.fill_method == "snapshot_cross"), None)
        if cross is not None:
            anchors.append(("cross_fill", cross.filled_at, Decimal(cross.prob)))

    return anchors


def _quotes_near(session: Session, venue_market_id: int, horizon_ts: datetime,
                 side: str) -> list[tuple[datetime, Decimal]]:
    lower = horizon_ts - QUOTE_WINDOW
    rows = session.execute(
        _QUOTES_NEAR, {"venue_market_id": venue_market_id, "lower": lower, "upper": horizon_ts}).all()
    out = []
    for r in rows:
        mid = ((r.yes_bid + r.yes_ask) / 2).quantize(FOUR, rounding=ROUND_HALF_UP)
        out.append((r.fetched_at, side_p(mid, side)))
    return out


def _book_mid_fn(session: Session, ticker: str, side: str) -> Callable[[datetime], tuple[Decimal, int] | None]:
    """Fix round 1, Minor 2: returns `(mid, age_s)`, the book's own age at `instant`
    (`book_age_s`, the same accessor the executor uses), rather than a bare mid a caller would
    otherwise have to stand in a false `0` for.

    Final review I7: one `BookWalker` per order, not a `book_at` rebuild per horizon. The
    walker answers the book `book_at` would build at each instant, re-anchoring only when a new
    snapshot landed between two of them and otherwise advancing the cached book from its own
    cursor. `_process_order` hands it instants in `ts` order for that reason.
    """
    walker = BookWalker(session, ticker)

    def fn(instant: datetime) -> tuple[Decimal, int] | None:
        book = walker.at(instant)
        if book is None:
            return None
        mid = book.mid()
        if mid is None:
            return None
        return side_p(mid, side), book_age_s(book, instant)
    return fn


def _fee_role(anchor: str) -> str:
    """Fix round 1, Important 7: `cross_fill` is a `snapshot_cross` fill -- by definition the
    print crossed our resting price, i.e. we took liquidity -- so it is scored at the taker
    rate; every other anchor (`place`, `fill`, `nw_fill`) is scored at the maker rate the paper
    executor always posts at."""
    return "taker" if anchor == "cross_fill" else "maker"


def _process_order(session: Session, row, now: datetime,
                   fair_cache: dict[tuple, list]) -> int:
    shape_key = (row.game_id, row.market_type, row.side_team_id, row.market_side, row.threshold)
    raw_fairs = fair_cache.get(shape_key)
    if raw_fairs is None:
        raw_fairs = session.execute(_FAIR_ROWS_FOR_SHAPE, {
            "game_id": row.game_id, "market_type": row.market_type, "team_id": row.side_team_id,
            "side": row.market_side, "threshold": row.threshold}).all()
        fair_cache[shape_key] = raw_fairs
    side = row.side
    fairs = [FairPoint(row_id=f.id, created_at=f.created_at, newest_book_ts=f.newest_book_ts,
                       p=side_p(f.fair_p, side))
            for f in raw_fairs]

    fills = session.execute(_FILLS_FOR_ORDER, {"order_id": row.order_id}).all()
    existing = {(r.anchor, r.horizon) for r in
               session.execute(_EXISTING_MARKOUTS, {"order_id": row.order_id}).all()}
    book_fn = _book_mid_fn(session, row.ticker, side)
    fee_model = fee_model_for(row.fee_type, row.fee_multiplier)
    close_ts = None if row.kickoff_utc is None else row.kickoff_utc - CLOSE_OFFSET

    # Every (anchor, horizon) this order still owes, collected across the anchors first and
    # then walked in `horizon_ts` order (final review I7): `book_fn` keeps one book per order
    # and advances it, so it has to be asked for instants that only move forward. The rows
    # themselves are independent of the order they are written in.
    due: list[tuple[datetime, str, str, datetime, Decimal]] = []
    for anchor, anchor_ts, p_used in _anchors_for(row, fills):
        horizons: list[tuple[str, datetime]] = [
            (h, anchor_ts + timedelta(seconds=HORIZONS[h])) for h in _horizons_for(anchor)
        ]
        if close_ts is not None:
            horizons.append(("close", close_ts))
        for horizon, horizon_ts in horizons:
            if (anchor, horizon) in existing:
                continue
            if horizon_ts > now:
                continue
            due.append((horizon_ts, anchor, horizon, anchor_ts, p_used))
    due.sort(key=lambda d: (d[0], d[1], d[2]))

    inserted = 0
    for horizon_ts, anchor, horizon, anchor_ts, p_used in due:
        quotes = _quotes_near(session, row.venue_market_id, horizon_ts, side)
        point = markout_at(anchor_ts, horizon_ts, fairs, quotes, book_fn)
        fee = fee_per_contract(fee_model, _fee_role(anchor), p_used, FEE_CONTRACTS)
        fair_changed = point.fair_row_id != row.fair_row_id_at_place
        inserted += _insert_markout(
            session, order_id=row.order_id, anchor=anchor, horizon=horizon,
            at_ts=anchor_ts, horizon_ts=horizon_ts, p_used=p_used, fee_per_contract=fee,
            fair_p=point.fair_p, fair_row_id=point.fair_row_id,
            fair_book_ts=point.fair_book_ts, fair_changed=fair_changed,
            fair_age_s=point.fair_age_s, venue_mid=point.venue_mid,
            mid_age_s=point.mid_age_s, source=point.source)
    return inserted


def _get_cursor(session: Session) -> int:
    row = session.get(JobState, MARKOUTS_CURSOR_KEY)
    return 0 if row is None or row.value is None else int(row.value)


def _set_cursor(session: Session, value: int, now: datetime) -> None:
    stmt = insert(JobState).values(key=MARKOUTS_CURSOR_KEY, value=value, updated_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value, "updated_at": now})
    session.execute(stmt)


def _rotate_from_cursor(rows: list, cursor: int) -> list:
    """Candidates ordered by id, starting just after `cursor` and wrapping around to the head
    once the tail is exhausted or empty (fix 47) -- so two consecutive budget-spent runs walk
    different halves of the backlog instead of the same low-id prefix every time."""
    if not rows:
        return rows
    split = next((i for i, r in enumerate(rows) if r.order_id > cursor), len(rows))
    return rows[split:] + rows[:split]


def compute_markouts(session: Session, now: datetime, budget: Budget) -> int:
    """Every markout row that is both missing and due (`horizon_ts <= now`) for every
    non-replay order's current anchors. One savepoint per order, mirroring the other
    settlement stages: one order's pricing history raising must not cost the orders around it
    their own rows.

    Fix 47: the walk starts just past `markouts.cursor` (the last order id successfully
    processed) rather than always at the lowest id, wrapping around to the head when the tail
    runs out -- so a budget that only ever admits part of the backlog still reaches every
    order over a few runs instead of re-walking the same prefix forever. The cursor advances
    inside each order's own commit (only on success) and resets to 0 once a run gets all the
    way through the candidates it fetched (a "full pass"), so the next run's rotation starts
    fresh from the head rather than drifting past a backlog that has since shrunk.
    """
    ctx = current_ctx()
    inserted = 0
    cursor = _get_cursor(session)
    rows = _rotate_from_cursor(
        session.execute(_CANDIDATE_ORDERS, {"now": now, "close_offset": CLOSE_OFFSET}).all(),
        cursor)
    fair_cache: dict[tuple, list] = {}
    full_pass = True
    for seen, row in enumerate(rows):
        if not budget.ok():
            log.info("compute_markouts budget spent with %d orders left", len(rows) - seen)
            full_pass = False
            break
        try:
            with session.begin_nested():
                n = _process_order(session, row, now, fair_cache)
            _set_cursor(session, row.order_id, now)
            session.commit()
            inserted += n
        except Exception as exc:  # noqa: BLE001 - one order must not cost the pass
            session.rollback()
            log.exception("compute_markouts failed for order_id=%s", row.order_id)
            ctx["errors"].append({"compute_markouts": row.order_id,
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
    if rows and full_pass:
        _set_cursor(session, 0, now)
        session.commit()
    return inserted


def markouts_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    n = compute_markouts(session, now, budget)
    return StageResult("markouts", {"markouts": n}, not budget.ok(), None)


register_stage("markouts", markouts_stage)
