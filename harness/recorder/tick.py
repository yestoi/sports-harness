import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.db.models import RawResponse, Run
from harness.db.schema import ensure_partitions
from harness.feeds.espn import EspnClient, Kickoff, parse_kickoffs
from harness.feeds.odds_api import OddsApiClient, parse_credit_headers, parse_event_ids_and_times
from harness.normalize.runner import normalize_new
from harness.recorder import store
from harness.recorder.cadence import (SPORTS, alternates_due, interval_for, is_due, select_ladders,
                                      select_trade_tickers)
from harness.strategy.pipeline import price_and_signal
from harness.venues.kalshi.public import FOOTBALL_SERIES, KalshiPublic, MarketSummary, parse_market_summaries

log = logging.getLogger(__name__)
_ESPN_PATH = {"nfl": "/nfl/scoreboard", "ncaaf": "/college-football/scoreboard"}
_SERIES_SPORT = {s: ("nfl" if "NFL" in s else "ncaaf") for s in FOOTBALL_SERIES}
ALTERNATES_BUDGET_S = 40  # alternates may spend at most this much of the tick budget
KALSHI_COMMIT_EVERY = 50  # commit after this many stored trade/ladder responses
TRADES_MAX_PAGES = 20  # 20,000 trades per window before we stop paginating and record a gap


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Budget:
    def __init__(self, seconds: int, monotonic: Callable[[], float]):
        self._deadline = monotonic() + seconds
        self._mono = monotonic

    def ok(self) -> bool:
        return self._mono() < self._deadline

    def remaining_s(self) -> float:
        return self._deadline - self._mono()


class Recorder:
    def __init__(self, settings: Settings, session_factory: sessionmaker, odds: OddsApiClient, espn: EspnClient,
                 kalshi: KalshiPublic, clock: Callable[[], datetime] = utcnow,
                 monotonic: Callable[[], float] = time.monotonic):
        self.s = settings
        self.session_factory = session_factory
        self.odds, self.espn, self.kalshi = odds, espn, kalshi
        self.clock, self.monotonic = clock, monotonic
        # I7: last known-good body per (source, endpoint); avoids re-reading multi-MB JSONB every tick.
        self._last_good: dict[tuple[str, str], dict | list] = {}

    # ---- helpers -------------------------------------------------------------------
    def _latest_body_from_db(self, session: Session, source: str, endpoint: str) -> dict | list | None:
        row = (session.query(RawResponse).filter_by(source=source, endpoint=endpoint, http_status=200)
               .order_by(desc(RawResponse.fetched_at)).first())
        return row.body if row else None

    def _latest_body(self, session: Session, source: str, endpoint: str) -> dict | list | None:
        cached = self._last_good.get((source, endpoint))
        if cached is not None:
            return cached
        body = self._latest_body_from_db(session, source, endpoint)
        if body is not None:
            self._last_good[(source, endpoint)] = body
        return body

    def _checkpoint(self, session: Session, run: Run) -> None:
        """Commit what has been stored so far and drop it from the identity map (I1/I8)."""
        session.commit()
        session.expunge_all()
        session.add(run)  # re-attach so finish_run still writes through this session

    # ---- sources -------------------------------------------------------------------
    def _espn(self, session: Session, run: Run, now: datetime, ctx: dict) -> list[Kickoff]:
        kickoffs: list[Kickoff] = []
        for sport in SPORTS:
            key = f"espn:{sport}"
            try:
                body = None
                if is_due(store.get_source_state(session, key), now, 900):
                    r = self.espn.fetch_scoreboard(sport)  # type: ignore[arg-type]
                    store.store_raw(session, run.id, "espn", _ESPN_PATH[sport], {}, r)
                    ctx["n"] += 1
                    if r.status == 200:
                        store.set_source_state(session, key, now)
                        body = r.body
                        self._last_good[("espn", _ESPN_PATH[sport])] = body
                    else:
                        ctx["errors"].append({key: f"http {r.status}"})
                    ctx["fetched"] = True
                if body is None:
                    body = self._latest_body(session, "espn", _ESPN_PATH[sport])
                kickoffs.extend(parse_kickoffs(sport, body))
            except Exception as e:  # noqa: BLE001
                log.exception("espn failed")
                ctx["errors"].append({key: repr(e)})
        return kickoffs

    def _odds(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff], budget: _Budget,
              ctx: dict) -> None:
        # C2: alternates get a sub-budget so a slow Odds API cannot starve the Kalshi capture.
        alt_floor = self.s.tick_budget_s - ALTERNATES_BUDGET_S
        for sport, sport_key in SPORTS.items():
            key = f"odds_featured:{sport}"
            try:
                interval = interval_for(sport, now, kickoffs, self.s.tz_local)
                endpoint = f"/sports/{sport_key}/odds"
                body = None
                if is_due(store.get_source_state(session, key), now, interval):
                    r = self.odds.fetch_featured(sport_key)
                    store.store_raw(session, run.id, "odds_api", endpoint, {"markets": "featured"}, r)
                    ctx["n"] += 1
                    c = parse_credit_headers(r.headers)
                    ctx["credits"] += c.last
                    if "x-requests-remaining" in r.headers:  # I5
                        ctx["remaining"] = c.remaining
                    if r.status == 200:
                        store.set_source_state(session, key, now)
                        body = r.body
                        self._last_good[("odds_api", endpoint)] = body
                    else:
                        ctx["errors"].append({key: f"http {r.status}"})
                    ctx["fetched"] = True
                if body is None:
                    body = self._latest_body(session, "odds_api", endpoint)
                if interval is None:
                    continue
                events = parse_event_ids_and_times(body)
                last_alt = {eid: ts for eid, _ in events
                            if (ts := store.get_source_state(session, f"odds_alt:{eid}")) is not None}
                for eid in alternates_due(now, events, last_alt, self.s.odds_alternates_interval_s):
                    if budget.remaining_s() < alt_floor:
                        ctx["skipped_alternates"] += 1
                        continue
                    try:
                        r = self.odds.fetch_event_alternates(sport_key, eid)
                        store.store_raw(session, run.id, "odds_api", f"/sports/{sport_key}/events/{eid}/odds",
                                        {"markets": "alternates"}, r)
                        ctx["n"] += 1
                        c = parse_credit_headers(r.headers)
                        ctx["credits"] += c.last
                        if "x-requests-remaining" in r.headers:  # I5
                            ctx["remaining"] = c.remaining
                        if r.status == 200:
                            store.set_source_state(session, f"odds_alt:{eid}", now)
                        else:
                            ctx["warnings"].append({f"odds_alt:{eid}": f"http {r.status}"})
                        ctx["fetched"] = True
                    except Exception as e:  # noqa: BLE001
                        log.exception("odds alternates failed")
                        ctx["errors"].append({f"odds_alt:{eid}": repr(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("odds featured failed")
                ctx["errors"].append({key: repr(e)})

    def _kalshi_markets(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff], ctx: dict) -> list[MarketSummary]:
        summaries: list[MarketSummary] = []
        for series in FOOTBALL_SERIES:
            key = f"kalshi_markets:{series}"
            try:
                interval = interval_for(_SERIES_SPORT[series], now, kickoffs, self.s.tz_local)
                pages: list = []
                if is_due(store.get_source_state(session, key), now, interval):
                    for r in self.kalshi.fetch_markets_all(series):
                        store.store_raw(session, run.id, "kalshi", "/markets", {"series_ticker": series}, r)
                        ctx["n"] += 1
                        pages.append(r)
                    all_ok = all(r.status == 200 for r in pages)
                    if pages and all_ok:
                        store.set_source_state(session, key, now)
                    elif pages and not all_ok:
                        ctx["errors"].append({key: f"partial pagination: statuses {[r.status for r in pages]}"})
                    ctx["fetched"] = True
                for r in pages:
                    if r.status == 200:
                        summaries.extend(parse_market_summaries(r.body))
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi markets failed")
                ctx["errors"].append({key: repr(e)})
        return summaries

    def _kalshi_events(self, session: Session, run: Run, now: datetime, ctx: dict) -> None:
        for series in FOOTBALL_SERIES:
            key = f"kalshi_events:{series}"
            try:
                pages: list = []
                if is_due(store.get_source_state(session, key), now, 900):
                    for r in self.kalshi.fetch_events_all(series):
                        store.store_raw(session, run.id, "kalshi", "/events", {"series_ticker": series}, r)
                        ctx["n"] += 1
                        pages.append(r)
                    all_ok = all(r.status == 200 for r in pages)
                    if pages and all_ok:
                        store.set_source_state(session, key, now)
                    elif pages and not all_ok:
                        ctx["errors"].append({key: f"partial pagination: statuses {[r.status for r in pages]}"})
                    ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi events failed")
                ctx["errors"].append({key: repr(e)})

    def _kalshi_trades_and_ladders(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff],
                                   summaries: list[MarketSummary], budget: _Budget, ctx: dict) -> None:
        wms = {t: (w.last_ts, Decimal(w.last_volume_fp)) for t, w in store.get_watermarks(session).items()}
        trades = select_trade_tickers(now, summaries, wms)
        vol = {m.ticker: m.volume_fp for m in summaries}
        stored = 0

        def maybe_commit() -> None:
            nonlocal stored
            if stored >= KALSHI_COMMIT_EVERY:
                self._checkpoint(session, run)
                stored = 0

        for ticker, min_ts in trades:
            if not budget.ok():
                ctx["skipped_trades"] += 1
                continue
            try:
                pages = self.kalshi.fetch_trades(ticker, min_ts, max_pages=TRADES_MAX_PAGES)  # I3: follows the cursor
                for r in pages:
                    store.store_raw(session, run.id, "kalshi", "/markets/trades",
                                    {"ticker": ticker, "min_ts": min_ts.isoformat()}, r)
                    ctx["n"] += 1
                    stored += 1
                if not pages:
                    continue
                ctx["fetched"] = True
                if all(r.status == 200 for r in pages):
                    stamps: list[datetime] = []
                    for r in pages:
                        trades_list = r.body.get("trades", []) if isinstance(r.body, dict) else []
                        for t in trades_list:
                            try:
                                stamps.append(datetime.fromisoformat(t["created_time"].replace("Z", "+00:00")))
                            except (KeyError, ValueError, AttributeError):
                                pass
                    newest = max(stamps) if stamps else now
                    store.upsert_watermark(session, ticker, newest, vol.get(ticker, Decimal("0")))
                    last = pages[-1]
                    unexhausted = (isinstance(last.body, dict) and bool(last.body.get("cursor"))
                                   and len(pages) >= TRADES_MAX_PAGES)
                    if unexhausted:
                        oldest = min(stamps) if stamps else min_ts
                        ctx["warnings"].append(
                            {f"kalshi_trades:{ticker}": f"trade gap: {len(pages)} pages, "
                                                        f"oldest_seen={oldest.isoformat()}, "
                                                        f"min_ts={min_ts.isoformat()}"})
                        ctx["trade_gaps"].append({"ticker": ticker, "min_ts": min_ts.isoformat(),
                                                  "oldest_seen": oldest.isoformat(), "pages": len(pages)})
                else:
                    # I2: record the failure but still park the volume watermark so the ticker is not
                    # re-selected every tick. last_ts is left untouched, so no trades are skipped.
                    ctx["warnings"].append(
                        {f"kalshi_trades:{ticker}": f"http {[r.status for r in pages]}"})
                    prior_ts = wms[ticker][0] if ticker in wms else min_ts
                    store.upsert_watermark(session, ticker, prior_ts, vol.get(ticker, Decimal("0")))
                maybe_commit()
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi trades failed")
                ctx["errors"].append({f"kalshi_trades:{ticker}": repr(e)})
        for ticker in select_ladders(now, summaries, kickoffs, self.s.tz_local, self.s.ladder_cap_per_tick):
            if not budget.ok():
                ctx["skipped_ladders"] += 1
                continue
            try:
                r = self.kalshi.fetch_orderbook(ticker)
                store.store_raw(session, run.id, "kalshi", f"/markets/{ticker}/orderbook", {"depth": 20}, r)
                ctx["n"] += 1
                stored += 1
                if r.status != 200:
                    ctx["warnings"].append({f"kalshi_orderbook:{ticker}": f"http {r.status}"})
                ctx["fetched"] = True
                maybe_commit()
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi orderbook failed")
                ctx["errors"].append({f"kalshi_orderbook:{ticker}": repr(e)})

    # ---- entry point -----------------------------------------------------------------
    def maybe_tick(self) -> Run:
        now = self.clock()
        budget = _Budget(self.s.tick_budget_s, self.monotonic)
        ctx: dict = {"n": 0, "credits": 0, "remaining": None, "errors": [], "warnings": [], "fetched": False,
                     "skipped_trades": 0, "skipped_ladders": 0, "skipped_alternates": 0, "trade_gaps": []}
        with self.session_factory() as session:
            ensure_partitions(session, now)
            run = store.start_run(session, now)
            summaries: list[MarketSummary] = []
            try:
                kickoffs = self._espn(session, run, now, ctx)
                self._checkpoint(session, run)
                self._odds(session, run, now, kickoffs, budget, ctx)
                self._checkpoint(session, run)
                summaries = self._kalshi_markets(session, run, now, kickoffs, ctx)
                self._checkpoint(session, run)
                self._kalshi_events(session, run, now, ctx)
                self._checkpoint(session, run)
                if summaries:
                    self._kalshi_trades_and_ladders(session, run, now, kickoffs, summaries, budget, ctx)
                # Commit the tail batch of trades/ladders before normalization so a rollback there
                # can never discard fetched raw rows.
                self._checkpoint(session, run)
            except Exception as e:  # noqa: BLE001
                log.exception("tick failed")
                ctx["errors"].append({"tick": repr(e)})
            try:
                ctx["normalized"] = normalize_new(session, ctx=ctx, time_budget_s=30)
            except Exception as e:  # noqa: BLE001
                log.exception("normalize failed")
                # A DB error here (e.g. a missing table right after an upgrade) leaves the
                # session's transaction aborted; roll back so finish_run's UPDATE below does not
                # also fail with InFailedSqlTransaction. The raw rows already made it in via the
                # per-source checkpoints, so nothing is lost.
                session.rollback()
                ctx["warnings"].append({"normalize": repr(e)})
            if summaries:
                # Only price when this tick actually refreshed Kalshi markets, so gap snapshots
                # are computed against fresh quotes rather than stale ones from a skipped tick.
                # Price with the clock read *now*, not the tick's start time: the odds fetched a
                # few seconds into this tick carry fetched_at > run.started_at, and the book-line
                # loader's upper bound would otherwise fall back to the previous fetch (2-5 min old),
                # labelling every fair value stale.
                pricing_now = self.clock()
                try:
                    ctx["pricing"] = price_and_signal(session, run.id, pricing_now, self.s, self.s.price_budget_s)
                except Exception as e:  # noqa: BLE001
                    log.exception("pricing failed")
                    session.rollback()
                    ctx["warnings"].append({"pricing": repr(e)})
            exhausted = (ctx["skipped_trades"] > 0 or ctx["skipped_ladders"] > 0
                         or ctx["skipped_alternates"] > 0)
            if ctx["errors"]:
                status = "error"
            elif ctx["warnings"]:
                status = "degraded"
            else:
                status = "ok" if ctx["fetched"] else "skipped"
            store.finish_run(session, run, status, error=None if not ctx["errors"] else "see notes",
                             n_requests=ctx["n"], credits_used=ctx["credits"], odds_remaining=ctx["remaining"],
                             budget_exhausted=exhausted,
                             notes={"errors": ctx["errors"], "warnings": ctx["warnings"],
                                    "skipped_trades": ctx["skipped_trades"],
                                    "skipped_ladders": ctx["skipped_ladders"],
                                    "skipped_alternates": ctx["skipped_alternates"],
                                    "trade_gaps": ctx["trade_gaps"],
                                    "normalized": ctx.get("normalized", {}),
                                    "unresolved_teams": sorted(set(ctx.get("unresolved_teams", [])))[:50],
                                    "normalize_errors": ctx.get("normalize_errors", []),
                                    "odds_dropped": ctx.get("odds_dropped", {}),
                                    "taker_side_missing": ctx.get("taker_side_missing", 0),
                                    "pricing": ctx.get("pricing", {})},
                             finished_at=self.clock())
            log.info("tick %s n=%d credits=%d errors=%d warnings=%d", status, ctx["n"], ctx["credits"],
                     len(ctx["errors"]), len(ctx["warnings"]))
            return run
