"""`needs(leg, score)`: what still has to happen for a leg to hit. Pure, and the only parlay
logic that ships before phase 5c's writers."""

from decimal import Decimal

import pytest

from harness.parlay.needs import LegSpec, ScoreState, needs

HOME, AWAY = 1, 2


def _score(home, away, status="in_progress"):
    return ScoreState(status=status, home_team_id=HOME, away_team_id=AWAY,
                      home_score=home, away_score=away)


def _ml(team=HOME):
    return LegSpec("ml", team, None, None)


def _spread(team=HOME, line="-7.0"):
    return LegSpec("spread", team, None, Decimal(line))


def _total(side="over", line="55.5"):
    return LegSpec("total", None, side, Decimal(line))


def test_no_score_yet():
    assert needs(_ml(), None) == "no score yet"
    assert needs(_spread(), None) == "no score yet"
    assert needs(_total(), None) == "no score yet"


def test_moneyline_leading_trailing_and_final():
    assert needs(_ml(), _score(17, 10)) == "any win does it"
    assert needs(_ml(), _score(10, 17)) == "needs to come back from 7"
    assert needs(_ml(), _score(10, 10)) == "any win does it"
    assert needs(_ml(), _score(24, 17, status="final")) == "already done"
    assert needs(_ml(), _score(17, 24, status="final")) == "did not happen"
    assert needs(_ml(), _score(17, 17, status="final")) == "push, refunded"


def test_moneyline_reads_the_away_side_too():
    assert needs(_ml(AWAY), _score(10, 17)) == "any win does it"
    assert needs(_ml(AWAY), _score(17, 10)) == "needs to come back from 7"


def test_spread_needs_points_and_says_when_it_is_covering():
    assert needs(_spread(line="-7.5"), _score(17, 10)) == "needs 1 more point"
    assert needs(_spread(line="-7.5"), _score(24, 10)) == "covering by 6.5"
    assert needs(_spread(line="-3.5"), _score(0, 0)) == "needs 4 or more"
    assert needs(_spread(line="+3.5"), _score(0, 7)) == "needs 4 or more"


def test_a_whole_number_spread_can_push():
    assert needs(_spread(line="-7.0"), _score(17, 10)) == "push as it stands"
    assert needs(_spread(line="-7.0"), _score(17, 10, status="final")) == "push, refunded"
    assert needs(_spread(line="-7.0"), _score(24, 10, status="final")) == "already done"
    assert needs(_spread(line="-7.0"), _score(14, 10, status="final")) == "did not happen"


def test_the_away_side_of_a_spread():
    assert needs(_spread(team=AWAY, line="-3.5"), _score(0, 7)) == "covering by 3.5"


def test_total_over_and_under():
    assert needs(_total("over", "55.5"), _score(20, 20)) == "needs 16 more points"
    assert needs(_total("over", "55.5"), _score(30, 30)) == "already done"
    assert needs(_total("under", "55.5"), _score(20, 20)) == \
        "needs the total to stay under 55.5, 15 points of room"
    assert needs(_total("under", "55.5"), _score(30, 30)) == "did not happen"


def test_a_whole_number_total_can_push():
    assert needs(_total("over", "55"), _score(28, 27, status="final")) == "push, refunded"
    assert needs(_total("under", "55"), _score(28, 27, status="final")) == "push, refunded"
    assert needs(_total("over", "55"), _score(28, 27)) == "push as it stands"


def test_a_finished_total_reads_already_done_or_not():
    assert needs(_total("over", "55.5"), _score(30, 30, status="final")) == "already done"
    assert needs(_total("under", "55.5"), _score(20, 20, status="final")) == "already done"


def test_an_unknown_market_type_is_honest_rather_than_wrong():
    assert needs(LegSpec("parlay", None, None, None), _score(0, 0)) == "no rule for this bet"


def test_the_function_is_pure_and_touches_no_database():
    from pathlib import Path

    from harness.parlay import needs as module

    body = Path(module.__file__).read_text().lower()
    for word in ("session", "select", "sqlalchemy", "text("):
        assert word not in body
