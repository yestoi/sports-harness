"""D12: the trailing `as_measured` bucket table (`harness/strategy/as_measured.py`).

This module owns no settlement stage (fix round 1, Important 5 -- see
`tests/test_pipeline.py::test_importing_pipeline_registers_no_settlement_stage` for the
import-graph half of that fix) and must never fail a pricing tick: a broken read logs a
warning and answers `{}`, covered here directly against a session double rather than a real
database.
"""

import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game, Markout, Order, VenueMarket
from harness.settlement.markouts import HORIZONS
from harness.strategy.as_measured import as_measured_table, price_bucket

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
HOME, AWAY = 14, 19
FOUR = Decimal("0.0001")


def _game(session) -> Game:
    g = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    session.add(g)
    session.flush()
    return g


def _market(session, game_id, ticker="T-ML-HOME") -> VenueMarket:
    m = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME-EVT",
                    series_ticker="KXNFLGAME", game_id=game_id, market_type="moneyline",
                    side_team_id=HOME, match_confidence=Decimal("1.00"),
                    match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    session.add(m)
    session.flush()
    return m


def _order(session, market, side="yes", prob="0.5000", status="settled") -> Order:
    o = Order(intent_id=uuid.uuid4(), variant_id="v1", venue="kalshi", mode="paper",
              client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
              venue_market_id=market.id, side=side, prob=Decimal(prob),
              contracts=Decimal("10.00"), status=status, placed_at=NOW - timedelta(hours=1),
              game_id=market.game_id, sport="nfl", replay=False)
    session.add(o)
    session.flush()
    return o


def _insert_markout_row(session, order_id, anchor, horizon, at_ts, fair_p, p_used, fee,
                        fair_changed=True):
    row = Markout(order_id=order_id, anchor=anchor, horizon=horizon, at_ts=at_ts,
                  horizon_ts=at_ts + timedelta(seconds=HORIZONS[horizon]), p_used=p_used,
                  fee_per_contract=fee, fair_p=fair_p, fair_row_id=999, fair_changed=fair_changed,
                  source="quote")
    session.add(row)
    session.flush()
    return row


# --- price_bucket ------------------------------------------------------------------------


def test_price_bucket_floors_to_the_nearest_5c():
    assert price_bucket(Decimal("0.5000")) == 50
    assert price_bucket(Decimal("0.5499")) == 50
    assert price_bucket(Decimal("0.5999")) == 55


# --- as_measured_table -------------------------------------------------------------------


def test_as_measured_null_below_50_and_value_above(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    fee = Decimal("0.0050")

    # 49 rows in one bucket: below the floor, must not appear.
    for _ in range(49):
        order = _order(db_session, market, side="yes", prob="0.5000")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee)

    # 50 rows in a different bucket (a different price -> a different 5c bucket): reaches
    # the floor and gets a mean.
    for _ in range(50):
        order = _order(db_session, market, side="yes", prob="0.6000")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.6300"), p_used=Decimal("0.6000"), fee=fee)
    db_session.commit()

    table = as_measured_table(db_session, NOW)

    assert ("nfl", 50, "yes") not in table
    key = ("nfl", 60, "yes")
    assert key in table
    expected = Decimal("0.6300") - Decimal("0.6000") - fee
    assert table[key] == expected.quantize(FOUR)


def test_as_measured_excludes_stale_unchanged_and_wrong_anchor(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    fee = Decimal("0.0050")

    for _ in range(50):
        order = _order(db_session, market, side="yes", prob="0.5000")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee,
                            fair_changed=False)  # unchanged: excluded

    for _ in range(50):
        order = _order(db_session, market, side="yes", prob="0.5000")
        _insert_markout_row(db_session, order.id, "fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee)  # wrong anchor

    for _ in range(50):
        order = _order(db_session, market, side="yes", prob="0.5000")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=20),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee)  # too old
    db_session.commit()

    table = as_measured_table(db_session, NOW)
    assert ("nfl", 50, "yes") not in table


def test_as_measured_recorded_not_used():
    """`SignalRow.as_measured` is set from the table but the decision (edge, labels,
    rejection) is computed exactly as it would be with no table at all."""
    from harness.strategy.run import run_strategy
    from harness.strategy.variants import load_variants

    from tests.test_strategy import gap_row

    variant = next(v for v in load_variants(Path("harness/variants")) if v.name == "sharp_direct")

    row = gap_row()
    (baseline,) = run_strategy([row], variant, NOW)
    assert baseline.as_measured is None

    bucket = price_bucket(baseline.price_target)
    key = (row.sport, bucket, baseline.side)
    table = {key: Decimal("0.0123"), ("nfl", bucket + 1000, "yes"): Decimal("9.9999")}

    (with_table,) = run_strategy([row], variant, NOW, as_measured=table)

    assert with_table.decision == baseline.decision
    assert with_table.edge == baseline.edge
    assert with_table.labels == baseline.labels
    assert with_table.price_target == baseline.price_target
    assert with_table.as_measured == Decimal("0.0123")

    # A row whose bucket has no entry in the table stays None.
    (no_match,) = run_strategy([row], variant, NOW, as_measured={})
    assert no_match.as_measured is None


def test_as_measured_table_swallows_a_query_error_and_yields_empty_dict():
    """Fix round 1, Important 5: `as_measured_table` runs on every pricing tick, which has
    never depended on settlement output before -- a broken read must log and answer `{}`,
    never raise, and it must do so without a real database (a fake session is enough to prove
    the `try`/`except` shape without needing a way to actually break Postgres)."""

    class _RaisingSession:
        def begin_nested(self):
            return contextlib.nullcontext()

        def execute(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    assert as_measured_table(_RaisingSession(), NOW) == {}
