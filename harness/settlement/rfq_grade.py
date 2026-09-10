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
#: still count as that leg's close. The test is `created_at < kickoff - CLOSING_WINDOW`, so
#: it bounds how *early* the fair may be and places no bound on how late.
CLOSING_WINDOW = timedelta(hours=6)

_UNGRADED = text("""
    select q.id, q.rfq_id, q.yes_bid, q.no_bid
    from rfq_quotes q
    where q.graded_at is null and q.declined_reason is null
    order by q.computed_at
""")

_CLOSING_LEG = text("""
    select f.fair_p, f.created_at, g.status, g.kickoff_utc
    from venue_markets vm
    join games g on g.id = vm.game_id
    left join lateral (
        select fv.fair_p, fv.created_at from fair_values fv
        where fv.game_id = vm.game_id and fv.market_type = vm.market_type
          and fv.outcome_team_id is not distinct from vm.side_team_id
          and fv.outcome_side is not distinct from vm.side
          and fv.fair_source = 'direct'
        order by fv.created_at desc limit 1
    ) f on true
    where vm.ticker = :ticker
""")

_LEGS = text("select legs from rfqs where id = :rfq_id")
_FINAL = ("final", "final_ot")


def grade_rfq_quotes(session: Session, now: datetime, budget: Budget) -> StageResult:
    counts = {"graded": 0, "stale": 0, "waiting": 0}
    exhausted = False
    for row in session.execute(_UNGRADED).all():
        if budget.remaining_s() < MIN_BUDGET_S:
            exhausted = True
            break
        legs = session.execute(_LEGS, {"rfq_id": row.rfq_id}).scalar() or []
        closing = Decimal("1")
        stale = False
        settled = True
        for leg in legs:
            result = session.execute(_CLOSING_LEG, {"ticker": leg.get("market_ticker")}).first()
            if result is None or result.status not in _FINAL:
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
        if not settled:
            counts["waiting"] += 1
            continue
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
    session.flush()
    return StageResult(name="rfq_grade", counts=counts, budget_exhausted=exhausted)


register_stage("rfq_grade", grade_rfq_quotes)
