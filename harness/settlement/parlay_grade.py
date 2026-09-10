"""Grading placed parlay cards (addendum §1.3 "Settlement", ruling B-C4, B-I7).

Runs hourly with the settlement chain, **immediately after `settle`** -- it grades against the
finals that stage just resolved, and the settler's budget is shared across every stage, so a
stage placed last is the one that never runs on a busy Sunday.

**Idempotent, and it resumes.** A leg already graded is skipped, the ledger's `return` and `void`
rows are written once per card, and a spent budget yields with `budget_exhausted` so the next
hour picks up where this one stopped.

**Every leg grades through `harness.parlay.needs.leg_outcome`** (fix round 1, rulings C1/C2/I1),
not `harness.settlement.settle.resolve_market`. That function speaks the Kalshi series
convention -- threshold as the venue's own margin bar, over-only totals, half-point lines -- and
a parlay leg speaks DraftKings': `threshold` is the handicap stored from the odds row's `point`,
a total names an explicit `side`, and a whole-number line pushes. `leg_outcome` shares its
helpers with the sentence functions the slip surface renders from, so the grade and the "needs"
text can never disagree. A refund is not a win: a `push` (or a `postponed`/`canceled` game's
`void`) pays `void` on the leg, and a card whose every leg lands there returns its stake.

**One card at a time, in its own savepoint** (fix round 1, ruling I2). A leg with an unknown
market type, a spread/total leg with no threshold, or a side team that matches neither side of
its game is data the harness cannot grade -- `leg_outcome` raises rather than guessing, and
`grade_parlays` catches it per card: the card is left untouched for the next pass, the failure is
counted in `counts["errors"]`, and every other card in the pass still grades. A raise from one
game must never cost the pass every card it already settled.

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
from harness.parlay.needs import ScoreState, leg_outcome, leg_spec
from harness.research.spend import chicago_day
from harness.settlement.job import Budget, StageResult, register_stage

log = logging.getLogger(__name__)

#: The stage yields below this many seconds of the settler's shared budget.
MIN_BUDGET_S = 30

_LIVE_CARDS = text("""
    select c.id from parlay_cards c
    where c.status in ('placed', 'alive')
    order by c.built_at
""")
_GAME = text("select status, home_team_id, away_team_id, home_score, away_score "
             "from games where id = :game_id")


def _score_state(game) -> ScoreState | None:
    """A `_GAME` row as a `ScoreState`, or `None` while there is nothing to grade against yet.

    `postponed`/`canceled` builds a `ScoreState` even with no recorded score -- `leg_outcome`
    never reads its score fields for that branch, it only needs the status -- so a game that
    will never be played (or never finish) voids on that status alone. `final`/`final_ot` needs
    an actual score: "never guess a result" holds for those, so a missing one stays `None` and
    the leg stays ungraded for the next pass.
    """
    if game.status in ("postponed", "canceled"):
        return ScoreState(status=game.status, home_team_id=game.home_team_id,
                          away_team_id=game.away_team_id, home_score=0, away_score=0)
    if game.home_score is None or game.away_score is None:
        return None
    return ScoreState(status=game.status, home_team_id=game.home_team_id,
                      away_team_id=game.away_team_id, home_score=int(game.home_score),
                      away_score=int(game.away_score))


def _grade_leg(session: Session, leg: ParlayLeg, now: datetime) -> str:
    game = session.execute(_GAME, {"game_id": leg.game_id}).first()
    if game is None:
        return leg.status
    outcome = leg_outcome(leg_spec(leg), _score_state(game))
    if outcome is None:
        return leg.status
    # A refund is not a win: `push` and `void` both land on the leg as `void`.
    leg.status = "void" if outcome in ("push", "void") else outcome
    leg.graded_at = now
    return leg.status


def grade_parlays(session: Session, now: datetime, budget: Budget) -> StageResult:
    """The stage. One pass over every `placed` or `alive` card, oldest first.

    A card is visited once per pass: its ungraded legs are graded against their games' finals,
    and a card whose every leg has landed is settled and paid. A card still carrying an ungraded
    leg is left `alive` for the next hour, which is also what makes the pass resumable -- the
    budget is checked between cards, never inside one, so a card is never left half-graded.

    Each card grades inside its own savepoint (`session.begin_nested()`) and commits on its own:
    a card that raises rolls back to its own savepoint, alone, and is counted in
    `counts["errors"]` rather than costing the pass every card already settled.

    `counts` goes into `job_runs.notes` verbatim, so every value here is an int.
    """
    counts = {"cards": 0, "legs": 0, "cashed": 0, "busted": 0, "void": 0, "errors": 0}
    exhausted = False
    for card_id in session.execute(_LIVE_CARDS).scalars().all():
        if budget.remaining_s() < MIN_BUDGET_S:
            exhausted = True
            break
        try:
            with session.begin_nested():
                _grade_card(session, card_id, now, counts)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one card must not cost the pass
            session.rollback()
            log.exception("grading parlay card %s failed", card_id)
            counts["errors"] += 1
    return StageResult(name="parlay_grade", counts=counts, budget_exhausted=exhausted)


def _grade_card(session: Session, card_id: int, now: datetime, counts: dict) -> None:
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
        return
    _settle_card(session, card, legs, now, counts)


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
    hit_legs = [leg for leg in legs if leg.status == "hit"]
    pushed = any(leg.status == "void" for leg in legs)
    if not hit_legs:
        # Every leg pushed (or voided): the book refunds the stake.
        card.status = "void"
        counts["void"] += 1
        _pay(session, card, "void", stake, iso, now)
        return
    card.status = "cashed"
    counts["cashed"] += 1
    if pushed:
        # DraftKings drops a pushed leg and re-prices the slip off the surviving legs' own
        # odds (fix round 1, ruling I4): `dk_payout_actual` was quoted at placement over every
        # leg, including the one that no longer counts.
        payout = stake
        for leg in hit_legs:
            payout *= leg.dk_decimal
    else:
        payout = placement.dk_payout_actual if placement is not None else card.dk_payout_est
        if payout is None:
            # A card with no recorded payout still cashed; the ledger records the stake back and
            # the task report says so, rather than inventing a number the book never quoted.
            payout = stake
        payout = Decimal(str(payout))
    _pay(session, card, "return", payout, iso, now)


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
