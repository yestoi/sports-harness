"""The stored go-live gate report: the pre-registered criteria as data, one row per exec
variant, and `harness gate`.

Every fixture here lands before `NOW`, the evaluation instant every test passes to
`evaluate_gate`/`evaluate_all`. The gate window is everything up to that instant (phase 3's
whole paper run), not one ISO week -- the criteria count 150 fills across 40 games, which no
single week produces.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.models import (
    Benchmark,
    FairValue,
    Fill,
    Game,
    GapOutcome,
    GateReport,
    MarketGapSnapshot,
    Markout,
    Order,
    OrderClv,
    Signal,
    StrategyVariant,
    VenueMarket,
    VenueSettlement,
)
from harness.report import gate as gate_mod
from harness.report.gate import (
    CRITERIA,
    INSUFFICIENT,
    Criterion,
    criteria_hash,
    evaluate_all,
    evaluate_gate,
)

runner = CliRunner()

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
PLACED = NOW - timedelta(days=1)
PRIMARY, SECOND = "p00000000001", "s00000000001"
HOME, AWAY = 14, 19
#: The seven gated benchmark types, pinned here so a change to the module's own tuple has to
#: change this test too (R1: the families are an invariant).
GATED = ("pinnacle_t5", "consensus_t5", "consensus_t60", "consensus_t180", "kalshi_mid_t5",
         "kalshi_last_trade_pre_kick", "novig_devig_t5")

_ids = iter(range(1, 1_000_000))


# --- fixtures and seeding -------------------------------------------------------------------


@pytest.fixture
def cli_settings(monkeypatch, db_session):
    """Point `harness.cli`'s own engine at the database `db_session` uses (tests/test_cli.py)."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _variant(session, variant_id, name, tier, active=True) -> StrategyVariant:
    row = StrategyVariant(variant_id=variant_id, name=name, tier=tier, config_json={},
                          registered_at=PLACED, active=active)
    session.add(row)
    session.flush()
    return row


def _game(session, sport="nfl", kickoff=None) -> Game:
    row = Game(sport=sport, home_team_id=HOME, away_team_id=AWAY,
               kickoff_utc=kickoff or PLACED + timedelta(hours=6), status="final")
    session.add(row)
    session.flush()
    return row


def _market(session, game, match_key="mk", ticker=None) -> VenueMarket:
    row = VenueMarket(venue="kalshi", ticker=ticker or f"KXNFLGAME-{next(_ids)}",
                      event_ticker="KXNFLGAME-EVT", series_ticker="KXNFLGAME",
                      game_id=game.id, market_type="moneyline", side_team_id=HOME,
                      match_confidence=Decimal("1.00"), match_status="matched",
                      match_key=match_key, first_seen_raw_id=1, last_seen_at=PLACED)
    session.add(row)
    session.flush()
    return row


def _order(session, market, variant_id, side="yes", book_source="ws", dirty_minutes=0,
           bid="0.4800", ask="0.5000", fair_p_at_place="0.5500", match_key="mk",
           replay=False, placed_at=None, cancel_reason=None, cancelled_at=None,
           prob="0.5000", sport="nfl") -> Order:
    row = Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi", mode="paper",
                client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
                venue_market_id=market.id, side=side, prob=Decimal(prob),
                contracts=Decimal("10.00"), status="filled", placed_at=placed_at or PLACED,
                cancel_reason=cancel_reason, cancelled_at=cancelled_at,
                fair_p_at_place=Decimal(fair_p_at_place),
                venue_bid_at_place=Decimal(bid), venue_ask_at_place=Decimal(ask),
                book_source=book_source, dirty_minutes=dirty_minutes,
                game_id=market.game_id, sport=sport, match_key=match_key, replay=replay)
    session.add(row)
    session.flush()
    return row


def _fill(session, order, fill_method="queue_model", replay=False) -> Fill:
    row = Fill(order_id=order.id, prob=order.prob, contracts=Decimal("10.00"),
               fee=Decimal("0.2500"), filled_at=order.placed_at + timedelta(minutes=2),
               fill_method=fill_method, tape_source="ws", through=False, has_print=True,
               replay=replay)
    session.add(row)
    session.flush()
    return row


def _clv(session, order, benchmark_type="pinnacle_t5", clv_p_net="0.0200",
         stale=False) -> OrderClv:
    row = OrderClv(order_id=order.id, benchmark_type=benchmark_type, p_bench=Decimal("0.5600"),
                   p_used=order.prob, p_used_kind="order", clv_p=Decimal("0.0250"),
                   clv_p_net=Decimal(clv_p_net), clv_roi_net=Decimal("0.0400"), stale=stale)
    session.add(row)
    session.flush()
    return row


def _markout(session, order, anchor="nw_fill", horizon="30m", fair_p="0.5600",
             p_used="0.5000", fee="0.0025", fair_changed=True) -> Markout:
    row = Markout(order_id=order.id, anchor=anchor, horizon=horizon, at_ts=order.placed_at,
                  horizon_ts=order.placed_at + timedelta(minutes=30),
                  p_used=None if p_used is None else Decimal(p_used),
                  fee_per_contract=None if fee is None else Decimal(fee),
                  fair_p=None if fair_p is None else Decimal(fair_p),
                  fair_changed=fair_changed, venue_mid=Decimal("0.5200"), source="quote")
    session.add(row)
    session.flush()
    return row


def _signal(session, market, variant_id, staleness_s=60, decision="candidate", replay=False,
            fair_value=True) -> Signal:
    run_id = next(_ids)
    fair = None
    if fair_value:
        fair = FairValue(run_id=run_id, game_id=market.game_id, market_type="moneyline",
                         outcome_team_id=HOME, fair_p=Decimal("0.5500"), fair_source="direct",
                         n_groups=2, staleness_s=staleness_s, created_at=PLACED)
        session.add(fair)
        session.flush()
    gap = MarketGapSnapshot(run_id=run_id, venue_market_id=market.id,
                            fair_value_id=None if fair is None else fair.id,
                            fair_source="direct", fair_p=Decimal("0.5500"),
                            venue_mid=Decimal("0.5000"), n_groups=2, ttk_minutes=120,
                            dow=3, hour_ct=13, created_at=PLACED)
    session.add(gap)
    session.flush()
    row = Signal(run_id=run_id, variant_id=variant_id, gap_snapshot_id=gap.id,
                 venue_market_id=market.id, side="yes", price_target=Decimal("0.5000"),
                 decision=decision, labels={}, replay=replay, created_at=PLACED)
    session.add(row)
    session.flush()
    return row


def _settled(session, market, derived="yes", venue="yes") -> None:
    for source, result in (("derived", derived), ("venue", venue)):
        if result is None:
            continue
        session.add(VenueSettlement(venue="kalshi", ticker=market.ticker, source=source,
                                    result=result, payout=Decimal("1.00"), settled_at=PLACED))
    session.flush()


def _seed_passing(session, variant_id=PRIMARY, games=40, filled_per_game=4):
    """Enough rows for every numeric criterion to pass for one variant.

    40 games (half NFL, half NCAAF) x 4 filled orders is 160 fill events across 40 game
    clusters with both sports present; one unfilled order per game gives criterion 6 its 40
    unfilled clusters. Values alternate by game so the cluster-robust SE is not degenerate --
    a constant sample has no between-cluster variation and answers `nan` for `t`.
    """
    markets = []
    for i in range(games):
        sport = "nfl" if i % 2 == 0 else "ncaaf"
        game = _game(session, sport=sport)
        # NCAAF books are seeded wide, so only the NFL half is marquee: the share is 0.5, which
        # proves NFL counts unconditionally and a wide NCAAF book does not.
        ask = "0.5000" if i % 2 == 0 else "0.6000"
        market = _market(session, game)
        markets.append(market)
        clv = "0.0150" if i % 2 == 0 else "0.0250"
        fair_30m = "0.5550" if i % 2 == 0 else "0.5650"
        fair_0m = "0.5510" if i % 2 == 0 else "0.5490"
        for _ in range(filled_per_game):
            order = _order(session, market, variant_id, ask=ask, sport=sport)
            _fill(session, order)
            for benchmark in GATED:
                _clv(session, order, benchmark, clv_p_net=clv)
            _markout(session, order, "nw_fill", "30m", fair_p=fair_30m)
            _markout(session, order, "nw_fill", "0m", fair_p=fair_0m)
        unfilled = _order(session, market, variant_id, ask=ask, sport=sport)
        _clv(session, unfilled, "pinnacle_t5", clv_p_net="0.0200")
        _signal(session, market, variant_id)
        _settled(session, market)
    session.flush()
    return markets


# --- the criteria as data -------------------------------------------------------------------


def test_empty_db_fails_every_criterion_and_stores_rows_per_variant(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECOND, "sharp_two_sided", "secondary")

    results = evaluate_all(db_session, NOW, [PRIMARY, SECOND], "sharp_direct")

    assert [r.variant_id for r in results] == [PRIMARY, SECOND]
    for result in results:
        assert result.passed is False
        assert set(result.criteria) == {c.name for c in CRITERIA}
        failed = [name for name, r in result.criteria.items() if r.passed]
        assert failed == [], failed
    rows = db_session.query(GateReport).order_by(GateReport.variant_id).all()
    assert [r.variant_id for r in rows] == [PRIMARY, SECOND]
    assert [r.passed for r in rows] == [False, False]
    assert {r.criteria_hash for r in rows} == {criteria_hash()}
    assert set(rows[0].criteria_json) == {c.name for c in CRITERIA}


def test_seeded_numeric_criteria_pass_but_manual_items_fail_overall(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _seed_passing(db_session)

    result = evaluate_gate(db_session, NOW, PRIMARY)

    numeric = [name for name in result.criteria
               if name not in ("legal_decision", "live_trading_env")]
    failed = [name for name in numeric if not result.criteria[name].passed]
    assert failed == [], {n: result.criteria[n] for n in failed}
    assert result.criteria["legal_decision"].passed is False
    assert result.criteria["live_trading_env"].passed is False
    assert result.passed is False
    assert result.criteria["fill_events"].value == 160
    assert result.criteria["fill_events"].n_clusters == 40
    assert result.criteria["marquee_share"].value == pytest.approx(0.5)


def test_hash_changes_when_a_definition_changes(monkeypatch):
    from harness.report import CRITERIA_TEXT, criteria_hash as report_hash

    before = criteria_hash()
    assert len(before) == 64
    # One identity: the report's meta hash and the gate rows' hash are the same value.
    assert report_hash() == before
    assert "kickoff_moved" in CRITERIA_TEXT
    assert "insufficient (fails)" in CRITERIA_TEXT

    edited = (Criterion(CRITERIA[0].name, CRITERIA[0].definition + " (edited)",
                        CRITERIA[0].fn, CRITERIA[0].threshold),) + CRITERIA[1:]
    monkeypatch.setattr(gate_mod, "CRITERIA", edited)
    assert criteria_hash() != before
    assert report_hash() == criteria_hash()


# --- what the criteria may and may not read -------------------------------------------------


def test_stale_benchmarks_excluded(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for _ in range(4):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        _clv(db_session, order, "pinnacle_t5", clv_p_net="0.0200", stale=False)
        _clv(db_session, order, "consensus_t5", clv_p_net="-0.9000", stale=True)
    # A stale pinnacle row on its own order, which would drag the mean below zero if read.
    market = _market(db_session, _game(db_session))
    stale_order = _order(db_session, market, PRIMARY)
    _fill(db_session, stale_order)
    _clv(db_session, stale_order, "pinnacle_t5", clv_p_net="-0.9000", stale=True)

    result = evaluate_gate(db_session, NOW, PRIMARY)

    clv = result.criteria["clv_pinnacle_lb"]
    assert clv.n_obs == 4
    assert clv.detail["mean"] == pytest.approx(0.02)
    every = result.criteria["clv_every_benchmark"]
    assert every.detail["by_benchmark"]["consensus_t5"] is None
    assert every.status == INSUFFICIENT


def test_result_and_opening_excluded_from_every_benchmark(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for i in range(4):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        for benchmark in GATED:
            _clv(db_session, order, benchmark, clv_p_net="0.0150" if i % 2 else "0.0250")
        _clv(db_session, order, "result", clv_p_net="-0.9000")
        _clv(db_session, order, "opening_first_seen", clv_p_net="-0.9000")

    result = evaluate_gate(db_session, NOW, PRIMARY)

    every = result.criteria["clv_every_benchmark"]
    assert set(every.detail["by_benchmark"]) == set(GATED)
    assert every.passed is True


def test_fill_share_uses_book_source_and_dirty_minutes(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    clean = []
    for book_source, dirty in (("ws", 0), ("ws", 0), ("ws", 0), ("rest", 0), ("ws", 3)):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY, book_source=book_source,
                       dirty_minutes=dirty)
        _fill(db_session, order)
        clean.append(book_source == "ws" and dirty == 0)
    # A fill on the other track is not a fill event, and neither is a replay fill.
    market = _market(db_session, _game(db_session))
    _fill(db_session, _order(db_session, market, PRIMARY), fill_method="snapshot_cross")
    _fill(db_session, _order(db_session, market, PRIMARY), replay=True)

    fills = evaluate_gate(db_session, NOW, PRIMARY).criteria["fill_events"]

    assert fills.value == 5
    assert fills.detail["ws_clean_share"] == pytest.approx(0.6)
    assert fills.passed is False


def test_equivalence_bound_insufficient_below_20_clusters(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _seed_passing(db_session, games=19)

    result = evaluate_gate(db_session, NOW, PRIMARY)

    bound = result.criteria["filled_vs_unfilled"]
    assert bound.status == INSUFFICIENT
    assert bound.passed is False
    assert bound.detail["n_clusters_filled"] == 19
    assert bound.detail["n_clusters_unfilled"] == 19


def test_markout_reads_nw_fill_anchor_with_fair_changed(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for i in range(6):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        _markout(db_session, order, "nw_fill", "30m",
                 fair_p="0.5550" if i % 2 else "0.5650")
        # Neither of these may be read: the wrong anchor, and an unchanged fair.
        _markout(db_session, order, "fill", "30m", fair_p="0.1000")
        _markout(db_session, order, "nw_fill", "120m", fair_p="0.1000")
    market = _market(db_session, _game(db_session))
    unchanged = _order(db_session, market, PRIMARY)
    _fill(db_session, unchanged)
    _markout(db_session, unchanged, "nw_fill", "30m", fair_p="0.1000", fair_changed=False)

    markout = evaluate_gate(db_session, NOW, PRIMARY).criteria["markout_30m"]

    assert markout.n_obs == 6
    assert markout.value == pytest.approx(0.0575)
    assert markout.detail["fair_unchanged_share"] == pytest.approx(1 / 7)
    assert markout.passed is True


def test_staleness_median_uses_pricing_time_fair_values(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for staleness in (10, 20, 30, 40, 5000):
        _signal(db_session, _market(db_session, _game(db_session)), PRIMARY,
                staleness_s=staleness)
    # Not this variant's, not a candidate, and a signal whose gap snapshot has no fair value.
    _signal(db_session, _market(db_session, _game(db_session)), SECOND, staleness_s=9000)
    _signal(db_session, _market(db_session, _game(db_session)), PRIMARY, staleness_s=9000,
            decision="rejected")
    _signal(db_session, _market(db_session, _game(db_session)), PRIMARY, fair_value=False)
    # A fair value nothing points at is not in the population either.
    db_session.add(FairValue(run_id=next(_ids), game_id=_game(db_session).id,
                             market_type="moneyline",
                             fair_p=Decimal("0.5"), fair_source="direct", n_groups=2,
                             staleness_s=9000, created_at=PLACED))
    db_session.flush()

    staleness = evaluate_gate(db_session, NOW, PRIMARY).criteria["staleness_median"]

    assert staleness.n_obs == 5
    assert staleness.value == 30
    assert staleness.passed is True


def test_criteria_read_order_clv_not_gap_outcomes(db_session):
    import inspect

    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for i in range(4):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        for benchmark in GATED:
            _clv(db_session, order, benchmark, clv_p_net="0.0150" if i % 2 else "0.0250")
        signal = _signal(db_session, market, PRIMARY)
        for benchmark in ("pinnacle_t5", "consensus_t5"):
            db_session.add(GapOutcome(gap_snapshot_id=signal.gap_snapshot_id,
                                      benchmark_type=benchmark, p_bench=Decimal("0.0100"),
                                      clv_target_p_net=Decimal("-0.9000"),
                                      p_used_kind="target"))
    db_session.flush()

    result = evaluate_gate(db_session, NOW, PRIMARY)

    assert result.criteria["clv_pinnacle_lb"].detail["mean"] == pytest.approx(0.02)
    assert result.criteria["clv_every_benchmark"].passed is True
    source = inspect.getsource(gate_mod)
    assert "from gap_outcomes" not in source and "join gap_outcomes" not in source


# --- the gate row ---------------------------------------------------------------------------


def test_gate_variant_row_is_marked_and_primary_reported_beside_it(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECOND, "sharp_two_sided", "secondary")

    results = evaluate_all(db_session, NOW, [PRIMARY, SECOND], "sharp_two_sided")

    marked = [r for r in results if r.gate_variant]
    assert [r.variant_id for r in marked] == [SECOND]
    rows = db_session.query(GateReport).all()
    assert sorted((r.variant_id, r.gate_variant) for r in rows) == [
        (PRIMARY, False), (SECOND, True)]
    # The primary is reported beside it: its own row is stored in the same evaluation.
    assert {r.evaluated_at for r in rows} == {NOW}


def test_gate_row_falls_back_to_the_primary_when_the_named_variant_is_not_registered(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")

    results = evaluate_all(db_session, NOW, [PRIMARY], "sharp_two_sided")

    assert [r.variant_id for r in results if r.gate_variant] == [PRIMARY]
    rows = db_session.query(GateReport).all()
    assert sum(1 for r in rows if r.gate_variant) == 1


def test_gate_row_switches_to_the_named_variant_once_it_is_registered(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECOND, "sharp_two_sided", "secondary", active=False)

    before = evaluate_all(db_session, NOW, [PRIMARY, SECOND], "sharp_two_sided")
    assert [r.variant_id for r in before if r.gate_variant] == [PRIMARY]

    db_session.query(StrategyVariant).filter_by(variant_id=SECOND).update({"active": True})
    later = NOW + timedelta(hours=1)
    after = evaluate_all(db_session, later, [PRIMARY, SECOND], "sharp_two_sided")

    assert [r.variant_id for r in after if r.gate_variant] == [SECOND]
    rows = db_session.query(GateReport).all()
    assert len(rows) == 4  # a re-run stores a new report run; it never rewrites an old row
    for evaluated_at in (NOW, later):
        assert sum(1 for r in rows if r.evaluated_at == evaluated_at and r.gate_variant) == 1


# --- the CLI ---------------------------------------------------------------------------------


def test_gate_cli_stores_rows_prints_a_summary_and_exits_zero(db_session, cli_settings):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _seed_passing(db_session, games=4)
    db_session.commit()

    result = runner.invoke(app, ["gate"])

    assert result.exit_code == 0, result.output
    assert "sharp_direct" in result.output
    assert "fill_events" in result.output
    assert "passed=False" in result.output
    rows = db_session.query(GateReport).all()
    assert len(rows) == 1 and rows[0].gate_variant is True and rows[0].passed is False


# --- settlement and match keys ----------------------------------------------------------------


def test_settlement_and_mismatched_markets(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    markets = _seed_passing(db_session, games=4)
    # One market the venue settled the other way, and one order whose market was re-matched.
    db_session.query(VenueSettlement).filter_by(ticker=markets[0].ticker,
                                                source="venue").update({"result": "no"})
    db_session.query(VenueMarket).filter_by(id=markets[1].id).update({"match_key": "moved"})
    db_session.flush()

    result = evaluate_gate(db_session, NOW, PRIMARY)

    assert result.criteria["settlement"].detail["mismatches"] == 1
    assert result.criteria["settlement"].passed is False
    assert result.criteria["mismatched_markets"].value == 5
    assert result.criteria["mismatched_markets"].passed is False


def test_kickoff_moved_games_are_excluded_from_gate_means(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for i in range(4):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        _clv(db_session, order, "pinnacle_t5", clv_p_net="0.0150" if i % 2 else "0.0250")
    moved = _game(db_session)
    order = _order(db_session, _market(db_session, moved), PRIMARY)
    _fill(db_session, order)
    _clv(db_session, order, "pinnacle_t5", clv_p_net="-0.9000")
    db_session.add(Benchmark(game_id=moved.id, market_type="moneyline", outcome_team_id=HOME,
                             benchmark_type="pinnacle_t5", p=Decimal("0.5600"),
                             target_ts=PLACED, source_ts=PLACED, stale=False,
                             kickoff_moved=True, created_at=PLACED))
    db_session.flush()

    result = evaluate_gate(db_session, NOW, PRIMARY)

    assert result.criteria["clv_pinnacle_lb"].n_obs == 4
    assert result.criteria["clv_pinnacle_lb"].detail["mean"] == pytest.approx(0.02)
    # The count criteria are not means: the moved game's fill event still counts.
    assert result.criteria["fill_events"].value == 5


def test_no_pooling_across_variants(db_session):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECOND, "sharp_two_sided", "secondary")
    for i in range(4):
        market = _market(db_session, _game(db_session))
        for variant, clv in ((PRIMARY, "0.0200"), (SECOND, "-0.5000")):
            order = _order(db_session, market, variant)
            _fill(db_session, order)
            _clv(db_session, order, "pinnacle_t5", clv_p_net=clv)

    primary, second = evaluate_all(db_session, NOW, [PRIMARY, SECOND], "sharp_direct")

    assert primary.criteria["clv_pinnacle_lb"].n_obs == 4
    assert primary.criteria["clv_pinnacle_lb"].detail["mean"] == pytest.approx(0.02)
    assert second.criteria["clv_pinnacle_lb"].detail["mean"] == pytest.approx(-0.5)


# --- fix round 1 -------------------------------------------------------------------------------


def test_storing_the_same_evaluation_twice_leaves_one_row_per_variant(db_session):
    """An evaluation is one `evaluated_at` shared by its rows, and it is stored once.

    The unique index on `(evaluated_at, variant_id)` plus `on conflict do nothing` makes a
    re-run of the same evaluation idempotent, while a later evaluation still appends.
    """
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECOND, "sharp_two_sided", "secondary")

    evaluate_all(db_session, NOW, [PRIMARY, SECOND], "sharp_direct")
    evaluate_all(db_session, NOW, [PRIMARY, SECOND], "sharp_direct")

    rows = db_session.query(GateReport).all()
    assert len(rows) == 2
    assert sorted(r.variant_id for r in rows) == [PRIMARY, SECOND]
    evaluate_all(db_session, NOW + timedelta(hours=1), [PRIMARY, SECOND], "sharp_direct")
    assert db_session.query(GateReport).count() == 4


def test_episode_of_is_public_and_shared_with_the_report(db_session):
    """Criterion 6 and table 3 must collapse reprice chains by the same rule, so they share one
    function rather than two copies of it."""
    from harness.report import tables

    assert tables.episode_of is gate_mod.episode_of
    assert tables._episode_of is tables.episode_of  # the private alias table 3 still uses


def test_equivalence_bound_uses_the_two_sample_cluster_robust_estimator(db_session):
    """The bound is the difference-in-means clustered interval, on clusters whose filled and
    unfilled counts differ -- the case a sign-weighted `cluster_ci` gets wrong."""
    from harness.report.stats import cluster_diff_ci

    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    values, sides, clusters = [], [], []
    for i in range(22):
        market = _market(db_session, _game(db_session))
        for j in range(1 + i % 3):  # 1..3 filled episodes
            order = _order(db_session, market, PRIMARY)
            _fill(db_session, order)
            clv = f"0.0{10 + i + j:03d}"[:6]
            _clv(db_session, order, "pinnacle_t5", clv_p_net=clv)
            values.append(float(clv))
            sides.append(False)
            clusters.append(market.game_id)
        for j in range(1 + (i + 1) % 2):  # 1..2 unfilled episodes
            order = _order(db_session, market, PRIMARY)
            clv = f"0.0{20 + i + j:03d}"[:6]
            _clv(db_session, order, "pinnacle_t5", clv_p_net=clv)
            values.append(float(clv))
            sides.append(True)
            clusters.append(market.game_id)

    bound = evaluate_gate(db_session, NOW, PRIMARY).criteria["filled_vs_unfilled"]

    expected = cluster_diff_ci(values, sides, clusters, level=0.90)
    assert bound.status != INSUFFICIENT
    assert bound.value == pytest.approx(expected.hi)
    assert bound.detail["difference"] == pytest.approx(expected.mean)


def test_markout_drops_rows_with_a_null_price(db_session):
    """A NULL `p_used` or `fee_per_contract` makes the markout undefined, not zero: substituting
    zero would report roughly `fair_p` (~0.5) on a criterion that lives at ~0.005."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    for i in range(6):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        _markout(db_session, order, "nw_fill", "30m", fair_p="0.5550" if i % 2 else "0.5650")
    for missing in ("p_used", "fee"):
        market = _market(db_session, _game(db_session))
        order = _order(db_session, market, PRIMARY)
        _fill(db_session, order)
        _markout(db_session, order, "nw_fill", "30m", fair_p="0.9000",
                 **{missing: None})

    markout = evaluate_gate(db_session, NOW, PRIMARY).criteria["markout_30m"]

    assert markout.n_obs == 6
    assert markout.value == pytest.approx(0.0575)
    assert markout.detail["dropped_incomplete"] == 2


def test_settlement_counts_games_and_is_bounded_by_now(db_session):
    """`n_clusters` is games, as the dataclass says, with the market count beside it; and a
    venue settlement recorded after `now` cannot change what an evaluation at `now` said."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    markets = _seed_passing(db_session, games=4)
    # A second market on the first game: five settled markets across four games.
    second = _market(db_session, db_session.get(Game, markets[0].game_id))
    _fill(db_session, _order(db_session, second, PRIMARY))
    _settled(db_session, second)
    # A market the venue settled the other way, after the evaluation instant.
    later = _market(db_session, _game(db_session))
    db_session.add(VenueSettlement(venue="kalshi", ticker=later.ticker, source="derived",
                                   result="yes", payout=Decimal("1.00"), settled_at=PLACED))
    db_session.add(VenueSettlement(venue="kalshi", ticker=later.ticker, source="venue",
                                   result="no", payout=Decimal("0.00"),
                                   settled_at=NOW + timedelta(hours=1)))
    db_session.flush()

    result = evaluate_gate(db_session, NOW, PRIMARY).criteria["settlement"]

    assert result.detail["mismatches"] == 0
    assert result.n_clusters == 4 and result.n_obs == 5
    assert result.detail["n_markets"] == 5
    assert result.passed is True


def test_hash_changes_when_a_threshold_changes(monkeypatch):
    """`threshold` is a field of the criterion, so it is part of the identity the hash stands
    for: editing 150 to 100 without touching the text still moves the hash."""
    before = criteria_hash()
    edited = (Criterion(CRITERIA[0].name, CRITERIA[0].definition, CRITERIA[0].fn, 100),
              ) + CRITERIA[1:]
    monkeypatch.setattr(gate_mod, "CRITERIA", edited)
    assert criteria_hash() != before
