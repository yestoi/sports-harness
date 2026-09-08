"""`harness replay --execute`: the R14 replay-versus-live check, `export-fixture`, the day.

The five tests here answer five different questions:

1. Does a replay executor, stepped over the same 15 s grid the live one walked, produce exactly
   the same orders and exactly the same fills? (R14: strict equality, not a 2 % band.)
2. Does a replayed signal carry its own run's pricing clock, so the grid is a timeline?
3. Does a second replay over the same range write nothing at all?
4. Does the committed fixture day run end to end -- signals, orders, fills, gap outcomes -- with
   no order on a market the matcher never confidently matched?
5. Does `export-fixture --out -` put the record on stdout in the shape the fixture day carries?

Everything is driven by an explicit clock. Nothing here opens a socket, and no test asserts on
wall time.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from harness.db.models import (
    Fill,
    Intent,
    OddsSnapshot,
    Order,
    OrderEvent,
    Run,
    Signal,
    VenueMarket,
    VenueQuote,
)
from harness.replay import replay
from harness.strategy.pipeline import price_and_signal, pricing_clock_for_run
from harness.strategy.variants import load_variants, register_variants
from tests.test_exec_loop import T2, T3, Clock, _book2, _book3, _print, make_executor
from tests.test_pipeline import NOW, VARIANTS_DIR, _seed

runner = CliRunner()

DAY = Path(__file__).parent / "fixtures" / "day_synthetic"

#: The grid the R14 test walks: two priced runs 45 s apart, so the executor takes four steps
#: (place, hold, fill, hold) rather than the single step one run would give it.
GRID_S = 45

#: What the second run's copied rows add to their `raw_id`s: those keys are per raw response,
#: not per run, and the second tick would have read its own responses.
RAW_OFFSET = 100_000


def _finish(session, run, at: datetime) -> None:
    """Give a run a `finished_at`, so `pricing_clock_for_run` is that instant exactly.

    `_seed` leaves its run `running`, whose pricing clock is `started_at + tick_budget_s`. That
    is the right fallback in production and a needless offset here: the live pass and the replay
    have to price at the same instant for their signals to line up on one grid.
    """
    run.finished_at = at
    run.status = "ok"
    session.flush()


def _second_run(session, at: datetime) -> Run:
    """A second priced tick `at`: the first run's odds and quotes again under a new run id.

    Copying rather than re-fetching keeps the two runs' inputs identical, so any difference the
    R14 comparison finds is the executor's, never the pricing's. `uq_odds_snapshot_row` is keyed
    on `raw_id` rather than on the run, so the copy takes its own raw ids: the second tick read
    its own HTTP responses, and pretending otherwise would collide.
    """
    run = Run(started_at=at, finished_at=at, status="ok")
    session.add(run)
    session.flush()
    for row in session.query(OddsSnapshot).order_by(OddsSnapshot.id).all():
        session.add(OddsSnapshot(
            raw_id=row.raw_id + RAW_OFFSET, run_id=run.id, book=row.book, game_id=row.game_id,
            market_type=row.market_type, outcome_team_id=row.outcome_team_id,
            outcome_side=row.outcome_side, point=row.point, price_decimal=row.price_decimal,
            book_last_update=row.book_last_update, fetched_at=at))
    for row in session.query(VenueQuote).order_by(VenueQuote.id).all():
        session.add(VenueQuote(
            raw_id=row.raw_id + RAW_OFFSET, run_id=run.id, venue_market_id=row.venue_market_id,
            yes_bid=row.yes_bid, yes_ask=row.yes_ask, no_bid=row.no_bid, no_ask=row.no_ask,
            yes_bid_size=row.yes_bid_size, yes_ask_size=row.yes_ask_size, volume=row.volume,
            volume_24h=row.volume_24h, open_interest=row.open_interest, fetched_at=at))
    session.flush()
    return run


def _order_key(row) -> tuple:
    return (row.ticker, row.side, row.prob, row.contracts, row.placed_at)


def _fill_keys(session, replay_flag: bool) -> set:
    """Every fill of one side of the record, keyed on its order's identity rather than its id.

    Order ids differ between the live pass and the replay by construction, so the comparison is
    made on what an order *is* -- ticker, side, price, placement instant -- plus the fill's own
    price, size and instant.
    """
    rows = (session.query(Fill, Order).join(Order, Order.id == Fill.order_id)
            .filter(Order.replay == replay_flag).all())
    return {(fill.order_id and (order.ticker, order.side, order.prob, order.placed_at),
             fill.fill_method, fill.prob, fill.contracts, fill.filled_at)
            for fill, order in rows}


def _row_counts(session) -> dict:
    """The replay side of every table `--execute` writes, for the rerun test."""
    return {
        "signals": session.query(Signal).filter_by(replay=True).count(),
        "intents": session.query(Intent).filter_by(replay=True).count(),
        "orders": session.query(Order).filter_by(replay=True).count(),
        "order_events": session.query(OrderEvent).filter_by(replay=True).count(),
        "fills": session.query(Fill).join(Order, Order.id == Fill.order_id)
                        .filter(Order.replay.is_(True)).count(),
    }


@pytest.fixture
def two_runs(env_settings, db_session):
    """Two priced runs 45 s apart, both books on the tape, one print that fills T2 in between.

    The live pass is driven exactly as the day happened: run A is priced, the executor steps
    the grid, and run B's signals only appear at run B's own clock -- which is the state a
    replay of the same range has to reconstruct from nothing but the record.
    """
    game, run_a, markets = _seed(db_session)
    _finish(db_session, run_a, NOW)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    _book2(db_session, NOW - timedelta(seconds=5))
    _book3(db_session, NOW - timedelta(seconds=5))
    # 100 contracts lifted at our 0.35 on T2: the 40 resting ahead of us go first, we take the
    # remaining 60 of our 97, and the fill lands on the step at NOW + 30.
    _print(db_session, T2, NOW + timedelta(seconds=20), "0.35", "100", taker_side="no",
           trade_id="fill-t2")
    db_session.commit()
    run_b = _second_run(db_session, NOW + timedelta(seconds=GRID_S))
    db_session.commit()
    return game, run_a, run_b


def _run_live(env_settings, db_session, run_a, run_b):
    """Price and step the live executor across the grid, the way the day itself ran."""
    price_and_signal(db_session, run_a.id, NOW, env_settings, budget_s=20)
    db_session.commit()
    clock = Clock(NOW)
    executor = make_executor(env_settings, db_session, clock)
    for offset in range(0, GRID_S + 1, env_settings.exec_period_s):
        if offset == GRID_S:
            price_and_signal(db_session, run_b.id, NOW + timedelta(seconds=GRID_S),
                             env_settings, budget_s=20)
            db_session.commit()
        clock.now = NOW + timedelta(seconds=offset)
        clock.mono = float(offset)
        executor.step()
    db_session.commit()
    db_session.expire_all()


# --- R14 ------------------------------------------------------------------------------


def test_replay_execute_reproduces_live_orders_and_fills_exactly(env_settings, db_session, two_runs):
    """R14: the same grid, the same tape, the same orders and the same fills -- exactly."""
    game, run_a, run_b = two_runs
    _run_live(env_settings, db_session, run_a, run_b)

    live_orders = {_order_key(o) for o in db_session.query(Order).filter_by(replay=False).all()}
    live_fills = _fill_keys(db_session, False)
    assert live_orders, "the live pass placed nothing; the R14 comparison would be vacuous"
    assert live_fills, "the live pass filled nothing; the R14 comparison would be vacuous"

    counts = replay(db_session, run_a.id, run_b.id, "tiny", execute=True, settings=env_settings)
    db_session.commit()
    db_session.expire_all()

    replay_orders = db_session.query(Order).filter_by(replay=True).all()
    assert {_order_key(o) for o in replay_orders} == live_orders
    assert _fill_keys(db_session, True) == live_fills
    assert counts.orders == len(replay_orders) == len(live_orders)

    # Tagged everywhere, and the live side of the record is exactly as the live pass left it.
    assert all(o.replay is True and o.mode == "paper" for o in replay_orders)
    assert all(o.client_order_id.startswith("replay-") for o in replay_orders)
    assert {i.replay for i in db_session.query(Intent).all()} == {False, True}
    assert db_session.query(Order).filter_by(replay=False).count() == len(live_orders)
    assert _fill_keys(db_session, False) == live_fills


def test_replay_execute_writes_no_live_row_and_no_heartbeat(env_settings, db_session, two_runs):
    """Ruling 4, the other half: a replay never touches a live row, the ledger or the heartbeat.

    The heartbeat matters operationally as much as the ledger does -- it is one row for the
    whole harness, and `exec-health` restarts the container on its age, so a replay stamping it
    with an instant three days back would take the live executor down with it.
    """
    game, run_a, run_b = two_runs
    price_and_signal(db_session, run_a.id, NOW, env_settings, budget_s=20)
    db_session.commit()

    replay(db_session, run_a.id, run_b.id, "tiny", execute=True, settings=env_settings)
    db_session.commit()
    db_session.expire_all()

    assert db_session.query(Order).filter_by(replay=False).count() == 0
    assert db_session.query(Order).filter_by(replay=True).count() > 0
    assert db_session.execute(text("select count(*) from exec_heartbeat")).scalar() == 0
    assert db_session.execute(
        text("select count(*) from ledger where replay = false")).scalar() == 0


# --- the pricing clock ----------------------------------------------------------------


def test_replay_signals_carry_the_run_pricing_clock(env_settings, db_session):
    """Every replayed signal is stamped with its own run's pricing clock, never wall time.

    Both branches of `pricing_clock_for_run` are exercised: a finished run prices at its
    `finished_at`, an unfinished one at `started_at + tick_budget_s`.
    """
    game, run_a, markets = _seed(db_session)
    _finish(db_session, run_a, NOW)  # the finished branch: priced at its own finished_at
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    price_and_signal(db_session, run_a.id, NOW, env_settings, budget_s=20)
    run_b = _second_run(db_session, NOW + timedelta(seconds=GRID_S))
    run_b.finished_at = None  # the unfinished branch: priced at start + tick_budget_s
    run_b.status = "running"
    db_session.commit()
    price_and_signal(db_session, run_b.id, NOW + timedelta(seconds=GRID_S), env_settings,
                     budget_s=20)
    db_session.commit()

    # A wall-clock `now` is passed deliberately: it must reach the variant registration and
    # nothing else.
    far = datetime(2027, 1, 1, tzinfo=timezone.utc)
    replay(db_session, run_a.id, run_b.id, "tiny", now=far)
    db_session.commit()
    db_session.expire_all()

    clock_a = pricing_clock_for_run(db_session.get(Run, run_a.id), env_settings.tick_budget_s)
    clock_b = pricing_clock_for_run(db_session.get(Run, run_b.id), env_settings.tick_budget_s)
    assert clock_a == NOW
    assert clock_b == NOW + timedelta(seconds=GRID_S) + timedelta(
        seconds=env_settings.tick_budget_s)

    for run_id, clock in ((run_a.id, clock_a), (run_b.id, clock_b)):
        stamps = {s.created_at for s in
                  db_session.query(Signal).filter_by(run_id=run_id, replay=True).all()}
        assert stamps == {clock}, f"run {run_id} signals carry {stamps}, not {clock}"


# --- idempotency ----------------------------------------------------------------------


def test_rerun_inserts_zero(env_settings, db_session, two_runs):
    """A second `replay --execute` over the same range re-derives everything and writes nothing."""
    game, run_a, run_b = two_runs
    price_and_signal(db_session, run_a.id, NOW, env_settings, budget_s=20)
    db_session.commit()

    first = replay(db_session, run_a.id, run_b.id, "tiny", execute=True, settings=env_settings)
    db_session.commit()
    before = _row_counts(db_session)
    assert first.inserted > 0 and before["orders"] > 0

    second = replay(db_session, run_a.id, run_b.id, "tiny", execute=True, settings=env_settings)
    db_session.commit()
    db_session.expire_all()

    assert second.inserted == 0
    assert second.signals_candidate == first.signals_candidate
    assert second.signals_rejected == first.signals_rejected
    assert second.orders == first.orders and second.fills == first.fills
    assert _row_counts(db_session) == before


# --- the fixture day ------------------------------------------------------------------


def test_fixture_day_end_to_end(env_settings, db_session):
    """The committed synthetic day, loaded and run: signals, orders, fills, gap outcomes.

    The day carries only what the recorder taped -- `raw_responses`, `orderbook_events`,
    `venue_trades` -- so this walks the whole chain that turns those into a paper position, and
    every count it asserts is a count the chain has to produce for the day to be usable.
    """
    from tests.fixture_day import load_day

    day = load_day(db_session, DAY, env_settings)

    # Pinned, not merely non-zero: the day is committed, so every count it produces is a fact
    # about the file, and a change in any of them is a change in what the harness does to it.
    assert (day.markets, day.matched) == (5, 4)
    assert (day.signals, day.candidates) == (8, 4)
    # Three orders: the total (filled outright by the day's last two prints), the moneyline
    # placed with no book at all (R10's path), and the total again once the first one closed
    # and the second run's signal asked for it. Four fills, two per track. Sixteen gap
    # outcomes: four gap snapshots against four benchmark types.
    assert (day.orders, day.fills, day.gap_outcomes) == (3, 4, 16)

    # The unmatched market of the day is quoted and priced like any other, and is never traded:
    # a market the matcher could not confidently tie to a game has no fair value to trade
    # against (§6.4), so it draws no order at all.
    unmatched = [m.id for m in db_session.query(VenueMarket).all()
                 if m.match_status != "matched"]
    assert unmatched, "the day is meant to carry a market the matcher does not confidently match"
    assert db_session.query(Order).filter(Order.venue_market_id.in_(unmatched)).count() == 0


def test_fixture_day_says_it_is_synthetic():
    """The day is synthesised, not recorded, and its README has to say so where a reader looks."""
    readme = (DAY / "README.md").read_text()
    assert "synthes" in readme.lower()


# --- export-fixture -------------------------------------------------------------------


def test_export_fixture_out_dash(monkeypatch, env_settings, db_session, two_runs):
    """`--out -` puts the whole document on stdout, in both kinds."""
    import os

    from harness.cli import app
    from harness.config.settings import get_settings

    game, run_a, run_b = two_runs
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["export-fixture", "--kind", "day",
                                     "--from-run", str(run_a.id), "--to-run", str(run_b.id),
                                     "--out", "-"])
        assert result.exit_code == 0, result.output
        doc = json.loads(result.stdout)
        assert doc["kind"] == "day"
        assert doc["meta"]["from_run"] == run_a.id and doc["meta"]["to_run"] == run_b.id
        assert [r["id"] for r in doc["meta"]["runs"]] == [run_a.id, run_b.id]
        assert doc["counts"]["orderbook_events"] == len(doc["orderbook_events"]) > 0
        assert doc["counts"]["venue_trades"] == len(doc["venue_trades"]) > 0
        assert {e["ticker"] for e in doc["orderbook_events"]} == {T2, T3}

        window = doc["meta"]["window"]
        tape = runner.invoke(app, ["export-fixture", "--kind", "ws-tape", "--ticker", T2,
                                   "--from", window["lower"], "--to", window["upper"],
                                   "--out", "-"])
        assert tape.exit_code == 0, tape.output
        tape_doc = json.loads(tape.stdout)
        assert tape_doc["kind"] == "ws-tape" and tape_doc["ticker"] == T2
        assert tape_doc["snapshot"] is not None
        assert tape_doc["counts"]["prints"] == len(tape_doc["prints"]) == 1
    finally:
        get_settings.cache_clear()


def test_export_fixture_rejects_an_empty_or_backwards_range(monkeypatch, env_settings,
                                                            db_session, two_runs):
    """A range with no runs, or one that runs backwards, exits 1 rather than writing `[]`."""
    import os

    from harness.cli import app
    from harness.config.settings import get_settings

    game, run_a, run_b = two_runs
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        empty = runner.invoke(app, ["export-fixture", "--kind", "day", "--from-run", "9000",
                                    "--to-run", "9001", "--out", "-"])
        assert empty.exit_code == 1
        backwards = runner.invoke(app, ["export-fixture", "--kind", "day", "--from-run",
                                        str(run_b.id), "--to-run", str(run_a.id), "--out", "-"])
        assert backwards.exit_code == 1
    finally:
        get_settings.cache_clear()
