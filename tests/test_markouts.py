"""Markouts at placement and fill, and the trailing `as_measured` bucket table.

`markout_at` is the one pure decision (ruling 1): it picks a fair by book time, then a venue
mid by quote-then-book-then-none, from lists the caller has already loaded and, for a NO-side
order, already flipped through `side_p`. `compute_markouts` is the settlement stage that walks
non-replay orders, and `as_measured_table` is the trailing bucket mean that later feeds
`SignalRow.as_measured` without ever being read by the strategy's own decision.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import Fill, Game, Markout, Order, VenueMarket, VenueQuote
from harness.execution.book import side_p
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.settlement.job import Budget
from harness.settlement.markouts import (
    HORIZONS,
    FairPoint,
    as_measured_table,
    compute_markouts,
    markout_at,
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


# --- fixtures ------------------------------------------------------------------------------


def _game(session, kickoff=None) -> Game:
    g = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=kickoff or NOW + timedelta(hours=2), status="scheduled")
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


def _order(session, market, side="yes", prob="0.5000", placed_at=None, fair_row_id_at_place=None,
          crossed=False, nw_crossed=False, replay=False, status="open") -> Order:
    o = Order(intent_id=uuid.uuid4(), variant_id="v1", venue="kalshi", mode="paper",
              client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
              venue_market_id=market.id, side=side, prob=Decimal(prob),
              contracts=Decimal("10.00"), status=status,
              placed_at=placed_at or NOW - timedelta(hours=1),
              fair_row_id_at_place=fair_row_id_at_place,
              crossed=crossed, nw_crossed=nw_crossed,
              game_id=market.game_id, sport="nfl", replay=replay)
    session.add(o)
    session.flush()
    return o


def _fill(session, order, fill_method="queue_model", filled_at=None, prob="0.5000") -> Fill:
    f = Fill(order_id=order.id, prob=Decimal(prob), contracts=Decimal("10.00"),
             fee=Decimal("0.10"), filled_at=filled_at or NOW - timedelta(minutes=50),
             fill_method=fill_method, has_print=True, replay=False)
    session.add(f)
    session.flush()
    return f


def _fair_value(session, game_id, created_at, p, newest_book_ts=None, market_type="moneyline",
                team=HOME, side=None, threshold=None, fair_source="direct"):
    from harness.db.models import FairValue

    row = FairValue(run_id=1, game_id=game_id, market_type=market_type, outcome_team_id=team,
                    outcome_side=side, threshold=None if threshold is None else Decimal(str(threshold)),
                    fair_p=Decimal(str(p)), fair_source=fair_source, created_at=created_at,
                    newest_book_ts=newest_book_ts)
    session.add(row)
    session.flush()
    return row


def _quote(session, venue_market_id, fetched_at, yes_bid, yes_ask, raw_id=1) -> VenueQuote:
    q = VenueQuote(raw_id=raw_id, run_id=1, venue_market_id=venue_market_id,
                   yes_bid=Decimal(str(yes_bid)), yes_ask=Decimal(str(yes_ask)),
                   fetched_at=fetched_at)
    session.add(q)
    session.flush()
    return q


FOUR = Decimal("0.0001")


# --- markout_at: pure, no database -----------------------------------------------------


def test_markout_selects_fair_by_book_ts_and_marks_changed():
    anchor_ts = NOW
    horizon_ts = NOW + timedelta(minutes=5)
    fairs = [
        FairPoint(row_id=1, created_at=NOW - timedelta(minutes=20),
                 newest_book_ts=NOW - timedelta(minutes=19), p=Decimal("0.5000")),
        # Newer book_ts, still before the horizon: this one should win.
        FairPoint(row_id=2, created_at=NOW - timedelta(minutes=1),
                 newest_book_ts=NOW - timedelta(minutes=2), p=Decimal("0.5600")),
        # After the horizon: never a candidate.
        FairPoint(row_id=3, created_at=horizon_ts + timedelta(minutes=1),
                 newest_book_ts=horizon_ts + timedelta(minutes=1), p=Decimal("0.9900")),
    ]
    point = markout_at(anchor_ts, horizon_ts, fairs, [], lambda instant: None)

    assert point.fair_p == Decimal("0.5600")
    assert point.fair_row_id == 2
    assert point.fair_book_ts == NOW - timedelta(minutes=2)
    assert point.fair_age_s == 420  # 7 minutes between fair_book_ts and horizon_ts

    fair_row_id_at_place = 1
    fair_changed = point.fair_row_id != fair_row_id_at_place
    assert fair_changed is True


def test_markout_fair_falls_back_to_created_at_and_can_be_missing():
    horizon_ts = NOW
    fairs = [
        FairPoint(row_id=5, created_at=NOW - timedelta(minutes=3), newest_book_ts=None,
                 p=Decimal("0.4400")),
    ]
    point = markout_at(NOW - timedelta(hours=1), horizon_ts, fairs, [], lambda instant: None)
    assert point.fair_p == Decimal("0.4400")
    assert point.fair_book_ts == NOW - timedelta(minutes=3)
    assert point.fair_age_s == 180

    empty = markout_at(NOW - timedelta(hours=1), horizon_ts, [], [], lambda instant: None)
    assert empty.fair_p is None
    assert empty.fair_row_id is None
    assert empty.fair_book_ts is None
    assert empty.fair_age_s is None


def test_quote_then_book_then_none():
    horizon_ts = NOW

    # A quote within 60 s wins over the book.
    quotes = [(NOW - timedelta(seconds=30), Decimal("0.5100")),
             (NOW - timedelta(minutes=10), Decimal("0.9000"))]
    point = markout_at(NOW, horizon_ts, [], quotes, lambda instant: Decimal("0.6000"))
    assert point.source == "quote"
    assert point.venue_mid == Decimal("0.5100")
    assert point.mid_age_s == 30

    # No quote within the window: falls back to the book mid.
    point = markout_at(NOW, horizon_ts, [], [(NOW - timedelta(minutes=10), Decimal("0.9000"))],
                       lambda instant: Decimal("0.6000"))
    assert point.source == "ws_book"
    assert point.venue_mid == Decimal("0.6000")

    # Neither: none.
    point = markout_at(NOW, horizon_ts, [], [], lambda instant: None)
    assert point.source == "none"
    assert point.venue_mid is None
    assert point.mid_age_s is None


# --- compute_markouts: the stage, against the database ----------------------------------


def test_unfilled_orders_get_place_anchor_only(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    order = _order(db_session, market, side="yes", prob="0.5000",
                   placed_at=NOW - timedelta(hours=3))
    db_session.commit()

    n = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    rows = db_session.query(Markout).filter_by(order_id=order.id).all()

    assert n == len(rows) == 4  # 1m, 5m, 30m, 120m -- no 0m for `place`
    anchors = {r.anchor for r in rows}
    assert anchors == {"place"}
    horizons = {r.horizon for r in rows}
    assert horizons == {"1m", "5m", "30m", "120m"}
    for r in rows:
        assert r.at_ts == order.placed_at
        assert r.horizon_ts == order.placed_at + timedelta(seconds=HORIZONS[r.horizon])
        assert r.p_used == Decimal("0.5000")
        assert r.fee_per_contract == fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.5000"), 100)
        assert r.fair_p is None and r.fair_row_id is None
        assert r.source == "none"


def test_fill_nw_fill_and_cross_fill_anchors(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at,
                  crossed=True)
    qm = _fill(db_session, order, fill_method="queue_model",
              filled_at=placed_at + timedelta(minutes=1), prob="0.5000")
    _fill(db_session, order, fill_method="no_watcher",
         filled_at=placed_at + timedelta(minutes=5), prob="0.5100")
    cross = _fill(db_session, order, fill_method="snapshot_cross",
                 filled_at=placed_at + timedelta(seconds=30), prob="0.5050")
    db_session.commit()

    n = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    rows = db_session.query(Markout).filter_by(order_id=order.id).all()
    by_anchor: dict[str, list] = {}
    for r in rows:
        by_anchor.setdefault(r.anchor, []).append(r)

    assert set(by_anchor) == {"place", "fill", "nw_fill", "cross_fill"}
    # place, cross_fill: 1m/5m/30m/120m only. fill, nw_fill: 0m too.
    assert {r.horizon for r in by_anchor["place"]} == {"1m", "5m", "30m", "120m"}
    assert {r.horizon for r in by_anchor["cross_fill"]} == {"1m", "5m", "30m", "120m"}
    assert {r.horizon for r in by_anchor["fill"]} == {"0m", "1m", "5m", "30m", "120m"}
    assert {r.horizon for r in by_anchor["nw_fill"]} == {"0m", "1m", "5m", "30m", "120m"}

    fill_at_ts = {r.at_ts for r in by_anchor["fill"]}
    assert fill_at_ts == {qm.filled_at}
    cross_at_ts = {r.at_ts for r in by_anchor["cross_fill"]}
    assert cross_at_ts == {cross.filled_at}

    # `nw_fill` takes the *first* of queue_model/no_watcher by time -- the queue_model fill
    # here, one minute after placement, beats the no_watcher fill four minutes later.
    nw_p_used = {r.p_used for r in by_anchor["nw_fill"]}
    assert nw_p_used == {Decimal("0.5000")}

    n_again = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    assert n_again == 0


def test_no_cross_fill_anchor_when_order_never_crossed(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at,
                  crossed=False, nw_crossed=False)
    _fill(db_session, order, fill_method="queue_model", filled_at=placed_at + timedelta(minutes=1))
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    anchors = {r.anchor for r in db_session.query(Markout).filter_by(order_id=order.id).all()}
    assert "cross_fill" not in anchors


def test_no_side_markout_in_no_space(db_session):
    """A NO order's stored `fair_p`/`venue_mid` are in NO space (`1 - p`), not the shape's own
    YES space the fair value and quote rows are recorded in."""
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    fair_created = placed_at - timedelta(minutes=5)
    _fair_value(db_session, game.id, fair_created, "0.6000", newest_book_ts=fair_created)
    order = _order(db_session, market, side="no", prob="0.4000", placed_at=placed_at)
    _quote(db_session, market.id, placed_at + timedelta(minutes=1) - timedelta(seconds=10),
          yes_bid="0.5800", yes_ask="0.6000")
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    row = db_session.query(Markout).filter_by(order_id=order.id, anchor="place", horizon="1m").one()

    assert row.fair_p == side_p(Decimal("0.6000"), "no")
    assert row.fair_p == Decimal("0.4000")
    yes_mid = (Decimal("0.5800") + Decimal("0.6000")) / 2
    assert row.venue_mid == side_p(yes_mid, "no")
    assert row.venue_mid == Decimal("0.4100")
    assert row.source == "quote"


def test_idempotent(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    order = _order(db_session, market, placed_at=NOW - timedelta(hours=3))
    db_session.commit()

    first = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    assert first == 4
    second = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    assert second == 0
    assert db_session.query(Markout).filter_by(order_id=order.id).count() == 4


def test_replay_orders_are_excluded(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    _order(db_session, market, placed_at=NOW - timedelta(hours=3), replay=True)
    db_session.commit()

    n = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    assert n == 0
    assert db_session.query(Markout).count() == 0


def test_horizons_beyond_now_are_not_written_yet(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    # Placed 10 minutes ago: only 1m and 5m horizons are already in the past.
    order = _order(db_session, market, placed_at=NOW - timedelta(minutes=10))
    db_session.commit()

    n = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    rows = db_session.query(Markout).filter_by(order_id=order.id).all()
    assert n == len(rows) == 2
    assert {r.horizon for r in rows} == {"1m", "5m"}

    later = compute_markouts(db_session, NOW + timedelta(minutes=30), Budget(60, Mono(0.0)))
    assert later == 1  # 30m horizon now due; 120m still isn't
    rows = db_session.query(Markout).filter_by(order_id=order.id).all()
    assert {r.horizon for r in rows} == {"1m", "5m", "30m"}


# --- as_measured_table -------------------------------------------------------------------


def _insert_markout_row(session, order_id, anchor, horizon, at_ts, fair_p, p_used, fee,
                        fair_changed=True):
    row = Markout(order_id=order_id, anchor=anchor, horizon=horizon, at_ts=at_ts,
                  horizon_ts=at_ts + timedelta(seconds=HORIZONS[horizon]), p_used=p_used,
                  fee_per_contract=fee, fair_p=fair_p, fair_row_id=999, fair_changed=fair_changed,
                  source="quote")
    session.add(row)
    session.flush()
    return row


def test_as_measured_null_below_50_and_value_above(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    fee = Decimal("0.0050")

    # 49 rows in one bucket: below the floor, must not appear.
    for i in range(49):
        order = _order(db_session, market, side="yes", prob="0.5000",
                       placed_at=NOW - timedelta(days=1), status="settled")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee)

    # 50 rows in a different bucket (a different price -> a different 5c bucket): reaches
    # the floor and gets a mean.
    for i in range(50):
        order = _order(db_session, market, side="yes", prob="0.6000",
                       placed_at=NOW - timedelta(days=1), status="settled")
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

    for i in range(50):
        order = _order(db_session, market, side="yes", prob="0.5000",
                       placed_at=NOW - timedelta(days=1), status="settled")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee,
                            fair_changed=False)  # unchanged: excluded

    for i in range(50):
        order = _order(db_session, market, side="yes", prob="0.5000",
                       placed_at=NOW - timedelta(days=1), status="settled")
        _insert_markout_row(db_session, order.id, "fill", "30m", NOW - timedelta(days=1),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee)  # wrong anchor

    for i in range(50):
        order = _order(db_session, market, side="yes", prob="0.5000",
                       placed_at=NOW - timedelta(days=1), status="settled")
        _insert_markout_row(db_session, order.id, "nw_fill", "30m", NOW - timedelta(days=20),
                            fair_p=Decimal("0.5200"), p_used=Decimal("0.5000"), fee=fee)  # too old
    db_session.commit()

    table = as_measured_table(db_session, NOW)
    assert ("nfl", 50, "yes") not in table


def test_as_measured_recorded_not_used():
    """`SignalRow.as_measured` is set from the table but the decision (edge, labels,
    rejection) is computed exactly as it would be with no table at all."""
    from pathlib import Path

    from harness.strategy.run import run_strategy
    from harness.strategy.variants import load_variants

    from tests.test_strategy import gap_row

    variant = next(v for v in load_variants(Path("harness/variants")) if v.name == "sharp_direct")

    row = gap_row()
    (baseline,) = run_strategy([row], variant, NOW)
    assert baseline.as_measured is None

    bucket = (int(baseline.price_target * 100) // 5) * 5
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
