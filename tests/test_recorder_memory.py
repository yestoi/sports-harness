"""Finding 49: the recorder tick's retention, tick over tick.

The NAS evidence the hotfix brief carries is RSS, which no test can assert on -- it counts
allocator arenas the interpreter has freed but not handed back. What a test can assert on is
`tracemalloc`'s traced total, the live Python allocation, which is deterministic. So this file
drives the real `Recorder.maybe_tick` against the test database with a realistic synthetic slate
(`scripts/measure_tick_memory.py`) and pins the growth from the second tick onward.

The second tick is the baseline, not the first: tick 1 warms every lazily built cache in the
process (SQLAlchemy's compiled-statement cache, `load_popularity`, the normalizer's event
cache), and that warm-up is not retention.

Run `-s` to see the per-tick table and the top allocation sites; the numbers in the fix 48/49
report came from exactly that.
"""

import re
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy.orm import sessionmaker

from harness.db.models import FairValue, MarketGapSnapshot, MetricSample, Signal, VenueMarket
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.matching.teams import seed_teams_from_espn
from harness.normalize import runner as runner_mod
from harness.recorder.tick import Recorder
from harness.strategy import pipeline
from harness.strategy.variants import load_variants, register_variants
from harness.venues.kalshi.public import KalshiPublic

from scripts.measure_tick_memory import StagePeaks, measure_ticks, synthetic_day, synthetic_teams

FIXD = Path(__file__).parent / "fixtures"
#: The seven registered production variants, not the one-variant test fixture: the pricing pass
#: is the tick's most allocation-hungry stage and measuring it with a single variant would miss
#: most of it. Read-only -- `harness/variants/` is frozen for this hotfix.
VARIANTS_DIR = Path(__file__).resolve().parents[1] / "harness" / "variants"
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
#: The brief asks for "~20 games"; 20 games of this shape is ~1,300 venue markets and a few
#: thousand fair values a tick, the same order as the NAS Saturday slate's 85 games.
GAMES = 20
_EVENT_ID = re.compile(r"/events/(\w+)/odds")


@pytest.fixture(autouse=True)
def _reset_events_cache():
    runner_mod._EVENTS.clear()
    yield
    runner_mod._EVENTS.clear()


def _mount(day) -> None:
    """Wire every endpoint the tick fetches to `day`'s bodies."""
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=day.espn))
    respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
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
        return httpx.Response(200, json=day.kalshi_markets.get(series, {"cursor": "", "markets": []}))

    def events(request):
        series = request.url.params.get("series_ticker", "")
        return httpx.Response(200, json=day.kalshi_events.get(series, {"cursor": "", "events": []}))

    respx.get("https://k/markets").mock(side_effect=markets)
    respx.get("https://k/events").mock(side_effect=events)
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(
        return_value=httpx.Response(200, json={"orderbook_fp": {}}))


def _recorder(env_settings, db_session, clock):
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: clock["now"])
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=0, sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return Recorder(env_settings, factory, odds, espn, kalshi, clock=lambda: clock["now"],
                    monotonic=time.monotonic)


def _driver(env_settings, db_session, ticks: int):
    """A zero-argument "run one tick" callable, plus the clock it advances.

    The clock advances by the production heartbeat (30 s) and every tick is forced, so twenty
    ticks land inside one fifteen-minute window. That window is the point: `build_gap_snapshots`
    reads every prior run's fair values inside it, so a rig that spaced its ticks out far enough
    to keep that read empty would measure a tick the NAS never runs.
    """
    teams_body, teams = synthetic_teams(2 * GAMES)
    day = synthetic_day(teams, GAMES, NOW)
    _mount(day)
    seed_teams_from_espn(db_session, "nfl", teams_body)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    db_session.commit()
    clock = {"now": NOW}
    rec = _recorder(env_settings, db_session, clock)

    def run_tick():
        rec.maybe_tick(force=True)
        clock["now"] = clock["now"] + timedelta(seconds=30)
        # respx keeps every request and response it has served; twenty ticks of that is the
        # rig's own retention, not the recorder's, so it is dropped between ticks.
        respx.mock.reset()

    return run_tick, day


@respx.mock
def test_the_synthetic_slate_actually_prices(env_settings, db_session):
    """Guard on the rig itself: a measurement over a slate that normalizes to nothing would
    report beautiful numbers and mean nothing. One tick has to produce matched markets, fair
    values, gap snapshots and signals before the retention assertion below is worth reading."""
    run_tick, _ = _driver(env_settings, db_session, 1)
    run_tick()

    matched = db_session.query(VenueMarket).filter(VenueMarket.match_status == "matched").count()
    assert matched > 100, f"only {matched} matched venue markets; the slate is not exercising pricing"
    assert db_session.query(FairValue).count() > 100
    assert db_session.query(MarketGapSnapshot).count() > 100
    assert db_session.query(Signal).count() > 100


@respx.mock
def test_the_tick_writes_its_own_rss_as_a_metric(env_settings, db_session):
    """Fix 49 visibility: `recorder.rss_mb` lands beside `recorder.tick_ms` every tick, labelled
    with the phase that produced it, so Pulse's vitals show the growth without ssh."""
    run_tick, _ = _driver(env_settings, db_session, 1)
    run_tick()

    rows = db_session.query(MetricSample).filter(
        MetricSample.source == "recorder", MetricSample.name == "recorder.rss_mb").all()
    assert len(rows) == 1
    assert rows[0].labels == {"phase": "tick"}
    assert rows[0].value > 0


@respx.mock
def test_the_gap_snapshot_read_does_not_grow_with_the_history_behind_it(
        env_settings, db_session, monkeypatch):
    """Finding 49's measured cause.

    `build_gap_snapshots` used to read every mapped `FairValue` written in the trailing fifteen
    minutes for the run's games and keep the newest per shape, so its peak allocation grew with
    every tick that landed in that window and with nothing else. On this rig it grew from
    6.1 MiB to 14.5 MiB over twelve ticks; on the NAS the same window holds thirty runs of
    3,269 fair values each. The `DISTINCT ON` read is bounded by the run's own shape count, so
    the peak is flat.
    """
    ticks = 12
    #: Every stage `price_and_signal` calls by name. `build_gap_snapshots`, `_load_gap_rows` and
    #: `run_strategy` each run more than once a tick since fix 48; `StagePeaks.wrap` keeps the
    #: largest reading of the tick, which is the one that sets the allocator's high-water mark.
    STAGES = ("compute_direct_fair_values", "compute_derived_fair_values", "build_gap_snapshots",
              "_load_gap_rows", "run_strategy")
    run_tick, _ = _driver(env_settings, db_session, ticks)
    peaks = StagePeaks()
    for name in STAGES:
        monkeypatch.setattr(pipeline, name, peaks.wrap(name, getattr(pipeline, name)))

    tracemalloc.start()
    try:
        for _ in range(ticks):
            run_tick()
            peaks.end_tick()
    finally:
        tracemalloc.stop()
    print("\n" + peaks.text(STAGES))

    # The bound is 25 %, not the 10 % this test carried before fix 48 landed beside it, because
    # `growth` compares two single ticks and a single tick's peak is noisy: the same twelve-tick
    # run reads 5,010 to 5,422 KiB with no trend in it (measured 2026-09-12), so a first tick at
    # the bottom of that band and a last tick at the top is +8 % of pure noise. What the test is
    # there to catch is unbounded growth with the history behind it, and that was +138.7 % over
    # the same twelve ticks before the `DISTINCT ON` read; 25 % separates the two with room for
    # the noise. Measured at the head of this branch: +4.1 %.
    growth = peaks.growth("build_gap_snapshots")
    assert growth < 0.25, (
        f"the gap snapshot stage's peak grew {growth:.1%} over {ticks} ticks of history\n"
        + peaks.text(("build_gap_snapshots",)))


@respx.mock
def test_the_tick_retains_nothing_after_the_second(env_settings, db_session):
    """Inherited small-leak detector, not the original finding 49 acceptance.

    Preserve its existing 8 KiB/tick bound without relaxing it. Passing this filtered,
    post-GC diagnostic does not establish <5% process growth. The report now also prints
    unfiltered pre-GC growth and RSS, and original acceptance remains open for controlled
    Linux measurement. The separate stage-peak test detects the fair-history regression.
    """
    ticks = 20
    run_tick, _ = _driver(env_settings, db_session, ticks)
    report = measure_ticks(run_tick, ticks, checkpoints=(1, 2, 5, 10))
    print("\n" + report.text())

    creep = report.creep_kb_per_tick(2)
    assert creep < 8.0, (
        f"traced total grew {creep:.1f} KiB per tick from tick 2 to tick {ticks} "
        f"({report.growth_after(2):.1%} in total)\n{report.text()}")


@pytest.mark.parametrize("path", ["candidate", "baseline_budget_exhausted"])
@respx.mock
def test_diagnostic_allocations_cover_the_actual_tick_path(
        env_settings, db_session, monkeypatch, path):
    from harness.recorder import tick as tick_module
    from pricing_baseline import baseline_pipeline
    from scripts.measure_tick_memory import AllocationStages

    run_tick, _ = _driver(env_settings, db_session, 2)
    allocations = AllocationStages()
    # Fetch boundaries do not overlap; _checkpoint stays real and clears the session as usual.
    for name in ("_espn", "_odds", "_kalshi_markets", "_kalshi_events", "_kalshi_settled",
                 "_kalshi_series", "_kalshi_trades_and_ladders"):
        monkeypatch.setattr(Recorder, name, allocations.wrap(name, getattr(Recorder, name)))
    monkeypatch.setattr(tick_module, "normalize_new",
                        allocations.wrap("normalize", tick_module.normalize_new))
    results = []
    if path == "baseline_budget_exhausted":
        original = baseline_pipeline().price_and_signal

        def price(session, run_id, now, settings, budget_s):
            # Original stage 1 computes every fair even with budget=0, then returns before gaps.
            # This reproduces the reported control flow without pretending to emulate NAS speed.
            result = original(session, run_id, now, settings, budget_s=0)
            results.append(result)
            return result
    else:
        original = tick_module.price_and_signal

        def price(session, run_id, now, settings, budget_s):
            result = original(session, run_id, now, settings, budget_s=600)
            results.append(result)
            return result
    monkeypatch.setattr(tick_module, "price_and_signal", allocations.wrap(path, price))
    tracemalloc.start()
    try:
        for _ in range(2):
            run_tick()
            stages = allocations.drain()
            assert {"_espn", "_odds", "_kalshi_markets", "normalize", path} <= {
                stage["stage"] for stage in stages}
    finally:
        tracemalloc.stop()
    assert all(result["fair_direct"] > 0 for result in results)
    if path == "baseline_budget_exhausted":
        assert all(result["budget_exhausted"] and result["order"] == []
                   and result["gaps"] == 0 and result["fair_derived"] > 0 for result in results)
    else:
        assert all(result["gaps"] > 0 and result["variants_run"] for result in results)


@respx.mock
def test_diagnostic_settle_contribution_is_separate_from_the_tick(
        env_settings, db_session):
    from harness.settlement.job import Settler
    from scripts.measure_tick_memory import AllocationStages

    run_tick, _ = _driver(env_settings, db_session, 1)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    settler = Settler(env_settings, factory, None, clock=lambda: NOW + timedelta(seconds=30),
                      monotonic=time.monotonic)
    stages = AllocationStages()
    tracemalloc.start()
    try:
        stages.wrap("tick", run_tick)()
        tick_samples = stages.drain()
        stages.wrap("settle", settler.run)()
        settle_samples = stages.drain()
    finally:
        tracemalloc.stop()
    assert [sample["stage"] for sample in tick_samples + settle_samples] == ["tick", "settle"]
    phases = {row.labels["phase"] for row in db_session.query(MetricSample).filter_by(
        source="recorder", name="recorder.rss_mb")}
    assert phases == {"tick", "settle"}
