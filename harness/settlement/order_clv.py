"""CLV against the fixed benchmarks: one row per order (`order_clv`) and the whole-population
map over every gap snapshot (`gap_outcomes`, F42's "H2 map").

`clv_formulas` is the one piece of arithmetic both rows share -- the edge realised against a
benchmark, net of the maker fee a paper order at that price would have paid -- so it lives here
once rather than twice. Everything else is plumbing: `compute_order_clv` looks up an order's
own benchmarks in its own side space; `drain_gap_outcomes` looks up a gap snapshot's benchmarks
in the venue market's native (YES) space, because a gap snapshot is not one side's decision the
way an order is.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import GapOutcome, JobState, OrderClv
from harness.execution.book import side_p
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.settlement.benchmarks import BENCHMARK_TYPES, GAP_OUTCOME_TYPES
from harness.settlement.job import Budget, StageResult, register_stage

log = logging.getLogger(__name__)

#: The fee a paper order's CLV is scored net of. Contracts is a fixed notional (100) rather
#: than the order's own size: CLV is a price comparison, and a per-contract fee is the same
#: number whatever size the order actually was (`fee_per_contract` divides by `contracts`).
CLV_CONTRACTS = 100
GAP_OUTCOMES_WATERMARK_KEY = "gap_outcomes_watermark"


def clv_formulas(p_bench: Decimal, p_used: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """`(clv, clv_net, roi_net)` for one benchmark against one price, pure (spec F42):

    `clv = p_bench - p_used`; `clv_net = clv - fee_per_contract(maker, p_used)`;
    `roi_net = p_bench / (p_used + fee) - 1` -- the return on the all-in cost of buying at
    `p_used`. Both callers (`order_clv` in the order's side space, `gap_outcomes.clv_target_*`
    in the venue market's own space) pass an already side-resolved `p_bench`.
    """
    fee = fee_per_contract(KALSHI_FOOTBALL, "maker", p_used, CLV_CONTRACTS)
    clv = p_bench - p_used
    clv_net = clv - fee
    roi_net = p_bench / (p_used + fee) - Decimal(1)
    return clv, clv_net, roi_net


# --- order_clv ---------------------------------------------------------------------------

#: Fix round 1, C1: candidacy is per missing (order, benchmark_type) pair, not "the order has
#: any order_clv row at all". The eight pre-kick types land together at kickoff + 5 min; the
#: ninth, `result`, lands hours later once the game settles. Gating on "any row exists" locked
#: every order out of its own `result` row forever, the moment the first eight were written. An
#: order is a candidate exactly as long as its game has a benchmark type it has not yet gotten
#: its own row for -- which is true again the instant `result` is inserted, and false again once
#: it picks that row up too.
_ORDER_CLV_CANDIDATES = text("""
    select o.id as order_id, o.side, o.prob, o.game_id, m.market_type, m.side_team_id,
           m.side as market_side, m.threshold
    from orders o
    join venue_markets m on m.id = o.venue_market_id
    where o.replay = false
      and o.game_id is not null
      and exists (
        select 1 from benchmarks b
        where b.game_id = o.game_id
          and not exists (
            select 1 from order_clv c
            where c.order_id = o.id and c.benchmark_type = b.benchmark_type
          )
      )
    order by o.id
""")

#: Fix round 1, M8: a non-replay order with no `game_id` can never match the candidates query
#: above (there is no game to look benchmarks up on), so it would otherwise be an invisible,
#: permanent gap. Counted separately in the stage's counts as `no_game` rather than silently
#: never appearing anywhere.
_NO_GAME_ORDER_COUNT = text("""
    select count(*) from orders o
    where o.replay = false and o.game_id is null
      and not exists (select 1 from order_clv c where c.order_id = o.id)
""")

_BENCHMARKS_FOR_SHAPE = text("""
    select benchmark_type, p, stale from benchmarks
    where game_id = :game_id and market_type = :market_type
      and coalesce(outcome_team_id, -1) = coalesce(:team_id, -1)
      and coalesce(outcome_side, '') = coalesce(:side, '')
      and coalesce(threshold, 0) = coalesce(:threshold, 0)
""")


def _benchmarks_for_shape(session: Session, game_id: int, market_type: str,
                          team_id: int | None, side: str | None,
                          threshold: Decimal | None) -> dict[str, tuple[Decimal, bool]]:
    rows = session.execute(_BENCHMARKS_FOR_SHAPE, {
        "game_id": game_id, "market_type": market_type, "team_id": team_id,
        "side": side, "threshold": threshold}).all()
    return {r.benchmark_type: (r.p, r.stale) for r in rows if r.p is not None}


def _insert_order_clv(session: Session, **kwargs) -> int:
    stmt = insert(OrderClv).values(**kwargs).on_conflict_do_nothing().returning(OrderClv.order_id)
    return len(session.execute(stmt).fetchall())


def _no_game_order_count(session: Session) -> int:
    return int(session.execute(_NO_GAME_ORDER_COUNT).scalar() or 0)


def compute_order_clv(session: Session, now: datetime, budget: Budget) -> int:
    """One `order_clv` row per benchmark type of a non-replay order's shape that it does not
    have one for yet, for every order whose game has at least one such type (fix round 1, C1:
    per missing (order, benchmark_type) pair, so `result` -- which lands hours after the other
    eight -- is picked up on whichever later pass follows its insertion, not lost). Nothing is
    filtered on fill state: an unfilled order gets the same rows a filled one does (spec: every
    order, whether filled or not)."""
    del now
    inserted = 0
    rows = session.execute(_ORDER_CLV_CANDIDATES).all()
    cache: dict[tuple, dict[str, tuple[Decimal, bool]]] = {}
    for seen, row in enumerate(rows):
        if not budget.ok():
            log.info("compute_order_clv budget spent with %d orders left", len(rows) - seen)
            break
        shape_key = (row.game_id, row.market_type, row.side_team_id, row.market_side, row.threshold)
        benches = cache.get(shape_key)
        if benches is None:
            benches = _benchmarks_for_shape(session, row.game_id, row.market_type,
                                            row.side_team_id, row.market_side, row.threshold)
            cache[shape_key] = benches
        for benchmark_type in BENCHMARK_TYPES:
            bench = benches.get(benchmark_type)
            if bench is None:
                continue
            p, stale = bench
            p_bench = side_p(p, row.side)
            p_used = Decimal(row.prob)
            clv_p, clv_p_net, clv_roi_net = clv_formulas(p_bench, p_used)
            inserted += _insert_order_clv(
                session, order_id=row.order_id, benchmark_type=benchmark_type, p_bench=p_bench,
                p_used=p_used, p_used_kind="order", clv_p=clv_p, clv_p_net=clv_p_net,
                clv_roi_net=clv_roi_net, stale=stale)
    return inserted


# --- gap_outcomes drain --------------------------------------------------------------------

#: `has_result` (fix round 1, C1) replaces the old `has_benchmarks` ("any row exists"): a game
#: is only *done* being benchmarked once its `result` row lands (the last of the nine types,
#: written by `insert_result_benchmarks` once the game settles), so that is the signal the
#: watermark waits for. It does not gate whether a row is written this pass -- every currently
#: available `GAP_OUTCOME_TYPES` type is written regardless -- only whether this snapshot is
#: "finished" for watermark-advancement purposes.
_NEXT_GAP_SNAPSHOTS = text("""
    select g.id, g.fair_p, g.venue_mid, g.best_bid, g.run_id,
           m.game_id, m.market_type, m.side_team_id, m.side as market_side, m.threshold,
           exists(select 1 from benchmarks b where b.game_id = m.game_id
                  and b.benchmark_type = 'result') as has_result
    from market_gap_snapshots g
    join venue_markets m on m.id = g.venue_market_id
    where g.id > :watermark
    order by g.id
    limit :batch
""")

#: Fix round 1, M1: `order by s.id` makes the pick deterministic when more than one
#: `tier = 'primary'` variant somehow exists for the same run and snapshot (register_variants
#: is expected to keep exactly one, but the query should not silently guess otherwise) --
#: the earliest-recorded signal wins, rather than whichever row the planner happens to visit
#: first.
_PRIMARY_SIGNAL = text("""
    select s.price_target
    from signals s
    join strategy_variants v on v.variant_id = s.variant_id
    where v.tier = 'primary' and s.run_id = :run_id and s.gap_snapshot_id = :gap_snapshot_id
      and s.replay = false
    order by s.id
    limit 1
""")


def _get_watermark(session: Session) -> int:
    row = session.get(JobState, GAP_OUTCOMES_WATERMARK_KEY)
    return 0 if row is None or row.value is None else int(row.value)


def _set_watermark(session: Session, value: int) -> None:
    now = datetime.now(timezone.utc)
    stmt = insert(JobState).values(key=GAP_OUTCOMES_WATERMARK_KEY, value=value, updated_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value, "updated_at": now})
    session.execute(stmt)


def _insert_gap_outcome(session: Session, **kwargs) -> int:
    stmt = insert(GapOutcome).values(**kwargs).on_conflict_do_nothing().returning(GapOutcome.gap_snapshot_id)
    return len(session.execute(stmt).fetchall())


@dataclass(frozen=True)
class _Used:
    p: Decimal | None
    kind: str | None


def _p_used(session: Session, run_id: int, gap_snapshot_id: int, best_bid: Decimal | None) -> _Used:
    row = session.execute(_PRIMARY_SIGNAL, {"run_id": run_id, "gap_snapshot_id": gap_snapshot_id}).first()
    if row is not None and row.price_target is not None:
        return _Used(Decimal(row.price_target), "target")
    if best_bid is not None:
        return _Used(Decimal(best_bid), "best_bid")
    # Fix round 1, M2: neither a target nor a best bid exists, so there is no price this row
    # was "used" at -- `p_used_kind` must not claim `best_bid` for a price that was never there.
    return _Used(None, None)


def drain_gap_outcomes(session: Session, batch: int = 50_000, budget: Budget | None = None) -> int:
    """The whole-population H2 map: every gap snapshot with a fair price gets one `gap_outcomes`
    row per `GAP_OUTCOME_TYPES` type its game currently has a benchmark for.

    Reads a `job_state` watermark on `market_gap_snapshots.id` and scans the next `batch`
    snapshots by id, inserting whatever `GAP_OUTCOME_TYPES` rows are available for each one
    regardless of whether its game is "finished" yet. The watermark itself only advances past a
    *finished* snapshot -- one whose game already has its `result` benchmark, the last of the
    nine types to land, so no further type will ever arrive for it (fix round 1, C1) -- or one
    with no `fair_p` at all, which can never become eligible for anything (fix round 1
    preserves this: a permanently-unpriced snapshot is done, not stuck). Fix round 1, I2: an
    unfinished snapshot holds the watermark back, but the scan never stops there -- every later
    snapshot in the batch is still processed, so one game that never settles cannot starve every
    game after it in id order.

    `budget`, when given (the stage passes its own; the brief's positional signature stays
    callable without one -- fix round 1, I3), is checked before each snapshot the way every
    other stage checks it, and per-shape benchmark rows are cached across snapshots that share
    a shape within one call, so a full batch costs one query per distinct shape rather than one
    per row.
    """
    watermark = _get_watermark(session)
    rows = session.execute(_NEXT_GAP_SNAPSHOTS, {"watermark": watermark, "batch": batch}).all()
    inserted = 0
    advance_to = watermark
    stop_advance = False
    cache: dict[tuple, dict[str, tuple[Decimal, bool]]] = {}
    for seen, row in enumerate(rows):
        if budget is not None and not budget.ok():
            log.info("drain_gap_outcomes budget spent with %d snapshots left", len(rows) - seen)
            break
        shape_key = (row.game_id, row.market_type, row.side_team_id, row.market_side, row.threshold)
        benches = cache.get(shape_key)
        if benches is None:
            benches = _benchmarks_for_shape(session, row.game_id, row.market_type,
                                            row.side_team_id, row.market_side, row.threshold)
            cache[shape_key] = benches
        if row.fair_p is not None:
            used = _p_used(session, row.run_id, row.id, row.best_bid)
            for benchmark_type in GAP_OUTCOME_TYPES:
                bench = benches.get(benchmark_type)
                if bench is None:
                    continue
                p_bench, _stale = bench
                clv_mid_p = None if row.venue_mid is None else p_bench - Decimal(row.venue_mid)
                clv_bid_p = None if row.best_bid is None else p_bench - Decimal(row.best_bid)
                clv_target_p = clv_target_p_net = clv_target_roi_net = None
                if used.p is not None:
                    clv, clv_net, roi_net = clv_formulas(p_bench, used.p)
                    clv_target_p = clv if used.kind == "target" else None
                    clv_target_p_net = clv_net
                    clv_target_roi_net = roi_net
                inserted += _insert_gap_outcome(
                    session, gap_snapshot_id=row.id, benchmark_type=benchmark_type,
                    p_bench=p_bench, clv_mid_p=clv_mid_p, clv_bid_p=clv_bid_p,
                    clv_target_p=clv_target_p, clv_target_p_net=clv_target_p_net,
                    clv_target_roi_net=clv_target_roi_net, p_used_kind=used.kind)
        finished = row.fair_p is None or row.has_result
        if finished and not stop_advance:
            advance_to = row.id
        else:
            stop_advance = True
    if advance_to != watermark:
        _set_watermark(session, advance_to)
    return inserted


# --- stages ----------------------------------------------------------------------------


def gap_outcomes_drain_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    del now
    n = drain_gap_outcomes(session, budget=budget)
    return StageResult("gap_outcomes_drain", {"gap_outcomes_drain": n}, not budget.ok(), None)


def order_clv_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    n = compute_order_clv(session, now, budget)
    no_game = _no_game_order_count(session)
    return StageResult("order_clv", {"order_clv": n, "no_game": no_game}, not budget.ok(), None)


register_stage("gap_outcomes_drain", gap_outcomes_drain_stage)
register_stage("order_clv", order_clv_stage)
