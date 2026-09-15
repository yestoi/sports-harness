"""`needs(leg, score)`: what still has to happen for a leg to hit. Pure, and the only parlay
logic that ships before phase 5c's writers."""

from decimal import Decimal

import pytest

from harness.parlay.needs import LegSpec, ScoreState, StatState, leg_outcome, needs

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


# --- T12 fix round 1: `leg_outcome`, rulings C1/C2/I1 -------------------------------------------


def test_leg_outcome_agrees_with_the_sentence_it_renders_beside():
    """A grid of final cases: `needs` and `leg_outcome` share `_margin`, so they can never
    disagree -- "already done" <-> hit, "did not happen" <-> miss, "push, refunded" <-> push."""
    cases = [
        (_ml(), _score(24, 17, status="final"), "already done", "hit"),
        (_ml(), _score(17, 24, status="final"), "did not happen", "miss"),
        (_ml(), _score(17, 17, status="final"), "push, refunded", "push"),
        (_spread(line="-7.0"), _score(24, 10, status="final"), "already done", "hit"),
        (_spread(line="-7.0"), _score(14, 10, status="final"), "did not happen", "miss"),
        (_spread(line="-7.0"), _score(17, 10, status="final"), "push, refunded", "push"),
        (_total("over", "55.5"), _score(30, 30, status="final"), "already done", "hit"),
        (_total("under", "55.5"), _score(30, 30, status="final"), "did not happen", "miss"),
        (_total("over", "55"), _score(28, 27, status="final"), "push, refunded", "push"),
        (_total("under", "55"), _score(28, 27, status="final"), "push, refunded", "push"),
    ]
    for leg, score, sentence, outcome in cases:
        assert needs(leg, score) == sentence
        assert leg_outcome(leg, score) == outcome


def test_leg_outcome_is_none_before_a_result_exists():
    assert leg_outcome(_ml(), None) is None
    assert leg_outcome(_ml(), _score(17, 10)) is None                    # in_progress
    assert leg_outcome(_ml(), _score(17, 10, status="halftime")) is None  # not FINAL_STATUSES


def test_leg_outcome_voids_a_postponed_or_canceled_game_on_status_alone():
    """Review I2 ruling: a void needs no result, score or not."""
    assert leg_outcome(_ml(), _score(0, 0, status="postponed")) == "void"
    assert leg_outcome(_spread(), _score(0, 0, status="canceled")) == "void"


def test_leg_outcome_raises_on_an_unrecognized_market_type():
    with pytest.raises(ValueError):
        leg_outcome(LegSpec("parlay", None, None, None), _score(0, 0, status="final"))


def test_leg_outcome_raises_on_a_spread_or_total_with_no_threshold():
    with pytest.raises(ValueError):
        leg_outcome(LegSpec("spread", HOME, None, None), _score(24, 10, status="final"))
    with pytest.raises(ValueError):
        leg_outcome(LegSpec("total", None, "over", None), _score(24, 10, status="final"))


def test_leg_outcome_the_favourite_that_covers_by_less_than_the_line_misses():
    """Review C1: a favourite laying seven that wins by three is a miss, not a hit --
    `resolve_market`'s inverted sign would have said the opposite."""
    assert leg_outcome(_spread(line="-7.0"), _score(24, 21, status="final")) == "miss"


def test_leg_outcome_the_underdog_that_loses_by_less_than_the_line_hits():
    """Review C1: an underdog taking seven that loses by three is a hit, not a miss."""
    dog = LegSpec("spread", AWAY, None, Decimal("7.0"))
    assert leg_outcome(dog, _score(24, 21, status="final")) == "hit"


def test_leg_outcome_reads_the_total_s_own_side():
    """Review C2: an under leg is graded by its own side, never as an over."""
    assert leg_outcome(_total("under", "44"), _score(20, 17, status="final")) == "hit"    # 37
    assert leg_outcome(_total("over", "44"), _score(22, 22, status="final")) == "push"    # 44


# --- Phase 4.6 Task 5: prop needs phrases and the final grade per operator (addendum 4.4) ------
#
# `StatState` is the second input beside `ScoreState`: one player's newest recorded value for one
# stat, with the *game's* finality, not the stat's. A value read before the final box score is
# provisional, because a later poll may correct it downward.

PASS = LegSpec("prop", None, None, Decimal("225"), stat="pass_yds", operator="over",
               player_id=7)
ATLEAST = LegSpec("prop", None, None, Decimal("50"), stat="rush_yds", operator="atleast",
                  player_id=8)
UNDER = LegSpec("prop", None, None, Decimal("81"), stat="rec_yds", operator="under", player_id=9)
TD = LegSpec("prop", None, None, None, stat="anytime_td", operator="yes", player_id=10)
LIVE = ScoreState("in_progress", 1, 2, 14, 10)
FINAL = ScoreState("final", 1, 2, 24, 21)


def _stat(value, final=False, stat="pass_yds"):
    return StatState(stat=stat, value=Decimal(str(value)), source_ts=None, final=final)


def test_a_prop_under_its_line_says_how_much_is_left():
    assert needs(PASS, LIVE, _stat(208)) == "17 to go"


def test_a_prop_over_its_line_before_final_is_provisional():
    """Addendum §4.4: `reached` and `provisional until final`, because a stat can still be
    corrected downward and a slip that said `already done` would be lying."""
    assert needs(PASS, LIVE, _stat(240)) == "reached 240 of 225 · provisional until final"


def test_an_under_leg_reads_live_until_the_game_ends():
    assert needs(UNDER, LIVE, _stat(50, stat="rec_yds")) == "under by 31 · live until the game ends"
    assert needs(UNDER, LIVE, _stat(95, stat="rec_yds")) == "over by 14"


def test_an_anytime_touchdown_reads_yes_or_not_yet():
    assert needs(TD, LIVE, _stat(0, stat="anytime_td")) == "no touchdown yet"
    assert needs(TD, LIVE, _stat(1, stat="anytime_td")) == "scored · provisional until final"


def test_no_stat_state_reads_no_stat_yet_and_never_zero():
    """Roadmap phase 4.6 item 2: missing stat state reads unknown, never zero."""
    assert needs(PASS, LIVE, None) == "no stat yet"


def test_a_whole_number_line_pushes_on_over_and_under_but_never_on_atleast():
    """Computed independently of the code: `over 225` at exactly 225 is a push at DraftKings
    (the line is whole), `under 81` at exactly 81 is a push, and `225+` (`atleast`) is a hit at
    exactly 225 -- it is a different market, not the same market read differently.
    """
    assert leg_outcome(PASS, FINAL, _stat(225, final=True)) == "push"
    assert leg_outcome(PASS, FINAL, _stat(226, final=True)) == "hit"
    assert leg_outcome(PASS, FINAL, _stat(224, final=True)) == "miss"
    assert leg_outcome(UNDER, FINAL, _stat(81, final=True, stat="rec_yds")) == "push"
    assert leg_outcome(ATLEAST, FINAL, _stat(50, final=True, stat="rush_yds")) == "hit"
    assert leg_outcome(ATLEAST, FINAL, _stat(49.9, final=True, stat="rush_yds")) == "miss"


def test_a_missing_stat_at_final_is_none_never_a_miss():
    """Addendum §4.4: absence is not evidence. The leg stays pending and `parlay_grade` counts
    `stat_missing`; a card that graded a leg missing because the feed never reported it would
    bust a slip the book will pay."""
    assert leg_outcome(PASS, FINAL, None) is None
    assert leg_outcome(PASS, FINAL, _stat(300, final=False)) is None


def test_the_game_line_rules_are_unchanged():
    """This task adds an operator family; it changes nothing about ml/spread/total."""
    ml = LegSpec("ml", 1, None, None)
    assert leg_outcome(ml, FINAL) == "hit"
    assert needs(ml, LIVE) == "any win does it"


def test_a_prop_sitting_on_a_whole_number_line_is_called_a_push():
    """Fix round 1, review I6: this module's contract (lines 9-10) is that a push on a
    whole-number line is called a push, in progress and at the final whistle alike. A live slip
    reading `reached 225 of 225` about a leg that will refund is lying to its reader. `atleast`
    is the exception, because `225+` at exactly 225 is a hit."""
    assert needs(PASS, LIVE, _stat(225)) == "on the line · push as it stands"
    assert needs(UNDER, LIVE, _stat(81, stat="rec_yds")) == "on the line · push as it stands"
    assert needs(ATLEAST, LIVE, _stat(50, stat="rush_yds")) == \
        "reached 50 of 50 · provisional until final"


def test_a_period_prop_is_never_graded_from_the_game_long_value():
    """Fix round 1, review I4 (controller ruling): release one grades the full game only (D3).
    A `1h` leg graded off the game-long stat would be a wrong money grade with no error, so it
    raises -- `grade_parlays` isolates the card and counts `errors` -- and the live phrase says
    there is no rule rather than quoting a number that is not this leg's."""
    half = LegSpec("prop", None, None, Decimal("120"), stat="pass_yds", operator="over",
                   player_id=7, period="1h")
    with pytest.raises(ValueError):
        leg_outcome(half, FINAL, _stat(240, final=True))
    assert needs(half, LIVE, _stat(240)) == "no rule for this bet"
