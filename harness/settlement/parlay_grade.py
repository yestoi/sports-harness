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

**Prop legs grade from the stat, never from absence** (phase 4.6, addendum 4.4). A prop leg's
result is the newest recorded `player_stat_events` value for its `(game_id, player_id, stat)`,
graded once the *game* is final; only a total absence -- no row at all -- leaves the leg pending
and counted `stat_missing`. A feed that goes quiet must never bust a slip the book will pay.

**Finality is the game's, not the stat's** (review I1). Nothing on a stat row says "this came
from the final box score", so the newest value grades the leg the moment `games.status` reaches
`FINAL_STATUSES`, whatever its age: a value read at the two-minute warning is what the grader
sees if the collector's post-final polls (addendum 4.3, up to 6 h) have not landed yet. A later
poll writes a newer row and the next hourly pass re-reads it, but a leg already settled is not
re-graded -- the owner's `leg_status:<seq>` correction (addendum 5.2) is the remedy until the
collector can mark the final box score.

**Every row this stage writes says `computed`** (D18) and carries the **card's** `(year, week)`
(D20), not the settlement day's. The owner's own figures -- the stake, a corrected stake, a
confirmed return or void -- are written `confirmed` by the placement and correction routes.
`_pay` still writes **one row per `(card_id, kind)`** (review I5): a confirmed result replaces
this stage's computed row rather than landing beside it, which is Task 9/12's job, because every
reader of `parlay_ledger` (`_SEASON`, `_BEST_HIT`, `_WEEK_STAKED`) sums by kind with no `source`
filter and a second row for one kind would be counted twice. A `correlated` card gets no
computed return at all: multiplying its legs assumes an independence they do not have, and a
number the book never quoted is not a settlement figure.

**The vocabulary is `parlay_cards.status`'s own**: `cashed | busted | void`. Never `won | lost`
-- the shipped Ticket builder filters on that vocabulary in five places and a card outside it
would render nowhere at all.
"""
import logging
from collections import namedtuple
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ParlayCard, ParlayLedger, ParlayLeg, ParlayPlacement
from harness.parlay.needs import FINAL_STATUSES, ScoreState, StatState, leg_outcome, leg_spec
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
#: Bound: one leg's `(game_id, player_id, stat)`, newest row only. Index:
#: `ix_player_stat_game_player_ts (game_id, player_id, ts desc)` -- the head of one bounded run.
#: The `stat` filter is residual (the index does not carry it), so the run walked is one
#: player's rows in one game: a few dozen at most, and the whole run only when nothing matches.
#: `id desc` breaks a tie on `ts` (a re-poll inside one second, a backfill): the arbitrary
#: choice would otherwise grade money. `ts` and `correction` are read by T7's surface (the fetch
#: age and the `corrected from 12 to 8` note), not by grading, which needs `value` alone.
_NEWEST_STAT = text("""
    select value, ts, correction from player_stat_events
    where game_id = :game_id and player_id = :player_id and stat = :stat
    order by ts desc, id desc limit 1
""")

#: A ledger row's week is the card's own, never the settlement day's (D20); the shape matches
#: what `date.isocalendar()` used to hand `_pay`.
_Week = namedtuple("_Week", ("year", "week"))


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


def _stat_state(session: Session, leg: ParlayLeg, score: ScoreState | None) -> StatState | None:
    """The newest recorded value of this prop leg's stat, or `None` when none exists yet.

    `final` is the *game's*, read from its status (addendum 4.4 and review I1): no column marks a
    row as the final box score's, so the newest value is what grades once the game is over.
    `source_ts` stays null -- ESPN's summary carries no per-stat timestamp (addendum 4.3) -- and
    the row's own `ts` is the fetch moment the surface ages from.

    A prop leg with no player or no stat is data this stage cannot grade, not a quiet feed:
    it raises (review M5), `grade_parlays` isolates the card and counts `errors`, and the leg
    never hides in `stat_missing`, which the operator reads as "the feed has gone quiet".
    """
    if leg.player_id is None or leg.stat is None:
        raise ValueError(f"prop leg {leg.id} has no player_id/stat to grade")
    row = session.execute(_NEWEST_STAT, {"game_id": leg.game_id, "player_id": leg.player_id,
                                         "stat": leg.stat}).first()
    if row is None:
        return None
    return StatState(stat=leg.stat, value=Decimal(row.value), source_ts=None,
                     final=score is not None and score.status in FINAL_STATUSES)


def _grade_leg(session: Session, leg: ParlayLeg, now: datetime, counts: dict) -> str:
    game = session.execute(_GAME, {"game_id": leg.game_id}).first()
    if game is None:
        return leg.status
    spec = leg_spec(leg)
    score = _score_state(game)
    stat = _stat_state(session, leg, score) if spec.market_type == "prop" else None
    outcome = leg_outcome(spec, score, stat)
    if outcome is None:
        if spec.market_type == "prop" and score is not None and score.status in FINAL_STATUSES:
            # The game is over and no stat row exists: absence is not a miss (addendum 4.4). The
            # leg stays ungraded, the card stays `alive`, and the count is what the hung-leg
            # surface and the WATCH rule read. `score is not None` is doing work: a `final` game
            # whose score was never recorded leaves the leg ungraded *and* uncounted here, so
            # `stat_missing == 0` does not mean "no hung legs" -- `parlay_pending_final`
            # (addendum 1.3) is the rule that catches that one.
            counts["stat_missing"] += 1
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
    counts = {"cards": 0, "legs": 0, "cashed": 0, "busted": 0, "void": 0, "errors": 0,
              "stat_missing": 0}
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
        if _grade_leg(session, leg, now, counts) in ("hit", "miss", "void"):
            counts["legs"] += 1
    counts["cards"] += 1
    if any(leg.status not in ("hit", "miss", "void") for leg in legs):
        card.status = "alive"
        return
    _settle_card(session, card, legs, now, counts)


def _settle_card(session: Session, card: ParlayCard, legs, now: datetime, counts: dict) -> None:
    placement = session.get(ParlayPlacement, card.id)
    stake = placement.stake_actual if placement is not None else card.stake
    # D20: every row carries the **card's** `(year, week)`, not the settlement day's, so a
    # Saturday-built card settled on Monday stays in its slate's week -- which is the week the
    # cap counts and the week `mark_placed` keys the stake row this pays against to. The card's
    # own week was itself keyed to the America/Chicago day at build.
    iso = _Week(card.year, card.week)
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
        _pay(session, card, "void", stake, iso, now, source="computed")
        return
    card.status = "cashed"
    counts["cashed"] += 1
    if card.correlated:
        # D18/C5: the harness prices a same-game card by multiplying its legs, which assumes an
        # independence the legs do not have and the book never quoted. That product is not a
        # settlement figure, so a correlated card gets no computed return at all: the owner's
        # `confirmed` return (addendum 5.2) is the only one. The card still cashed.
        return
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
    _pay(session, card, "return", payout, iso, now, source="computed")


def _pay(session: Session, card: ParlayCard, kind: str, amount: Decimal, iso, now,
         source: str = "computed") -> None:
    """One ledger row per `(card_id, kind)`, carrying where its figure came from.

    The guard matches on `(card_id, kind)` alone (review I5). `source` says whether the harness
    derived the figure (`computed`) or the owner typed it (`confirmed`, D18), but it is not part
    of the key: `parlay_ledger`'s readers (`_SEASON`, `_BEST_HIT`, `_WEEK_STAKED`) sum by kind
    with no `source` filter, so a confirmed row beside a computed one for the same kind would be
    counted twice in the season tiles and the week's stake. The owner's confirmed result
    **replaces** this stage's computed row -- the correction route's job (addendum 5.2, Tasks 9
    and 12) -- and never adds to it.
    """
    exists = session.execute(text(
        "select 1 from parlay_ledger where card_id = :c and kind = :k"),
        {"c": card.id, "k": kind}).first()
    if exists:
        return
    session.add(ParlayLedger(ts=now, card_id=card.id, kind=kind,
                             amount=amount.quantize(Decimal("0.01")),
                             year=iso.year, week=iso.week, source=source))


register_stage("parlay_grade", grade_parlays)
