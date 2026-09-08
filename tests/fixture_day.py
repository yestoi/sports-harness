"""Loads a committed fixture day and runs it end to end, for `test_fixture_day_end_to_end`.

A day exported by `harness export-fixture --kind day` carries only what the recorder taped --
`raw_responses`, `orderbook_events`, `venue_trades`, plus the runs that define the range -- so
loading one means inserting those and then running the same chain the NAS runs: normalize,
price and signal, replay the executor over the range, benchmark the game, drain the gap
outcomes. Nothing here fetches anything; there is no HTTP client in this module.

This is a test helper rather than production code: the harness itself never needs to read a
fixture back, and a loader in `harness/` would be a second, untested way to write the record.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


from harness.db.models import (
    Fill,
    GapOutcome,
    Order,
    OrderbookEvent,
    RawResponse,
    Run,
    Signal,
    VenueMarket,
    VenueTrade,
)
from harness.db.schema import ensure_partitions
from harness.matching.teams import seed_teams_from_espn
from harness.normalize import runner as runner_mod
from harness.replay import replay
from harness.settlement.benchmarks import compute_benchmarks_for_game
from harness.settlement.order_clv import drain_gap_outcomes
from harness.strategy.pipeline import price_and_signal, pricing_clock_for_run
from harness.strategy.variants import load_variants, register_variants

FIXD = Path(__file__).parent / "fixtures"
VARIANT = "tiny"


@dataclass(frozen=True)
class DayResult:
    """What the day produced, one count per stage the fixture exists to exercise."""

    markets: int
    matched: int
    signals: int
    candidates: int
    orders: int
    fills: int
    gap_outcomes: int


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def load_rows(session, day: dict) -> list[Run]:
    """Insert the day's runs and its three recorded tables, exactly as exported.

    Run ids are preserved because `raw_responses.run_id` points at them and the export's
    `meta.runs` is what a replay's grid is built from; the tables are empty at this point (the
    `db_session` fixture truncates and restarts identity between tests), so the ids are free.
    """
    ensure_partitions(session, _dt(day["meta"]["window"]["lower"]))
    ensure_partitions(session, _dt(day["meta"]["window"]["upper"]))
    runs = []
    for row in day["meta"]["runs"]:
        run = Run(id=row["id"], started_at=_dt(row["started_at"]),
                  finished_at=None if row["finished_at"] is None else _dt(row["finished_at"]),
                  status=row["status"])
        session.add(run)
        runs.append(run)
    for row in day["raw_responses"]:
        session.add(RawResponse(
            id=row["id"], fetched_at=_dt(row["fetched_at"]), run_id=row["run_id"],
            source=row["source"], endpoint=row["endpoint"], params=row["params"],
            http_status=row["http_status"], body=row["body"]))
    for row in day["orderbook_events"]:
        session.add(OrderbookEvent(
            id=row["id"], ticker=row["ticker"], ts=_dt(row["ts"]), sid=row["sid"],
            seq=row["seq"], kind=row["kind"], side=row["side"], price=_dec(row["price"]),
            delta=_dec(row["delta"]), raw=row["raw"]))
    for row in day["venue_trades"]:
        session.add(VenueTrade(
            venue=row["venue"], trade_id=row["trade_id"], ticker=row["ticker"],
            ts=_dt(row["ts"]), yes_price=_dec(row["yes_price"]), count=_dec(row["count"]),
            taker_side=row["taker_side"], taker_outcome_side=row["taker_outcome_side"],
            taker_book_side=row["taker_book_side"], is_block=row["is_block"],
            source=row["source"]))
    session.commit()
    return runs


def load_day(session, directory: Path, settings) -> DayResult:
    """Load `directory/day.json` and run the whole chain over it."""
    day = json.loads((directory / "day.json").read_text())
    runs = load_rows(session, day)

    # Teams are seeded by `harness seed-teams`, not by the recorder, so they are setup rather
    # than part of the day: the ESPN teams endpoint is a reference list, not a day's tape.
    seed_teams_from_espn(session, "nfl",
                         json.loads((FIXD / "espn_teams_nfl.json").read_text()))
    session.commit()

    # `_EVENTS` is a process-wide cache in the normalizer; a stale one would let this day's
    # markets match against another test's events.
    runner_mod._EVENTS.clear()
    try:
        runner_mod.normalize_new(session)
        session.commit()

        register_variants(session, load_variants(FIXD / "variants"),
                          runs[0].started_at, prune=True)
        session.commit()
        for run in runs:
            price_and_signal(session, run.id, pricing_clock_for_run(run, settings.tick_budget_s),
                             settings, budget_s=30)
        session.commit()

        replay(session, runs[0].id, runs[-1].id, VARIANT, execute=True, settings=settings)
        session.commit()

        game_id = session.query(VenueMarket.game_id).filter(
            VenueMarket.game_id.isnot(None)).limit(1).scalar()
        compute_benchmarks_for_game(session, game_id, _after_kickoff(session, game_id))
        session.commit()
        drain_gap_outcomes(session)
        session.commit()
    finally:
        runner_mod._EVENTS.clear()

    session.expire_all()
    markets = session.query(VenueMarket).all()
    return DayResult(
        markets=len(markets),
        matched=sum(1 for m in markets if m.match_status == "matched"),
        signals=session.query(Signal).filter_by(replay=True).count(),
        candidates=session.query(Signal).filter_by(replay=True, decision="candidate").count(),
        orders=session.query(Order).filter_by(replay=True).count(),
        fills=session.query(Fill).join(Order, Order.id == Fill.order_id)
                     .filter(Order.replay.is_(True)).count(),
        gap_outcomes=session.query(GapOutcome).count(),
    )


def _after_kickoff(session, game_id: int) -> datetime:
    """`compute_benchmarks_for_game`'s `now`: past kickoff, which is when a game is benchmarked.

    The day itself stops two hours before kickoff, so every benchmark it can produce is marked
    stale -- which is the honest label for a line that old, and is exactly what a 30-minute
    slice of a day is able to say.
    """
    from harness.db.models import Game

    return session.get(Game, game_id).kickoff_utc + timedelta(minutes=10)
