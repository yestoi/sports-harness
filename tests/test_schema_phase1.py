from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.db.models import Game, OddsSnapshot, Team, TeamAlias, VenueMarket, VenueTrade

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_phase1_tables_exist_and_roundtrip(db_session):
    db_session.add(Team(id=26, sport="ncaaf", display_name="UCLA Bruins", location="UCLA", name="Bruins",
                        abbreviation="UCLA", short_display_name="UCLA"))
    db_session.add(TeamAlias(sport="ncaaf", source="kalshi_name", raw_name="UCLA", team_id=26))
    g = Game(sport="ncaaf", home_team_id=26, away_team_id=26, kickoff_utc=NOW, odds_api_event_id="e1")
    db_session.add(g)
    db_session.flush()
    db_session.add(VenueMarket(venue="kalshi", ticker="T", event_ticker="E", series_ticker="KXNCAAFGAME", game_id=g.id,
                               market_type="moneyline", side_team_id=26, match_confidence=Decimal("1.00"),
                               match_status="matched", match_reason="pair+date", first_seen_raw_id=1, last_seen_at=NOW))
    db_session.add(VenueTrade(venue="kalshi", trade_id="t1", ticker="T", ts=NOW, yes_price=Decimal("0.2300"),
                              count=Decimal("5.00"), taker_side="yes", is_block=False, source="rest", raw_id=None))
    db_session.flush()
    names = set(db_session.execute(text("select tablename from pg_tables where schemaname='public'")).scalars())
    assert {"teams", "team_aliases", "games", "odds_snapshots", "venue_markets", "venue_quotes",
            "orderbook_snapshots", "venue_trades", "orderbook_events", "normalize_state"} <= names


def test_odds_snapshot_unique_index_treats_nulls_as_equal(db_session):
    from sqlalchemy.exc import IntegrityError
    import pytest
    row = dict(raw_id=1, run_id=1, book="pinnacle", game_id=None, market_type="h2h", outcome_team_id=None,
               outcome_side=None, point=None, price_decimal=Decimal("1.9"), book_last_update=NOW, fetched_at=NOW)
    db_session.add(OddsSnapshot(**row))
    db_session.flush()
    db_session.add(OddsSnapshot(**row))
    with pytest.raises(IntegrityError):
        db_session.flush()
