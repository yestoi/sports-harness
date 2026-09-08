"""One-page operator dashboard: health, funnel, match report, signals, and a kill switch.

Every query here is read-only and bounded by a time window (24h for most sections, 1h for
the WebSocket/data-quality sections per spec) plus a `LIMIT`, except `/kill` and `/unkill`
which are the only writes in this module. `/healthz` and the page's Health section both call
into `harness.health.compute_health` rather than re-deriving the staleness/error rule.
"""

import hmac
import logging
import re
import importlib.resources
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Callable
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings

from harness.db.models import (ExecHeartbeat, FairValue, Fill, Game, JobRun, KillSwitch, Ledger, MarketGapSnapshot,
                                OddsSnapshot, Order, OrderbookEvent, OrderEvent, RawResponse, Run, Signal,
                                StrategyVariant, Team, VenueMarket, VenueQuote, VenueTrade)
from harness.execution.plan import POST_ONLY_REJECT
from harness.health import compute_health
from harness.pricing.fees import KALSHI_FOOTBALL, fee_model_for
from harness.settlement.settle import stale_unsettled

log = logging.getLogger(__name__)
WINDOW_24H = timedelta(hours=24)
WINDOW_7D = timedelta(days=7)
WINDOW_1H = timedelta(hours=1)
WINDOW_5M = timedelta(minutes=5)
SIGNALS_LIMIT = 100
UNMATCHED_LIMIT = 50
REASONS_LIMIT = 10
RUNS_NOTES_LIMIT = 500  # bound on how many recent runs' notes we scan for trade gaps
#: VenueMarket has no `sport` column; a market's sport is inferred from its Kalshi series
#: prefix, same convention `harness match-report` (harness/cli.py) uses, because unmatched
#: markets often have no `game_id` to join through.
SPORT_PREFIXES = {"nfl": "KXNFL", "ncaaf": "KXNCAAF"}

# --- Task 12: executor, orders/fills, P&L, candidates, skips, database ceiling, data quality --
HEARTBEAT_RED_S = 60  # executor heartbeat age turns red past this many seconds
WS_EVENT_RED_S = 120  # the executor's own ws_last_event_at turns red past this many seconds
OPEN_ORDERS_LIMIT = 100
FILLS_TODAY_LIMIT = 200
EXPOSURE_LIMIT = 500  # positions is already one row per (variant, ticker, side); still bounded
SKIP_REASONS_LIMIT = 20  # more than the number of distinct reasons order_events can carry
JOB_RUNS_SCAN_LIMIT = 30  # a month of daily housekeeping notes, or a few days of hourly ones
DB_CEILING_RED_PCT = 80.0
#: F50: a kill reason is free text an operator types into a form; only this shape survives.
KILL_REASON_RE = re.compile(r"[^\w \-.,:/()]")
KILL_REASON_MAX = 200
#: Sec-Fetch-Site values a same-origin browser POST can carry (a direct navigation or a request
#: with no Sec-Fetch-Site support at all, e.g. curl, sends no header, which is accepted too).
KILL_ALLOWED_SEC_FETCH_SITE = ("same-origin", "none")

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


def _health(session: Session, session_factory: sessionmaker, now: datetime, credits_budget: int) -> dict:
    body, _ = compute_health(session_factory, now, credits_budget)
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
    # `load_variants` enforces at most one active primary at load time, but nothing stops two
    # from existing in the table at once (e.g. a hand-edited row, or a registration race), and
    # `scalar_one_or_none()` would 500 the whole dashboard the moment that happens. Picking the
    # lowest variant_id keeps the page rendering with a deterministic choice instead.
    primary = session.execute(
        select(StrategyVariant)
        .where(StrategyVariant.active.is_(True), StrategyVariant.tier == "primary")
        .order_by(StrategyVariant.variant_id)
        .limit(1)
    ).scalars().first()
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
    """Orderbook events arrive at up to ~4M/hour during live games, so the event count uses a
    5-minute window (BRIN-indexed) and the last event is read by primary key, not by max(ts)."""
    cutoff_5m = now - WINDOW_5M
    cutoff_1h = now - WINDOW_1H
    ob_count = session.execute(
        select(func.count()).select_from(OrderbookEvent).where(OrderbookEvent.ts >= cutoff_5m)
    ).scalar_one()
    last_ob = session.execute(select(OrderbookEvent.ts).order_by(OrderbookEvent.id.desc()).limit(1)).scalar()
    trades_count = session.execute(
        select(func.count()).select_from(VenueTrade)
        .where(VenueTrade.source == "ws", VenueTrade.ts >= cutoff_1h)
    ).scalar_one()
    return {"orderbook_events_5m": ob_count, "ws_trades_1h": trades_count, "last_event_at": _iso(last_ob)}


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
    taker_side_missing = 0
    for notes in notes_rows:
        trade_gaps.extend((notes or {}).get("trade_gaps", []))
        taker_side_missing += (notes or {}).get("taker_side_missing", 0) or 0

    # Fix round 1, I1: the brief's row is a 24h share. A dropped print (no resolvable taker
    # side) never reaches venue_trades at all (harness/normalize/kalshi.py insert_trades), so
    # the numerator can only come from run notes -- already a 24h scan, above. The denominator
    # is widened to the same 24h window rather than the rest of this section's 1h, so both
    # halves of the ratio read from the same clock.
    stored_trades_24h = session.execute(
        select(func.count()).select_from(VenueTrade)
        .where(VenueTrade.source == "rest", VenueTrade.ts >= cutoff_24h)
    ).scalar_one()
    taker_side_total = taker_side_missing + stored_trades_24h
    no_taker_side_share = (taker_side_missing / taker_side_total) if taker_side_total else None

    matched_total, non_linear_cent = session.execute(
        select(func.count(),
              func.count().filter(VenueMarket.price_level_structure.isnot(None)
                                  & (VenueMarket.price_level_structure != "linear_cent")))
        .where(VenueMarket.match_status.in_(("matched", "fuzzy", "manual")), VenueMarket.last_seen_at >= cutoff_1h)
    ).one()
    non_linear_cent_share = (non_linear_cent / matched_total) if matched_total else None

    nonzero_exchange_index = session.execute(
        select(func.count()).select_from(VenueMarket)
        .where(VenueMarket.exchange_index != 0, VenueMarket.last_seen_at >= cutoff_1h)
    ).scalar_one()

    skipped_total, post_only_rejects = session.execute(
        select(func.count(), func.count().filter(OrderEvent.reason == POST_ONLY_REJECT))
        .where(OrderEvent.kind == "skipped", OrderEvent.ts >= cutoff_1h, OrderEvent.replay.is_(False))
    ).one()
    post_only_reject_rate = (post_only_rejects / skipped_total) if skipped_total else None

    return {"staleness_median_s": staleness_median_s, "trade_gaps_24h": len(trade_gaps),
            "trade_gaps_sample": trade_gaps[:20],
            "no_taker_side_share_24h": no_taker_side_share,
            "non_linear_cent_share_1h": non_linear_cent_share,
            "non_linear_cent_count_1h": non_linear_cent,
            "nonzero_exchange_index_1h": nonzero_exchange_index,
            "post_only_reject_rate_1h": post_only_reject_rate,
            "fee_drift": _fee_drift(session)}


def _fee_drift(session: Session) -> dict:
    """The newest stored Kalshi series body's fee shape against `KALSHI_FOOTBALL`, the fee
    model the executor assumes for every series that has never recorded its own (F45/R21):
    `fee_model_for(None, ...)` is `KALSHI_FOOTBALL` exactly, so any recorded shape that resolves
    to a different maker rate, taker rate or multiplier is real drift, not noise."""
    row = session.execute(
        select(RawResponse.body, RawResponse.fetched_at)
        .where(RawResponse.source == "kalshi", RawResponse.endpoint.like("/series/%"), RawResponse.http_status == 200)
        .order_by(desc(RawResponse.fetched_at))
        .limit(1)
    ).first()
    if row is None:
        return {"checked": False}
    body, fetched_at = row
    info = (body or {}).get("series") or {}
    fee_type, raw_multiplier = info.get("fee_type"), info.get("fee_multiplier")
    try:
        model = fee_model_for(fee_type, raw_multiplier)
    except ValueError:
        return {"checked": True, "fetched_at": _iso(fetched_at), "fee_type": fee_type, "drift": True,
                "reason": "unsupported fee_type"}
    drift = (model.maker_rate != KALSHI_FOOTBALL.maker_rate or model.taker_rate != KALSHI_FOOTBALL.taker_rate
            or model.multiplier != KALSHI_FOOTBALL.multiplier)
    return {"checked": True, "fetched_at": _iso(fetched_at), "fee_type": fee_type,
            "maker_rate": _dec(model.maker_rate), "taker_rate": _dec(model.taker_rate),
            "multiplier": _dec(model.multiplier), "drift": drift}


def _local_day_bounds_utc(now: datetime, tz_local: str) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) for `now`'s local calendar day, in UTC."""
    tz = ZoneInfo(tz_local)
    start_local = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _executor(session: Session, now: datetime) -> dict:
    """The paper executor's own heartbeat (`exec_heartbeat`, id=1): a stalled loop and an idle
    one both mean zero recent orders, so the heartbeat age -- not order activity -- is what
    tells them apart."""
    row = session.get(ExecHeartbeat, 1)
    if row is None:
        return {"present": False}
    heartbeat_age_s = (now - row.last_loop_at).total_seconds() if row.last_loop_at else None
    ws_event_age_s = (now - row.ws_last_event_at).total_seconds() if row.ws_last_event_at else None
    return {
        "present": True,
        "loops": row.loops,
        "open_orders": row.open_orders,
        "last_error": row.last_error,
        "last_loop_ms": row.last_loop_ms,
        "p95_loop_ms": row.p95_loop_ms,
        "loops_skipped": row.loops_skipped,
        "book_dirty_markets": row.book_dirty_markets,
        "executor_version": row.executor_version,
        "last_loop_at": _iso(row.last_loop_at),
        "heartbeat_age_s": heartbeat_age_s,
        "heartbeat_red": heartbeat_age_s is not None and heartbeat_age_s > HEARTBEAT_RED_S,
        "ws_last_event_at": _iso(row.ws_last_event_at),
        "ws_last_event_age_s": ws_event_age_s,
        "ws_red": ws_event_age_s is not None and ws_event_age_s > WS_EVENT_RED_S,
    }


def _open_orders(session: Session, now: datetime) -> list[dict]:
    del now  # bounded by status, not by a time window: an open order can be days old (R8)
    rows = session.execute(
        select(Order.id, Order.variant_id, Order.ticker, Order.side, Order.prob, Order.contracts,
              Order.filled_contracts, Order.status, Order.book_source, Order.dirty_minutes, Order.placed_at)
        .where(Order.status.in_(("open", "partially_filled")), Order.replay.is_(False))
        .order_by(desc(Order.placed_at))
        .limit(OPEN_ORDERS_LIMIT)
    ).all()
    return [{"id": r.id, "variant": r.variant_id, "ticker": r.ticker, "side": r.side, "prob": _dec(r.prob),
            "contracts": _dec(r.contracts), "filled_contracts": _dec(r.filled_contracts), "status": r.status,
            "book_source": r.book_source, "dirty_minutes": r.dirty_minutes, "placed_at": _iso(r.placed_at)}
           for r in rows]


def _fills_today(session: Session, now: datetime, tz_local: str) -> list[dict]:
    start, end = _local_day_bounds_utc(now, tz_local)
    rows = session.execute(
        select(Fill.id, Fill.order_id, Fill.prob, Fill.contracts, Fill.fee, Fill.fill_method, Fill.filled_at,
              Order.variant_id, Order.ticker, Order.side)
        .join(Order, Order.id == Fill.order_id)
        .where(Fill.filled_at >= start, Fill.filled_at < end, Fill.fill_method == "queue_model",
              Order.replay.is_(False))
        .order_by(desc(Fill.filled_at))
        .limit(FILLS_TODAY_LIMIT)
    ).all()
    return [{"id": r.id, "order_id": r.order_id, "variant": r.variant_id, "ticker": r.ticker, "side": r.side,
            "prob": _dec(r.prob), "contracts": _dec(r.contracts), "fee": _dec(r.fee), "fill_method": r.fill_method,
            "filled_at": _iso(r.filled_at)} for r in rows]


_EXPOSURE = text(
    "select variant_id, ticker, side, open_contracts, avg_price from positions "
    "order by variant_id, ticker, side limit :limit")


def _pnl(session: Session, now: datetime) -> dict:
    cutoff = now - WINDOW_7D
    rows = session.execute(
        select(Ledger.variant_id, func.sum(Ledger.cash_delta), func.sum(Ledger.fee))
        .where(Ledger.ts >= cutoff, Ledger.replay.is_(False))
        .group_by(Ledger.variant_id)
    ).all()
    by_variant = {vid: {"cash_delta": _dec(cash), "fees": _dec(fee)} for vid, cash, fee in rows}

    exposure_rows = session.execute(_EXPOSURE, {"limit": EXPOSURE_LIMIT}).all()
    exposure = [{"variant": v, "ticker": t, "side": s, "open_contracts": _dec(oc), "avg_price": _dec(ap)}
               for v, t, s, oc, ap in exposure_rows]
    return {"by_variant_7d": by_variant, "exposure": exposure}


def _candidates(session: Session, now: datetime) -> dict:
    cutoff = now - WINDOW_24H
    rows = session.execute(
        select(StrategyVariant.name, Signal.side, func.count())
        .join(StrategyVariant, StrategyVariant.variant_id == Signal.variant_id)
        .where(Signal.decision == "candidate", Signal.created_at >= cutoff, Signal.replay.is_(False))
        .group_by(StrategyVariant.name, Signal.side)
    ).all()
    out: dict[str, dict[str, int]] = {}
    for name, side, n in rows:
        out.setdefault(name, {})[side] = n
    return out


def _skip_reasons(session: Session, now: datetime) -> list[dict]:
    cutoff = now - WINDOW_24H
    rows = session.execute(
        select(OrderEvent.reason, func.count())
        .where(OrderEvent.kind == "skipped", OrderEvent.ts >= cutoff, OrderEvent.replay.is_(False))
        .group_by(OrderEvent.reason)
        .order_by(desc(func.count()))
        .limit(SKIP_REASONS_LIMIT)
    ).all()
    return [{"reason": r or "(none)", "count": n} for r, n in rows]


def _latest_housekeeping_counts(session: Session, now: datetime) -> dict | None:
    """The newest `housekeeping` stage note on `job_runs` that actually ran (not one the
    once-a-day gate skipped), scanning back a bounded number of recent settlement passes."""
    rows = session.execute(
        select(JobRun.notes).where(JobRun.job == "settle").order_by(desc(JobRun.started_at))
        .limit(JOB_RUNS_SCAN_LIMIT)
    ).scalars().all()
    for notes in rows:
        for stage in (notes or {}).get("stages", []):
            if stage.get("name") == "housekeeping" and "size_gb" in (stage.get("counts") or {}):
                return stage["counts"]
    return None


def _db_ceiling(session: Session, now: datetime, db_budget_gb: int) -> dict:
    note = _latest_housekeeping_counts(session, now)
    if note is None:
        return {"measured": False, "db_budget_gb": db_budget_gb, "size_gb": None, "pct_of_budget": None,
                "red": False, "growth_gb_per_day": None, "days_to_ceiling": None, "partial": True,
                "tables_gb": {}}
    size_gb = note.get("size_gb")
    pct = (100.0 * size_gb / db_budget_gb) if size_gb is not None and db_budget_gb else None
    return {"measured": True, "db_budget_gb": db_budget_gb, "size_gb": size_gb, "pct_of_budget": pct,
            "red": pct is not None and pct >= DB_CEILING_RED_PCT,
            "growth_gb_per_day": note.get("growth_gb_per_day"), "days_to_ceiling": note.get("days_to_ceiling"),
            "partial": note.get("partial", True), "tables_gb": note.get("tables_gb", {})}


_SETTLEMENT_MISMATCHES = text("""
    select d.ticker, d.result, v.result
    from venue_settlements d
    join venue_settlements v on v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
    where d.source = 'derived' and d.settled_at >= :cutoff
      and coalesce(d.result, '') <> coalesce(v.result, '')
    order by d.settled_at desc
    limit :limit
""")
MISMATCHES_LIMIT = 50


def _settlement_health(session: Session, now: datetime) -> dict:
    cutoff = now - WINDOW_7D
    rows = session.execute(_SETTLEMENT_MISMATCHES, {"cutoff": cutoff, "limit": MISMATCHES_LIMIT}).all()
    mismatches = [{"ticker": t, "derived": d, "venue": v} for t, d, v in rows]
    return {"mismatches_7d": mismatches, "mismatch_count_7d": len(mismatches),
            "stale_unsettled": stale_unsettled(session, now)}


def _section(session: Session, name: str, fn: Callable[[], dict]) -> dict:
    """One slow or failing section (e.g. a statement timeout) must not take the whole page down."""
    try:
        return fn()
    except SQLAlchemyError as e:  # noqa: BLE001
        log.warning("dashboard section %s failed: %s", name, type(e).__name__)
        session.rollback()
        return {"error": type(e).__name__}


def build_summary(session: Session, session_factory: sessionmaker, now: datetime, credits_budget: int,
                   tz_local: str = "UTC", db_budget_gb: int = 2000, build: dict | None = None) -> dict:
    if build is None:
        build = {"sha": "dev", "time": None}
    return {
        "now": _iso(now),
        "build": build,
        "health": _section(session, "health", lambda: _health(session, session_factory, now, credits_budget)),
        "kill_switch": _section(session, "kill_switch", lambda: _kill_switch(session)),
        "funnel": _section(session, "funnel", lambda: _funnel(session, now)),
        "match_report": _section(session, "match_report", lambda: _match_report(session, now)),
        "signals": _section(session, "signals", lambda: _primary_signals(session, now)),
        "unmatched_markets": _section(session, "unmatched_markets", lambda: _unmatched_markets(session, now)),
        "websocket": _section(session, "websocket", lambda: _websocket(session, now)),
        "data_quality": _section(session, "data_quality", lambda: _data_quality(session, now)),
        # --- Task 12 additions: additive keys only, the block above is the frozen contract ---
        "executor": _section(session, "executor", lambda: _executor(session, now)),
        "open_orders": _section(session, "open_orders", lambda: _open_orders(session, now)),
        "fills_today": _section(session, "fills_today", lambda: _fills_today(session, now, tz_local)),
        "pnl": _section(session, "pnl", lambda: _pnl(session, now)),
        "candidates": _section(session, "candidates", lambda: _candidates(session, now)),
        "skip_reasons": _section(session, "skip_reasons", lambda: _skip_reasons(session, now)),
        "db_ceiling": _section(session, "db_ceiling", lambda: _db_ceiling(session, now, db_budget_gb)),
        "settlement_health": _section(session, "settlement_health", lambda: _settlement_health(session, now)),
    }


def create_dashboard(session_factory: sessionmaker, settings: Settings,
                     clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="harness-dashboard")
    templates = Jinja2Templates(directory=str(_templates_dir()))
    build = {"sha": settings.build_sha, "time": settings.build_time}

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        body, code = compute_health(session_factory, clock(), settings.odds_monthly_credits)
        response.status_code = code
        return {**body, "build": settings.build_sha}

    @app.get("/api/summary")
    def api_summary() -> dict:
        now = clock()
        with session_factory() as s:
            return build_summary(s, session_factory, now, settings.odds_monthly_credits,
                                 settings.tz_local, settings.db_budget_gb, build=build)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        now = clock()
        with session_factory() as s:
            summary = build_summary(s, session_factory, now, settings.odds_monthly_credits,
                                    settings.tz_local, settings.db_budget_gb, build=build)
        return templates.TemplateResponse(request, "index.html", {"summary": summary})

    @app.post("/kill")
    def kill(reason: str = Form(...),
             sec_fetch_site: str | None = Header(None, alias="Sec-Fetch-Site")) -> dict:
        # F50: same-origin only. A browser sends Sec-Fetch-Site on every fetch/form POST; a
        # value other than same-origin or none means the request came from another site's page,
        # not this dashboard's own form. No header at all (curl, an older browser) is accepted,
        # same as /kill always has been -- this is a same-origin check, not an auth check.
        if sec_fetch_site is not None and sec_fetch_site not in KILL_ALLOWED_SEC_FETCH_SITE:
            raise HTTPException(status_code=403, detail="cross-site request rejected")
        clean_reason = KILL_REASON_RE.sub("", reason)[:KILL_REASON_MAX]
        now = clock()
        with session_factory() as s:
            row = s.get(KillSwitch, 1)
            if row is None:
                row = KillSwitch(id=1, active=True, reason=clean_reason, set_at=now)
                s.add(row)
            else:
                row.active = True
                row.reason = clean_reason
                row.set_at = now
            s.commit()
        return {"active": True, "reason": clean_reason}

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
