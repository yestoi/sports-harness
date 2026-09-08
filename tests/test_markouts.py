"""Markouts at placement and fill.

`markout_at` is the one pure decision (ruling 1): it picks a fair by book time, then a venue
mid by quote-then-book-then-none with no lookahead, from lists the caller has already loaded
and, for a NO-side order, already flipped through `side_p`. `compute_markouts` is the
settlement stage that walks non-replay orders and writes the horizons that are due, including
`close` (kickoff - 5 min) once the game's kickoff is known.

The trailing `as_measured` bucket table lives in `tests/test_as_measured.py`, alongside the
module it now belongs to (`harness/strategy/as_measured.py`, fix round 1, Important 5).
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import Fill, Game, Markout, OrderbookEvent, Order, VenueMarket, VenueQuote
from harness.execution.book import book_age_s, book_at, side_p
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.settlement.job import Budget
from harness.settlement.markouts import (
    HORIZONS,
    FairPoint,
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


_fair_run_ids = iter(range(1, 10_000))


def _fair_value(session, game_id, created_at, p, newest_book_ts=None, market_type="moneyline",
                team=HOME, side=None, threshold=None, fair_source="direct", run_id=None):
    from harness.db.models import FairValue

    # uq_fair_value_row is keyed (run_id, game_id, market_type, ..., fair_source): two fairs
    # for the same shape need their own run_id, exactly as the pricing pipeline gives one fair
    # value per run -- a fixed run_id=1 would collide the moment a test wants two fair rows for
    # one shape (e.g. the tie-break test).
    row = FairValue(run_id=run_id if run_id is not None else next(_fair_run_ids), game_id=game_id,
                    market_type=market_type, outcome_team_id=team, outcome_side=side,
                    threshold=None if threshold is None else Decimal(str(threshold)),
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


def _ws_snapshot(session, ticker, ts, yes=(("0.40", "10.00"),), no=(("0.55", "10.00"),), sid=1, seq=1):
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="snapshot",
                         raw={"market_ticker": ticker, "yes_dollars_fp": [list(l) for l in yes],
                              "no_dollars_fp": [list(l) for l in no]})
    session.add(row)
    session.flush()
    return row


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


def test_a_later_quote_is_ignored_in_favour_of_an_earlier_one():
    """Fix round 1, Important 3: the quote window is one-sided -- a quote after the horizon
    must never win over one before it, however much closer in time it is."""
    horizon_ts = NOW
    quotes = [(NOW - timedelta(seconds=30), Decimal("0.5100")),
             (NOW + timedelta(seconds=10), Decimal("0.9900"))]
    point = markout_at(NOW, horizon_ts, [], quotes, lambda instant: None)
    assert point.source == "quote"
    assert point.venue_mid == Decimal("0.5100")
    assert point.mid_age_s == 30


def test_only_a_later_quote_falls_back_to_the_book():
    """Fix round 1, Important 3: with nothing at or before the horizon, the chain falls to the
    book mid rather than looking ahead to the later quote."""
    horizon_ts = NOW
    quotes = [(NOW + timedelta(seconds=10), Decimal("0.9900"))]
    point = markout_at(NOW, horizon_ts, [], quotes, lambda instant: (Decimal("0.6000"), 12))
    assert point.source == "ws_book"
    assert point.venue_mid == Decimal("0.6000")
    assert point.mid_age_s == 12


def test_a_quote_more_than_60s_stale_falls_back_to_the_book():
    horizon_ts = NOW
    quotes = [(NOW - timedelta(minutes=10), Decimal("0.9000"))]
    point = markout_at(NOW, horizon_ts, [], quotes, lambda instant: (Decimal("0.6000"), 45))
    assert point.source == "ws_book"
    assert point.venue_mid == Decimal("0.6000")
    assert point.mid_age_s == 45


def test_neither_quote_nor_book_is_none():
    point = markout_at(NOW, NOW, [], [], lambda instant: None)
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

    assert n == len(rows) == 4  # 1m, 5m, 30m, 120m -- no 0m for `place`; kickoff is 2h out,
    # so `close` isn't due in this test (see test_close_horizon_... below).
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


def test_cross_fill_scored_at_the_taker_fee(db_session):
    """Fix round 1, Important 7: a `snapshot_cross` fill takes liquidity, so it is scored net
    of the taker rate; every other anchor keeps the maker rate the paper executor posts at."""
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at,
                  crossed=True)
    _fill(db_session, order, fill_method="snapshot_cross",
         filled_at=placed_at + timedelta(seconds=30), prob="0.5200")
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    cross_row = db_session.query(Markout).filter_by(
        order_id=order.id, anchor="cross_fill", horizon="1m").one()
    place_row = db_session.query(Markout).filter_by(
        order_id=order.id, anchor="place", horizon="1m").one()

    assert cross_row.fee_per_contract == fee_per_contract(
        KALSHI_FOOTBALL, "taker", Decimal("0.5200"), 100)
    assert place_row.fee_per_contract == fee_per_contract(
        KALSHI_FOOTBALL, "maker", Decimal("0.5000"), 100)
    assert cross_row.fee_per_contract != place_row.fee_per_contract


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


def test_fair_scan_includes_derived_fair_rows(db_session):
    """Fix round 1, Important 4: a derived-shape order (e.g. `sharp_plus_derived`) has no
    `direct` fair row to scan against -- a direct fair is never computed for a shape once a
    derived one has been. The scan must find its own `derived` fair, not read as permanently
    `fair_changed` against an empty result."""
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    derived_created = placed_at - timedelta(minutes=5)
    fv = _fair_value(db_session, game.id, derived_created, "0.6000",
                     newest_book_ts=derived_created, fair_source="derived")
    order = _order(db_session, market, side="yes", prob="0.5500", placed_at=placed_at,
                  fair_row_id_at_place=fv.id)
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    row = db_session.query(Markout).filter_by(order_id=order.id, anchor="place", horizon="1m").one()

    assert row.fair_p == Decimal("0.6000")
    assert row.fair_row_id == fv.id
    assert row.fair_changed is False


def test_fair_tie_break_prefers_latest_created_at_then_id(db_session):
    """Fix round 1, Important 6: two fairs sharing a `newest_book_ts` (common when no book
    event arrived between two pricing ticks) must resolve deterministically -- the one with
    the later `created_at` wins, not whichever Postgres happens to return first."""
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    shared_book_ts = placed_at - timedelta(minutes=10)
    _fair_value(db_session, game.id, shared_book_ts - timedelta(minutes=1), "0.5000",
               newest_book_ts=shared_book_ts)
    newer = _fair_value(db_session, game.id, shared_book_ts + timedelta(minutes=1), "0.5300",
                        newest_book_ts=shared_book_ts)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at)
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    row = db_session.query(Markout).filter_by(order_id=order.id, anchor="place", horizon="1m").one()

    assert row.fair_row_id == newer.id
    assert row.fair_p == Decimal("0.5300")


def test_quote_tie_break_prefers_the_later_inserted_row(db_session):
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    tie_ts = placed_at + timedelta(minutes=1) - timedelta(seconds=10)
    _quote(db_session, market.id, tie_ts, yes_bid="0.4000", yes_ask="0.4200", raw_id=1)
    _quote(db_session, market.id, tie_ts, yes_bid="0.5800", yes_ask="0.6000", raw_id=2)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at)
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    row = db_session.query(Markout).filter_by(order_id=order.id, anchor="place", horizon="1m").one()

    assert row.venue_mid == (Decimal("0.5800") + Decimal("0.6000")) / 2
    assert row.source == "quote"


def test_book_mid_age_reflects_true_book_age_not_a_false_zero(db_session):
    """Fix round 1, Minor 2: `mid_age_s` for a `ws_book` mid is the book's own age at the
    horizon (`book_age_s`), never a stand-in `0`."""
    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at)
    horizon_ts = placed_at + timedelta(minutes=1)
    _ws_snapshot(db_session, market.ticker, horizon_ts - timedelta(seconds=45))
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    row = db_session.query(Markout).filter_by(order_id=order.id, anchor="place", horizon="1m").one()

    assert row.source == "ws_book"
    assert row.mid_age_s == 45


def test_close_horizon_is_kickoff_minus_five_minutes_for_every_anchor(db_session):
    """Fix round 1, Important 1: `close` is `kickoff_utc - 5 min`, a sixth horizon label
    written for every anchor once its own instant is due -- independent of the anchor's own
    offset-based horizons."""
    kickoff = NOW - timedelta(hours=1)
    game = _game(db_session, kickoff=kickoff)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at)
    _fill(db_session, order, fill_method="queue_model", filled_at=placed_at + timedelta(minutes=1))
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    rows = {(r.anchor, r.horizon): r
           for r in db_session.query(Markout).filter_by(order_id=order.id).all()}

    close_ts = kickoff - timedelta(minutes=5)
    assert ("place", "close") in rows
    assert rows[("place", "close")].at_ts == placed_at
    assert rows[("place", "close")].horizon_ts == close_ts
    assert ("fill", "close") in rows
    assert rows[("fill", "close")].horizon_ts == close_ts

    again = compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    assert again == 0


def test_close_horizon_never_written_when_the_game_is_unmatched(db_session):
    """An order with no `game_id` (an unmatched market) can never get a `close` row -- there is
    no kickoff to compute it from -- and must not be scanned forever waiting for one."""
    game = _game(db_session)
    market = _market(db_session, game.id)
    order = _order(db_session, market, side="yes", prob="0.5000",
                   placed_at=NOW - timedelta(hours=3))
    db_session.execute(
        Order.__table__.update().where(Order.id == order.id).values(game_id=None)
    )
    db_session.commit()

    compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    anchors_and_horizons = {(r.anchor, r.horizon)
                            for r in db_session.query(Markout).filter_by(order_id=order.id).all()}
    assert ("place", "close") not in anchors_and_horizons


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


def test_one_book_per_order_walked_forward_not_rebuilt_per_horizon(db_session):
    """Final review I7: `_book_mid_fn` called `book_at` afresh for every (anchor, horizon) with
    no nearby quote, so one order re-anchored on the ticker's newest snapshot and replayed
    every delta since it up to twenty-four times. The stage now walks one book per order
    through its horizons in `ts` order.

    Both halves are asserted: the anchor is loaded once for the order rather than once per
    horizon, and every mid and age still equals what a per-instant rebuild produces.
    """
    from sqlalchemy import event as sa_event

    game = _game(db_session)
    market = _market(db_session, game.id)
    placed_at = NOW - timedelta(hours=3)
    order = _order(db_session, market, side="yes", prob="0.5000", placed_at=placed_at)
    _ws_snapshot(db_session, market.ticker, placed_at - timedelta(seconds=30))
    # A delta shortly before each due horizon, so the book differs at every one of them and a
    # cached-but-not-advanced book would be caught.
    seq = 2
    for name in ("1m", "5m", "30m", "120m"):
        horizon_ts = placed_at + timedelta(seconds=HORIZONS[name])
        db_session.add(OrderbookEvent(
            ticker=market.ticker, ts=horizon_ts - timedelta(seconds=20), sid=1, seq=seq,
            kind="delta", side="yes", price=Decimal("0.40"), delta=Decimal("1.00"),
            raw={"market_ticker": market.ticker}))
        seq += 1
    db_session.commit()

    engine = db_session.get_bind()
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()).lower())

    sa_event.listen(engine, "before_cursor_execute", capture)
    try:
        compute_markouts(db_session, NOW, Budget(60, Mono(0.0)))
    finally:
        sa_event.remove(engine, "before_cursor_execute", capture)
    db_session.commit()

    rows = db_session.query(Markout).filter_by(order_id=order.id, anchor="place").all()
    assert {r.horizon for r in rows} == {"1m", "5m", "30m", "120m"}

    anchor_loads = [s for s in statements if "kind = 'snapshot' and ts <=" in s]
    assert len(anchor_loads) == 1, f"{len(anchor_loads)} anchor loads for one order's horizons"

    for row in rows:
        rebuilt = book_at(db_session, market.ticker, row.horizon_ts)
        assert row.source == "ws_book"
        assert row.venue_mid == side_p(rebuilt.mid(), "yes")
        assert row.mid_age_s == book_age_s(rebuilt, row.horizon_ts)
