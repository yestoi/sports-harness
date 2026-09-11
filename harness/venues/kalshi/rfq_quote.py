"""The combo quote we would have sent, computed and stored and never sent (spec §8.2, F72).

`fair = Pi(leg fair)`; the pre-fee bids are `yes = fair - margin x legs`,
`no = 1 - fair - margin x legs`, with the margin defaulting to three cents a leg. Every number
here is a record of what we would have quoted; nothing in this module can deliver one, and a
static test says so.

**The decline rules** (0.11, ruling A-I2). Each leg's `market_ticker` resolves to
`venue_markets.game_id`, and two legs on one `game_id` decline `same_game` -- the spec says "two
legs from one game", and reading that as "one event" would pass a spread and a total on the same
game through as a cross-game combo. A leg that does not resolve falls back to `event_ticker`
distinctness and is counted in `unmatched_legs`, so a reader can see the quote rested on a weaker
test. A leg with no `direct` sharp fair declines `no_fair`; one over the disagreement threshold
declines `disagreement`.

**Fix 35: `no_fair` is decided before any fair lookup, wherever it can be.** The 03:15-03:45 CT
incident (journal 109) was 4,902 frames in 30 minutes, almost all combos on non-football series
(`KXMVECROSSCATEGORY-SHARD1-...`), every leg of every one running the `fair_values` lateral in
`_LEG` to find nothing -- 16,264 rows scanned to keep 236, 14 s cold. A leg whose `event_ticker`
does not start with a football prefix this harness prices (`KXNFL`/`KXNCAAF`), or whose
`market_ticker` has no `venue_markets` row at all, can never carry a `direct` fair: `_venue_only`
answers that from `venue_markets` alone (a unique-indexed lookup on `ticker`, no
`fair_values` touched), and `compute_quote` declines `no_fair` -- and `single_leg` and
`same_game`, which need only the same cheap resolution -- before `resolve_legs` (the expensive
lateral) ever runs. Only a combo whose every leg is a priced football market reaches it.

**Both fee branches are stored** (F72, ruling A-I2). F72 subtracts a maker fee only when the
combo is *not* NFL-only-independent, and that test has two readings -- all component games
distinct, or all component events distinct. Both are computed and both are stored, with the
bids the other branch would have quoted, so grading can be re-run either way without re-deriving
anything.

**The fee is Kalshi's real maker fee, not the quoting margin** (review M9). `margin` is this
combo's own quoting spread, folded into `spread` above; the fee F72 asks about is a different
quantity, `harness.pricing.fees.fee_per_contract(KALSHI_FOOTBALL, "maker", p, 1)` at the price we
would actually be quoting. It is computed once per side, at that side's own pre-fee price (the
yes side's fee uses the pre-fee yes price, the no side's its own), and subtracted once from that
side -- never multiplied by leg count the way `spread` is. `fee_subtracted` records the yes
side's fee for the branch taken; the no side's own fee follows the same rule at its own price
but is not separately stored, the same asymmetry `pnl_yes`/`pnl_no` already carries elsewhere.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import RfqQuote
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract

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
#: Fix 35: the family test for whether a leg can have a fair *at all* -- both football families
#: this harness prices, not just F72's NFL-only one above. `KXNFL` alone would decline every
#: `KXNCAAFGAME` leg `no_fair` before it ever got a chance to resolve one, which is wrong: the
#: incident's combos were on neither family (`KXMVECROSSCATEGORY-SHARD1-...`), and this is the
#: cheap gate that catches those without a `fair_values` read.
_FOOTBALL_PREFIXES = ("KXNFL", "KXNCAAF")


@dataclass(frozen=True)
class LegFair:
    market_ticker: str
    event_ticker: str
    game_id: int | None
    fair_p: Decimal | None
    disagreement: Decimal | None
    stale: bool


@dataclass(frozen=True)
class LegVenue:
    """A leg's identity alone -- `venue_markets`, never `fair_values`. What `single_leg`,
    `same_game` and the fix-35 `no_fair` precheck need, and all any of them may cost."""
    market_ticker: str
    event_ticker: str
    game_id: int | None


#: Fix 35: the cheap half of what `_LEG` below reads -- `venue_markets` alone, keyed on its
#: unique `ticker`, no `fair_values` touched. `_venue_only` runs this for every leg before
#: `compute_quote` decides whether the combo is even eligible for `resolve_legs`.
_LEG_VENUE = text("select vm.game_id, vm.event_ticker from venue_markets vm where vm.ticker = "
                  ":ticker")

#: Fix 35: served by `ix_fair_leg_lookup` (`harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL`) --
#: `(game_id, market_type, outcome_team_id, outcome_side, threshold, created_at desc) where
#: fair_source = 'direct'` is exactly this lateral's five equality predicates (in that order)
#: plus its sort, under its own partial predicate. Before the fix this ran for every leg of
#: every combo, including the incident's non-football ones, which had no chance of a hit and
#: still walked `ix_fair_game_type_created`'s wider (game_id, market_type, created_at) index down
#: to the newest row and read every candidate in the range to apply the rest of the predicates
#: by hand rather than through the index. `compute_quote` (fix 35) now calls this only for a
#: combo every one of whose legs is already known, cheaply, to be a priced football market.
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
          -- Review C1: `threshold` is part of the shape's identity everywhere else in the
          -- harness (the fair_values unique key, venue_markets.match_key, the settlement/report
          -- joins). Without it, a game with two spread strikes or two total lines returns
          -- whichever line was priced most recently, not the leg's own line.
          and fv.threshold is not distinct from vm.threshold
          and fv.fair_source = 'direct' and fv.created_at <= :as_of
        order by fv.created_at desc limit 1
    ) f on true
    where vm.ticker = :ticker
""")


def _venue_only(session: Session, legs: list[dict]) -> list[LegVenue]:
    """Fix 35: each leg's game and event, from `venue_markets` alone. Cheap enough to run on
    every arrival regardless of what the combo turns out to be: one unique-indexed lookup per
    leg, never `fair_values`."""
    out: list[LegVenue] = []
    for leg in legs:
        ticker = leg.get("market_ticker") or ""
        row = session.execute(_LEG_VENUE, {"ticker": ticker}).first()
        if row is None:
            out.append(LegVenue(ticker, str(leg.get("event_ticker") or ""), None))
        else:
            out.append(LegVenue(ticker, row.event_ticker or str(leg.get("event_ticker") or ""),
                                row.game_id))
    return out


def _is_football(leg: LegVenue) -> bool:
    """Fix 35: whether this leg could possibly have a `direct` fair -- resolved in
    `venue_markets` *and* on a series this harness prices. Neither half is optional: an
    unmatched ticker has no game to price against, and a matched one on, say,
    `KXMVECROSSCATEGORY-SHARD1-...` (the incident's combos) is never going to find one either."""
    return leg.game_id is not None and (leg.event_ticker or "").startswith(_FOOTBALL_PREFIXES)


def resolve_legs(session: Session, legs: list[dict], as_of: datetime) -> list[LegFair]:
    """Each leg's game and its newest `direct` fair value as of the arrival.

    Fix 35: `compute_quote` calls this only once the cheap `_venue_only` resolution has already
    cleared `single_leg`, `same_game` and the "not a priced football market" `no_fair` precheck,
    so every leg this reaches is one that could actually resolve a fair."""
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


def _decline(rfq, now: datetime, legs_count: int, reason: str, margin: Decimal,
             unmatched: int) -> RfqQuote:
    return RfqQuote(rfq_id=rfq.id, computed_at=now, legs=legs_count, fair=None,
                    margin_per_leg=margin, yes_bid=None, no_bid=None,
                    fee_branch_game=None, fee_branch_event=None, fee_subtracted=None,
                    yes_bid_other_branch=None, no_bid_other_branch=None,
                    declined_reason=reason, unmatched_legs=unmatched)


def compute_quote(session: Session, settings, rfq, now: datetime) -> RfqQuote:
    """One stored quote (or decline) for one arrival. Never sends anything.

    Fix 35: `single_leg`, `same_game` and "no leg here could ever have a fair" are decided from
    `_venue_only`'s cheap `venue_markets`-only resolution, before `resolve_legs` -- the lateral
    join into `fair_values` -- runs at all. Only a combo that clears all three reaches it.
    """
    margin = Decimal(str(settings.rfq_margin_per_leg))
    raw_legs = rfq.legs or []
    venue_legs = _venue_only(session, raw_legs)
    unmatched = sum(1 for leg in venue_legs if leg.game_id is None)

    if len(venue_legs) < 2:
        quote = _decline(rfq, now, len(venue_legs), DECLINE_SINGLE_LEG, margin, unmatched)
        session.add(quote)
        return quote

    # 0.11: game-level distinctness, with an event-level fallback for legs that did not resolve.
    keys = [leg.game_id if leg.game_id is not None else f"e:{leg.event_ticker}"
            for leg in venue_legs]
    if len(set(keys)) != len(keys):
        quote = _decline(rfq, now, len(venue_legs), DECLINE_SAME_GAME, margin, unmatched)
        session.add(quote)
        return quote
    if any(not _is_football(leg) for leg in venue_legs):
        # Fix 35: the incident's decline -- a leg on a series this harness never prices
        # (`KXMVECROSSCATEGORY-SHARD1-...`) or with no `venue_markets` row at all can never
        # carry a `direct` fair, so the whole combo declines `no_fair` here, before
        # `resolve_legs` touches `fair_values` for a single leg of it.
        quote = _decline(rfq, now, len(venue_legs), DECLINE_NO_FAIR, margin, unmatched)
        session.add(quote)
        return quote

    legs = resolve_legs(session, raw_legs, now)
    if any(leg.fair_p is None or leg.stale for leg in legs):
        # A leg that passed the cheap football precheck but still has no current `direct` fair
        # (stale, or genuinely unpriced -- e.g. a bye-week or not-yet-primed game) declines here.
        quote = _decline(rfq, now, len(legs), DECLINE_NO_FAIR, margin, unmatched)
        session.add(quote)
        return quote
    if any(leg.disagreement is not None and leg.disagreement > DISAGREEMENT_MAX for leg in legs):
        quote = _decline(rfq, now, len(legs), DECLINE_DISAGREEMENT, margin, unmatched)
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
        quote = _decline(rfq, now, len(legs), DECLINE_COLLATERAL, margin, unmatched)
        session.add(quote)
        return quote

    spread = margin * len(legs)
    base_yes = fair - spread
    base_no = Decimal("1") - fair - spread

    branch_game = nfl_only_independent(legs, "game_id")
    branch_event = nfl_only_independent(legs, "event_ticker")
    # F72 (review M9): subtract Kalshi's real maker fee -- not the quoting margin -- only when
    # the combo is NOT NFL-only-independent. The fee is a function of price, so each side gets
    # its own fee at its own pre-fee price, computed once (never multiplied by leg count).
    fee_yes_game = (Decimal("0") if branch_game
                    else fee_per_contract(KALSHI_FOOTBALL, "maker", base_yes, 1))
    fee_no_game = (Decimal("0") if branch_game
                   else fee_per_contract(KALSHI_FOOTBALL, "maker", base_no, 1))
    fee_yes_event = (Decimal("0") if branch_event
                     else fee_per_contract(KALSHI_FOOTBALL, "maker", base_yes, 1))
    fee_no_event = (Decimal("0") if branch_event
                    else fee_per_contract(KALSHI_FOOTBALL, "maker", base_no, 1))

    quote = RfqQuote(
        rfq_id=rfq.id, computed_at=now, legs=len(legs), fair=fair, margin_per_leg=margin,
        yes_bid=_clamp(base_yes - fee_yes_game),
        no_bid=_clamp(base_no - fee_no_game),
        fee_branch_game=branch_game, fee_branch_event=branch_event,
        # The yes side's own fee, for the branch taken (docstring above says why only one side
        # is stored). The no side's fee (`fee_no_game`) followed the same rule at its own price.
        fee_subtracted=fee_yes_game,
        yes_bid_other_branch=_clamp(base_yes - fee_yes_event),
        no_bid_other_branch=_clamp(base_no - fee_no_event),
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
