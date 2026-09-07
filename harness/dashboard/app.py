"""One-page operator dashboard: health, funnel, match report, signals, and a kill switch.

Every query here is read-only and bounded by a time window (24h for most sections, 1h for
the WebSocket/data-quality sections per spec) plus a `LIMIT`, except `/kill` and `/unkill`
which are the only writes in this module. `/healthz` and the page's Health section both call
into `harness.health.compute_health` rather than re-deriving the staleness/error rule.
"""

import hmac
import importlib.resources
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Callable

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.db.models import (FairValue, Game, KillSwitch, MarketGapSnapshot, OddsSnapshot, OrderbookEvent,
                                RawResponse, Run, Signal, StrategyVariant, Team, VenueMarket, VenueQuote, VenueTrade)
from harness.health import compute_health

WINDOW_24H = timedelta(hours=24)
WINDOW_1H = timedelta(hours=1)
SIGNALS_LIMIT = 100
UNMATCHED_LIMIT = 50
REASONS_LIMIT = 10
RUNS_NOTES_LIMIT = 500  # bound on how many recent runs' notes we scan for trade gaps
#: VenueMarket has no `sport` column; a market's sport is inferred from its Kalshi series
#: prefix, same convention `harness match-report` (harness/cli.py) uses, because unmatched
#: markets often have no `game_id` to join through.
SPORT_PREFIXES = {"nfl": "KXNFL", "ncaaf": "KXNCAAF"}

_exit_stack = ExitStack()


def _templates_dir():
    ref = importlib.resources.files("harness.dashboard").joinpath("templates")
    # Entered once per create_dashboard() call and never exited: for a normal (non-zipped)
    # install this resolves to the real on-disk directory with no extraction, so leaving the
    # context open for the life of the process is harmless and keeps the path valid for as
    # long as Jinja2Templates needs to read from it.
    return _exit_stack.enter_context(importlib.resources.as_file(ref))


def _dec(x) -> float | None:
    return float(x) if x is not None else None


def _iso(x) -> str | None:
    return x.isoformat() if x is not None else None


def _kill_switch(session: Session) -> dict:
    row = session.get(KillSwitch, 1)
    if row is None:
        return {"active": False, "reason": "", "set_at": None}
    return {"active": row.active, "reason": row.reason, "set_at": _iso(row.set_at)}


def _health(session: Session, session_factory: sessionmaker, now: datetime) -> dict:
    body, _ = compute_health(session_factory, now)
    last_run_id = session.execute(select(Run.id).order_by(desc(Run.started_at)).limit(1)).scalar_one_or_none()
    return {**body, "run_id": last_run_id}


def _funnel(session: Session, now: datetime) -> dict:
    cutoff = now - WINDOW_24H

    sources = dict(session.execute(
        select(RawResponse.source, func.count())
        .where(RawResponse.fetched_at >= cutoff)
        .group_by(RawResponse.source)
    ).all())

    markets_by_sport = {}
    for sport, prefix in SPORT_PREFIXES.items():
        rows = session.execute(
            select(VenueMarket.match_status, func.count())
            .where(VenueMarket.series_ticker.like(f"{prefix}%"))
            .where(VenueMarket.last_seen_at >= cutoff)
            .group_by(VenueMarket.match_status)
        ).all()
        markets_by_sport[sport] = dict(rows)

    fair_by_source = dict(session.execute(
        select(FairValue.fair_source, func.count())
        .where(FairValue.created_at >= cutoff)
        .group_by(FairValue.fair_source)
    ).all())

    gaps = session.execute(
        select(func.count()).select_from(MarketGapSnapshot).where(MarketGapSnapshot.created_at >= cutoff)
    ).scalar_one()

    variants = session.execute(
        select(StrategyVariant).where(StrategyVariant.active.is_(True)).order_by(StrategyVariant.name)
    ).scalars().all()
    signals_by_variant = {}
    for v in variants:
        counts = dict(session.execute(
            select(Signal.decision, func.count())
            .where(Signal.variant_id == v.variant_id, Signal.created_at >= cutoff, Signal.replay.is_(False))
            .group_by(Signal.decision)
        ).all())
        signals_by_variant[v.name] = {"tier": v.tier, "candidate": counts.get("candidate", 0),
                                       "rejected": counts.get("rejected", 0)}

    return {"sources": sources, "markets_by_sport": markets_by_sport, "fair_by_source": fair_by_source,
            "gaps": gaps, "signals_by_variant": signals_by_variant}


def _match_report(session: Session, now: datetime) -> dict:
    cutoff = now - WINDOW_24H
    report = {}
    for sport, prefix in SPORT_PREFIXES.items():
        counts = dict(session.execute(
            select(VenueMarket.match_status, func.count())
            .where(VenueMarket.series_ticker.like(f"{prefix}%"))
            .where(VenueMarket.last_seen_at >= cutoff)
            .group_by(VenueMarket.match_status)
        ).all())
        total = sum(counts.values())
        matched = counts.get("matched", 0) + counts.get("fuzzy", 0) + counts.get("manual", 0)
        pct = (100.0 * matched / total) if total else None
        reasons = session.execute(
            select(VenueMarket.match_reason, func.count())
            .where(VenueMarket.series_ticker.like(f"{prefix}%"))
            .where(VenueMarket.match_status == "unmatched")
            .where(VenueMarket.last_seen_at >= cutoff)
            .group_by(VenueMarket.match_reason)
            .order_by(desc(func.count()))
            .limit(REASONS_LIMIT)
        ).all()
        report[sport] = {"total": total, "matched_pct": pct, "counts": counts,
                          "top_unmatched_reasons": [{"reason": r or "(none)", "count": n} for r, n in reasons]}
    return report


def _primary_signals(session: Session, now: datetime) -> list[dict]:
    primary = session.execute(
        select(StrategyVariant).where(StrategyVariant.active.is_(True), StrategyVariant.tier == "primary")
    ).scalar_one_or_none()
    if primary is None:
        return []

    rows = session.execute(
        select(Signal, VenueMarket, Game)
        .join(VenueMarket, VenueMarket.id == Signal.venue_market_id)
        .outerjoin(Game, Game.id == VenueMarket.game_id)
        .where(Signal.variant_id == primary.variant_id, Signal.replay.is_(False))
        .order_by(desc(Signal.created_at))
        .limit(SIGNALS_LIMIT)
    ).all()

    team_keys = set()
    for _, _, game in rows:
        if game is not None:
            team_keys.add((game.sport, game.home_team_id))
            team_keys.add((game.sport, game.away_team_id))
    teams: dict[tuple[str, int], Team] = {}
    if team_keys:
        conds = [(Team.sport == sp) & (Team.id == tid) for sp, tid in team_keys]
        teams = {(t.sport, t.id): t for t in session.execute(select(Team).where(or_(*conds))).scalars().all()}

    out = []
    for sig, market, game in rows:
        game_label = None
        if game is not None:
            home = teams.get((game.sport, game.home_team_id))
            away = teams.get((game.sport, game.away_team_id))
            home_label = home.abbreviation if home else str(game.home_team_id)
            away_label = away.abbreviation if away else str(game.away_team_id)
            game_label = f"{away_label} @ {home_label}"
        out.append({
            "time": _iso(sig.created_at),
            "variant": primary.name,
            "game": game_label,
            "contract": market.ticker,
            "fair": _dec(sig.fair_p),
            "fair_source": sig.fair_source,
            "bid": _dec(sig.venue_best_bid),
            "ask": _dec(sig.venue_best_ask),
            "target": _dec(sig.price_target),
            "edge": _dec(sig.edge),
            "decision": sig.decision,
            "reason": sig.rejection_reason,
        })
    return out


def _unmatched_markets(session: Session, now: datetime) -> list[dict]:
    cutoff = now - WINDOW_24H
    latest = (
        select(VenueQuote.venue_market_id, func.max(VenueQuote.fetched_at).label("max_fetched"))
        .where(VenueQuote.fetched_at >= cutoff)
        .group_by(VenueQuote.venue_market_id)
        .subquery()
    )
    rows = session.execute(
        select(VenueMarket.ticker, VenueMarket.match_status, VenueMarket.match_reason, VenueQuote.volume_24h)
        .join(latest, VenueMarket.id == latest.c.venue_market_id)
        .join(VenueQuote, (VenueQuote.venue_market_id == latest.c.venue_market_id)
              & (VenueQuote.fetched_at == latest.c.max_fetched))
        .where(VenueMarket.match_status == "unmatched")
        .order_by(desc(VenueQuote.volume_24h))
        .limit(UNMATCHED_LIMIT)
    ).all()
    return [{"ticker": t, "match_status": ms, "match_reason": mr or "", "volume_24h": _dec(v)}
            for t, ms, mr, v in rows]


def _websocket(session: Session, now: datetime) -> dict:
    cutoff = now - WINDOW_1H
    ob_count = session.execute(
        select(func.count()).select_from(OrderbookEvent).where(OrderbookEvent.ts >= cutoff)
    ).scalar_one()
    last_ob = session.execute(select(func.max(OrderbookEvent.ts)).where(OrderbookEvent.ts >= cutoff)).scalar_one()
    trades_count = session.execute(
        select(func.count()).select_from(VenueTrade)
        .where(VenueTrade.source == "ws", VenueTrade.ts >= cutoff)
    ).scalar_one()
    return {"orderbook_events_1h": ob_count, "ws_trades_1h": trades_count, "last_event_at": _iso(last_ob)}


def _data_quality(session: Session, now: datetime) -> dict:
    cutoff_1h = now - WINDOW_1H
    rows = session.execute(
        select(OddsSnapshot.book, OddsSnapshot.fetched_at, OddsSnapshot.book_last_update)
        .where(OddsSnapshot.fetched_at >= cutoff_1h)
    ).all()
    by_book: dict[str, list[float]] = {}
    for book, fetched_at, book_last_update in rows:
        if book_last_update is None:
            continue
        by_book.setdefault(book, []).append((fetched_at - book_last_update).total_seconds())
    staleness_median_s = {book: median(vals) for book, vals in by_book.items()}

    cutoff_24h = now - WINDOW_24H
    notes_rows = session.execute(
        select(Run.notes).where(Run.started_at >= cutoff_24h).order_by(desc(Run.started_at)).limit(RUNS_NOTES_LIMIT)
    ).scalars().all()
    trade_gaps = []
    for notes in notes_rows:
        trade_gaps.extend((notes or {}).get("trade_gaps", []))

    return {"staleness_median_s": staleness_median_s, "trade_gaps_24h": len(trade_gaps),
            "trade_gaps_sample": trade_gaps[:20]}


def build_summary(session: Session, session_factory: sessionmaker, now: datetime) -> dict:
    return {
        "now": _iso(now),
        "health": _health(session, session_factory, now),
        "kill_switch": _kill_switch(session),
        "funnel": _funnel(session, now),
        "match_report": _match_report(session, now),
        "signals": _primary_signals(session, now),
        "unmatched_markets": _unmatched_markets(session, now),
        "websocket": _websocket(session, now),
        "data_quality": _data_quality(session, now),
    }


def create_dashboard(session_factory: sessionmaker, settings: Settings,
                     clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="harness-dashboard")
    templates = Jinja2Templates(directory=str(_templates_dir()))

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        body, code = compute_health(session_factory, clock())
        response.status_code = code
        return body

    @app.get("/api/summary")
    def api_summary() -> dict:
        now = clock()
        with session_factory() as s:
            return build_summary(s, session_factory, now)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        now = clock()
        with session_factory() as s:
            summary = build_summary(s, session_factory, now)
        return templates.TemplateResponse(request, "index.html", {"summary": summary})

    @app.post("/kill")
    def kill(reason: str = Form(...)) -> dict:
        now = clock()
        with session_factory() as s:
            row = s.get(KillSwitch, 1)
            if row is None:
                row = KillSwitch(id=1, active=True, reason=reason, set_at=now)
                s.add(row)
            else:
                row.active = True
                row.reason = reason
                row.set_at = now
            s.commit()
        return {"active": True, "reason": reason}

    @app.post("/unkill")
    def unkill(x_dashboard_token: str | None = Header(None, alias="X-Dashboard-Token"),
               token: str | None = Form(None)) -> dict:
        # Fail closed: no token file provisioned means /unkill can never succeed, not that it
        # falls back to accepting anything.
        if not settings.dashboard_token_file.exists():
            raise HTTPException(status_code=403, detail="dashboard token not configured")
        try:
            expected = settings.dashboard_token()
        except OSError:
            raise HTTPException(status_code=403, detail="dashboard token not configured")
        supplied = x_dashboard_token or token or ""
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="invalid token")
        now = clock()
        with session_factory() as s:
            row = s.get(KillSwitch, 1)
            if row is None:
                row = KillSwitch(id=1, active=False, reason="", set_at=now)
                s.add(row)
            else:
                row.active = False
                row.set_at = now
            s.commit()
        return {"active": False}

    return app
