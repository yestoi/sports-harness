"""One-page operator dashboard: health, funnel, match report, signals, and a kill switch.

Every query here is read-only and bounded by a time window (24h for most sections, 1h for
the WebSocket/data-quality sections per spec) plus a `LIMIT`. The writes are `/kill`, `/unkill`
and -- on the LAN listener only (phase 4.6, addendum 5) -- the owner's two parlay write routes.
`/healthz` and the page's Health section both call into `harness.health.compute_health` rather
than re-deriving the staleness/error rule.
"""

import hashlib
import hmac
import json
import logging
import importlib.resources
from collections import deque
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response as StarletteResponse

from harness import telemetry
from harness.config.settings import Settings

from harness.dashboard import auth, snapshots as snap
from harness.dashboard.queries import local_day_bounds_utc, recent_run_notes, signals_by_variant_from_notes
from harness.db.models import (DashboardSnapshot, ExecHeartbeat, Fill, Game, JobRun, KillSwitch, Ledger,
                                MetricSample, OddsSnapshot, Order, OrderbookEvent, OrderEvent, ParlayCard,
                                ParlayPlacement, ParlayPlacementCorrection, RawResponse, Run,
                                Signal, StrategyVariant, Team, VenueMarket, VenueQuote)
from harness.execution.plan import POST_ONLY_REJECT
from harness.health import HEARTBEAT_WATCH_S, WS_EVENT_BROKEN_S, compute_health
from harness.parlay.placement import (FIELD_MAX, VALUE_MAX, BudgetExceeded, CardNotPlaceable,
                                      ConfirmationReused, CorrectionNotAllowed, CorrectionsCapped,
                                      LineMoved, apply_correction, mark_placed)
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
#: VenueMarket has no `sport` column; a market's sport is inferred from its Kalshi series
#: prefix, same convention `harness match-report` (harness/cli.py) uses, because unmatched
#: markets often have no `game_id` to join through.
SPORT_PREFIXES = {"nfl": "KXNFL", "ncaaf": "KXNCAAF"}

# --- Task 12: executor, orders/fills, P&L, candidates, skips, database ceiling, data quality --
# Phase 4.5 (addendum §0.9): the two numbers moved to harness/health.py so this page and the
# Pulse surface cannot disagree about the same machine. The values are unchanged -- this page
# turns the heartbeat red at one minute and the WS event red at two -- and the names stay so the
# template and this module's own tests are untouched.
HEARTBEAT_RED_S = HEARTBEAT_WATCH_S   # 60
WS_EVENT_RED_S = WS_EVENT_BROKEN_S    # 120
OPEN_ORDERS_LIMIT = 100
FILLS_TODAY_LIMIT = 200
EXPOSURE_LIMIT = 500  # positions is already one row per (variant, ticker, side); still bounded
SKIP_REASONS_LIMIT = 20  # more than the number of distinct reasons order_events can carry
JOB_RUNS_SCAN_LIMIT = 30  # a month of daily housekeeping notes, or a few days of hourly ones
DB_CEILING_RED_PCT = 80.0
#: Sec-Fetch-Site values a same-origin browser POST can carry (a direct navigation or a request
#: with no Sec-Fetch-Site support at all, e.g. curl, sends no header, which is accepted too).
KILL_ALLOWED_SEC_FETCH_SITE = ("same-origin", "none")

# --- Phase 4.6 (addendum §6): the LAN listener's session gate ------------------------------
#: What the session check exempts. `/healthz` is the one *data* exemption §6 grants (build sha
#: and health status, bounded contents, and the container's own healthcheck has no cookie to
#: offer); `/login` is the page the gate redirects to, so gating it would lock everyone out.
LAN_EXEMPT_PATHS = frozenset({"/healthz", "/login"})
#: Paths whose refusal is a JSON code rather than a redirect, whatever the client's `Accept`:
#: `/api/*` by prefix, plus these. A browser navigating to `/kill` is not a thing that happens.
LAN_JSON_PATHS = frozenset({"/kill", "/unkill", "/logout"})
#: §5.5's CSRF header. `/logout` is the only write this task adds; Task 11's two routes take
#: the same header.
CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "sports-ui"
SESSION_REQUIRED = {"refusal": "session_required"}

# --- Phase 4.6 (addendum 5.1, 5.2, 5.5): the two owner write routes ------------------------
#: Writes one session may make in a minute. The confirm sheet sends one per submit and a
#: correction is a tap; thirty is a whole evening of an owner's fumbling and still bounds what a
#: page left open on a borrowed phone can do before the session is revoked.
WRITE_LIMIT_PER_MINUTE = 30
WRITE_WINDOW_S = 60
#: How many sessions the write table may hold, bounded for the reason `auth.LoginLimiter`'s
#: address table is: this LAN has one owner and a handful of devices, and an unbounded table is
#: a way to grow the process rather than a way to count.
WRITE_MAX_SESSIONS = 64
#: The most a write body may carry. A confirm body is a few hundred bytes and a correction is
#: less. The cap is applied to the *stream*: `Content-Length` is a claim, not a measurement.
BODY_MAX_BYTES = 4096
WRITE_CONTENT_TYPE = "application/json"
#: `parlay_placements.note` and `parlay_placement_corrections.note` are String(200). A longer
#: note is refused, never truncated (addendum 9).
NOTE_MAX = 200
#: The American price the owner read off their slip, bounded because it reaches an Integer
#: column and is multiplied by the stake.
ODDS_MIN, ODDS_MAX = -100_000, 100_000
#: `parlay_placements.stake_actual` is Numeric(8, 2); the $50 weekly cap refuses long before
#: this, and this is only what keeps a number the column cannot hold out of the transaction.
STAKE_MAX = Decimal("999999.99")
#: A card has a handful of legs and a line is a football number; both bounds are the shape of
#: the data, so a body carrying a thousand legs is refused before a query is planned.
LEG_SEQ_MAX = 50
LEG_POINT_MAX = Decimal("1000")
#: A browser omits the scheme's default port from `Origin`, so the one origin the write routes
#: accept omits it too (review M-3).
HTTPS_DEFAULT_PORT = 443
#: American odds are `<= -100` or `>= +100`. Nothing lies between and `0` is not a price
#: (review M-1).
ODDS_MIN_MAGNITUDE = 100
#: Every refusal the login page can show. Fixed strings, chosen by code: nothing the client
#: submitted is ever rendered back (§6).
LOGIN_REFUSALS = {
    "bad_password": "That password was not accepted.",
    "not_configured": "This listener has no owner password on file, so no one can sign in.",
    "rate_limited": "Too many attempts. Wait a minute and try again.",
}

# Phase 4.5 (addendum §2): the three `runs.notes` helpers moved to harness/dashboard/queries.py
# so the Floor surface's funnel and this page's funnel read them through one implementation. The
# private names stay bound here: this module's own unit tests and call sites use them, and a
# rename would be a behaviour-free diff across a frozen page.
_recent_run_notes = recent_run_notes
_signals_by_variant_from_notes = signals_by_variant_from_notes
_local_day_bounds_utc = local_day_bounds_utc

_exit_stack = ExitStack()


def _templates_dir():
    ref = importlib.resources.files("harness.dashboard").joinpath("templates")
    # Entered once per create_dashboard() call and never exited: for a normal (non-zipped)
    # install this resolves to the real on-disk directory with no extraction, so leaving the
    # context open for the life of the process is harmless and keeps the path valid for as
    # long as Jinja2Templates needs to read from it.
    return _exit_stack.enter_context(importlib.resources.as_file(ref))


def _static_dir():
    """The packaged `static/` directory, resolved exactly as `_templates_dir` resolves
    templates, so a wheel install and a checkout behave the same."""
    ref = importlib.resources.files("harness.dashboard").joinpath("static")
    return _exit_stack.enter_context(importlib.resources.as_file(ref))


class _NoCacheStatic(StaticFiles):
    """Every static response revalidates (ruling A-I11).

    A phone holding a cached `app.mjs` renders it against a new payload shape and fails quietly,
    which is the exact failure mode the snapshot design exists to prevent. `no-cache` is not
    "do not store": the file is still cached, it is just revalidated, which is one cheap 304 per
    asset per load over the tunnel. The shell also shows the payload's `build_sha` beside its
    own and flags a mismatch, so a stale client is visible even if a proxy ignores this.
    """

    def file_response(self, *args, **kwargs) -> StarletteResponse:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _dec(x) -> float | None:
    return float(x) if x is not None else None


def _iso(x) -> str | None:
    return x.isoformat() if x is not None else None


def _if_none_match_hits(header: str | None, etag: str) -> bool:
    """Ruling A-M3: an exact string comparison against `If-None-Match` misses a weak validator
    (`W/"..."`) and a comma-separated list of candidates, both of which a caching proxy or
    client may legitimately send -- and either would otherwise pay the full payload every poll.
    Strip a leading `W/` from each comma-separated candidate before comparing."""
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == etag:
            return True
    return False


def _kill_switch(session: Session) -> dict:
    row = session.get(KillSwitch, 1)
    if row is None:
        return {"active": False, "reason": "", "set_at": None}
    return {"active": row.active, "reason": row.reason, "set_at": _iso(row.set_at)}


def _health(session: Session, session_factory: sessionmaker, now: datetime, credits_budget: int) -> dict:
    body, _ = compute_health(session_factory, now, credits_budget)
    last_run_id = session.execute(select(Run.id).order_by(desc(Run.started_at)).limit(1)).scalar_one_or_none()
    return {**body, "run_id": last_run_id}


def _funnel(session: Session, now: datetime, run_notes_24h: list[dict] | None = None) -> dict:
    """`sources` and `markets_by_sport` still come straight from `raw_responses` and
    `venue_markets`, which stay small. `fair_by_source`, `gaps` and `signals_by_variant` are
    per-run *sums* of `runs.notes->'pricing'` over the trailing 24h (fix 15, journal 44): a
    direct scan of `fair_values`, `market_gap_snapshots` and `signals` took 86-92s against the
    10s bound and blew the dashboard's statement timeout once the season's pricing volume grew,
    and it recurs after every Postgres restart. The rendered keys are unchanged, so the frozen
    `/api/summary` contract holds -- only where the values come from has changed. The scan is
    uncapped (fix round 1, Important 2): a 500-row cap covers only a few hours at the default
    heartbeat, and this section's own docstring -- and the `sources`/`markets_by_sport` keys
    beside it -- promise the full 24h.

    `run_notes_24h`, when given, is the already-fetched `runs.notes` rows for the trailing 24h
    (fix 17): `build_summary` fetches this once and passes it to `_funnel`, `_candidates` and
    `_data_quality` so the page issues one scan of `runs.notes`, not three. Omit it (as the direct
    unit tests below do) and this fetches it itself.
    """
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

    if run_notes_24h is None:
        run_notes_24h = _recent_run_notes(session, cutoff)

    signals_by_variant = _signals_by_variant_from_notes(session, run_notes_24h)
    fair_direct = fair_derived = gaps = 0
    for notes in run_notes_24h:
        pricing = (notes or {}).get("pricing") or {}
        fair_direct += pricing.get("fair_direct", 0) or 0
        fair_derived += pricing.get("fair_derived", 0) or 0
        gaps += pricing.get("gaps", 0) or 0

    return {"sources": sources, "markets_by_sport": markets_by_sport,
            "fair_by_source": {"direct": fair_direct, "derived": fair_derived},
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
    """Fix 25: `venue_quotes` has no index leading with `fetched_at` alone
    (`ix_quotes_market_fetched` leads with `venue_market_id`), so a time-only
    `group_by(venue_market_id) / max(fetched_at)` scan walked the whole table. `venue_markets`
    is small, so this drives from it first and reads `venue_quotes` only by the resulting
    market ids, which the existing index serves directly. A market whose latest quote falls
    inside the window was seen by the same fetch, so filtering on `last_seen_at >= cutoff` here
    is equivalent to the old time filter for every row that reaches the output; a market with
    no quote in the window is dropped either way, same as the old inner join."""
    cutoff = now - WINDOW_24H
    unmatched = session.execute(
        select(VenueMarket.id, VenueMarket.ticker, VenueMarket.match_status, VenueMarket.match_reason)
        .where(VenueMarket.match_status == "unmatched", VenueMarket.last_seen_at >= cutoff)
    ).all()
    if not unmatched:
        return []
    by_id = {mid: (ticker, match_status, match_reason) for mid, ticker, match_status, match_reason in unmatched}

    latest_quote = select(VenueQuote.venue_market_id, VenueQuote.volume_24h).distinct(
        VenueQuote.venue_market_id
    ).where(
        VenueQuote.venue_market_id.in_(by_id.keys()), VenueQuote.fetched_at >= cutoff
    ).order_by(VenueQuote.venue_market_id, VenueQuote.fetched_at.desc())
    quotes = session.execute(latest_quote).all()

    out = []
    for market_id, volume_24h in quotes:
        ticker, match_status, match_reason = by_id[market_id]
        out.append({"ticker": ticker, "match_status": match_status, "match_reason": match_reason or "",
                    "volume_24h": _dec(volume_24h)})
    out.sort(key=lambda r: (r["volume_24h"] is None, -(r["volume_24h"] or 0)))
    return out[:UNMATCHED_LIMIT]


def _websocket(session: Session, now: datetime) -> dict:
    """Orderbook events arrive at up to ~4M/hour during live games, so the event count uses a
    5-minute window (`ix_obe_ts_brin`, a BRIN on `ts`, F19 -- correlated well since
    `orderbook_events` has one writer, the WS sink, so `ts` tracks insertion order) and the
    last event is read by primary key, not by max(ts).

    `ws_trades_1h` ("WebSocket prints in the last hour") no longer counts `venue_trades` live
    (fix 19/dashboard-cold, verify 2026-09-08 09:28 CT: a cold first `/api/summary` after a
    Postgres restart took 21.6s against the 10s page-time bound). `venue_trades` does carry a
    BRIN on `ts` (`ix_trades_ts_brin`), but this predicate also filters on `source = 'ws'`,
    which no index covers, and `venue_trades` has two writers -- the WS sink and the REST
    normalizer backfilling the same ticker's tape (see `VenueTrade`'s docstring) -- so `ts`
    does not track insertion order as tightly as it does on the single-writer
    `orderbook_events`, weakening the BRIN's block-range pruning. Instead this sums the last
    hour of `ws.trades_per_min` metric_samples: one row a minute, written by
    `WsRecorder._write_ws_metrics` (`harness/venues/kalshi/ws.py`) from
    `WsSink.drain_counts()`'s exact per-minute trade tally, read here by `(name, ts desc)`
    (`ix_metric_samples_name_ts`) -- a table with one row per source per minute, not one per
    trade. The result is an approximation, not the exact live count that follows: any minute
    whose sample write failed (ruling 1, telemetry never fails the ws loop) is silently
    undercounted rather than retried, and the partial minute at each edge of the window is
    either fully in or fully out by its sample's own `ts`, not pro-rated -- the same shape of
    approximation `_data_quality`'s `kalshi_trades_normalized` already accepts, for the same
    reason (fix 17 round 1)."""
    cutoff_5m = now - WINDOW_5M
    cutoff_1h = now - WINDOW_1H
    ob_count = session.execute(
        select(func.count()).select_from(OrderbookEvent).where(OrderbookEvent.ts >= cutoff_5m)
    ).scalar_one()
    last_ob = session.execute(select(OrderbookEvent.ts).order_by(OrderbookEvent.id.desc()).limit(1)).scalar()
    trades_1h = session.execute(
        select(func.coalesce(func.sum(MetricSample.value), 0))
        .where(MetricSample.source == "ws", MetricSample.name == "ws.trades_per_min",
              MetricSample.ts >= cutoff_1h)
    ).scalar_one()
    return {"orderbook_events_5m": ob_count, "ws_trades_1h": int(trades_1h), "last_event_at": _iso(last_ob)}


def _data_quality(session: Session, now: datetime, run_notes_24h: list[dict] | None = None) -> dict:
    """`run_notes_24h`, when given, is the same uncapped 24h `runs.notes` list `build_summary`
    already fetched for `_funnel` and `_candidates` (fix 17 round 1): this section used to keep
    its own 500-row-capped fetch, but the `kalshi_trades_normalized` sum below needs the true
    24h window (the cap truncates to about 4.2h at the default heartbeat -- see
    `_recent_run_notes`'s docstring), so it now shares the uncapped list instead, both fixing
    that truncation for `trade_gaps`/`taker_side_missing` too and dropping this section's own
    scan of `runs.notes` down to zero when called from `build_summary`. Omit it (as the direct
    unit tests do) and this fetches its own, uncapped.
    """
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
    if run_notes_24h is None:
        run_notes_24h = _recent_run_notes(session, cutoff_24h)
    trade_gaps = []
    taker_side_missing = 0
    kalshi_trades_normalized = 0
    for notes in run_notes_24h:
        notes = notes or {}
        trade_gaps.extend(notes.get("trade_gaps", []))
        # Fix 17 round 2: a pre-fix-17 row has no `kalshi_trades_normalized` key, so it must
        # feed neither half below -- else it inflates the numerator with no denominator to
        # balance it, reading a false 1.0 right after deploy.
        if "kalshi_trades_normalized" not in notes:
            continue
        taker_side_missing += notes.get("taker_side_missing", 0) or 0
        kalshi_trades_normalized += notes["kalshi_trades_normalized"] or 0

    # Fix round 1, I1: the brief's row is a 24h share. A dropped print (no resolvable taker
    # side) never reaches venue_trades at all (harness/normalize/kalshi.py insert_trades), so
    # the numerator can only come from run notes -- already a 24h scan, above. The denominator
    # is widened to the same 24h window rather than the rest of this section's 1h, so both
    # halves of the ratio read from the same clock.
    #
    # Fix 17 round 1 (roadmap row 17, Important): the denominator used to be a live count over
    # `venue_trades` (`source = 'rest' and ts >= cutoff_24h`); `venue_trades` carries no index
    # on `source`, so even bounded by weekly RANGE partition pruning (F19) that statement could
    # still sequentially scan up to two full weekly partitions inside the 2s statement budget.
    # It is now `kalshi_trades_normalized`, the same per-run sum of REST trade rows
    # `insert_trades` (`harness/normalize/kalshi.py`) writes into `venue_trades`, read from
    # `runs.notes` above instead of the table -- so both halves of the ratio come from the same
    # scan, and this section issues no statement against `venue_trades` at all.
    taker_side_total = taker_side_missing + kalshi_trades_normalized
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


def _executor(session: Session, now: datetime) -> dict:
    """The paper executor's own heartbeat (`exec_heartbeat`, id=1): a stalled loop and an idle
    one both mean zero recent orders, so the heartbeat age -- not order activity -- is what
    tells them apart."""
    row = session.get(ExecHeartbeat, 1)
    if row is None:
        return {"present": False}
    # Clamped at 0.0, not left negative (fix 19 addendum, walkthrough 2026-09-08 09:28 CT): the
    # executor can write a heartbeat between this query's read and the request's `now` (a
    # negative `heartbeat_age_s`), and the exchange's own event timestamps run up to ~7s ahead
    # of the NAS clock (a negative `ws_last_event_age_s`). Both are still fresh, not stale, so
    # 0.0 is the honest floor; the template's "%.0f" turned -0.6 into the misleading "-1".
    heartbeat_age_s = max(0.0, (now - row.last_loop_at).total_seconds()) if row.last_loop_at else None
    ws_event_age_s = max(0.0, (now - row.ws_last_event_at).total_seconds()) if row.ws_last_event_at else None
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


def _candidates(session: Session, now: datetime, run_notes_24h: list[dict] | None = None) -> dict:
    """Per-variant candidate counts over the trailing 24h, from `runs.notes->'pricing'->'signals'`
    -- the same per-run sums `_funnel`'s `signals_by_variant` uses (fix 17, journal 44/48). The
    previous version grouped `signals` by variant and side directly; on the NAS's season-sized
    `signals` table (~3.9M rows/day) that took 43s against the 10s page-time bound. Run notes
    carry no per-side split (`price_and_signal` only tallies candidate/rejected per variant), so
    the by-side breakdown is dropped -- controller ruling, journal 48/roadmap row 17 -- and the
    `sides` key says why instead of silently going empty.

    `run_notes_24h`, when given, is the same list `build_summary` already fetched for `_funnel`,
    so this issues no statement against `signals` at all when called from the summary builder.
    Omit it and this fetches its own (used by the direct unit tests).
    """
    cutoff = now - WINDOW_24H
    if run_notes_24h is None:
        run_notes_24h = _recent_run_notes(session, cutoff)
    signals_by_variant = _signals_by_variant_from_notes(session, run_notes_24h)
    by_variant = {name: {"candidate": agg["candidate"]} for name, agg in signals_by_variant.items()}
    return {"by_variant": by_variant, "sides": "not split: served from run notes (fix 17)"}


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
    """One slow or failing section (e.g. a statement timeout) must not take the whole page down.

    Fix round 1, Minor 4: also catches the shapes a malformed `runs.notes` JSON blob raises
    when a section does Python-level arithmetic over it (`_funnel`, `_data_quality`) --
    `TypeError`/`ValueError`/`AttributeError`/`KeyError` from e.g. a string where a dict was
    expected, or a missing key on an object that isn't a dict at all. Notes are written only by
    our own code, so this is a defensive backstop, not an expected path.
    """
    try:
        return fn()
    except (SQLAlchemyError, TypeError, ValueError, AttributeError, KeyError) as e:  # noqa: BLE001
        log.warning("dashboard section %s failed: %s", name, type(e).__name__)
        session.rollback()
        return {"error": type(e).__name__}


def build_summary(session: Session, session_factory: sessionmaker, now: datetime, credits_budget: int,
                   tz_local: str = "UTC", db_budget_gb: int = 2000, build: dict | None = None) -> dict:
    if build is None:
        build = {"sha": "dev", "time": None}

    # Fix 17 (journal 44/48): `_funnel` and `_candidates` both aggregate `runs.notes` over the
    # trailing 24h; fetch it once here and hand both sections the same list so the page scans
    # `runs.notes` once, not twice. Memoized rather than fetched eagerly so a failure here still
    # degrades only the section(s) that hit it, via `_section`, instead of the whole page -- if
    # `_funnel` fails before caching it, `_candidates` retries the fetch on its own.
    run_notes_24h: list[dict] | None = None

    def _shared_run_notes_24h() -> list[dict]:
        nonlocal run_notes_24h
        if run_notes_24h is None:
            run_notes_24h = _recent_run_notes(session, now - WINDOW_24H)
        return run_notes_24h

    return {
        "now": _iso(now),
        "build": build,
        "health": _section(session, "health", lambda: _health(session, session_factory, now, credits_budget)),
        "kill_switch": _section(session, "kill_switch", lambda: _kill_switch(session)),
        "funnel": _section(session, "funnel", lambda: _funnel(session, now, _shared_run_notes_24h())),
        "match_report": _section(session, "match_report", lambda: _match_report(session, now)),
        "signals": _section(session, "signals", lambda: _primary_signals(session, now)),
        "unmatched_markets": _section(session, "unmatched_markets", lambda: _unmatched_markets(session, now)),
        "websocket": _section(session, "websocket", lambda: _websocket(session, now)),
        "data_quality": _section(session, "data_quality", lambda: _data_quality(session, now, _shared_run_notes_24h())),
        # --- Task 12 additions: additive keys only, the block above is the frozen contract ---
        "executor": _section(session, "executor", lambda: _executor(session, now)),
        "open_orders": _section(session, "open_orders", lambda: _open_orders(session, now)),
        "fills_today": _section(session, "fills_today", lambda: _fills_today(session, now, tz_local)),
        "pnl": _section(session, "pnl", lambda: _pnl(session, now)),
        "candidates": _section(session, "candidates", lambda: _candidates(session, now, _shared_run_notes_24h())),
        "skip_reasons": _section(session, "skip_reasons", lambda: _skip_reasons(session, now)),
        "db_ceiling": _section(session, "db_ceiling", lambda: _db_ceiling(session, now, db_budget_gb)),
        "settlement_health": _section(session, "settlement_health", lambda: _settlement_health(session, now)),
    }


def _install_lan_session(app: FastAPI, templates: Jinja2Templates, settings: Settings,
                         clock: Callable[[], datetime]) -> None:
    """The LAN listener's session gate, its login page and its logout (addendum §6).

    Called only from `create_dashboard(..., lan=True)`, so the loopback app is byte-for-byte
    the app it was before this task: no `/login`, no `/logout`, no middleware, and a kill pair
    that still answers on its token alone (invariant 9).

    Nothing in here passes a password, a hash line, a key or a cookie value to `log`, to `repr`
    or to an exception message. The page renders one of `LOGIN_REFUSALS`' fixed strings and
    never anything the client submitted.
    """
    limiter = auth.LoginLimiter()
    warned_about_the_hash_line = False

    def _session_is_valid(request: Request) -> bool:
        """Fail closed: no *usable* hash line means no session can be valid, which is the same
        rule `/unkill` applies to a missing token file.

        Usable is `valid_hash_line`, not merely "the file had bytes in it" (review IM-2). The
        session key is derived from those bytes, so a file holding anything public -- the
        certificate every LAN client is handed, the likeliest hand-placement mix-up (D7) --
        would otherwise let a client derive the key and forge a cookie, reaching every route
        including the token-less `POST /kill`. §6's fail-closed rule covers the gate, not only
        `POST /login`.
        """
        nonlocal warned_about_the_hash_line
        line = auth.read_hash_line(settings)
        if line is None or not auth.valid_hash_line(line):
            if line is not None and not warned_about_the_hash_line:
                # Once per process, not once per request: a polling browser would otherwise
                # fill the log. The message names the condition and no part of the file.
                warned_about_the_hash_line = True
                log.warning("owner password hash file is not a pinned scrypt line; "
                            "every session on this listener is refused")
            return False
        value = request.cookies.get(auth.COOKIE_NAME)
        if not value:
            return False
        return auth.read_cookie(auth.session_key(line), value, clock())

    def _refuse_with_json(path: str, request: Request) -> bool:
        """`/api/*`, the kill pair and `/logout` always get the JSON refusal; every other path
        is a document navigation and gets the redirect, unless the client explicitly asked for
        JSON and not HTML (a scripted client on a page path)."""
        if path.startswith("/api/") or path in LAN_JSON_PATHS:
            return True
        accept = request.headers.get("accept", "")
        return "application/json" in accept and "text/html" not in accept

    @app.middleware("http")
    async def lan_session_gate(request: Request, call_next):
        path = request.url.path
        if path in LAN_EXEMPT_PATHS or _session_is_valid(request):
            response = await call_next(request)
        elif _refuse_with_json(path, request):
            response = JSONResponse(SESSION_REQUIRED, status_code=401)
        else:
            response = RedirectResponse("/login", status_code=302)
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def _login_page(request: Request, status_code: int = 200, refusal: str | None = None):
        return templates.TemplateResponse(
            request, "login.html",
            {"message": LOGIN_REFUSALS[refusal] if refusal else None},
            status_code=status_code)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return _login_page(request)

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, password: str = Form(...)):
        # The socket's address, never a header: `X-Forwarded-For` is attacker-controlled on a
        # listener with no proxy in front of it, so trusting it would hand out a fresh
        # allowance per request.
        address = request.client.host if request.client else "unknown"
        now = clock()
        if limiter.blocked(address, now):
            return _login_page(request, 429, "rate_limited")
        line = auth.read_hash_line(settings)
        if line is None:
            limiter.record_failure(address, now)
            return _login_page(request, 403, "not_configured")
        try:
            accepted = auth.verify_password(password, line)
        except ValueError:
            # The hash line is not a pinned scrypt line. Fail closed and say nothing about it
            # beyond the condition: the message names no field's value.
            log.warning("owner password hash line is not a pinned scrypt line; login refused")
            limiter.record_failure(address, now)
            return _login_page(request, 403, "not_configured")
        if not accepted:
            limiter.record_failure(address, now)
            return _login_page(request, 403, "bad_password")
        response = RedirectResponse("/ui/", status_code=303)
        response.set_cookie(auth.COOKIE_NAME, auth.issue_cookie(auth.session_key(line), now),
                            max_age=auth.COOKIE_MAX_AGE_S, path="/", secure=True,
                            httponly=True, samesite="strict")
        return response

    @app.post("/logout")
    def logout(request: Request) -> Response:
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"refusal": "csrf_header_required"}, status_code=403)
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(auth.COOKIE_NAME, path="/", secure=True, httponly=True,
                               samesite="strict")
        return response



class _WriteRefused(Exception):
    """One write route's refusal: a code and the fields addendum 5 names, never a message.

    Raised from the request rules and from the exception translation below, and turned into a
    JSON body by the handler `_install_parlay_writes` registers. `HTTPException` would have put
    the text under `detail`, and the rule here is that a refusal the owner's page reads is a
    fixed code plus the figures 5.1 lists -- never a sentence about what went wrong, which is
    how a query, a path or a value gets into a page that is open on a phone.
    """

    def __init__(self, status_code: int, refusal: str, **fields) -> None:
        super().__init__(refusal)          # the code itself: fixed text, never client data
        self.status_code = status_code
        self.body = {"refusal": refusal, **fields}


class _WriteLimiter:
    """Write requests per session and minute, in a bounded table (addendum 5.5).

    Shaped like `auth.LoginLimiter`, which counts failed logins per address: one deque of
    instants per key, pruned to the window on every look, and a dict that is capped so that a
    flood of keys cannot grow it without bound. What differs is what is counted -- every write
    that passes the request rules, refused or not, because the point is to bound how fast a
    session can ask, not how often it is wrong.
    """

    def __init__(self, limit: int = WRITE_LIMIT_PER_MINUTE, window_s: int = WRITE_WINDOW_S,
                 max_sessions: int = WRITE_MAX_SESSIONS) -> None:
        self._limit = limit
        self._window = timedelta(seconds=window_s)
        self._max_sessions = max_sessions
        self._writes: dict[str, deque[datetime]] = {}

    def _prune(self, key: str, now: datetime) -> deque[datetime]:
        writes = self._writes.get(key)
        if writes is None:
            return deque()
        while writes and now - writes[0] > self._window:
            writes.popleft()
        if not writes:
            self._writes.pop(key, None)
        return writes

    def allow(self, key: str, now: datetime) -> bool:
        writes = self._prune(key, now)
        if len(writes) >= self._limit:
            return False
        if key not in self._writes:
            if len(self._writes) >= self._max_sessions:
                self._evict(now)
            self._writes[key] = writes
        writes.append(now)
        return True

    def _evict(self, now: datetime) -> None:
        """Drop every key whose window has emptied; failing that, the least recently active
        one. Either way the dict is back under its cap before the next insert."""
        for key in list(self._writes):
            self._prune(key, now)
        while len(self._writes) >= self._max_sessions:
            oldest = min(self._writes, key=lambda k: self._writes[k][-1])
            self._writes.pop(oldest, None)


class PlacedBody(BaseModel):
    """The body of `POST /api/parlay/placed` (addendum 5.1).

    Every field is typed and bounded here rather than in the route, and an unknown field is
    refused rather than ignored: the sheet and this model are one contract, and a body carrying
    a key nobody reads is a page talking to a listener it does not know. The refusal the client
    sees is `bad_value` and nothing else -- pydantic's own message quotes the submitted value
    back, which is not something this listener says out loud.
    """

    model_config = ConfigDict(extra="forbid")

    card_id: int = Field(ge=1)
    stake: Decimal
    #: The plan calls this `accepted_odds`; `mark_placed` calls it `payout_american`. The wire
    #: name is the sheet's, and the route maps it.
    accepted_odds: int = Field(ge=ODDS_MIN, le=ODDS_MAX)
    confirmation_id: str | None = None
    leg_lines: dict[int, Decimal] | None = None
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @field_validator("stake")
    @classmethod
    def _a_finite_amount(cls, value: Decimal) -> Decimal:
        """`Decimal("nan")` and `Decimal("inf")` parse, and either one in the ledger poisons
        every sum taken over it afterwards -- including the weekly cap."""
        if not value.is_finite() or not Decimal("0") < value <= STAKE_MAX:
            raise ValueError("stake is a positive amount the column can hold")
        return value

    @field_validator("accepted_odds")
    @classmethod
    def _an_american_price(cls, value: int) -> int:
        """American odds are `<= -100` or `>= +100`; `0` and the `-99..99` band are not prices.

        `_payout_from_american` reads `>= 0` as the plus branch, so `0` would record a payout
        equal to the stake and a fat-fingered `45` for `450` would record $36.25 on a $25 slip
        instead of $137.50 -- a wrong figure in the fun-money ledger, quietly (review M-1).
        """
        if abs(value) < ODDS_MIN_MAGNITUDE:
            raise ValueError("an American price is at most -100 or at least +100")
        return value

    @field_validator("confirmation_id")
    @classmethod
    def _a_uuid(cls, value: str | None) -> str | None:
        """The confirm sheet mints `crypto.randomUUID()`; anything else is refused before it
        reaches a String(36) column and the partial unique index over it (5.3)."""
        if value is None:
            return None
        try:
            return str(UUID(str(value)))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("confirmation_id is a UUID") from None

    @field_validator("leg_lines")
    @classmethod
    def _bounded_lines(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        for seq, point in value.items():
            if not 1 <= seq <= LEG_SEQ_MAX:
                raise ValueError("a leg line is keyed by a leg's seq")
            if not point.is_finite() or abs(point) > LEG_POINT_MAX:
                raise ValueError("a leg line is a bounded number")
        return value


class CorrectionBody(BaseModel):
    """The body of `POST /api/parlay/correct` (addendum 5.2).

    The two lengths are `harness.parlay.placement`'s own, imported rather than restated: they
    are the widths of `parlay_placement_corrections.field` and `.new_value`, and a value past
    them is refused here and again there, never truncated.
    """

    model_config = ConfigDict(extra="forbid")

    card_id: int = Field(ge=1)
    field: str = Field(min_length=1, max_length=FIELD_MAX)
    new_value: str = Field(min_length=1, max_length=VALUE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)


def _lan_session_is_valid(settings: Settings, request: Request, now: datetime) -> bool:
    """Whether this request carries a live owner session, by the rule the gate applies.

    The session gate already refuses every `/api/*` request without one, so this is the second
    of two checks rather than the only one: a write route that is reachable only because a
    middleware happens to be installed above it is one edit away from being reachable without
    it. Fail closed exactly as the gate does -- no *usable* hash line means no session can be
    valid -- and nothing here reaches `log`, a `repr` or an exception message.
    """
    line = auth.read_hash_line(settings)
    if line is None or not auth.valid_hash_line(line):
        return False
    value = request.cookies.get(auth.COOKIE_NAME)
    if not value:
        return False
    return auth.read_cookie(auth.session_key(line), value, now)


def _allowed_origin(settings: Settings) -> str:
    """The one origin a write may carry, stated once (review M-3).

    From settings, never from the request: a spoofed `Host:` must not be able to name the origin
    it is then compared against (A-I8). The port is dropped when it is HTTPS's own default,
    because a browser drops it from `Origin` -- with `lan_port = 443` an exact
    `https://<addr>:443` comparison would refuse every write while the page itself still loaded.
    Any other address, port or scheme than the settings' own is a different origin and is
    refused: that is the hostname case the runbook has to carry.
    """
    if settings.lan_port == HTTPS_DEFAULT_PORT:
        return f"https://{settings.lan_addr}"
    return f"https://{settings.lan_addr}:{settings.lan_port}"


def _confirmation_on_another_card(session: Session, card_id: int,
                                  confirmation_id: str | None) -> bool:
    """Whether some *other* card already holds this confirmation id.

    One row at most and it rides `uq_parlay_placement_confirmation`, the partial unique index the
    id is unique under. Asked only on the integrity-error path, to tell the one refusal that has
    a name apart from the ones that do not (review I-1).
    """
    if confirmation_id is None:
        return False
    return session.query(ParlayPlacement).filter(
        ParlayPlacement.confirmation_id == confirmation_id,
        ParlayPlacement.card_id != card_id).first() is not None


def _session_digest(request: Request) -> str:
    """The limiter's key for this session: a digest of the cookie, never the cookie itself.

    A dict key is not a log line, but it is one `repr` away from being one and this table is
    exactly the kind of object a traceback prints.
    """
    return hashlib.sha256(
        request.cookies.get(auth.COOKIE_NAME, "").encode("utf-8", "replace")).hexdigest()


async def _read_capped_body(request: Request) -> bytes:
    """The request body, refused at `BODY_MAX_BYTES` while it is still arriving.

    Streamed on purpose: `Content-Length` is a number the client wrote, so a body claiming ten
    bytes and sending five kilobytes is refused on what it actually sends (addendum 5.5).
    """
    size, chunks = 0, []
    async for chunk in request.stream():
        size += len(chunk)
        if size > BODY_MAX_BYTES:
            raise _WriteRefused(413, "body_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _placement_json(placement: ParlayPlacement | None) -> dict | None:
    """One placement as the confirm sheet reads it, or None when the card has none.

    Money as strings, because a payout that reaches a page as a float is a payout that can be
    displayed as $137.49999999999999. Nothing here is a secret and nothing here is a message.
    """
    if placement is None:
        return None
    return {"card_id": placement.card_id,
            "placed_at": _iso(placement.placed_at),
            "stake_actual": (None if placement.stake_actual is None
                             else str(placement.stake_actual)),
            "dk_payout_actual": (None if placement.dk_payout_actual is None
                                 else str(placement.dk_payout_actual)),
            "dk_odds_actual": placement.dk_odds_actual,
            "confirmation_id": placement.confirmation_id,
            "note": placement.note}


def _correction_json(row: ParlayPlacementCorrection) -> dict:
    return {"card_id": row.card_id, "ts": _iso(row.ts), "field": row.field,
            "old_value": row.old_value, "new_value": row.new_value, "note": row.note}


def _install_parlay_writes(app: FastAPI, session_factory: sessionmaker, settings: Settings,
                           clock: Callable[[], datetime]) -> None:
    """The owner's two write routes (addendum 5.1, 5.2, 5.5; D5).

    Called only from `create_dashboard(..., lan=True)`, so the loopback app is byte-for-byte the
    app it was: no write route, no exception handler, no limiter (invariant 9). Both routes
    record; neither places. Every refusal is a code, the `card_not_placeable` body carries the
    placement that already exists so a retry after a lost response shows the owner what is
    recorded (A-I17), and no body carries a message, a query or a stack trace.

    There is no third route: the confirm sheet's decline is a `status` correction (D5).
    """
    limiter = _WriteLimiter()
    allowed_origin = _allowed_origin(settings)

    @app.exception_handler(_WriteRefused)
    async def _write_refused(_request: Request, exc: _WriteRefused) -> JSONResponse:
        return JSONResponse(exc.body, status_code=exc.status_code)

    async def write_rules(request: Request) -> dict:
        """The request rules both routes share, cheapest refusal first (addendum 5.5).

        The session, the CSRF header, the content type, the origin, the body cap and then the
        limiter -- so a request that fails a header check is never read off the socket, and a
        body over the cap is refused on the bytes it sends rather than on the length it claims.
        The body is parsed here and handed on, so neither route declares a body parameter:
        FastAPI reads and parses a declared body *before* any dependency runs, which would put
        the whole 5 KiB in memory and answer a malformed body with a validation message.
        """
        if not _lan_session_is_valid(settings, request, clock()):
            raise _WriteRefused(401, "session_required")
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise _WriteRefused(403, "forbidden")
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != WRITE_CONTENT_TYPE:
            raise _WriteRefused(403, "forbidden")
        if request.headers.get("origin") != allowed_origin:
            raise _WriteRefused(403, "forbidden")
        # The allowance is spent whatever the read does. A body refused at the cap that the
        # limiter never saw is an unmetered way to make this listener read 4 KiB at a time, over
        # and over, from a page left open on a borrowed phone -- which is the threat the limiter
        # exists for (review M-2). The 413 still wins the response when both apply: the body was
        # refused before the allowance was even looked at.
        try:
            raw = await _read_capped_body(request)
        finally:
            spent = limiter.allow(_session_digest(request), clock())
        if not spent:
            raise _WriteRefused(429, "rate_limited")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise _WriteRefused(400, "bad_value") from None
        if not isinstance(payload, dict):
            raise _WriteRefused(400, "bad_value")
        return payload

    def _validated(model: type[BaseModel], payload: dict):
        try:
            return model.model_validate(payload)
        except ValidationError:
            # The code, and only the code: pydantic's message quotes the body back.
            raise _WriteRefused(400, "bad_value") from None

    def _placement_of(session: Session, card_id: int) -> ParlayPlacement | None:
        """The card's placement. `card_id` is `parlay_placements`' primary key: one per card."""
        return session.get(ParlayPlacement, card_id)

    def _placement_by_confirmation(session: Session, card_id: int,
                                   confirmation_id: str | None) -> ParlayPlacement | None:
        """The placement this exact submit already recorded, or None.

        A double tap, a retry over a flaky phone connection or a second tab sends the same
        `(card_id, confirmation_id)` and gets the same 200 and the same placement back (5.1).
        The same id against *another* card is a different thing entirely and `mark_placed`
        refuses it (`ConfirmationReused`).
        """
        if confirmation_id is None:
            return None
        placement = session.get(ParlayPlacement, card_id)
        if placement is None or placement.confirmation_id != confirmation_id:
            return None
        return placement

    @app.post("/api/parlay/placed")
    def parlay_placed(payload: dict = Depends(write_rules)):
        """Record a slip the owner placed by hand at DraftKings (addendum 5.1).

        This route records; it never places. Every refusal `mark_placed` makes is kept and
        translated to its code, and the `card_not_placeable` body carries the existing placement
        so a retry after a lost response shows the owner what is already recorded rather than a
        bare refusal (A-I17).
        """
        body = _validated(PlacedBody, payload)
        now = clock()
        with session_factory() as s:
            existing = _placement_by_confirmation(s, body.card_id, body.confirmation_id)
            if existing is not None:
                return {"placement": _placement_json(existing)}
            try:
                placement = mark_placed(s, body.card_id, body.accepted_odds, body.stake, now,
                                        body.leg_lines, confirmation_id=body.confirmation_id,
                                        note=body.note)
                # Serialized before the commit, so the response is the row this request wrote
                # whatever the factory's `expire_on_commit` is.
                recorded = _placement_json(placement)
                s.commit()
            except ConfirmationReused:
                s.rollback()
                raise _WriteRefused(409, "confirmation_reused") from None
            except CardNotPlaceable:
                s.rollback()
                raise _WriteRefused(409, "card_not_placeable",
                                    placement=_placement_json(
                                        _placement_of(s, body.card_id))) from None
            except BudgetExceeded as exc:
                s.rollback()
                raise _WriteRefused(409, "budget_exceeded", recorded=str(exc.recorded),
                                    left=str(exc.left)) from None
            except LineMoved as exc:
                s.rollback()
                raise _WriteRefused(409, "line_moved", moved={
                    str(seq): [str(was), str(moved_to)]
                    for seq, (was, moved_to) in exc.legs.items()}) from None
            except IntegrityError:
                # The partial unique index on `confirmation_id` is the backstop, not the check
                # (5.3): the request that lost the race answers with what is recorded.
                s.rollback()
                landed = _placement_of(s, body.card_id)
                if landed is not None:
                    return {"placement": _placement_json(landed)}
                # Nothing is recorded, so this was some other constraint, not the race the line
                # above answers. A 200 here would tell the owner's phone that a $25 slip is in
                # the ledger when the transaction wrote nothing at all, and the week's cap would
                # then let a further $25 through (review I-1). Name the one case that has a
                # name; everything else is an unnamed failure and answers as one.
                if _confirmation_on_another_card(s, body.card_id, body.confirmation_id):
                    raise _WriteRefused(409, "confirmation_reused") from None
                log.exception("the parlay placed route hit an integrity error with no placement "
                              "recorded")
                raise _WriteRefused(500, "internal_error") from None
            except Exception:
                # Anything the two modules above did not name: the owner's page gets a code and
                # the process gets the traceback. A refusal body never carries one.
                s.rollback()
                log.exception("the parlay placed route failed unexpectedly")
                raise _WriteRefused(500, "internal_error") from None
        return {"placement": recorded}

    @app.post("/api/parlay/correct")
    def parlay_correct(payload: dict = Depends(write_rules)):
        """Correct one placed card through the closed table of addendum 5.2.

        The same shape as the route above, over `apply_correction`, and the same rule about
        refusals: a triple the table does not define is `correction_not_allowed`, not an
        explanation of the table. The decline of 5.4 rides this route as `(status, declined)`
        and writes `void` with the owner's word in `declined_reason` -- there is no third
        route (D5).
        """
        body = _validated(CorrectionBody, payload)
        now = clock()
        with session_factory() as s:
            try:
                row = apply_correction(s, body.card_id, body.field, body.new_value, now,
                                       body.note)
                recorded = _correction_json(row)
                card = s.get(ParlayCard, body.card_id)
                status = None if card is None else card.status
                s.commit()
            except CardNotPlaceable:
                s.rollback()
                raise _WriteRefused(409, "card_not_placeable",
                                    placement=_placement_json(
                                        _placement_of(s, body.card_id))) from None
            except CorrectionsCapped:
                s.rollback()
                raise _WriteRefused(409, "corrections_capped") from None
            except CorrectionNotAllowed:
                s.rollback()
                raise _WriteRefused(409, "correction_not_allowed") from None
            except BudgetExceeded as exc:
                s.rollback()
                raise _WriteRefused(409, "budget_exceeded", recorded=str(exc.recorded),
                                    left=str(exc.left)) from None
            except ValueError:
                # A field or a value past the column's width: refused, never truncated.
                s.rollback()
                raise _WriteRefused(400, "bad_value") from None
            except Exception:
                # Anything the two modules above did not name: the owner's page gets a code and
                # the process gets the traceback. A refusal body never carries one.
                s.rollback()
                log.exception("the parlay correct route failed unexpectedly")
                raise _WriteRefused(500, "internal_error") from None
        return {"correction": recorded, "card_status": status}


def _payload_for_listener(name: str, payload, *, lan: bool):
    """A stored snapshot payload as the listener it was asked of may serve it.

    Addendum §6: the three actions on a Ticket slip (`open`, `I placed this`, `Not this one`)
    are offers to write, and the two write routes are installed on the LAN app alone. A loopback
    reader shown those buttons would post to routes that are not registered there and read a
    refusal for an offer that should never have been made, so the loopback listener serves every
    card with `actions == []` (Task 15 review, Important 1).

    Decided here rather than in the builder because there is exactly one `dashboard_snapshots`
    row per surface and both listeners read it: an `actions` array chosen at build time would
    say whatever the process that happened to build last chose, and each app would then serve
    the other's answer. Which listener was asked is a property of the request, so it is answered
    on the request path. The stored row is never modified -- the cards are rebuilt into new
    dicts on the way out -- and every other key of the payload is passed through untouched.
    """
    if lan or name != "ticket" or not isinstance(payload, dict):
        return payload

    def _read_only(card):
        # A failed section is `{"error": <class name>}`, not a card; it is passed through.
        return {**card, "actions": []} if isinstance(card, dict) else card

    served = dict(payload)
    cards = payload.get("cards")
    if isinstance(cards, list):
        served["cards"] = [_read_only(card) for card in cards]
    ideas = payload.get("ideas")
    if isinstance(ideas, dict) and isinstance(ideas.get("slots"), list):
        served["ideas"] = {**ideas, "slots": [
            {**slot, "card": _read_only(slot["card"])}
            if isinstance(slot, dict) and isinstance(slot.get("card"), dict) else slot
            for slot in ideas["slots"]]}
    return served


def create_dashboard(session_factory: sessionmaker, settings: Settings,
                     clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                     *, lan: bool = False) -> FastAPI:
    """The dashboard app. `lan` false is the loopback listener exactly as it has always been.

    Phase 4.6, addendum §6: `lan=True` adds the owner login, the session gate over every path
    but `/healthz` and the login page itself, and `X-Frame-Options: DENY` on every response. It
    adds no route to the loopback app and changes none of its behaviour -- in particular the
    kill pair is untouched there (invariant 9); on the LAN app the gate is what makes it need a
    session as well as its token (D15), and the two route bodies below are the same code either
    way.
    """
    scheduler = None
    snapshot_engine = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """The snapshot scheduler's whole lifetime, and the only place it is started.

        `snapshots_enabled` false means no jobs at all, which is what every test relies on: the
        suite drives `run_builder` directly and must never have background threads writing to
        the test database underneath it. The scheduler gets its own engine
        (`make_snapshot_engine`), never this app's request-path factory.
        """
        nonlocal scheduler, snapshot_engine
        if settings.snapshots_enabled:
            from harness.dashboard.scheduler import SnapshotScheduler

            snapshot_engine = snap.make_snapshot_engine(settings)
            scheduler = SnapshotScheduler(
                sessionmaker(bind=snapshot_engine, expire_on_commit=False), settings)
            scheduler.start()
            _app.state.snapshot_scheduler = scheduler
        else:
            _app.state.snapshot_scheduler = None
        try:
            yield
        finally:
            # Jobs first, then the pool. `shutdown(wait=False)` returns while a build may still
            # be running, and `dispose()` only closes *idle* connections -- a checked-out one is
            # discarded when it is returned -- so the build in flight still finishes.
            if scheduler is not None:
                scheduler.shutdown()
            if snapshot_engine is not None:
                snapshot_engine.dispose()

    app = FastAPI(title="harness-dashboard", lifespan=lifespan)
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

    @app.get("/api/snap")
    def api_snap_index() -> dict:
        """Every snapshot row's name, age and cadence. One sequential read, no builder.

        Named columns plus `payload ->> 'cadence_s'`, never the whole row: this is the endpoint
        the shell polls to decide what is stale, and `select *` here would deserialize every
        payload -- Floor's worst case is about 168 KB and each Study week carries a couple of
        thousand equity points -- to read one integer out of each. The table holds one row per
        surface plus one per ISO week of the season, so no `limit` is needed once the payloads
        are left on the server.
        """
        now = clock()
        with session_factory() as s:
            rows = s.execute(select(
                DashboardSnapshot.name, DashboardSnapshot.generated_at,
                DashboardSnapshot.elapsed_ms, DashboardSnapshot.error,
                DashboardSnapshot.payload["cadence_s"].as_integer().label("cadence_s"),
            )).all()
        return {"now": _iso(now), "snapshots": sorted(
            ({"name": row.name, "generated_at": _iso(row.generated_at),
              "age_s": (now - row.generated_at).total_seconds(),
              "cadence_s": row.cadence_s,
              "elapsed_ms": row.elapsed_ms, "error": row.error} for row in rows),
            key=lambda r: r["name"])}

    @app.get("/api/snap/{name}")
    def api_snap(name: str, response: Response,
                 if_none_match: str | None = Header(None, alias="If-None-Match")):
        """One snapshot, by primary key. No builder runs here and no other table is read: that
        is spec §0.3 made structural rather than habitual.

        The name is validated against the anchored pattern before it reaches the table lookup or
        the ETag header, so nothing but the five builder names and `study:<year>-<week>` can
        get through (ruling B-(e), item 8).
        """
        if not snap.valid_snapshot_name(name):
            raise HTTPException(status_code=404, detail="unknown snapshot")
        with session_factory() as s:
            row = s.get(DashboardSnapshot, name)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown snapshot")
        etag = f'"{name}:{row.generated_at.isoformat()}"'
        if _if_none_match_hits(if_none_match, etag):
            return Response(status_code=304, headers={"ETag": etag,
                                                      "Cache-Control": "no-cache"})
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        return {"name": row.name, "generated_at": _iso(row.generated_at),
                "elapsed_ms": row.elapsed_ms,
                "cadence_s": (row.payload or {}).get("cadence_s"),
                # The same code on both apps, one rule inside it: the LAN app is the loopback
                # app plus its additions, and neither reads a different row.
                "payload": _payload_for_listener(name, row.payload, lan=lan),
                "error": row.error}

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
        clean_reason = telemetry.sanitize_reason(reason)
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
            # Same transaction as the row update (design spec §3.2).
            telemetry.event(s, "kill_on", clean_reason, ts=now)
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
            # Same transaction as the row update (design spec §3.2).
            telemetry.event(s, "kill_off", "", ts=now)
            s.commit()
        return {"active": False}

    if lan:
        _install_lan_session(app, templates, settings, clock)
        _install_parlay_writes(app, session_factory, settings, clock)

    # `html=True` serves `index.html` for `/ui/`. Root `/`, `/api/summary`, `/healthz`, `/kill`
    # and `/unkill` are unchanged and are declared above, so nothing here can shadow them.
    app.mount("/ui", _NoCacheStatic(directory=str(_static_dir()), html=True), name="ui")

    return app
