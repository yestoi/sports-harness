from difflib import SequenceMatcher
from pathlib import Path

import yaml
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Team, TeamAlias
from harness.matching.names import normalize_name

PRIORITY = ("kalshi_uuid", "kalshi_name", "odds_api", "espn_display", "espn_abbr_name", "espn_location",
            "espn_short", "espn_abbr", "espn_slug")
# ESPN's NCAAF feed carries D-II/D-III namesakes, so two teams can normalize to the same alias
# key ("troy", "charlotte", "osu"). Rather than let the last seeded team win, the key is parked
# on this sentinel and ignored by resolution, so lookup falls through to a more specific source
# or a manual alias. Manual and learned sources keep plain overwrite semantics.
AMBIGUOUS_TEAM_ID = -1


def _upsert_alias(session: Session, sport: str, source: str, raw_name: str, team_id: int) -> None:
    key = raw_name if source == "kalshi_uuid" else normalize_name(raw_name)
    if not key:
        return
    stmt = insert(TeamAlias).values(sport=sport, source=source, raw_name=key, team_id=team_id)
    keep = (TeamAlias.team_id == stmt.excluded.team_id) | (~TeamAlias.source.like("espn_%"))
    session.execute(stmt.on_conflict_do_update(
        index_elements=["sport", "source", "raw_name"],
        set_={"team_id": case((keep, stmt.excluded.team_id), else_=AMBIGUOUS_TEAM_ID)}))


def seed_teams_from_espn(session: Session, sport: str, body: dict) -> int:
    n = 0
    for entry in body["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        tid = int(t["id"])
        vals = dict(display_name=t["displayName"], location=t.get("location", ""), name=t.get("name", ""),
                    abbreviation=t.get("abbreviation", ""), short_display_name=t.get("shortDisplayName", ""))
        stmt = insert(Team).values(sport=sport, id=tid, **vals)
        session.execute(stmt.on_conflict_do_update(index_elements=["sport", "id"], set_=vals))
        _upsert_alias(session, sport, "espn_display", t["displayName"], tid)
        # Kalshi NFL event titles read "<ABBR> <Nickname>", e.g. "NO Saints", "GB Packers".
        _upsert_alias(session, sport, "espn_abbr_name", f"{t.get('abbreviation', '')} {t.get('name', '')}", tid)
        _upsert_alias(session, sport, "espn_short", t.get("shortDisplayName", ""), tid)
        _upsert_alias(session, sport, "espn_location", t.get("location", ""), tid)
        _upsert_alias(session, sport, "espn_abbr", t.get("abbreviation", ""), tid)
        _upsert_alias(session, sport, "espn_slug", (t.get("slug") or "").replace("-", " "), tid)
        n += 1
    session.flush()
    return n


def load_manual_aliases(session: Session, path: Path) -> int:
    data = yaml.safe_load(Path(path).read_text()) or {}
    n = 0
    for sport, by_source in data.items():
        for source, names in (by_source or {}).items():
            for raw_name, team_id in (names or {}).items():
                _upsert_alias(session, sport, f"manual:{source}", str(raw_name), int(team_id))
                n += 1
    session.flush()
    return n


def learn_alias(session: Session, sport: str, source: str, raw_name: str, team_id: int) -> None:
    _upsert_alias(session, sport, source, raw_name, team_id)


def resolve_team(session: Session, sport: str, raw_name: str, sources: tuple[str, ...] = PRIORITY) -> tuple[int | None, str]:
    key = normalize_name(raw_name)
    ok = TeamAlias.team_id != AMBIGUOUS_TEAM_ID
    rows = list(session.execute(select(TeamAlias).where(TeamAlias.sport == sport, TeamAlias.raw_name == key, ok)).scalars())
    if raw_name and "kalshi_uuid" in sources:
        rows += list(session.execute(select(TeamAlias).where(TeamAlias.sport == sport, TeamAlias.source == "kalshi_uuid",
                                                             TeamAlias.raw_name == raw_name, ok)).scalars())
    by_source = {r.source: r.team_id for r in rows}
    for src in [x for x in by_source if x.startswith("manual:")]:
        return by_source[src], src
    for src in sources:
        if src in by_source:
            return by_source[src], src
    return None, ""


def ambiguous_candidates(session: Session, sport: str, raw_name: str) -> list[int]:
    """Team ids that could plausibly be `raw_name` when its normalized key maps to the
    AMBIGUOUS_TEAM_ID sentinel (a colliding ESPN alias, e.g. "Los Angeles" for both the
    Rams and the Chargers). The TeamAlias row for a collision only remembers the sentinel,
    not who collided, so this reads back from the seeded Team rows instead: any team in
    `sport` whose display name, location, or short display name normalizes to the same
    key. Returns [] when there's no real collision (0 matches: a genuine miss; exactly 1
    match: not ambiguous -- resolve_team would already have found it) -- resolve_team's
    own contract (None on either ambiguity or a miss) is unchanged.
    """
    key = normalize_name(raw_name)
    if not key:
        return []
    teams = session.execute(select(Team).where(Team.sport == sport)).scalars().all()
    hits = [t.id for t in teams
            if key in {normalize_name(t.display_name), normalize_name(t.location), normalize_name(t.short_display_name)}]
    return sorted(hits) if len(hits) > 1 else []


def resolve_fuzzy(session: Session, sport: str, raw_name: str) -> tuple[int | None, float]:
    key = normalize_name(raw_name)
    rows = session.execute(select(TeamAlias).where(TeamAlias.sport == sport, TeamAlias.team_id != AMBIGUOUS_TEAM_ID,
                                                   TeamAlias.source.in_(("espn_display", "espn_location")))).scalars().all()
    scored = sorted(((SequenceMatcher(None, key, a.raw_name).ratio(), a.team_id) for a in rows), reverse=True)
    if not scored:
        return None, 0.0
    best_ratio, best_id = scored[0]
    runner_up = next((r for r, tid in scored[1:] if tid != best_id), 0.0)
    if best_ratio >= 0.90 and runner_up < 0.85:
        return best_id, best_ratio
    return None, best_ratio
