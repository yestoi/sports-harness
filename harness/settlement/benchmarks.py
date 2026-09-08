"""Benchmarks: a fixed reference probability per market shape, computed once a game is done
enough for every pre-kickoff source to have settled into its final value.

Every shape gets up to eight benchmark rows from `compute_benchmarks` (kickoff + 5 min, once
per game) and a ninth, `result`, from `insert_result_benchmarks` once the score-derived
settlement exists. None is ever updated after it is written -- a shape's `benchmarks` rows are
the fixed comparison point every gap snapshot and every order on it is measured against
(`harness/settlement/order_clv.py`), so a row that could drift would drift every CLV number
computed from it.

Two pure decisions carry no session at all (ruling 1): `benchmark_at` picks the right snapshot
out of a list this module has already read from the database, and its `stale` flag is the one
piece of arithmetic every benchmark type shares. `kickoff_moved` takes a session because "the
game's first gap snapshot" is a database fact, but it takes no clock and returns a plain bool a
row can carry.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Benchmark, FairValue, Game, VenueMarket, VenueQuote, VenueTrade
from harness.pricing.devig import devig
from harness.pricing.fair import MATCHED_STATUSES
from harness.pricing.lines import (
    SPREAD_MARKETS, TOTAL_MARKETS, Line, latest_book_lines, ml_pair, spread_pair, total_pair,
)
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

FOUR = Decimal("0.0001")
STALE_WINDOW = timedelta(minutes=10)

#: `p, source_ts, stale` is what every non-result row needs; `result` is written separately by
#: `insert_result_benchmarks` because it depends on the settlement, not on a pre-kickoff source.
BENCHMARK_TYPES = (
    "pinnacle_t5", "consensus_t5", "consensus_t60", "consensus_t180", "opening_first_seen",
    "kalshi_mid_t5", "kalshi_last_trade_pre_kick", "novig_devig_t5", "result",
)
#: The whole-population H2 map (`drain_gap_outcomes`) is restricted to these four -- the shapes
#: that exist for every gap snapshot regardless of whether a variant traded it.
GAP_OUTCOME_TYPES = ("pinnacle_t5", "consensus_t5", "kalshi_mid_t5", "result")

RESULT = "result"


# --- pure decisions ---------------------------------------------------------------------


@dataclass(frozen=True)
class Snap:
    """One observation a benchmark type can be picked from.

    `ts` is what `benchmark_at` orders and filters on -- when this row itself was produced
    (a `fair_values.created_at`, a quote's `fetched_at`, a trade's `ts`). `source_ts` is what
    the staleness check compares against `target_ts`: the underlying market data's own
    timestamp, which for a fair value is `newest_book_ts`, not the row's `created_at`. The two
    coincide for quotes and trades, where the observation and its own timestamp are the same
    thing.
    """

    ts: datetime
    p: Decimal
    source_ts: datetime | None


def benchmark_at(kind: str, target_ts: datetime,
                 snapshots: list[Snap]) -> tuple[Decimal, datetime, bool] | None:
    """`(p, source_ts, stale)` for one benchmark type, or None when nothing qualifies.

    Every kind but `opening_first_seen` picks the last snapshot with `ts <= target_ts`: the
    freshest observation this benchmark's cutoff allows. `opening_first_seen` ignores the
    cutoff and always answers with the earliest snapshot by `ts`, whatever `target_ts` is --
    the opening line is "the first one ever seen", not "the first one before some instant".

    `stale` is `source_ts < target_ts - 10 min` (spec F42): a snapshot whose underlying data is
    more than ten minutes older than the benchmark's own target reads as stale regardless of
    which kind picked it. A snapshot with no `source_ts` falls back to its own `ts`.
    """
    if not snapshots:
        return None
    if kind == "opening_first_seen":
        chosen = min(snapshots, key=lambda s: s.ts)
    else:
        candidates = [s for s in snapshots if s.ts <= target_ts]
        if not candidates:
            return None
        chosen = max(candidates, key=lambda s: s.ts)
    source_ts = chosen.source_ts if chosen.source_ts is not None else chosen.ts
    stale = source_ts < target_ts - STALE_WINDOW
    return chosen.p, source_ts, stale


_FIRST_GAP_SNAPSHOT = text("""
    select g.created_at, g.ttk_minutes
    from market_gap_snapshots g
    join venue_markets m on m.id = g.venue_market_id
    where m.game_id = :game_id
    order by g.created_at asc, g.id asc
    limit 1
""")


def kickoff_moved(session: Session, game: Game) -> bool:
    """Whether this game's kickoff drifted more than 5 min from what its first gap snapshot
    implied it would be (`created_at + ttk_minutes` minutes). False when the game has no gap
    snapshot yet, or that snapshot never got a `ttk_minutes` -- there is nothing to compare
    the current kickoff against, so it reads as unmoved rather than moved.
    """
    row = session.execute(_FIRST_GAP_SNAPSHOT, {"game_id": game.id}).first()
    if row is None or row.ttk_minutes is None:
        return False
    kickoff_then = row.created_at + timedelta(minutes=row.ttk_minutes)
    return abs((kickoff_then - game.kickoff_utc).total_seconds()) > 300


# --- shapes ------------------------------------------------------------------------------


def _shapes_for_markets(markets: list[VenueMarket]) -> list[tuple]:
    """Distinct pricing shapes among a game's matched venue markets: the same rule
    `harness.pricing.fair._shapes_for_game` uses, so a shape here is exactly the key a
    `fair_values` row for this game was written under.
    """
    shapes: dict[tuple, None] = {}
    for m in markets:
        if m.match_status not in MATCHED_STATUSES:
            continue
        if m.market_type == "moneyline":
            key = ("moneyline", m.side_team_id, None, None)
        elif m.market_type == "spread":
            key = ("spread", m.side_team_id, None, m.threshold)
        elif m.market_type == "total":
            key = ("total", None, "over", m.threshold)
        else:
            continue
        shapes[key] = None
    return list(shapes.keys())


def _markets_for_shape(markets: list[VenueMarket], shape: tuple) -> list[VenueMarket]:
    market_type, team_id, side, threshold = shape
    return [m for m in markets if m.market_type == market_type and m.side_team_id == team_id
            and m.side == side and m.threshold == threshold]


# --- pre-kickoff sources -------------------------------------------------------------------


def _pair_for(market_type: str, threshold: Decimal | None, home_id: int, away_id: int,
             team_id: int | None, lines: dict, book: str,
             main_only: bool) -> tuple[Line, Line] | None:
    if market_type == "moneyline":
        opp_id = away_id if team_id == home_id else home_id
        pairs = ml_pair(lines, team_id, opp_id)
    elif market_type == "spread":
        opp_id = away_id if team_id == home_id else home_id
        markets = ("spreads",) if main_only else SPREAD_MARKETS
        pairs = spread_pair(lines, team_id, opp_id, threshold, markets=markets)
    else:  # total
        markets = ("totals",) if main_only else TOTAL_MARKETS
        pairs = total_pair(lines, threshold, markets=markets)
    return pairs.get(book)


def _pinnacle_t5(shape: tuple, home_id: int, away_id: int, lines: dict,
                 target_ts: datetime) -> tuple[str, Decimal, datetime, bool] | None:
    market_type, team_id, _side, threshold = shape
    pair = _pair_for(market_type, threshold, home_id, away_id, team_id, lines, "pinnacle", main_only=False)
    if pair is None:
        return None
    leg1, leg2 = pair
    p = devig([leg1.price, leg2.price], method="power")[0]
    source_ts = leg1.last_update or leg1.fetched_at
    stale = source_ts < target_ts - STALE_WINDOW
    return "pinnacle_t5", p, source_ts, stale


def _novig_devig_t5(shape: tuple, home_id: int, away_id: int, lines: dict,
                    target_ts: datetime) -> tuple[str, Decimal, datetime, bool] | None:
    market_type, team_id, _side, threshold = shape
    pair = _pair_for(market_type, threshold, home_id, away_id, team_id, lines, "novig", main_only=True)
    if pair is None:
        return None
    leg1, leg2 = pair
    p = devig([leg1.price, leg2.price], method="proportional")[0]
    source_ts = leg1.last_update or leg1.fetched_at
    stale = source_ts < target_ts - STALE_WINDOW
    return "novig_devig_t5", p, source_ts, stale


def _fair_snaps(session: Session, game_id: int, shape: tuple) -> list[Snap]:
    market_type, team_id, side, threshold = shape
    stmt = (
        select(FairValue.created_at, FairValue.fair_p, FairValue.newest_book_ts)
        .where(FairValue.game_id == game_id, FairValue.market_type == market_type,
              FairValue.outcome_team_id == team_id, FairValue.outcome_side == side,
              FairValue.threshold == threshold, FairValue.fair_source == "direct")
    )
    return [Snap(ts=row.created_at, p=row.fair_p, source_ts=row.newest_book_ts)
            for row in session.execute(stmt).all()]


def _quote_snaps(session: Session, venue_market_ids: list[int]) -> list[Snap]:
    if not venue_market_ids:
        return []
    stmt = select(VenueQuote).where(VenueQuote.venue_market_id.in_(venue_market_ids))
    snaps = []
    for row in session.execute(stmt).scalars():
        if row.yes_bid is None or row.yes_ask is None:
            continue
        mid = ((row.yes_bid + row.yes_ask) / 2).quantize(FOUR)
        snaps.append(Snap(ts=row.fetched_at, p=mid, source_ts=row.fetched_at))
    return snaps


def _trade_snaps(session: Session, tickers: list[str]) -> list[Snap]:
    if not tickers:
        return []
    stmt = select(VenueTrade).where(VenueTrade.ticker.in_(tickers))
    return [Snap(ts=row.ts, p=row.yes_price, source_ts=row.ts)
            for row in session.execute(stmt).scalars()]


def _benchmarks_for_shape(session: Session, game: Game, shape: tuple, lines_t5: dict,
                          markets: list[VenueMarket]) -> list[tuple[str, Decimal, datetime, bool]]:
    """Every `(benchmark_type, p, source_ts, stale)` this shape can produce, skipping any type
    whose source has no snapshot at or before its target (spec: `benchmark_at` returns None ->
    no row for that type).
    """
    kickoff = game.kickoff_utc
    t5, t60, t180 = kickoff - timedelta(minutes=5), kickoff - timedelta(minutes=60), kickoff - timedelta(minutes=180)
    shape_markets = _markets_for_shape(markets, shape)
    venue_market_ids = [m.id for m in shape_markets]
    tickers = [m.ticker for m in shape_markets]

    rows: list[tuple[str, Decimal, datetime, bool]] = []

    pinnacle = _pinnacle_t5(shape, game.home_team_id, game.away_team_id, lines_t5, t5)
    if pinnacle is not None:
        rows.append(pinnacle)

    novig = _novig_devig_t5(shape, game.home_team_id, game.away_team_id, lines_t5, t5)
    if novig is not None:
        rows.append(novig)

    fair_snaps = _fair_snaps(session, game.id, shape)
    for kind, target in (("consensus_t5", t5), ("consensus_t60", t60), ("consensus_t180", t180),
                         ("opening_first_seen", kickoff)):
        got = benchmark_at(kind, target, fair_snaps)
        if got is not None:
            rows.append((kind, got[0], got[1], got[2]))

    quote_snaps = _quote_snaps(session, venue_market_ids)
    got = benchmark_at("kalshi_mid_t5", t5, quote_snaps)
    if got is not None:
        rows.append(("kalshi_mid_t5", got[0], got[1], got[2]))

    trade_snaps = _trade_snaps(session, tickers)
    got = benchmark_at("kalshi_last_trade_pre_kick", kickoff, trade_snaps)
    if got is not None:
        rows.append(("kalshi_last_trade_pre_kick", got[0], got[1], got[2]))

    return rows


# --- the benchmarks stage ------------------------------------------------------------------

_ELIGIBLE_GAMES = text("""
    select distinct g.id
    from games g
    join venue_markets m on m.game_id = g.id
    where g.kickoff_utc + interval '5 minutes' <= :now
      and m.match_status = any(:statuses)
      and not exists (select 1 from benchmarks b where b.game_id = g.id)
    order by g.id
""")


def _insert_benchmark(session: Session, **kwargs) -> int:
    stmt = insert(Benchmark).values(**kwargs).on_conflict_do_nothing().returning(Benchmark.id)
    return len(session.execute(stmt).fetchall())


def compute_benchmarks(session: Session, now: datetime, budget: Budget) -> int:
    """Every non-`result` benchmark row for every game past `kickoff + 5 min` that has a
    matched venue market and no benchmark rows yet (spec: computed exactly once per game).

    One savepoint per game, mirroring `run_settlement`: a game whose pricing data raises must
    not cost the games around it their own rows.
    """
    ctx = current_ctx()
    inserted = 0
    game_ids = session.execute(
        _ELIGIBLE_GAMES, {"now": now, "statuses": list(MATCHED_STATUSES)}).scalars().all()
    for seen, game_id in enumerate(game_ids):
        if not budget.ok():
            log.info("compute_benchmarks budget spent with %d games left", len(game_ids) - seen)
            break
        try:
            with session.begin_nested():
                inserted += _process_game(session, game_id, now)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one game must not cost the pass
            session.rollback()
            log.exception("compute_benchmarks failed for game_id=%s", game_id)
            ctx["errors"].append({"compute_benchmarks": game_id,
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
    return inserted


def compute_benchmarks_for_game(session: Session, game_id: int, now: datetime) -> int:
    """The `harness benchmarks --game-id` path: run `_process_game` for exactly this game,
    regardless of the job's own eligibility gate (kickoff + 5 min, no rows yet). An operator
    naming a game by id already knows it is ready; the insert is through the same unique key as
    the scheduled job, so calling this on a game that already has rows inserts nothing new."""
    return _process_game(session, game_id, now)


def _process_game(session: Session, game_id: int, now: datetime) -> int:
    game = session.get(Game, game_id)
    markets = list(session.execute(
        select(VenueMarket).where(VenueMarket.game_id == game_id,
                                  VenueMarket.match_status.in_(MATCHED_STATUSES))).scalars())
    shapes = _shapes_for_markets(markets)
    if not shapes:
        return 0
    moved = kickoff_moved(session, game)
    lines_t5 = latest_book_lines(session, game_id, game.kickoff_utc - timedelta(minutes=5))
    inserted = 0
    for shape in shapes:
        market_type, team_id, side, threshold = shape
        for kind, p, source_ts, stale in _benchmarks_for_shape(session, game, shape, lines_t5, markets):
            inserted += _insert_benchmark(
                session, game_id=game_id, market_type=market_type, outcome_team_id=team_id,
                outcome_side=side, threshold=threshold, benchmark_type=kind, p=p,
                target_ts=game.kickoff_utc, source_ts=source_ts, stale=stale,
                kickoff_moved=moved, created_at=now)
    return inserted


# --- the result_benchmarks stage ------------------------------------------------------------

_PENDING_RESULT_ROWS = text("""
    select d.payout, d.settled_at, m.game_id, m.market_type, m.side_team_id, m.side, m.threshold
    from venue_settlements d
    join venue_markets m on m.ticker = d.ticker and m.venue = d.venue
    left join benchmarks b
      on b.game_id = m.game_id and b.market_type = m.market_type
     and coalesce(b.outcome_team_id, -1) = coalesce(m.side_team_id, -1)
     and coalesce(b.outcome_side, '') = coalesce(m.side, '')
     and coalesce(b.threshold, 0) = coalesce(m.threshold, 0)
     and b.benchmark_type = 'result'
    where d.source = 'derived' and b.game_id is null
    order by d.ticker
""")

_PENDING_RESULT_ROWS_FOR_GAME = text("""
    select d.payout, d.settled_at, m.game_id, m.market_type, m.side_team_id, m.side, m.threshold
    from venue_settlements d
    join venue_markets m on m.ticker = d.ticker and m.venue = d.venue
    left join benchmarks b
      on b.game_id = m.game_id and b.market_type = m.market_type
     and coalesce(b.outcome_team_id, -1) = coalesce(m.side_team_id, -1)
     and coalesce(b.outcome_side, '') = coalesce(m.side, '')
     and coalesce(b.threshold, 0) = coalesce(m.threshold, 0)
     and b.benchmark_type = 'result'
    where d.source = 'derived' and b.game_id is null and m.game_id = :game_id
    order by d.ticker
""")


def insert_result_benchmarks_for_game(session: Session, game_id: int, now: datetime) -> int:
    """The `result` rows for one game's `benchmarks --game-id` path, mirroring
    `insert_result_benchmarks` scoped to a single game rather than every pending settlement."""
    inserted = 0
    game = session.get(Game, game_id)
    moved = kickoff_moved(session, game) if game is not None else False
    for row in session.execute(_PENDING_RESULT_ROWS_FOR_GAME, {"game_id": game_id}).all():
        inserted += _insert_benchmark(
            session, game_id=row.game_id, market_type=row.market_type,
            outcome_team_id=row.side_team_id, outcome_side=row.side, threshold=row.threshold,
            benchmark_type=RESULT, p=row.payout, target_ts=row.settled_at,
            source_ts=row.settled_at, stale=False, kickoff_moved=moved, created_at=now)
    return inserted


def insert_result_benchmarks(session: Session, now: datetime, budget: Budget) -> int:
    """The `result` benchmark for every derived settlement whose shape does not have one yet.

    Independent of `compute_benchmarks`: a shape gets its `result` row as soon as it is
    settled, whether or not the game ever qualified for the other eight types.
    """
    ctx = current_ctx()
    inserted = 0
    rows = session.execute(_PENDING_RESULT_ROWS).all()
    for seen, row in enumerate(rows):
        if not budget.ok():
            log.info("insert_result_benchmarks budget spent with %d rows left", len(rows) - seen)
            break
        try:
            with session.begin_nested():
                game = session.get(Game, row.game_id)
                moved = kickoff_moved(session, game) if game is not None else False
                inserted += _insert_benchmark(
                    session, game_id=row.game_id, market_type=row.market_type,
                    outcome_team_id=row.side_team_id, outcome_side=row.side,
                    threshold=row.threshold, benchmark_type=RESULT, p=row.payout,
                    target_ts=row.settled_at, source_ts=row.settled_at, stale=False,
                    kickoff_moved=moved, created_at=now)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            log.exception("insert_result_benchmarks failed for game_id=%s", row.game_id)
            ctx["errors"].append({"insert_result_benchmarks": row.game_id,
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
    return inserted


# --- stages ----------------------------------------------------------------------------


def benchmarks_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    n = compute_benchmarks(session, now, budget)
    return StageResult("benchmarks", {"benchmarks": n}, not budget.ok(), None)


def result_benchmarks_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    n = insert_result_benchmarks(session, now, budget)
    return StageResult("result_benchmarks", {"result_benchmarks": n}, not budget.ok(), None)


register_stage("benchmarks", benchmarks_stage)
register_stage("result_benchmarks", result_benchmarks_stage)
