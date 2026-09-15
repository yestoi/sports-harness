"""DraftKings leg prices, out of `odds_snapshots` (addendum 0.5, D14).

**No request is made here.** Props are not fetched at build time in phase 5, so the builder reads
the rows the recorder already stored and spends no Odds credit. `book = 'draftkings'` and nothing
else: a Pinnacle price is the sharp fair's input, not a slip's payout.

**Thirty minutes.** A leg priced off an older row is a leg whose payout arithmetic is fiction,
and the card prints each leg's `fetched_at` so the operator can see how old the number is when
they type it into the app (ruling A-M6).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

BOOK = "draftkings"


@dataclass(frozen=True)
class LegPrice:
    odds_snapshot_id: int | None
    dk_decimal: Decimal
    dk_american: int
    point: Decimal | None
    fetched_at: datetime
    #: Phase 4.6 (addendum 2.2, D23): a prop outcome lives in `odds_prop_snapshots`, whose ids
    #: are their own space. A game line still fills `odds_snapshot_id` and leaves this null; a
    #: prop fills this and leaves that null, and the leg's `market_type` says which table the
    #: id it recorded belongs to. Defaulted, so every existing construction is unchanged.
    odds_prop_snapshot_id: int | None = None
    #: The prop row's validated deep link and the venue's selection id (addendum 3.3); null on
    #: a game line, whose table carries neither column.
    link: str | None = None
    sid: str | None = None


def american(decimal_odds: Decimal) -> int:
    """Decimal odds as American. 2.00 is +100 by convention on both sides of the pick-em."""
    odds = decimal_from(decimal_odds)
    if odds >= 2:
        return int(((odds - 1) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return int((-100 / (odds - 1)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def decimal_from(price_decimal) -> Decimal:
    """A price as a `Decimal`, whatever numeric type it arrived as (a driver-returned `Decimal`,
    a `float`, a plain string). Always through `str()` first: `Decimal(0.1)` is
    `0.1000000000000000055511151231257827021181583404541015625`, and a price column is never a
    binary float's rounding error."""
    return Decimal(str(price_decimal))


_NEWEST = text("""
    select id, price_decimal, point, fetched_at
    from odds_snapshots
    where book = :book and game_id = :game_id and market_type = :market_type
      and outcome_team_id is not distinct from :team_id
      and outcome_side is not distinct from :side
      and fetched_at <= :now
    order by fetched_at desc
    limit 1
""")


def newest_dk_price(session: Session, game_id: int, market_type: str, team_id: int | None,
                    side: str | None, now: datetime, max_age: timedelta) -> LegPrice | None:
    """The newest DraftKings row for one outcome, or None when there is none inside `max_age`."""
    row = session.execute(_NEWEST, {"book": BOOK, "game_id": game_id,
                                    "market_type": market_type, "team_id": team_id,
                                    "side": side, "now": now}).first()
    if row is None or now - row.fetched_at > max_age:
        return None
    price = decimal_from(row.price_decimal)
    if price <= 1:
        return None
    return LegPrice(odds_snapshot_id=row.id, dk_decimal=price, dk_american=american(price),
                    point=row.point, fetched_at=row.fetched_at)


#: Bound: one `(game_id, market_type, player_id, point, side)` and `fetched_at <= :now`, newest
#: row only. Index: `ix_odds_prop_lookup (game_id, market_type, player_id, fetched_at desc)
#: where player_id is not null` -- the three leading columns seek and the fourth orders, so this
#: reads the head of one bounded run on a bulk table rather than walking it.
_NEWEST_PROP = text("""
    select id, price_decimal, point, fetched_at, link, sid
    from odds_prop_snapshots
    where book = :book and game_id = :game_id and market_type = :market_type
      and player_id = :player_id
      and point is not distinct from :point
      and outcome_side is not distinct from :side
      and fetched_at <= :now
    order by fetched_at desc
    limit 1
""")


def newest_dk_prop_price(session: Session, game_id: int, market_type: str, player_id: int,
                         point: Decimal | None, side: str | None, now: datetime,
                         max_age: timedelta) -> LegPrice | None:
    """The newest DraftKings row for one prop outcome, or None inside `max_age`.

    Separate from `newest_dk_price` rather than a branch inside it: a prop outcome is keyed by
    player and line, a game line by team and side, they live in two tables since D23, and the
    two ride different indexes. Making one function serve both would put a `player_id is null`
    predicate on the game-line read.
    """
    row = session.execute(_NEWEST_PROP, {"book": BOOK, "game_id": game_id,
                                         "market_type": market_type, "player_id": player_id,
                                         "point": point, "side": side, "now": now}).first()
    if row is None or now - row.fetched_at > max_age:
        return None
    price = decimal_from(row.price_decimal)
    if price <= 1:
        return None
    return LegPrice(odds_snapshot_id=None, dk_decimal=price, dk_american=american(price),
                    point=row.point, fetched_at=row.fetched_at, odds_prop_snapshot_id=row.id,
                    link=row.link, sid=row.sid)
