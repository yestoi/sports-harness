import logging
import re
import time
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from harness.db.models import NormalizeState, RawResponse
from harness.matching.games import upsert_games_from_odds
from harness.normalize.espn import link_espn_scoreboard
from harness.normalize.kalshi import (apply_series_fee, insert_orderbook, insert_trades, insert_venue_quotes,
                                      upsert_venue_markets)
from harness.normalize.odds import parse_odds_body, upsert_odds_rows

log = logging.getLogger(__name__)
FAMILIES = ("espn", "odds_featured", "odds_alternates", "kalshi_events", "kalshi_markets", "kalshi_orderbook",
           "kalshi_trades", "kalshi_series")
# Tables rebuildable in full from raw_responses. venue_trades is deliberately absent: it also
# holds source='ws' rows that exist nowhere in raw_responses, so it is pruned by source instead.
NORMALIZED_TABLES = ("odds_snapshots", "venue_quotes", "orderbook_snapshots", "venue_markets", "games")
_EVENTS: dict[str, dict] = {}  # event_ticker -> event, refreshed from raw /events bodies
_OB_RE = re.compile(r"^/markets/([^/]+)/orderbook$")
_SERIES_RE = re.compile(r"^/series/([^/]+)$")


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
        "kalshi_series": (src == "kalshi") & ep.like("/series/%"),
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
        odds = upsert_odds_rows(session, sport, parse_odds_body(body, sport), r.id, r.run_id, r.fetched_at)
        dropped = ctx.setdefault("odds_dropped", {"unknown_game": 0, "unresolved_team": 0})
        dropped["unknown_game"] += odds.dropped_unknown_game
        dropped["unresolved_team"] += odds.dropped_unresolved_team
    elif family == "kalshi_events":
        for ev in (body or {}).get("events", []) if isinstance(body, dict) else []:
            if ev.get("event_ticker"):
                _EVENTS[ev["event_ticker"]] = ev
    elif family == "kalshi_markets" and sport:
        if (r.params or {}).get("status") == "settled":
            # Recorder._kalshi_settled (F10(b)/R11) stores this page for phase 3's settlement
            # task to read `result` from directly; it must not touch venue_markets or
            # venue_quotes, which would bump last_seen_at and re-derive match_reason for markets
            # that settled days ago (flooding the dashboard's last_seen_at-keyed "in play" views)
            # and would add a venue_quotes row that build_gap_snapshots would price as if live.
            return
        markets = (body or {}).get("markets", []) if isinstance(body, dict) else []
        upsert_venue_markets(session, sport, markets, _EVENTS, r.id, r.fetched_at, ctx)
        insert_venue_quotes(session, markets, r.id, r.run_id, r.fetched_at)
    elif family == "kalshi_orderbook":
        m = _OB_RE.match(r.endpoint)
        if m:
            insert_orderbook(session, m.group(1), body, r.id, r.fetched_at)
    elif family == "kalshi_trades":
        insert_trades(session, body, r.id, ctx)
    elif family == "kalshi_series":
        m = _SERIES_RE.match(r.endpoint)
        if m:
            apply_series_fee(session, m.group(1), body)


def _watermark(session: Session, family: str) -> NormalizeState:
    state = session.get(NormalizeState, family)
    if state is None:
        state = NormalizeState(family=family, last_raw_id=0)
        session.add(state)
        session.flush()
    return state


def _drain_batch(session: Session, family: str, batch: int, ctx: dict,
                 deadline: float | None = None) -> tuple[int, int, bool]:
    """Process one batch of a family. Returns (normalized, fetched, committed).

    Stops early (after at least one row) once ``deadline`` (a ``time.monotonic()`` value) passes,
    committing progress through the last processed row so the next call resumes there.
    """
    state = _watermark(session, family)
    last_committed = state.last_raw_id
    rows = session.execute(select(RawResponse).where(_family_filter(family), RawResponse.http_status == 200,
                                                     RawResponse.id > last_committed)
                           .order_by(RawResponse.id).limit(batch)).scalars().all()
    if not rows:
        return 0, 0, True
    n, last_id, processed = 0, last_committed, 0
    for r in rows:
        if deadline is not None and processed > 0 and time.monotonic() >= deadline:
            break
        processed += 1
        try:
            # One savepoint per row: a database error aborts only this row, leaving the rest
            # of the batch (and the watermark) intact.
            with session.begin_nested():
                _handle(session, family, r, ctx)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.exception("normalize %s raw_id=%s failed", family, r.id)
            ctx.setdefault("normalize_errors", []).append({family: {"raw_id": r.id, "error": repr(e)[:300]}})
        # A poison row is skipped, never retried in a loop.
        last_id = r.id
    _watermark(session, family).last_raw_id = last_id
    try:
        session.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("normalize %s commit failed", family)
        session.rollback()
        ctx.setdefault("normalize_errors", []).append({family: {"commit_error": repr(e)[:300]}})
        # The rollback restored the watermark to the last successfully committed row.
        _watermark(session, family).last_raw_id = last_committed
        session.commit()
        return 0, processed, False
    return n, processed, True


def normalize_new(session: Session, batch: int = 500, ctx: dict | None = None,
                  time_budget_s: float = 30.0) -> dict[str, int]:
    ctx = ctx if ctx is not None else {}
    if not _EVENTS:
        _load_events_cache(session)
    counts: dict[str, int] = {}
    deadline = time.monotonic() + time_budget_s
    for family in FAMILIES:
        n, first = 0, True
        while True:
            # Every family gets at least one (deadline-aware) batch per call so no family starves;
            # after that, stop as soon as the budget is spent and resume next tick.
            if not first and time.monotonic() >= deadline:
                log.warning("normalize %s: time budget %.0fs reached; resuming next tick", family, time_budget_s)
                break
            done, fetched, committed = _drain_batch(session, family, batch, ctx, deadline=deadline)
            first = False
            n += done
            if not committed or fetched < batch:
                break
        counts[family] = n
    return counts


def reprocess(session: Session, from_raw_id: int = 0, families: list[str] | None = None, truncate: bool = False) -> dict[str, int]:
    if truncate:
        session.execute(text("truncate " + ", ".join(NORMALIZED_TABLES) + " restart identity cascade"))
        # WebSocket trades are not rebuildable from raw_responses; only REST rows are.
        session.execute(text("delete from venue_trades where source = 'rest'"))
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
