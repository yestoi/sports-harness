"""Grading stored RFQ quotes at settlement (spec §8.2, ruling B-I6, H5).

**A declared counterfactual, not a P&L.** Nothing was ever quoted and nothing was ever filled, so
this is what our bid would have earned if the RFQ's creator had taken it and nothing about our
being there had changed the price. It is an upper bound twice over -- no fill risk, no adverse
selection -- and t10's header says so in the report rather than only here.

**The scored side is the one the RFQ asked for**: `pnl_yes` is `closing_fair - yes_bid`, what
buying YES at our bid and settling at the closing product would have made. `pnl_no` is the
mirror.

**`closing_stale`** marks a quote any of whose legs had no current closing fair value. Those
quotes are **graded and reported separately, with no number invented for them**: `closing_fair`,
`pnl_yes` and `pnl_no` stay null and only `graded_at` and the flag are written. Ruling B-I6 asks
for separate reporting, not for a substitute value, and a fabricated neutral 0.5 would land in a
column t10 averages and read as a measurement of a market. Dropping them instead would make the
panel look better than the week was, which is the other half of the same rule.

**`voided`** marks a quote any of whose legs' games were `postponed` or `canceled` (ruling I4):
the combo can never settle, so it is graded once, flagged, and never re-read, rather than sitting
in the ungraded queue for the rest of the season with no number and no signal it never will have
one. A `final`/`final_ot` game with a NULL score is not settled either -- "never guess a result"
holds here the same way `harness.settlement.parlay_grade._score_state` holds it for a leg -- and
stays ungraded for the next pass, exactly as an `in_progress` or `scheduled` game does.

**One quote at a time, in its own savepoint** (review I1, the `harness.settlement.parlay_grade`
shape). A quote whose `legs` holds something `grade_rfq_quotes` cannot read, or any other raise,
is left untouched for the next pass; the failure is counted in `counts["errors"]`, and every
other quote in the pass still grades.

Runs immediately after `parlay_grade`, idempotent, and yields on a spent budget.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import RfqQuote
from harness.settlement.job import Budget, StageResult, register_stage

log = logging.getLogger(__name__)

MIN_BUDGET_S = 30
#: How far **before kickoff** a leg's newest `direct` fair value may have been written and
#: still count as that leg's close. `_CLOSING_LEG` bounds the fair to `created_at <= kickoff_utc`
#: (review I3: an in-play or post-game fair is never the close), and this is the staleness floor
#: on the early side: `created_at < kickoff - CLOSING_WINDOW` has no current close at all.
CLOSING_WINDOW = timedelta(hours=6)

_UNGRADED = text("""
    select q.id, q.rfq_id, q.yes_bid, q.no_bid
    from rfq_quotes q
    where q.graded_at is null and q.declined_reason is null
    order by q.computed_at
""")

# Fix 35: served by `ix_fair_leg_lookup` (`harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL`) --
# `(game_id, market_type, outcome_team_id, outcome_side, threshold, created_at desc) where
# fair_source = 'direct'` covers this lateral's five equality predicates and its sort, the same
# index `_LEG` in `harness/venues/kalshi/rfq_quote.py` reads. The `created_at <= g.kickoff_utc`
# bound below is a range on the index's trailing (descending) column, so the scan still starts
# from the newest row and stops at the first one at or before kickoff.
_CLOSING_LEG = text("""
    select f.fair_p, f.created_at, g.status, g.kickoff_utc, g.home_score, g.away_score
    from venue_markets vm
    join games g on g.id = vm.game_id
    left join lateral (
        select fv.fair_p, fv.created_at from fair_values fv
        where fv.game_id = vm.game_id and fv.market_type = vm.market_type
          and fv.outcome_team_id is not distinct from vm.side_team_id
          and fv.outcome_side is not distinct from vm.side
          -- Review C1: threshold is part of the shape's identity; without it a game with two
          -- spread strikes or two total lines is graded off whichever line was priced last.
          and fv.threshold is not distinct from vm.threshold
          and fv.fair_source = 'direct'
          -- Review I3: the close is the last fair at or before kickoff. Without this bound the
          -- lateral join returns the newest `direct` fair full stop, which for a finished NFL
          -- game is normally an in-play price from deep in the fourth quarter, not a close.
          and fv.created_at <= g.kickoff_utc
        order by fv.created_at desc limit 1
    ) f on true
    where vm.ticker = :ticker
""")

_LEGS = text("select legs from rfqs where id = :rfq_id")
_FINAL = ("final", "final_ot")
#: Review I4: a game that will never be played (or never finish) voids every quote with a leg on
#: it, rather than leaving that quote in the ungraded queue for the rest of the season.
_VOID_STATUSES = ("postponed", "canceled")


def grade_rfq_quotes(session: Session, now: datetime, budget: Budget) -> StageResult:
    """The stage. One pass over every ungraded, undeclined quote, oldest first.

    Each quote grades inside its own savepoint (`session.begin_nested()`) and commits on its
    own (review I1): a quote that raises rolls back to its own savepoint, alone, is counted in
    `counts["errors"]`, and is left untouched for the next pass -- a bad row must never cost the
    pass every quote it already graded.
    """
    counts = {"graded": 0, "stale": 0, "waiting": 0, "voided": 0, "errors": 0}
    exhausted = False
    for row in session.execute(_UNGRADED).all():
        if budget.remaining_s() < MIN_BUDGET_S:
            exhausted = True
            break
        try:
            with session.begin_nested():
                _grade_quote(session, row, now, counts)
            session.commit()
        except Exception:  # noqa: BLE001 - one quote must not cost the pass
            session.rollback()
            log.exception("grading rfq quote %s (rfq %s) failed", row.id, row.rfq_id)
            counts["errors"] += 1
    return StageResult(name="rfq_grade", counts=counts, budget_exhausted=exhausted)


def _void(quote: RfqQuote, now: datetime) -> None:
    quote.graded_at = now
    quote.voided = True
    quote.closing_stale = False
    quote.closing_fair = None
    quote.pnl_yes = None
    quote.pnl_no = None


def _grade_quote(session: Session, row, now: datetime, counts: dict) -> None:
    legs = session.execute(_LEGS, {"rfq_id": row.rfq_id}).scalar() or []
    if not legs:
        # Reported Minor M5: an rfqs row with no legs has no product to grade. Unreachable
        # through compute_quote (fewer than two legs always declines single_leg), and left
        # "waiting" rather than graded with a fabricated certainty (the empty product, 1.0000).
        counts["waiting"] += 1
        return

    closing = Decimal("1")
    stale = False
    settled = True
    voided = False
    for leg in legs:
        result = session.execute(_CLOSING_LEG, {"ticker": leg.get("market_ticker")}).first()
        if result is None:
            settled = False
            break
        if result.status in _VOID_STATUSES:
            voided = True
            break
        if result.status not in _FINAL:
            settled = False
            break
        if result.home_score is None or result.away_score is None:
            # "Never guess a result": a final game with no recorded score yet is not settled,
            # the same rule harness.settlement.parlay_grade's _score_state holds a leg to.
            settled = False
            break
        if result.fair_p is None or (result.kickoff_utc is not None
                                     and result.created_at is not None
                                     and result.created_at < result.kickoff_utc
                                     - CLOSING_WINDOW):
            # No current close for this leg. Nothing is substituted: a fabricated 0.5 would
            # land in `closing_fair` and then in a P&L that t10 averages, and the row would
            # read as a measurement of a market instead of an artefact of the substitution.
            stale = True
        else:
            closing *= Decimal(str(result.fair_p))

    if voided:
        _void(session.get(RfqQuote, row.id), now)
        counts["voided"] += 1
        return
    if not settled:
        counts["waiting"] += 1
        return
    quote = session.get(RfqQuote, row.id)
    quote.graded_at = now
    quote.closing_stale = stale
    if stale:
        # Ruling B-I6 asks only that a quote with any stale closing leg be reported
        # separately. It is: graded, flagged, and with no number invented for it. t10 gives
        # these their own row and prints the placeholder in the two P&L columns.
        quote.closing_fair = None
        quote.pnl_yes = None
        quote.pnl_no = None
    else:
        quote.closing_fair = closing.quantize(Decimal("0.0001"))
        quote.pnl_yes = (quote.closing_fair - quote.yes_bid
                         if quote.yes_bid is not None else None)
        quote.pnl_no = ((Decimal("1") - quote.closing_fair) - quote.no_bid
                        if quote.no_bid is not None else None)
    counts["graded"] += 1
    counts["stale"] += int(stale)


register_stage("rfq_grade", grade_rfq_quotes)
