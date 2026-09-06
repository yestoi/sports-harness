from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from harness.db.models import Game, Team
from harness.matching.games import find_game_by_pair

_STATUS = {"STATUS_SCHEDULED": "scheduled", "STATUS_IN_PROGRESS": "in_progress", "STATUS_FINAL": "final",
           "STATUS_HALFTIME": "in_progress", "STATUS_END_PERIOD": "in_progress", "STATUS_POSTPONED": "postponed",
           "STATUS_CANCELED": "canceled", "STATUS_DELAYED": "delayed"}


@dataclass
class LinkResult:
    linked: int = 0
    status_updates: int = 0
    unresolved: list[str] = field(default_factory=list)


def _score(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def link_espn_scoreboard(session: Session, sport: str, body: dict) -> LinkResult:
    res = LinkResult()
    if not isinstance(body, dict):
        return res
    for ev in body.get("events", []):
        try:
            eid = str(ev["id"])
            kick = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
            comps = ev["competitions"][0]["competitors"]
            home = next(c for c in comps if c["homeAway"] == "home")
            away = next(c for c in comps if c["homeAway"] == "away")
            h, a = int(home["team"]["id"]), int(away["team"]["id"])
        except (KeyError, ValueError, IndexError, StopIteration, TypeError):
            continue
        if session.get(Team, (sport, h)) is None or session.get(Team, (sport, a)) is None:
            res.unresolved.append(f"{away.get('team', {}).get('displayName')} at {home.get('team', {}).get('displayName')}")
            continue
        g = session.query(Game).filter_by(espn_event_id=eid).one_or_none()
        if g is None:
            cands = find_game_by_pair(session, sport, h, a, kick.date())
            g = next((x for x in cands if x.espn_event_id is None), None)
            if g is None:
                g = Game(sport=sport, home_team_id=h, away_team_id=a, kickoff_utc=kick)
                session.add(g)
            g.espn_event_id = eid
            res.linked += 1
        status = _STATUS.get(ev.get("status", {}).get("type", {}).get("name", ""), "scheduled")
        hs, as_ = _score(home.get("score")), _score(away.get("score"))
        if (g.status, g.home_score, g.away_score) != (status, hs, as_):
            g.status, g.home_score, g.away_score = status, hs, as_
            res.status_updates += 1
        if g.kickoff_utc != kick:
            g.kickoff_utc = kick
    session.flush()
    return res
