"""The card builder (spec §8.1, addendum §1.3).

A smart card is 3-4 legs from **different games**, anchored on an LSU or Saints moneyline, spread
or total, with the rest drawn from the +EV pool: candidate signals with a `direct` fair value and
an edge over the threshold in the last six hours. A lottery card is 6-8 legs and may take two
legs from one game, in which case the card is labelled `correlated` -- DraftKings will quote
lower than the independence product and a slip that implied otherwise would be lying about the
payout.

**An anchor with no priced DraftKings row ends the build** (D14). Exit 2, `no anchor priced`. A
card built on a stale feed has fictional arithmetic on it, and the operator's next move is to
wait for a tick, not to place it.

**No Odds credit is spent.** Props are out of scope this phase (0.5) and every price comes from
rows the recorder already stored.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ParlayCard, ParlayLeg
from harness.parlay.config import load_config
from harness.parlay.pricing import newest_dk_price

log = logging.getLogger(__name__)

#: The harness's market types, and the two-to-six-character names `parlay_legs.market_type` holds.
_MARKET_NAMES = {"moneyline": "ml", "spread": "spread", "total": "total"}


class NoAnchorPriced(RuntimeError):
    """No LSU or Saints outcome has a DraftKings price inside the freshness window (D14)."""


_POOL = text("""
    select distinct on (g.id, vm.market_type, vm.side_team_id, vm.side)
           g.id as game_id, g.sport, vm.market_type, vm.side_team_id, vm.side, vm.threshold,
           f.fair_p, s.edge, s.created_at, t.abbreviation,
           th.abbreviation as home_abbr, ta.abbreviation as away_abbr
    from signals s
    join venue_markets vm on vm.id = s.venue_market_id
    join games g on g.id = vm.game_id
    -- `signals.gap_snapshot_id` is a `market_gap_snapshots` id, not a `fair_values` id. The
    -- executor's own candidate query hops the same way (`harness/execution/store.py`), and
    -- joining `fair_values` on it directly would silently return another row's fair_p and
    -- fair_source -- which is both the payout arithmetic and the `direct` filter.
    join market_gap_snapshots gs on gs.id = s.gap_snapshot_id
    join fair_values f on f.id = gs.fair_value_id
    left join teams t on t.sport = g.sport and t.id = vm.side_team_id
    -- A `total` row's `side_team_id` is always NULL (over/under has no side team), so `t` above
    -- never matches one. Design 1.3/D14 name the anchor as an LSU or Saints moneyline, spread
    -- *or total*, so a total leg's eligibility comes from the game itself -- home and away --
    -- never from the venue market's side (review round 1, Important 1).
    left join teams th on th.sport = g.sport and th.id = g.home_team_id
    left join teams ta on ta.sport = g.sport and ta.id = g.away_team_id
    where s.replay = false and s.decision = 'candidate'
      and s.created_at > :since and s.created_at <= :now
      and s.edge >= :min_edge
      and f.fair_source = 'direct'
      and g.sport = :sport and g.kickoff_utc > :now
      and vm.market_type in ('moneyline', 'spread', 'total')
    order by g.id, vm.market_type, vm.side_team_id, vm.side, s.created_at desc
""")


def build_card(session: Session, settings, sport: str, week: int, kind: str, now: datetime,
               client=None) -> ParlayCard:
    """One proposed card. Raises `NoAnchorPriced` when no anchor outcome has a fresh price."""
    if kind not in ("smart", "lottery"):
        raise ValueError(f"unknown card kind {kind!r}")
    config = load_config()
    max_age = timedelta(minutes=config.leg_max_age_minutes)
    candidates = session.execute(_POOL, {
        "since": now - timedelta(hours=config.pool_window_hours), "now": now,
        "min_edge": config.pool_min_edge, "sport": sport}).all()

    priced = []
    for row in candidates:
        market = _MARKET_NAMES.get(row.market_type)
        price = newest_dk_price(session, row.game_id, row.market_type, row.side_team_id,
                                row.side, now, max_age)
        if market is None or price is None:
            continue
        if row.market_type == "total":
            # No side team on a total: the anchor is whichever team is playing this game.
            is_anchor = (row.home_abbr in config.anchors) or (row.away_abbr in config.anchors)
        else:
            is_anchor = (row.abbreviation or "") in config.anchors
        priced.append({"row": row, "market": market, "price": price, "is_anchor": is_anchor})

    anchors = [item for item in priced if item["is_anchor"]]
    if not anchors:
        raise NoAnchorPriced("no anchor priced")
    anchors.sort(key=lambda item: item["row"].edge or Decimal("0"), reverse=True)
    chosen = [anchors[0]]

    lo, hi = config.smart_legs if kind == "smart" else config.lottery_legs
    rest = sorted((item for item in priced if item is not chosen[0]),
                  key=lambda item: item["row"].edge or Decimal("0"), reverse=True)
    used_games = {chosen[0]["row"].game_id}
    for item in rest:
        if len(chosen) >= hi:
            break
        if kind == "smart" and item["row"].game_id in used_games:
            continue
        chosen.append(item)
        used_games.add(item["row"].game_id)
    if len(chosen) < lo:
        raise NoAnchorPriced(
            f"only {len(chosen)} priced legs, {kind} needs {lo}")

    stake = config.smart_stake if kind == "smart" else config.lottery_stake
    payout = stake
    true_p = Decimal("1")
    for item in chosen:
        payout *= item["price"].dk_decimal
        true_p *= Decimal(str(item["row"].fair_p))
    correlated = len({item["row"].game_id for item in chosen}) < len(chosen)

    card = ParlayCard(year=now.year, week=week, sport=sport, kind=kind, built_at=now,
                      stake=stake.quantize(Decimal("0.01")),
                      dk_payout_est=payout.quantize(Decimal("0.01")),
                      true_prob_est=true_p.quantize(Decimal("0.000001")),
                      hold_est=_hold(true_p, payout, stake), rationale=None,
                      anchor_leg_id=None, status="proposed", correlated=correlated)
    session.add(card)
    session.flush()

    legs = []
    for seq, item in enumerate(chosen, start=1):
        leg = ParlayLeg(card_id=card.id, seq=seq, game_id=item["row"].game_id,
                        market_type=item["market"], side_team_id=item["row"].side_team_id,
                        side=item["row"].side, threshold=item["price"].point,
                        dk_american=item["price"].dk_american,
                        dk_decimal=item["price"].dk_decimal,
                        plain_text=_plain_text(item)[:80],
                        odds_snapshot_id=item["price"].odds_snapshot_id, status="pending",
                        graded_at=None)
        session.add(leg)
        legs.append((leg, item))
    session.flush()
    card.anchor_leg_id = legs[0][0].id

    from harness.parlay.rationale import write_rationale

    card.rationale = write_rationale(session, settings, card, [leg for leg, _ in legs], now,
                                     client=client)
    session.flush()
    return card


def _hold(true_p: Decimal, payout: Decimal, stake: Decimal) -> Decimal:
    """The book's implied hold on this slip: 1 minus (our probability times the payout multiple).
    Positive is the book's edge over our own estimate."""
    multiple = payout / stake
    return (Decimal("1") - true_p * multiple).quantize(Decimal("0.0001"))


def _plain_text(item) -> str:
    """The fan-facing description. Sanitized at write, and sanitized again on the way into a
    payload, because `harness/dashboard/snapshots/ticket.py` is the surface that renders it."""
    from harness.research.text import sanitize_model_text

    row, market = item["row"], item["market"]
    name = row.abbreviation or "the pick"
    if market == "ml":
        body = f"{name} to win"
    elif market == "spread":
        body = f"{name} {row.threshold:+}" if row.threshold is not None else f"{name} spread"
    else:
        body = f"{(row.side or 'over').title()} {row.threshold}"
    return sanitize_model_text(body, 80)
