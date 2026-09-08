import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import desc, text
from sqlalchemy.orm import Session, sessionmaker

from harness import telemetry
from harness.config.settings import Settings
from harness.db.models import Game, RawResponse, Run
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
# Fix 14: ESPN's undated scoreboard is scoped to "today" in US/Eastern, not the recorder's own
# tz_local -- the rollover this fetch chases is ESPN's, so it always uses Eastern regardless of
# where the harness runs.
_ESPN_TZ = ZoneInfo("America/New_York")
_ESPN_TERMINAL_STATUSES = {"final", "postponed", "canceled"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Task 12b telemetry -----------------------------------------------------------------

_NEWEST_RUN_BUILD_SHA = text("select build_sha from runs order by id desc limit 1")

_FETCHED_BY_SOURCE = text(
    "select source, count(*) from raw_responses where run_id = :run_id group by source")

_FAIR_VALUES_BY_FEED_KIND = text("""
    select coalesce(feed_kind, 'none') as feed_kind, count(*),
           percentile_cont(0.5) within group (order by staleness_s)
    from fair_values where run_id = :run_id
    group by coalesce(feed_kind, 'none')
""")

_REJECTED_BY_VARIANT_REASON = text("""
    select v.name, coalesce(s.rejection_reason, 'unknown'), count(*)
    from signals s join strategy_variants v on v.variant_id = s.variant_id
    where s.run_id = :run_id and s.decision = 'rejected'
    group by v.name, coalesce(s.rejection_reason, 'unknown')
""")


def _pricing_samples(session: Session, run_id: int, pricing: dict) -> list[tuple[str, object, dict]]:
    """`pricing.*` metric_samples for one tick (design spec §3.1), read back from what this
    run actually wrote rather than threaded through `price_and_signal`'s return value: that
    dict already carries candidate/rejected *counts*, not the reason breakdown the dashboard
    wants, and the fair-value feed_kind split is a property of the `fair_values` rows
    themselves.
    """
    samples: list[tuple[str, object, dict]] = []
    for feed_kind, count, median_staleness in session.execute(
            _FAIR_VALUES_BY_FEED_KIND, {"run_id": run_id}).all():
        label = {"feed_kind": feed_kind}
        samples.append(("pricing.fair_values", count, label))
        if median_staleness is not None:
            samples.append(("pricing.staleness_median_s", float(median_staleness), label))
    for variant, counts in (pricing or {}).get("signals", {}).items():
        samples.append(("pricing.candidates", counts.get("candidate", 0), {"variant": variant}))
    for variant, reason, count in session.execute(
            _REJECTED_BY_VARIANT_REASON, {"run_id": run_id}).all():
        samples.append(("pricing.rejected", count, {"variant": variant, "reason": reason}))
    return samples


def _recorder_samples(session: Session, run_id: int, tick_ms: int,
                      ctx: dict) -> list[tuple[str, object, dict]]:
    """`recorder.*` metric_samples for one tick."""
    samples: list[tuple[str, object, dict]] = [("recorder.tick_ms", tick_ms, {})]
    for source, count in session.execute(_FETCHED_BY_SOURCE, {"run_id": run_id}).all():
        samples.append(("recorder.fetched", count, {"source": source}))
    samples.append(("recorder.errors", len(ctx["errors"]), {}))
    if ctx.get("remaining") is not None:
        samples.append(("recorder.credits_remaining", ctx["remaining"], {}))
    samples.append(("recorder.trade_gaps", len(ctx["trade_gaps"]), {}))
    samples.extend(_pricing_samples(session, run_id, ctx.get("pricing") or {}))
    return samples


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
        # A forced tick (deploy verification) ignores every per-source interval for that one tick.
        self._force = False
        # Task 12b: the build_sha deploy check runs once per process, at the first tick.
        self._startup_checked = False

    # ---- helpers -------------------------------------------------------------------
    def _latest_body_from_db(self, session: Session, source: str, endpoint: str) -> dict | list | None:
        # Fix round 1, Important 1: fix 14's dated rollover fetch is stored under this same
        # (source, endpoint) and can be the newest row for it, e.g. right after a restart
        # while today's cadence key is still fresh. It must never be handed back here as
        # "today's" body -- that would feed yesterday's (possibly still in_progress) games into
        # the paid Odds/Kalshi cadence planner. `~params.has_key("dates")` excludes it; every
        # other caller's rows have no `dates` key, so this filter is a no-op for them.
        row = (session.query(RawResponse).filter_by(source=source, endpoint=endpoint, http_status=200)
               .filter(~RawResponse.params.has_key("dates"))
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
                if self._due(store.get_source_state(session, key), now, 900):
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
                self._espn_rollover(session, run, sport, now, ctx)
            except Exception as e:  # noqa: BLE001
                log.exception("espn failed")
                ctx["errors"].append({key: repr(e)})
        return kickoffs

    def _espn_rollover(self, session: Session, run: Run, sport: str, now: datetime, ctx: dict) -> None:
        """Fix 14 (journal 43-44, game 114): ESPN's undated scoreboard drops an event the
        instant its day rolls at 00:00 Eastern, so a game whose kickoff fell on the previous
        Eastern date and is still non-terminal never gets a final status or score from the
        plain fetch above and is stuck forever. While such a game exists for this sport, also
        fetch that date's scoreboard (same path and other params, plus `dates=YYYYMMDD`) under
        its own per-sport-and-date cadence key so it never competes with today's 900s fetch.
        `_family_filter("espn")` in harness/normalize/runner.py partitions raw espn rows by
        source alone, so the dated body is normalized exactly like today's -- no change needed
        there or in `link_espn_scoreboard`, which already updates any game carrying the body's
        event id regardless of which date fetched it.
        """
        # Fix round 1, Minor 1: each edge is its own Eastern midnight, converted independently --
        # not `day_start + 24h` -- so the window is exactly one Eastern calendar day even across
        # a DST transition (the US fall-back day has a 25th UTC hour; `+timedelta(days=1)` on the
        # UTC-converted start would end the window an hour before the next Eastern midnight).
        yesterday_et = (now.astimezone(_ESPN_TZ) - timedelta(days=1)).date()
        day_start = datetime.combine(yesterday_et, datetime.min.time(), tzinfo=_ESPN_TZ).astimezone(timezone.utc)
        day_end = datetime.combine(yesterday_et + timedelta(days=1), datetime.min.time(),
                                   tzinfo=_ESPN_TZ).astimezone(timezone.utc)
        pending = session.query(Game.id).filter(
            Game.sport == sport, Game.kickoff_utc >= day_start, Game.kickoff_utc < day_end,
            Game.status.notin_(_ESPN_TERMINAL_STATUSES)
        ).first() is not None
        if not pending:
            return
        date_str = yesterday_et.strftime("%Y%m%d")
        key = f"espn_dated:{sport}:{date_str}"
        if not self._due(store.get_source_state(session, key), now, 900):
            return
        r = self.espn.fetch_scoreboard(sport, dates=date_str)  # type: ignore[arg-type]
        store.store_raw(session, run.id, "espn", _ESPN_PATH[sport], {"dates": date_str}, r)
        ctx["n"] += 1
        if r.status == 200:
            store.set_source_state(session, key, now)
        else:
            ctx["errors"].append({key: f"http {r.status}"})
        ctx["fetched"] = True

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
                if self._due(store.get_source_state(session, key), now, interval):
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
                # Alternates are deliberately not forced: one per in-window event would multiply the
                # credit cost of a deploy check; the featured fetch above already proves the pipeline.
                for eid in alternates_due(now, events, last_alt, near_s=self.s.odds_alt_interval_near_s,
                                          far_s=self.s.odds_alt_interval_far_s, window_h=self.s.odds_alt_window_h):
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
                if self._due(store.get_source_state(session, key), now, interval):
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
                if self._due(store.get_source_state(session, key), now, 900):
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

    def _kalshi_settled(self, session: Session, run: Run, now: datetime, ctx: dict) -> None:
        """Record each football series' recently settled markets so phase 3 can grade paper
        positions against the venue's own `result`.

        Hourly, not per tick: a settled market's `result` never changes, so one fetch an hour is
        enough to catch every settlement. The 8-day `min_settled_ts` window covers a full football
        week plus a day of slack for a recorder outage, and stays clear of the historical cutoff
        below which markets only exist on `GET /historical/markets`. Like `_kalshi_events`, the
        interval is a constant rather than `interval_for`, so this also runs during the cadence
        planner's quiet window; the Kalshi public API costs no Odds API credits.
        """
        for series in FOOTBALL_SERIES:
            key = f"kalshi_settled:{series}"
            try:
                pages: list = []
                if self._due(store.get_source_state(session, key), now, 3600):
                    min_settled_ts = int((now - timedelta(days=8)).timestamp())
                    for r in self.kalshi.fetch_markets_all(series, status="settled", min_settled_ts=min_settled_ts):
                        store.store_raw(session, run.id, "kalshi", "/markets",
                                        {"series_ticker": series, "status": "settled"}, r)
                        ctx["n"] += 1
                        pages.append(r)
                    all_ok = all(r.status == 200 for r in pages)
                    if pages and all_ok:
                        store.set_source_state(session, key, now)
                    elif pages and not all_ok:
                        ctx["errors"].append({key: f"partial pagination: statuses {[r.status for r in pages]}"})
                    ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi settled failed")
                ctx["errors"].append({key: repr(e)})

    def _kalshi_series(self, session: Session, run: Run, now: datetime, ctx: dict) -> None:
        """A series' fee shape (F45/R21) rarely changes, so one fetch per football series per
        day is enough; the normalizer writes `fee_type`/`fee_multiplier` onto that series'
        venue_markets rows from the stored body."""
        for series in FOOTBALL_SERIES:
            key = f"kalshi_series:{series}"
            try:
                if self._due(store.get_source_state(session, key), now, 86400):
                    r = self.kalshi.fetch_series(series)
                    store.store_raw(session, run.id, "kalshi", f"/series/{series}", {"series_ticker": series}, r)
                    ctx["n"] += 1
                    if r.status == 200:
                        store.set_source_state(session, key, now)
                    else:
                        ctx["errors"].append({key: f"http {r.status}"})
                    ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi series failed")
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
    def _due(self, last: datetime | None, now: datetime, interval: int | None) -> bool:
        # interval=None is the cadence planner's quiet-window "do not fetch": a forced tick never
        # overrides it (no paid calls at 03:00 for a deploy check).
        if interval is None:
            return False
        return True if self._force else is_due(last, now, interval)

    def maybe_tick(self, force: bool = False) -> Run:
        self._force = force
        now = self.clock()
        started_mono = self.monotonic()
        budget = _Budget(self.s.tick_budget_s, self.monotonic)
        ctx: dict = {"n": 0, "credits": 0, "remaining": None, "errors": [], "warnings": [], "fetched": False,
                     "skipped_trades": 0, "skipped_ladders": 0, "skipped_alternates": 0, "trade_gaps": []}
        with self.session_factory() as session:
            ensure_partitions(session, now)
            # Task 12b: read the newest run's build_sha before this run's own row exists, so
            # the comparison below is against the *previous* deploy, not this one.
            prior_sha = (session.execute(_NEWEST_RUN_BUILD_SHA).scalar()
                        if not self._startup_checked else None)
            run = store.start_run(session, now)
            run.build_sha = self.s.build_sha
            # Committed on its own, ahead of everything telemetry can fail on below (D11's
            # backstop: every run row must carry its build_sha whether or not the deploy event
            # or the end-of-tick metric batch succeeds).
            session.commit()
            if not self._startup_checked:
                self._startup_checked = True
                if prior_sha is not None and prior_sha != self.s.build_sha:
                    try:
                        telemetry.event(
                            session, "deploy", f"build_sha {prior_sha} -> {self.s.build_sha}",
                            ref={"from": prior_sha, "to": self.s.build_sha}, ts=now)
                        session.commit()
                    except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails a tick
                        log.exception("recorder deploy event failed")
                        session.rollback()
            summaries: list[MarketSummary] = []
            try:
                kickoffs = self._espn(session, run, now, ctx)
                self._checkpoint(session, run)
                self._odds(session, run, now, kickoffs, budget, ctx)
                self._checkpoint(session, run)
                summaries = self._kalshi_markets(session, run, now, kickoffs, ctx)
                self._checkpoint(session, run)
                self._kalshi_events(session, run, now, ctx)
                self._kalshi_settled(session, run, now, ctx)
                self._kalshi_series(session, run, now, ctx)
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
            try:
                tick_ms = int((self.monotonic() - started_mono) * 1000)
                telemetry.record_many(session, "recorder",
                                      _recorder_samples(session, run.id, tick_ms, ctx), ts=now)
            except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails a tick
                log.exception("recorder metrics failed")
                session.rollback()
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
                                    "non_linear_cent": ctx.get("non_linear_cent", 0),
                                    "pricing": ctx.get("pricing", {})},
                             finished_at=self.clock())
            log.info("tick %s n=%d credits=%d errors=%d warnings=%d", status, ctx["n"], ctx["credits"],
                     len(ctx["errors"]), len(ctx["warnings"]))
            return run
