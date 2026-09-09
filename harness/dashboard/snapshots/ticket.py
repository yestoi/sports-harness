"""Ticket: the week's fun parlays.

Spec §2.5. This is the one surface that shows **real money** -- the owner's $50-a-week fun
budget, placed by hand at DraftKings -- and the one that is allowed to be loud. Its badge says so
instead of saying PAPER, and paper and fun money never appear on the same surface.

The parlay tables are empty in this phase: phase 5c ships the card builder, `harness parlay
placed`, the leg-probability writer and the `parlay_grade` stage. What ships now is the surface,
the between-cards state, and the guards -- including the sanitizer on `plain_text` and
`rationale`, because a model writes the rationale and this surface is the only thing standing
between it and the page until the writer that sanitizes at insert ships.

**Never shown here.** Any paper number, any research variant, any CLV. No DraftKings account
state. No place button: placement is by hand and is confirmed through the CLI.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.snapshots import base_payload, register_builder, section
from harness.parlay.needs import needs, ScoreState
from harness.telemetry import sanitize_reason

log = logging.getLogger(__name__)

CADENCE_IN_WINDOW_S = 15
CADENCE_OUT_S = 60
#: The owner's weekly fun budget (v2 spec §8.1, roadmap phase 5c). Real money.
WEEKLY_BUDGET = Decimal("50.00")
#: The badge, in place of PAPER, on this surface and no other.
BADGE = "FUN MONEY - $50/WEEK - PLACED BY HAND"
#: How much of a leg's probability history the small bar shows (spec §2.5 item 1).
PROB_WINDOW = timedelta(hours=6)
#: The standing rule for what a card must carry (spec §2.5 item 3).
ANCHOR_RULE = "every card carries an LSU or Saints leg"

TICKET_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                         "badge", "cards", "season", "between"})

_LIVE_CARDS = text("""
    select c.id, c.year, c.week, c.sport, c.kind, c.built_at, c.stake, c.dk_payout_est,
           c.true_prob_est, c.hold_est, c.rationale, c.status, c.correlated, c.anchor_leg_id,
           p.placed_at, p.stake_actual, p.dk_payout_actual, p.dk_odds_actual
    from parlay_cards c
    left join parlay_placements p on p.card_id = c.id
    where c.status in ('placed', 'alive', 'cashed', 'busted')
    order by case when c.status in ('placed', 'alive') then 0 else 1 end, c.built_at desc
    limit 10
""")
_LEGS = text("""
    select l.id, l.card_id, l.seq, l.game_id, l.market_type, l.side_team_id, l.side,
           l.threshold, l.dk_american, l.dk_decimal, l.plain_text, l.status,
           g.home_team_id, g.away_team_id, g.status as game_status
    from parlay_legs l
    left join games g on g.id = l.game_id
    where l.card_id = any(:card_ids)
    order by l.card_id, l.seq
""")
_SCORES = text("""
    select distinct on (game_id) game_id, ts, status, period, clock, home_score, away_score
    from game_score_events where game_id = any(:game_ids)
    order by game_id, ts desc
""")
_LEG_PROBS = text("""
    select distinct on (leg_id) leg_id, ts, sharp_p, book_p
    from parlay_leg_probs where leg_id = any(:leg_ids)
    order by leg_id, ts desc
""")
#: Spec §2.5 asks for "sharps say NN %" **with a small history bar**, so the newest row is not
#: enough: the last 6 h of a leg's probability, which is what `legProbHistory` draws. Bounded by
#: the leg ids of the live cards and by the window, and `parlay_leg_probs` is a few hundred rows
#: per leg per game.
_LEG_PROB_HISTORY = text("""
    select leg_id, ts, sharp_p from parlay_leg_probs
    where leg_id = any(:leg_ids) and ts >= :since
    order by leg_id, ts
""")
_SEASON = text("""
    select kind, coalesce(sum(amount), 0) as total from parlay_ledger group by kind
""")
_WEEK_STAKED = text("""
    select coalesce(sum(amount), 0) from parlay_ledger
    where kind = 'stake' and year = :year and week = :week
""")
_STRIP = text("""
    select id, year, week, kind, status, stake, dk_payout_est
    from parlay_cards order by built_at desc limit 40
""")


def _dec(value):
    return float(value) if value is not None else None


def _cards(session: Session, now: datetime) -> list[dict]:
    rows = list(session.execute(_LIVE_CARDS))
    if not rows:
        return []
    card_ids = [row.id for row in rows]
    legs = list(session.execute(_LEGS, {"card_ids": card_ids}))
    game_ids = sorted({leg.game_id for leg in legs if leg.game_id is not None})
    leg_ids = [leg.id for leg in legs]
    scores = {row.game_id: row for row in
              session.execute(_SCORES, {"game_ids": game_ids})} if game_ids else {}
    probs = {row.leg_id: row for row in
             session.execute(_LEG_PROBS, {"leg_ids": leg_ids})} if leg_ids else {}
    prob_history: dict[int, list] = {}
    if leg_ids:
        for row in session.execute(_LEG_PROB_HISTORY,
                                   {"leg_ids": leg_ids, "since": now - PROB_WINDOW}):
            prob_history.setdefault(row.leg_id, []).append(
                [row.ts.isoformat(), _dec(row.sharp_p)])

    by_card: dict[int, list] = {}
    for leg in legs:
        score = scores.get(leg.game_id)
        state = None
        if score is not None and score.home_score is not None:
            state = ScoreState(status=score.status, home_team_id=leg.home_team_id,
                               away_team_id=leg.away_team_id,
                               home_score=int(score.home_score),
                               away_score=int(score.away_score))
        prob = probs.get(leg.id)
        by_card.setdefault(leg.card_id, []).append({
            "leg_id": leg.id, "seq": leg.seq, "game_id": leg.game_id,
            "market_type": leg.market_type, "side": leg.side,
            "threshold": _dec(leg.threshold),
            "dk_american": leg.dk_american, "dk_decimal": _dec(leg.dk_decimal),
            # Model- or template-written, and its writer ships in phase 5c: sanitized here.
            "plain_text": sanitize_reason(leg.plain_text or ""),
            "status": leg.status,
            "needs": needs(_leg_spec(leg), state),
            "home_score": int(score.home_score) if score and score.home_score is not None
                          else None,
            "away_score": int(score.away_score) if score and score.away_score is not None
                          else None,
            "period": score.period if score else None,
            "clock": sanitize_reason(score.clock or "") if score else None,
            "score_age_s": (now - score.ts).total_seconds() if score else None,
            "sharp_p": _dec(prob.sharp_p) if prob else None,
            "book_p": _dec(prob.book_p) if prob else None,
            "sharp_p_history": prob_history.get(leg.id, []),
        })

    cards = []
    for row in rows:
        card_legs = by_card.get(row.id, [])
        remaining = [leg for leg in card_legs if leg["status"] in ("pending", "alive")]
        card = {
            "card_id": row.id, "year": row.year, "week": row.week, "sport": row.sport,
            "kind": row.kind, "status": row.status, "correlated": bool(row.correlated),
            "built_at": row.built_at.isoformat(),
            "stake": _dec(row.stake_actual if row.stake_actual is not None else row.stake),
            "payout": _dec(row.dk_payout_actual if row.dk_payout_actual is not None
                           else row.dk_payout_est),
            "true_prob_est": _dec(row.true_prob_est), "hold_est": _dec(row.hold_est),
            "dk_odds_actual": row.dk_odds_actual,
            "rationale": sanitize_reason(row.rationale or ""),
            "placed": row.placed_at is not None,
            "placed_at": row.placed_at.isoformat() if row.placed_at else None,
            "legs": card_legs, "legs_remaining": len(remaining),
        }
        card["sentences"] = sentences.ticket_card(card)
        cards.append(card)
    return cards


def _leg_spec(leg):
    from harness.parlay.needs import LegSpec

    return LegSpec(leg.market_type, leg.side_team_id, leg.side, leg.threshold)


def _season(session: Session) -> dict:
    totals = {row.kind: float(row.total) for row in session.execute(_SEASON)}
    staked = totals.get("stake", 0.0)
    returned = totals.get("return", 0.0)
    strip = [{"card_id": row.id, "year": row.year, "week": row.week, "kind": row.kind,
              "status": row.status, "stake": _dec(row.stake),
              "payout": _dec(row.dk_payout_est)} for row in session.execute(_STRIP)]
    return {"staked": staked, "returned": returned, "net": returned - staked, "strip": strip}


def _between(session: Session, now: datetime) -> dict:
    """The state the surface is in for most of the week (spec §2.5 item 3): when the next card
    is built, the anchor rule, and what is left of this week's $50."""
    iso = now.isocalendar()
    staked = Decimal(str(session.execute(
        _WEEK_STAKED, {"year": iso.year, "week": iso.week}).scalar() or 0))
    # College cards are built on Friday, NFL cards on Saturday evening.
    next_day = "Friday" if now.weekday() < 4 else "Saturday evening"
    return {"next_build_day": next_day, "anchor_rule": ANCHOR_RULE,
            "budget_left": float(WEEKLY_BUDGET - staked),
            "weekly_budget": float(WEEKLY_BUDGET),
            "year": iso.year, "week": iso.week}


def build_ticket(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("ticket", now, settings, CADENCE_OUT_S)
    payload["badge"] = BADGE
    section(payload, "cards", lambda: _cards(session, now))
    section(payload, "season", lambda: _season(session))
    section(payload, "between", lambda: _between(session, now))
    between = payload["between"] if isinstance(payload["between"], dict) else {}
    payload["sentences"] = {"between": sentences.ticket_between(between)}
    payload["readings"] = {}
    return payload


register_builder("ticket", build_ticket)
