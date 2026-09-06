import logging
import re
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from harness.db.models import NormalizeState, RawResponse
from harness.matching.games import upsert_games_from_odds
from harness.normalize.espn import link_espn_scoreboard
from harness.normalize.kalshi import insert_orderbook, insert_trades, insert_venue_quotes, upsert_venue_markets
from harness.normalize.odds import parse_odds_body, upsert_odds_rows

log = logging.getLogger(__name__)
FAMILIES = ("espn", "odds_featured", "odds_alternates", "kalshi_events", "kalshi_markets", "kalshi_orderbook", "kalshi_trades")
NORMALIZED_TABLES = ("odds_snapshots", "venue_quotes", "orderbook_snapshots", "venue_trades", "venue_markets", "games")
_EVENTS: dict[str, dict] = {}  # event_ticker -> event, refreshed from raw /events bodies
_OB_RE = re.compile(r"^/markets/([^/]+)/orderbook$")


def _sport_from_endpoint(endpoint: str, params: dict) -> str | None:
    if "americanfootball_nfl" in endpoint or endpoint.startswith("/nfl/"):
        return "nfl"
    if "americanfootball_ncaaf" in endpoint or endpoint.startswith("/college-football/"):
        return "ncaaf"
    s = (params or {}).get("series_ticker", "")
    if s.startswith("KXNFL"):
        return "nfl"
    if s.startswith("KXNCAAF"):
        return "ncaaf"
    return None


def _family_filter(family: str):
    src, ep = RawResponse.source, RawResponse.endpoint
    return {
        "espn": src == "espn",
        "odds_featured": (src == "odds_api") & ep.like("/sports/%/odds") & ~ep.like("/sports/%/events/%"),
        "odds_alternates": (src == "odds_api") & ep.like("/sports/%/events/%/odds"),
        "kalshi_events": (src == "kalshi") & (ep == "/events"),
        "kalshi_markets": (src == "kalshi") & (ep == "/markets"),
        "kalshi_orderbook": (src == "kalshi") & ep.like("/markets/%/orderbook"),
        "kalshi_trades": (src == "kalshi") & (ep == "/markets/trades"),
    }[family]


def _load_events_cache(session: Session) -> None:
    rows = session.execute(select(RawResponse).where(_family_filter("kalshi_events"), RawResponse.http_status == 200)
                           .order_by(RawResponse.id.desc()).limit(200)).scalars().all()
    for r in reversed(rows):
        for ev in (r.body or {}).get("events", []) if isinstance(r.body, dict) else []:
            if ev.get("event_ticker"):
                _EVENTS[ev["event_ticker"]] = ev


def _handle(session: Session, family: str, r: RawResponse, ctx: dict) -> None:
    sport = _sport_from_endpoint(r.endpoint, r.params)
    body = r.body
    if family == "espn" and sport:
        link_espn_scoreboard(session, sport, body)
    elif family in ("odds_featured", "odds_alternates") and sport:
        if family == "odds_featured":
            res = upsert_games_from_odds(session, sport, body, r.id)
            ctx.setdefault("unresolved_teams", []).extend(res.unresolved)
        upsert_odds_rows(session, sport, parse_odds_body(body, sport), r.id, r.run_id, r.fetched_at)
    elif family == "kalshi_events":
        for ev in (body or {}).get("events", []) if isinstance(body, dict) else []:
            if ev.get("event_ticker"):
                _EVENTS[ev["event_ticker"]] = ev
    elif family == "kalshi_markets" and sport:
        markets = (body or {}).get("markets", []) if isinstance(body, dict) else []
        upsert_venue_markets(session, sport, markets, _EVENTS, r.id, r.fetched_at)
        insert_venue_quotes(session, markets, r.id, r.run_id, r.fetched_at)
    elif family == "kalshi_orderbook":
        m = _OB_RE.match(r.endpoint)
        if m:
            insert_orderbook(session, m.group(1), body, r.id, r.fetched_at)
    elif family == "kalshi_trades":
        insert_trades(session, body, r.id)


def normalize_new(session: Session, batch: int = 500, ctx: dict | None = None) -> dict[str, int]:
    ctx = ctx if ctx is not None else {}
    if not _EVENTS:
        _load_events_cache(session)
    counts: dict[str, int] = {}
    for family in FAMILIES:
        state = session.get(NormalizeState, family) or NormalizeState(family=family, last_raw_id=0)
        session.add(state)
        rows = session.execute(select(RawResponse).where(_family_filter(family), RawResponse.http_status == 200,
                                                         RawResponse.id > state.last_raw_id)
                               .order_by(RawResponse.id).limit(batch)).scalars().all()
        n = 0
        for r in rows:
            try:
                _handle(session, family, r, ctx)
                n += 1
            except Exception:  # noqa: BLE001
                log.exception("normalize %s raw_id=%s failed", family, r.id)
                ctx.setdefault("normalize_errors", []).append({family: r.id})
            state.last_raw_id = r.id
        session.commit()
        counts[family] = n
    return counts


def reprocess(session: Session, from_raw_id: int = 0, families: list[str] | None = None, truncate: bool = False) -> dict[str, int]:
    if truncate:
        session.execute(text("truncate " + ", ".join(NORMALIZED_TABLES) + " restart identity cascade"))
    for family in families or FAMILIES:
        state = session.get(NormalizeState, family) or NormalizeState(family=family)
        state.last_raw_id = from_raw_id
        session.add(state)
    session.commit()
    _EVENTS.clear()
    total: dict[str, int] = {f: 0 for f in FAMILIES}
    while True:
        counts = normalize_new(session, batch=2000)
        for k, v in counts.items():
            total[k] += v
        if sum(counts.values()) == 0:
            return total
