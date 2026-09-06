import json
from datetime import date, datetime, timezone
from pathlib import Path

from harness.db.models import Game
from harness.matching.games import find_game_by_pair, upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
ODDS = [{"id": "ev1", "sport_key": "americanfootball_nfl", "commence_time": "2026-09-21T00:20:00Z",
         "home_team": "Los Angeles Rams", "away_team": "New York Giants", "bookmakers": []},
        {"id": "ev2", "sport_key": "americanfootball_nfl", "commence_time": "2026-09-21T20:25:00Z",
         "home_team": "Kansas City Chiefs", "away_team": "Nowhere FC", "bookmakers": []}]


def test_upsert_games_and_find_by_pair(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    res = upsert_games_from_odds(db_session, "nfl", ODDS, raw_id=1)
    assert res.created == 1 and res.unresolved == ["Nowhere FC"]
    g = db_session.query(Game).filter_by(odds_api_event_id="ev1").one()
    assert g.home_team_id == 14 and g.away_team_id == 19  # Rams, Giants ESPN ids (verify in fixture)
    found = find_game_by_pair(db_session, "nfl", 19, 14, date(2026, 9, 20))
    assert [x.id for x in found] == [g.id]
    assert find_game_by_pair(db_session, "nfl", 19, 14, date(2026, 9, 25)) == []
    res2 = upsert_games_from_odds(db_session, "nfl", ODDS, raw_id=2)
    assert res2.created == 0 and res2.updated == 0
