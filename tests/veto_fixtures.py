"""Seeding helpers shared by the three veto test modules.

The veto's own tests all need the same small world -- one game, one venue market, one or more
candidate signals, six hours of fair values, a venue quote, a book line, a score event and a
weather snapshot -- because `build_features` reads all of it. Writing that world three times
would make the three files disagree the first time a column moved, so it is written once here
and the fixtures themselves stay in the test modules that use them.

Nothing here is a fixture: these are plain functions the fixtures call, so a test file can seed
two games or a moved line without a fixture-composition dance.
"""
import itertools
from datetime import timedelta
from decimal import Decimal

from harness.db.models import (FairValue, Game, GameScoreEvent, OddsSnapshot, Signal,
                               VenueMarket, VenueQuote, VetoQueue, WeatherSnapshot)

#: A markup-carrying forecast, so the sanitizer has something to do in every seeded world.
FORECAST = "<b>Partly Sunny</b> then rain"

#: `uq_signal_key` is `(run_id, variant_id, venue_market_id, side, replay)`, so two signals on
#: one market inside one bucket -- which is the whole subject of this task -- need two run ids.
_RUNS = itertools.count(1)


def seed_game(session, *, kickoff, status="scheduled", sport="nfl", score_status=None,
              score_ts=None):
    """One game and one venue market on it. Returns `(game, market)`."""
    game = Game(sport=sport, home_team_id=1, away_team_id=2, kickoff_utc=kickoff, status=status)
    session.add(game)
    session.flush()
    market = VenueMarket(venue="kalshi", ticker=f"KXNFLGAME-{game.id}", event_ticker="EV",
                         series_ticker="KXNFLGAME", game_id=game.id, market_type="moneyline",
                         side="home", first_seen_raw_id=1, last_seen_at=kickoff,
                         match_confidence=Decimal("1.00"), match_status="matched",
                         match_reason="seed")
    session.add(market)
    session.flush()
    if score_status is not None:
        session.add(GameScoreEvent(game_id=game.id, ts=score_ts or kickoff - timedelta(hours=8),
                                   status=score_status))
        session.flush()
    return game, market


def seed_signal(session, *, market, created_at, fair_p="0.5100", edge="0.0300",
                decision="candidate"):
    """One signal on `market`, in the shape `candidate_signals` returns and the worker loads."""
    signal = Signal(run_id=next(_RUNS), variant_id="v_base", gap_snapshot_id=1,
                    venue_market_id=market.id, side="yes", fair_p=Decimal(fair_p),
                    fair_source="direct", price_target=Decimal("0.4800"),
                    edge=Decimal(edge), edge_min=Decimal("0.0100"), stake=Decimal("10.00"),
                    contracts=20, decision=decision, labels={}, created_at=created_at)
    session.add(signal)
    session.flush()
    return signal


def seed_history(session, *, game, market, as_of, hours=6, fair_p="0.5100",
                 disagreement="0.0040", staleness_s=40):
    """Fair values, book lines and venue quotes over the `hours` before `as_of`.

    One row every twenty minutes, plus **one row after `as_of`**: the point of ruling A-I3 is
    that the later row never reaches the feature vector, and a fixture with nothing after the
    signal could not tell a correct cut from no cut at all.
    """
    for step in range(hours * 3):
        ts = as_of - timedelta(minutes=20 * step)
        # `uq_fair_value_row` keys on `run_id` and `uq_odds_snapshot_row` on `raw_id`, so every
        # point of the history is its own run: a fixture that reused one id would be storing a
        # single moment rather than six hours of it.
        ident = next(_RUNS)
        session.add(FairValue(run_id=ident, game_id=game.id, market_type="moneyline",
                              fair_p=Decimal(fair_p), fair_source="direct", n_groups=3,
                              disagreement=Decimal(disagreement), staleness_s=staleness_s,
                              created_at=ts))
        session.add(OddsSnapshot(raw_id=ident, run_id=ident, book="pinnacle", game_id=game.id,
                                 market_type="moneyline", price_decimal=Decimal("1.9600"),
                                 fetched_at=ts))
        session.add(VenueQuote(raw_id=ident, run_id=ident, venue_market_id=market.id,
                               yes_bid=Decimal("0.4700"), yes_ask=Decimal("0.4900"),
                               fetched_at=ts))
    # After the signal. Never visible to `build_features` (ruling A-I3).
    session.add(FairValue(run_id=next(_RUNS), game_id=game.id, market_type="moneyline",
                          fair_p=Decimal("0.9000"), fair_source="direct", n_groups=3,
                          disagreement=Decimal("0.0100"), staleness_s=5,
                          created_at=as_of + timedelta(minutes=10)))
    session.flush()


def seed_weather(session, *, game, fetched_at, short_forecast=FORECAST):
    session.add(WeatherSnapshot(run_id=1, game_id=game.id, fetched_at=fetched_at,
                                period_start=fetched_at + timedelta(hours=1),
                                temperature_f=72, wind_mph=8, wind_dir="SSE", precip_pct=20,
                                short_forecast=short_forecast, roof="open"))
    session.flush()


def enqueue(session, *, signal, game, bucket_start, enqueued_at, market_type="moneyline"):
    session.add(VetoQueue(signal_id=signal.id, game_id=game.id, market_type=market_type,
                          bucket_start=bucket_start, enqueued_at=enqueued_at, claimed_at=None))
    session.flush()
