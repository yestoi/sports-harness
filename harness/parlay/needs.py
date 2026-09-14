"""What still has to happen for a leg to hit.

Spec §2.5: "The 'needs to happen' text is a pure function of the leg's market, line and the
current score, never a guess." So this module holds no query and no model import: it takes the
leg's shape and the score's shape and returns one short phrase.

The phrases are the fan's, not the system's: "needs 4 or more", "needs 16 more points", "any win
does it", "already done". A push on a whole-number line is called a push, in progress and at the
final whistle alike, because a refund is not a win and a slip that says otherwise is lying to
its reader.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

#: Statuses that mean the game is over and the answer is final.
FINAL_STATUSES = ("final", "final_ot", "postponed", "canceled")


@dataclass(frozen=True)
class LegSpec:
    """One leg's shape, as `parlay_legs` stores it. `threshold` is the leg's own line: the
    team's handicap for a spread (`-7.0` means that team must win by more than seven), or the
    points line for a total."""
    market_type: str                 # ml | spread | total | prop
    side_team_id: int | None
    side: str | None                 # over | under
    threshold: Decimal | None
    #: Phase 4.6 (addendum 4.4): the prop family. All four default, so the three existing
    #: construction sites are unchanged; `period` is `game` in release one (D3).
    stat: str | None = None          # pass_yds | rush_yds | rec_yds | receptions | anytime_td
    period: str = "game"
    operator: str | None = None      # over | under | atleast | yes
    player_id: int | None = None


@dataclass(frozen=True)
class StatState:
    """One player's newest recorded value for one stat (addendum 4.3, 4.4).

    `source_ts` is null for ESPN's summary -- it carries no per-stat timestamp -- so the surface
    shows the fetch age instead. `final` is the game's, not the stat's: a value read before the
    final box score is provisional, because a later poll may correct it downward.
    """
    stat: str
    value: Decimal
    source_ts: datetime | None
    final: bool


@dataclass(frozen=True)
class ScoreState:
    status: str
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int


def leg_spec(row) -> LegSpec:
    """A `ParlayLeg` row as a `LegSpec`."""
    return LegSpec(row.market_type, row.side_team_id, row.side, row.threshold, stat=row.stat,
                   period=row.period or "game", operator=row.operator, player_id=row.player_id)


def score_state(game_row, event_row) -> ScoreState | None:
    """A `games` row plus its newest `game_score_events` row as a `ScoreState`, or None when no
    score has been recorded yet."""
    if event_row is None or event_row.home_score is None or event_row.away_score is None:
        return None
    return ScoreState(status=event_row.status, home_team_id=game_row.home_team_id,
                      away_team_id=game_row.away_team_id, home_score=int(event_row.home_score),
                      away_score=int(event_row.away_score))


def _final(score: ScoreState) -> bool:
    return score.status in FINAL_STATUSES


def _margin(leg: LegSpec, score: ScoreState) -> int | None:
    """Our side's score minus the other side's, or None when the leg names no side."""
    if leg.side_team_id == score.home_team_id:
        return score.home_score - score.away_score
    if leg.side_team_id == score.away_team_id:
        return score.away_score - score.home_score
    return None


def _points(value: Decimal) -> str:
    """A points figure, without a trailing `.0` on a whole number."""
    if value == value.to_integral_value():
        return str(int(value))
    return str(value.normalize())


def _moneyline(leg: LegSpec, score: ScoreState) -> str:
    margin = _margin(leg, score)
    if margin is None:
        return "no rule for this bet"
    if _final(score):
        if margin > 0:
            return "already done"
        return "push, refunded" if margin == 0 else "did not happen"
    if margin >= 0:
        return "any win does it"
    return f"needs to come back from {abs(margin)}"


def _spread(leg: LegSpec, score: ScoreState) -> str:
    margin = _margin(leg, score)
    if margin is None or leg.threshold is None:
        return "no rule for this bet"
    #: The covered margin: positive means the leg is already ahead of its line.
    over = Decimal(margin) + leg.threshold
    if _final(score):
        if over > 0:
            return "already done"
        return "push, refunded" if over == 0 else "did not happen"
    if over > 0:
        return f"covering by {_points(over)}"
    if over == 0:
        return "push as it stands"
    shortfall = -over
    points = math.ceil(float(shortfall))
    if points <= 1:
        return "needs 1 more point"
    return f"needs {points} or more"


def _needs_more_points(shortfall: Decimal) -> str:
    points = math.ceil(float(shortfall))
    if points <= 1:
        return "needs 1 more point"
    return f"needs {points} more points"


def _room(line: Decimal, total: Decimal) -> Decimal:
    """How many more points a total can take before an under leg breaks: up to the line itself
    on a whole-number line (an exact hit there is a push, not a break), or up to half a point
    short of a fractional line (which cannot push at all)."""
    if line != line.to_integral_value():
        return line - Decimal("0.5") - total
    return line - total


def _total(leg: LegSpec, score: ScoreState) -> str:
    if leg.threshold is None or leg.side not in ("over", "under"):
        return "no rule for this bet"
    total = Decimal(score.home_score + score.away_score)
    line = leg.threshold
    final = _final(score)
    if leg.side == "over":
        if total > line:
            return "already done"
        if total == line:
            return "push, refunded" if final else "push as it stands"
        return "did not happen" if final else _needs_more_points(line - total)
    #: under
    if total < line:
        return "already done" if final else \
            (f"needs the total to stay under {_points(line)}, "
             f"{_points(_room(line, total))} points of room")
    if total == line:
        return "push, refunded" if final else "push as it stands"
    return "did not happen"


def _whole(line: Decimal) -> bool:
    return line == line.to_integral_value()


def _prop_needs(leg: LegSpec, stat: StatState | None) -> str:
    """The live phrase for one prop leg.

    A whole-number line sitting on its number is called a push (review I6), in progress as at
    the final whistle, because this module's contract is that a refund is not a win and a slip
    that reads `reached 225 of 225` about a leg that will refund is lying to its reader.
    `atleast` keeps `reached ...`: `225+` at 225 is a hit, not a push.
    """
    if leg.period != "game":
        #: Release one grades the full game only (D3); a period leg has no rule here.
        return "no rule for this bet"
    if stat is None:
        return "no stat yet"
    if leg.operator == "yes":
        return "scored · provisional until final" if stat.value > 0 else "no touchdown yet"
    line = leg.threshold
    if line is None:
        return "no rule for this bet"
    if leg.operator in ("over", "atleast"):
        if stat.value < line:
            return f"{_points(line - stat.value)} to go"
        if leg.operator == "over" and stat.value == line and _whole(line):
            return "on the line · push as it stands"
        return (f"reached {_points(stat.value)} of {_points(line)} · provisional until final")
    #: under
    if stat.value < line:
        return f"under by {_points(line - stat.value)} · live until the game ends"
    if stat.value == line and _whole(line):
        return "on the line · push as it stands"
    return f"over by {_points(stat.value - line)}"


def _prop_outcome(leg: LegSpec, stat: StatState | None) -> str | None:
    """The final grade of one prop leg, or None while there is nothing to grade.

    `None` on a missing `StatState` and on a non-final one: absence is not a miss (4.4). A
    whole-number line pushes on `over` and `under` and never on `atleast` -- `225+` is a
    different market, not the same market read differently. A leg whose `period` is not the
    full game raises rather than grading off the game-long value (review I4).
    """
    if stat is None or not stat.final:
        return None
    if leg.period != "game":
        #: Release one is the full game only (D3). A period leg graded off the game-long value
        #: would be a wrong money grade with no error, so it raises: `grade_parlays` isolates
        #: the card and counts it in `errors` (review I4).
        raise ValueError(f"cannot grade prop period {leg.period!r}; release one is game only")
    if leg.operator == "yes":
        return "hit" if stat.value > 0 else "miss"
    line = leg.threshold
    if line is None:
        raise ValueError("prop leg has no threshold")
    if leg.operator == "atleast":
        return "hit" if stat.value >= line else "miss"
    if leg.operator == "over":
        if stat.value > line:
            return "hit"
        return "push" if (stat.value == line and _whole(line)) else "miss"
    if leg.operator == "under":
        if stat.value < line:
            return "hit"
        return "push" if (stat.value == line and _whole(line)) else "miss"
    raise ValueError(f"cannot grade prop operator {leg.operator!r}")


_RULES = {"ml": _moneyline, "spread": _spread, "total": _total}


def needs(leg: LegSpec, score: ScoreState | None, stat: StatState | None = None) -> str:
    """One short phrase saying what still has to happen for this leg to hit.

    A prop leg reads its own `StatState` and never the score, so it answers before the score
    question: a player's yards are what the leg is about, and a game with no recorded score yet
    still has a stat line to show (addendum 1.3). `stat` defaults, so the two-argument calls on
    game lines are unchanged.
    """
    if leg.market_type == "prop":
        return _prop_needs(leg, stat)
    if score is None:
        return "no score yet"
    rule = _RULES.get(leg.market_type)
    return rule(leg, score) if rule is not None else "no rule for this bet"


def leg_outcome(leg: LegSpec, score: ScoreState | None,
                stat: StatState | None = None) -> str | None:
    """The settled result of one leg: `"hit"`, `"miss"`, `"push"`, `"void"`, or `None` while
    there is nothing to grade yet (T12 fix round 1, rulings C1/C2/I1).

    Shares `_margin` and the side handling with the sentence functions above, so the grade a
    card settles on and the "needs" text a live card renders can never disagree.
    `parlay_legs.threshold` is DraftKings' own handicap (`-7.0` means that team must win by more
    than seven) and, for a total, `leg.side` says which way the leg points -- neither is the
    convention `harness.settlement.settle.resolve_market` speaks (a Kalshi series market's
    margin-bar threshold, over-only, with no `side` at all), so grading a parlay leg never
    delegates to it.

    `None` means "not settled yet": no score, or a status that has not reached
    `FINAL_STATUSES`, or a `final`/`final_ot` game whose score has not been recorded (`score` is
    only ever `None` or fully populated -- see `score_state`). `postponed`/`canceled` voids on
    status alone, score or not: a void needs no result. A prop leg grades from `stat` instead of
    the score once its game is final (addendum 4.4): a missing `StatState`, or one whose game
    had not finished when it was read, is `None` and stays ungraded -- absence is not a miss,
    and `parlay_grade` counts it `stat_missing`. Raises `ValueError` for a leg grading
    cannot resolve at all -- an unrecognized market type, a spread/total leg with no threshold,
    or a side team in neither side of the game -- data the caller should isolate and count as an
    error, never mistake for "not settled yet".
    """
    if score is None or not _final(score):
        return None
    if score.status in ("postponed", "canceled"):
        return "void"
    if leg.market_type == "prop":
        #: The game is final; the grade is the stat's, and a missing or still-provisional stat
        #: is None, never a miss (addendum 4.4).
        return _prop_outcome(leg, stat)
    if leg.market_type == "ml":
        margin = _margin(leg, score)
        if margin is None:
            raise ValueError(
                f"moneyline leg's side team {leg.side_team_id!r} is in neither side of the game")
        if margin > 0:
            return "hit"
        return "push" if margin == 0 else "miss"
    if leg.market_type == "spread":
        if leg.threshold is None:
            raise ValueError("spread leg has no threshold")
        margin = _margin(leg, score)
        if margin is None:
            raise ValueError(
                f"spread leg's side team {leg.side_team_id!r} is in neither side of the game")
        over = Decimal(margin) + leg.threshold
        if over > 0:
            return "hit"
        return "push" if over == 0 else "miss"
    if leg.market_type == "total":
        if leg.threshold is None:
            raise ValueError("total leg has no threshold")
        if leg.side not in ("over", "under"):
            raise ValueError(f"total leg has no side (over/under), got {leg.side!r}")
        total = Decimal(score.home_score + score.away_score)
        line = leg.threshold
        if leg.side == "over":
            if total > line:
                return "hit"
            return "push" if total == line else "miss"
        #: under
        if total < line:
            return "hit"
        return "push" if total == line else "miss"
    raise ValueError(f"cannot grade market type {leg.market_type!r}")
