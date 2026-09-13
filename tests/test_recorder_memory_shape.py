"""Finding 49, round 2: the recorder tick at *production* body shape.

Round 1 (`tests/test_recorder_memory.py`) measured a tick whose bodies are two orders of
magnitude smaller than the live ones, and whose `/markets/trades` endpoint returns
`{"trades": []}`. Production on build 93dfb95 (2026-09-12, Saturday college window, 15 games in
progress) handles per tick:

* roughly three `/markets` pages of ~2.3 MB each (Kalshi pages at `limit=1000`, so ~2.3 KB a
  market once the rules text and the twenty-odd fields the harness never reads are on it), and
* of the order of a hundred `/markets/trades` pages of 100-320 KB each (~1,000 prints a page).

`raw_responses` for one 10-minute window: `kalshi /markets` 31 rows / 35.9 MB / max 2,269 KB;
`kalshi /markets/trades` 1,180 rows / 19.5 MB / max 321 KB.

That shape is the whole point. The recorder's memory is dominated by what the *normalize* phase
has to hold at once, and what it holds is `batch` whole jsonb bodies: `_drain_batch` materialized
its batch with `.all()` before handling the first row, so psycopg had already decoded every body
in it. At round-1 body sizes that is a rounding error; at production sizes one batch of
`kalshi_trades` is a hundred-thousand-plus decoded trade dicts, and the `RssAnon` series the
brief carries (121.8 -> 1,935.4 MiB over the first five ticks, then steps of ~1.2 GB per tick up
to 4,169,636 kB) is that allocation repeated every tick into an allocator that only returns an
arena once it empties.

Two tests here, deliberately split:

* `test_one_normalize_batch_does_not_hold_the_whole_batch_of_bodies` is the deterministic one.
  It seeds N production-shaped pending rows, neutralises the per-row handler, and measures the
  peak allocation of one `_drain_batch` call. The peak must not scale with N.
* `test_eight_production_shaped_ticks_do_not_grow_the_process` is the brief's acceptance, over
  eight ticks rather than twenty (controller ruling, 2026-09-13 -- see `TICKS`): eight real
  `Recorder.maybe_tick` calls at the body shape above, with the traced total, the per-tick peak
  and RSS compared between tick 5 and tick 8.

Run with `-s` to see the per-tick table and the top growing tracebacks.
"""

import gc
import json
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from sqlalchemy.orm import sessionmaker

from harness.db.models import FairValue, RawResponse, Signal, VenueMarket, VenueTrade
from harness.db.schema import ensure_partitions
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.matching.teams import seed_teams_from_espn
from harness.normalize import runner as runner_mod
from harness.recorder.tick import Recorder
from harness.strategy.variants import load_variants, register_variants
from harness.telemetry import rss_mb
from harness.venues.kalshi.public import KalshiPublic

from scripts.measure_tick_memory import _DRIVER_CACHE, synthetic_day, synthetic_teams

from tests.test_recorder_memory import VARIANTS_DIR, _EVENT_ID

#: Excluded from the traced total this test asserts on, with the reason each one is not the
#: recorder's retention:
#:
#: * `psycopg/_queries.py` and `psycopg/_preparing.py` are the driver's prepared-statement cache,
#:   bounded by construction (`prepared_max`) and filling at whichever tick each statement
#:   happens to cross `prepare_threshold`. Round 1's rig already excludes the first of them.
#: * `respx`'s compiled route patterns, `httpx/_urls.py`'s URL objects and the `urllib.parse`
#:   caches underneath them are in this process only because the rig mocks the network:
#:   production's httpx holds connections, not a router full of route patterns. Measured
#:   2026-09-13, these four modules were the whole of the traced growth between tick 5 and tick 8
#:   (28.6 KiB of 28.6 KiB).
#:
#: Each entry names a *module*, never a package: excluding all of `*/httpx/*` or `*/urllib/*`
#: would also hide `httpx/_models.py`, where a response body the recorder had failed to release
#: would be counted -- which is exactly the kind of growth this assertion exists to catch.
#:
#: The unfiltered total and RSS are printed and asserted on beside it, so nothing hides here.
_NOT_THE_RECORDER = (
    _DRIVER_CACHE,
    tracemalloc.Filter(False, "*/psycopg/_preparing.py"),
    tracemalloc.Filter(False, "*/respx/*"),
    tracemalloc.Filter(False, "*/httpx/_urls.py"),
    tracemalloc.Filter(False, "*/urllib/parse.py"),
)

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
GAMES = 6
#: Markets on the one page this rig grows to production size. Production reaches its 2,269 KB
#: maximum `/markets` body with Kalshi's own `limit=1000` at ~2.3 KB a market; this rig reaches
#: the same *body size* with fewer, longer markets, because the body size is what the normalizer
#: has to decode and hold while the market count is 1,000 extra upserts and 1,000 extra quote
#: inserts a tick that have nothing to do with what is being measured. `_RULES_PADDING` below is
#: what makes up the difference, and the rig's own guard test pins the resulting body size.
MARKETS_PER_PAGE = 320
#: The series whose page is grown to production size. Only one, not all three: the *body size* is
#: what this file is about, and a second and third 2.3 MB page a tick buys another 1,000 venue
#: markets to upsert and 1,000 quotes to insert for nothing. Production fetches three of them a
#: tick; `_drain_batch`'s bound does not depend on how many.
BULK_SERIES = "KXNFLSPREAD"
#: Markets whose `volume_fp` moves every tick, so `select_trade_tickers` picks them every tick.
#: Every other market on the page is given `volume_fp = 0`, which that selector skips, so the
#: trade fan-out is this number and not the whole page. Production's is of the order of a
#: hundred; a dozen production-sized bodies is enough for a tick to read a multi-body normalize
#: batch, and `test_one_normalize_batch_does_not_hold_the_whole_batch_of_bodies` measures the
#: batch itself at 25 and 250 rows.
LIVE_MARKETS = 8
#: Prints a `/markets/trades` page carries. 1,000 is Kalshi's `limit`; at the field shape below
#: that is ~320 KB of JSON, matching production's 321 KB maximum.
TRADES_PER_PAGE = 1000
#: How many of each page's prints are new on each tick. The rest repeat, exactly as a page taken
#: from a `min_ts` window does, and `_already_recorded` skips them. This keeps the rig's database
#: writes to a few thousand rows a tick while leaving the *body* at production size, which is
#: what this file measures. Kept small deliberately: `insert_trades` writes one statement per
#: print, so a higher number buys nothing for a memory measurement and costs minutes of wall
#: clock across the run.
NEW_TRADES_PER_TICK = 20
#: Ticks, and the tick the comparison is based on. Ticks 1-4 warm every lazily built cache in the
#: process (SQLAlchemy's compiled statements, psycopg's prepared statements, `load_popularity`,
#: the normalizer's event cache, the stadium tables), so the baseline is tick 5 and never tick 1.
#:
#: Eight ticks, not the brief's twenty: a tick at production body shape costs seconds even after
#: the fix, and this has to be an ordinary member of the full suite rather than a quarter-hour
#: one (controller ruling, 2026-09-13). The fail-before/pass-after evidence for finding 49 is
#: `test_one_normalize_batch_does_not_hold_the_whole_batch_of_bodies`, which is deterministic and
#: measures the defect directly; this test is the growth guard around the whole tick.
TICKS = 8
BASELINE_TICK = 5
#: The floor under the RSS band, in MiB (controller ruling, 2026-09-13). Four of the ~4 MiB arena
#: steps this process's allocator extends the heap by; see the assertion for why a pure percentage
#: is the wrong instrument on a process this size.
RSS_FLOOR_MIB = 16.0


# --- production-shaped bodies -------------------------------------------------------------

#: `rules_primary` and `rules_secondary` on a live football market run to a few hundred
#: characters each, and they are the bulk of the ~2.3 KB a market weighs. Nothing in the harness
#: reads either; they are on the page, so they are decoded into the process every tick.
_RULES_PRIMARY = (
    "If the team named in the market title wins the game identified in the event title, then the "
    "market resolves to Yes. The game must be played on the scheduled date or, if postponed, "
    "within one week of the originally scheduled date, and must be considered an official game "
    "by the league. If the game is cancelled, abandoned or not completed within that period, or "
    "if the result is subsequently vacated or overturned by the league, the market resolves to "
    "No. Overtime counts. The source of record for the result is the league's official box "
    "score as published at the conclusion of the game."
)
_RULES_SECONDARY = (
    "The Underlying for this market is the official final score of the game identified in the "
    "event title. Settlement is determined solely by the league's published box score and not "
    "by any other source, broadcast or wagering market. The Exchange may, at its discretion, "
    "delay settlement pending confirmation of the official result."
)
#: How many times the rules text above is repeated on each market. Production's per-market weight
#: is ~2.3 KB and its page is 1,000 markets; this rig holds the *body* at production size with
#: `MARKETS_PER_PAGE` markets instead, so each one carries proportionally more of the venue's
#: free text. `_drain_batch`'s bound is a property of the body, not of the market count.
_RULES_PADDING = 6


def _bulk_market_fields(ticker: str, i: int) -> dict:
    """The fields a live Kalshi `/markets` row carries beyond the handful the harness reads.

    Finding 49: the round-1 rig's market dicts were ~300 B, so a whole page was 130 KB against
    production's 2,269 KB. Every one of these fields is decoded by psycopg into the recorder's
    heap on every fetch and again on every normalize batch, whether or not anything reads it.
    """
    return {
        "status": "active",
        "response_price_units": "usd_cent",
        "notional_value": 100,
        "notional_value_dollars": "1.00",
        "tick_size": 1,
        "rules_primary": " ".join([_RULES_PRIMARY] * _RULES_PADDING),
        "rules_secondary": " ".join([_RULES_SECONDARY] * _RULES_PADDING),
        "open_time": "2026-09-02T12:00:00Z",
        "expected_expiration_time": "2026-09-10T06:00:00Z",
        "expiration_time": "2026-09-17T06:00:00Z",
        "latest_expiration_time": "2026-09-17T06:00:00Z",
        "settlement_timer_seconds": 3600,
        "fee_waiver_expiration_time": None,
        "can_close_early": True,
        "cap_strike": None,
        "category": "Sports",
        "risk_limit_cents": 0,
        "liquidity": 125000 + i,
        "liquidity_dollars": f"{1250 + i}.00",
        "last_price": 50,
        "last_price_dollars": "0.5000",
        "previous_yes_bid": 49,
        "previous_yes_ask": 51,
        "previous_price": 50,
        "result": "",
        "settlement_value": None,
        "market_type": "binary",
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": 1, "end": 99, "tick_size": 1}],
        "custom_strike": {"team": ticker.split("-")[-1]},
        "early_close_condition": "Market may close early if the game is suspended.",
        "rules_summary": "Resolves Yes if the named outcome occurs in the official box score.",
        "functional_strike": "",
        "settlement_sources": [{"name": "League official box score", "url": "https://example.invalid/boxscore"}],
    }


def _decoy_market(series: str, j: int) -> dict:
    """A market for an event this slate does not carry, exactly as a live series page does.

    A real `/markets?series_ticker=...&limit=1000` page holds the whole week's events, not just
    today's. These rows are classifiable, so the normalizer creates `venue_markets` and
    `venue_quotes` for them and then leaves them unmatched ("no event title recorded"), which is
    what the live unmatched rows do. `volume_fp` is zero so they never enter the trade fan-out.
    """
    event_ticker = f"{series}-26SEP27D{j:04d}"
    ticker = f"{event_ticker}-T{40 + (j % 40)}5"
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": f"Over {40 + (j % 40)}.5 total points?",
        "yes_sub_title": "Over",
        "strike_type": "greater",
        "floor_strike": 40 + (j % 40) + 0.5,
        "close_time": "2026-09-27T23:00:00Z",
        "yes_bid_dollars": "0.4500",
        "yes_ask_dollars": "0.4600",
        "no_bid_dollars": "0.5400",
        "no_ask_dollars": "0.5500",
        "volume_fp": "0",
        "volume_24h_fp": "0",
        "open_interest_fp": "0",
        **_bulk_market_fields(ticker, j),
    }


def _production_markets(day) -> dict[str, list]:
    """`day`'s Kalshi market pages grown to production shape.

    Every real market keeps its quote and gets the bulk fields; the first `LIVE_MARKETS` of them
    keep a nonzero `volume_fp` (the trade fan-out), the rest are parked at zero; and
    `BULK_SERIES`' page is padded to `MARKETS_PER_PAGE` with markets for other events, which is
    what takes it to production's body size.
    """
    pages: dict[str, list] = {}
    live = 0
    for series, markets in day.kalshi_markets.items():
        out = []
        for i, m in enumerate(markets["markets"]):
            m = dict(m)
            m.update(_bulk_market_fields(m["ticker"], i))
            if live < LIVE_MARKETS:
                live += 1
                m["_live"] = True          # stripped below; marks the trade fan-out
            else:
                m["volume_fp"] = "0"
            out.append(m)
        if series == BULK_SERIES:
            for j in range(len(out), MARKETS_PER_PAGE):
                out.append(_decoy_market(series, j))
        pages[series] = out
    return pages


def _markets_body(pages: dict[str, list], series: str, tick: int) -> dict:
    """One `/markets` page for `series` on tick `tick`.

    `volume_fp` on the live markets moves every tick, which is what makes
    `select_trade_tickers` pick them every tick -- in the round-1 rig the volume never moved, so
    after tick 1 the recorder fetched no trades at all.
    """
    markets = []
    for m in pages[series]:
        m = dict(m)
        if m.pop("_live", False):
            m["volume_fp"] = f"{1500 + tick * 37}.00"
        markets.append(m)
    return {"cursor": "", "markets": markets}


def _trade(ticker: str, n: int) -> dict:
    """One print, with the fields Kalshi's `/markets/trades` actually returns."""
    return {
        "trade_id": f"{ticker}-{n:09d}",
        "ticker": ticker,
        "count": 25 + (n % 50),
        "count_fp": f"{25 + (n % 50)}.0000",
        "created_time": (NOW - timedelta(seconds=n % 86_400)).isoformat().replace("+00:00", "Z"),
        "yes_price": 40 + (n % 20),
        "yes_price_dollars": f"0.{40 + (n % 20):02d}00",
        "no_price": 60 - (n % 20),
        "no_price_dollars": f"0.{60 - (n % 20):02d}00",
        "taker_side": "yes" if n % 2 else "no",
        "taker_outcome_side": "yes" if n % 2 else "no",
        "taker_book_side": "bid" if n % 3 else "ask",
        "is_block_trade": False,
    }


def _trades_body(ticker: str, tick: int) -> dict:
    """A `/markets/trades` page for `ticker` on tick `tick`: `TRADES_PER_PAGE` prints of which
    `NEW_TRADES_PER_TICK` are new. A page taken from a `min_ts` window repeats the prints it
    already returned, and `_already_recorded` drops those, so the database writes stay small
    while the body stays production-sized."""
    first = tick * NEW_TRADES_PER_TICK
    return {"cursor": "",
            "trades": [_trade(ticker, first + n) for n in range(TRADES_PER_PAGE)]}


# --- the rig ------------------------------------------------------------------------------

def _mount(pages, clock_tick) -> None:
    """Every endpoint the tick fetches, at production body shape."""
    day = clock_tick["day"]
    respx.get(url__regex=r"https://k/series/.*").mock(
        return_value=httpx.Response(200, json={"series": {"fee_type": "quadratic",
                                                         "fee_multiplier": "0.07"}}))
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=day.espn))
    respx.get("https://e/college-football/scoreboard").mock(
        return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/americanfootball_nfl/odds").mock(
        return_value=httpx.Response(200, json=day.odds_featured,
                                    headers={"x-requests-last": "3", "x-requests-remaining": "5000"}))
    respx.get(url__regex=r"https://o/v4/sports/americanfootball_ncaaf/odds").mock(
        return_value=httpx.Response(200, json=[],
                                    headers={"x-requests-last": "3", "x-requests-remaining": "5000"}))

    def alternates(request):
        m = _EVENT_ID.search(str(request.url))
        body = day.odds_alternates.get(m.group(1) if m else "", {})
        return httpx.Response(200, json=body,
                              headers={"x-requests-last": "2", "x-requests-remaining": "5000"})

    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(side_effect=alternates)

    def markets(request):
        series = request.url.params.get("series_ticker", "")
        if request.url.params.get("status") == "settled":
            return httpx.Response(200, json={"cursor": "", "markets": []})
        if series not in pages:
            return httpx.Response(200, json={"cursor": "", "markets": []})
        return httpx.Response(200, json=_markets_body(pages, series, clock_tick["tick"]))

    def events(request):
        series = request.url.params.get("series_ticker", "")
        return httpx.Response(200, json=day.kalshi_events.get(series, {"cursor": "", "events": []}))

    def trades(request):
        ticker = request.url.params.get("ticker", "")
        return httpx.Response(200, json=_trades_body(ticker, clock_tick["tick"]))

    respx.get("https://k/markets").mock(side_effect=markets)
    respx.get("https://k/events").mock(side_effect=events)
    respx.get("https://k/markets/trades").mock(side_effect=trades)
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(
        return_value=httpx.Response(200, json={"orderbook_fp": {}}))


def _driver(env_settings, db_session):
    """A zero-argument "run one tick" callable at production body shape, plus its state dict."""
    teams_body, teams = synthetic_teams(2 * GAMES)
    day = synthetic_day(teams, GAMES, NOW)
    pages = _production_markets(day)
    state = {"now": NOW, "tick": 0, "day": day}
    _mount(pages, state)
    seed_teams_from_espn(db_session, "nfl", teams_body)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    db_session.commit()

    http = HttpClient(1, sleep=lambda s: None, clock=lambda: state["now"])
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=0, sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    rec = Recorder(env_settings, factory, odds, espn, kalshi,
                   clock=lambda: state["now"], monotonic=time.monotonic)

    def run_tick():
        state["tick"] += 1
        run = rec.maybe_tick(force=True)
        state["last_notes"] = dict(run.notes or {})
        state["now"] = state["now"] + timedelta(seconds=30)
        # respx keeps every request and response it has served; that is the rig's retention,
        # not the recorder's.
        respx.mock.reset()
        return state["last_notes"]

    return run_tick, state


@pytest.fixture(autouse=True)
def _reset_events_cache():
    runner_mod._EVENTS.clear()
    yield
    runner_mod._EVENTS.clear()


# --- the deterministic one ----------------------------------------------------------------

def _seed_pending_trade_bodies(db_session, count: int, first_id_marker: int) -> int:
    """`count` pending `kalshi_trades` raw rows of production size. Returns the body's JSON size.

    `ensure_partitions(NOW)` first, the house pattern from `tests/test_runner.py`: `conftest`'s
    session fixture only builds this week's and next week's `raw_responses` partitions, and
    these rows are dated `NOW` (2026-09-09), so without it the insert fails with "no partition
    of relation raw_responses found" for every real week outside that fortnight.
    """
    ensure_partitions(db_session, NOW)
    body = _trades_body(f"KXNFLGAME-26SEP09ASBR-{first_id_marker}", 0)
    size = len(json.dumps(body))
    for i in range(count):
        db_session.add(RawResponse(
            run_id=1, source="kalshi", endpoint="/markets/trades",
            params={"ticker": f"T{first_id_marker}-{i}", "min_ts": NOW.isoformat()},
            fetched_at=NOW, http_status=200,
            body=_trades_body(f"KXNFLGAME-26SEP09ASBR-{first_id_marker}-{i}", 0)))
    db_session.commit()
    return size


@pytest.mark.parametrize("rows", [25, 250])
def test_one_normalize_batch_does_not_hold_the_whole_batch_of_bodies(
        db_session, monkeypatch, rows, request):
    """Finding 49 round 2's measured cause, isolated.

    `_drain_batch` read its batch with `.scalars().all()`, so psycopg had decoded all `batch`
    jsonb bodies into the process before the first row was handled. Round 1 dropped each row
    *after* handling it (`rows[i] = None`), which cannot lower a peak that has already happened
    at materialisation. At production body sizes that peak is the recorder's largest single
    allocation: one `kalshi_trades` batch on the live slate is a few hundred pages of ~1,000
    decoded prints each.

    The handler is neutralised on purpose. What is under test is how much of the batch the
    *batch read itself* holds resident, which is independent of what `_handle` then does with
    each row -- and measuring it without the handler is what makes the number deterministic.
    """
    monkeypatch.setattr(runner_mod, "_handle", lambda session, family, r, ctx: None)
    body_kb = _seed_pending_trade_bodies(db_session, rows, request.node.callspec.id) / 1024
    gc.collect()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        normalized, fetched, committed = runner_mod._drain_batch(
            db_session, "kalshi_trades", 500, {})
        peak_kb = (tracemalloc.get_traced_memory()[1] - before) / 1024
    finally:
        tracemalloc.stop()
    assert (normalized, fetched, committed) == (rows, rows, True)
    print(f"\n{rows:4d} pending rows of {body_kb:.0f} KiB: "
          f"_drain_batch peak {peak_kb:9.1f} KiB ({peak_kb / body_kb:5.1f} bodies)")

    # The read may hold a handful of bodies (one in flight plus the driver's own row buffer);
    # it must not hold the batch. 8 bodies is far above the bounded read and far below the 25
    # and 250 the unbounded one holds.
    assert peak_kb < 8 * body_kb, (
        f"one normalize batch of {rows} rows held {peak_kb / body_kb:.1f} bodies "
        f"({peak_kb:.0f} KiB) resident at once")


# --- the brief's acceptance ----------------------------------------------------------------

@respx.mock
def test_the_production_shaped_slate_actually_prices(env_settings, db_session):
    """Guard on the rig: a measurement over a slate that normalizes to nothing means nothing.

    Also pins the body shape this file exists to drive, so a future change that quietly shrinks
    it fails here rather than passing the acceptance test below for the wrong reason.
    """
    run_tick, _ = _driver(env_settings, db_session)
    run_tick()

    markets_bodies = sorted(
        (len(json.dumps(r.body)) for r in db_session.query(RawResponse).filter_by(
            source="kalshi", endpoint="/markets")), reverse=True)
    trade_bodies = sorted(
        (len(json.dumps(r.body)) for r in db_session.query(RawResponse).filter_by(
            source="kalshi", endpoint="/markets/trades")), reverse=True)
    print(f"\n/markets bodies: {len(markets_bodies)}, largest {markets_bodies[0] / 1024:.0f} KiB"
          f"\n/markets/trades bodies: {len(trade_bodies)}, largest {trade_bodies[0] / 1024:.0f} KiB")
    assert markets_bodies[0] > 1_800_000, (
        "the largest /markets body is not at production size (2,269 KB live)")
    assert len(trade_bodies) >= LIVE_MARKETS, "the trade fan-out is narrower than the rig intends"
    assert trade_bodies[0] > 250_000, (
        "the /markets/trades bodies are not at production size (321 KB live)")

    matched = db_session.query(VenueMarket).filter(VenueMarket.match_status == "matched").count()
    assert matched > 100, f"only {matched} matched venue markets; the slate is not pricing"
    assert db_session.query(FairValue).count() > 100
    assert db_session.query(Signal).count() > 100
    assert db_session.query(VenueTrade).count() > 1000


@respx.mock
def test_eight_production_shaped_ticks_do_not_grow_the_process(env_settings, db_session):
    """Finding 49's original acceptance, at production body shape.

    `TICKS` (eight) ticks; the traced total, the per-tick peak and RSS are compared between tick
    `BASELINE_TICK` (five -- every lazily built cache in the process is warm by then: SQLAlchemy's
    compiled statements, psycopg's prepared statements, `load_popularity`, the normalizer's event
    cache) and the last tick.
    """
    ticks, baseline = TICKS, BASELINE_TICK
    run_tick, _ = _driver(env_settings, db_session)
    samples: list[tuple[int, float, float, float | None, float]] = []
    snapshots: dict[int, tracemalloc.Snapshot] = {}

    print("\ntick   traced KiB      peak KiB     RSS MiB    tick s   (stage ms)", flush=True)
    # Three frames, not twelve: `tracemalloc` captures a traceback on *every* allocation, and at
    # twelve frames that instrumentation cost five to seven times the tick itself -- measured
    # 2026-09-13, 68-140 s a tick against ~13 s uninstrumented. Three frames still name the
    # allocating call site and its caller, which is what a growth diagnosis needs.
    tracemalloc.start(3)
    try:
        for i in range(1, ticks + 1):
            tracemalloc.reset_peak()
            t0 = time.monotonic()
            notes = run_tick()
            elapsed = time.monotonic() - t0
            peak = tracemalloc.get_traced_memory()[1]
            gc.collect()
            snapshot = tracemalloc.take_snapshot()
            unfiltered = sum(st.size for st in snapshot.statistics("filename"))
            tm_overhead = tracemalloc.get_tracemalloc_memory()
            # The traced total has psycopg's prepared-statement cache taken out of it, exactly
            # as round 1's rig does: that cache is the driver's, it is bounded by construction
            # (`prepared_max`), and it fills at whichever tick each statement happens to cross
            # `prepare_threshold`, which is noise of the order of the whole harness total.
            traced = sum(st.size for st in
                         snapshot.filter_traces(_NOT_THE_RECORDER).statistics("filename"))
            rss = rss_mb()
            samples.append((i, traced / 1024, peak / 1024, rss, elapsed,
                            unfiltered / 1024, tm_overhead / (1024 * 1024)))
            # Printed as it happens, not at the end: a tick at production body shape costs
            # seconds, and a run that is cut short still has to say where the time went.
            stages = " ".join(f"{st['name']}={st['elapsed_ms']}"
                              for st in (notes.get("pricing") or {}).get("stages", []))
            print(f"{i:4d} {traced / 1024:12.1f} {peak / 1024:13.1f} "
                  f"{'n/a' if rss is None else f'{rss:11.1f}'} {elapsed:9.1f}   "
                  f"unfiltered={unfiltered / 1024:.1f} KiB tracemalloc={tm_overhead / (1024 * 1024):.1f} MiB  "
                  f"{stages}  normalized={notes.get('normalized')}", flush=True)
            if i in (baseline, ticks):
                snapshots[i] = snapshot
        diff = snapshots[ticks].compare_to(snapshots[baseline], "traceback")
    finally:
        tracemalloc.stop()

    lines = ["tick   traced KiB      peak KiB     RSS MiB    tick s  unfiltered KiB  "
             "tracemalloc MiB"]
    for i, traced, peak, rss, elapsed, unfiltered, tm_overhead in samples:
        lines.append(f"{i:4d} {traced:12.1f} {peak:13.1f} "
                     f"{'n/a' if rss is None else f'{rss:11.1f}'} {elapsed:9.1f} "
                     f"{unfiltered:15.1f} {tm_overhead:16.1f}")
    grown = [stat for stat in diff if stat.size_diff > 0][:10]
    lines.append("")
    lines.append(f"top growing tracebacks, tick {baseline} -> tick {ticks}:")
    for stat in grown:
        lines.append(f"  {stat.size_diff / 1024:+9.1f} KiB  {stat.count_diff:+7d} blocks")
        for frame in stat.traceback.format():
            lines.append(f"      {frame}")
    table = "\n".join(lines)
    print("\n" + table)

    base_traced, base_rss = samples[baseline - 1][1], samples[baseline - 1][3]
    last_traced, last_rss = samples[-1][1], samples[-1][3]
    traced_growth = (last_traced - base_traced) / base_traced
    assert traced_growth < 0.05, (
        f"traced total grew {traced_growth:.1%} from tick {baseline} to tick {ticks}\n{table}")
    # RSS gets a band of max(5 % of the tick-5 reading, RSS_FLOOR_MIB), not a pure percentage
    # (controller ruling, 2026-09-13). The C allocator extends the heap in discrete ~4 MiB arena
    # steps -- measured +4.3 and +4.0 MiB at ticks 6 and 7 on a 142 MiB process -- so 5 % of a
    # small test process (7.1 MiB here) is finer than two of those steps and measures arena
    # quantisation rather than retention: the same eight ticks read +6.0 % from a cold 142 MiB
    # start and -1.4 % from a 231 MiB one, with the recorder's own numbers flat to 0.3 % in both.
    # `RSS_FLOOR_MIB` is four such steps; it is an order of magnitude below one tick's growth in
    # production (~1.2 GB before this fix) and well below the 500 MiB six-hour production
    # criterion, which only the live `recorder.rss_mb` series can judge. This is a band, not a
    # ceiling and not a skip: the traced-total bound above and the high-water peak bound below are
    # the retention detectors, and the roadmap's 5 % criterion stays enforced unchanged by the
    # synthetic twenty-tick test in `tests/test_recorder_memory.py`.
    if base_rss is not None and last_rss is not None:
        band = max(0.05 * base_rss, RSS_FLOOR_MIB)
        assert last_rss - base_rss < band, (
            f"RSS grew {last_rss - base_rss:+.1f} MiB ({base_rss:.1f} -> {last_rss:.1f}) from "
            f"tick {baseline} to tick {ticks}, past the {band:.1f} MiB band\n{table}")

    # The high-water mark, not two single ticks: a tick's peak depends on which families the
    # normalize budget reached and is bimodal by construction, so comparing tick `baseline`
    # against tick `ticks` compares two draws from that distribution. What must not happen is
    # the high-water mark climbing -- which is what it did before the `_drain_batch` fix, since
    # the peak was `batch` bodies and the backlog feeding `batch` grew with the run. The 25 %
    # band is the one round 1's stage-peak test settled on for the same reason.
    warm_peak = max(s[2] for s in samples[:baseline - 1])
    later_peak = max(s[2] for s in samples[baseline - 1:])
    assert later_peak < 1.25 * warm_peak, (
        f"the tick's high-water mark rose from {warm_peak:.0f} KiB (ticks 1-{baseline - 1}) to "
        f"{later_peak:.0f} KiB (ticks {baseline}-{ticks})\n{table}")
