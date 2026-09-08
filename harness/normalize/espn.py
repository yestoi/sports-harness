import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import Game, GameScoreEvent, Team
from harness.matching.games import find_game_by_pair

log = logging.getLogger(__name__)

_STATUS = {"STATUS_SCHEDULED": "scheduled", "STATUS_IN_PROGRESS": "in_progress", "STATUS_FINAL": "final",
           "STATUS_HALFTIME": "in_progress", "STATUS_END_PERIOD": "in_progress", "STATUS_POSTPONED": "postponed",
           "STATUS_CANCELED": "canceled", "STATUS_DELAYED": "delayed"}

#: The newest `game_score_events` row for one game, to compare a fresh ESPN body against
#: (Task 12b, design spec §3.5). `ts desc, id desc` rather than `id desc` alone: `ts` is this
#: writer's own clock, and a tie only matters within the same instant.
_NEWEST_SCORE_EVENT = text(
    "select status, period, clock, home_score, away_score from game_score_events "
    "where game_id = :game_id order by ts desc, id desc limit 1")


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


def _period(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _maybe_score_event(session: Session, game: Game, status_obj: dict, status: str,
                       hs: int | None, as_: int | None, raw_id: int | None) -> None:
    """Append one `game_score_events` row when anything about the live score changed since
    the game's newest row -- appended, never updated, so the dashboard can show the game's
    whole in-progress timeline, not just its current state.

    `game.id` must already exist by the time this is called (fix round 1, I2): the caller
    flushes a brand-new game *outside* this function's own savepoint, so a database failure in
    here can never roll the game itself back out of existence along with the telemetry row.
    """
    period = _period(status_obj.get("period"))
    clock = status_obj.get("displayClock")
    clock = str(clock)[:8] if clock else None
    current = (status, period, clock, hs, as_)
    prev = session.execute(_NEWEST_SCORE_EVENT, {"game_id": game.id}).first()
    if prev is not None and tuple(prev) == current:
        return
    session.add(GameScoreEvent(game_id=game.id, ts=datetime.now(timezone.utc), status=status,
                               period=period, clock=clock, home_score=hs, away_score=as_,
                               raw_id=raw_id))


def link_espn_scoreboard(session: Session, sport: str, body: dict,
                         raw_id: int | None = None) -> LinkResult:
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
        status_obj = ev.get("status", {}) if isinstance(ev.get("status"), dict) else {}
        raw = status_obj.get("type", {}).get("name", "") if isinstance(status_obj.get("type"), dict) else ""
        status = _STATUS.get(raw, raw.lower() or "scheduled")
        hs, as_ = _score(home.get("score")), _score(away.get("score"))
        if (g.status, g.home_score, g.away_score) != (status, hs, as_):
            g.status, g.home_score, g.away_score = status, hs, as_
            res.status_updates += 1
        if g.kickoff_utc != kick:
            g.kickoff_utc = kick
        if g.id is None:
            # Fix round 1, I2: flushed here, outside the savepoint below, so a telemetry
            # failure's rollback can never undo the game's own creation along with it.
            session.flush()
        try:
            with session.begin_nested():
                _maybe_score_event(session, g, status_obj, status, hs, as_, raw_id)
        except Exception:  # noqa: BLE001 - telemetry never fails the tick this runs inside
            log.exception("game_score_events write failed for %s", eid)
    session.flush()
    return res
