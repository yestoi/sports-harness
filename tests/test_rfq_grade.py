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


def test_grading_twice_is_idempotent(db_session, settled_quote):
    grade_rfq_quotes(db_session, NOW, _budget())
    first = db_session.execute(text("select graded_at from rfq_quotes")).scalar()
    grade_rfq_quotes(db_session, NOW, _budget())
    assert db_session.execute(text("select graded_at from rfq_quotes")).scalar() == first


def test_a_spent_budget_yields(db_session, settled_quote):
    result = grade_rfq_quotes(db_session, NOW, Budget(0.0, lambda: 1.0))
    assert result.budget_exhausted is True
    assert MIN_BUDGET_S == 30
