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

**No Odds credit is spent.** Every price comes from rows the recorder already stored: game lines
from `odds_snapshots`, and, since phase 4.6, player props from `odds_prop_snapshots` (D23). The
one request this module can make is ESPN's free game log for a prop leg's context line
(addendum 4.1), and it fails soft to `no season data yet`.

**A prop carries no edge claim** (D4). It has no signal and no sharp fair, so its `p_at_build`
is the two-sided devig of DraftKings' own prices for the same selection and `p_source` records
that; a one-sided price with no pair inside the age limit is `p_source = 'none'` and is left out
of the combined chance rather than assigned a number the harness invented. A family whose
DraftKings settlement rule is not recorded in `parlay.yaml`'s `market_defs` is
`market_unsupported` and is never built (D19).
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ParlayCard, ParlayLeg
from harness.normalize.players import candidates_for_game, match_player, parse_gamelog
from harness.parlay.config import load_config
from harness.parlay.pricing import (BOOK, LegPrice, american, decimal_from,
                                    newest_dk_price)
from harness.research.spend import chicago_day

log = logging.getLogger(__name__)


def resolve_iso_week(now: datetime, week: int | None) -> tuple[int, int]:
    """The Chicago ISO `(year, week)` a card built at `now` belongs to (fix round 2, I4).

    `parlay_ledger`'s cap and `parlay_cards.week` must agree: `mark_placed`, `show_cards` and
    `parlay_grade._settle_card` all key the $50 cap and the ledger to
    `chicago_day(now).isocalendar()`. A raw `now.isocalendar()` disagrees with that for up to
    five hours a week (19:00-23:59 CT Sunday, where UTC has already rolled to Monday) and near
    the turn of the year the calendar `year` and the ISO `year` can disagree too. `week` is the
    operator's own `--week` override when given; `year` always follows Chicago, since a card is
    never built for a week outside the one it is paid out of.
    """
    iso = chicago_day(now).isocalendar()
    return iso.year, week if week is not None else iso.week

#: The harness's market types, and the two-to-six-character names `parlay_legs.market_type` holds.
_MARKET_NAMES = {"moneyline": "ml", "spread": "spread", "total": "total"}


#: Every reason a build can refuse, by code (addendum 2.2, 2.4). The builder raises the middle
#: five; `week_at_cap` is the settle stage's own refusal before it calls this module at all, and
#: `anchor_bye` is a sport-week with no anchor game to build on. The stage records whichever
#: code it is given in `job_state`, so the surface can name the reason instead of showing a slot
#: that silently produced nothing.
REASON_CODES = ("no_anchor_priced", "anchor_bye", "no_props_fresh", "player_unmatched",
                "market_unsupported", "stale_price", "week_at_cap")


class BuildRefused(RuntimeError):
    """A build that produced no card, with the reason recorded by code, never silently skipped.

    `reason_code` is one of `REASON_CODES`. The disqualifiers are reported rather than swallowed
    because the ticket surface's "no idea" sentence is written from them (addendum 1.2, 2.4): a
    slot with no card has to say *why* it has none.
    """

    def __init__(self, reason_code: str, message: str | None = None):
        super().__init__(message or reason_code)
        self.reason_code = reason_code


class NoAnchorPriced(BuildRefused):
    """No LSU or Saints outcome has a DraftKings price inside the freshness window (D14).

    A subclass of `BuildRefused` so `harness/cli.py`'s existing `except NoAnchorPriced` path --
    which turns it into exit 2 and the operator's `no anchor priced` line -- is unchanged.
    """

    def __init__(self, message: str = "no anchor priced"):
        super().__init__("no_anchor_priced", message)


#: The fixed relationship table of addendum 2.2, keyed by the sorted stat pair. A note is
#: written only when both legs name the **same team** (and, for `rush_yds` + `anytime_td`, the
#: same player): a quarterback and the opposing receiver do not move together, and a pair that
#: is not in this table gets no note at all rather than an invented one (B-C6).
RELATIONSHIP_NOTES = {
    ("pass_yds", "rec_yds"): "both move on a {qb} completion to {wr}",
    ("anytime_td", "pass_yds"): "a touchdown pass moves the yards, not the scorer prop",
    ("anytime_td", "rush_yds"): "a rushing score moves both",
}

#: The stat families ESPN's game log can answer for a context line (addendum 4.1). A family
#: outside this set -- `anytime_td` -- has no season line in the log, so its context reads
#: `no season data yet` and no request is made for it at all.
_GAMELOG_FAMILIES = ("pass_yds", "rush_yds", "rec_yds", "receptions")
#: The design's bound on one build's free ESPN game-log fetches (addendum 4.1).
MAX_GAMELOG_FETCHES = 40

#: The fan-facing name of each prop family, for `plain_text`.
_STAT_NAMES = {"pass_yds": "passing yards", "rush_yds": "rushing yards",
               "rec_yds": "receiving yards", "receptions": "receptions",
               "anytime_td": "to score a touchdown"}
#: The other side of one prop selection: the side a devig needs.
_OPPOSITE = {"over": "under", "under": "over", "yes": "no", "no": "yes"}
#: Which side is built when no side has a probability to rank it by: the side a fan slip means.
_SIDE_PREFERENCE = ("yes", "over", "under", "no")


def _relationship_note(first, second) -> str | None:
    """The note for two legs of one game, or None (addendum 2.2, B-C6).

    Each argument is a mapping carrying `stat`, `team_id`, `player_id` and `player_name`. The
    pair must be in `RELATIONSHIP_NOTES` **and** name the same team; `rush_yds` + `anytime_td`
    must also name the same player, because it is one player's rushing score that moves both.
    """
    stats = tuple(sorted((first.get("stat") or "", second.get("stat") or "")))
    note = RELATIONSHIP_NOTES.get(stats)
    if note is None:
        return None
    if first.get("team_id") is None or first.get("team_id") != second.get("team_id"):
        return None
    if stats == ("anytime_td", "rush_yds") and first.get("player_id") != second.get("player_id"):
        return None
    by_stat = {first.get("stat"): first, second.get("stat"): second}
    return note.format(qb=(by_stat.get("pass_yds") or {}).get("player_name", "the quarterback"),
                       wr=(by_stat.get("rec_yds") or {}).get("player_name", "the receiver"))


def _devig(over: Decimal, under: Decimal) -> Decimal:
    """One side's share of the book's own two-sided prices, quantized to 0.0001 (D4).

    `(1/over) / (1/over + 1/under)`: the two implied probabilities sum to more than one by the
    book's margin, and this is the first side's share of that sum. It is the only probability a
    prop leg can carry honestly -- there is no sharp prop price to compare against -- and it is
    recorded as `book_devig` so no reader mistakes it for a fair value.
    """
    implied_first = Decimal("1") / over
    implied_second = Decimal("1") / under
    return (implied_first / (implied_first + implied_second)).quantize(Decimal("0.0001"))


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


#: Bound: one sport's games kicking off inside `prop_window_hours`, DraftKings only, prop rows
#: only. Index: `ix_odds_prop_lookup` on `odds_prop_snapshots` (D23), driven from `games` by the
#: kickoff window. The `distinct on` takes the newest row per outcome, the shape `_POOL` already
#: uses for lines.
#:
#: `:since` is the **prop window**, not `leg_max_age_minutes`: a selection whose newest price is
#: older than the age limit has to be *seen* for `stale_price` to be recorded rather than
#: silently vanishing from the pool (addendum 2.2's disqualifiers are "recorded, not silent").
#: The freshness rule itself is applied to each newest row below, where the disqualifier is
#: counted.
_PROP_POOL = text("""
    select distinct on (o.game_id, o.market_type, o.player_id, o.point, o.outcome_side)
           o.id, o.game_id, o.market_type, o.player_id, o.player_name, o.point,
           o.outcome_side, o.price_decimal, o.fetched_at, o.link, o.sid,
           g.home_team_id, g.away_team_id, p.name as player_display, p.espn_id as player_espn_id,
           p.team_id as player_team_id
    from odds_prop_snapshots o
    join games g on g.id = o.game_id
    left join players p on p.id = o.player_id
    where o.book = :book and o.market_type like 'prop:%'
      and o.player_id is not null
      and o.fetched_at >= :since and o.fetched_at <= :now
      and g.sport = :sport
      and g.kickoff_utc > :now and g.kickoff_utc <= :window_end
    order by o.game_id, o.market_type, o.player_id, o.point, o.outcome_side, o.fetched_at desc
""")


def _family_of(market_type: str) -> str:
    """`prop:rec_yds:alt` -> `rec_yds`: the family a market key belongs to (D22)."""
    base = market_type[5:] if market_type.startswith("prop:") else market_type
    return base[:-4] if base.endswith(":alt") else base


def _prop_selections(session, config, sport: str, now: datetime, max_age: timedelta,
                     reasons: dict) -> list[dict]:
    """The buildable prop selections, newest price per outcome, with every disqualifier counted.

    One pool read, then pure work: the rows are grouped by `(game, market, player, line)` so a
    two-sided pair can be devigged, one selection is kept per group (the side with the higher
    devigged chance, which is the side a fan slip means), and each kept selection is checked
    against the three disqualifiers of addendum 2.2 in the order `parlay.yaml` lists them --
    unmatched player, unsupported family, stale price. A disqualified selection is counted in
    `reasons` and dropped; it is never built and never silently forgotten.
    """
    props = config.props
    rows = session.execute(_PROP_POOL, {
        "book": BOOK, "sport": sport, "now": now,
        "since": now - timedelta(hours=props.prop_window_hours),
        "window_end": now + timedelta(hours=props.prop_window_hours)}).all()

    groups: dict[tuple, dict[str, object]] = {}
    for row in rows:
        key = (row.game_id, row.market_type, row.player_id, row.point)
        groups.setdefault(key, {})[row.outcome_side or ""] = row

    # The main-line pairs an alternate line can devig against (B-I8), by (game, family,
    # player): an alternate with no pair of its own borrows the main line's.
    mains: dict[tuple, list[tuple]] = {}
    for (game_id, market_type, player_id, point), sides in groups.items():
        if market_type.endswith(":alt"):
            continue
        mains.setdefault((game_id, _family_of(market_type), player_id), []).append((point, sides))

    rosters: dict[int, list[tuple[int, str]]] = {}
    selections: list[dict] = []
    for (game_id, market_type, player_id, point), sides in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or 0)):
        priced = {side: row for side, row in sides.items() if row is not None}
        if not priced:
            continue
        chances = {side: _selection_chance(side, row, priced, mains, game_id, market_type,
                                           player_id, point, now, max_age)
                   for side, row in priced.items()}
        side = _preferred_side(chances)
        row = priced[side]
        chance, source = chances[side]
        family = _family_of(market_type)

        if game_id not in rosters:
            rosters[game_id] = candidates_for_game(session, sport, game_id)
        if row.player_display is None or match_player(row.player_name,
                                                      rosters[game_id]) != row.player_id:
            # D14: exactly one rostered candidate, or the outcome is unmatched. A stored
            # `player_id` whose row is gone, or a name two rostered players both fit, is a
            # wrong ticket waiting to happen; an unmatched outcome is only a leg not built.
            reasons["player_unmatched"] = reasons.get("player_unmatched", 0) + 1
            continue
        if family not in props.families or family not in props.market_defs:
            # D19: no recorded DraftKings settlement rule, no leg. The harness will not put a
            # selection on a slip when it cannot state what settles it.
            reasons["market_unsupported"] = reasons.get("market_unsupported", 0) + 1
            continue
        if now - row.fetched_at > max_age:
            reasons["stale_price"] = reasons.get("stale_price", 0) + 1
            continue

        price = _prop_leg_price(row)
        if price is None:                 # a price at or below 1.0 pays nothing; not a leg
            continue
        selections.append({
            "kind": "prop", "game_id": game_id, "market_type": market_type, "family": family,
            "player_id": player_id, "player_name": row.player_display or row.player_name,
            "team_id": row.player_team_id, "espn_id": row.player_espn_id, "point": point,
            "side": side, "operator": _operator_for(market_type, side), "price": price,
            "p": chance, "p_source": source, "is_anchor": False,
        })
    return selections


def _prop_leg_price(row) -> LegPrice | None:
    """The pool row as a `LegPrice`, without a second read.

    `_PROP_POOL`'s `distinct on ... order by fetched_at desc` already returned the newest row
    for this outcome -- the same row `newest_dk_prop_price` would fetch -- so pricing from it
    costs nothing where re-reading would be one index seek per candidate selection on every
    build. `newest_dk_prop_price` stays the interface for the callers that hold one selection
    and no pool: the reprice and the placement check (T8, T9).
    """
    price = decimal_from(row.price_decimal)
    if price <= 1:
        return None
    return LegPrice(odds_snapshot_id=None, dk_decimal=price, dk_american=american(price),
                    point=row.point, fetched_at=row.fetched_at, odds_prop_snapshot_id=row.id,
                    link=row.link, sid=row.sid)


def _selection_chance(side, row, priced, mains, game_id, market_type, player_id, point, now,
                      max_age) -> tuple[Decimal | None, str]:
    """One side's `(p_at_build, p_source)`: the two-sided devig, the matching main line's pair
    for an alternate, or `(None, 'none')` (addendum 2.2, B-I8)."""
    other = priced.get(_OPPOSITE.get(side, ""))
    if (other is not None and now - row.fetched_at <= max_age
            and now - other.fetched_at <= max_age):
        return _devig(decimal_from(row.price_decimal), decimal_from(other.price_decimal)), \
            "book_devig"
    if market_type.endswith(":alt"):
        pair = _main_pair(mains, game_id, _family_of(market_type), player_id, point, side, now,
                          max_age)
        if pair is not None:
            return _devig(decimal_from(pair[0].price_decimal),
                          decimal_from(pair[1].price_decimal)), "book_devig"
    # No pair inside the age limit: the leg is built with no probability at all and is left out
    # of the combined chance. A one-sided price carries the book's whole margin, so calling it
    # a probability would be inventing one (B-I8).
    return None, "none"


def _main_pair(mains, game_id, family, player_id, point, side, now, max_age):
    """The main line's two-sided pair an alternate devigs against: the closest line to the
    alternate's own, both sides inside the age limit."""
    fresh = []
    for main_point, sides in mains.get((game_id, family, player_id), []):
        first, second = sides.get(side), sides.get(_OPPOSITE.get(side, ""))
        if first is None or second is None:
            continue
        if now - first.fetched_at > max_age or now - second.fetched_at > max_age:
            continue
        distance = abs((main_point or Decimal("0")) - (point or Decimal("0")))
        fresh.append((distance, main_point or Decimal("0"), first, second))
    if not fresh:
        return None
    fresh.sort(key=lambda item: (item[0], item[1]))
    return fresh[0][2], fresh[0][3]


def _preferred_side(chances: dict) -> str:
    """The one side of a selection that is built: the likelier side when the devig says which,
    otherwise the side a fan slip means (`yes`, then `over`)."""
    sourced = {side: value for side, (value, _source) in chances.items() if value is not None}
    if sourced:
        return max(sorted(sourced), key=lambda side: sourced[side])
    for side in _SIDE_PREFERENCE:
        if side in chances:
            return side
    return sorted(chances)[0]


def _operator_for(market_type: str, side: str | None) -> str:
    """`parlay_legs.operator` (design 5.1): `yes` for a scorer market, `atleast` for an
    alternate yardage line, `over`/`under` for a main line."""
    if _family_of(market_type) == "anytime_td":
        return "yes"
    if market_type.endswith(":alt"):
        return "atleast"
    return side or "over"


def build_card(session: Session, settings, sport: str, week: int, kind: str, now: datetime,
               client=None, year: int | None = None, *, same_game: bool = False,
               parent_card_id: int | None = None, timeout_s: float | None = None,
               espn=None) -> ParlayCard:
    """One proposed card. Raises `BuildRefused` (of which `NoAnchorPriced` is one) when none of
    this shape can be built, with the reason recorded by code.

    `year` is the card's `parlay_cards.year` -- the Chicago ISO year `resolve_iso_week` computed
    (fix round 2, I4), not `now.year`, which disagreed with the ledger and the cap for up to five
    hours a week. Callers that do not pass one get the same Chicago-anchored value by default,
    so a caller that only fixes `week` cannot still leave `year` wrong.

    `same_game` builds the correlated shape of addendum 2.2: the anchor's game only, anchor plus
    2 to 5 legs each a different player or market, `correlated = true`, `combined_kind =
    'calculated'` and **no hold**, because an independence product the book never quotes
    measures nothing (D4). `parent_card_id` links a replacement to the card it replaces.
    `timeout_s` is passed straight through to `write_rationale` for the scheduled stage (B-I9);
    every existing positional call site is unchanged. `espn` is an already-built ESPN client for
    the prop context line; the default builds one only when a leg needs a game log at all.
    """
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
        priced.append({"kind": "line", "row": row, "market": market, "price": price,
                       "is_anchor": is_anchor, "game_id": row.game_id,
                       "p": Decimal(str(row.fair_p)), "p_source": "sharp",
                       "player_id": None, "family": None})

    anchors = [item for item in priced if item["is_anchor"]]
    if not anchors:
        raise NoAnchorPriced("no anchor priced")
    anchors.sort(key=lambda item: item["row"].edge or Decimal("0"), reverse=True)
    chosen = [anchors[0]]

    # Counted disqualifiers, for the dominant reason a refusal reports (addendum 2.4).
    reasons: dict[str, int] = {}
    props = _prop_selections(session, config, sport, now, max_age, reasons)

    if same_game:
        lo, hi = config.props.same_game_min_legs, config.props.same_game_max_legs
    else:
        lo, hi = config.smart_legs if kind == "smart" else config.lottery_legs
    anchor_game = chosen[0]["game_id"]
    lines = sorted((item for item in priced if item is not chosen[0]),
                   key=lambda item: item["row"].edge or Decimal("0"), reverse=True)
    # Game lines first (they carry a sharp fair and an edge), then props by the only number a
    # prop has: its devigged chance. A prop with no pair ranks last but is still buildable.
    ordered = lines + sorted(props, key=lambda item: item["p"] or Decimal("0"), reverse=True)

    used_games = {anchor_game}
    used_keys = {_distinct_key(chosen[0])}
    prop_legs = 0
    for item in ordered:
        if len(chosen) >= hi:
            break
        if same_game and item["game_id"] != anchor_game:
            continue
        if not same_game and kind == "smart" and item["game_id"] in used_games:
            continue
        if item["kind"] == "prop":
            if not same_game and kind == "smart" \
                    and prop_legs >= config.props.max_prop_legs_smart:
                continue
        if _distinct_key(item) in used_keys:
            # `distinct_player_or_market`: one selection per player and market on a card, so a
            # same-game slip cannot carry the same player twice in the same market.
            continue
        chosen.append(item)
        used_games.add(item["game_id"])
        used_keys.add(_distinct_key(item))
        prop_legs += 1 if item["kind"] == "prop" else 0
    if len(chosen) < lo:
        if reasons:
            # The dominant reason (addendum 2.4): the most-counted disqualifier, ties broken by
            # the order `parlay.yaml` lists them in, so the code a refusal reports is a config
            # decision rather than whatever `dict` iteration happened to give.
            order = list(config.props.disqualifiers)
            dominant = min(reasons, key=lambda code: (-reasons[code], order.index(code)
                                                      if code in order else len(order), code))
            raise BuildRefused(
                dominant,
                f"only {len(chosen)} legs for a {kind} card needing {lo}; {dominant} "
                f"disqualified {reasons[dominant]} selection(s)")
        if same_game:
            # A same-game card is a prop card by construction: short of legs with nothing
            # disqualified means the anchor's game had no fresh prop price to build on.
            raise BuildRefused("no_props_fresh",
                               f"only {len(chosen)} legs in the anchor's game, a same-game "
                               f"card needs {lo}")
        raise NoAnchorPriced(f"only {len(chosen)} priced legs, {kind} needs {lo}")

    stake = config.smart_stake if kind == "smart" else config.lottery_stake
    payout = stake
    true_p = Decimal("1")
    sourced = 0
    for item in chosen:
        payout *= item["price"].dk_decimal
        if item["p_source"] != "none":
            true_p *= item["p"]
            sourced += 1
    correlated = same_game or len({item["game_id"] for item in chosen}) < len(chosen)
    if all(item["p_source"] == "sharp" for item in chosen):
        p_source_min = "sharp"
    elif any(item["p_source"] == "book_devig" for item in chosen):
        p_source_min = "book_devig"
    else:
        p_source_min = "none"
    # A hold is the book's edge over *our* probability, so it is only meaningful when every leg
    # carries a sharp fair and the legs are independent. A correlated card gets none at all
    # rather than a number DraftKings will never quote (D4).
    hold = _hold(true_p, payout, stake) if p_source_min == "sharp" and not correlated else None

    card_year = year if year is not None else chicago_day(now).isocalendar().year
    card = ParlayCard(year=card_year, week=week, sport=sport, kind=kind, built_at=now,
                      stake=stake.quantize(Decimal("0.01")),
                      dk_payout_est=payout.quantize(Decimal("0.01")),
                      true_prob_est=(true_p.quantize(Decimal("0.000001")) if sourced else None),
                      hold_est=hold, rationale=None,
                      anchor_leg_id=None, status="proposed", correlated=correlated,
                      policy_version=config.policy_version, parent_card_id=parent_card_id,
                      # D8: `quoted` is defined and never written in release one -- the harness
                      # has no source for DraftKings' own combined price.
                      combined_kind="calculated",
                      dk_combined_american=american(payout / stake), dk_combined_at=now,
                      link_capability=_link_capability(chosen), p_source_min=p_source_min)
    session.add(card)
    session.flush()

    contexts = _context_lines(chosen, settings, sport, espn)
    legs = []
    for seq, item in enumerate(chosen, start=1):
        price = item["price"]
        leg = ParlayLeg(card_id=card.id, seq=seq, game_id=item["game_id"],
                        market_type="prop" if item["kind"] == "prop" else item["market"],
                        side_team_id=None if item["kind"] == "prop"
                        else item["row"].side_team_id,
                        side=item["side"] if item["kind"] == "prop" else item["row"].side,
                        threshold=price.point,
                        dk_american=price.dk_american, dk_decimal=price.dk_decimal,
                        plain_text=_plain_text(item)[:80],
                        # The two snapshot tables keep separate id spaces (D23) and
                        # `parlay_legs` has one column for the row a leg was priced from: a
                        # prop leg records its `odds_prop_snapshots` id here and its
                        # `market_type = 'prop'` says which table to resolve it in.
                        odds_snapshot_id=(price.odds_snapshot_id
                                          if price.odds_snapshot_id is not None
                                          else price.odds_prop_snapshot_id),
                        status="pending", graded_at=None,
                        player_id=item["player_id"], stat=item["family"], period="game",
                        operator=item.get("operator"),
                        market_def=(config.props.market_defs.get(item["family"], "")[:120]
                                    or None) if item["kind"] == "prop" else None,
                        dk_link=price.link, dk_sid=price.sid, offered=True,
                        p_at_build=item["p"] if item["p_source"] != "none" else None,
                        p_source=item["p_source"], context_text=contexts[seq - 1])
        session.add(leg)
        legs.append((leg, item))
    _apply_relationship_notes(legs)
    session.flush()
    card.anchor_leg_id = legs[0][0].id

    from harness.parlay.rationale import write_rationale

    card.rationale = write_rationale(session, settings, card, [leg for leg, _ in legs], now,
                                     client=client, timeout_s=timeout_s)
    session.flush()
    return card


def _distinct_key(item) -> tuple:
    """`distinct_player_or_market` (addendum 2.1): a prop is keyed by its player and family, a
    game line by its own market and side."""
    if item["kind"] == "prop":
        return (item["player_id"], item["family"])
    return (None, item["market"], item["row"].side_team_id, item["row"].side)


def _link_capability(chosen) -> str:
    """The weakest deep link over the card's legs (addendum 2.2).

    `selection` when every leg carries a validated outcome link, `event` when every leg carries
    at least the venue's selection id, else `none`. A game line carries neither today --
    `odds_snapshots` has no link columns -- so a card with one is `none`, which is exactly what
    the confirm sheet needs to know before it offers a deep link at all.
    """
    if all(item["price"].link for item in chosen):
        return "selection"
    if all(item["price"].link or item["price"].sid for item in chosen):
        return "event"
    return "none"


def _context_lines(chosen, settings, sport: str, espn) -> list[str | None]:
    """One context line per leg, in leg order (addendum 1.1).

    A game line restates the sharp probability it was built from; a prop restates its own
    season line from ESPN's game log, or says it has none. The game log is fetched at build
    time, at most `MAX_GAMELOG_FETCHES` times, only for a family the log can answer, and never
    for a family it cannot (`anytime_td` has no season column). Any failure is
    `no season data yet`: a context line is never worth failing a build for.
    """
    lines: list[str | None] = []
    fetched = 0
    for item in chosen:
        if item["kind"] != "prop":
            percent = (item["p"] * 100).quantize(Decimal("1"))
            lines.append(f"sharps say {percent} % at build")
            continue
        log = {}
        if (item["family"] in _GAMELOG_FAMILIES and item.get("espn_id")
                and fetched < MAX_GAMELOG_FETCHES):
            if espn is None:
                espn = _espn_client(settings)
            if espn is not None:
                fetched += 1
                log = _gamelog(espn, sport, item["espn_id"])
        values = log.get(item["family"]) or []
        if not values:
            lines.append("no season data yet")
            continue
        average = (sum(values) / Decimal(len(values))).quantize(Decimal("1"))
        lines.append(f"avg {average} \u00b7 last {values[0]} \u00b7 ESPN"[:80])
    return lines


def _espn_client(settings):
    """The read-only ESPN client for the context line, or None when settings cannot build one.

    Built here rather than taken as a required parameter so every existing call site of
    `build_card` is unchanged; a caller that already has one passes it as `espn=`.
    """
    try:
        from harness.feeds.espn import EspnClient
        from harness.feeds.http import HttpClient

        return EspnClient(HttpClient(settings.http_timeout_s), settings.espn_base_url,
                          settings.espn_gamelog_url)
    except Exception as exc:                        # no client, no context line, no failure
        log.info("parlay build: no espn client for the context line: %r", exc)
        return None


def _gamelog(espn, sport: str, espn_id: str) -> dict:
    """One athlete's game log, or `{}`. Fails soft in every direction (addendum 4.1): the v3
    host is browser-facing, and `no season data yet` is a printable answer where an exception
    inside a build is not."""
    try:
        result = espn.fetch_gamelog(sport, str(espn_id))
        return parse_gamelog(result.body if result else None, str(espn_id))
    except Exception as exc:
        log.info("parlay build: game log unavailable for athlete %s: %r", espn_id, exc)
        return {}


def _apply_relationship_notes(legs) -> None:
    """The fixed table's note for a same-team pair in one game (addendum 2.2, B-C6).

    Appended to the second leg of the pair, so a note appears once per pair and the reader sees
    it beside the leg it explains. Two legs of different teams, or a pair not in the table, get
    nothing at all.
    """
    noted = set()
    for index, (leg, item) in enumerate(legs):
        if item["kind"] != "prop":
            continue
        for other_leg, other_item in legs[:index]:
            if other_item["kind"] != "prop" or other_item["game_id"] != item["game_id"]:
                continue
            note = _relationship_note(
                {"stat": other_item["family"], "team_id": other_item["team_id"],
                 "player_id": other_item["player_id"], "player_name": other_item["player_name"]},
                {"stat": item["family"], "team_id": item["team_id"],
                 "player_id": item["player_id"], "player_name": item["player_name"]})
            if note is None or (other_leg.seq, leg.seq) in noted:
                continue
            noted.add((other_leg.seq, leg.seq))
            leg.context_text = f"{leg.context_text} \u00b7 {note}"[:80] if leg.context_text \
                else note[:80]
            break


def _hold(true_p: Decimal, payout: Decimal, stake: Decimal) -> Decimal:
    """The book's implied hold on this slip: 1 minus (our probability times the payout multiple).
    Positive is the book's edge over our own estimate."""
    multiple = payout / stake
    return (Decimal("1") - true_p * multiple).quantize(Decimal("0.0001"))


def _plain_text(item) -> str:
    """The fan-facing description. Sanitized at write, and sanitized again on the way into a
    payload, because `harness/dashboard/snapshots/ticket.py` is the surface that renders it."""
    from harness.research.text import sanitize_model_text

    if item["kind"] == "prop":
        return sanitize_model_text(_prop_text(item), 80)
    row, market = item["row"], item["market"]
    name = row.abbreviation or "the pick"
    if market == "ml":
        body = f"{name} to win"
    elif market == "spread":
        body = f"{name} {row.threshold:+}" if row.threshold is not None else f"{name} spread"
    else:
        body = f"{(row.side or 'over').title()} {row.threshold}"
    return sanitize_model_text(body, 80)


def _prop_text(item) -> str:
    """A prop leg's own description: the player, what they have to do, and the line.

    The player's name comes from the `players` row the outcome matched (D14), never from the
    venue's spelling of it, so the slip names whoever the harness actually graded.
    """
    name = item["player_name"] or "the player"
    stat = _STAT_NAMES.get(item["family"], item["family"] or "the prop")
    if item["family"] == "anytime_td":
        return f"{name} {stat}"
    point = item["price"].point
    if point is None:
        return f"{name} {stat}"
    if item["operator"] == "atleast":
        return f"{name} {point}+ {stat}"
    return f"{name} {item['side'] or 'over'} {point} {stat}"
