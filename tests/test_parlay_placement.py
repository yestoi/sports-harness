"""Placement: the weekly cap, the moved-line refusal, and expiry.

**Each fixture seeds** one `teams` row per side, one `games` row inside this ISO week, one
`venue_markets` row per leg, one `parlay_cards` row and its `parlay_legs`, and one
`odds_snapshots` row per leg with `book = 'draftkings'`. What distinguishes them:
`proposed_card` a `proposed` card whose stored `threshold` equals the newest DraftKings `point`;
`proposed_cards_over_budget` two proposed cards and nothing in `parlay_ledger`;
`last_week_stake` one `parlay_ledger` `stake` row dated in the previous ISO week;
`card_with_moved_line` a proposed card whose newest DraftKings row carries a different `point`
for one leg, returning `(card, moved_seq)`; `placed_card` a card whose `status` is already
`placed`; `old_proposed_card` a proposed card `built_at` eight days ago; `old_placed_card` the
same age but `placed`.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from harness.db.models import (OddsPropSnapshot, ParlayCard, ParlayLedger, ParlayLeg,
                               ParlayPlacement)
from harness.parlay.placement import (BudgetExceeded, CardNotPlaceable, ConfirmationReused,
                                      CorrectionNotAllowed, CorrectionsCapped, LineMoved,
                                      apply_correction, expire_cards, mark_placed, show_cards,
                                      week_staked)
from tests.conftest import _make_parlay_card, _one_leg_game

NOW = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)
#: The same instant under the name the §5 cases use; one clock for the whole file.
T0 = NOW
#: Monday 2026-09-14, ISO week 38: the day a card built on Saturday of week 37 is placed. D20
#: says the card keeps its slate's week, so nothing this instant touches is keyed to week 38.
T_MONDAY = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)

_cards = count(1)


@pytest.fixture
def second_db_session(db_session):
    """A second session on the same engine, committing on its own.

    `db_session` is one transaction that `conftest` rolls back and truncates behind; the week
    advisory lock is transaction-scoped, so a race against it needs a genuinely second
    transaction. Cleanup is `db_session`'s truncate, and this fixture depends on `db_session`
    for that ordering alone: pytest tears a fixture down before the fixtures it requested, so
    this session is always closed before the truncate runs, whichever order a test lists them
    in (fix round 1, MI-5; `truncate_all` would otherwise block on an open transaction).
    """
    _schema = db_session.get_bind()
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=_schema)() as session:
        yield session
        session.rollback()


def _card(db_session, *, year: int = 2026, week: int = 37, status: str = "proposed",
          leg1_graded: bool = False) -> ParlayCard:
    """One card with a single spread leg and a fresh, unmoved DraftKings price.

    Built from `conftest`'s placement helpers rather than a second card shape, then re-keyed to
    the `(year, week)` under test: the card's own week is what every ledger row of the card
    carries (D20), and it is no longer read off the placement instant.
    """
    team, game = _one_leg_game(db_session, f"K{next(_cards)}")
    card = _make_parlay_card(db_session, status=status, built_at=NOW,
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-3.5"))])
    card.year, card.week = year, week
    if leg1_graded:
        leg = db_session.query(ParlayLeg).filter_by(card_id=card.id, seq=1).one()
        leg.status, leg.graded_at = "hit", NOW
    db_session.flush()
    return card


def _placed_card(db_session, *, status: str = "placed", year: int = 2026, week: int = 37,
                 stake: Decimal = Decimal("25"), odds: int = 400) -> ParlayCard:
    """A card that went through `mark_placed` and was then driven to `status`.

    Every state after `proposed` has a `parlay_placements` row behind it in production --
    `mark_placed` writes the placement and the stake row in one transaction -- and 9's
    invariant forbids a corrections row on a card that has none, so the correction cases build
    their cards this way rather than by writing a status straight onto a bare card.
    """
    card = _card(db_session, year=year, week=week)
    mark_placed(db_session, card.id, odds, stake, T0)
    card.status = status
    db_session.flush()
    return card


def _stake(db_session, *, week: int, amount: Decimal, year: int = 2026) -> ParlayCard:
    """A stake already recorded against `week`, on its own card, the way `mark_placed` records
    one: `source = 'confirmed'` and the card's own `(year, week)`."""
    card = _card(db_session, year=year, week=week, status="placed")
    db_session.add(ParlayLedger(ts=NOW, card_id=card.id, kind="stake", amount=amount,
                                year=year, week=week, source="confirmed"))
    db_session.flush()
    return card


def _prop_card(db_session, *, dk_point: Decimal, threshold: Decimal = Decimal("62.5"),
               fetched_at: datetime = datetime(2026, 9, 18, 20, 55, tzinfo=timezone.utc)):
    """A proposed card whose only leg is a prop (`market_type = 'prop'`, ruling T5/T6), priced
    from `odds_prop_snapshots`. `dk_point` is the line the newest DraftKings prop row carries;
    `threshold` is the line the card was built at. No `odds_snapshots` row exists for it, so a
    prop leg resolved through the game-line table would silently find nothing to compare."""
    team, game = _one_leg_game(db_session, f"P{next(_cards)}")
    card = _make_parlay_card(db_session, status="proposed", built_at=NOW,
                             legs=[(game.id, team.id, Decimal("-3.5"), Decimal("-3.5"))])
    player = 900_000 + next(_cards)          # any int: `parlay_legs.player_id` has no FK
    leg = db_session.query(ParlayLeg).filter_by(card_id=card.id, seq=1).one()
    leg.market_type, leg.stat, leg.side = "prop", "rec_yds", "over"
    leg.side_team_id, leg.player_id, leg.threshold = None, player, threshold
    leg.odds_snapshot_id = None
    db_session.add(OddsPropSnapshot(raw_id=next(_cards), book="draftkings", game_id=game.id,
                                    market_type="prop:rec_yds", player_name="A Receiver",
                                    player_id=player, outcome_side="over", point=dk_point,
                                    price_decimal=Decimal("1.9100"), fetched_at=fetched_at))
    db_session.flush()
    return card


def _drive_alive(db_session, card):
    """The state `parlay_grade` leaves a placed card in once one leg has graded: the card is
    `alive` and its legs are still pending. Written here rather than reached through
    `grade_parlays`, so a placement test never depends on the grader."""
    card.status = "alive"
    db_session.flush()


def test_placing_writes_a_placement_and_a_stake_row(db_session, proposed_card):
    placement = mark_placed(db_session, proposed_card.id, payout_american=1450,
                            stake=Decimal("25"), now=NOW)
    assert placement.stake_actual == Decimal("25.00") and placement.dk_odds_actual == 1450
    card = db_session.get(type(proposed_card), proposed_card.id)
    assert card.status == "placed"
    ledger = db_session.execute(text(
        "select kind, amount, year, week from parlay_ledger")).all()
    assert [(r.kind, r.amount) for r in ledger] == [("stake", Decimal("25.00"))]


def test_the_actual_payout_is_recorded_from_the_operator_s_own_slip(db_session, proposed_card):
    """Spec §8.1: record the actual DK payout on mark-placed and compute hold from it. The
    estimate was ours; this number is the book's."""
    placement = mark_placed(db_session, proposed_card.id, payout_american=1200,
                            stake=Decimal("25"), now=NOW)
    assert placement.dk_payout_actual is not None
    assert placement.dk_payout_actual != db_session.get(
        type(proposed_card), proposed_card.id).dk_payout_est


def test_the_weekly_cap_is_hard(db_session, proposed_cards_over_budget):
    """D15: $50 a week, checked against `parlay_ledger`. The $10 unallocated stays unspent, and
    a card that would cross the line is refused rather than trimmed."""
    first, second = proposed_cards_over_budget
    mark_placed(db_session, first.id, payout_american=1450, stake=Decimal("45"), now=NOW)
    with pytest.raises(BudgetExceeded):
        mark_placed(db_session, second.id, payout_american=900, stake=Decimal("10"), now=NOW)


def test_the_cap_is_per_iso_week(db_session, proposed_card, last_week_stake):
    """Last week's $50 does not bind this week's."""
    mark_placed(db_session, proposed_card.id, payout_american=1450, stake=Decimal("25"), now=NOW)
    assert week_staked(db_session, NOW.year, NOW.isocalendar().week) == Decimal("25.00")


#: Sunday 2026-09-13 20:00 in America/Chicago -- still ISO week 37 there -- is Monday 2026-09-14
#: 01:00 in UTC, already into ISO week 38. Fix round 1 stopped `mark_placed` keying the cap off a
#: raw `now.isocalendar()` on the UTC timestamp, which attributed a stake placed at this moment
#: (as late as 7 p.m. Central on a Sunday, for about five hours) to next week's budget. D20
#: carries that further: the key is not read off `now` in any zone, it is the card's own week.
CT_WEEK_BOUNDARY_UTC = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def test_the_cap_is_keyed_to_the_card_s_week_not_to_any_reading_of_now(
        db_session, proposed_cards_over_budget):
    """The cards are week 37's; the instant they are placed at is week 38 in UTC and week 37 in
    Chicago. Both stakes belong to week 37 because that is the week of the slate they were
    built for (D20), and no reading of `now` moves them."""
    first, second = proposed_cards_over_budget
    for card in (first, second):
        card.year, card.week = 2026, 37
    db_session.flush()
    mark_placed(db_session, first.id, payout_american=1450, stake=Decimal("45"),
                now=CT_WEEK_BOUNDARY_UTC)
    with pytest.raises(BudgetExceeded):
        mark_placed(db_session, second.id, payout_american=900, stake=Decimal("10"),
                    now=CT_WEEK_BOUNDARY_UTC)
    assert week_staked(db_session, 2026, 37) == Decimal("45.00")
    assert week_staked(db_session, 2026, 38) == Decimal("0")


def test_a_moved_line_is_refused_without_a_leg_line(db_session, card_with_moved_line):
    """Ruling A-M7: a moved line is a different bet. Confirming it silently would put a card in
    the ledger that is not the card that was placed."""
    with pytest.raises(LineMoved) as caught:
        mark_placed(db_session, card_with_moved_line.card.id, payout_american=1450,
                    stake=Decimal("25"), now=NOW)
    assert card_with_moved_line.moved_seq in caught.value.legs


def test_a_leg_line_accepts_the_move_and_updates_the_leg(db_session, card_with_moved_line):
    seq = card_with_moved_line.moved_seq
    mark_placed(db_session, card_with_moved_line.card.id, payout_american=1450,
                stake=Decimal("25"), now=NOW, leg_lines={seq: Decimal("-3.5")})
    point = db_session.execute(text(
        "select threshold from parlay_legs where card_id = :c and seq = :s"),
        {"c": card_with_moved_line.card.id, "s": seq}).scalar()
    assert point == Decimal("-3.5")


def test_a_leg_line_for_a_leg_that_did_not_move_is_still_accepted(db_session, proposed_card):
    """The operator's slip is the record. If they say they took -3.5, the card says -3.5."""
    mark_placed(db_session, proposed_card.id, payout_american=1450, stake=Decimal("25"),
                now=NOW, leg_lines={1: Decimal("-3.5")})


def test_a_card_that_is_not_proposed_cannot_be_placed(db_session, placed_card):
    with pytest.raises(CardNotPlaceable):
        mark_placed(db_session, placed_card.id, payout_american=1450, stake=Decimal("25"),
                    now=NOW)


def test_an_unknown_card_id_is_refused(db_session):
    with pytest.raises(CardNotPlaceable):
        mark_placed(db_session, 99_999, payout_american=100, stake=Decimal("5"), now=NOW)


def test_a_proposed_card_older_than_seven_days_becomes_void(db_session, old_proposed_card):
    """Ruling B-C4. `void`, not `expired`: the Ticket surface filters on
    `proposed|placed|alive|cashed|busted|void` in five places and a status outside that
    vocabulary is invisible on the surface built to render it."""
    assert expire_cards(db_session, NOW) == 1
    card = db_session.get(type(old_proposed_card), old_proposed_card.id)
    assert card.status == "void"


def test_expiry_never_touches_a_placed_card(db_session, old_placed_card):
    assert expire_cards(db_session, NOW) == 0


def test_expiry_writes_no_ledger_row(db_session, old_proposed_card):
    """A card nobody placed cost nothing, so it moves no money."""
    expire_cards(db_session, NOW)
    assert db_session.execute(text("select count(*) from parlay_ledger")).scalar() == 0


def test_show_lists_the_week_and_the_remaining_budget(db_session, proposed_card):
    rows = show_cards(db_session, NOW)
    assert rows and rows[0]["card_id"] == proposed_card.id
    assert rows[0]["status"] == "proposed"
    assert rows[0]["week_remaining"] == Decimal("50.00")


# --- Task 9: the card's week, confirmation ids, the week lock and the correction table ---------
#
# Addendum 5.1-5.4 and 0 (D20). Every case below uses `_card`, whose (year, week) is the card's
# own, and `T0`/`T_MONDAY`, which are four days apart and in different ISO weeks.


def test_the_stake_row_carries_the_card_s_week_not_the_placement_week(db_session):
    """D20: a Saturday-built card placed on Monday stays in its slate's week, and the cap reads
    that key. Computed independently: the card fixture is week 37; `T_MONDAY` is in week 38."""
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, -110, Decimal("25"), T_MONDAY)
    row = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").one()
    assert (row.year, row.week) == (2026, 37) and row.source == "confirmed"


def test_two_submits_with_one_confirmation_id_record_one_placement(db_session):
    card = _card(db_session)
    cid = "0f9d6a2c-2b2b-4a1e-9a7a-9d5f7a1c3e11"
    first = mark_placed(db_session, card.id, -110, Decimal("25"), T0, confirmation_id=cid)
    db_session.commit()
    with pytest.raises(CardNotPlaceable):
        mark_placed(db_session, card.id, -110, Decimal("25"), T0, confirmation_id=cid)
    assert db_session.query(ParlayPlacement).filter_by(card_id=card.id).count() == 1
    assert db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").count() == 1
    assert first.confirmation_id == cid


def test_a_confirmation_id_used_on_another_card_is_refused(db_session):
    a, b = _card(db_session), _card(db_session)
    cid = "0f9d6a2c-2b2b-4a1e-9a7a-9d5f7a1c3e11"
    mark_placed(db_session, a.id, -110, Decimal("25"), T0, confirmation_id=cid)
    db_session.commit()
    with pytest.raises(ConfirmationReused):
        mark_placed(db_session, b.id, -110, Decimal("5"), T0, confirmation_id=cid)


def test_a_reused_confirmation_id_is_refused_before_any_write(db_session):
    """The refusal is on the code path, not on the unique index: nothing at all is written for
    the second card, so there is no placement to roll back and no stake row to reconcile."""
    a, b = _card(db_session), _card(db_session)
    cid = "b1c4f6d8-1111-4a1e-9a7a-9d5f7a1c3e22"
    mark_placed(db_session, a.id, -110, Decimal("25"), T0, confirmation_id=cid)
    db_session.commit()
    with pytest.raises(ConfirmationReused):
        mark_placed(db_session, b.id, -110, Decimal("5"), T0, confirmation_id=cid)
    assert db_session.query(ParlayPlacement).filter_by(card_id=b.id).count() == 0
    assert db_session.query(ParlayLedger).filter_by(card_id=b.id).count() == 0
    assert db_session.get(ParlayCard, b.id).status == "proposed"


def test_two_different_cards_at_the_cap_record_one_and_refuse_one(db_session, second_db_session):
    """5.3: the week lock is what serializes the cap across *different* cards. Two sessions,
    $25 already staked, both submitting $25 against a $50 week: one records, one is refused.

    Deviation from the brief's draft, recorded rather than silent: the first session commits
    before the second submits. The advisory lock is transaction-scoped, so an uncommitted first
    transaction would make the second block on the lock until its own timeout rather than reach
    the cap at all. The lock's mutual exclusion is proved directly in the test below."""
    _stake(db_session, week=37, amount=Decimal("25"))
    a, b = _card(db_session, week=37), _card(db_session, week=37)
    db_session.commit()
    mark_placed(db_session, a.id, -110, Decimal("25"), T0)
    db_session.commit()
    with pytest.raises(BudgetExceeded) as caught:
        mark_placed(second_db_session, b.id, -110, Decimal("25"), T0)
    assert caught.value.recorded == Decimal("50.00") and caught.value.left == Decimal("0.00")
    assert week_staked(db_session, 2026, 37) == Decimal("50")
    assert db_session.query(ParlayPlacement).filter_by(card_id=b.id).count() == 0


def test_the_week_lock_excludes_a_second_transaction_on_the_same_week(db_session,
                                                                      second_db_session):
    """The lock itself, not only its consequence. While one transaction is inside `mark_placed`
    on week 37, a second transaction cannot enter that week at all: it waits, and here it is
    given a 250 ms `lock_timeout` (with a 2 s statement timeout behind it) so a regression that
    dropped the lock fails as a passing `mark_placed` rather than hanging the suite."""
    a, b = _card(db_session, week=37), _card(db_session, week=37)
    db_session.commit()
    mark_placed(db_session, a.id, -110, Decimal("5"), T0)          # holds the week lock
    second_db_session.execute(text("set local lock_timeout = '250ms'"))
    second_db_session.execute(text("set local statement_timeout = '2s'"))
    with pytest.raises(DBAPIError):
        mark_placed(second_db_session, b.id, -110, Decimal("5"), T0)
    second_db_session.rollback()
    db_session.commit()


def test_a_different_week_is_not_blocked_by_the_lock(db_session, second_db_session):
    """The lock is per week, not global: week 38's card records while week 37 is held."""
    a = _card(db_session, week=37)
    b = _card(db_session, week=38)
    db_session.commit()
    mark_placed(db_session, a.id, -110, Decimal("5"), T0)          # holds week 37
    second_db_session.execute(text("set local lock_timeout = '2s'"))
    mark_placed(second_db_session, b.id, -110, Decimal("5"), T0)
    second_db_session.commit()
    db_session.commit()
    assert week_staked(db_session, 2026, 38) == Decimal("5.00")


def test_a_prop_leg_s_moved_line_is_read_from_the_prop_table(db_session):
    """A prop leg is `market_type = 'prop'` and its price lives in `odds_prop_snapshots`; the
    game-line table holds no row for it, so a prop resolved through `odds_snapshots` would find
    nothing and confirm a line that had moved."""
    card = _prop_card(db_session, dk_point=Decimal("70.5"))
    with pytest.raises(LineMoved) as caught:
        mark_placed(db_session, card.id, 450, Decimal("25"), T0)
    assert caught.value.legs[1] == (Decimal("62.5"), Decimal("70.5"))


def test_a_prop_leg_whose_line_held_is_placed(db_session):
    card = _prop_card(db_session, dk_point=Decimal("62.5"))
    placement = mark_placed(db_session, card.id, 450, Decimal("25"), T0)
    assert placement.stake_actual == Decimal("25.00")


def test_a_note_is_sanitized_onto_the_placement(db_session):
    card = _card(db_session)
    placement = mark_placed(db_session, card.id, -110, Decimal("25"), T0,
                            note="typed it <script> off the slip")
    assert "<" not in placement.note and "off the slip" in placement.note


@pytest.mark.parametrize("state,field,value", [
    ("proposed", "status", "declined"),
    ("placed", "stake", "30.00"),
    ("alive", "accepted_odds", "450"),
    ("placed", "leg_line:1", "227.5"),
    ("busted", "leg_status:1", "void"),
    ("cashed", "return", "137.50"),
    ("busted", "status", "void"),
])
def test_every_row_of_the_transition_table_is_accepted(db_session, state, field, value):
    card = (_card(db_session, status="proposed") if state == "proposed"
            else _placed_card(db_session, status=state))
    row = apply_correction(db_session, card.id, field, value, T0)
    # The brief's blanket `row.new_value == value` is amended for the decline row only (fix
    # round 1, IM-1): 5.2's Effect column records that row as `(status, proposed, void)`, so
    # the persisted value is the card's new state, not the word the route body carried.
    recorded = "void" if (field, value) == ("status", "declined") else value
    assert row.field == field and row.new_value == recorded


@pytest.mark.parametrize("state,field,value", [
    ("cashed", "stake", "30.00"),            # graded: stake is frozen
    ("busted", "accepted_odds", "450"),
    ("placed", "status", "cashed"),          # never writable
    ("placed", "status", "busted"),
    ("placed", "status", "alive"),
    ("proposed", "return", "10.00"),
    ("placed", "leg_line:1", "227.5"),       # with leg 1 already graded
])
def test_everything_outside_the_table_is_refused(db_session, state, field, value):
    card = _card(db_session, status=state, leg1_graded=(field == "leg_line:1"))
    with pytest.raises((CorrectionNotAllowed,)):
        apply_correction(db_session, card.id, field, value, T0)


def test_a_refused_correction_writes_no_row_at_all(db_session):
    card = _card(db_session, status="placed")
    with pytest.raises(CorrectionNotAllowed):
        apply_correction(db_session, card.id, "status", "cashed", T0)
    assert db_session.execute(text(
        "select count(*) from parlay_placement_corrections")).scalar() == 0
    assert db_session.get(ParlayCard, card.id).status == "placed"


def test_a_stake_correction_writes_the_signed_delta_on_the_card_s_week(db_session):
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, -110, Decimal("25"), T0)
    apply_correction(db_session, card.id, "stake", "30.00", T_MONDAY)
    rows = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").all()
    assert sorted(r.amount for r in rows) == [Decimal("5.00"), Decimal("25.00")]
    assert {(r.year, r.week) for r in rows} == {(2026, 37)}
    assert db_session.get(ParlayPlacement, card.id).stake_actual == Decimal("30.00")


def test_a_stake_correction_down_writes_a_negative_delta(db_session):
    """The ledger's stake rows sum to what is actually staked (9's per-card invariant), so a
    correction downwards is a negative row, never a deleted one."""
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, -110, Decimal("25"), T0)
    apply_correction(db_session, card.id, "stake", "20.00", T0)
    rows = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").all()
    assert sum(r.amount for r in rows) == Decimal("20.00")
    assert db_session.get(ParlayPlacement, card.id).stake_actual == Decimal("20.00")


def test_a_stake_correction_over_the_cap_is_refused(db_session):
    """5.2: the cap is re-checked on an increase, and it is the card's week that is read."""
    _stake(db_session, week=37, amount=Decimal("20"))
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, -110, Decimal("25"), T0)
    with pytest.raises(BudgetExceeded):
        apply_correction(db_session, card.id, "stake", "31.00", T0)
    assert db_session.get(ParlayPlacement, card.id).stake_actual == Decimal("25.00")


def test_an_accepted_odds_correction_recomputes_the_payout(db_session):
    card = _card(db_session)
    mark_placed(db_session, card.id, 400, Decimal("25"), T0)
    apply_correction(db_session, card.id, "accepted_odds", "450", T0)
    placement = db_session.get(ParlayPlacement, card.id)
    assert placement.dk_odds_actual == 450
    assert placement.dk_payout_actual == Decimal("137.50")


def test_a_leg_status_correction_voids_the_leg_for_the_next_grading_pass(db_session):
    card = _placed_card(db_session, status="alive")
    apply_correction(db_session, card.id, "leg_status:1", "void", T0)
    leg = db_session.query(ParlayLeg).filter_by(card_id=card.id, seq=1).one()
    assert leg.status == "void" and leg.graded_at == T0


def test_a_confirmed_return_and_a_confirmed_void_are_the_only_confirmed_rows(db_session):
    # `mark_placed` refuses anything but a `proposed` card, so the card is placed first and
    # then driven `alive` the way `parlay_grade` drives it (plan review IM-5).
    card = _card(db_session)
    mark_placed(db_session, card.id, 450, Decimal("25"), T0)
    _drive_alive(db_session, card)
    apply_correction(db_session, card.id, "return", "137.50", T0)
    row = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").one()
    assert row.source == "confirmed" and row.amount == Decimal("137.50")
    assert db_session.get(ParlayCard, card.id).status == "cashed"


def test_a_second_confirmed_return_replaces_the_computed_row_rather_than_adding_beside_it(
        db_session):
    """One return row per card, whoever wrote it: `parlay_ledger` has no unique key, so this is
    the code's invariant to keep. The grader's computed row is what the owner is correcting,
    and a confirmed figure replaces it; a second confirmation replaces that."""
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, 450, Decimal("25"), T0)
    _drive_alive(db_session, card)
    db_session.add(ParlayLedger(ts=T0, card_id=card.id, kind="return", amount=Decimal("137.50"),
                                year=2026, week=37, source="computed"))
    db_session.flush()
    apply_correction(db_session, card.id, "return", "130.00", T0)
    apply_correction(db_session, card.id, "return", "125.00", T0)
    row = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").one()
    assert row.source == "confirmed" and row.amount == Decimal("125.00")
    assert db_session.query(ParlayLedger).filter_by(card_id=card.id).count() == 2   # + the stake


def test_a_confirmed_void_returns_the_stake_once(db_session):
    """DraftKings voided the whole slip. The grader may already have written a computed `void`
    row for it; the owner's confirmation replaces that row, and there is exactly one."""
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, 450, Decimal("25"), T0)
    _drive_alive(db_session, card)
    db_session.add(ParlayLedger(ts=T0, card_id=card.id, kind="void", amount=Decimal("25.00"),
                                year=2026, week=37, source="computed"))
    db_session.flush()
    apply_correction(db_session, card.id, "status", "void", T0)
    row = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="void").one()
    assert row.source == "confirmed" and row.amount == Decimal("25.00")
    assert (row.year, row.week) == (2026, 37)
    assert db_session.get(ParlayCard, card.id).status == "void"


def test_declining_a_proposed_card_voids_it_and_moves_no_money(db_session):
    """5.4: `Not this one` is the first row of the table, not a third route."""
    card = _card(db_session, status="proposed")
    row = apply_correction(db_session, card.id, "status", "declined", T0)
    card = db_session.get(ParlayCard, card.id)
    assert (card.status, card.declined_reason) == ("void", "declined")
    # 5.2's Effect column: the row is `(status, proposed, void)`. The owner's own word is on
    # the card, in `declined_reason`, which is what the Season chip reads.
    assert (row.field, row.old_value, row.new_value) == ("status", "proposed", "void")
    assert db_session.query(ParlayLedger).filter_by(card_id=card.id).count() == 0


def test_a_correction_records_the_value_it_replaced(db_session):
    card = _card(db_session)
    mark_placed(db_session, card.id, 400, Decimal("25"), T0)
    row = apply_correction(db_session, card.id, "accepted_odds", "450", T0, note="read it again")
    assert row.old_value == "400" and row.new_value == "450" and row.note == "read it again"
    assert row.ts == T0


def test_the_same_field_corrected_twice_writes_two_rows(db_session):
    """5.3: corrections carry no confirmation id; the trail is append-only."""
    card = _card(db_session)
    mark_placed(db_session, card.id, 400, Decimal("25"), T0)
    apply_correction(db_session, card.id, "accepted_odds", "450", T0)
    apply_correction(db_session, card.id, "accepted_odds", "450", T0)
    assert db_session.execute(text(
        "select count(*) from parlay_placement_corrections where card_id = :c"),
        {"c": card.id}).scalar() == 2


def test_a_correction_to_an_unknown_card_is_refused(db_session):
    with pytest.raises(CardNotPlaceable):
        apply_correction(db_session, 99_999, "status", "void", T0)


def test_the_corrections_cap_is_twenty_per_card(db_session):
    card = _card(db_session)
    mark_placed(db_session, card.id, 450, Decimal("25"), T0)
    _drive_alive(db_session, card)
    for i in range(20):
        apply_correction(db_session, card.id, "accepted_odds", str(400 + i), T0)
    with pytest.raises(CorrectionsCapped):
        apply_correction(db_session, card.id, "accepted_odds", "999", T0)


def test_a_value_longer_than_thirty_two_characters_is_refused_never_truncated(db_session):
    card = _card(db_session, status="alive")
    with pytest.raises(ValueError):
        apply_correction(db_session, card.id, "accepted_odds", "9" * 33, T0)


def test_a_field_carrying_a_suffix_the_table_does_not_define_is_refused(db_session):
    """`field` is the 5.2 name as written; only a leg field carries a `:<seq>`."""
    card = _card(db_session, status="placed")
    with pytest.raises(CorrectionNotAllowed):
        apply_correction(db_session, card.id, "status:void", "void", T0)


def test_a_field_longer_than_the_column_is_refused_never_truncated(db_session):
    card = _card(db_session, status="placed")
    with pytest.raises(ValueError):
        apply_correction(db_session, card.id, "leg_line:" + "1" * 30, "227.5", T0)


# --- Fix round 1 -------------------------------------------------------------------------------


def test_a_half_cent_rounds_up_the_way_the_column_does(db_session):
    """IM-2: money is quantized `ROUND_HALF_UP` here, as `numeric(10,2)` rounds and as the rest
    of the repo does. `decimal`'s default half-even would store the stake as $25.00 and the
    return as $137.50, each a cent under what PostgreSQL's own cast of the same string gives."""
    card = _card(db_session, year=2026, week=37)
    placement = mark_placed(db_session, card.id, 450, Decimal("25.005"), T0)
    assert placement.stake_actual == Decimal("25.01")
    row = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="stake").one()
    assert row.amount == Decimal("25.01")
    _drive_alive(db_session, card)
    apply_correction(db_session, card.id, "return", "137.505", T0)
    paid = db_session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").one()
    assert paid.amount == Decimal("137.51")


def test_a_stake_correction_of_a_half_cent_rounds_up(db_session):
    card = _card(db_session, year=2026, week=37)
    mark_placed(db_session, card.id, 400, Decimal("25"), T0)
    apply_correction(db_session, card.id, "stake", "30.005", T0)
    assert db_session.get(ParlayPlacement, card.id).stake_actual == Decimal("30.01")
    assert sum(r.amount for r in db_session.query(ParlayLedger).filter_by(
        card_id=card.id, kind="stake")) == Decimal("30.01")


@pytest.mark.parametrize("field,value", [
    ("stake", "30.00"),
    ("accepted_odds", "450"),
    ("leg_status:1", "void"),
    ("return", "137.50"),
])
def test_a_correction_on_a_card_with_no_placement_is_refused(db_session, field, value):
    """MI-2 and 9's invariant: no corrections row whose card has no placement and whose field
    is not `status`. `mark_placed` writes both rows in one transaction, so the only way to this
    state is hand-made data, and a refusal keeps the invariant true by construction."""
    card = _card(db_session, status="placed")          # no `parlay_placements` row behind it
    with pytest.raises(CorrectionNotAllowed):
        apply_correction(db_session, card.id, field, value, T0)
    assert db_session.execute(text(
        "select count(*) from parlay_placement_corrections")).scalar() == 0


def test_a_status_correction_needs_no_placement(db_session):
    """The one field the invariant exempts: a `proposed` card has no placement by definition,
    and declining it is the first row of the table."""
    card = _card(db_session, status="proposed")
    apply_correction(db_session, card.id, "status", "declined", T0)
    assert db_session.get(ParlayCard, card.id).status == "void"


def test_a_confirmed_return_leaves_every_other_card_s_ledger_alone(db_session):
    """MI-4: the delete behind the one-row-per-kind rule is scoped to one card and one kind.
    Card B is settled and confirmed; correcting card A must not touch a row of B's."""
    a = _card(db_session, year=2026, week=37)
    b = _card(db_session, year=2026, week=37)
    mark_placed(db_session, a.id, 450, Decimal("5"), T0)
    mark_placed(db_session, b.id, 450, Decimal("5"), T0)
    _drive_alive(db_session, a)
    _drive_alive(db_session, b)
    apply_correction(db_session, b.id, "return", "27.50", T0)
    apply_correction(db_session, a.id, "return", "27.50", T0)
    apply_correction(db_session, a.id, "return", "30.00", T0)
    kept = db_session.query(ParlayLedger).filter_by(card_id=b.id, kind="return").one()
    assert kept.amount == Decimal("27.50") and kept.source == "confirmed"
    assert db_session.query(ParlayLedger).filter_by(card_id=b.id, kind="stake").count() == 1
    assert db_session.query(ParlayLedger).filter_by(card_id=a.id, kind="return").one().amount \
        == Decimal("30.00")
