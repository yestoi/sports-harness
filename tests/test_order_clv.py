"""CLV: the shared pure formulas, per-order rows in the order's side space, and the
whole-population `gap_outcomes` drain in the venue market's own space.
"""

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import (
    Benchmark,
    Game,
    GapOutcome,
    JobState,
    MarketGapSnapshot,
    Order,
    OrderClv,
    Signal,
    StrategyVariant,
    VenueMarket,
)
from harness.execution.book import side_p
from harness.settlement.benchmarks import BENCHMARK_TYPES, GAP_OUTCOME_TYPES
from harness.settlement.job import Budget
from harness.settlement.order_clv import (
    GAP_OUTCOMES_WATERMARK_KEY,
    clv_formulas,
    compute_order_clv,
    drain_gap_outcomes,
)

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
HOME, AWAY = 14, 19


class Mono:
    def __init__(self, *values: float) -> None:
        self.values = list(values) or [0.0]
        self.i = 0

    def __call__(self) -> float:
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return value


def _game(session, kickoff=None) -> Game:
    g = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=kickoff or NOW - timedelta(hours=4), status="final",
             home_score=24, away_score=21)
    session.add(g)
    session.flush()
    return g


def _market(session, game_id, ticker="T-ML-HOME", market_type="moneyline", threshold=None,
           side_team_id=HOME, side=None) -> VenueMarket:
    m = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME-EVT",
                    series_ticker="KXNFLGAME", game_id=game_id, market_type=market_type,
                    threshold=None if threshold is None else Decimal(str(threshold)),
                    side_team_id=side_team_id, side=side, match_confidence=Decimal("1.00"),
                    match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    session.add(m)
    session.flush()
    return m


def _benchmark(session, game_id, benchmark_type, p, market_type="moneyline", team=HOME,
              side=None, threshold=None, stale=False) -> Benchmark:
    row = Benchmark(game_id=game_id, market_type=market_type, outcome_team_id=team,
                    outcome_side=side, threshold=None if threshold is None else Decimal(str(threshold)),
                    benchmark_type=benchmark_type, p=None if p is None else Decimal(str(p)),
                    target_ts=NOW, source_ts=NOW, stale=stale, kickoff_moved=False, created_at=NOW)
    session.add(row)
    session.flush()
    return row


def _order(session, market, side="no", prob="0.42", contracts="10.00", replay=False) -> Order:
    o = Order(intent_id=uuid.uuid4(), variant_id="v1", venue="kalshi", mode="paper",
              client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
              venue_market_id=market.id, side=side, prob=Decimal(prob),
              contracts=Decimal(contracts), status="open", placed_at=NOW - timedelta(hours=1),
              game_id=market.game_id, replay=replay)
    session.add(o)
    session.flush()
    return o


_run_ids = itertools.count(1)


def _gap(session, market_id, run_id=None, fair_p=None, venue_mid=None, best_bid=None) -> MarketGapSnapshot:
    # uq_gap_run_market is keyed (run_id, venue_market_id): each snapshot on the same market
    # needs its own run_id, exactly as the pricing pipeline gives one gap snapshot per run.
    row = MarketGapSnapshot(run_id=run_id if run_id is not None else next(_run_ids),
                            venue_market_id=market_id,
                            fair_p=None if fair_p is None else Decimal(str(fair_p)),
                            venue_mid=None if venue_mid is None else Decimal(str(venue_mid)),
                            best_bid=None if best_bid is None else Decimal(str(best_bid)),
                            n_groups=1, dow=1, hour_ct=12, created_at=NOW)
    session.add(row)
    session.flush()
    return row


def _primary_variant(session, variant_id="p1") -> StrategyVariant:
    v = StrategyVariant(variant_id=variant_id, name=f"primary-{variant_id}", tier="primary",
                        config_json={}, registered_at=NOW)
    session.add(v)
    session.flush()
    return v


def _signal(session, run_id, gap_snapshot_id, venue_market_id, variant_id, price_target,
           replay=False) -> Signal:
    row = Signal(run_id=run_id, variant_id=variant_id, gap_snapshot_id=gap_snapshot_id,
                venue_market_id=venue_market_id, side="yes",
                price_target=None if price_target is None else Decimal(str(price_target)),
                decision="candidate", labels={}, replay=replay, created_at=NOW)
    session.add(row)
    session.flush()
    return row


FOUR = Decimal("0.0001")


def _q4(x: Decimal) -> Decimal:
    """Match the `Numeric(8,4)` rounding a roi value goes through on its way into the
    database: the pure formula itself is not rounded (spec: `p_bench / (p_used + fee) - 1`),
    so a value read back from a `clv_roi_net`/`clv_target_roi_net` column must be compared
    against this, not against the raw division."""
    return x.quantize(FOUR)


# --- pure formulas -----------------------------------------------------------------------


def test_formulas_on_fixed_numbers():
    clv, clv_net, roi_net = clv_formulas(Decimal("0.60"), Decimal("0.55"))
    assert clv == Decimal("0.0500")
    assert clv_net == Decimal("0.0457")
    assert roi_net == Decimal("0.60") / Decimal("0.5543") - Decimal(1)


# --- order_clv -----------------------------------------------------------------------------


def _seed_all_benchmarks(session, game_id, p="0.6000", stale_type=None):
    for kind in BENCHMARK_TYPES:
        _benchmark(session, game_id, kind, p, stale=(kind == stale_type))


#: `order_clv.benchmark_type` is still `String(16)` (see order_clv.py's ORDER_CLV_BENCHMARK_TYPE_MAX
#: note): these two BENCHMARK_TYPES values are longer and are skipped with a warning, not stored.
_ORDER_CLV_STORABLE_TYPES = tuple(t for t in BENCHMARK_TYPES if len(t) <= 16)


def test_order_clv_one_row_per_benchmark_type_in_side_space(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    _seed_all_benchmarks(db_session, game.id, p="0.6000")
    order = _order(db_session, market, side="no", prob="0.42")
    db_session.commit()

    n = compute_order_clv(db_session, NOW, Budget(60, Mono(0.0)))
    rows = {r.benchmark_type: r for r in db_session.query(OrderClv).filter_by(order_id=order.id).all()}

    assert n == len(_ORDER_CLV_STORABLE_TYPES) == len(rows)
    assert set(rows) == set(_ORDER_CLV_STORABLE_TYPES)
    expected_p_bench = side_p(Decimal("0.6000"), "no")
    assert expected_p_bench == Decimal("0.4000")
    for row in rows.values():
        assert row.p_bench == Decimal("0.4000")
        assert row.p_used == Decimal("0.4200")
        assert row.p_used_kind == "order"
        clv, clv_net, roi_net = clv_formulas(Decimal("0.4000"), Decimal("0.4200"))
        assert row.clv_p == clv
        assert row.clv_p_net == clv_net
        assert row.clv_roi_net == _q4(roi_net)
        assert row.stale is False


def test_order_clv_idempotent_and_stale_copied(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    _seed_all_benchmarks(db_session, game.id, p="0.6000", stale_type="pinnacle_t5")
    order = _order(db_session, market, side="yes", prob="0.55")
    db_session.commit()

    first = compute_order_clv(db_session, NOW, Budget(60, Mono(0.0)))
    assert first == len(_ORDER_CLV_STORABLE_TYPES)
    row = db_session.query(OrderClv).filter_by(order_id=order.id, benchmark_type="pinnacle_t5").one()
    assert row.stale is True
    other = db_session.query(OrderClv).filter_by(order_id=order.id, benchmark_type="consensus_t5").one()
    assert other.stale is False

    again = compute_order_clv(db_session, NOW, Budget(60, Mono(0.0)))
    assert again == 0
    assert db_session.query(OrderClv).filter_by(order_id=order.id).count() == len(_ORDER_CLV_STORABLE_TYPES)


def test_order_clv_excludes_replay_and_requires_game_benchmarks(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    _seed_all_benchmarks(db_session, game.id)
    _order(db_session, market, side="yes", prob="0.50", replay=True)

    unbenchmarked_game = _game(db_session)
    unbenchmarked_market = _market(db_session, unbenchmarked_game.id, ticker="T-ML-OTHER")
    _order(db_session, unbenchmarked_market, side="yes", prob="0.50")
    db_session.commit()

    n = compute_order_clv(db_session, NOW, Budget(60, Mono(0.0)))
    assert n == 0
    assert db_session.query(OrderClv).count() == 0


# --- drain_gap_outcomes ----------------------------------------------------------------


def test_gap_outcomes_skip_rows_without_fair(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    for kind in GAP_OUTCOME_TYPES:
        _benchmark(db_session, game.id, kind, "0.6000")
    no_fair = _gap(db_session, market.id, fair_p=None, venue_mid="0.55", best_bid="0.50")
    with_fair = _gap(db_session, market.id, fair_p="0.5600", venue_mid="0.55", best_bid="0.50")
    db_session.commit()

    n = drain_gap_outcomes(db_session, batch=50_000)

    assert n == len(GAP_OUTCOME_TYPES)
    assert db_session.query(GapOutcome).filter_by(gap_snapshot_id=no_fair.id).count() == 0
    rows = {r.benchmark_type: r for r in
            db_session.query(GapOutcome).filter_by(gap_snapshot_id=with_fair.id).all()}
    assert set(rows) == set(GAP_OUTCOME_TYPES)
    for row in rows.values():
        assert row.p_bench == Decimal("0.6000")
        assert row.clv_mid_p == Decimal("0.6000") - Decimal("0.55")
        assert row.clv_bid_p == Decimal("0.6000") - Decimal("0.50")
        # No primary signal for this snapshot: p_used falls back to best_bid.
        assert row.p_used_kind == "best_bid"
        assert row.clv_target_p is None
        clv, clv_net, roi_net = clv_formulas(Decimal("0.6000"), Decimal("0.50"))
        assert row.clv_target_p_net == clv_net
        assert row.clv_target_roi_net == _q4(roi_net)

    # The watermark advances past both snapshots: both games (the same one, here) already have
    # benchmarks, so neither is left to revisit even though only one produced rows.
    watermark = db_session.get(JobState, GAP_OUTCOMES_WATERMARK_KEY)
    assert watermark.value == with_fair.id


def test_drain_gap_outcomes_uses_primary_signal_target_when_present(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    for kind in GAP_OUTCOME_TYPES:
        _benchmark(db_session, game.id, kind, "0.6000")
    gap = _gap(db_session, market.id, run_id=7, fair_p="0.5600", venue_mid="0.55", best_bid="0.50")
    _primary_variant(db_session, "p1")
    _signal(db_session, run_id=7, gap_snapshot_id=gap.id, venue_market_id=market.id,
           variant_id="p1", price_target="0.5300")
    db_session.commit()

    drain_gap_outcomes(db_session, batch=50_000)

    row = db_session.query(GapOutcome).filter_by(gap_snapshot_id=gap.id, benchmark_type="pinnacle_t5").one()
    assert row.p_used_kind == "target"
    clv, clv_net, roi_net = clv_formulas(Decimal("0.6000"), Decimal("0.5300"))
    assert row.clv_target_p == clv
    assert row.clv_target_p_net == clv_net
    assert row.clv_target_roi_net == _q4(roi_net)


def test_drain_respects_batch_watermark_and_is_idempotent(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    for kind in GAP_OUTCOME_TYPES:
        _benchmark(db_session, game.id, kind, "0.6000")
    gaps = [_gap(db_session, market.id, fair_p="0.5000", venue_mid="0.50", best_bid="0.49")
            for _ in range(3)]

    unbenchmarked_game = _game(db_session)
    unbenchmarked_market = _market(db_session, unbenchmarked_game.id, ticker="T-ML-OTHER")
    blocked = _gap(db_session, unbenchmarked_market.id, fair_p="0.5000", venue_mid="0.50", best_bid="0.49")
    db_session.commit()

    first = drain_gap_outcomes(db_session, batch=2)
    assert first == 2 * len(GAP_OUTCOME_TYPES)
    assert db_session.get(JobState, GAP_OUTCOMES_WATERMARK_KEY).value == gaps[1].id

    second = drain_gap_outcomes(db_session, batch=2)
    assert second == 1 * len(GAP_OUTCOME_TYPES)
    # The watermark stops at the third (last benchmarked) snapshot: the fourth belongs to a
    # game with no benchmarks yet and is left for a later pass to revisit.
    assert db_session.get(JobState, GAP_OUTCOMES_WATERMARK_KEY).value == gaps[2].id
    assert db_session.query(GapOutcome).filter_by(gap_snapshot_id=blocked.id).count() == 0

    third = drain_gap_outcomes(db_session, batch=50_000)
    assert third == 0  # idempotent: nothing new for the three already-processed snapshots
    assert db_session.get(JobState, GAP_OUTCOMES_WATERMARK_KEY).value == gaps[2].id

    # Once the blocked game gets its own benchmarks, a later pass picks the snapshot back up.
    for kind in GAP_OUTCOME_TYPES:
        _benchmark(db_session, unbenchmarked_game.id, kind, "0.6000")
    db_session.commit()
    fourth = drain_gap_outcomes(db_session, batch=50_000)
    assert fourth == len(GAP_OUTCOME_TYPES)
    assert db_session.get(JobState, GAP_OUTCOMES_WATERMARK_KEY).value == blocked.id
