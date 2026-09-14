"""`harness rescore`: what the repaired simulator says about the orders already on the record.

The unit is the **order**, the denominator is the orders in the range whose counterfactual has
finished (`nw_done = true`), and the result is a **partition** -- completed,
unverifiable-no-tape, unverifiable-read-cancelled -- with the right-censoring caveat attached.
Not a single ratio: two numbers from different denominators read side by side look like a trend
and are not one (ruling IM-4).

27/445 is not recomputed here and is not quoted beside these numbers. It is a pooled, key-level,
right-censored count produced by the defective simulator, and the key-level comparison waits for
6D's coverage contract. This sentence is the only place it appears.

Every row written is a retrospective estimate under a named correction set and a named cancel
policy. No `orders`, `fills` or `ledger` row is updated or deleted: a correction is new rows
beside the originals (§6.7, D8), which is what 6A preserved the originals for.

Two policy rows per order, `ahead` and `behind` (§0.7). `ahead` is the point estimate and the
verdict is read off it; `behind` is the other end of the band. Their pair is equal whenever
`cancels_ahead` is 0 -- the implication only (ruling I-10): an order that retired nothing is
insensitive to the convention, while an order that retired something may still agree by
accident, so the disagreement rate over the range is what is reported and no equality is
asserted in the other direction.

Read-mostly and abandonable. Each order's tape read is bounded by `store.DELTA_BATCH_LIMIT` and
runs under its own `statement_timeout`, a cancelled read writes `unverifiable` instead of
aborting the run, and the whole command is resumable on the primary key -- so the controller can
stop it the moment `exec.loop_ms` goes over 30 s and lose only the orders it had not reached
(§4.4, ruling I-11).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from harness.db.models import OrderRescore
from harness.execution import store
from harness.execution.dirty_time import order_dirty_time
from harness.execution.fills import AHEAD, BEHIND, PaperOrder, SimState, TapePrint, simulate_fills

log = logging.getLogger(__name__)

#: Per-order ceiling on the re-score's own reads. Lower than the executor's, because this runs
#: beside a live loop in the quiet window and the loop's step is what must not stall (§4.4).
RESCORE_STATEMENT_TIMEOUT_MS = 15_000
VERDICTS = ("validated", "corrected", "unverifiable")
#: `validated` means the repaired simulator reproduces the recorded fill within one contract,
#: the tolerance `harness/audit.py` uses, and **strictly** within it (see `_verdict`).
FILL_TOLERANCE = Decimal("1")
#: `correction_ids` is `varchar(64)`; a set that does not fit is refused rather than truncated,
#: because a truncated id string would silently label a row with the wrong correction set.
MAX_CORRECTION_IDS = 64


@dataclass(frozen=True)
class RescoreCounts:
    """The partition, over an order-level denominator. Never divided into a ratio."""

    completed: int = 0
    unverifiable_no_tape: int = 0
    unverifiable_read_cancelled: int = 0
    denominator: int = 0


#: The orders to re-score. `nw_done = true` is the selection, not a filter applied afterwards
#: (ruling I-16): a pending counterfactual has no final answer to compare against, and reading
#: one as if it had is what §3 row 12 forbids. Rides `orders_pkey` for the range (`ix_orders_nw`
#: is `(nw_done, expiry) where nw_done = false`, so it indexes the complement of this selection
#: and cannot serve it); bounded by the id range, by `id > :after` and by `limit`.
_ORDERS = text("""
select id, ticker, side, prob, contracts, placed_at, expiry, cancelled_at,
       queue_ahead_at_place, filled_contracts
from orders
where replay = false and nw_done = true and id >= :from_order and id <= :to_order
  and id > :after
order by id
limit :limit
""")

#: The highest order already written for this correction set inside the range: where `--resume`
#: starts. Bounded by the range and rides `order_rescores_pkey`.
_RESUME_AFTER = text("""
select coalesce(max(order_id), :floor) from order_rescores
where correction_ids = :ids and order_id between :a and :b
""")

#: One order's prints, bounded by the track's own window and by the delta batch the executor
#: itself uses. Rides `ix_trades_ticker_ts (ticker, ts)`. `coalesce(taker_outcome_side,
#: taker_side)` is `harness/execution/store.py`'s own expression, not a second one: the
#: canonical taker side is `taker_outcome_side or taker_side` and is never defaulted (F5).
_PRINTS = text("""
select trade_id, ts, yes_price, count, coalesce(taker_outcome_side, taker_side) as taker_side,
       source
from venue_trades
where ticker = :t and ts >= :lower and ts <= :upper
order by ts, trade_id
limit :cap
""")

#: The same window of deltas. Ordered by `(ts, id)` and bounded on `ts` at both ends, which is
#: the past-instant shape `store._DELTAS_AT` and `book._DELTAS_BY_TS` use: it rides
#: `ix_obe_ticker_ts (ticker, ts)`. `limit :cap` is this command's own bound -- the replay path
#: has no loop deadline to miss and carries none.
_DELTAS = text("""
select id, ts, side, price, delta, sid, seq
from orderbook_events
where ticker = :t and kind = 'delta' and ts >= :lower and ts <= :upper
order by ts, id
limit :cap
""")


def _order_tape(session: Session, row, cap: int) -> tuple[list, list]:
    """One order's window of prints and deltas, under this command's own statement timeout.

    The timeout is set per statement rather than per session so that a cancelled read kills one
    order's re-score and not the run (ruling I-11). `set local` is transaction-scoped, which is
    what makes it per order here: `rescore` commits after every order, so the next order's read
    sets it again.

    The window is the track's own: `placed_at - store.PRINT_LOOKBACK` (the lower bound the
    executor re-reads prints from) to `expiry` (the counterfactual's deadline, the later of the
    two tracks'). `simulate_fills` drops anything outside each track's own deadline.
    """
    session.execute(text(f"set local statement_timeout = {RESCORE_STATEMENT_TIMEOUT_MS}"))
    lower = row.placed_at - store.PRINT_LOOKBACK
    upper = row.expiry
    params = {"t": row.ticker, "lower": lower, "upper": upper, "cap": cap}
    prints = session.execute(_PRINTS, params).all()
    deltas = session.execute(_DELTAS, params).all()
    return prints, deltas


def _as_prints(rows) -> list[TapePrint]:
    """The print rows as tape. `taker_side` is already canonical (the query coalesces it).

    Not deduplicated on `trade_id` the way `store.load_prints` is, because it does not have to
    be: `simulate_fills` keeps its own set of applied trade ids inside one call, so the same
    print reaching us from the WebSocket and from REST is applied once either way.
    """
    return [TapePrint(trade_id=r.trade_id, ts=r.ts, yes_price=r.yes_price, count=r.count,
                      taker_side=r.taker_side, source=r.source) for r in rows]


def _as_deltas(rows) -> list:
    """The delta rows as tape, through `store`'s own builder rather than a second copy of it
    (it is what drops a row whose side, price or delta is NULL)."""
    return store._tape_deltas(rows)


def _verdict(recorded: Decimal, repaired: Decimal) -> str:
    """`validated` within one contract, `corrected` otherwise. `unverifiable` is the caller's:
    it is a statement about the evidence, not about the arithmetic.

    Strictly within: a difference of exactly one contract is a difference. `harness/audit.py`
    compares the same tolerance with `<=`, and on order 157's quantities (38.92 against 6,401)
    the two rules cannot differ; here they can, because C3's equal-timestamp double count is a
    one-contract-scale error on a ten-contract order -- the seeded world's order B is exactly
    that case, a recorded fill of 1 the repair removes entirely -- and a rule that called a
    removed fill `validated` would hide the correction on precisely the orders it corrects.
    """
    return "validated" if abs(repaired - recorded) < FILL_TOLERANCE else "corrected"


def rescore(session: Session, from_order: int, to_order: int, corrections: list[str], *,
            limit: int | None = None, resume: bool = False,
            build_sha: str | None = None, now: datetime | None = None) -> RescoreCounts:
    """Re-score `[from_order, to_order]` under `corrections`, two policy rows per order.

    `resume` starts after the highest `order_id` already written for this correction set, so a
    run stopped half-way costs only the orders it had not reached. Without it the run starts at
    `from_order` and the inserts conflict harmlessly on the primary key, which is why a second
    full run writes nothing either.

    The counts are right-censored by construction: an order whose counterfactual had not
    finished when this ran is not in the denominator at all (`nw_done`), so the partition
    describes the orders that could be scored, never a rate over the range.
    """
    ids = ",".join(sorted(corrections))
    if not ids or len(ids) > MAX_CORRECTION_IDS:
        raise ValueError(f"correction ids must be 1..{MAX_CORRECTION_IDS} characters: {ids!r}")
    instant = now or datetime.now(timezone.utc)
    after = from_order - 1
    if resume:
        after = session.execute(_RESUME_AFTER, {"floor": from_order - 1, "ids": ids,
                                                "a": from_order, "b": to_order}).scalar()
    counts = RescoreCounts()
    rows = session.execute(_ORDERS, {"from_order": from_order, "to_order": to_order,
                                     "after": after,
                                     "limit": limit or 10_000}).all()
    for row in rows:
        counts = _rescore_one(session, row, ids, instant, build_sha, counts)
        # Per order, so an abandoned run keeps everything it had already scored and `--resume`
        # starts at the first order it had not reached (§4.4).
        session.commit()
    return counts


def _rescore_one(session: Session, row, ids: str, instant: datetime, build_sha: str | None,
                 counts: RescoreCounts) -> RescoreCounts:
    """One order, both policies, one `order_rescores` row each. Never raises out of the run."""
    denominator = counts.denominator + 1
    missing = _missing_anchor(row)
    if missing is not None:
        # Nothing the tape could say would anchor this order: no queue was seen at placement
        # (R10, `queue_ahead_at_place` NULL) or the order has no resting interval to read a tape
        # over. Reported in the same cell as an empty window -- both are an absence of evidence
        # rather than a cancelled read -- and named in the log, because the cell's three causes
        # are one partition member by §1.8's definition and cannot be three columns without a
        # schema change.
        log.warning("rescore: order %s unverifiable (%s)", row.id, missing)
        _write(session, row, ids, "unverifiable", None, None, instant, build_sha)
        return RescoreCounts(counts.completed, counts.unverifiable_no_tape + 1,
                             counts.unverifiable_read_cancelled, denominator)
    try:
        prints, deltas = _order_tape(session, row, store.DELTA_BATCH_LIMIT)
    except (OperationalError, DBAPIError) as exc:
        # A statement timeout is the one cancellation we have actually seen. The order is
        # recorded as unverifiable for a reason distinct from "no tape", because collapsing the
        # two would make a starved host look like a gap-ridden tape (ruling IM-4).
        log.warning("rescore read cancelled for order %s: %s", row.id, exc)
        # The cancelled statement left its transaction aborted; nothing already scored is lost,
        # because every order before this one is committed.
        session.rollback()
        _write(session, row, ids, "unverifiable", None, None, instant, build_sha)
        return RescoreCounts(counts.completed, counts.unverifiable_no_tape,
                             counts.unverifiable_read_cancelled + 1, denominator)
    if len(prints) >= store.DELTA_BATCH_LIMIT or len(deltas) >= store.DELTA_BATCH_LIMIT:
        # The bound stopped the read before the window ended, so what came back is a prefix of
        # this order's tape and scoring it would report a fill computed from part of the
        # evidence as though it were all of it -- on the busiest tickers, which is a bias and
        # not noise. Counted with the cancelled reads because it is the same kind of event: the
        # read's own bound, not the tape's silence (the shape of T7's refusal at the cap).
        log.warning("rescore read truncated at %d rows for order %s (%d prints, %d deltas)",
                    store.DELTA_BATCH_LIMIT, row.id, len(prints), len(deltas))
        _write(session, row, ids, "unverifiable", None, None, instant, build_sha)
        return RescoreCounts(counts.completed, counts.unverifiable_no_tape,
                             counts.unverifiable_read_cancelled + 1, denominator)
    if not prints and not deltas:
        _write(session, row, ids, "unverifiable", None, None, instant, build_sha)
        return RescoreCounts(counts.completed, counts.unverifiable_no_tape + 1,
                             counts.unverifiable_read_cancelled, denominator)

    order = PaperOrder(order_id=row.id, ticker=row.ticker, side=row.side, prob=row.prob,
                       contracts=row.contracts, placed_at=row.placed_at, expiry=row.expiry,
                       queue_ahead_at_place=row.queue_ahead_at_place)
    tape_prints, tape_deltas = _as_prints(prints), _as_deltas(deltas)
    watched_deadline = min(row.cancelled_at or row.expiry, row.expiry)
    # One dirty-time read per order rather than one per policy: the two rows describe the same
    # order's elapsed seconds, and the query is the same bounded statement either way.
    timing = _timing(session, row, instant)
    verdict = None
    for policy in (AHEAD, BEHIND):
        watched = simulate_fills(order, SimState.initial(order), None,
                                 tape_prints, tape_deltas, watched_deadline,
                                 "queue_model", cancel_policy=policy)
        counterfactual = simulate_fills(order, SimState.initial(order), None,
                                        tape_prints, tape_deltas, row.expiry,
                                        "no_watcher", cancel_policy=policy)
        if verdict is None:
            # The verdict is the point estimate's: the band is a sensitivity, not a second
            # opinion about what happened (D3).
            verdict = _verdict(row.filled_contracts, watched.state.filled_contracts)
        _write(session, row, ids, verdict, (policy, watched, counterfactual), timing, instant,
               build_sha)
    return RescoreCounts(counts.completed + 1, counts.unverifiable_no_tape,
                         counts.unverifiable_read_cancelled, denominator)


def _missing_anchor(row) -> str | None:
    """Why this order cannot be scored at all, or None if it can.

    `queue_ahead_at_place` NULL is R10: no book existed at placement, so the simulator is out of
    the order entirely and would report a fill of zero for every order -- a fabricated
    correction on any order that did fill. A NULL `expiry` has no resting interval, so there is
    no window to read and no deadline for either track.
    """
    if row.expiry is None:
        return "no expiry: the order has no resting interval"
    if row.queue_ahead_at_place is None:
        return "no queue at placement (R10): no book was seen"
    return None


def _timing(session: Session, row, instant: datetime):
    """This order's elapsed dirty and unobserved seconds, or None if the query does not carry it.

    `order_dirty_time` is bounded to `id > :boundary_order_id` with a `limit`, so asking for one
    order is `boundary_order_id = id - 1, limit = 1`. It excludes NULL-expiry orders, and its
    first row is the next order in the range rather than this one when this one is excluded --
    hence the identity check rather than a bare `[0]` (T6 carry-forward F6).
    """
    elapsed = order_dirty_time(session, instant, boundary_order_id=row.id - 1, limit=1)
    return elapsed[0] if elapsed and elapsed[0].order_id == row.id else None


def _write(session: Session, row, ids: str, verdict: str, measured, timing, instant: datetime,
           build_sha: str | None) -> None:
    """One `order_rescores` row, `on conflict do nothing` so a resumed run is a no-op.

    An unverifiable order gets one row per policy with the measured columns null, so the
    partition sums to the denominator over either policy's rows alone.
    """
    if verdict not in VERDICTS:
        # The vocabulary is `harness/audit.py`'s, and §2's invariant query is written against
        # exactly these three: a fourth value would make that query's "must return 0" false
        # without anything else going wrong, which is the kind of drift a column with no check
        # constraint cannot catch by itself.
        raise ValueError(f"unknown verdict {verdict!r}")
    policies = [measured[0]] if measured else [AHEAD, BEHIND]
    for policy in policies:
        values = {"order_id": row.id, "correction_ids": ids, "cancel_policy": policy,
                  "verdict": verdict, "computed_at": instant, "build_sha": build_sha,
                  "watched_dirty_s": timing.watched_dirty_s if timing else None,
                  "counterfactual_dirty_s": timing.counterfactual_dirty_s if timing else None,
                  "unobserved_s": timing.unobserved_s if timing else None}
        if measured:
            _policy, watched, counterfactual = measured
            values.update(watched_filled=watched.state.filled_contracts,
                          counterfactual_filled=counterfactual.state.filled_contracts,
                          queue_remaining=watched.state.queue_remaining,
                          cancels_ahead=watched.state.cancels_ahead)
        session.execute(insert(OrderRescore).values(**values).on_conflict_do_nothing())
