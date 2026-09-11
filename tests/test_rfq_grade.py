"""Grading a stored quote against the product of closing leg fair values (ruling B-I6).

**Each fixture seeds** one `rfqs` row with two legs, one `rfq_quotes` row for it, and the
`venue_markets`, `games` and `fair_values` rows the legs resolve through. What distinguishes
them: `settled_quote` both games `final` with closing fairs 0.70 and 0.50, written after
kickoff; `stale_closing_quote` the same, but one leg's newest `fair_values` row predates its
kickoff by more than `CLOSING_WINDOW`; `declined_quote` a quote whose `declined_reason` is set
and whose bids are null; `unsettled_quote` one game still `in_progress`.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.settlement.job import STAGE_MODULES, Budget
from harness.settlement.rfq_grade import MIN_BUDGET_S, grade_rfq_quotes

NOW = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)


def _budget(seconds=600.0):
    return Budget(seconds, lambda: 0.0)


def test_rfq_grade_runs_immediately_after_parlay_grade():
    assert STAGE_MODULES.index("harness.settlement.rfq_grade") == \
        STAGE_MODULES.index("harness.settlement.parlay_grade") + 1


def test_a_settled_quote_is_graded_against_the_closing_product(db_session, settled_quote):
    grade_rfq_quotes(db_session, NOW, _budget())
    row = db_session.execute(text(
        "select graded_at, closing_fair, closing_stale, pnl_yes, pnl_no from rfq_quotes")).first()
    assert row.graded_at is not None
    assert row.closing_fair == Decimal("0.3500")     # 0.70 * 0.50
    assert row.closing_stale is False
    assert row.pnl_yes is not None and row.pnl_no is not None


def test_the_scored_side_is_the_creator_taking_our_bid(db_session, settled_quote):
    """Ruling B-I6: the counterfactual is the RFQ creator taking our bid on the side the RFQ
    asked for. `pnl_yes` is what we would have made buying YES at `yes_bid` and settling at the
    closing fair; `pnl_no` the mirror."""
    grade_rfq_quotes(db_session, NOW, _budget())
    row = db_session.execute(text(
        "select yes_bid, no_bid, closing_fair, pnl_yes, pnl_no from rfq_quotes")).first()
    assert row.pnl_yes == (row.closing_fair - row.yes_bid)
    # The mirror, asserted rather than assumed: NO settles at `1 - closing_fair`, so a formula
    # that dropped the complement (or the sign) would pass on `pnl_yes` alone.
    assert row.pnl_no == (Decimal("1") - row.closing_fair) - row.no_bid


def test_a_stale_closing_leg_is_flagged_graded_and_carries_no_invented_number(
        db_session, stale_closing_quote):
    """Ruling B-I6 asks for separate reporting, not for a substitute value. A fabricated
    neutral 0.5 would land in `closing_fair` and then in a P&L t10 averages."""
    grade_rfq_quotes(db_session, NOW, _budget())
    row = db_session.execute(text(
        "select closing_stale, graded_at, closing_fair, pnl_yes, pnl_no from rfq_quotes")).first()
    assert row.closing_stale is True and row.graded_at is not None
    assert row.closing_fair is None and row.pnl_yes is None and row.pnl_no is None


def test_a_declined_quote_is_never_graded(db_session, declined_quote):
    grade_rfq_quotes(db_session, NOW, _budget())
    assert db_session.execute(text("select graded_at from rfq_quotes")).scalar() is None


def test_an_ungraded_leg_leaves_the_quote_alone(db_session, unsettled_quote):
    grade_rfq_quotes(db_session, NOW, _budget())
    assert db_session.execute(text("select graded_at from rfq_quotes")).scalar() is None


def test_a_postponed_leg_voids_the_quote_rather_than_leaving_it_waiting(
        db_session, postponed_leg_quote):
    """Review I4: a postponed/canceled leg means this combo can never settle. Voided -- graded
    once and flagged -- rather than left in the ungraded queue for the rest of the season."""
    result = grade_rfq_quotes(db_session, NOW, _budget())
    assert result.counts["voided"] == 1
    row = db_session.execute(text(
        "select graded_at, voided, closing_fair, pnl_yes, pnl_no from rfq_quotes")).first()
    assert row.graded_at is not None and row.voided is True
    assert row.closing_fair is None and row.pnl_yes is None and row.pnl_no is None


def test_a_final_game_with_no_score_stays_ungraded(db_session, final_no_score_quote):
    """Review I4: "never guess a result" holds here the way it holds
    `harness.settlement.parlay_grade`'s `_score_state` -- a `final` game with no recorded score
    is not settled yet, the same as an `in_progress` or `scheduled` one."""
    grade_rfq_quotes(db_session, NOW, _budget())
    assert db_session.execute(text("select graded_at from rfq_quotes")).scalar() is None


def test_a_malformed_quote_is_isolated_and_the_good_quote_still_grades(
        db_session, malformed_quote_beside_a_good_one):
    """Review I1: a quote whose stored `legs` holds something `grade_rfq_quotes` cannot read is
    isolated in its own savepoint, counted as an error, and left untouched for the next pass;
    the other quote in the same pass still grades."""
    result = grade_rfq_quotes(db_session, NOW, _budget())
    assert result.counts["errors"] == 1
    fixtures = malformed_quote_beside_a_good_one
    bad = db_session.execute(text(
        "select graded_at from rfq_quotes where id = :id"), {"id": fixtures.bad.id}).scalar()
    good = db_session.execute(text(
        "select graded_at from rfq_quotes where id = :id"), {"id": fixtures.good.id}).scalar()
    assert bad is None            # untouched, left for the next pass
    assert good is not None       # graded despite the other quote's failure


def test_an_empty_legs_list_is_left_waiting_not_graded_with_a_fabricated_certainty(
        db_session, empty_legs_quote):
    """Review M5: an `rfqs` row with no legs has no product to grade. Left waiting -- not graded
    with `closing_fair = 1.0000` (the empty product) and a P&L built off it."""
    result = grade_rfq_quotes(db_session, NOW, _budget())
    assert result.counts["waiting"] == 1
    row = db_session.execute(text(
        "select graded_at, closing_fair from rfq_quotes where id = :id"),
        {"id": empty_legs_quote.quote.id}).first()
    assert row.graded_at is None and row.closing_fair is None


def test_grading_twice_is_idempotent(db_session, settled_quote):
    grade_rfq_quotes(db_session, NOW, _budget())
    first = db_session.execute(text("select graded_at from rfq_quotes")).scalar()
    grade_rfq_quotes(db_session, NOW, _budget())
    assert db_session.execute(text("select graded_at from rfq_quotes")).scalar() == first


def test_a_spent_budget_yields(db_session, settled_quote):
    result = grade_rfq_quotes(db_session, NOW, Budget(0.0, lambda: 1.0))
    assert result.budget_exhausted is True
    assert MIN_BUDGET_S == 30
