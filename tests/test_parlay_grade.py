"""Grading a placed card: leg by leg through the push rules, then the card, then the ledger.

**Each fixture seeds** one `teams` row per side, one `games` row per leg, one `parlay_cards` row
with `status = 'placed'`, its `parlay_legs` rows with `status = 'alive'`, and one
`parlay_placements` row plus the `parlay_ledger` `stake` row `mark_placed` would have written.
What distinguishes them is the games' final scores: `placed_card_final` every game `final` with a
mix of outcomes; `placed_card_push` one `moneyline` leg whose game finished tied;
`placed_card_all_hit` every leg's side won; `placed_card_one_miss` one leg's side lost;
`placed_card_all_void` every leg is a tie or a `postponed` game; `placed_card_one_live` one game
still `in_progress`; `two_placed_cards_final` two independent cards, both fully final.

**T12 fix round 1** adds a second family, still on that same shape, but on a spread or total
leg: `placed_card_favourite_covers_by_three`, `placed_card_underdog_covers`,
`placed_card_spread_push`, `placed_card_under_hits`, `placed_card_total_push` (review C1, C2,
I1); `placed_card_hit_and_push` (review I4); and `placed_card_malformed_beside_a_good_one`,
which commits rather than merely flushing (review I2 -- see its own docstring for why).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import text

from harness.db.models import (Game, ParlayCard, ParlayLedger, ParlayLeg, ParlayPlacement,
                               PlayerStatEvent)
from harness.research.spend import chicago_day
from harness.settlement.job import Budget, STAGE_MODULES
from harness.settlement.parlay_grade import MIN_BUDGET_S, grade_parlays
from tests.conftest import PARLAY_NOW, _make_graded_card, _next_id, _pf_leg

NOW = datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc)
#: Phase 4.6 Task 5's moments: the settlement pass, and the same pass run a week later (D20).
T0 = NOW
T0_NEXT_WEEK = NOW + timedelta(days=7)


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
    """`leg_outcome`'s push rules: a tied moneyline pushes, and a refund is not a win. The leg
    is `void` and the card is graded around it."""
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


# --- T12 fix round 1: spread/total grading, the re-priced push, and per-card isolation ---------
#
# Review C1, C2, I1, I4, I2. Every fixture up to this point in the file is moneyline-only, which
# is how the sign inversion and the missing `side` handling reached the diff unchallenged.


def test_a_favourite_that_covers_by_less_than_the_line_misses(
        db_session, placed_card_favourite_covers_by_three):
    """Review C1: `resolve_market`'s venue-margin convention would have called this a hit."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select status from parlay_legs where seq = 1")).scalar() == "miss"


def test_an_underdog_that_loses_by_less_than_the_line_hits(
        db_session, placed_card_underdog_covers):
    """Review C1: the mirror case -- an underdog covers by beating its own line."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select status from parlay_legs where seq = 1")).scalar() == "hit"


def test_a_spread_landing_exactly_on_a_whole_number_line_pushes(
        db_session, placed_card_spread_push):
    """Review I1: DraftKings' whole-number lines push; `resolve_market`'s strict `>` would have
    busted it."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select status from parlay_legs where seq = 1")).scalar() == "void"


def test_an_under_leg_grades_by_its_own_side_not_as_an_over(db_session, placed_card_under_hits):
    """Review C2: `resolve_market` has no notion of `under` and would have graded this a miss."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select status from parlay_legs where seq = 1")).scalar() == "hit"


def test_a_total_landing_exactly_on_a_whole_number_line_pushes(
        db_session, placed_card_total_push):
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text(
        "select status from parlay_legs where seq = 1")).scalar() == "void"


def test_a_card_with_a_hit_and_a_push_is_repriced_off_the_surviving_leg(
        db_session, placed_card_hit_and_push):
    """Review I4: DraftKings drops a pushed leg and re-prices the slip off the survivors' own
    odds, so the recorded return is `stake_actual * dk_decimal` of the hit leg, not the
    `dk_payout_actual` quoted over both legs at placement."""
    grade_parlays(db_session, NOW, _budget())
    assert db_session.execute(text("select status from parlay_cards")).scalar() == "cashed"
    amount = db_session.execute(text(
        "select amount from parlay_ledger where kind = 'return'")).scalar()
    assert amount == (Decimal("25.00") * Decimal("1.9100")).quantize(Decimal("0.01"))


def test_a_malformed_card_is_isolated_and_the_good_card_still_grades(
        db_session, placed_card_malformed_beside_a_good_one):
    """Review I2: a spread/total leg with no threshold is data the stage cannot grade --
    `grade_parlays` isolates it per card, counts it as an error, and the next card still
    grades."""
    result = grade_parlays(db_session, NOW, _budget())
    assert result.counts["errors"] == 1
    fixtures = placed_card_malformed_beside_a_good_one
    bad_status = db_session.execute(text(
        "select status from parlay_cards where id = :id"), {"id": fixtures.bad.id}).scalar()
    good_status = db_session.execute(text(
        "select status from parlay_cards where id = :id"), {"id": fixtures.good.id}).scalar()
    assert bad_status == "placed"       # untouched, left for the next pass
    assert good_status == "cashed"      # graded despite the other card's failure


# --- Phase 4.6 Task 5: prop legs, computed provenance and the card's own week ------------------
#
# The helpers below extend the T12 card helpers (`tests.conftest._make_graded_card`, `_pf_leg`)
# with the prop columns addendum 9 added, rather than building a second card shape: a prop card
# is the same placed card, its leg carrying `market_type = 'prop'` (addendum 0's amendment row;
# the `prop:<stat>` keys are `odds_prop_snapshots`' own), a `player_id`, a `stat` and an
# `operator`. `_placement` is the only thing conftest cannot supply: addendum 5.1 keys the stake
# row to the card's `(year, week)`, and D20's test reads every row of the card.

_prop_prefix = count(1)


def _placement(session, card, now, stake=None):
    """The `parlay_placements` row and the `parlay_ledger` `stake` row `mark_placed` writes,
    keyed to the **card's** week (addendum 5.1) and marked `confirmed`: the owner typed it."""
    stake = stake if stake is not None else card.stake
    session.add(ParlayPlacement(card_id=card.id, placed_at=now, stake_actual=stake,
                                dk_payout_actual=card.dk_payout_est, dk_odds_actual=100,
                                note=None))
    session.add(ParlayLedger(ts=now, card_id=card.id, kind="stake", amount=stake,
                             year=card.year, week=card.week, source="confirmed"))
    session.flush()


def _legs(session, card):
    return session.query(ParlayLeg).filter_by(card_id=card.id).order_by(ParlayLeg.seq).all()


_UNSET = object()


def _prop_card(session, *, stat, line, operator, correlated=False, period="game",
               player_id=_UNSET):
    """One placed card with a single prop leg on a game that has not finished yet.

    `period` and `player_id` are the fix-round-1 handles: a leg the harness cannot grade (a
    period prop, D3; a leg with no player) has to reach `grade_parlays` to be counted.
    """
    card = _make_graded_card(session, status="placed", built_at=PARLAY_NOW)
    card.correlated = correlated
    # `pending`, not the T12 fixtures' `alive`: a prop leg has nothing recorded against it until
    # a stat row arrives, and the ungraded-leg assertions below read that word.
    _pf_leg(session, card, 1, f"PR{next(_prop_prefix)}", home_score=None, away_score=None,
            status="in_progress", leg_status="pending", market_type="prop", threshold=line)
    legs = _legs(session, card)
    for leg in legs:
        leg.side_team_id = None          # a prop leg names a player, not a side
        leg.player_id = _next_id() if player_id is _UNSET else player_id
        leg.stat = stat
        leg.period = period
        leg.operator = operator
    _placement(session, card, PARLAY_NOW)
    session.flush()
    return card, legs


def _final_game(session, card, *, home, away):
    """Every game under the card finishes with this score."""
    for leg in _legs(session, card):
        game = session.get(Game, leg.game_id)
        game.status = "final"
        game.home_score = home
        game.away_score = away
    session.flush()


def _stat_row(session, leg, *, value, ts, correction=False):
    """One `player_stat_events` row for the leg's `(game_id, player_id, stat)`."""
    session.add(PlayerStatEvent(game_id=leg.game_id, player_id=leg.player_id, ts=ts,
                                source_ts=None, stat=leg.stat, value=value, source="espn",
                                raw_id=None, correction=correction))
    session.flush()
    return leg


def _cashing_card(session, *, correlated=False, year=None, week=None):
    """A placed card whose two moneyline legs both hit: it cashes on the next pass."""
    card = _make_graded_card(session, status="placed", built_at=PARLAY_NOW)
    card.correlated = correlated
    if year is not None:
        card.year = year
    if week is not None:
        card.week = week
    session.flush()
    prefix = next(_prop_prefix)
    _pf_leg(session, card, 1, f"CC{prefix}A", home_score=30, away_score=10)
    _pf_leg(session, card, 2, f"CC{prefix}B", home_score=27, away_score=24)
    _placement(session, card, PARLAY_NOW)
    return card, _legs(session, card)


def test_a_prop_leg_grades_from_the_newest_final_stat_row(db_session):
    card, legs = _prop_card(db_session, stat="pass_yds", line=Decimal("225"), operator="over")
    _final_game(db_session, card, home=24, away=21)
    _stat_row(db_session, legs[0], value=Decimal("240"), ts=T0)
    counts = grade_parlays(db_session, T0, _budget()).counts
    assert legs[0].status == "hit" and counts["stat_missing"] == 0


def test_a_leg_with_no_stat_row_at_final_stays_pending_and_is_counted(db_session):
    card, legs = _prop_card(db_session, stat="rush_yds", line=Decimal("50"), operator="atleast")
    _final_game(db_session, card, home=24, away=21)
    counts = grade_parlays(db_session, T0, _budget()).counts
    assert legs[0].status == "pending" and counts["stat_missing"] == 1
    assert db_session.get(ParlayCard, card.id).status == "alive"


def test_an_owner_voided_leg_grades_void_and_the_card_re_grades(db_session):
    """Addendum §5.2: `leg_status:<seq> = void` is the owner recording that DraftKings voided
    the leg (the player did not play). `parlay_grade` treats it as a push on a non-correlated
    card, exactly as it treats a pushed line."""
    card, legs = _prop_card(db_session, stat="anytime_td", line=None, operator="yes")
    legs[0].status = "void"
    legs[0].graded_at = T0
    _final_game(db_session, card, home=24, away=21)
    grade_parlays(db_session, T0, _budget())
    assert db_session.get(ParlayCard, card.id).status in ("cashed", "void")
    # Fix round 1, review M1: "exactly as it treats a pushed line" means the stake comes back,
    # once, as this stage's own figure, in the card's week -- assert it rather than the
    # two-way status alone.
    rows = db_session.query(ParlayLedger).filter(
        ParlayLedger.card_id == card.id, ParlayLedger.kind != "stake").all()
    assert [(r.kind, r.source, r.amount, r.year, r.week) for r in rows] == \
        [("void", "computed", card.stake, card.year, card.week)]


def test_every_computed_ledger_row_says_so(db_session):
    """D18: `parlay_grade` writes provenance `computed`. CASHED, VOID and any sentence naming
    DraftKings appear only on a `confirmed` row, which only the correction route writes."""
    card, legs = _cashing_card(db_session)
    grade_parlays(db_session, T0, _budget())
    rows = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").all()
    assert [r.source for r in rows] == ["computed"]


def test_a_correlated_card_gets_no_computed_return(db_session):
    """D18/C5: an independence product the book never quoted is not a settlement figure. The
    owner's confirmed return is the only one on a correlated card."""
    card, legs = _cashing_card(db_session, correlated=True)
    grade_parlays(db_session, T0, _budget())
    assert db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").count() == 0
    assert db_session.get(ParlayCard, card.id).status == "cashed"


def test_every_ledger_row_carries_the_card_s_week_not_the_settlement_week(db_session):
    """D20: the cap and every row read the card's `(year, week)`, so a Saturday-built card
    settled on Monday stays in its slate's week."""
    card, legs = _cashing_card(db_session, year=2026, week=37)
    grade_parlays(db_session, T0_NEXT_WEEK, _budget())
    rows = db_session.query(ParlayLedger).filter_by(card_id=card.id).all()
    assert {(r.year, r.week) for r in rows} == {(2026, 37)}
    # Fix round 1, review M7: the stake row above was written by this file's own helper, so name
    # the discriminating row -- the `return` the grader wrote -- and the week it must not carry.
    settled = chicago_day(T0_NEXT_WEEK).isocalendar()
    ret = [r for r in rows if r.kind == "return"]
    assert [(r.year, r.week) for r in ret] == [(2026, 37)]
    assert (settled.year, settled.week) != (2026, 37)


# --- Fix round 1 (review I2, I3, I4, M5) ------------------------------------------------------


def test_a_correction_that_lowers_a_value_is_the_one_that_grades(db_session):
    """Review I2: `_NEWEST_STAT` exists for this. ESPN's summary carries no per-stat timestamp
    and sequential polls return the source's current state, so a later poll reporting a lower
    value is a correction (models.py, addendum 4.3), and it is the correction -- not the high
    water mark -- that settles the leg. 240 then 218 on an `over 225` leg is a miss."""
    card, legs = _prop_card(db_session, stat="pass_yds", line=Decimal("225"), operator="over")
    _final_game(db_session, card, home=24, away=21)
    _stat_row(db_session, legs[0], value=Decimal("240"), ts=T0 - timedelta(hours=1))
    _stat_row(db_session, legs[0], value=Decimal("218"), ts=T0, correction=True)
    counts = grade_parlays(db_session, T0, _budget()).counts
    assert legs[0].status == "miss" and counts["stat_missing"] == 0
    assert db_session.get(ParlayCard, card.id).status == "busted"


def test_a_recorded_zero_touchdowns_at_final_is_a_miss_not_a_missing_stat(db_session):
    """Review I3 (controller ruling): the collector writes a value-0 row for every listed player
    of a final game who scored no touchdown, so zero and absence are different facts. A recorded
    zero at final grades the `anytime_td` leg a miss; only an absent row is `stat_missing`."""
    card, legs = _prop_card(db_session, stat="anytime_td", line=None, operator="yes")
    _final_game(db_session, card, home=24, away=21)
    _stat_row(db_session, legs[0], value=Decimal("0"), ts=T0)
    counts = grade_parlays(db_session, T0, _budget()).counts
    assert legs[0].status == "miss" and counts["stat_missing"] == 0
    assert db_session.get(ParlayCard, card.id).status == "busted"


def test_a_period_prop_is_counted_an_error_and_never_graded(db_session):
    """Review I4 (controller ruling): release one grades the full game only (D3). A `1h` leg
    graded off the game-long value would be a wrong money grade with no error at all, so it
    raises: the card is isolated, left for the next pass, and counted in `errors`."""
    card, legs = _prop_card(db_session, stat="pass_yds", line=Decimal("120"), operator="over",
                            period="1h")
    _final_game(db_session, card, home=24, away=21)
    _stat_row(db_session, legs[0], value=Decimal("240"), ts=T0)
    # Committed, not merely flushed: `grade_parlays` rolls the failing card's savepoint back
    # (and the session with it), so an uncommitted fixture would vanish with the error it is
    # about -- the same reason `placed_card_malformed_beside_a_good_one` commits.
    db_session.commit()
    counts = grade_parlays(db_session, T0, _budget()).counts
    assert counts["errors"] == 1 and counts["stat_missing"] == 0
    assert db_session.get(ParlayLeg, legs[0].id).status == "pending"
    assert db_session.get(ParlayCard, card.id).status == "placed"


def test_a_prop_leg_with_no_player_is_an_error_not_a_quiet_feed(db_session):
    """Review M5: a leg the harness can never grade must not hide in `stat_missing`, which the
    operator reads as "the feed has gone quiet"."""
    card, legs = _prop_card(db_session, stat="rush_yds", line=Decimal("50"), operator="atleast",
                            player_id=None)
    _final_game(db_session, card, home=24, away=21)
    db_session.commit()                  # see the period test above
    counts = grade_parlays(db_session, T0, _budget()).counts
    assert counts["errors"] == 1 and counts["stat_missing"] == 0
    assert db_session.get(ParlayCard, card.id).status == "placed"
