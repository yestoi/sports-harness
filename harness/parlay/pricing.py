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
    odds_snapshot_id: int
    dk_decimal: Decimal
    dk_american: int
    point: Decimal | None
    fetched_at: datetime


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
