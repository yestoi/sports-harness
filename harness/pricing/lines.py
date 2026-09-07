from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from harness.db.models import OddsSnapshot

SPREAD_MARKETS = ("spreads", "alternate_spreads")
TOTAL_MARKETS = ("totals", "alternate_totals")


@dataclass(frozen=True)
class LineKey:
    book: str
    market_type: str
    outcome_team_id: int | None
    outcome_side: str | None
    point: Decimal | None


@dataclass(frozen=True)
class Line:
    key: LineKey
    price: Decimal
    last_update: datetime | None
    fetched_at: datetime


def feed_kind(line: Line) -> str:
    """"alternate" for a line quoted on one of the `alternate_*` Odds API markets (fetched on
    the alternates cadence), "featured" for the main-line market (fetched every tick)."""
    return "alternate" if line.key.market_type.startswith("alternate_") else "featured"


def latest_book_lines(session: Session, game_id: int, now: datetime, lookback_s: int = 1200) -> dict[LineKey, Line]:
    since = now - timedelta(seconds=lookback_s)
    cols = (
        OddsSnapshot.book,
        OddsSnapshot.market_type,
        OddsSnapshot.outcome_team_id,
        OddsSnapshot.outcome_side,
        OddsSnapshot.point,
    )
    stmt = (
        select(OddsSnapshot)
        .distinct(*cols)
        .where(OddsSnapshot.game_id == game_id, OddsSnapshot.fetched_at >= since, OddsSnapshot.fetched_at <= now)
        .order_by(*cols, OddsSnapshot.fetched_at.desc())
    )
    result: dict[LineKey, Line] = {}
    for row in session.execute(stmt).scalars():
        key = LineKey(row.book, row.market_type, row.outcome_team_id, row.outcome_side, row.point)
        result[key] = Line(key, row.price_decimal, row.book_last_update, row.fetched_at)
    return result


def _books(lines: dict[LineKey, Line]) -> set[str]:
    return {k.book for k in lines}


def spread_pair(
    lines: dict[LineKey, Line], team_id: int, opp_id: int, threshold: Decimal,
    markets: tuple[str, ...] = SPREAD_MARKETS,
) -> dict[str, tuple[Line, Line]]:
    pairs: dict[str, tuple[Line, Line]] = {}
    for book in _books(lines):
        for market in markets:
            team_key = LineKey(book, market, team_id, None, -threshold)
            opp_key = LineKey(book, market, opp_id, None, threshold)
            if team_key in lines and opp_key in lines:
                pairs[book] = (lines[team_key], lines[opp_key])
                break
    return pairs


def total_pair(
    lines: dict[LineKey, Line], threshold: Decimal, markets: tuple[str, ...] = TOTAL_MARKETS,
) -> dict[str, tuple[Line, Line]]:
    pairs: dict[str, tuple[Line, Line]] = {}
    for book in _books(lines):
        for market in markets:
            over_key = LineKey(book, market, None, "over", threshold)
            under_key = LineKey(book, market, None, "under", threshold)
            if over_key in lines and under_key in lines:
                pairs[book] = (lines[over_key], lines[under_key])
                break
    return pairs


def ml_pair(lines: dict[LineKey, Line], team_id: int, opp_id: int) -> dict[str, tuple[Line, Line]]:
    pairs: dict[str, tuple[Line, Line]] = {}
    for book in _books(lines):
        team_key = LineKey(book, "h2h", team_id, None, None)
        opp_key = LineKey(book, "h2h", opp_id, None, None)
        if team_key in lines and opp_key in lines:
            pairs[book] = (lines[team_key], lines[opp_key])
    return pairs
