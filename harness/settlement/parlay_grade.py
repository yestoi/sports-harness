"""Grading placed parlay cards (addendum §1.3 "Settlement", ruling B-C4, B-I7).

Runs hourly with the settlement chain, **immediately after `settle`** -- it grades against the
finals that stage just resolved, and the settler's budget is shared across every stage, so a
stage placed last is the one that never runs on a busy Sunday.

**Idempotent, and it resumes.** A leg already graded is skipped, the ledger's `return` and `void`
rows are written once per card, and a spent budget yields with `budget_exhausted` so the next
hour picks up where this one stopped.

**The push rules are `resolve_market`'s**, not a second copy. A tied moneyline pays half a
contract in the paper book; on a slip a refund is not a win, so a leg that resolves to a half
pays `void`, and a card whose every leg voids returns its stake.

**The vocabulary is `parlay_cards.status`'s own**: `cashed | busted | void`. Never `won | lost`
-- the shipped Ticket builder filters on that vocabulary in five places and a card outside it
would render nowhere at all.
"""
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ParlayCard, ParlayLedger, ParlayLeg, ParlayPlacement
from harness.parlay.needs import FINAL_STATUSES
from harness.research.spend import chicago_day
from harness.settlement.job import Budget, StageResult, register_stage
from harness.settlement.settle import HALF, ONE, resolve_market

log = logging.getLogger(__name__)

#: The stage yields below this many seconds of the settler's shared budget.
MIN_BUDGET_S = 30
#: `parlay_legs.market_type` back to the harness's own names, for `resolve_market`.
_BACK = {"ml": "moneyline", "spread": "spread", "total": "total"}

_LIVE_CARDS = text("""
    select c.id from parlay_cards c
    where c.status in ('placed', 'alive')
    order by c.built_at
""")
_GAME = text("select status, home_team_id, away_team_id, home_score, away_score "
             "from games where id = :game_id")


def _grade_leg(session: Session, leg: ParlayLeg, now: datetime) -> str:
    game = session.execute(_GAME, {"game_id": leg.game_id}).first()
    if game is None:
        return leg.status
    if game.status in ("postponed", "canceled"):
        # A void needs no result: the game will never be played (or never finish), score or no
        # score, and a card left `alive` for it would sit on the weekly cap forever.
        leg.status, leg.graded_at = "void", now
        return leg.status
    if game.status not in FINAL_STATUSES or game.home_score is None:
        # Only `final`/`final_ot` reach here (postponed/canceled are handled above), and there
        # "never guess a result" still holds: no score yet means the leg stays ungraded.
        return leg.status
    payout = resolve_market(_BACK[leg.market_type], leg.threshold, leg.side_team_id,
                            game.home_team_id, game.away_team_id,
                            int(game.home_score), int(game.away_score))
    # A refund is not a win: `HALF` is the paper book's tie, and on a slip it is a push.
    leg.status = "hit" if payout == ONE else ("void" if payout == HALF else "miss")
    leg.graded_at = now
    return leg.status


def grade_parlays(session: Session, now: datetime, budget: Budget) -> StageResult:
    counts = {"cards": 0, "legs": 0, "cashed": 0, "busted": 0, "void": 0}
    exhausted = False
    for card_id in session.execute(_LIVE_CARDS).scalars().all():
        if budget.remaining_s() < MIN_BUDGET_S:
            exhausted = True
            break
        card = session.get(ParlayCard, card_id)
        legs = session.query(ParlayLeg).filter_by(card_id=card.id).order_by(ParlayLeg.seq).all()
        for leg in legs:
            if leg.status in ("hit", "miss", "void"):
                continue
            if _grade_leg(session, leg, now) in ("hit", "miss", "void"):
                counts["legs"] += 1
        counts["cards"] += 1
        if any(leg.status not in ("hit", "miss", "void") for leg in legs):
            card.status = "alive"
            continue
        _settle_card(session, card, legs, now, counts)
    session.flush()
    return StageResult(name="parlay_grade", counts=counts, budget_exhausted=exhausted)


def _settle_card(session: Session, card: ParlayCard, legs, now: datetime, counts: dict) -> None:
    placement = session.get(ParlayPlacement, card.id)
    stake = placement.stake_actual if placement is not None else card.stake
    # Global constraints: weeks are keyed to the America/Chicago day, never a raw UTC
    # isocalendar() -- the same rule `mark_placed` follows for the stake row this pays against.
    iso = chicago_day(now).isocalendar()
    if any(leg.status == "miss" for leg in legs):
        card.status = "busted"
        counts["busted"] += 1
        return
    live = [leg for leg in legs if leg.status == "hit"]
    if not live:
        # Every leg pushed: the book refunds the stake.
        card.status = "void"
        counts["void"] += 1
        _pay(session, card, "void", stake, iso, now)
        return
    card.status = "cashed"
    counts["cashed"] += 1
    payout = placement.dk_payout_actual if placement is not None else card.dk_payout_est
    if payout is None:
        # A card with no recorded payout still cashed; the ledger records the stake back and the
        # task report says so, rather than inventing a number the book never quoted.
        payout = stake
    _pay(session, card, "return", Decimal(str(payout)), iso, now)


def _pay(session: Session, card: ParlayCard, kind: str, amount: Decimal, iso, now) -> None:
    exists = session.execute(text(
        "select 1 from parlay_ledger where card_id = :c and kind = :k"),
        {"c": card.id, "k": kind}).first()
    if exists:
        return
    session.add(ParlayLedger(ts=now, card_id=card.id, kind=kind,
                             amount=amount.quantize(Decimal("0.01")),
                             year=iso.year, week=iso.week))


register_stage("parlay_grade", grade_parlays)
