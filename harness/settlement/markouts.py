"""Markouts: how an order's price aged against the sharp fair and the venue's own mid, from
placement and from each fill set, plus the trailing `as_measured` bucket table that feeds
(but never drives) the strategy's adverse-selection estimate.

`markout_at` is the one pure decision (ruling 1): given the fairs and quotes a caller has
already loaded -- and, for a NO-side order, already converted through `side_p` -- it picks the
newest fair whose own book time is at or before the horizon, and a venue mid by the
quote-then-book-then-none chain (F36: `book_mid_fn` already answers None for a book stale by
`BOOK_MAX_AGE`). `compute_markouts` is the settlement stage: for every non-replay order it
walks the anchors that apply (`place` always; `fill`/`nw_fill`/`cross_fill` once their fill
exists) and writes whichever `HORIZONS` rows are both missing and already due (`horizon_ts <=
now`), so an order gains rows over the two hours after each anchor and a re-run inserts
nothing new.

`as_measured_table` is the D12 bucket mean: trailing 14 days of `nw_fill` 30 m markouts with
`fair_changed`, by (sport, 5c price bucket, side), NULL below 50 rows. `run_strategy` records
it on `SignalRow.as_measured` and never reads it back into a label or a price (F56).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Markout
from harness.execution.book import book_at, side_p
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

FOUR = Decimal("0.0001")

#: Seconds after each anchor a markout row is due. `0m` (the anchor instant itself) is only
#: ever written for the `fill`/`nw_fill` anchors, so "fair at fill" is a stored row -- `place`
#: and `cross_fill` skip it (spec F15).
HORIZONS = {"0m": 0, "1m": 60, "5m": 300, "30m": 1800, "120m": 7200}
ZERO_M_ANCHORS = ("fill", "nw_fill")

#: How close a `venue_quotes` row must be to the horizon instant to be used over the WS book.
QUOTE_WINDOW = timedelta(seconds=60)

#: The fee an order's markout is scored net of, at a fixed notional (spec F42's "the
#: 100-contract reference is exact at every size") rather than the order's own `contracts`.
FEE_CONTRACTS = 100

AS_MEASURED_WINDOW = timedelta(days=14)
AS_MEASURED_MIN_ROWS = 50


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
    book_mid_fn: Callable[[datetime], Decimal | None],
) -> MarkoutPoint:
    """One markout at one horizon instant, pure and unit-tested without a database (ruling 1).

    The fair is the newest one whose own timestamp (`newest_book_ts`, fallback `created_at`)
    is at or before `horizon_ts` -- `None` when nothing qualifies. The venue mid is the last
    quote within `QUOTE_WINDOW` of `horizon_ts` (`source = quote`); failing that,
    `book_mid_fn(horizon_ts)` (`source = ws_book`, already `None` when the book is stale --
    F36); failing that, `source = none`. `anchor_ts` is part of the interface for symmetry
    with the anchors `compute_markouts` builds around, but nothing here uses it: every
    candidate is already scoped to this order's own tape by the caller.
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

    near = [(ts, p) for ts, p in quotes if abs((ts - horizon_ts).total_seconds()) <= QUOTE_WINDOW.total_seconds()]
    if near:
        ts, p = max(near, key=lambda item: item[0])
        venue_mid, mid_age_s, source = p, int(abs((horizon_ts - ts).total_seconds())), "quote"
    else:
        book_mid = book_mid_fn(horizon_ts)
        if book_mid is not None:
            venue_mid, mid_age_s, source = book_mid, 0, "ws_book"
        else:
            venue_mid, mid_age_s, source = None, None, "none"

    return MarkoutPoint(fair_p=fair_p, fair_row_id=fair_row_id, fair_book_ts=fair_book_ts,
                        fair_age_s=fair_age_s, venue_mid=venue_mid, mid_age_s=mid_age_s,
                        source=source)


# --- compute_markouts: the stage ----------------------------------------------------------

#: A non-replay order is a candidate as long as some anchor it could have could still be
#: missing its furthest (`120m`) row -- the same "per missing thing, not per any row exists"
#: shape as Task 8's order_clv candidacy (fix round 1, C1), so an order whose fill lands well
#: after placement is not locked out of its own `fill`/`nw_fill`/`cross_fill` rows forever.
_CANDIDATE_ORDERS = text("""
    select o.id as order_id, o.side, o.prob, o.placed_at, o.game_id, o.fair_row_id_at_place,
           o.crossed, o.nw_crossed, o.ticker, o.venue_market_id,
           m.market_type, m.side_team_id, m.side as market_side, m.threshold
    from orders o
    join venue_markets m on m.id = o.venue_market_id
    where o.replay = false
      and (
        not exists (
          select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'place' and mk.horizon = '120m'
        )
        or exists (
          select 1 from fills f where f.order_id = o.id and f.fill_method = 'queue_model'
          and not exists (
            select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'fill' and mk.horizon = '120m'
          )
        )
        or exists (
          select 1 from fills f where f.order_id = o.id and f.fill_method in ('queue_model', 'no_watcher')
          and not exists (
            select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'nw_fill' and mk.horizon = '120m'
          )
        )
        or (
          (o.crossed or o.nw_crossed)
          and exists (select 1 from fills f where f.order_id = o.id and f.fill_method = 'snapshot_cross')
          and not exists (
            select 1 from markouts mk where mk.order_id = o.id and mk.anchor = 'cross_fill' and mk.horizon = '120m'
          )
        )
      )
    order by o.id
""")

_FILLS_FOR_ORDER = text("""
    select fill_method, filled_at, prob from fills where order_id = :order_id order by filled_at, id
""")

_EXISTING_MARKOUTS = text("select anchor, horizon from markouts where order_id = :order_id")

_FAIR_ROWS_FOR_SHAPE = text("""
    select id, created_at, newest_book_ts, fair_p from fair_values
    where game_id = :game_id and market_type = :market_type
      and coalesce(outcome_team_id, -1) = coalesce(:team_id, -1)
      and coalesce(outcome_side, '') = coalesce(:side, '')
      and coalesce(threshold, 0) = coalesce(:threshold, 0)
      and fair_source = 'direct'
""")

_QUOTES_NEAR = text("""
    select fetched_at, yes_bid, yes_ask from venue_quotes
    where venue_market_id = :venue_market_id and fetched_at between :lower and :upper
      and yes_bid is not null and yes_ask is not null
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
    lower, upper = horizon_ts - QUOTE_WINDOW, horizon_ts + QUOTE_WINDOW
    rows = session.execute(
        _QUOTES_NEAR, {"venue_market_id": venue_market_id, "lower": lower, "upper": upper}).all()
    out = []
    for r in rows:
        mid = ((r.yes_bid + r.yes_ask) / 2).quantize(FOUR, rounding=ROUND_HALF_UP)
        out.append((r.fetched_at, side_p(mid, side)))
    return out


def _book_mid_fn(session: Session, ticker: str, side: str) -> Callable[[datetime], Decimal | None]:
    def fn(instant: datetime) -> Decimal | None:
        book = book_at(session, ticker, instant)
        if book is None:
            return None
        mid = book.mid()
        return None if mid is None else side_p(mid, side)
    return fn


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

    inserted = 0
    for anchor, anchor_ts, p_used in _anchors_for(row, fills):
        for horizon in _horizons_for(anchor):
            if (anchor, horizon) in existing:
                continue
            horizon_ts = anchor_ts + timedelta(seconds=HORIZONS[horizon])
            if horizon_ts > now:
                continue
            quotes = _quotes_near(session, row.venue_market_id, horizon_ts, side)
            point = markout_at(anchor_ts, horizon_ts, fairs, quotes, book_fn)
            fee = fee_per_contract(KALSHI_FOOTBALL, "maker", p_used, FEE_CONTRACTS)
            fair_changed = point.fair_row_id != row.fair_row_id_at_place
            inserted += _insert_markout(
                session, order_id=row.order_id, anchor=anchor, horizon=horizon,
                at_ts=anchor_ts, horizon_ts=horizon_ts, p_used=p_used, fee_per_contract=fee,
                fair_p=point.fair_p, fair_row_id=point.fair_row_id,
                fair_book_ts=point.fair_book_ts, fair_changed=fair_changed,
                fair_age_s=point.fair_age_s, venue_mid=point.venue_mid,
                mid_age_s=point.mid_age_s, source=point.source)
    return inserted


def compute_markouts(session: Session, now: datetime, budget: Budget) -> int:
    """Every markout row that is both missing and due (`horizon_ts <= now`) for every
    non-replay order's current anchors. One savepoint per order, mirroring the other
    settlement stages: one order's pricing history raising must not cost the orders around it
    their own rows."""
    ctx = current_ctx()
    inserted = 0
    rows = session.execute(_CANDIDATE_ORDERS).all()
    fair_cache: dict[tuple, list] = {}
    for seen, row in enumerate(rows):
        if not budget.ok():
            log.info("compute_markouts budget spent with %d orders left", len(rows) - seen)
            break
        try:
            with session.begin_nested():
                n = _process_order(session, row, now, fair_cache)
            session.commit()
            inserted += n
        except Exception as exc:  # noqa: BLE001 - one order must not cost the pass
            session.rollback()
            log.exception("compute_markouts failed for order_id=%s", row.order_id)
            ctx["errors"].append({"compute_markouts": row.order_id,
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
    return inserted


def markouts_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    n = compute_markouts(session, now, budget)
    return StageResult("markouts", {"markouts": n}, not budget.ok(), None)


register_stage("markouts", markouts_stage)


# --- as_measured_table ---------------------------------------------------------------------

_AS_MEASURED_ROWS = text("""
    select o.sport, o.side, m.p_used, m.fair_p, m.fee_per_contract
    from markouts m
    join orders o on o.id = m.order_id
    where m.anchor = 'nw_fill' and m.horizon = '30m' and m.fair_changed = true
      and m.at_ts >= :since
      and o.replay = false
      and o.sport is not null and m.p_used is not null and m.fair_p is not null
      and m.fee_per_contract is not null
""")


def _price_bucket(p: Decimal) -> int:
    """The 5c price bucket a probability falls in, in whole cents (`gaps.py`'s convention)."""
    return (int(p * 100) // 5) * 5


def as_measured_table(session: Session, now: datetime) -> dict[tuple[str, int, str], Decimal]:
    """D12: the trailing 14-day realised adverse-selection estimate, by (sport, 5c price
    bucket, side), from `nw_fill` 30-minute markouts on rows where the fair actually moved
    since placement (`fair_changed`). A bucket under `AS_MEASURED_MIN_ROWS` is left out of the
    map entirely -- `run_strategy` records `None` for it -- rather than reported on too few
    fills to mean anything (spec F56)."""
    since = now - AS_MEASURED_WINDOW
    buckets: dict[tuple[str, int, str], list[Decimal]] = {}
    for row in session.execute(_AS_MEASURED_ROWS, {"since": since}).all():
        key = (row.sport, _price_bucket(Decimal(row.p_used)), row.side)
        value = Decimal(row.fair_p) - Decimal(row.p_used) - Decimal(row.fee_per_contract)
        buckets.setdefault(key, []).append(value)
    return {
        key: (sum(values) / Decimal(len(values))).quantize(FOUR, rounding=ROUND_HALF_UP)
        for key, values in buckets.items() if len(values) >= AS_MEASURED_MIN_ROWS
    }
