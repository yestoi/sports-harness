"""The combo quote we would have sent, computed and stored and never sent (spec §8.2, F72).

`fair = Pi(leg fair)`; `yes_bid = fair - margin x legs`; `no_bid = 1 - fair - margin x legs`,
with the margin defaulting to three cents a leg. Every number here is a record of what we would
have quoted; nothing in this module can deliver one, and a static test says so.

**The decline rules** (0.11, ruling A-I2). Each leg's `market_ticker` resolves to
`venue_markets.game_id`, and two legs on one `game_id` decline `same_game` -- the spec says "two
legs from one game", and reading that as "one event" would pass a spread and a total on the same
game through as a cross-game combo. A leg that does not resolve falls back to `event_ticker`
distinctness and is counted in `unmatched_legs`, so a reader can see the quote rested on a weaker
test. A leg with no `direct` sharp fair declines `no_fair`; one over the disagreement threshold
declines `disagreement`.

**Both fee branches are stored** (F72, ruling A-I2). F72 subtracts a maker fee only when the
combo is *not* NFL-only-independent, and that test has two readings -- all component games
distinct, or all component events distinct. Both are computed and both are stored, with the
bids the other branch would have quoted, so grading can be re-run either way without re-deriving
anything.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import RfqQuote

log = logging.getLogger(__name__)

DECLINE_SAME_GAME = "same_game"
DECLINE_NO_FAIR = "no_fair"
DECLINE_DISAGREEMENT = "disagreement"
#: Two reasons beyond §1.6's enumerated set, and the task report states them as this plan's.
#: H5's question is which RFQs we would and would not have answered **and why**, so filing a size
#: refusal or a single-market request as `no_fair` would corrupt t10's decline mix with two
#: outcomes that have nothing to do with having a price. `rfq_quotes.declined_reason` is
#: `String(16)`; both fit.
DECLINE_COLLATERAL = "collateral"
DECLINE_SINGLE_LEG = "single_leg"

#: The §8.2 threshold a leg's `fair_values.disagreement` must be under to be quotable: a leg the
#: harness would not act on is not one to quote against.
#:
#: This is **this phase's own constant**, not a borrowed one. The strategy's `disagreement_ok`
#: filter is not a fixed probability cap at all -- each variant carries `disagreement_mult`
#: (1.5 in the shipped set) and multiplies it against its own edge floor -- so there is no
#: number under `harness/variants/` to import, and this phase may not edit those files anyway.
#: Two probability points is the same order as the veto's fair-move invalidator, and the task
#: report states it as a plan-level value.
DISAGREEMENT_MAX = Decimal("0.02")
#: How far back a leg's fair value may be and still count as current for a quote computed on
#: arrival. An RFQ is a live request; a ten-minute-old fair is not a live answer.
FAIR_MAX_AGE = timedelta(minutes=10)
#: F72's family test: a combo whose every component event ticker starts with this one
#: prefix is "NFL-only". Deliberately narrower than `harness.venues.kalshi.public`'s
#: `FOOTBALL_SERIES`, which also carries the three `KXNCAAF*` series -- F72 says NFL, and a
#: college leg makes the combo not independent.
_NFL_PREFIX = "KXNFL"


@dataclass(frozen=True)
class LegFair:
    market_ticker: str
    event_ticker: str
    game_id: int | None
    fair_p: Decimal | None
    disagreement: Decimal | None
    stale: bool


_LEG = text("""
    select vm.game_id, vm.event_ticker, vm.market_type, vm.side_team_id, vm.side,
           f.fair_p, f.disagreement, f.created_at
    from venue_markets vm
    left join lateral (
        select fv.fair_p, fv.disagreement, fv.created_at
        from fair_values fv
        where fv.game_id = vm.game_id and fv.market_type = vm.market_type
          and fv.outcome_team_id is not distinct from vm.side_team_id
          and fv.outcome_side is not distinct from vm.side
          and fv.fair_source = 'direct' and fv.created_at <= :as_of
        order by fv.created_at desc limit 1
    ) f on true
    where vm.ticker = :ticker
""")


def resolve_legs(session: Session, legs: list[dict], as_of: datetime) -> list[LegFair]:
    """Each leg's game and its newest `direct` fair value as of the arrival."""
    out: list[LegFair] = []
    for leg in legs:
        ticker = leg.get("market_ticker") or ""
        row = session.execute(_LEG, {"ticker": ticker, "as_of": as_of}).first()
        if row is None:
            out.append(LegFair(ticker, str(leg.get("event_ticker") or ""), None, None, None,
                               True))
            continue
        stale = row.created_at is None or (as_of - row.created_at) > FAIR_MAX_AGE
        out.append(LegFair(ticker, row.event_ticker or str(leg.get("event_ticker") or ""),
                           row.game_id, row.fair_p, row.disagreement, stale))
    return out


def nfl_only_independent(legs: list[LegFair], key: str) -> bool:
    """F72's test under one reading of "independent": every component event is `KXNFL*` and every
    component `key` is distinct. `key` is `game_id` for the game-level branch and `event_ticker`
    for the event-level one."""
    if not legs:
        return False
    if not all((leg.event_ticker or "").startswith(_NFL_PREFIX) for leg in legs):
        return False
    values = [getattr(leg, key) for leg in legs]
    if any(value is None for value in values):
        return False
    return len(set(values)) == len(values)


def _decline(rfq, now: datetime, legs: list[LegFair], reason: str, margin: Decimal,
             unmatched: int) -> RfqQuote:
    return RfqQuote(rfq_id=rfq.id, computed_at=now, legs=len(legs), fair=None,
                    margin_per_leg=margin, yes_bid=None, no_bid=None,
                    fee_branch_game=None, fee_branch_event=None, fee_subtracted=None,
                    yes_bid_other_branch=None, no_bid_other_branch=None,
                    declined_reason=reason, unmatched_legs=unmatched)


def compute_quote(session: Session, settings, rfq, now: datetime) -> RfqQuote:
    """One stored quote (or decline) for one arrival. Never sends anything."""
    margin = Decimal(str(settings.rfq_margin_per_leg))
    legs = resolve_legs(session, rfq.legs or [], now)
    unmatched = sum(1 for leg in legs if leg.game_id is None)

    if len(legs) < 2:
        quote = _decline(rfq, now, legs, DECLINE_SINGLE_LEG, margin, unmatched)
        session.add(quote)
        return quote

    # 0.11: game-level distinctness, with an event-level fallback for legs that did not resolve.
    keys = [leg.game_id if leg.game_id is not None else f"e:{leg.event_ticker}" for leg in legs]
    if len(set(keys)) != len(keys):
        quote = _decline(rfq, now, legs, DECLINE_SAME_GAME, margin, unmatched)
        session.add(quote)
        return quote
    if any(leg.fair_p is None or leg.stale for leg in legs):
        quote = _decline(rfq, now, legs, DECLINE_NO_FAIR, margin, unmatched)
        session.add(quote)
        return quote
    if any(leg.disagreement is not None and leg.disagreement > DISAGREEMENT_MAX for leg in legs):
        quote = _decline(rfq, now, legs, DECLINE_DISAGREEMENT, margin, unmatched)
        session.add(quote)
        return quote
    fair = Decimal("1")
    for leg in legs:
        fair *= Decimal(str(leg.fair_p))
    fair = fair.quantize(Decimal("0.0001"))

    # The collateral cap, in dollars against dollars. `rfqs.contracts_fp` is a contract quantity
    # and `rfq_collateral_cap_usd` is money, so the comparison uses `exposure_usd`, and a size
    # refusal gets its own reason rather than being filed as "we had no price".
    exposure = exposure_usd(rfq, fair)
    if exposure is not None and exposure > Decimal(str(settings.rfq_collateral_cap_usd)):
        quote = _decline(rfq, now, legs, DECLINE_COLLATERAL, margin, unmatched)
        session.add(quote)
        return quote

    spread = margin * len(legs)

    branch_game = nfl_only_independent(legs, "game_id")
    branch_event = nfl_only_independent(legs, "event_ticker")
    # F72: subtract a maker fee only when the combo is NOT NFL-only-independent.
    fee_game = Decimal("0") if branch_game else margin
    fee_event = Decimal("0") if branch_event else margin

    quote = RfqQuote(
        rfq_id=rfq.id, computed_at=now, legs=len(legs), fair=fair, margin_per_leg=margin,
        yes_bid=_clamp(fair - spread - fee_game),
        no_bid=_clamp(Decimal("1") - fair - spread - fee_game),
        fee_branch_game=branch_game, fee_branch_event=branch_event, fee_subtracted=fee_game,
        yes_bid_other_branch=_clamp(fair - spread - fee_event),
        no_bid_other_branch=_clamp(Decimal("1") - fair - spread - fee_event),
        declined_reason=None, unmatched_legs=unmatched)
    session.add(quote)
    return quote


def exposure_usd(rfq, fair: Decimal) -> Decimal | None:
    """What answering this RFQ would put at risk, in dollars, or None when it cannot be said.

    `target_cost_dollars` is the venue's own dollar figure and is used when it is there. When it
    is not, the exposure is the contract count times the combo's fair value, which is what a
    filled YES side would cost. `contracts_fp` alone is a quantity and is never compared against
    a dollar cap.
    """
    target = getattr(rfq, "target_cost_dollars", None)
    if target is not None:
        return Decimal(str(target))
    contracts = getattr(rfq, "contracts_fp", None)
    if contracts is None:
        return None
    return (Decimal(str(contracts)) * fair).quantize(Decimal("0.0001"))


def _clamp(value: Decimal) -> Decimal:
    """A bid below zero is not a bid. Stored at zero rather than negative, because the column is
    a probability and a negative one would poison every average taken over it."""
    return max(Decimal("0"), value).quantize(Decimal("0.0001"))
