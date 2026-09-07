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


def _venue_trade_columns(session) -> dict[str, tuple]:
    return {c: (dt, n, ln) for c, dt, n, ln in session.execute(text(
        "select column_name, data_type, is_nullable, character_maximum_length "
        "from information_schema.columns where table_name = 'venue_trades'")).all()}


def test_venue_trades_carries_taker_outcome_and_book_side_idempotently(db_session):
    # F5: Kalshi deprecated `taker_side` on trades in favour of `taker_outcome_side` and
    # `taker_book_side`. Both are additive nullable columns, so create_schema must add them to a
    # venue_trades that predates them and stay idempotent when the next boot runs it again.
    # create_all never alters an existing table, so only the ADD COLUMN statements can do it.
    from harness.db.schema import create_schema

    # stand in for the deployed database, which has the table but not the column
    db_session.execute(text("alter table venue_trades drop column taker_outcome_side"))
    db_session.commit()  # release the lock before create_schema opens its own connection
    assert "taker_outcome_side" not in _venue_trade_columns(db_session)

    create_schema(db_session.get_bind())
    create_schema(db_session.get_bind())  # the boot after that one runs it again
    cols = _venue_trade_columns(db_session)
    assert cols.get("taker_outcome_side") == ("character varying", "YES", 4), sorted(cols)
    assert cols.get("taker_book_side") == ("character varying", "YES", 4), sorted(cols)
    assert cols["taker_side"][1] == "NO"  # the canonical side stays required
    db_session.add(VenueTrade(venue="kalshi", trade_id="t-cols", ticker="T", ts=NOW, yes_price=Decimal("0.2300"),
                              count=Decimal("5.00"), taker_side="no", taker_outcome_side="no",
                              taker_book_side="ask", is_block=False, source="ws", raw_id=None))
    db_session.flush()
