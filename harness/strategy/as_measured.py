"""D12: the trailing 14-day `as_measured` bucket table, read on every pricing tick.

This module owns no settlement stage and imports nothing from `harness.settlement` -- it reads
the `markouts` table `harness.settlement.markouts` writes, but purely as a query, so importing
`harness.strategy.pipeline` (and, transitively, the recorder tick) can never register a stage
or change stage order (fix round 1, Important 5: the shipped version imported
`harness.settlement.markouts` for this function, and `markouts.py`'s module body calls
`register_stage` at import time -- so importing the pricing pipeline silently promoted
`markouts` ahead of `settle` in the settlement job's registration order, and `test_settle.py`'s
"`load_stages()` is the first thing to import these modules" comment stopped being true).

`as_measured_table` is also defensive in a way a settlement stage does not have to be: a
pricing tick has never depended on settlement output before, so a broken query here logs a
warning and returns an empty table rather than failing the tick. The query itself runs inside
its own savepoint so a failure cannot roll back whatever fair-value or gap-snapshot work the
tick has already done on the same session.
"""

import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

FOUR = Decimal("0.0001")
AS_MEASURED_WINDOW = timedelta(days=14)
AS_MEASURED_MIN_ROWS = 50

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


def price_bucket(p: Decimal) -> int:
    """The 5c price bucket a probability falls in, in whole cents (`gaps.py`'s convention).
    Fix round 1, Minor 3: the one copy of this formula -- `as_measured_table` keys its map
    with it and `harness.strategy.run._as_measured_for` looks a signal's own price up with it,
    so the two sides of one dictionary cannot drift into different bucket boundaries."""
    return (int(p * 100) // 5) * 5


def as_measured_table(session: Session, now: datetime) -> dict[tuple[str, int, str], Decimal]:
    """D12: the trailing 14-day realised adverse-selection estimate, by (sport, 5c price
    bucket, side), from `nw_fill` 30-minute markouts on rows where the fair actually moved
    since placement (`fair_changed`). A bucket under `AS_MEASURED_MIN_ROWS` is left out of the
    map entirely -- `run_strategy` records `None` for it -- rather than reported on too few
    fills to mean anything (spec F56).

    Never raises: the read runs in its own savepoint, and any failure -- a missing table on an
    old branch, a lock, a type surprise -- is logged and answered with `{}`, the same "nothing
    known yet" a normal empty result would produce.
    """
    since = now - AS_MEASURED_WINDOW
    try:
        with session.begin_nested():
            rows = session.execute(_AS_MEASURED_ROWS, {"since": since}).all()
    except Exception:  # noqa: BLE001 - a pricing tick must not fail because of this read
        log.exception("as_measured_table failed; the pricing tick proceeds with an empty table")
        return {}

    buckets: dict[tuple[str, int, str], list[Decimal]] = {}
    for row in rows:
        key = (row.sport, price_bucket(Decimal(row.p_used)), row.side)
        value = Decimal(row.fair_p) - Decimal(row.p_used) - Decimal(row.fee_per_contract)
        buckets.setdefault(key, []).append(value)
    return {
        key: (sum(values) / Decimal(len(values))).quantize(FOUR, rounding=ROUND_HALF_UP)
        for key, values in buckets.items() if len(values) >= AS_MEASURED_MIN_ROWS
    }
