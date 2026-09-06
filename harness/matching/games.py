import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from harness.db.models import Game
from harness.matching.teams import learn_alias, resolve_fuzzy, resolve_team

log = logging.getLogger(__name__)
ODDS_SOURCES = ("odds_api", "espn_display", "espn_location", "espn_short")


@dataclass
class GamesResult:
    created: int = 0
    updated: int = 0
    unresolved: list[str] = field(default_factory=list)


def _resolve_odds_name(session: Session, sport: str, name: str) -> int | None:
    tid, src = resolve_team(session, sport, name, sources=ODDS_SOURCES)
    if tid is not None:
        if src != "odds_api":
            learn_alias(session, sport, "odds_api", name, tid)
        return tid
    tid, ratio = resolve_fuzzy(session, sport, name)
    if tid is not None:
        log.info("fuzzy odds_api alias %r -> %s (%.2f)", name, tid, ratio)
        learn_alias(session, sport, "odds_api", name, tid)
    return tid


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)


def upsert_games_from_odds(session: Session, sport: str, body: list, raw_id: int) -> GamesResult:
    res = GamesResult()
    if not isinstance(body, list):
        return res
    for ev in body:
        try:
            eid, kick = ev["id"], _ts(ev["commence_time"])
            home, away = ev["home_team"], ev["away_team"]
        except (KeyError, ValueError, AttributeError):
            continue
        h = _resolve_odds_name(session, sport, home)
        a = _resolve_odds_name(session, sport, away)
        if h is None:
            res.unresolved.append(home)
        if a is None:
            res.unresolved.append(away)
        if h is None or a is None:
            continue
        g = session.execute(select(Game).where(Game.odds_api_event_id == eid)).scalar_one_or_none()
        if g is None:
            existing = find_game_by_pair(session, sport, h, a, kick.date())
            g = next((x for x in existing if x.odds_api_event_id is None), None)
            if g is None:
                session.add(Game(sport=sport, home_team_id=h, away_team_id=a, kickoff_utc=kick, odds_api_event_id=eid))
                res.created += 1
                continue
            g.odds_api_event_id = eid
        if g.kickoff_utc != kick or g.home_team_id != h or g.away_team_id != a:
            g.kickoff_utc, g.home_team_id, g.away_team_id = kick, h, a
            res.updated += 1
    session.flush()
    return res


def find_game_by_pair(session: Session, sport: str, team_a: int, team_b: int, date_utc: date, tolerance_days: int = 1) -> list[Game]:
    lo = datetime.combine(date_utc - timedelta(days=tolerance_days), datetime.min.time(), tzinfo=timezone.utc)
    hi = datetime.combine(date_utc + timedelta(days=tolerance_days + 1), datetime.min.time(), tzinfo=timezone.utc)
    pair = or_(and_(Game.home_team_id == team_a, Game.away_team_id == team_b),
               and_(Game.home_team_id == team_b, Game.away_team_id == team_a))
    return list(session.execute(select(Game).where(Game.sport == sport, pair, Game.kickoff_utc >= lo, Game.kickoff_utc < hi)
                                .order_by(Game.kickoff_utc)).scalars())
