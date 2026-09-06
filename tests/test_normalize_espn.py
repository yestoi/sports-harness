import json
from pathlib import Path

from harness.db.models import Game
from harness.matching.games import upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.espn import link_espn_scoreboard

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
SB = {"events": [{"id": "401", "date": "2026-09-21T00:20Z", "status": {"type": {"name": "STATUS_FINAL"}},
      "competitions": [{"competitors": [
          {"homeAway": "home", "score": "24", "team": {"id": "14", "displayName": "Los Angeles Rams"}},
          {"homeAway": "away", "score": "17", "team": {"id": "19", "displayName": "New York Giants"}}]}]}]}


def test_link_scoreboard_sets_espn_id_status_scores(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_games_from_odds(db_session, "nfl", [{"id": "ev1", "commence_time": "2026-09-21T00:20:00Z",
                           "home_team": "Los Angeles Rams", "away_team": "New York Giants"}], raw_id=1)
    res = link_espn_scoreboard(db_session, "nfl", SB)
    assert res.linked == 1
    g = db_session.query(Game).filter_by(odds_api_event_id="ev1").one()
    assert g.espn_event_id == "401" and g.status == "final" and (g.home_score, g.away_score) == (24, 17)


def test_link_creates_espn_only_game(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    res = link_espn_scoreboard(db_session, "nfl", SB)
    assert res.linked == 1
    assert db_session.query(Game).filter_by(espn_event_id="401", odds_api_event_id=None).count() == 1


def test_unknown_status_is_kept_raw_lowercased(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    sb = {"events": [{"id": "402", "date": "2026-09-21T00:20Z", "status": {"type": {"name": "STATUS_SUSPENDED"}},
          "competitions": [{"competitors": [
              {"homeAway": "home", "team": {"id": "14", "displayName": "Los Angeles Rams"}},
              {"homeAway": "away", "team": {"id": "19", "displayName": "New York Giants"}}]}]}]}
    link_espn_scoreboard(db_session, "nfl", sb)
    assert db_session.query(Game).filter_by(espn_event_id="402").one().status == "status_suspended"
