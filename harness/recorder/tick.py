import logging
import math
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
from harness.feeds.nws import NwsClient
from harness.feeds.odds_api import OddsApiClient, parse_credit_headers, parse_event_ids_and_times
from harness.normalize.runner import normalize_new
from harness.recorder import store
from harness.recorder.cadence import (SPORTS, PropEvent, alternates_due, interval_for, is_due,
                                      prop_events_due, prop_events_watched, select_ladders,
                                      select_trade_tickers)
from harness.strategy.pipeline import price_and_signal
from harness.venues.kalshi.public import FOOTBALL_SERIES, KalshiPublic, MarketSummary, parse_market_summaries
from harness.weeks import chicago_day, chicago_iso_week
from harness.weather.snapshots import ALLOWED_CADENCES, MIN_TICK_REMAINING_S, run_weather_source

log = logging.getLogger(__name__)
_ESPN_PATH = {"nfl": "/nfl/scoreboard", "ncaaf": "/college-football/scoreboard"}
_SERIES_SPORT = {s: ("nfl" if "NFL" in s else "ncaaf") for s in FOOTBALL_SERIES}
ALTERNATES_BUDGET_S = 40  # alternates may spend at most this much of the tick budget
#: Phase 4.6 3.2. The prop source's own sub-budget, the `ALTERNATES_BUDGET_S` pattern and a
#: separate constant: the roster half and the prop calls share it, so the two together can never
#: take more than this much of a tick.
PROPS_BUDGET_S = 40
#: Props run on the 900 s tick and on no other (3.2): never the 120 s game window, never the
#: 20 s NFL pre-kickoff window, and never in the quiet hours, where `interval_for` returns None.
PROPS_CADENCE_S = 900
#: One roster per team per week (4.1). The fetch is skipped for the next seven days per team,
#: which is what bounds the roster half to a handful of calls even on a full Saturday.
ROSTER_MAX_AGE = timedelta(days=7)
#: A safety bound on the per-sport candidate read, not the policy cap: `prop_events_max` (16)
#: decides what is watched. A 24 h window holds at most about 60 college games.
PROP_EVENT_SCAN_LIMIT = 200
#: The collector (4.3): the game-window cadence, its own sub-budget, and at most this many games
#: a tick.
PLAYER_STATS_CADENCE_S = 120
PLAYER_STATS_BUDGET_S = 10
PLAYER_STATS_MAX_GAMES = 8
#: A safety bound on the carded-game read; the real bound is the placed/alive cards of one week.
CARDED_GAMES_SCAN_LIMIT = 32
#: After a game goes final the collector keeps asking for the box score for at most this long
#: (4.3). A card still pending after it is the hung-leg state, which the `parlay_pending_final`
#: WATCH rule reports rather than the collector fetching for ever.
FINAL_RETRY_CEILING = timedelta(hours=6)
#: `parlay_legs.market_type` -> the spelling `odds_snapshots` uses for the same market. Prop
#: legs are the string "prop" and resolve through `odds_prop_snapshots` instead.
LEG_MARKET_TYPES = {"ml": "moneyline", "spread": "spread", "total": "total"}
#: The other side of a two-sided prop selection, for the devig the reprice re-derives.
PROP_OPPOSITE = {"over": "under", "under": "over", "yes": "no", "no": "yes"}
#: A source-state stamp that sorts before every real one: a key never fetched leads its rotation.
_NEVER_FETCHED = datetime.min.replace(tzinfo=timezone.utc)
#: Fix round 1 (review C1). The prop pass's own `source_state` key, the gate every other paid
#: source in this file already has. `maybe_tick` runs on the 30 s heartbeat, so the cadence
#: *value* being 900 says which period is in force, not that 900 s have passed: without this the
#: rotation paid for sixteen events thirty times an hour (17,280 credits) where the design prices
#: four (576), and the month's 300,000 allocation was gone in about a day.
PROPS_STATE_KEY = "odds_props"
#: The reprice's own gate (fix round 1, the controller's ruling on concern 1). Same 900 s
#: interval as the prop rotation and deliberately **independent** of it: the reprice spends no
#: credit and reads only stored rows, so a draft -- and its game-line legs, whose prices keep
#: arriving from the featured feed -- must keep repricing while props are dormant for the month,
#: held back by the 40 % watch fraction, or absent because it is a weekend.
REPRICE_STATE_KEY = "parlay_reprice"
#: The roster half's slice of the prop sub-budget (review M2): on the first pass after a weekly
#: expiry a full watched set is up to 64 teams, and without a slice of its own the free ESPN half
#: could spend the whole 40 s and leave no prop call at all.
PROPS_ROSTER_BUDGET_S = 15
#: Review I4. Consecutive failures on one event before it is rested, and for how long. Sixteen
#: dead events (re-keyed ids answering 404) would otherwise hold the head of the oldest-first
#: rotation and convert the entire 16-call allowance into waste until their kickoffs pass.
PROP_FAIL_BACKOFF_AFTER = 3
PROP_FAIL_BACKOFF = timedelta(hours=1)
KALSHI_COMMIT_EVERY = 50  # commit after this many stored trade/ladder responses
TRADES_MAX_PAGES = 20  # 20,000 trades per window before we stop paginating and record a gap
# Fix 14: ESPN's undated scoreboard is scoped to "today" in US/Eastern, not the recorder's own
# tz_local -- the rollover this fetch chases is ESPN's, so it always uses Eastern regardless of
# where the harness runs.
_ESPN_TZ = ZoneInfo("America/New_York")
_ESPN_TERMINAL_STATUSES = {"final", "postponed", "canceled"}

#: Amendment 4 fix round 1. `tick_budget_s` bounds the fetch phase only: normalization takes up
#: to 30 s after it and pricing a further `price_budget_s`, so a 45 s pricing budget would put
#: the worst-case tick at 175 s -- past the 120 s game-window cadence and far past the 20 s NFL
#: pre-kickoff one. `max_instances=1, coalesce=True` on the scheduler's job then drops the next
#: tick outright and the data it would have recorded is simply lost. So pricing gets what the
#: cadence in force actually leaves, less a margin, and never less than the floor.
PRICE_BUDGET_FLOOR_S = 20
PRICE_BUDGET_MARGIN_S = 10
#: The weekday period `interval_for` returns outside every game window. Used when no sport has a
#: cadence in force -- the 01:00-08:00 quiet window, or a tick whose ESPN fetch failed before it
#: learned the day's kickoffs.
DEFAULT_CADENCE_S = 900

#: How often the recorder re-reads `GET /account/limits` on its signed, GET-only reader (§1.3,
#: ruling A-C3). The tick runs every `heartbeat_s` (30 s in production); the account's tier and
#: buckets change on the scale of days, so an hourly read is plenty and keeps the phase's only
#: production signed call to about 24 a day.
LIMITS_REFRESH_S = 3600
#: The venue's `tier` is the one venue *string* this task stores and prints (`runs.notes` and the
#: Health block), so it gets the global constraint's treatment for untrusted venue text, scaled
#: to an identifier: ASCII-escaped, control characters stripped, truncated.
_TIER_MAX_LEN = 32
#: A failed read's `repr` goes into `notes.warnings`; bounded for the same reason.
_LIMITS_WARNING_MAX_LEN = 200
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}

#: The ceiling on the page pause the venue's own bucket may impose (fix round 1, Important 2).
#: `KalshiPublic._pause` sleeps this after every page and the paging loops have no budget check
#: inside them, so an unbounded pause from one malformed or mis-unit'd `refill_rate` would stop
#: the recorder's Kalshi fetching silently and indefinitely -- `max_instances=1` then drops every
#: subsequent tick. Five seconds is far above any real Kalshi read bucket and far below a tick.
PAGE_PAUSE_CEILING_S = 5.0
#: The range a venue-supplied refill rate must fall in to be believed, in requests per second,
#: and the range for a bucket capacity (fix round 1, Important 1). A number outside either has no
#: safe interpretation, so the whole read is discarded rather than clamped: these values reach a
#: control path (the page pause) and `runs.notes`, and the global constraints keep venue numbers
#: out of both unfiltered. `dec` has already rejected NaN and the infinities upstream; this also
#: catches the finite Decimals that become 0.0 or inf when narrowed to a float (`1e-400`, `1e400`).
LIMIT_RATE_RANGE = (0.001, 1e6)
LIMIT_CAPACITY_RANGE = (0.0, 1e9)


class _UnusableLimits(ValueError):
    """A decoded `Limits` carrying a number outside `LIMIT_RATE_RANGE`/`LIMIT_CAPACITY_RANGE`, or
    one that does not narrow to a finite float. Treated exactly like a failed read: the note is
    `null`, the pause is untouched, and the tick carries on. Carries no venue text, only the
    field name that failed."""


def _safe_venue_text(value, max_len: int) -> str | None:
    """Untrusted venue text made safe to store and print: ASCII-escaped, every C0 control
    character and DEL removed, truncated. Never reaches a decision -- only a note."""
    if value is None:
        return None
    text = str(value).encode("ascii", "backslashreplace").decode("ascii")
    return "".join(ch for ch in text if ch not in _CONTROL_CHARS)[:max_len]


def _checked_float(value, lo: float, hi: float, field: str) -> float | None:
    """A decoded venue Decimal as a finite JSON number inside [lo, hi], or None when the venue
    did not send the field at all.

    Fix round 1, Important 1. The old `_as_float` was wrong twice over: `float(Decimal('1e400'))`
    is `inf`, which `json.dumps` writes as a bare `Infinity` that Postgres rejects for `jsonb`
    (failing `finish_run` at the end of the tick), and `float(Decimal('1e-400'))` underflows to
    `0.0`, which then divided by zero in the pause floor. Both Decimals are finite, so `dec`
    passes them through.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise _UnusableLimits(f"{field} is not a number") from None
    if not math.isfinite(number):
        raise _UnusableLimits(f"{field} is not finite as a float")
    if not lo <= number <= hi:
        raise _UnusableLimits(f"{field} outside [{lo}, {hi}]")
    return number


def page_pause_s(setting: float, read_refill_rate: Decimal | float | None) -> float:
    """The recorder's Kalshi page pause, floored by the venue's own read bucket, never lowered
    below the setting and never raised above `PAGE_PAUSE_CEILING_S` (§1.3; fix round 1,
    Important 2).

    A None, zero, negative or unnarrowable refill rate leaves the setting untouched: an
    unreadable bucket is not a licence to go faster. The rate is narrowed to a float *before*
    the sign test, because `float(Decimal('1e-400'))` is `0.0` and dividing by it raises.

    The ceiling is applied as `max(setting, PAGE_PAUSE_CEILING_S)` rather than a bare
    `min(..., PAGE_PAUSE_CEILING_S)`: the operator's own `kalshi_sleep_s` is trusted input and
    the task's headline invariant is that the pause is never lowered below it. The two agree for
    every setting at or under the ceiling, which is every real configuration (production runs
    0.05 s).
    """
    if read_refill_rate is None:
        return setting
    try:
        rate = float(read_refill_rate)
    except (TypeError, ValueError, OverflowError):
        return setting
    if not math.isfinite(rate) or rate <= 0:
        return setting
    floored = max(setting, 1.0 / rate)
    if floored <= PAGE_PAUSE_CEILING_S:
        return floored
    log.warning("kalshi read bucket implies a %.1f s page pause; clamped to %.1f s",
                floored, PAGE_PAUSE_CEILING_S)
    return max(setting, PAGE_PAUSE_CEILING_S)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cadence_in_force(now: datetime, kickoffs: list[Kickoff], tz: str) -> int:
    """The shortest cadence any sport is on for this tick, from the same `interval_for` the
    per-source `_due` checks use. That is how soon the next tick's fetches come due, so it is the
    wall clock this tick's fetch, normalization and pricing have to fit inside."""
    intervals = [i for i in (interval_for(sport, now, kickoffs, tz) for sport in SPORTS)
                 if i is not None]
    return min(intervals, default=DEFAULT_CADENCE_S)


def pricing_budget(price_budget_s: int, cadence_s: int, elapsed_s: float) -> tuple[float, bool]:
    """The pricing budget this tick may actually spend, and whether the cadence capped it.

    `elapsed_s` is the monotonic time the tick has already spent fetching and normalizing.
    Below `PRICE_BUDGET_FLOOR_S` the tick overruns its cadence by design: scoring nothing at all
    would leave the gate variant unmeasured on exactly the busiest ticks.
    """
    effective = max(PRICE_BUDGET_FLOOR_S,
                    min(price_budget_s, cadence_s - elapsed_s - PRICE_BUDGET_MARGIN_S))
    return effective, effective < price_budget_s


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
    # Fix 49. `app-run` went 78 MiB -> 1.82 GiB across one tick on 2026-09-12 and the only way
    # to see it was ssh and `docker stats`; this puts the same number on Pulse's vitals. The
    # `phase` label is there because the hourly settle job shares this process (controller
    # addendum, 01:20 CT): a sample taken at the end of a tick says "tick", so a future sample
    # at the end of a settle run can say "settle" without the two series colliding.
    rss = telemetry.rss_mb()
    if rss is not None:
        samples.append(("recorder.rss_mb", round(rss, 1), {"phase": "tick"}))
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


#: Ruling B-I8, and `harness/db/models.py::ParlayLegProb`'s docstring verbatim: "once per
#: recorder tick while a card is placed or alive and its game is inside the in-progress window".
#: `book_p` is DraftKings' own live implied probability for the leg's outcome when the feed
#: carries one, from the same `(game_id, market_type, outcome)` key the leg was priced from. The
#: `market_type` mapping ("ml" -> "moneyline") cannot be a bind parameter inside the correlated
#: subquery, so the leg CTE resolves it once and both reads share `leg.mt`.
_LEG_PROB_ROWS = text("""
    with leg as (
        select l.id, l.game_id, l.side_team_id, l.side,
               case l.market_type when 'ml' then 'moneyline' else l.market_type end as mt
        from parlay_legs l
        join parlay_cards c on c.id = l.card_id
        join games g on g.id = l.game_id
        where c.status in ('placed', 'alive') and g.status = 'in_progress'
    )
    select leg.id as leg_id, f.fair_p as sharp_p,
           (select 1.0 / o.price_decimal from odds_snapshots o
             where o.book = 'draftkings' and o.game_id = leg.game_id
               and o.market_type = leg.mt
               and o.outcome_team_id is not distinct from leg.side_team_id
               and o.outcome_side is not distinct from leg.side
               and o.price_decimal > 0
             order by o.fetched_at desc limit 1) as book_p
    from leg
    join lateral (
        select fv.fair_p from fair_values fv
        where fv.game_id = leg.game_id and fv.market_type = leg.mt
          and fv.outcome_team_id is not distinct from leg.side_team_id
          and fv.outcome_side is not distinct from leg.side
        order by fv.created_at desc limit 1
    ) f on true
""")


#: The prop source's candidate events (3.2), one sport at a time.
#:
#: Bound: `ix_games_sport_kick (sport, kickoff_utc)` seeks one sport's games inside the 24 h
#: kickoff window, and `PROP_EVENT_SCAN_LIMIT` caps the read whatever the slate. The `exists`
#: is the "priced signal" half of the watched rule: `ix_venue_markets_game_id` finds the game's
#: markets and `ix_signal_market_created (venue_market_id, created_at)` the recent signals on
#: them. `signals.fair_source` is the signal's own column, so no gap-snapshot hop is needed to
#: tell a direct fair from a consensus one.
_PROP_EVENT_CANDIDATES = text("""
    select g.id as game_id, g.sport, g.odds_api_event_id as event_id, g.kickoff_utc,
           g.home_team_id, g.away_team_id,
           th.abbreviation as home_abbr, ta.abbreviation as away_abbr,
           exists (select 1 from venue_markets vm
                    join signals s on s.venue_market_id = vm.id
                    where vm.game_id = g.id and s.replay = false and s.decision = 'candidate'
                      and s.fair_source = 'direct'
                      and s.created_at > :since and s.created_at <= :now) as signal
    from games g
    left join teams th on th.sport = g.sport and th.id = g.home_team_id
    left join teams ta on ta.sport = g.sport and ta.id = g.away_team_id
    where g.sport = :sport and g.kickoff_utc > :now and g.kickoff_utc <= :window_end
      and g.odds_api_event_id is not null
    order by g.kickoff_utc
    limit :scan_limit
""")

#: The games the collector may fetch a summary for (4.3): one prop leg on a `placed` or `alive`
#: card, the game in progress or just final.
#:
#: Bound: the driving set is the placed/alive cards, which is at most one week's slots (tens of
#: rows); `ix_parlay_legs_card_seq (card_id, seq)` joins their legs and `games` resolves by
#: primary key. `final_ts` reads `ix_game_score_events_game_ts (game_id, ts desc)` for one game
#: (review I3: whether the final box score has landed is a *fetch* fact, kept in
#: `source_state['espn_final:<game_id>']`, never a change-only write timestamp).
_CARDED_GAMES = text("""
    select distinct on (g.id) g.id as game_id, g.sport, g.espn_event_id, g.status,
           (select max(e.ts) from game_score_events e
             where e.game_id = g.id and e.status = 'final') as final_ts
    from parlay_cards c
    join parlay_legs l on l.card_id = c.id
    join games g on g.id = l.game_id
    where c.status in ('placed', 'alive') and l.market_type = 'prop'
      and g.status in ('in_progress', 'final') and g.espn_event_id is not null
      and g.kickoff_utc >= :since
    order by g.id
    limit :scan_limit
""")

#: The carded players of one game, and the ESPN athlete id each one is keyed by in the box
#: score. Bound: one game's legs (`ix_parlay_legs_card_seq` after the card filter) and `players`
#: by primary key. Rows are written for these players and for nobody else (4.3).
_CARDED_PLAYERS = text("""
    select distinct l.player_id, p.espn_id
    from parlay_cards c
    join parlay_legs l on l.card_id = c.id
    join players p on p.id = l.player_id
    where c.status in ('placed', 'alive') and l.market_type = 'prop'
      and l.game_id = :game_id and l.player_id is not null
""")

#: The value each carded player's stat currently stands at, so only a *change* is written (the
#: `game_score_events` rule, 4.3). Bound: one game and its carded players on
#: `ix_player_stat_game_player_ts (game_id, player_id, ts desc)`; `, id desc` breaks a tie
#: between two rows written at the same instant.
_NEWEST_STATS = text("""
    select distinct on (player_id, stat) player_id, stat, value
    from player_stat_events
    where game_id = :game_id and player_id = any(:player_ids)
    order by player_id, stat, ts desc, id desc
""")

#: The drafts the reprice may touch (2.3): `proposed` only, this Chicago week only. Bound:
#: `ix_parlay_cards_week (year, week)` and the limit the caller passes,
#: `2 sports x (1 + lottery_cards_max)`. A placed card is never in this result.
_PROPOSED_CARDS = text("""
    select c.id
    from parlay_cards c
    where c.status = 'proposed' and c.year = :year and c.week = :week
    order by c.id
    limit :card_limit
""")


class Recorder:
    #: Class attribute, not a module one, so a test can monkeypatch `Recorder._LEG_PROB_ROWS`.
    _LEG_PROB_ROWS = _LEG_PROB_ROWS
    #: The phase 4.6 sources' queries, class attributes for the same reason.
    _PROP_EVENT_CANDIDATES = _PROP_EVENT_CANDIDATES
    _CARDED_GAMES = _CARDED_GAMES
    _CARDED_PLAYERS = _CARDED_PLAYERS
    _NEWEST_STATS = _NEWEST_STATS
    _PROPOSED_CARDS = _PROPOSED_CARDS

    def __init__(self, settings: Settings, session_factory: sessionmaker, odds: OddsApiClient, espn: EspnClient,
                 kalshi: KalshiPublic, clock: Callable[[], datetime] = utcnow,
                 monotonic: Callable[[], float] = time.monotonic,
                 limits_reader=None):
        self.s = settings
        self.session_factory = session_factory
        self.odds, self.espn, self.kalshi = odds, espn, kalshi
        self.clock, self.monotonic = clock, monotonic
        # Ruling A-C3. A `KalshiReader` (GET-only, signed) when `build_recorder` found both
        # production key files, else None -- on the Mac, in tests and in any container without
        # the two bind mounts the recorder simply has no authenticated reader and no limits.
        self._limits_reader = limits_reader
        self._venue_limits = None            # the newest decoded Limits, or None
        self._limits_fields: dict | None = None   # its validated note fields, minus age_s
        self._limits_read_at: datetime | None = None        # wall clock of the last success
        # The cadence runs on the monotonic clock, not the wall clock (fix round 1, Minor): a
        # backwards NTP step would otherwise defer the next read by the size of the jump.
        self._limits_attempted_mono: float | None = None
        # 401/403 only (§9.4); reset by any successful read. Task 10's OutageCounter subsumes
        # this integer -- do not import it here, it does not exist yet, and no paper process
        # writes `venue_status` (ruling D11).
        self._limits_auth_errors = 0
        # I7: last known-good body per (source, endpoint); avoids re-reading multi-MB JSONB every tick.
        self._last_good: dict[tuple[str, str], dict | list] = {}
        # Phase 5: the NWS client, built on first use and closed with the recorder. One client
        # for the life of the process, like every other feed client here; the source itself is
        # guarded so this is often never built at all.
        self._nws: NwsClient | None = None
        # A forced tick (deploy verification) ignores every per-source interval for that one tick.
        self._force = False
        # Task 12b: the build_sha deploy check runs once per process, at the first tick.
        self._startup_checked = False
        # Phase 4.6: process-lifetime counters the fun-ticket sources keep. `prop_skipped_budget`
        # is the month's allocation refusing a tick (3.2), `player_unmatched` the prop outcomes
        # the normalizer could not resolve to a rostered player (4.2), `stat_missing` the carded
        # players a fetched box score did not list (4.3). Each tick also reports its own numbers
        # in `ctx`; these are the running totals a Pulse rule and the weekly report read.
        self.counters = {"prop_skipped_budget": 0, "player_unmatched": 0, "stat_missing": 0}

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

    # ---- the venue's own rate limits (ruling A-C3) ----------------------------------
    @staticmethod
    def _limits_fields_from(limits, pause_s: float, read_at: datetime) -> dict:
        """The `runs.notes->'venue_limits'` block minus `age_s`: numeric and enum fields only,
        never the raw body (`Limits.raw` is deliberately not read here). `tier` is sanitized
        venue text; every number is range-checked, so this raises `_UnusableLimits` rather than
        returning something a `jsonb` column or a division would choke on."""
        return {"tier": _safe_venue_text(limits.tier, _TIER_MAX_LEN),
                "read_refill_rate": _checked_float(limits.read_refill_rate, *LIMIT_RATE_RANGE,
                                                   "read_refill_rate"),
                "read_capacity": _checked_float(limits.read_capacity, *LIMIT_CAPACITY_RANGE,
                                                "read_capacity"),
                "write_refill_rate": _checked_float(limits.write_refill_rate, *LIMIT_RATE_RANGE,
                                                    "write_refill_rate"),
                "write_capacity": _checked_float(limits.write_capacity, *LIMIT_CAPACITY_RANGE,
                                                 "write_capacity"),
                "page_pause_s": pause_s,
                "read_at": read_at.isoformat()}

    def _limits_note(self, now: datetime) -> dict | None:
        """The stored fields plus `age_s`, seconds since the last successful read, so the Health
        block shows how stale the block is rather than implying it was just refreshed (fix
        round 1, Minor)."""
        if self._limits_fields is None or self._limits_read_at is None:
            return None
        return {**self._limits_fields,
                "age_s": round((now - self._limits_read_at).total_seconds(), 3)}

    def _read_limits(self, now: datetime, ctx: dict) -> None:
        """`GET /account/limits` at the first tick and hourly after it, on the signed GET-only
        reader (§1.3, ruling A-C3, roadmap pre-loaded decision 5).

        This is the only call that writes a `venue_requests` row in production, which is what
        makes the §3 paper-posture tripwire non-vacuous.

        Three things are deliberate here. **It can never fail a tick**: the whole step -- the
        call, the decode, the range checks, the note and the pause derivation -- sits inside one
        `try`, and nothing after it can raise (fix round 1, Important 1: the success path used to
        sit outside, so a finite-but-extreme venue rate raised `ZeroDivisionError` straight out
        of `maybe_tick` and left the run row unfinished). **The cadence gates the attempt, not the
        success**: a 401 at a 30 s heartbeat would otherwise be 120 signed failures an hour,
        inflating the §9.4 auth-error count and hammering the venue, so a failed read waits out
        the hour like a successful one. **The pause is re-derived from the setting**, never from
        the pause currently in force, so repeated reads cannot ratchet it upwards.

        Nothing here logs the exception object or a headers mapping (Task 5's review): an httpx
        error carries `.request`, whose headers hold a live signature. The class name and the
        constant path are all that go to the log.
        """
        if self._limits_reader is None:
            return
        try:
            mono = self.monotonic()
            due = (self._limits_attempted_mono is None
                   or (mono - self._limits_attempted_mono) >= LIMITS_REFRESH_S)
            if not due:
                # Between reads the note still reports the limits actually in force, so the
                # Health block does not blink to null for 59 minutes out of every 60. `age_s`
                # says how old the reading is.
                ctx["venue_limits"] = self._limits_note(now)
                return
            # Set before the call, so a raising read still consumes the hour.
            self._limits_attempted_mono = mono
            limits = self._limits_reader.get_account_limits()
            fields = self._limits_fields_from(
                limits, page_pause_s(self.s.kalshi_sleep_s, limits.read_refill_rate), now)
            pause_s = fields["page_pause_s"]
        except Exception as e:  # noqa: BLE001 - a venue read never fails the tick
            log.warning("kalshi limits read failed on /account/limits: %s", type(e).__name__)
            if getattr(e, "status", None) in (401, 403):
                self._limits_auth_errors += 1
            ctx["warnings"].append(
                {"venue_limits": _safe_venue_text(repr(e), _LIMITS_WARNING_MAX_LEN)})
            # The pause and the last known limits are both left exactly as they were.
            ctx["venue_limits"] = None
            return
        # Nothing below this line can raise: `fields` is built and range-checked above.
        self._limits_auth_errors = 0
        self._venue_limits = limits
        self._limits_fields = fields
        self._limits_read_at = now
        self.kalshi._sleep_s = pause_s
        ctx["venue_limits"] = self._limits_note(now)

    def close(self) -> None:
        """Release the limits reader's httpx clients (fix round 1, Minor).

        The scheduler's recorder lives as long as the process and never needs this; the one-shot
        `harness tick-once` path does, or it leaks a client until exit. Never raises: this runs
        in a `finally` after a tick whose result matters more than the cleanup.
        """
        if self._nws is not None:
            try:
                self._nws.close()
            except Exception:  # noqa: BLE001
                log.warning("closing the nws client failed")
            self._nws = None
        transport = getattr(self._limits_reader, "_transport", None)
        if transport is None:
            return
        try:
            transport.close()
        except Exception:  # noqa: BLE001
            log.warning("closing the kalshi limits transport failed")

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

    def _weather(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff],
                 budget: _Budget, ctx: dict) -> None:
        """The NWS forecast source (addendum §1.2), last in the fetch phase and twice guarded.

        Rulings A-I7 and B-I10. `cadence.py` returns 20 s for NFL 60-100 minutes before kickoff,
        which is the most valuable recording window the harness has; 120 s applies inside a game
        window. Twenty seconds of forecast plus a five-second 429 retry in either of those pushes
        the tick past its cadence and `max_instances=1, coalesce=True` then drops the next tick
        outright. A 72-hour forecast tolerates the gap; the tape does not.

        Nothing here can fail a tick: the whole call sits inside one `try` and a failure is a
        warning on the run.
        """
        # The budget helper falls back to 900 when every sport is quiet. That is a
        # pricing allowance, not permission to fetch NWS on the 30 s heartbeat.
        intervals = [interval_for(sport, now, kickoffs, self.s.tz_local) for sport in SPORTS]
        cadence = min((i for i in intervals if i is not None), default=None)
        if cadence not in ALLOWED_CADENCES:
            ctx["weather"] = {"skipped": f"cadence {cadence}"}
            return
        if budget.remaining_s() < MIN_TICK_REMAINING_S:
            ctx["weather"] = {"skipped": "tick budget"}
            return
        source_budget = _Budget(int(min(self.s.nws_budget_s, budget.remaining_s())),
                                self.monotonic)
        try:
            # Built inside the guard, not before it (Minor 10): a failure constructing the
            # client is exactly the kind of thing "nothing here can fail a tick" above promises
            # to confine to a warning on this source, not let escape into the tick's outer
            # handler and skip the rest of the fetch phase.
            if self._nws is None:
                self._nws = NwsClient(self.s)
            counts = run_weather_source(session, run.id, self._nws, self.s, now,
                                        source_budget, ctx)
            ctx["weather"] = counts
            # Only mark the tick "fetched" when the pass actually reached the network (an
            # hourly-forecast GET was attempted for at least one game): every other source in
            # this file sets `ctx["fetched"]` behind its own due/guard check, never
            # unconditionally, and a due list of nothing-to-fetch (or every game a dome or
            # missing its stadium) must still leave an otherwise-quiet tick "skipped".
            if counts.get("fetched", 0) > 0:
                ctx["fetched"] = True
        except Exception as e:  # noqa: BLE001 - a forecast never fails a tick
            log.exception("weather source failed")
            session.rollback()
            ctx["warnings"].append({"weather": repr(e)})
            ctx["weather"] = {"error": type(e).__name__}

    def _leg_probs(self, session: Session, run: Run, now: datetime, ctx: dict) -> None:
        """"Sharps say NN %" per live parlay leg (spec §2.5 item 1, ruling B-I8).

        One row per leg per tick while the card is placed or alive and its game is in progress.
        `parlay_leg_probs` is keyed `(leg_id, ts)`, so a repeated tick at the same instant is an
        upsert rather than a duplicate-key error that would fail the tick. Nothing here can fail
        a tick: this is the fun-money surface's history bar, not the tape.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from harness.db.models import ParlayLegProb

        try:
            rows = [{"leg_id": r.leg_id, "ts": now, "sharp_p": r.sharp_p, "book_p": r.book_p}
                    for r in session.execute(self._LEG_PROB_ROWS)]
            if rows:
                session.execute(pg_insert(ParlayLegProb).values(rows)
                                .on_conflict_do_nothing(index_elements=["leg_id", "ts"]))
            ctx["leg_probs"] = len(rows)
        except Exception as e:  # noqa: BLE001 - the fun-money bar never fails a tick
            log.exception("parlay leg probs failed")
            session.rollback()
            ctx["warnings"].append({"leg_probs": repr(e)})
            ctx["leg_probs"] = 0

    # ---- phase 4.6: the fun-ticket sources -------------------------------------------
    def _prop_events(self, session: Session, now: datetime):
        """This tick's prop candidates: the database rows by event id, the `PropEvent`s the two
        cadence functions read, and the loaded parlay policy.

        One bounded read per sport (`_PROP_EVENT_CANDIDATES`); the watched rule itself is pure
        and lives in `cadence.py`, so what is watched can be reasoned about without a database.
        """
        from harness.parlay.config import load_config

        config = load_config()
        rows: dict[str, object] = {}
        events: list[PropEvent] = []
        for sport in SPORTS:
            for row in session.execute(self._PROP_EVENT_CANDIDATES, {
                    "sport": sport, "now": now,
                    "window_end": now + timedelta(hours=config.props.prop_window_hours),
                    "since": now - timedelta(hours=config.pool_window_hours),
                    "scan_limit": PROP_EVENT_SCAN_LIMIT}):
                rows[row.event_id] = row
                events.append(PropEvent(row.event_id, row.sport, row.kickoff_utc,
                                        row.home_abbr, row.away_abbr, bool(row.signal)))
        return rows, events, config

    def _watched_prop_events(self, session: Session, now: datetime):
        """`(rows by event id, watched events)` -- the same set `_props` fetches from, exposed
        so a test and a future diagnostic can read the watched set without spending a credit."""
        rows, events, config = self._prop_events(session, now)
        return rows, prop_events_watched(now, events,
                                         window_h=config.props.prop_window_hours,
                                         per_sport=config.props.prop_events_max,
                                         anchors=frozenset(config.anchors))

    def _prop_rosters(self, session: Session, run: Run, now: datetime, rows: list,
                      source_budget: "_Budget", ctx: dict) -> int:
        """One ESPN roster per watched team per week (addendum 4.1, 4.2; plan review IM-7).

        **Nothing else fills `players`**, and without `players` the normalizer's resolver matches
        no prop outcome at all: every row keeps `player_id` null and no prop leg is ever built.
        Bounded by the watched set (at most 16 events x 2 teams a sport) and by the seven-day
        skip per team, and it shares the prop calls' sub-budget so the two together can never
        exceed it. A failure here is a warning on the run, never a tick.
        """
        from harness.normalize.players import parse_roster, upsert_players

        fetched, seen = 0, set()
        for row in rows:
            for team_id in (row.home_team_id, row.away_team_id):
                if team_id is None or (row.sport, team_id) in seen:
                    continue
                seen.add((row.sport, team_id))
                key = f"espn_roster:{row.sport}:{team_id}"
                last = store.get_source_state(session, key)
                if last is not None and now - last < ROSTER_MAX_AGE:
                    continue
                if not source_budget.ok():
                    log.info("props: sub-budget spent before the roster of %s team %s",
                             row.sport, team_id)
                    return fetched
                try:
                    r = self.espn.fetch_roster(row.sport, team_id)
                    store.store_raw(session, run.id, "espn",
                                    f"{_ESPN_PATH[row.sport].rsplit('/', 1)[0]}/teams/{team_id}/roster",
                                    {"team": str(team_id)}, r)
                    ctx["n"] += 1
                    ctx["fetched"] = True
                    if r.status != 200:
                        ctx["warnings"].append({key: f"http {r.status}"})
                        continue
                    upsert_players(session, row.sport, team_id, parse_roster(r.body), now)
                    store.set_source_state(session, key, now)
                    fetched += 1
                except Exception as e:  # noqa: BLE001 - a roster never fails a tick
                    log.exception("espn roster failed")
                    ctx["warnings"].append({key: repr(e)})
        return fetched

    def _prop_attempts(self, session: Session, now: datetime, events: list[PropEvent]):
        """`({event_id: last attempt}, {resting after repeated failures}, {ever failed})`.

        One batched `source_state` read (review I4 and C1). The last **attempt** is the newer of
        the success stamp `odds_prop:<id>` and the failure stamp `odds_prop_fail:<id>`, so a
        failing event waits its turn in the rotation exactly like a fetched one; the failure
        key's `credits_used` column carries the consecutive-failure count, and an event that has
        failed `PROP_FAIL_BACKOFF_AFTER` times in a row rests for `PROP_FAIL_BACKOFF`.
        """
        keys = []
        for event in events:
            keys.append(f"odds_prop:{event.event_id}")
            keys.append(f"odds_prop_fail:{event.event_id}")
        rows = store.get_source_rows(session, keys)
        attempts: dict[str, datetime] = {}
        rested: set[str] = set()
        failed: set[str] = set()
        for event in events:
            ok = rows.get(f"odds_prop:{event.event_id}")
            bad = rows.get(f"odds_prop_fail:{event.event_id}")
            stamps = [row[0] for row in (ok, bad) if row is not None and row[0] is not None]
            if stamps:
                attempts[event.event_id] = max(stamps)
            if bad is not None:
                failed.add(event.event_id)
            failures = (bad[1] or 0) if bad is not None else 0
            if (failures >= PROP_FAIL_BACKOFF_AFTER and bad is not None
                    and now - bad[0] < PROP_FAIL_BACKOFF):
                rested.add(event.event_id)
        return attempts, rested, failed

    def _stamp_prop_failure(self, session: Session, run: Run, event_id: str, now: datetime,
                            charged: int, month_key: str, ctx: dict, counts: dict) -> None:
        """Record a failed prop call durably: the event's failure stamp and, when the venue
        charged for the call anyway, the month's credits (review I1 and I4).

        Its own transaction, because the caller has just rolled one back: the accounting for a
        metered call must survive whatever broke the call's own writes, or the allocation guard
        reads a number that is too low for the rest of the month.
        """
        try:
            store.add_source_credits(session, f"odds_prop_fail:{event_id}", now, 1)
            if charged:
                store.add_source_credits(session, month_key, now, charged)
            self._checkpoint(session, run)
            if charged:
                ctx["credits"] += charged
                counts["credits"] += charged
        except Exception:  # noqa: BLE001 - accounting never fails a tick either
            log.exception("prop failure accounting failed")
            session.rollback()

    def _prop_watched(self, session: Session, now: datetime, events: list[PropEvent], config):
        """`(watched, events, attempts, failed, rested)` -- the watched set with the rested events
        removed, so a dead event frees its slot for a healthy one instead of holding it."""
        attempts, rested, failed = self._prop_attempts(session, now, events)
        if rested:
            events = [event for event in events if event.event_id not in rested]
        watched = prop_events_watched(now, events, window_h=config.props.prop_window_hours,
                                      per_sport=config.props.prop_events_max,
                                      anchors=frozenset(config.anchors))
        return watched, events, attempts, failed, len(rested)

    def _props(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff],
               budget: "_Budget", ctx: dict) -> None:
        """DraftKings player props for the watched events, and the weekly roster fetch that
        makes them resolvable at all (addendum 3.2, 4.1).

        Four guards before a credit is spent. **The cadence value**: any allowed cadence (300 s
        or 900 s: `ALLOWED_CADENCES`), never the 120 s game window, never the 20 s pre-kickoff
        window, never the quiet hours; the source's own 900 s stamp bounds it to one pass per
        period on any day (user decision 2026-09-14, journal 209). **The period itself** (fix
        round 1, review C1): `maybe_tick` runs on the 30 s
        heartbeat, so the pass is gated on its own `source_state` stamp exactly like every other
        paid source here, and `prop_events_due` filters the rotation by the same interval.
        `is_due` and not `self._due`: a forced deploy tick must not spend 144 credits, the same
        rule the alternates source keeps. **The month's own allocation**: `x-requests-last`
        summed per Chicago month in `source_state['odds_props:<YYYY-MM>']` against
        `odds_prop_monthly_credits`, dormant for the rest of the month at the allocation. **The
        strategy feed first**: skipped whenever the feed's remaining credits are under
        `credits_watch_fraction` of the month's tier, and an unknown balance is never a licence
        to spend.

        The free ESPN roster half runs **above** the two paid guards (review M3): the identity
        map every other task reads must not go stale in the same moment the paid feed goes
        dormant. It has its own slice of the 40 s sub-budget (M2), and the two halves together
        can never exceed it.

        Each event's raw row, its stamp and its credits are committed **per event** (review I1):
        a database failure on call 4 keeps calls 1-3 and their credits instead of rolling back a
        pass the venue has already charged for. Nothing here can fail a tick.
        """
        intervals = [interval_for(sport, now, kickoffs, self.s.tz_local) for sport in SPORTS]
        cadence = min((i for i in intervals if i is not None), default=None)
        if cadence not in ALLOWED_CADENCES:
            ctx["props"] = {"skipped": f"cadence {cadence}"}
            return
        if not is_due(store.get_source_state(session, PROPS_STATE_KEY), now, PROPS_CADENCE_S):
            ctx["props"] = {"skipped": "interval"}
            return
        month_key = f"odds_props:{chicago_day(now):%Y-%m}"
        try:
            counts: dict = {"calls": 0, "credits": 0, "events": 0, "unmatched": 0,
                            "rosters": 0, "rested": 0, "event_ids": []}
            # The `ALTERNATES_BUDGET_S` pattern: a slow Odds API can spend this much of the tick
            # and no more, whatever is left of the tick budget when props are reached.
            source_budget = _Budget(int(min(PROPS_BUDGET_S, max(0.0, budget.remaining_s()))),
                                    self.monotonic)
            roster_budget = _Budget(
                int(min(PROPS_ROSTER_BUDGET_S, max(0.0, source_budget.remaining_s()))),
                self.monotonic)
            rows, events, config = self._prop_events(session, now)
            props = config.props
            watched, events, attempts, failed, rested = self._prop_watched(
                session, now, events, config)
            counts["events"], counts["rested"] = len(watched), rested
            counts["rosters"] = self._prop_rosters(
                session, run, now, [rows[w.event_id] for w in watched], roster_budget, ctx)
            spent = store.get_source_credits(session, month_key) or 0
            if spent >= self.s.odds_prop_monthly_credits:
                self.counters["prop_skipped_budget"] += 1
                ctx["props"] = {"skipped": "budget"}
                return
            remaining = ctx.get("remaining")
            if (remaining is None
                    or remaining < self.s.credits_watch_fraction * self.s.odds_monthly_credits):
                ctx["props"] = {"skipped": "remaining"}
                return
            due = prop_events_due(now, events, attempts, window_h=props.prop_window_hours,
                                  per_sport=props.prop_events_max,
                                  calls=props.prop_calls_per_tick, interval_s=PROPS_CADENCE_S,
                                  anchors=frozenset(config.anchors))
            for event_id in due:
                if not source_budget.ok():
                    log.info("props: sub-budget spent after %d of %d due calls",
                             counts["calls"], len(due))
                    break
                sport_key = SPORTS[rows[event_id].sport]
                # Counted before the call, not after: a call that failed cost the same slice of
                # the sub-budget as one that answered, which is what the budget is measuring.
                counts["calls"] += 1
                charged = 0
                try:
                    r = self.odds.fetch_event_props(sport_key, event_id)
                    credits = parse_credit_headers(r.headers)
                    # Read before anything can fail: the venue has already charged for this call.
                    charged = credits.last
                    store.store_raw(session, run.id, "odds_api",
                                    f"/sports/{sport_key}/events/{event_id}/odds",
                                    {"markets": "props"}, r)
                    if r.status == 200:
                        store.set_source_state(session, f"odds_prop:{event_id}", now)
                        if event_id in failed:
                            # A success clears the consecutive-failure count (I4).
                            store.reset_source_credits(session, f"odds_prop_fail:{event_id}", now)
                        counts["event_ids"].append(event_id)
                    else:
                        # A warning, not an error: the fun surface going quiet degrades a tick,
                        # it does not fail one. The event goes to the back of the rotation.
                        ctx["warnings"].append({f"odds_prop:{event_id}": f"http {r.status}"})
                        store.add_source_credits(session, f"odds_prop_fail:{event_id}", now, 1)
                    store.add_source_credits(session, month_key, now, charged)
                    # Durable per event (I1): calls 1..k-1 and their credits survive a failure
                    # at call k, so the month's counter never under-reads what was spent.
                    self._checkpoint(session, run)
                    ctx["n"] += 1
                    ctx["fetched"] = True
                    ctx["credits"] += charged
                    counts["credits"] += charged
                    charged = 0
                except Exception as e:  # noqa: BLE001 - one event never fails a tick
                    log.exception("odds props failed")
                    session.rollback()
                    ctx["warnings"].append({f"odds_prop:{event_id}": repr(e)})
                    self._stamp_prop_failure(session, run, event_id, now, charged, month_key,
                                             ctx, counts)
            # The period is spent once the rotation has run, whatever it returned (the
            # controller's ruling on C1): the next heartbeat 30 s from now finds this stamp and
            # skips. Committed here rather than left to `finish_run`, so a later failure in the
            # tick cannot roll the gate back and re-open the rotation.
            store.set_source_state(session, PROPS_STATE_KEY, now)
            self._checkpoint(session, run)
            ctx["props"] = counts
        except Exception as e:  # noqa: BLE001 - the prop source never fails a tick
            log.exception("prop source failed")
            session.rollback()
            ctx["warnings"].append({"props": repr(e)})
            ctx["props"] = {"error": type(e).__name__}

    def _player_stats(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff],
                      budget: "_Budget", ctx: dict) -> None:
        """ESPN box scores for the carded players of the games on the field (addendum 4.3).

        The game-window tick only (120 s), at most `PLAYER_STATS_MAX_GAMES` games inside a 10 s
        sub-budget, oldest-fetched first. Rows are written **for carded players only** and
        **only on a change** (the `game_score_events` rule). ESPN's summary carries no per-stat
        timestamp and sequential polls of one endpoint return the source's current state, so a
        later lower value is a **correction**, not an out-of-order arrival: `correction = true`,
        `source_ts` null, and the surface shows the fetch age. A player the update does not list
        writes nothing and is counted `stat_missing`.

        After a game goes final the fetch continues each tick until the final box score lands --
        a stat row written at or after the moment the game went final -- and for at most six
        hours (`FINAL_RETRY_CEILING`); a leg still pending after that is the hung-leg state the
        `parlay_pending_final` WATCH rule reports. Never fails a tick.
        """
        intervals = [interval_for(sport, now, kickoffs, self.s.tz_local) for sport in SPORTS]
        cadence = min((i for i in intervals if i is not None), default=None)
        if cadence != PLAYER_STATS_CADENCE_S:
            ctx["player_stats"] = {"skipped": f"cadence {cadence}"}
            return
        try:
            source_budget = _Budget(int(min(PLAYER_STATS_BUDGET_S, max(0.0, budget.remaining_s()))),
                                    self.monotonic)
            candidates = session.execute(self._CARDED_GAMES, {
                "since": now - timedelta(hours=24), "scan_limit": CARDED_GAMES_SCAN_LIMIT}).all()
            due = []
            for row in candidates:
                if row.status == "final":
                    if row.final_ts is None or now - row.final_ts > FINAL_RETRY_CEILING:
                        # Either the status has not reached `game_score_events` yet (next tick
                        # sees it) or the six-hour ceiling is spent.
                        continue
                    # Review I3: the stop condition is a *fetch* fact, never a write one. A
                    # final box score that repeats the last in-progress value writes nothing --
                    # the common case -- so comparing the newest `player_stat_events` row to
                    # `final_ts` never terminated and the game was re-fetched for six hours.
                    # `espn_final:<game_id>` is stamped when a post-final fetch actually carries
                    # a box score.
                    landed = store.get_source_state(session, f"espn_final:{row.game_id}")
                    if landed is not None and landed >= row.final_ts:
                        continue        # the final box score has landed; nothing more to ask for
                last = store.get_source_state(session, f"espn_summary:{row.game_id}")
                if not self._due(last, now, PLAYER_STATS_CADENCE_S):
                    continue
                due.append((last or _NEVER_FETCHED, row))
            due.sort(key=lambda item: item[0])
            counts = {"games": 0, "rows": 0, "corrections": 0, "missing": 0}
            for _last, row in due[:PLAYER_STATS_MAX_GAMES]:
                if not source_budget.ok():
                    log.info("player stats: sub-budget spent after %d games", counts["games"])
                    break
                try:
                    written, corrected, missing = self._collect_game_stats(
                        session, run, now, row, ctx)
                    counts["games"] += 1
                    counts["rows"] += written
                    counts["corrections"] += corrected
                    counts["missing"] += missing
                except Exception as e:  # noqa: BLE001 - one game never fails a tick
                    log.exception("espn summary failed")
                    ctx["warnings"].append({f"espn_summary:{row.game_id}": repr(e)})
            ctx["player_stats"] = counts
        except Exception as e:  # noqa: BLE001 - the collector never fails a tick
            log.exception("player stat source failed")
            session.rollback()
            ctx["warnings"].append({"player_stats": repr(e)})
            ctx["player_stats"] = {"error": type(e).__name__}

    def _collect_game_stats(self, session: Session, run: Run, now: datetime, row,
                            ctx: dict) -> tuple[int, int, int]:
        """One game's summary fetched, parsed and written as changes. Returns `(rows,
        corrections, carded players the update did not list)`."""
        from harness.db.models import PlayerStatEvent
        from harness.normalize.players import UNDECIDABLE, parse_scoring_tds, parse_summary_stats

        r = self.espn.fetch_summary(row.sport, row.espn_event_id)
        raw_id = store.store_raw(session, run.id, "espn",
                                 f"{_ESPN_PATH[row.sport].rsplit('/', 1)[0]}/summary",
                                 {"event": str(row.espn_event_id)}, r)
        ctx["n"] += 1
        ctx["fetched"] = True
        if r.status != 200:
            ctx["warnings"].append({f"espn_summary:{row.game_id}": f"http {r.status}"})
            return 0, 0, 0
        store.set_source_state(session, f"espn_summary:{row.game_id}", now)
        lines = parse_summary_stats(r.body)
        if row.status == "final" and lines:
            # Review I3: the box score landed, whether or not any value changed.
            store.set_source_state(session, f"espn_final:{row.game_id}", now)
        carded = {str(c.espn_id): c.player_id
                  for c in session.execute(self._CARDED_PLAYERS, {"game_id": row.game_id})}
        if not carded:
            return 0, 0, 0
        previous = {(p.player_id, p.stat): Decimal(str(p.value))
                    for p in session.execute(self._NEWEST_STATS,
                                             {"game_id": row.game_id,
                                              "player_ids": sorted(carded.values())})}
        scored = parse_scoring_tds(r.body)
        undecidable = scored.pop(UNDECIDABLE, 0)
        if undecidable:
            # Never a miss and never a credit: the leg stays pending and the count is logged so
            # the vocabulary can be widened against real bodies (plan review MI-2).
            log.info("player stats: %d undecidable scoring plays in game %s",
                     undecidable, row.game_id)
        listed, values = set(), {}
        for line in lines:
            player_id = carded.get(line.player_espn_id)
            if player_id is None:
                continue        # carded players only (4.3)
            listed.add(line.player_espn_id)
            values[(player_id, line.stat)] = line.value
        for espn_id in listed:
            # A listed player with no qualifying touchdown is a value-0 row, not an absent one:
            # the grader reads 0 as a miss and absence as `stat_missing` (Task 5 review, I3).
            values[(carded[espn_id], "anytime_td")] = Decimal(scored.get(espn_id, 0))
        written = corrections = 0
        for (player_id, stat), value in sorted(values.items()):
            old = previous.get((player_id, stat))
            if old is not None and old == value:
                continue        # change-only writes
            correction = old is not None and value < old
            session.add(PlayerStatEvent(game_id=row.game_id, player_id=player_id, ts=now,
                                        source_ts=None, stat=stat, value=value, source="espn",
                                        raw_id=raw_id, correction=correction))
            written += 1
            corrections += 1 if correction else 0
        missing = [espn_id for espn_id in carded if espn_id not in listed]
        if row.status == "final" and missing:
            # Review M1: a player absent from an *in-progress* box score is normal play (a
            # receiver with no catch yet), and counting that every 120 s made the counter
            # useless as a threshold. `stat_missing` is a carded player absent from a **final**
            # box score, which is the same fact Task 5's grading counter of that name records.
            self.counters["stat_missing"] += len(missing)
        return written, corrections, len(missing)

    def _parlay_reprice(self, session: Session, run: Run, now: datetime,
                        kickoffs: list[Kickoff], ctx: dict) -> None:
        """This week's drafts repriced in place (addendum 2.3).

        Any allowed cadence (300 s or 900 s: `ALLOWED_CADENCES`), never the 120 s game window,
        never the 20 s pre-kickoff window, never the quiet hours; the source's own 900 s stamp
        bounds it to one pass per period on any day (user decision 2026-09-14, journal 209).
        For every `proposed` card of the current Chicago week -- at most
        `2 sports x (1 + lottery_cards_max)` -- each leg is re-read at the newest DraftKings row
        for **its exact selection** (`odds_prop_snapshots` for a prop, `odds_snapshots` for a
        game line) and the card's payout, combined price and hold are recomputed by the build's
        own arithmetic. **A placed card is never touched** (EXPERIENCE-CONTRACTS 1) and **a
        leg's line is never changed**: a moved line leaves the price stale and the sheet's
        `LineMoved` path handles it.

        `offered` goes false only when the event is inside the prop window **and** the last
        successful prop fetch for it carried no row for the selection; outside the window
        nothing changes and the surface reads `not repriced - outside the price window`. A
        failed fetch is not evidence of removal, so the stamp itself has to be fresh. Fetches
        nothing and never fails a tick.
        """
        intervals = [interval_for(sport, now, kickoffs, self.s.tz_local) for sport in SPORTS]
        cadence = min((i for i in intervals if i is not None), default=None)
        if cadence not in ALLOWED_CADENCES:
            ctx["reprice"] = {"skipped": f"cadence {cadence}"}
            return
        # Review I2, with the controller's ruling on fix-round concern 1: once per 900 s
        # period, on the reprice's **own** stamp -- never on every 30 s heartbeat, and never
        # coupled to whether the prop rotation ran. It costs nothing to run and a dormant prop
        # feed is exactly when a draft's stored prices most need re-reading.
        if not is_due(store.get_source_state(session, REPRICE_STATE_KEY), now, PROPS_CADENCE_S):
            ctx["reprice"] = {"skipped": "interval"}
            return
        try:
            from harness.db.models import Game, OddsPropSnapshot, ParlayCard, ParlayLeg
            # The build's own arithmetic, imported rather than re-derived: 2.3 says the card's
            # numbers are "recomputed by the same rules as the build", and two copies of a devig
            # or a hold would be two answers.
            from harness.parlay.build import _devig, _hold
            from harness.parlay.config import load_config
            from harness.parlay.pricing import (american, decimal_from, newest_dk_price,
                                                newest_dk_prop_price)

            config = load_config()
            max_age = timedelta(minutes=config.leg_max_age_minutes)
            window = timedelta(hours=config.props.prop_window_hours)
            year, week = chicago_iso_week(now)
            cards = session.execute(self._PROPOSED_CARDS, {
                "year": year, "week": week,
                "card_limit": 2 * (1 + config.lottery_cards_max)}).all()
            counts = {"cards": 0, "legs": 0, "unoffered": 0}
            for card_row in cards:
                card = session.get(ParlayCard, card_row.id)
                legs = (session.query(ParlayLeg).filter_by(card_id=card.id)
                        .order_by(ParlayLeg.seq).all())
                if not legs:
                    continue
                # Review I2: a card whose inputs did not move is left exactly as it is --
                # including `dk_combined_at`, which is the freshness the surface shows.
                changed = False
                for leg in legs:
                    game = session.get(Game, leg.game_id)
                    if leg.market_type == "prop":
                        source = (session.get(OddsPropSnapshot, leg.odds_prop_snapshot_id)
                                  if leg.odds_prop_snapshot_id is not None else None)
                        # The `prop:<stat>` key lives on the snapshot, never on the leg, whose
                        # `market_type` is the string "prop" (Task 5 ruling); the family is the
                        # fallback when the row it was built from has aged out of the table.
                        market_type = (source.market_type if source is not None
                                       else f"prop:{leg.stat}")
                        price = newest_dk_prop_price(session, leg.game_id, market_type,
                                                     leg.player_id, leg.threshold, leg.side,
                                                     now, max_age)
                        if price is not None:
                            if (leg.dk_american != price.dk_american
                                    or decimal_from(leg.dk_decimal) != price.dk_decimal
                                    or leg.odds_prop_snapshot_id != price.odds_prop_snapshot_id
                                    or leg.dk_link != price.link or leg.dk_sid != price.sid):
                                leg.dk_american = price.dk_american
                                leg.dk_decimal = price.dk_decimal
                                leg.odds_prop_snapshot_id = price.odds_prop_snapshot_id
                                leg.dk_link, leg.dk_sid = price.link, price.sid
                                counts["legs"] += 1
                                changed = True
                            if leg.p_source == "book_devig":
                                other = newest_dk_prop_price(
                                    session, leg.game_id, market_type, leg.player_id,
                                    leg.threshold, PROP_OPPOSITE.get(leg.side or ""), now,
                                    max_age)
                                if other is not None:
                                    devigged = _devig(price.dk_decimal, other.dk_decimal)
                                    if (leg.p_at_build is None
                                            or decimal_from(leg.p_at_build) != devigged):
                                        leg.p_at_build = devigged
                                        changed = True
                        elif (game is not None
                              and timedelta(0) <= game.kickoff_utc - now <= window
                              and game.odds_api_event_id):
                            stamp = store.get_source_state(
                                session, f"odds_prop:{game.odds_api_event_id}")
                            if stamp is not None and now - stamp <= max_age and leg.offered:
                                leg.offered = False
                                changed = True
                    else:
                        price = newest_dk_price(session, leg.game_id,
                                                LEG_MARKET_TYPES.get(leg.market_type,
                                                                     leg.market_type),
                                                leg.side_team_id, leg.side, now, max_age)
                        # A game line whose newest row is at another number is a moved line, not
                        # a new price for this leg: the leg keeps the line it states.
                        if (price is not None and price.point == leg.threshold
                                and (leg.dk_american != price.dk_american
                                     or decimal_from(leg.dk_decimal) != price.dk_decimal
                                     or leg.odds_snapshot_id != price.odds_snapshot_id)):
                            leg.dk_american = price.dk_american
                            leg.dk_decimal = price.dk_decimal
                            leg.odds_snapshot_id = price.odds_snapshot_id
                            counts["legs"] += 1
                            changed = True
                    counts["unoffered"] += 0 if leg.offered else 1
                stake = decimal_from(card.stake)
                payout, true_p, sourced = stake, Decimal("1"), 0
                for leg in legs:
                    payout *= decimal_from(leg.dk_decimal)
                    if leg.p_source != "none" and leg.p_at_build is not None:
                        true_p *= decimal_from(leg.p_at_build)
                        sourced += 1
                fresh = {
                    "dk_payout_est": payout.quantize(Decimal("0.01")),
                    "true_prob_est": (true_p.quantize(Decimal("0.000001")) if sourced else None),
                    # A hold is only meaningful when every leg carries a sharp fair and the legs
                    # are independent (D4); the build's rule, unchanged.
                    "hold_est": (_hold(true_p, payout, stake)
                                 if card.p_source_min == "sharp" and not card.correlated
                                 else None),
                    "dk_combined_american": american(payout / stake)}
                for name, value in fresh.items():
                    current = getattr(card, name)
                    if current is None and value is None:
                        continue
                    if (current is None or value is None
                            or decimal_from(current) != decimal_from(value)):
                        setattr(card, name, value)
                        changed = True
                if changed:
                    # The one timestamp the surface reads as "this price is current".
                    card.dk_combined_at = now
                    counts["cards"] += 1
            # The period is spent once the pass has run, whatever it changed. Written in the
            # same transaction as the cards it just repriced, so the two can never disagree.
            store.set_source_state(session, REPRICE_STATE_KEY, now)
            ctx["reprice"] = counts
        except Exception as e:  # noqa: BLE001 - the reprice never fails a tick
            log.exception("parlay reprice failed")
            session.rollback()
            ctx["warnings"].append({"reprice": repr(e)})
            ctx["reprice"] = {"error": type(e).__name__}

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
            # Before the fetch phase, so this tick's Kalshi paging already runs at the floored
            # pause rather than the next one.
            self._read_limits(now, ctx)
            summaries: list[MarketSummary] = []
            # Bound before the try: an ESPN failure must still leave the pricing budget below a
            # cadence to work from, and no kickoffs is exactly what `interval_for` reads as the
            # quiet weekday period.
            kickoffs: list[Kickoff] = []
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
                # Phase 5: weather takes what is left of the fetch phase and never competes with
                # the tape. Twice guarded; see `_weather`.
                self._weather(session, run, now, kickoffs, budget, ctx)
                self._checkpoint(session, run)
                # Phase 5c: the live parlay leg-probability writer. Its own try/except never
                # raises out to this one; the checkpoint just gives it a clean session.
                self._leg_probs(session, run, now, ctx)
                self._checkpoint(session, run)
                # Phase 4.6: the three fun-ticket sources, in the plan's order. Each carries its
                # own cadence guard, its own sub-budget and its own try/except, so none of them
                # can fail a tick or take another source's time.
                self._props(session, run, now, kickoffs, budget, ctx)
                self._checkpoint(session, run)
                self._player_stats(session, run, now, kickoffs, budget, ctx)
                self._checkpoint(session, run)
                self._parlay_reprice(session, run, now, kickoffs, ctx)
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
            # 4.2: the prop outcomes the normalizer could not resolve to exactly one rostered
            # player. It is counted where it is decided -- inside `upsert_odds_rows`, which runs
            # after the fetch phase -- and folded into this tick's `ctx["props"]` here, so the
            # number the surface reads is the one this tick produced.
            # Review M8, for Task 18b: this is a *normalizer-pass* count. `normalize_new` works
            # through whatever raw rows it reaches inside its 30 s budget, so on a tick with a
            # backlog the number can include earlier ticks' bodies and exclude this one's. The
            # counter always gets it; `ctx["props"]["unmatched"]` only when props ran.
            prop_unmatched = ctx.get("prop_unmatched", 0)
            self.counters["player_unmatched"] += prop_unmatched
            if isinstance(ctx.get("props"), dict) and "unmatched" in ctx["props"]:
                ctx["props"]["unmatched"] = prop_unmatched
            if summaries:
                # Only price when this tick actually refreshed Kalshi markets, so gap snapshots
                # are computed against fresh quotes rather than stale ones from a skipped tick.
                # Price with the clock read *now*, not the tick's start time: the odds fetched a
                # few seconds into this tick carry fetched_at > run.started_at, and the book-line
                # loader's upper bound would otherwise fall back to the previous fetch (2-5 min old),
                # labelling every fair value stale.
                pricing_now = self.clock()
                # Amendment 4 fix round 1: `price_budget_s` is what pricing may spend, not what
                # it always gets. Fetch and normalization have already run, so pricing takes what
                # is left of the cadence in force less a margin, and never less than the floor.
                budget_s, budget_capped = pricing_budget(
                    self.s.price_budget_s,
                    cadence_in_force(pricing_now, kickoffs, self.s.tz_local),
                    self.monotonic() - started_mono)
                try:
                    pricing = price_and_signal(session, run.id, pricing_now, self.s, budget_s)
                    pricing["budget_s"] = budget_s
                    pricing["budget_capped"] = budget_capped
                    ctx["pricing"] = pricing
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
                                    "kalshi_trades_normalized": ctx.get("kalshi_trades_normalized", 0),
                                    "non_linear_cent": ctx.get("non_linear_cent", 0),
                                    "pricing": ctx.get("pricing", {}),
                                    "venue_limits": ctx.get("venue_limits"),
                                    "weather": ctx.get("weather"),
                                    "leg_probs": ctx.get("leg_probs"),
                                    "props": ctx.get("props"),
                                    "player_stats": ctx.get("player_stats"),
                                    "reprice": ctx.get("reprice")},
                             finished_at=self.clock())
            log.info("tick %s n=%d credits=%d errors=%d warnings=%d", status, ctx["n"], ctx["credits"],
                     len(ctx["errors"]), len(ctx["warnings"]))
            return run
