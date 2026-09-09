"""R4's game window as one bounded query over `games` (addendum §1)."""

from datetime import datetime, timedelta, timezone

from harness.dashboard.window import game_window_open
from harness.db.models import Game

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)


def _game(session, *, sport="ncaaf", kickoff, status="scheduled"):
    row = Game(sport=sport, home_team_id=1, away_team_id=2, kickoff_utc=kickoff, status=status)
    session.add(row)
    session.flush()
    return row


def test_no_games_means_the_window_is_shut(db_session):
    assert game_window_open(db_session, NOW) is False


def test_a_game_in_progress_opens_it(db_session):
    _game(db_session, kickoff=NOW - timedelta(days=2), status="in_progress")
    assert game_window_open(db_session, NOW) is True


def test_a_kickoff_in_the_last_four_hours_opens_it(db_session):
    _game(db_session, kickoff=NOW - timedelta(hours=3))
    assert game_window_open(db_session, NOW) is True


def test_a_kickoff_in_the_next_fifteen_minutes_opens_it(db_session):
    _game(db_session, kickoff=NOW + timedelta(minutes=10))
    assert game_window_open(db_session, NOW) is True


def test_an_nfl_kickoff_between_sixty_and_a_hundred_minutes_out_opens_it(db_session):
    _game(db_session, sport="nfl", kickoff=NOW + timedelta(minutes=75))
    assert game_window_open(db_session, NOW) is True


def test_a_college_kickoff_seventy_five_minutes_out_does_not(db_session):
    """The 60-100 minute clause is the NFL pre-game rule only (R4)."""
    _game(db_session, sport="ncaaf", kickoff=NOW + timedelta(minutes=75))
    assert game_window_open(db_session, NOW) is False


def test_a_kickoff_five_hours_ago_does_not(db_session):
    _game(db_session, kickoff=NOW - timedelta(hours=5), status="final")
    assert game_window_open(db_session, NOW) is False
