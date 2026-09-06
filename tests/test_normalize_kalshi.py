import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game, OrderbookSnapshot, VenueMarket, VenueQuote, VenueTrade
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.kalshi import insert_orderbook, insert_trades, insert_venue_quotes, upsert_venue_markets

FIXD = Path(__file__).parent / "fixtures"
KM = json.loads((FIXD / "kalshi_markets_page.json").read_text())["markets"]  # phase 0 fixture: NYG game + KC spread
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
EVENTS = {"KXNFLGAME-26SEP21NYGLAR": {"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"},
          "KXNFLSPREAD-26SEP14DENKC": {"event_ticker": "KXNFLSPREAD-26SEP14DENKC", "title": "Denver vs Kansas City"}}


def test_upsert_venue_markets_matches_and_classifies(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)  # must include Giants, Rams, Chiefs (fixture) — Broncos absent on purpose
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    res = upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    assert res.new == 2 and res.matched == 1 and res.unmatched == 1
    nyg = db_session.query(VenueMarket).filter_by(ticker="KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert nyg.market_type == "moneyline" and nyg.match_status == "matched" and nyg.side_team_id == 19
    kc = db_session.query(VenueMarket).filter_by(ticker="KXNFLSPREAD-26SEP14DENKC-KC7").one()
    assert kc.market_type == "spread" and kc.threshold == Decimal("6.5") and kc.match_status == "unmatched"
    assert "unresolved: Denver" in kc.match_reason
    res2 = upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=2, fetched_at=NOW)
    assert res2.new == 0


def test_quotes_orderbook_trades_idempotent(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    assert insert_venue_quotes(db_session, KM, raw_id=1, run_id=1, fetched_at=NOW) == 2
    assert insert_venue_quotes(db_session, KM, raw_id=1, run_id=1, fetched_at=NOW) == 0
    q = db_session.query(VenueQuote).join(VenueMarket, VenueMarket.id == VenueQuote.venue_market_id).filter(VenueMarket.ticker == "KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert (q.yes_bid, q.yes_ask, q.volume) == (Decimal("0.2200"), Decimal("0.2300"), Decimal("1234.00"))
    ob = json.loads((FIXD / "kalshi_orderbook.json").read_text())
    assert insert_orderbook(db_session, "KXNFLGAME-26SEP21NYGLAR-NYG", ob, raw_id=5, fetched_at=NOW) is True
    assert insert_orderbook(db_session, "KXNFLGAME-26SEP21NYGLAR-NYG", ob, raw_id=5, fetched_at=NOW) is False
    tr = json.loads((FIXD / "kalshi_trades_page.json").read_text())
    assert insert_trades(db_session, tr, raw_id=6) == 2
    assert insert_trades(db_session, tr, raw_id=6) == 0
    t = db_session.query(VenueTrade).order_by(VenueTrade.ts).first()
    assert t.source == "rest" and t.yes_price == Decimal("0.2300") and t.taker_side in ("yes", "no")
