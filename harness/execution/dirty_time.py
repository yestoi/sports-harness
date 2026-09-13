"""Elapsed dirty and unobserved seconds per order, derived rather than accrued.

The gate-read columns (`orders.dirty_seconds`, `dirty_minutes`) keep their nominal accrual --
`exec_period_s` per observation -- because `dirty_minutes` is integer division of those seconds
and the classification boundary is 60 accrued seconds, not one. Moving to elapsed accrual would
move that classification in both directions, which is a measurement change under R1 and goes to
the user as §0.13c. This module is the parallel elapsed mechanism (ruling I-4): it intersects
each order's two intervals with `market_dirty_intervals` and `market_observation_intervals` and
reports the result, so either answer to §0.13c is adoptable with no further code.

It is a parameterised query, never a view (ruling I-8): bounded to `id > :boundary_order_id`
with a `limit`, riding `orders_pkey` for the driving scan and `ix_mdi_market_started` /
`ix_moi_market_started` for the two lateral aggregates. Expected to return inside 2 s over the
~8,800-order table; a run that does not is abandoned under §4.4's rule rather than waited on.

Unobserved time is reported, never folded into clean time: absence of a dirty row means "not
observed", not "observed clean" (ruling IM-15).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class OrderDirtyTime:
    """One order's elapsed seconds, by interval and by cause."""

    order_id: int
    watched_dirty_s: int
    counterfactual_dirty_s: int
    to_first_fill_dirty_s: int
    unobserved_s: int
    by_cause: dict[str, int]


#: The intersection, in seconds, of `[lo, hi]` with each interval row, summed. `least(:now,
#: deadline)` clamps a still-open interval, so a stretch that began before an order's deadline
#: cannot go on accruing against it (review I-6). Written once and bound three times rather than
#: three near-identical statements.
_OVERLAP = """
coalesce((select sum(extract(epoch from (
             least(coalesce(i.ended_at, least(:now, {hi})), {hi})
             - greatest(i.started_at, {lo}))))
          from {table} i
          where i.venue_market_id = o.venue_market_id and i.replay = false
            and i.started_at <= {hi}
            and coalesce(i.ended_at, least(:now, {hi})) >= {lo}
            {extra}), 0)
"""

_QUERY = text(f"""
select o.id as order_id,
       greatest(0, {_OVERLAP.format(table='market_dirty_intervals',
                                    lo='o.placed_at',
                                    hi='least(coalesce(o.cancelled_at, o.expiry), o.expiry)',
                                    extra='')})::bigint as watched_dirty_s,
       greatest(0, {_OVERLAP.format(table='market_dirty_intervals',
                                    lo='o.placed_at', hi='o.expiry', extra='')})::bigint
           as counterfactual_dirty_s,
       greatest(0, {_OVERLAP.format(table='market_dirty_intervals',
                                    lo='o.placed_at',
                                    hi='coalesce((select min(f.filled_at) from fills f '
                                       'where f.order_id = o.id and f.replay = false), o.expiry)',
                                    extra='')})::bigint as to_first_fill_dirty_s,
       greatest(0, extract(epoch from (o.expiry - o.placed_at))
                   - {_OVERLAP.format(table='market_observation_intervals',
                                      lo='o.placed_at', hi='o.expiry', extra='')})::bigint
           as unobserved_s
from orders o
where o.replay = false and o.id > :boundary_order_id and o.expiry is not null
order by o.id
limit :limit
""")

#: The per-cause breakdown over the counterfactual interval, one row per (order, cause).
_BY_CAUSE = text("""
select o.id as order_id, i.cause,
       sum(extract(epoch from (
           least(coalesce(i.ended_at, least(:now, o.expiry)), o.expiry)
           - greatest(i.started_at, o.placed_at))))::bigint as seconds
from orders o
join market_dirty_intervals i
  on i.venue_market_id = o.venue_market_id and i.replay = false
 and i.started_at <= o.expiry
 and coalesce(i.ended_at, least(:now, o.expiry)) >= o.placed_at
where o.replay = false and o.id > :boundary_order_id and o.expiry is not null
  and o.id <= :max_order_id
group by 1, 2
""")


def order_dirty_time(session: Session, now: datetime, boundary_order_id: int,
                     limit: int = 1000) -> list[OrderDirtyTime]:
    """Elapsed dirty and unobserved seconds for the orders after `boundary_order_id`.

    Two bounded statements: the four interval aggregates, and the per-cause breakdown over the
    same id range. Both are `id >` plus a ceiling, so neither can walk the pre-6B history.

    `now` is the caller's instant, bound as `:now` and never read from the database (review
    IM-6). An open interval is clamped to `least(:now, deadline)`, so a function that called SQL
    `now()` would give a different answer every time it ran and would put wall time inside every
    test that asserts on a still-open row. The controller passes the instant it journals.
    """
    rows = session.execute(_QUERY, {"now": now, "boundary_order_id": boundary_order_id,
                                    "limit": limit}).all()
    if not rows:
        return []
    causes: dict[int, dict[str, int]] = {}
    for row in session.execute(_BY_CAUSE, {"now": now, "boundary_order_id": boundary_order_id,
                                           "max_order_id": rows[-1].order_id}).all():
        causes.setdefault(row.order_id, {})[row.cause] = int(row.seconds)
    return [OrderDirtyTime(order_id=row.order_id,
                           watched_dirty_s=int(row.watched_dirty_s),
                           counterfactual_dirty_s=int(row.counterfactual_dirty_s),
                           to_first_fill_dirty_s=int(row.to_first_fill_dirty_s),
                           unobserved_s=int(row.unobserved_s),
                           by_cause=causes.get(row.order_id, {}))
            for row in rows]
