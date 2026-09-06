import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from harness.db.models import Game, Team
from harness.matching.games import find_game_by_pair
from harness.matching.names import normalize_name
from harness.matching.teams import learn_alias, resolve_fuzzy, resolve_team

KALSHI_SOURCES = ("kalshi_name", "espn_display", "espn_location", "espn_short")
_VS = re.compile(r"\s+(?:vs\.?|@)\s+")
_SPREAD_TITLE = re.compile(r"^(?P<team>.+?) wins by over (?P<pts>\d+(?:\.5)?) points\??$", re.I)


@dataclass(frozen=True)
class MarketClass:
    market_type: str
    threshold: Decimal | None
    side_name: str
    side_kind: str
    team_uuid: str | None


@dataclass(frozen=True)
class EventMatch:
    game_id: int | None
    confidence: Decimal
    reason: str
    left_team_id: int | None
    right_team_id: int | None


def parse_event_title(title: str) -> tuple[str, str] | None:
    title = title or ""
    if ":" in title:
        # Kalshi SPREAD/TOTAL event titles carry a trailing "<team> vs <team>: Spread" /
        # ": Total" label. Strip it, but only when the colon comes after the " vs "/"@"
        # separator (i.e. the part after the colon is not itself another pairing) so a
        # colon that happens to be part of a team name is left untouched.
        left, _, right = title.rpartition(":")
        if left and not _VS.search(right):
            title = left
    parts = _VS.split(title)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


def classify_market(m: dict) -> MarketClass | None:
    series = (m.get("event_ticker") or "").split("-")[0]
    uuid = (m.get("custom_strike") or {}).get("football_team")
    if series.endswith("GAME"):
        return MarketClass("moneyline", None, m.get("yes_sub_title") or "", "team", uuid)
    if series.endswith("SPREAD"):
        mt = _SPREAD_TITLE.match(m.get("title") or "")
        name = mt.group("team") if mt else (m.get("yes_sub_title") or "")
        return MarketClass("spread", _dec(m.get("floor_strike")), name, "team", uuid)
    if series.endswith("TOTAL"):
        return MarketClass("total", _dec(m.get("floor_strike")), "over", "over", None)
    return None


def _resolve(session: Session, sport: str, name: str) -> tuple[int | None, bool, float]:
    tid, _ = resolve_team(session, sport, name, sources=KALSHI_SOURCES)
    if tid is not None:
        return tid, True, 1.0
    tid, ratio = resolve_fuzzy(session, sport, name)
    return tid, False, ratio


def match_event(session: Session, sport: str, event: dict, event_date: date) -> EventMatch:
    parsed = parse_event_title(event.get("title") or "")
    if parsed is None:
        return EventMatch(None, Decimal("0"), "unparseable title", None, None)
    left, right = parsed
    lt, l_exact, lr = _resolve(session, sport, left)
    rt, r_exact, rr = _resolve(session, sport, right)
    if lt is None or rt is None:
        missing = left if lt is None else right
        return EventMatch(None, Decimal("0"), f"unresolved: {missing}", lt, rt)
    cands = find_game_by_pair(session, sport, lt, rt, event_date)
    if len(cands) != 1:
        reason = f"ambiguous: {len(cands)} games" if cands else "no game for pair"
        return EventMatch(None, Decimal("0"), reason, lt, rt)
    game = cands[0]
    if l_exact and r_exact:
        learn_alias(session, sport, "kalshi_name", left, lt)
        learn_alias(session, sport, "kalshi_name", right, rt)
        return EventMatch(game.id, Decimal("1.00"), "pair+date exact", lt, rt)
    return EventMatch(game.id, Decimal("0.80"), f"pair+date fuzzy({min(lr, rr):.2f})", lt, rt)


def side_team_id_for(session: Session, sport: str, mc: MarketClass, game: Game) -> int | None:
    if mc.side_kind != "team":
        return None
    if mc.team_uuid:
        tid, _ = resolve_team(session, sport, mc.team_uuid, sources=("kalshi_uuid",))
        if tid in (game.home_team_id, game.away_team_id):
            learn_alias(session, sport, "kalshi_name", mc.side_name, tid)
            return tid
    key = normalize_name(mc.side_name)
    known, _ = resolve_team(session, sport, mc.side_name, sources=("kalshi_name",))
    hits: list[int] = []
    for tid in (game.home_team_id, game.away_team_id):
        t = session.get(Team, (sport, tid))
        if t is None:
            continue
        names = {normalize_name(x) for x in (t.display_name, t.location, t.short_display_name, t.abbreviation, t.name)}
        prefix = len(key) >= 6 and any(n.startswith(key) for n in names)
        if known == tid or key in names or prefix:
            hits.append(tid)
    if len(hits) != 1:
        return None
    tid = hits[0]
    if mc.team_uuid:
        learn_alias(session, sport, "kalshi_uuid", mc.team_uuid, tid)
    learn_alias(session, sport, "kalshi_name", mc.side_name, tid)
    return tid
