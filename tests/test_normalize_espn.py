import json
from pathlib import Path

from sqlalchemy import text

from harness.db.models import Game, GameScoreEvent
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


def _in_progress_body(period, clock, home_score, away_score):
    return {"events": [{"id": "401", "date": "2026-09-21T00:20Z",
            "status": {"type": {"name": "STATUS_IN_PROGRESS"}, "period": period, "displayClock": clock},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": str(home_score), "team": {"id": "14", "displayName": "Los Angeles Rams"}},
                {"homeAway": "away", "score": str(away_score), "team": {"id": "19", "displayName": "New York Giants"}}]}]}]}


def test_score_event_on_clock_change_not_on_identical_body(db_session):
    """Task 12b: `game_score_events` appends whenever `(status, period, clock, home_score,
    away_score)` differs from the game's newest row, and appends nothing for a re-poll that
    changed nothing (ESPN is polled every tick, so the identical case is the common one)."""
    seed_teams_from_espn(db_session, "nfl", NFL)

    link_espn_scoreboard(db_session, "nfl", _in_progress_body(2, "12:34", 7, 0))
    game = db_session.query(Game).filter_by(espn_event_id="401").one()
    rows = db_session.query(GameScoreEvent).filter_by(game_id=game.id).order_by(GameScoreEvent.id).all()
    assert len(rows) == 1
    first = rows[0]
    assert (first.status, first.period, first.clock, first.home_score, first.away_score) == (
        "in_progress", 2, "12:34", 7, 0)
    assert first.raw_id is None

    # The identical body again (a re-poll with nothing new): no second row.
    link_espn_scoreboard(db_session, "nfl", _in_progress_body(2, "12:34", 7, 0))
    assert db_session.query(GameScoreEvent).filter_by(game_id=game.id).count() == 1

    # Only the clock changed: a new row.
    link_espn_scoreboard(db_session, "nfl", _in_progress_body(2, "11:50", 7, 0))
    rows = db_session.query(GameScoreEvent).filter_by(game_id=game.id).order_by(GameScoreEvent.id).all()
    assert len(rows) == 2
    assert rows[-1].clock == "11:50"


def test_score_event_carries_the_raw_response_id(db_session):
    """Coverage fix: `game_score_events.raw_id` (design spec §3.5) is populated from the
    caller's raw_responses row, threaded through `harness/normalize/runner.py`'s call."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    link_espn_scoreboard(db_session, "nfl", SB, raw_id=555)
    game = db_session.query(Game).filter_by(espn_event_id="401").one()
    row = db_session.query(GameScoreEvent).filter_by(game_id=game.id).one()
    assert row.raw_id == 555


def test_score_event_failure_does_not_poison_the_linker(db_session, monkeypatch):
    """Fix round 1, I2: a database-level failure inside the score-event write must not abort
    the linker's transaction -- the game's own status/score update (and the final flush) must
    still land."""
    import harness.normalize.espn as espn_mod

    seed_teams_from_espn(db_session, "nfl", NFL)
    monkeypatch.setattr(espn_mod, "_NEWEST_SCORE_EVENT", text("select 1/0"))

    res = link_espn_scoreboard(db_session, "nfl", SB)  # must not raise
    assert res.linked == 1

    game = db_session.query(Game).filter_by(espn_event_id="401").one()
    assert game.status == "final"
    assert (game.home_score, game.away_score) == (24, 17)
    assert db_session.query(GameScoreEvent).filter_by(game_id=game.id).count() == 0
