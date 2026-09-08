"""The settlement job: pure resolution, the shared budget, the stage registry and the settler.

`resolve_market`, `payout_for_side` and `Budget` are pure and are tested without a database.
Everything else runs against the real schema, because every guarantee the job makes -- one
ledger row per fill, one savepoint per game, a status update that cannot resurrect a cancelled
order -- is a property of the SQL, not of the Python around it.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import (
    Fill,
    Game,
    JobRun,
    Ledger,
    Order,
    RawResponse,
    Run,
    Settlement,
    VenueMarket,
    VenueSettlement,
)
from harness.feeds.http import HttpClient
from harness.settlement import job as job_module
from harness.settlement import settle as settle_module
from harness.settlement.job import Budget, Settler, StageResult, load_stages, register_stage
from harness.settlement.settle import (
    payout_for_side,
    resolve_market,
    run_settlement,
    run_venue_result,
    stale_unsettled,
)
from harness.venues.kalshi.public import KalshiPublic

#: Inside the weekly partitions `conftest._schema` builds, so a `raw_responses` insert lands.
NOW = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
HOME, AWAY = 14, 19
ONE, HALF, ZERO = Decimal("1"), Decimal("0.5"), Decimal("0")


class Mono:
    """A monotonic clock the test steps by hand. The last value repeats forever."""

    def __init__(self, *values: float) -> None:
        self.values = list(values) or [0.0]
        self.i = 0

    def __call__(self) -> float:
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return value


def _ctx(kalshi=None) -> dict:
    return {"errors": [], "warnings": [], "kalshi": kalshi}


def _game(session, home_score=24, away_score=21, status="final", kickoff=None) -> Game:
    g = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=kickoff or NOW - timedelta(hours=4), status=status,
             home_score=home_score, away_score=away_score)
    session.add(g)
    session.flush()
    return g


def _market(session, game_id, ticker, market_type, threshold=None, side_team_id=None,
            side=None, match_status="matched") -> VenueMarket:
    m = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME-26SEP12HOMEAWAY",
                    series_ticker="KXNFLGAME", game_id=game_id, market_type=market_type,
                    threshold=None if threshold is None else Decimal(str(threshold)),
                    side_team_id=side_team_id, side=side,
                    match_confidence=Decimal("1.00"), match_status=match_status,
                    first_seen_raw_id=1, last_seen_at=NOW)
    session.add(m)
    session.flush()
    return m


def _order(session, market, variant="v1", side="yes", prob="0.40", contracts="10.00",
           status="filled", replay=False, venue="kalshi") -> Order:
    o = Order(intent_id=uuid.uuid4(), variant_id=variant, venue=venue, mode="paper",
              client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
              venue_market_id=market.id, side=side, prob=Decimal(prob),
              contracts=Decimal(contracts), status=status, placed_at=NOW - timedelta(hours=6),
              game_id=market.game_id, replay=replay)
    session.add(o)
    session.flush()
    return o


def _fill(session, order, contracts="10.00", prob=None, method="queue_model",
          trade_id=None) -> Fill:
    f = Fill(order_id=order.id, prob=Decimal(prob) if prob else order.prob,
             contracts=Decimal(contracts), fee=Decimal("0.0000"),
             filled_at=NOW - timedelta(hours=5), fill_method=method,
             source_trade_id=trade_id or f"tr-{uuid.uuid4()}", replay=order.replay)
    session.add(f)
    session.flush()
    return f


def _derived(session, ticker, result="yes", payout=ONE) -> VenueSettlement:
    row = VenueSettlement(venue="kalshi", ticker=ticker, source="derived", result=result,
                          payout=payout, settled_at=NOW - timedelta(hours=2))
    session.add(row)
    session.flush()
    return row


def _settled_page(session, run_id, markets, fetched_at) -> RawResponse:
    row = RawResponse(run_id=run_id, fetched_at=fetched_at, source="kalshi", endpoint="/markets",
                      params={"series_ticker": "KXNFLGAME", "status": "settled"},
                      http_status=200, body={"cursor": "", "markets": markets})
    session.add(row)
    session.flush()
    return row


def _run_row(session) -> Run:
    run = Run(started_at=NOW - timedelta(hours=1), status="ok")
    session.add(run)
    session.flush()
    return run


# --- pure ------------------------------------------------------------------------------


def test_resolve_market_table():
    # Moneyline: 1 for the side team's win, 0 for its loss, 0.5 for a tie.
    assert resolve_market("moneyline", None, HOME, HOME, AWAY, 24, 21) == ONE
    assert resolve_market("moneyline", None, AWAY, HOME, AWAY, 24, 21) == ZERO
    assert resolve_market("moneyline", None, HOME, HOME, AWAY, 21, 21) == HALF
    assert resolve_market("moneyline", None, AWAY, HOME, AWAY, 21, 21) == HALF

    # Spread for team T at k.5 pays 1 when T's margin beats k.5, both signs of k.
    assert resolve_market("spread", Decimal("2.5"), HOME, HOME, AWAY, 24, 21) == ONE
    assert resolve_market("spread", Decimal("3.5"), HOME, HOME, AWAY, 24, 21) == ZERO
    assert resolve_market("spread", Decimal("-3.5"), AWAY, HOME, AWAY, 24, 21) == ONE
    assert resolve_market("spread", Decimal("-2.5"), AWAY, HOME, AWAY, 24, 21) == ZERO

    # Total over k.5 pays 1 when home + away beats k.5.
    assert resolve_market("total", Decimal("44.5"), None, HOME, AWAY, 24, 21) == ONE
    assert resolve_market("total", Decimal("45.5"), None, HOME, AWAY, 24, 21) == ZERO

    with pytest.raises(ValueError):
        resolve_market("touchdowns", None, HOME, HOME, AWAY, 24, 21)


def test_payout_for_no_side():
    assert payout_for_side(ONE, "no") == ZERO
    assert payout_for_side(ZERO, "no") == ONE
    assert payout_for_side(HALF, "no") == HALF
    assert payout_for_side(ONE, "yes") == ONE
    with pytest.raises(ValueError):
        payout_for_side(ONE, "maybe")


def test_budget_ok_and_remaining():
    budget = Budget(60, Mono(0.0, 10.0, 100.0))
    assert budget.ok() is True
    assert budget.remaining_s() == pytest.approx(-40.0)


# --- settlement ------------------------------------------------------------------------


def _four_markets(db_session, game):
    return [
        _market(db_session, game.id, "T-ML-HOME", "moneyline", side_team_id=HOME),
        _market(db_session, game.id, "T-ML-AWAY", "moneyline", side_team_id=AWAY),
        _market(db_session, game.id, "T-SPR-HOME", "spread", threshold="2.5", side_team_id=HOME),
        _market(db_session, game.id, "T-TOT", "total", threshold="44.5", side="over"),
    ]


def test_settlement_is_idempotent(db_session):
    game = _game(db_session)
    markets = _four_markets(db_session, game)
    order = _order(db_session, markets[0])
    _fill(db_session, order)
    db_session.commit()

    first = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), _ctx())
    assert (first.games, first.markets, first.orders, first.ledger_rows) == (1, 4, 1, 1)

    second = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), _ctx())
    # Only rows this pass actually wrote move a counter, so a re-run moves nothing.
    assert (second.games, second.markets, second.orders, second.ledger_rows) == (0, 0, 0, 0)

    assert db_session.query(Settlement).count() == 1
    rows = {r.ticker: r for r in db_session.query(VenueSettlement).all()}
    assert len(rows) == 4
    assert (rows["T-ML-HOME"].result, rows["T-ML-HOME"].payout) == ("yes", ONE)
    assert (rows["T-ML-AWAY"].result, rows["T-ML-AWAY"].payout) == ("no", ZERO)
    assert rows["T-SPR-HOME"].result == "yes"
    assert rows["T-TOT"].result == "yes"
    assert all(r.source == "derived" for r in rows.values())
    assert db_session.query(Ledger).filter(Ledger.kind == "settlement").count() == 1


def test_settlement_records_a_tie_and_pays_both_sides_half(db_session):
    game = _game(db_session, home_score=21, away_score=21)
    market = _market(db_session, game.id, "T-ML-HOME", "moneyline", side_team_id=HOME)
    order = _order(db_session, market, side="no", prob="0.60", contracts="8.00")
    _fill(db_session, order, contracts="8.00")
    db_session.commit()

    run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), _ctx())

    row = db_session.query(VenueSettlement).one()
    assert (row.result, row.payout) == ("tie", HALF)
    ledger = db_session.query(Ledger).one()
    assert ledger.payout == HALF and ledger.cash_delta == Decimal("4.00")


def test_ledger_cash_delta_per_fill_and_restricted_status_update(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME", "moneyline", side_team_id=HOME)
    filled = _order(db_session, market, variant="v1", side="yes", contracts="10.00",
                    status="filled")
    partial = _order(db_session, market, variant="v2", side="no", contracts="6.00",
                     status="partially_filled")
    cancelled = _order(db_session, market, variant="v3", side="yes", contracts="4.00",
                       status="cancelled")
    open_order = _order(db_session, market, variant="v4", side="yes", contracts="4.00",
                        status="open")
    _fill(db_session, filled, contracts="6.00", trade_id="a")
    _fill(db_session, filled, contracts="4.00", trade_id="b")
    _fill(db_session, partial, contracts="3.00", trade_id="c")
    _fill(db_session, cancelled, contracts="2.00", trade_id="d")
    # A snapshot_cross fill is a counterfactual, not a position: it gets no ledger row.
    _fill(db_session, open_order, contracts="4.00", method="snapshot_cross", trade_id="e")
    db_session.commit()

    counts = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), _ctx())
    assert counts.orders == 2  # filled and partially_filled moved; cancelled did not

    rows = {(r.order_id, r.fill_id): r for r in db_session.query(Ledger).all()}
    assert len(rows) == 4
    by_order = {}
    for (order_id, _), row in rows.items():
        by_order.setdefault(order_id, []).append(row)
    # HOME won, so YES pays 1 per contract and NO pays 0.
    assert sorted(r.cash_delta for r in by_order[filled.id]) == [Decimal("4.00"), Decimal("6.00")]
    assert [r.cash_delta for r in by_order[partial.id]] == [Decimal("0.00")]
    assert [r.cash_delta for r in by_order[cancelled.id]] == [Decimal("2.00")]
    assert all(r.kind == "settlement" and r.replay is False for r in rows.values())

    db_session.expire_all()
    assert db_session.get(Order, filled.id).status == "settled"
    assert db_session.get(Order, partial.id).status == "settled"
    assert db_session.get(Order, cancelled.id).status == "cancelled"
    assert db_session.get(Order, open_order.id).status == "open"


def test_replay_order_settles_without_a_ledger_row(db_session):
    """The ledger is the live record: only a non-replay fill takes cash (brief, Task 7 fix 1).

    The status update is a separate statement over the ticker, so a replay order still reaches
    `settled` and leaves the replay executor's positions instead of stranding there forever.
    The `venue` predicate on both statements is asserted here too: an order on another venue's
    book that happens to share a ticker is not this market's order.
    """
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME", "moneyline", side_team_id=HOME)
    live = _order(db_session, market, variant="v1", contracts="5.00")
    shadow = _order(db_session, market, variant="v1", contracts="5.00", replay=True)
    other_venue = _order(db_session, market, variant="v1", contracts="5.00", venue="novig")
    _fill(db_session, live, contracts="5.00", trade_id="a")
    _fill(db_session, shadow, contracts="5.00", trade_id="b")
    _fill(db_session, other_venue, contracts="5.00", trade_id="c")
    db_session.commit()

    counts = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), _ctx())

    rows = db_session.query(Ledger).all()
    assert [(r.order_id, r.replay) for r in rows] == [(live.id, False)]
    assert counts.ledger_rows == 1
    # Both kalshi orders settle; the other venue's order is not this market's business.
    assert counts.orders == 2
    db_session.expire_all()
    assert db_session.get(Order, live.id).status == "settled"
    assert db_session.get(Order, shadow.id).status == "settled"
    assert db_session.get(Order, other_venue.id).status == "filled"
    live_total = db_session.execute(text(
        "select coalesce(sum(cash_delta), 0) from ledger where replay = false")).scalar()
    assert live_total == Decimal("5.00")


def test_unknown_market_type_fails_its_game_loudly(db_session):
    """Nothing is filtered out of the record silently: a shape this harness cannot resolve takes
    its own game down with an error rather than leaving its fills quietly unpaid."""
    odd = _game(db_session)
    good = _game(db_session)
    _market(db_session, odd.id, "T-ODD", "firsthalf", threshold="20.5", side_team_id=HOME)
    _market(db_session, odd.id, "T-ODD-ML", "moneyline", side_team_id=HOME)
    _market(db_session, good.id, "T-GOOD", "moneyline", side_team_id=HOME)
    db_session.commit()

    ctx = _ctx()
    counts = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), ctx)

    assert counts.games == 1
    assert {r.ticker for r in db_session.query(VenueSettlement).all()} == {"T-GOOD"}
    assert len(ctx["errors"]) == 1 and "firsthalf" in repr(ctx["errors"][0])


def test_budget_stops_between_games_and_resumes(db_session):
    first = _game(db_session)
    second = _game(db_session)
    _market(db_session, first.id, "T-A", "moneyline", side_team_id=HOME)
    _market(db_session, second.id, "T-B", "moneyline", side_team_id=HOME)
    db_session.commit()

    counts = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0, 0.0, 100.0)), _ctx())
    assert counts.games == 1 and counts.budget_exhausted is True
    assert db_session.query(Settlement).count() == 1

    resumed = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), _ctx())
    assert resumed.games == 1 and resumed.budget_exhausted is False
    assert {r.game_id for r in db_session.query(Settlement).all()} == {first.id, second.id}


def test_nested_rollback_isolates_a_bad_game(db_session):
    bad = _game(db_session)
    good = _game(db_session)
    # A spread with no threshold cannot be resolved; it must take its own game down, not the pass.
    _market(db_session, bad.id, "T-BAD-SPR", "spread", threshold=None, side_team_id=HOME)
    _market(db_session, bad.id, "T-BAD-ML", "moneyline", side_team_id=HOME)
    _market(db_session, good.id, "T-GOOD", "moneyline", side_team_id=HOME)
    db_session.commit()

    ctx = _ctx()
    counts = run_settlement(db_session, NOW, None, Budget(60, Mono(0.0)), ctx)

    assert counts.games == 1
    assert [r.game_id for r in db_session.query(Settlement).all()] == [good.id]
    assert {r.ticker for r in db_session.query(VenueSettlement).all()} == {"T-GOOD"}
    assert len(ctx["errors"]) == 1 and str(bad.id) in repr(ctx["errors"][0])


def test_stale_unsettled_count(db_session):
    old = _game(db_session, status="in_progress", home_score=None, away_score=None,
                kickoff=NOW - timedelta(hours=100))
    recent = _game(db_session, kickoff=NOW - timedelta(hours=10))
    done = _game(db_session, kickoff=NOW - timedelta(hours=100))
    for game in (old, recent, done):
        market = _market(db_session, game.id, f"T-{game.id}", "moneyline", side_team_id=HOME)
        _order(db_session, market)
    _order(db_session, _market(db_session, old.id, "T-OLD-REPLAY", "moneyline",
                               side_team_id=HOME), replay=True)
    db_session.add(Settlement(game_id=done.id, home_score=24, away_score=21, source="espn",
                              settled_at=NOW))
    db_session.commit()

    assert stale_unsettled(db_session, NOW) == 1


# --- the venue's own result ------------------------------------------------------------


def test_venue_result_from_settled_body_and_mismatch_recorded(db_session):
    run = _run_row(db_session)
    _derived(db_session, "T-ML-HOME", result="yes", payout=ONE)
    _derived(db_session, "T-ML-AWAY", result="no", payout=ZERO)
    _settled_page(db_session, run.id, [{"ticker": "T-ML-HOME", "result": "yes"}],
                  NOW - timedelta(hours=6))
    newest = _settled_page(db_session, run.id,
                           [{"ticker": "T-ML-HOME", "result": "no"},
                            {"ticker": "T-ML-AWAY", "result": "no"}],
                           NOW - timedelta(hours=1))
    newest_id = newest.id
    db_session.commit()

    ctx = _ctx()
    written = run_venue_result(db_session, NOW, None, Budget(60, Mono(0.0)), ctx)

    assert written == 2
    rows = {r.ticker: r for r in db_session.query(VenueSettlement)
            .filter(VenueSettlement.source == "venue").all()}
    assert rows["T-ML-HOME"].result == "no" and rows["T-ML-HOME"].payout == ZERO
    assert rows["T-ML-HOME"].raw_id == newest_id
    assert rows["T-ML-AWAY"].result == "no"
    assert ctx["warnings"] == [{"settlement_mismatch": "T-ML-HOME"}]

    # A second pass has nothing left to write and repeats no warning.
    ctx2 = _ctx()
    assert run_venue_result(db_session, NOW, None, Budget(60, Mono(0.0)), ctx2) == 0
    assert ctx2["warnings"] == []


@respx.mock
def test_venue_row_absent_while_result_empty_then_written_on_retry(db_session):
    run = _run_row(db_session)
    _derived(db_session, "T-ML-HOME", result="yes", payout=ONE)
    db_session.commit()

    route = respx.get("https://k/markets/T-ML-HOME").mock(side_effect=[
        httpx.Response(200, json={"market": {"ticker": "T-ML-HOME", "result": ""}}),
        httpx.Response(200, json={"market": {"ticker": "T-ML-HOME", "result": "yes"}}),
    ])
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: NOW - timedelta(minutes=5))
    kalshi = KalshiPublic(http, "https://k", sleep_s=0, sleep=lambda s: None)

    ctx = _ctx(kalshi)
    assert run_venue_result(db_session, NOW, kalshi, Budget(60, Mono(0.0)), ctx) == 0
    assert db_session.query(VenueSettlement).filter(VenueSettlement.source == "venue").count() == 0
    # The empty body is still recorded, so the fetch is auditable.
    assert db_session.query(RawResponse).filter(
        RawResponse.endpoint == "/markets/T-ML-HOME").count() == 1

    assert run_venue_result(db_session, NOW, kalshi, Budget(60, Mono(0.0)), ctx) == 1
    row = db_session.query(VenueSettlement).filter(VenueSettlement.source == "venue").one()
    assert (row.result, row.payout) == ("yes", ONE)
    assert row.raw_id is not None
    assert ctx["warnings"] == []
    assert len(route.calls) == 2


def test_unexpected_venue_result_is_skipped_and_the_next_ticker_still_settles(db_session):
    """`venue_settlements.result` is a 4-character column and Kalshi's `result` is free text, so
    anything outside yes/no/tie is recorded as a warning and written nowhere -- and it must not
    cost every ticker ordered after it its own venue row."""
    run = _run_row(db_session)
    _derived(db_session, "T-A-BAD", result="yes", payout=ONE)
    _derived(db_session, "T-B-GOOD", result="yes", payout=ONE)
    _settled_page(db_session, run.id,
                  [{"ticker": "T-A-BAD", "result": "cancelled_by_exchange"},
                   {"ticker": "T-B-GOOD", "result": "yes"}],
                  NOW - timedelta(hours=1))
    db_session.commit()

    ctx = _ctx()
    assert run_venue_result(db_session, NOW, None, Budget(60, Mono(0.0)), ctx) == 1

    rows = {r.ticker for r in db_session.query(VenueSettlement)
            .filter(VenueSettlement.source == "venue").all()}
    assert rows == {"T-B-GOOD"}
    assert ctx["warnings"] == [
        {"unexpected_venue_result": "T-A-BAD", "result": "cancelled_by_exc"}]
    # An unusable result is never a settlement mismatch: we do not know what the venue said.
    assert ctx["errors"] == []


def test_void_gets_a_venue_row_and_counts_as_a_mismatch(db_session):
    """A voided market is a settled market as far as the venue is concerned, so it must get its
    `source = venue` row: without one it can never satisfy the 48 h "derived rows have a venue
    row" invariant or gate criterion 9. It also disagrees with our derived result by definition
    -- the ledger paid on a market the venue refused to settle -- so it is a mismatch.
    """
    run = _run_row(db_session)
    _derived(db_session, "T-VOID", result="yes", payout=ONE)
    _settled_page(db_session, run.id, [{"ticker": "T-VOID", "result": "void"}],
                  NOW - timedelta(hours=1))
    db_session.commit()

    ctx = _ctx()
    assert run_venue_result(db_session, NOW, None, Budget(60, Mono(0.0)), ctx) == 1

    row = db_session.query(VenueSettlement).filter(VenueSettlement.source == "venue").one()
    assert row.result == "void"
    assert row.payout is None
    assert ctx["warnings"] == [{"settlement_mismatch": "T-VOID"}]
    assert ctx["errors"] == []


def test_venue_result_isolates_a_failing_ticker(db_session, monkeypatch):
    """One ticker that raises must not abort the stage: `_PENDING_VENUE_ROWS` is ordered by
    ticker, so without a savepoint the first bad ticker would block every later one forever."""
    run = _run_row(db_session)
    _derived(db_session, "T-A-BOOM", result="no", payout=ZERO)
    _derived(db_session, "T-B-GOOD", result="yes", payout=ONE)
    _settled_page(db_session, run.id,
                  [{"ticker": "T-A-BOOM", "result": "no"}, {"ticker": "T-B-GOOD", "result": "yes"}],
                  NOW - timedelta(hours=1))
    db_session.commit()

    real = settle_module._venue_payout

    def only_yes(result):
        if result == "no":
            raise RuntimeError("venue payout exploded")
        return real(result)

    monkeypatch.setattr(settle_module, "_venue_payout", only_yes)

    ctx = _ctx()
    written = run_venue_result(db_session, NOW, None, Budget(60, Mono(0.0)), ctx)

    assert written == 1
    assert [r.ticker for r in db_session.query(VenueSettlement)
            .filter(VenueSettlement.source == "venue").all()] == ["T-B-GOOD"]
    assert len(ctx["errors"]) == 1 and "T-A-BOOM" in repr(ctx["errors"][0])


# --- the stage registry and the settler ------------------------------------------------


def test_stage_registry_runs_stages_in_registration_order_under_one_budget(
        db_session, env_settings, monkeypatch):
    seen: list[tuple[str, float]] = []
    monkeypatch.setattr(job_module, "STAGES", [])
    monkeypatch.setattr(job_module, "STAGE_MODULES", [])

    def first(session, now, budget):
        seen.append(("first", budget.remaining_s()))
        return StageResult("first", {"n": 1}, False, None)

    def boom(session, now, budget):
        seen.append(("boom", budget.remaining_s()))
        raise RuntimeError("stage exploded")

    def last(session, now, budget):
        seen.append(("last", budget.remaining_s()))
        return StageResult("last", {"n": 3}, True, None)

    register_stage("first", first)
    register_stage("boom", boom)
    register_stage("last", last)
    assert [name for name, _ in load_stages()] == ["first", "boom", "last"]

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    settler = Settler(env_settings, factory, None, clock=lambda: NOW,
                      monotonic=Mono(0.0, 0.0, 5.0, 10.0, 20.0))
    row = settler.run()

    assert [name for name, _ in seen] == ["first", "boom", "last"]
    # One shared budget: each later stage sees strictly less of it than the one before.
    assert seen[0][1] > seen[1][1] > seen[2][1]
    stages = row.notes["stages"]
    assert [s["name"] for s in stages] == ["first", "boom", "last"]
    assert stages[0]["error"] is None and stages[0]["counts"] == {"n": 1}
    assert "RuntimeError" in stages[1]["error"] and stages[1]["counts"] == {}
    assert stages[2]["counts"] == {"n": 3}
    assert row.status == "degraded"
    assert row.budget_exhausted is True


def test_settler_writes_job_runs_and_never_touches_runs_notes(db_session, env_settings):
    run = Run(started_at=NOW - timedelta(hours=1), status="ok", notes={"tick": "mine"})
    db_session.add(run)
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME", "moneyline", side_team_id=HOME)
    _fill(db_session, _order(db_session, market))
    db_session.commit()

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    row = Settler(env_settings, factory, None, clock=lambda: NOW, monotonic=Mono(0.0)).run()

    assert row.job == "settle" and row.status == "ok"
    assert row.started_at is not None and row.finished_at is not None
    # Task 8 folded four more stages (benchmarks, result_benchmarks, gap_outcomes_drain,
    # order_clv) into the shared registry every settlement run now iterates; this run's own
    # game and order pass cleanly through all of them (no errors, no warnings), so only
    # `settle`'s and `venue_result`'s own presence and relative order are pinned here.
    stages = {s["name"]: s for s in row.notes["stages"]}
    names = [s["name"] for s in row.notes["stages"]]
    assert {"settle", "venue_result"} <= set(names)
    assert names.index("settle") < names.index("venue_result")
    assert stages["settle"]["counts"]["games"] == 1
    assert row.notes["stale_unsettled"] == 0
    assert row.notes["warnings"] == [] and row.notes["errors"] == []

    db_session.expire_all()
    assert db_session.get(Run, run.id).notes == {"tick": "mine"}
    assert db_session.query(JobRun).count() == 1
    assert db_session.query(Settlement).count() == 1


def test_load_stages_twice_registers_each_stage_once(monkeypatch):
    """Ruling 4: `load_stages()` is idempotent, and so is `register_stage` on a repeated name."""
    first = [name for name, _ in load_stages()]
    second = [name for name, _ in load_stages()]
    assert first == second
    assert first.count("settle") == 1 and first.count("venue_result") == 1

    # The guard underneath it: a module re-imported (or a stage registered twice by hand)
    # cannot append a second copy.
    monkeypatch.setattr(job_module, "STAGES", [])

    def fake(session, now, budget):
        return StageResult("fake", {}, False, None)

    register_stage("fake", fake)
    register_stage("fake", fake)
    assert [name for name, _ in job_module.STAGES] == ["fake"]


def test_settler_reports_degraded_when_a_game_fails(db_session, env_settings):
    """A pass that recorded game errors must not read `ok`: `verify.md` switches on that column
    and nothing automated reads `notes`."""
    bad = _game(db_session)
    good = _game(db_session)
    _market(db_session, bad.id, "T-BAD-SPR", "spread", threshold=None, side_team_id=HOME)
    _market(db_session, good.id, "T-GOOD", "moneyline", side_team_id=HOME)
    db_session.commit()

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    row = Settler(env_settings, factory, None, clock=lambda: NOW, monotonic=Mono(0.0)).run()

    assert row.status == "degraded"
    assert row.notes["stages"][0]["error"] is None  # the stage itself did not raise
    assert len(row.notes["errors"]) == 1
    db_session.expire_all()
    assert [r.game_id for r in db_session.query(Settlement).all()] == [good.id]
