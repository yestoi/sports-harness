import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from harness.db.models import Game, Team
from harness.matching.games import find_game_by_pair
from harness.matching.names import normalize_name
from harness.matching.teams import ambiguous_candidates, learn_alias, resolve_fuzzy, resolve_team

KALSHI_SOURCES = ("kalshi_name", "espn_display", "espn_abbr_name", "espn_location", "espn_short")
CODE_SOURCES = ("kalshi_code", "espn_abbr")
_VS = re.compile(r"\s+(?:vs\.?|@)\s+")
_SPREAD_TITLE = re.compile(r"^(?P<team>.+?) wins by over (?P<pts>\d+(?:\.5)?) points\??$", re.I)
_TICKER_CODES = re.compile(r"^[A-Z0-9]+-\d{2}[A-Z]{3}\d{2}([A-Z]+)$")


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


def codes_from_event_ticker(event_ticker: str) -> str | None:
    """The team-code segment of a Kalshi event ticker, e.g. "SFLAR" from
    "KXNFLSPREAD-26SEP10SFLAR" (the part after the "SERIES-DDMMMYY" date). None when the
    ticker isn't shaped that way.
    """
    m = _TICKER_CODES.match(event_ticker or "")
    return m.group(1) if m else None


def _code_splits(codes: str):
    """Every way to split `codes` into two known-length (2-4 char) chunks, in order."""
    for i in range(2, 5):
        j = len(codes) - i
        if 2 <= j <= 4:
            yield codes[:i], codes[i:]


def _accept_by_code(team_id: int | None, candidates: list[int]) -> tuple[int | None, bool]:
    """Whether a code-resolved `team_id` is acceptable for a side, and whether it's then
    safe to learn a kalshi_name alias for that side's raw name (never for a name that
    collided -- a bare city -- only for a name that was a genuine, non-colliding miss).
    """
    if team_id is None:
        return None, False
    if candidates:
        return (team_id, False) if team_id in candidates else (None, False)
    return team_id, True


def _resolve_by_code(session: Session, sport: str, ticker: str, left: str, right: str,
                      lt: int | None, rt: int | None) -> tuple[int | None, int | None, bool, bool] | None:
    """Try to resolve whichever of `lt`/`rt` is still None via the event ticker's code
    segment. The codes are ordinarily concatenated in title order, but a side that
    already resolved by name pins its own code first; a split+ordering is only a
    candidate when every side agrees. Every split point (and both orderings of each) is
    tried and collected; the code path only succeeds when exactly one distinct team-pair
    assignment survives -- if the code string happens to admit more than one valid
    partition (or none), that's not a resolution, it's a new ambiguity, so this returns
    None and match_event falls back to its pre-code-path result.
    """
    codes = codes_from_event_ticker(ticker)
    if not codes:
        return None
    l_cands = None if lt is not None else ambiguous_candidates(session, sport, left)
    r_cands = None if rt is not None else ambiguous_candidates(session, sport, right)
    results: dict[tuple[int, int], tuple[int, int, bool, bool]] = {}
    for code_a, code_b in _code_splits(codes):
        ta, _ = resolve_team(session, sport, code_a, sources=CODE_SOURCES)
        tb, _ = resolve_team(session, sport, code_b, sources=CODE_SOURCES)
        if ta is None or tb is None or ta == tb:
            continue
        for cl, cr in ((ta, tb), (tb, ta)):
            if lt is not None and cl != lt:
                continue
            if rt is not None and cr != rt:
                continue
            new_lt, l_learn = (lt, False) if lt is not None else _accept_by_code(cl, l_cands)
            new_rt, r_learn = (rt, False) if rt is not None else _accept_by_code(cr, r_cands)
            if new_lt is not None and new_rt is not None:
                results[(new_lt, new_rt)] = (new_lt, new_rt, l_learn, r_learn)
    if len(results) != 1:
        return None
    return next(iter(results.values()))


def match_event(session: Session, sport: str, event: dict, event_date: date) -> EventMatch:
    parsed = parse_event_title(event.get("title") or "")
    if parsed is None:
        return EventMatch(None, Decimal("0"), "unparseable title", None, None)
    left, right = parsed
    lt, l_exact, lr = _resolve(session, sport, left)
    rt, r_exact, rr = _resolve(session, sport, right)
    l_learn, r_learn, used_code = l_exact, r_exact, False
    if lt is None or rt is None:
        res = _resolve_by_code(session, sport, event.get("event_ticker") or "", left, right, lt, rt)
        if res is not None:
            new_lt, new_rt, l_learn_code, r_learn_code = res
            if lt is None:
                lt, l_learn = new_lt, l_learn_code
            if rt is None:
                rt, r_learn = new_rt, r_learn_code
            used_code = True
    if lt is None or rt is None:
        missing = left if lt is None else right
        return EventMatch(None, Decimal("0"), f"unresolved: {missing}", lt, rt)
    cands = find_game_by_pair(session, sport, lt, rt, event_date)
    if len(cands) != 1:
        reason = f"ambiguous: {len(cands)} games" if cands else "no game for pair"
        return EventMatch(None, Decimal("0"), reason, lt, rt)
    game = cands[0]
    if used_code:
        if l_learn:
            learn_alias(session, sport, "kalshi_name", left, lt)
        if r_learn:
            learn_alias(session, sport, "kalshi_name", right, rt)
        return EventMatch(game.id, Decimal("1.00"), "pair+date exact (code)", lt, rt)
    if l_exact and r_exact:
        learn_alias(session, sport, "kalshi_name", left, lt)
        learn_alias(session, sport, "kalshi_name", right, rt)
        return EventMatch(game.id, Decimal("1.00"), "pair+date exact", lt, rt)
    return EventMatch(game.id, Decimal("0.80"), f"pair+date fuzzy({min(lr, rr):.2f})", lt, rt)


def compose_match_key(game_id: int | None, market_type: str, side_team_id: int | None,
                      side: str | None, threshold: Decimal | None) -> str:
    """The matched shape as one string (Task 2's `venue_markets.match_key`), rendered exactly
    like `create_schema`'s one-off backfill renders `threshold::text` on a `numeric(6,1)`
    column: an integral value keeps its trailing zero (`3` -> `"3.0"`), not bare `"3"`.

    Both paths must agree byte-for-byte -- a Python-composed key that diverges from the
    backfilled one for the same market would make every open order on that market compare
    unequal to its own venue_market's key and read as unmatched (Task 3b's controller ruling).
    """
    t = "" if threshold is None else str(threshold.quantize(Decimal("0.1")))
    return f"{game_id}:{market_type}:{'' if side_team_id is None else side_team_id}:{side or ''}:{t}"


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
