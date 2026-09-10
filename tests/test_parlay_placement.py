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

import pytest
from sqlalchemy import text

from harness.parlay.placement import (BudgetExceeded, CardNotPlaceable, LineMoved, expire_cards,
                                      mark_placed, show_cards, week_staked)

NOW = datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc)


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
#: 01:00 in UTC, already into ISO week 38. Fix round 1: `mark_placed` used to key the cap off a
#: raw `now.isocalendar()` on the UTC timestamp, which would attribute a stake placed at this
#: moment (as late as 7 p.m. Central on a Sunday, for about five hours) to next week's budget
#: instead of the week the operator was actually in.
CT_WEEK_BOUNDARY_UTC = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def test_the_cap_is_keyed_to_the_chicago_week_not_a_raw_utc_isocalendar(
        db_session, proposed_cards_over_budget):
    """A raw `isocalendar()` on a UTC `now` would put this Sunday-night stake in next week's
    cap, five hours early. It has to land in the Chicago week the operator was actually
    placing in (2026-W37), not the UTC calendar's week (2026-W38)."""
    first, second = proposed_cards_over_budget
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
