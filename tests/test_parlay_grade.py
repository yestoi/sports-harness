"""Grading a placed card: leg by leg through the push rules, then the card, then the ledger.

**Each fixture seeds** one `teams` row per side, one `games` row per leg, one `parlay_cards` row
with `status = 'placed'`, its `parlay_legs` rows with `status = 'alive'`, and one
`parlay_placements` row plus the `parlay_ledger` `stake` row `mark_placed` would have written.
What distinguishes them is the games' final scores: `placed_card_final` every game `final` with a
mix of outcomes; `placed_card_push` one `moneyline` leg whose game finished tied;
`placed_card_all_hit` every leg's side won; `placed_card_one_miss` one leg's side lost;
`placed_card_all_void` every leg is a tie or a `postponed` game; `placed_card_one_live` one game
still `in_progress`; `two_placed_cards_final` two independent cards, both fully final.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from harness.settlement.job import Budget, STAGE_MODULES
from harness.settlement.parlay_grade import MIN_BUDGET_S, grade_parlays

NOW = datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc)


def _budget(seconds=600.0):
    ticks = iter([0.0] * 500)
    return Budget(seconds, lambda: next(ticks, 0.0))


def test_parlay_grade_is_registered_immediately_after_settle():
    """Review B, underspecified item 4, and ruling B-I7. It grades against the finals `settle`
    just resolved, and a stage placed last is the one that never runs on a busy Sunday."""
    assert STAGE_MODULES.index("harness.settlement.parlay_grade") == \
        STAGE_MODULES.index("harness.settlement.settle") + 1


def test_a_leg_whose_game_is_final_grades_hit_or_miss(db_session, placed_card_final):
    grade_parlays(db_session, NOW, _budget())
    statuses = db_session.execute(text(
        "select status from parlay_legs order by seq")).scalars().all()
    assert set(statuses) <= {"hit", "miss", "void"}
    assert "pending" not in statuses and "alive" not in statuses


def test_a_push_grades_void_not_a_hit(db_session, placed_card_push):
    """`resolve_market`'s push rules: a tied moneyline pays half a contract in the paper book,
    and a refund is not a win. The leg is `void` and the card is graded around it."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select status from parlay_legs where seq = 1")).scalar() == "void"


def test_a_card_with_every_leg_hit_cashes_and_pays(db_session, placed_card_all_hit):
    grade_parlays(db_session, NOW, _budget())
    card = db_session.execute(text("select status from parlay_cards")).scalar()
    ledger = db_session.execute(text(
        "select kind, amount from parlay_ledger order by kind")).all()
    assert card == "cashed"
    assert [r.kind for r in ledger] == ["return", "stake"]
    assert ledger[0].amount > 0


def test_a_card_with_one_miss_busts_and_pays_nothing(db_session, placed_card_one_miss):
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text("select status from parlay_cards")).scalar() == "busted"
    assert db_session.execute(text(
        "select count(*) from parlay_ledger where kind = 'return'")).scalar() == 0


def test_a_card_whose_every_leg_voids_is_void_and_the_stake_comes_back(db_session,
                                                                       placed_card_all_void):
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text("select status from parlay_cards")).scalar() == "void"
    assert db_session.execute(text(
        "select kind from parlay_ledger where kind = 'void'")).scalar() == "void"


def test_a_postponed_leg_voids_with_no_score_but_a_final_leg_without_one_stays_ungraded(
        db_session, placed_card_postponed_and_unscored_final):
    """A void needs no result: a postponed game will never be played, so its leg voids on
    status alone. `final`/`final_ot` are the only statuses "never guess a result" still applies
    to, so a final leg with no recorded score yet stays ungraded."""
    grade_parlays(db_session, NOW, _budget())
    statuses = {row.seq: row.status for row in db_session.execute(text(
        "select seq, status from parlay_legs order by seq"))}
    assert statuses[1] == "void"
    assert statuses[2] not in ("hit", "miss", "void")


def test_a_card_with_an_ungraded_leg_stays_alive(db_session, placed_card_one_live):
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text("select status from parlay_cards")).scalar() == "alive"


def test_the_statuses_are_the_ticket_surface_s_vocabulary(db_session, placed_card_all_hit):
    """Ruling B-C4: `cashed | busted | void`, never `won | lost`. The shipped Ticket builder
    filters on that vocabulary in five places and a card outside it renders nowhere."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select distinct status from parlay_cards")).scalars().all() == ["cashed"]


def test_grading_twice_pays_once(db_session, placed_card_all_hit):
    grade_parlays(db_session, NOW, _budget())
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select count(*) from parlay_ledger where kind = 'return'")).scalar() == 1


def test_a_spent_budget_yields_and_resumes_next_hour(db_session, two_placed_cards_final):
    """Ruling B-I7: the settler's budget is shared across every stage, so this one yields rather
    than running the job past its period, and the next run simply resumes."""
    spent = Budget(0.0, lambda: 1.0)
    result = grade_parlays(db_session, NOW, spent)
    assert result.budget_exhausted is True
    assert db_session.execute(text(
        "select count(*) from parlay_cards where status in ('cashed','busted','void')"
    )).scalar() < 2
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select count(*) from parlay_cards where status = 'placed'")).scalar() == 0


def test_the_stage_floor():
    assert MIN_BUDGET_S == 30
