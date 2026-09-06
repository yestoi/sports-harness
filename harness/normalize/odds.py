from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Game, OddsSnapshot
from harness.matching.teams import resolve_team


@dataclass(frozen=True)
class OddsRow:
    event_id: str
    book: str
    market_type: str
    outcome_name: str
    point: Decimal | None
    price: Decimal
    last_update: datetime | None


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_odds_body(body, sport: str) -> list[OddsRow]:
    events = body if isinstance(body, list) else ([body] if isinstance(body, dict) and "id" in body else [])
    out: list[OddsRow] = []
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        for bk in ev.get("bookmakers", []) or []:
            book, lu = bk.get("key"), _ts(bk.get("last_update"))
            for mk in bk.get("markets", []) or []:
                mtype = mk.get("key")
                for oc in mk.get("outcomes", []) or []:
                    price = _dec(oc.get("price"))
                    if not book or not mtype or price is None or not oc.get("name"):
                        continue
                    out.append(OddsRow(eid, book, mtype, oc["name"], _dec(oc.get("point")), price, lu))
    return out


def upsert_odds_rows(session: Session, sport: str, rows: list[OddsRow], raw_id: int, run_id: int, fetched_at: datetime) -> int:
    games = {g.odds_api_event_id: g.id for g in session.execute(
        select(Game).where(Game.odds_api_event_id.in_({r.event_id for r in rows}))).scalars()}
    inserted = 0
    cache: dict[str, int | None] = {}
    for r in rows:
        gid = games.get(r.event_id)
        if gid is None:
            continue
        team_id, side = None, None
        if r.outcome_name in ("Over", "Under"):
            side = r.outcome_name.lower()
        else:
            if r.outcome_name not in cache:
                cache[r.outcome_name] = resolve_team(session, sport, r.outcome_name, sources=("odds_api", "espn_display"))[0]
            team_id = cache[r.outcome_name]
            if team_id is None:
                continue
        stmt = insert(OddsSnapshot).values(raw_id=raw_id, run_id=run_id, book=r.book, game_id=gid, market_type=r.market_type,
                                           outcome_team_id=team_id, outcome_side=side, point=r.point, price_decimal=r.price,
                                           book_last_update=r.last_update, fetched_at=fetched_at).on_conflict_do_nothing().returning(OddsSnapshot.id)
        inserted += len(session.execute(stmt).fetchall())
    return inserted
