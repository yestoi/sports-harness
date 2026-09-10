"""Confirming a hand-placed slip, and expiring the ones nobody placed (addendum §1.3).

This is the one place in the harness where real money is recorded. It records; it never places.
The operator types the slip into DraftKings and then tells the harness what they actually got,
and the two refusals here exist so that what the harness records is what they actually got:

* **The weekly cap is hard** (D15). $50 an ISO week against `parlay_ledger`, and a card that
  would cross it is refused rather than trimmed. The $10 the allocation leaves over stays
  unspent, deliberately. The week is keyed to America/Chicago, not UTC: `now.isocalendar()` on a
  raw UTC timestamp attributes Sunday-night activity (as late as 7 p.m. Central, 01:00 UTC
  Monday) to next week's cap for about five hours, which is wrong for a budget the operator
  thinks of as running Sunday's slate through Saturday's.
* **A moved line is refused** (ruling A-M7). If the newest DraftKings row's `point` differs from
  the card's for any leg, the operator has to say `--leg-line <seq>=<point>` for it. A moved line
  is a different bet, and confirming it silently would put a card in the ledger that is not the
  card that was placed.

`void`, never `expired` (ruling B-C4): `parlay_cards.status` is
`proposed|placed|alive|cashed|busted|void` and `harness/dashboard/snapshots/ticket.py` filters on
that vocabulary in five places, so a status outside it is invisible on the surface built for it.
"""
import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ParlayCard, ParlayLedger, ParlayLeg, ParlayPlacement
from harness.parlay.config import load_config
from harness.parlay.pricing import newest_dk_price
from harness.research.spend import chicago_day

log = logging.getLogger(__name__)

#: The harness market type each `parlay_legs.market_type` maps back to, for the price re-read.
_BACK = {"ml": "moneyline", "spread": "spread", "total": "total"}


class CardNotPlaceable(RuntimeError):
    """No such card, or a card that is not `proposed`."""


class BudgetExceeded(RuntimeError):
    """This ISO week's stakes plus the new one would exceed the weekly budget."""


class LineMoved(RuntimeError):
    """One or more legs' DraftKings lines have moved and were not confirmed."""

    def __init__(self, legs: dict[int, tuple]) -> None:
        moved = ", ".join(f"leg {seq}: {was} -> {now}" for seq, (was, now) in legs.items())
        super().__init__(f"the line moved and was not confirmed: {moved}. "
                         f"Re-run with --leg-line <seq>=<point> for each.")
        self.legs = legs


_WEEK_STAKED = text("""
    select coalesce(sum(amount), 0) from parlay_ledger
    where kind = 'stake' and year = :year and week = :week
""")


def week_staked(session: Session, year: int, week: int) -> Decimal:
    return session.execute(_WEEK_STAKED, {"year": year, "week": week}).scalar() or Decimal("0")


def _payout_from_american(stake: Decimal, american_odds: int) -> Decimal:
    """The slip's total return at the odds the operator actually got."""
    multiple = (Decimal("1") + Decimal(american_odds) / 100
                if american_odds >= 0
                else Decimal("1") + Decimal("-100") / Decimal(american_odds))
    return (stake * multiple).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def mark_placed(session: Session, card_id: int, payout_american: int, stake: Decimal,
                now: datetime, leg_lines: dict[int, Decimal] | None = None) -> ParlayPlacement:
    """Confirm one hand-placed slip. Writes the placement and the ledger's stake row."""
    config = load_config()
    card = session.get(ParlayCard, card_id)
    if card is None or card.status != "proposed":
        raise CardNotPlaceable(
            f"card {card_id} is {'absent' if card is None else card.status}, not proposed")

    iso = chicago_day(now).isocalendar()
    stake = Decimal(str(stake)).quantize(Decimal("0.01"))
    already = week_staked(session, iso.year, iso.week)
    if already + stake > config.weekly_budget:
        raise BudgetExceeded(
            f"${already} already staked this week; ${stake} more would exceed "
            f"${config.weekly_budget}")

    legs = session.query(ParlayLeg).filter_by(card_id=card.id).order_by(ParlayLeg.seq).all()
    accepted = leg_lines or {}
    max_age = timedelta(minutes=config.leg_max_age_minutes)
    moved: dict[int, tuple] = {}
    for leg in legs:
        if leg.seq in accepted:
            leg.threshold = Decimal(str(accepted[leg.seq]))
            continue
        price = newest_dk_price(session, leg.game_id, _BACK[leg.market_type], leg.side_team_id,
                                leg.side, now, max_age)
        if price is None:
            continue          # no fresh row to compare against; the operator's slip stands
        if price.point != leg.threshold:
            moved[leg.seq] = (leg.threshold, price.point)
    if moved:
        raise LineMoved(moved)

    placement = ParlayPlacement(card_id=card.id, placed_at=now, stake_actual=stake,
                                dk_payout_actual=_payout_from_american(stake, payout_american),
                                dk_odds_actual=payout_american, note=None)
    session.add(placement)
    session.add(ParlayLedger(ts=now, card_id=card.id, kind="stake", amount=stake,
                             year=iso.year, week=iso.week))
    card.status = "placed"
    for leg in legs:
        leg.status = "alive"
    session.flush()
    log.info("parlay card %s placed: $%s at %+d", card.id, stake, payout_american)
    return placement


def expire_cards(session: Session, now: datetime) -> int:
    """Void every `proposed` card older than the config's expiry (ruling B-C4). Returns how many.

    No ledger row: a card nobody placed cost nothing and moved no money.
    """
    cutoff = now - timedelta(days=load_config().expiry_days)
    stale = session.query(ParlayCard).filter(
        ParlayCard.status == "proposed", ParlayCard.built_at < cutoff).all()
    for card in stale:
        card.status = "void"
        for leg in session.query(ParlayLeg).filter_by(card_id=card.id):
            leg.status = "void"
            leg.graded_at = now
    if stale:
        session.flush()
        log.info("expired %d proposed parlay cards to void", len(stale))
    return len(stale)


_SHOW = text("""
    select c.id, c.year, c.week, c.sport, c.kind, c.status, c.stake, c.dk_payout_est,
           c.true_prob_est, c.hold_est, c.correlated, c.rationale, p.placed_at, p.stake_actual,
           p.dk_payout_actual, p.dk_odds_actual
    from parlay_cards c
    left join parlay_placements p on p.card_id = c.id
    order by c.built_at desc
    limit 20
""")


def show_cards(session: Session, now: datetime) -> list[dict]:
    """The recent cards and this week's remaining budget, for `harness parlay show`."""
    config = load_config()
    iso = chicago_day(now).isocalendar()
    remaining = config.weekly_budget - week_staked(session, iso.year, iso.week)
    rows = []
    for row in session.execute(_SHOW):
        rows.append({"card_id": row.id, "year": row.year, "week": row.week, "sport": row.sport,
                     "kind": row.kind, "status": row.status, "stake": row.stake,
                     "payout_est": row.dk_payout_est, "payout_actual": row.dk_payout_actual,
                     "true_prob": row.true_prob_est, "hold": row.hold_est,
                     "correlated": row.correlated, "placed_at": row.placed_at,
                     "rationale": row.rationale, "week_remaining": remaining})
    return rows
