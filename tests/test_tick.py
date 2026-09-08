import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as SASession
from sqlalchemy.orm import sessionmaker

from harness.db.models import (Game, MetricSample, OperatorEvent, RawResponse, Run, TradeWatermark, VenueMarket,
                               VenueQuote, VenueTrade)
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.health import create_app
from harness.matching.teams import seed_teams_from_espn
from harness.normalize import runner as runner_mod
from harness.recorder import store
from harness.recorder.tick import Recorder, _pricing_samples, _recorder_samples
from harness.strategy.pipeline import price_and_signal
from harness.strategy.variants import load_variants, register_variants
from harness.venues.kalshi.public import KalshiPublic


@pytest.fixture(autouse=True)
def _reset_normalize_events_cache():
    # harness.normalize.runner._EVENTS is a module-level, process-wide cache; the tick now
    # calls normalize_new(), so clear it between tests to avoid cross-test contamination.
    runner_mod._EVENTS.clear()
    yield
    runner_mod._EVENTS.clear()

FIXD = Path(__file__).parent / "fixtures"
ODDS = json.loads((FIXD / "odds_featured_nfl.json").read_text())
ESPN = json.loads((FIXD / "espn_nfl_scoreboard.json").read_text())
KM = json.loads((FIXD / "kalshi_markets_page.json").read_text())
NFL_TEAMS = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wed 18:00 CT, 1h20m before the 19:20 kickoff


def _recorder(env_settings, db_session, now=NOW, monotonic=time.monotonic):
    clock = {"now": now}
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: clock["now"])
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=0, sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return (Recorder(env_settings, factory, odds, espn, kalshi, clock=lambda: clock["now"],
                     monotonic=monotonic), clock)


@respx.mock
def test_first_tick_fetches_everything_and_records(env_settings, db_session):
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
        return_value=httpx.Response(200, json=ODDS, headers={"x-requests-last": "3", "x-requests-remaining": "100"}))
    alt = respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(
        return_value=httpx.Response(200, json={}, headers={"x-requests-last": "2", "x-requests-remaining": "98"}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": [
        {"trade_id": "t1", "created_time": "2026-09-09T22:59:00Z"}]}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={"orderbook_fp": {}}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "ok", run.notes
    rows = db_session.query(RawResponse).filter_by(run_id=run.id).all()
    sources = {(r.source, r.endpoint) for r in rows}
    assert ("espn", "/nfl/scoreboard") in sources
    assert ("odds_api", "/sports/americanfootball_nfl/odds") in sources
    assert ("odds_api", "/sports/americanfootball_nfl/events/e1f2a3/odds") in sources  # commence within 36h
    assert ("kalshi", "/markets") in sources
    assert ("kalshi", "/markets/trades") in sources  # fixture market has volume 1234 and no watermark
    # 2 featured calls at 3 credits each; the fixture event is served for both sports but the
    # odds_alt:<event_id> source-state key dedupes it, so alternates are fetched once at 2 credits.
    assert alt.call_count == 1
    assert run.credits_used == 3 * 2 + 2
    wm = db_session.get(TradeWatermark, "KXNFLGAME-26SEP21NYGLAR-NYG")
    assert wm is not None and wm.last_ts == datetime(2026, 9, 9, 22, 59, tzinfo=timezone.utc)


@respx.mock
def test_second_tick_within_interval_is_skipped(env_settings, db_session):
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": []}))
    rec, clock = _recorder(env_settings, db_session)
    first = rec.maybe_tick()
    clock["now"] = NOW + timedelta(seconds=30)
    second = rec.maybe_tick()
    assert first.status == "ok" and second.status == "skipped"
    assert db_session.query(RawResponse).filter_by(run_id=second.id).count() == 0


@respx.mock
def test_source_failure_is_isolated(env_settings, db_session):
    respx.get("https://e/nfl/scoreboard").mock(side_effect=httpx.ConnectError("down"))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": []}))
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "error" and "espn:nfl" in json.dumps(run.notes["errors"])
    # 1 open-status page + 1 settled-status page per series across the 6 football series.
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets").count() == 12


@respx.mock
def test_budget_exhaustion_stops_ladders(env_settings, db_session):
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    ob = respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))
    env_settings.tick_budget_s = 0  # everything after bulk markets is over budget
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.budget_exhausted is True and ob.call_count == 0


@respx.mock
def test_non_http_exception_in_one_source_does_not_abort_tick(env_settings, db_session, monkeypatch):
    import harness.recorder.tick as tick_mod
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": []}))

    def boom(sport, body):
        if sport == "nfl":
            raise RuntimeError("parse exploded")
        return []

    monkeypatch.setattr(tick_mod, "parse_kickoffs", boom)
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "error" and "espn:nfl" in json.dumps(run.notes["errors"])
    # 1 open-status page + 1 settled-status page per series across the 6 football series.
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets").count() == 12
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="odds_api").count() == 2


@respx.mock
def test_tick_records_kalshi_events(env_settings, db_session):
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))
    ev = respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    rec, clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert ev.call_count == 6
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/events").count() == 6
    clock["now"] = NOW + timedelta(seconds=60)
    rec.maybe_tick()
    assert ev.call_count == 6  # 15-minute interval, not due


def _settled_rows(db_session, run_id=None):
    q = db_session.query(RawResponse).filter_by(source="kalshi", endpoint="/markets")
    if run_id is not None:
        q = q.filter_by(run_id=run_id)
    return [r for r in q.all() if (r.params or {}).get("status") == "settled"]


@respx.mock
def test_tick_records_kalshi_settled(env_settings, db_session):
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    mkt = respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))

    rec, clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()

    rows = _settled_rows(db_session, run.id)
    assert len(rows) == 6
    for r in rows:
        assert r.params["status"] == "settled"

    expected_min_ts = str(int((NOW - timedelta(days=8)).timestamp()))
    settled_calls = [c for c in mkt.calls if dict(c.request.url.params).get("status") == "settled"]
    assert len(settled_calls) == 6
    for c in settled_calls:
        assert dict(c.request.url.params)["min_settled_ts"] == expected_min_ts

    # 15 minutes later: inside the hour, kalshi_settled:<series> is not due again.
    clock["now"] = NOW + timedelta(minutes=15)
    second = rec.maybe_tick()
    assert _settled_rows(db_session, second.id) == []

    # 61 minutes after the first tick: the hourly interval has elapsed, so it fires again.
    clock["now"] = NOW + timedelta(minutes=61)
    third = rec.maybe_tick()
    assert len(_settled_rows(db_session, third.id)) == 6


@respx.mock
def test_tick_fetches_kalshi_series_daily_and_writes_fees(env_settings, db_session):
    # Task 3b item 3: GET /series/{series_ticker} fetched once a day per football series, and
    # the normalizer writes its fee shape onto that series' venue_markets rows.
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    series = respx.get(url__regex=r"https://k/series/.*").mock(
        return_value=httpx.Response(200, json={"series": {"fee_type": "quadratic", "fee_multiplier": "0.5"}}))

    rec, clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()

    assert series.call_count == 6
    rows = db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi").all()
    series_rows = [r for r in rows if r.endpoint.startswith("/series/")]
    assert {r.endpoint for r in series_rows} == {f"/series/{s}" for s in
                                                 ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL",
                                                  "KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL")}
    vm = db_session.query(VenueMarket).filter_by(series_ticker="KXNFLGAME").first()
    assert vm is not None and vm.fee_type == "quadratic" and vm.fee_multiplier == Decimal("0.5000")

    # 5 minutes later: inside the 24 h interval, kalshi_series:<series> is not due again.
    clock["now"] = NOW + timedelta(minutes=5)
    rec.maybe_tick()
    assert series.call_count == 6

    # A day later: due again.
    clock["now"] = NOW + timedelta(hours=25)
    rec.maybe_tick()
    assert series.call_count == 12


@respx.mock
def test_settled_rows_normalize_without_errors(env_settings, db_session):
    # F10(b)/R11 item 3: a settled /markets row must not choke the kalshi_markets normalizer.
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
        return_value=httpx.Response(200, json=ODDS, headers={"x-requests-last": "3", "x-requests-remaining": "100"}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(
        return_value=httpx.Response(200, json={}, headers={"x-requests-last": "2", "x-requests-remaining": "98"}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={"orderbook_fp": {}}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert len(_settled_rows(db_session, run.id)) == 6
    assert run.notes["normalize_errors"] == []
    # Review round 1 addendum: _handle skips settled-status pages internally (they stay raw for
    # phase 3), but each row still passes through _drain_batch and counts as processed, so the
    # watermark advances over all 12 raw /markets rows, not just the 6 open ones.
    assert run.notes["normalized"].get("kalshi_markets", 0) == 12
    # The single https://k/markets mock answers every series' open fetch with the same KM
    # fixture (2 valid markets; "WEIRD" fails classify_market), so 6 open raw rows each add a
    # venue_quotes row per market (unique on (raw_id, venue_market_id)); the 6 settled raw rows
    # are counted as processed but contribute none.
    assert db_session.query(VenueQuote).count() == 12


@respx.mock
def test_partial_kalshi_pagination_is_an_error_and_not_marked_fetched(env_settings, db_session):
    from harness.recorder.store import get_source_state
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))

    def pages(request):
        if "cursor" in dict(request.url.params):
            return httpx.Response(500)
        return httpx.Response(200, json={"cursor": "abc", "markets": [{"ticker": "A", "event_ticker": "KXNFLGAME-26SEP21NYGLAR"}]})

    respx.get("https://k/markets").mock(side_effect=pages)
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "error"
    assert "partial pagination" in json.dumps(run.notes["errors"])
    assert get_source_state(db_session, "kalshi_markets:KXNFLGAME") is None
    # The settled fetch paginates the same way, so its own hourly guard must stay unset too;
    # without this the row count below is the only evidence it ran, and that cannot tell the
    # two /markets families apart.
    assert get_source_state(db_session, "kalshi_settled:KXNFLGAME") is None
    # 2 pages (first 200, cursor page 500) each for the open and the settled fetch, x6 series.
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets").count() == 24


@respx.mock
def test_non_2xx_response_marks_run_error(env_settings, db_session):
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(401, json={"message": "INVALID_KEY"}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "error"
    assert "odds_featured:nfl" in json.dumps(run.notes["errors"]) and "http 401" in json.dumps(run.notes["errors"])
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="odds_api", http_status=401).count() == 2
    assert run.odds_remaining is None  # I5: no credit headers must not read as an exhausted account


@respx.mock
def test_alternates_stop_once_the_sub_budget_is_spent(env_settings, db_session):
    # C2: alternates may consume at most 40 s of the tick budget.
    two_events = [dict(ODDS[0]), dict(ODDS[0]) | {"id": "e2b4c6"}]
    mono = {"t": 0.0}

    def alt_response(request):
        mono["t"] += 41.0  # the first alternates call burns the whole sub-budget
        return httpx.Response(200, json={})

    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(return_value=httpx.Response(200, json=two_events))
    alt = respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(side_effect=alt_response)
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))

    rec, _ = _recorder(env_settings, db_session, monotonic=lambda: mono["t"])
    run = rec.maybe_tick()
    assert alt.call_count == 1
    assert run.notes["skipped_alternates"] >= 1
    assert run.budget_exhausted is True


@respx.mock
def test_tick_commits_incrementally_before_finish_run(env_settings, db_session, monkeypatch):
    # I1/I8: raw responses must be durable before finish_run commits the run row.
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(return_value=httpx.Response(200, json=ODDS))
    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(return_value=httpx.Response(200, json={}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))

    seen: dict = {}
    real_finish = store.finish_run

    def spy_finish(session, run, status, **kw):
        with sessionmaker(bind=db_session.get_bind())() as fresh:
            seen["rows"] = fresh.query(RawResponse).filter_by(run_id=run.id).count()
        return real_finish(session, run, status, **kw)

    commits = {"n": 0}
    real_commit = SASession.commit

    def counting_commit(self):
        commits["n"] += 1
        return real_commit(self)

    monkeypatch.setattr(store, "finish_run", spy_finish)
    monkeypatch.setattr(SASession, "commit", counting_commit)

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "ok", run.notes
    assert seen["rows"] > 0  # visible from a fresh session before finish_run committed
    # start_run + after espn + after odds + after kalshi markets + finish_run
    assert commits["n"] >= 5


@respx.mock
def test_non_2xx_trades_watermark_stops_the_refetch_loop(env_settings, db_session):
    # I2: a failed trades call still advances the volume watermark, leaving last_ts unchanged.
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    tr = respx.get("https://k/markets/trades").mock(return_value=httpx.Response(500, json={}))

    rec, clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "degraded", run.notes
    wm = db_session.get(TradeWatermark, "KXNFLGAME-26SEP21NYGLAR-NYG")
    assert wm is not None and Decimal(wm.last_volume_fp) == Decimal("1234.00")
    assert wm.last_ts == NOW - timedelta(hours=24)

    after_first = tr.call_count
    assert after_first > 0
    clock["now"] = NOW + timedelta(minutes=20)
    second = rec.maybe_tick()
    assert tr.call_count == after_first, second.notes


@respx.mock
def test_trades_pagination_is_stored_page_by_page(env_settings, db_session):
    # I3: every trades page is stored and the watermark uses the newest stamp across pages.
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))

    def trade_pages(request):
        if dict(request.url.params).get("cursor"):
            return httpx.Response(200, json={"cursor": "", "trades": [
                {"trade_id": "old", "created_time": "2026-09-09T21:00:00Z"}]})
        return httpx.Response(200, json={"cursor": "pg2", "trades": [
            {"trade_id": "new", "created_time": "2026-09-09T22:59:00Z"}]})

    respx.get("https://k/markets/trades").mock(side_effect=trade_pages)
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "ok", run.notes
    stored = db_session.query(RawResponse).filter_by(run_id=run.id, endpoint="/markets/trades").count()
    assert stored == 12  # 6 series x the same duplicated ticker x 2 pages
    wm = db_session.get(TradeWatermark, "KXNFLGAME-26SEP21NYGLAR-NYG")
    assert wm.last_ts == datetime(2026, 9, 9, 22, 59, tzinfo=timezone.utc)


@respx.mock
def test_trade_gap_is_recorded_when_cursor_unexhausted(env_settings, db_session, monkeypatch):
    import harness.recorder.tick as tick_mod
    monkeypatch.setattr(tick_mod, "TRADES_MAX_PAGES", 2)
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))

    def markets_by_series(request):
        series = dict(request.url.params).get("series_ticker")
        if series == "KXNFLGAME":
            return httpx.Response(200, json=KM)
        return httpx.Response(200, json={"cursor": "", "markets": []})

    respx.get("https://k/markets").mock(side_effect=markets_by_series)
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(side_effect=lambda request: httpx.Response(
        200, json={"cursor": "more", "trades": [
            {"trade_id": "t1", "created_time": "2026-09-09T22:59:00Z"},
            {"trade_id": "t0", "created_time": "2026-09-09T22:00:00Z"}]}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "degraded", run.notes
    gaps = run.notes["trade_gaps"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["ticker"] == "KXNFLGAME-26SEP21NYGLAR-NYG"
    assert gap["pages"] == 2
    assert gap["oldest_seen"].startswith("2026-09-09T22:00")
    wm = db_session.get(TradeWatermark, "KXNFLGAME-26SEP21NYGLAR-NYG")
    assert wm is not None and wm.last_ts == datetime(2026, 9, 9, 22, 59, tzinfo=timezone.utc)
    stored = db_session.query(RawResponse).filter_by(run_id=run.id, endpoint="/markets/trades").count()
    assert stored == 2


@respx.mock
def test_secondary_non_2xx_is_degraded_and_healthz_stays_green(env_settings, db_session):
    # I4: only secondary-source non-2xx -> degraded, and /healthz is 200 with last_status degraded.
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(return_value=httpx.Response(200, json=ODDS))
    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(return_value=httpx.Response(404, json={}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "degraded", run.notes
    assert run.notes["errors"] == []
    assert "odds_alt:e1f2a3" in json.dumps(run.notes["warnings"])

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    client = TestClient(create_app(factory, clock=lambda: NOW))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["last_status"] == "degraded"


@respx.mock
def test_latest_body_is_served_from_the_in_process_cache(env_settings, db_session, monkeypatch):
    # I7: after a successful fetch the fallback body comes from memory, never from raw_responses.
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))

    rec, clock = _recorder(env_settings, db_session)
    assert rec.maybe_tick().status == "ok"

    calls = {"n": 0}
    real = Recorder._latest_body_from_db

    def counting(self, session, source, endpoint):
        calls["n"] += 1
        return real(self, session, source, endpoint)

    monkeypatch.setattr(Recorder, "_latest_body_from_db", counting)
    clock["now"] = NOW + timedelta(seconds=30)
    assert rec.maybe_tick().status == "skipped"
    assert calls["n"] == 0


@respx.mock
def test_tick_runs_normalizer_and_reports_counts(env_settings, db_session):
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": [], "cursor": ""}))
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert "normalized" in run.notes and isinstance(run.notes["normalized"], dict)
    # F5: every run row carries the drop counter, 0 when nothing was dropped, so the
    # verification contract's runs.notes->>'taker_side_missing' invariant is never null.
    assert run.notes["taker_side_missing"] == 0


@respx.mock
def test_tick_counts_prints_dropped_for_a_missing_taker_side(env_settings, db_session):
    # F5 end to end: a print with no usable taker side is dropped by the normalizer and the
    # count reaches runs.notes through the tick's ctx.
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
        return_value=httpx.Response(200, json=ODDS, headers={"x-requests-last": "3", "x-requests-remaining": "100"}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(
        return_value=httpx.Response(200, json={}, headers={"x-requests-last": "2", "x-requests-remaining": "98"}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    trade = {"trade_id": "t-noside", "ticker": "KXNFLGAME-26SEP21NYGLAR-NYG",
             "created_time": "2026-09-09T22:59:00Z", "yes_price_dollars": "0.2300",
             "count_fp": "5.00", "is_block_trade": False}
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"cursor": "", "trades": [trade]}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={"orderbook_fp": {}}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    # every /markets/trades response this tick carried exactly one sideless print
    pages = db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets/trades").count()
    assert pages > 0
    assert run.notes["taker_side_missing"] == pages, run.notes
    assert db_session.query(func.count()).select_from(VenueTrade).scalar() == 0


@respx.mock
def test_normalize_failure_rolls_back_and_finishes_degraded(env_settings, db_session, monkeypatch):
    # A DB error inside normalize_new (e.g. a missing table after an upgrade) must not leave the
    # session in a failed transaction: the tick should roll back, record the warning, and still
    # finish the run (rather than leaving it "running" forever when finish_run's UPDATE raises
    # InFailedSqlTransaction).
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": [], "cursor": ""}))

    from sqlalchemy import text

    def broken_normalize_new(session, *a, **kw):
        session.execute(text("select * from table_that_does_not_exist"))
        return {}

    monkeypatch.setattr("harness.recorder.tick.normalize_new", broken_normalize_new)

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()

    assert run.status == "degraded", run.notes
    assert any("normalize" in w for w in run.notes["warnings"]), run.notes
    assert run.finished_at is not None


@respx.mock
def test_tick_prices_and_signals_when_kalshi_markets_refreshed(env_settings, db_session):
    # Pricing only runs once Kalshi markets have actually refreshed this tick (summaries
    # non-empty), so gap snapshots are computed against fresh quotes.
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()

    assert "pricing" in run.notes, run.notes
    assert run.notes["pricing"]["gaps"] >= 0


@respx.mock
def test_pricing_failure_is_isolated_and_degrades_the_run(env_settings, db_session, monkeypatch):
    import harness.recorder.tick as tick_mod
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))

    def boom(*a, **kw):
        raise RuntimeError("pricing exploded")

    monkeypatch.setattr(tick_mod, "price_and_signal", boom)
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()

    assert run.status == "degraded", run.notes
    assert any("pricing" in w for w in run.notes["warnings"]), run.notes
    assert run.finished_at is not None


@respx.mock
def test_pricing_clock_is_read_after_the_fetches_not_at_tick_start(env_settings, db_session, monkeypatch):
    """Regression: with `now` captured at tick start, the book-line loader's `fetched_at <= now`
    bound excluded the odds fetched seconds later in the same tick and priced against the
    previous fetch, labelling every fair value stale."""
    import harness.recorder.tick as tick_mod
    from harness.db.models import RawResponse

    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))

    seen = {}

    def capture(session, run_id, now, settings, budget_s):
        seen["now"] = now
        return {"gaps": 0}

    monkeypatch.setattr(tick_mod, "price_and_signal", capture)
    rec, clock = _recorder(env_settings, db_session)
    # A ticking clock: every read (tick start, each HTTP fetch, pricing) is one second later.
    t = {"i": 0}

    def ticking():
        t["i"] += 1
        return NOW + timedelta(seconds=t["i"])

    rec.clock = ticking
    rec.odds._http._clock = ticking
    run = rec.maybe_tick()

    latest_fetch = db_session.query(func.max(RawResponse.fetched_at)).filter(RawResponse.run_id == run.id).scalar()
    assert latest_fetch > run.started_at
    assert seen["now"] >= latest_fetch, (seen, latest_fetch, run.started_at)


@respx.mock
def test_forced_tick_fetches_inside_the_interval(env_settings, db_session):
    # A deploy verification needs a real tick now, not at the next cadence slot: force=True ignores
    # every per-source interval, while the default still yields a skipped heartbeat.
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": []}))
    rec, clock = _recorder(env_settings, db_session)
    first = rec.maybe_tick()
    clock["now"] = NOW + timedelta(seconds=30)
    forced = rec.maybe_tick(force=True)
    clock["now"] = NOW + timedelta(seconds=60)
    plain = rec.maybe_tick()
    assert first.status == "ok"
    assert forced.status == "ok"
    assert db_session.query(RawResponse).filter_by(run_id=forced.id).count() > 0
    assert plain.status == "skipped"


def test_forced_tick_never_overrides_the_quiet_window(env_settings, db_session):
    # interval=None is the planner's quiet-window signal; force must not turn it into a paid fetch.
    rec, _ = _recorder(env_settings, db_session)
    rec._force = True
    assert rec._due(None, NOW, None) is False
    assert rec._due(NOW, NOW, 900) is True
    rec._force = False
    assert rec._due(NOW, NOW, 900) is False


# --- Task 12b telemetry ----------------------------------------------------------------


def test_tick_records_run_metrics_with_feed_kind_and_variant_labels(env_settings, db_session):
    """`_pricing_samples`/`_recorder_samples` (harness/recorder/tick.py) produce the
    `pricing.*`/`recorder.*` metric_samples design spec §3.1 names, with a real `feed_kind`
    label off `fair_values` and a real `variant` label off `price_and_signal`'s own result."""
    from tests.test_pipeline import NOW as SEED_NOW
    from tests.test_pipeline import VARIANTS_DIR, _seed

    game, run, markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), SEED_NOW, prune=True)
    pricing = price_and_signal(db_session, run.id, SEED_NOW, env_settings, budget_s=20)
    db_session.commit()

    pricing_samples = _pricing_samples(db_session, run.id, pricing)
    fair_value_samples = [(n, v, l) for n, v, l in pricing_samples if n == "pricing.fair_values"]
    assert fair_value_samples, pricing_samples
    assert all("feed_kind" in labels for _, _, labels in fair_value_samples)

    candidate_samples = [(n, v, l) for n, v, l in pricing_samples if n == "pricing.candidates"]
    assert candidate_samples
    assert any(labels.get("variant") == "tiny" for _, _, labels in candidate_samples)

    ctx = {"errors": [], "trade_gaps": [{"ticker": "t"}], "remaining": 42, "pricing": pricing}
    recorder_samples = _recorder_samples(db_session, run.id, 123, ctx)
    names = {n for n, _, _ in recorder_samples}
    assert {"recorder.tick_ms", "recorder.errors", "recorder.credits_remaining",
           "recorder.trade_gaps"} <= names
    tick_ms = next(v for n, v, _ in recorder_samples if n == "recorder.tick_ms")
    assert tick_ms == 123
    trade_gaps = next(v for n, v, _ in recorder_samples if n == "recorder.trade_gaps")
    assert trade_gaps == 1


@respx.mock
def test_recorder_deploy_event_on_build_sha_change(env_settings, db_session):
    """The recorder's own startup check: a `deploy` operator_event once, the first tick this
    process's build_sha disagrees with the newest prior run's -- and every run row after that
    carries this process's own build_sha (D11's backstop)."""
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": []}))
    db_session.add(Run(started_at=NOW - timedelta(hours=1), status="ok", build_sha="old-sha"))
    db_session.commit()

    env_settings.build_sha = "new-sha"
    rec, clock = _recorder(env_settings, db_session)
    first = rec.maybe_tick()
    assert first.build_sha == "new-sha"

    events = db_session.query(OperatorEvent).filter_by(kind="deploy").all()
    assert len(events) == 1
    assert events[0].ref == {"from": "old-sha", "to": "new-sha"}

    clock["now"] = NOW + timedelta(seconds=60)
    second = rec.maybe_tick(force=True)
    assert second.build_sha == "new-sha"
    # Checked once per process: a second tick under the same (now unchanged) build writes no
    # second deploy event.
    assert db_session.query(OperatorEvent).filter_by(kind="deploy").count() == 1


# --- Fix 14: ESPN midnight-Eastern rollover ---------------------------------------------

# 05:00Z = 01:00 ET (EDT, UTC-4) on 2026-09-09 -- just after the Eastern day rolled, so
# "today" ET is 09-09 and "yesterday" ET is 09-08.
ROLLOVER_NOW = datetime(2026, 9, 9, 5, 0, tzinfo=timezone.utc)
# 00:20Z on 09-09 is 20:20 ET on 09-08 -- a kickoff that fell on yesterday's Eastern date.
YESTERDAY_ET_KICKOFF = datetime(2026, 9, 9, 0, 20, tzinfo=timezone.utc)


def _mock_nfl_scoreboard(dated_body: dict | None = None, plain_body: dict | None = None):
    """Route https://e/nfl/scoreboard, replying with `dated_body` when the request carries a
    `dates=20260908` param and `plain_body` (or an empty scoreboard) otherwise -- the two
    fetches share a path, so only the query params tell them apart."""
    calls = {"plain": [], "dated": []}

    def _side_effect(request):
        params = dict(request.url.params)
        if params.get("dates"):
            calls["dated"].append(params)
            return httpx.Response(200, json=dated_body if dated_body is not None else {"events": []})
        calls["plain"].append(params)
        return httpx.Response(200, json=plain_body if plain_body is not None else {"events": []})

    respx.get("https://e/nfl/scoreboard").mock(side_effect=_side_effect)
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": []}))
    return calls


@respx.mock
def test_espn_rollover_fetch_issued_for_pending_yesterday_game(env_settings, db_session):
    """A non-terminal game whose kickoff fell on the previous Eastern date makes the tick
    issue the extra dated fetch (fix 14, journal 43-44: game 114)."""
    calls = _mock_nfl_scoreboard()
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=YESTERDAY_ET_KICKOFF,
                        status="in_progress", espn_event_id="900"))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    assert calls["dated"] == [{"dates": "20260908"}]


@respx.mock
def test_espn_rollover_fetch_not_issued_once_game_is_terminal(env_settings, db_session):
    """Once the previous-date game is terminal, the extra fetch stops (fix 14)."""
    calls = _mock_nfl_scoreboard()
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=YESTERDAY_ET_KICKOFF,
                        status="final", home_score=24, away_score=17, espn_event_id="900"))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    assert calls["dated"] == []
    assert calls["plain"]  # today's undated fetch still runs


@respx.mock
def test_espn_rollover_cadence_key_is_independent_of_todays(env_settings, db_session):
    """The dated fetch's cadence key (`espn_dated:<sport>:<date>`) is tracked separately from
    today's (`espn:<sport>`): pre-marking today's key as just-fetched must not suppress the
    dated fetch, which has never run (fix 14)."""
    calls = _mock_nfl_scoreboard()
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=YESTERDAY_ET_KICKOFF,
                        status="in_progress", espn_event_id="900"))
    store.set_source_state(db_session, "espn:nfl", ROLLOVER_NOW - timedelta(seconds=30))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    assert calls["plain"] == []  # today's key says "not due yet"
    assert calls["dated"] == [{"dates": "20260908"}]  # the dated key, unset, still fires


@respx.mock
def test_espn_rollover_heals_stuck_game_to_final(env_settings, db_session):
    """End to end: the dated fetch's body carries a `final` event for the stuck game, and the
    normal normalize pass inside the same tick links it to `status=final` with scores (fix
    14, game 114's shape)."""
    dated_body = {"events": [{"id": "900", "date": "2026-09-09T00:20Z",
                  "status": {"type": {"name": "STATUS_FINAL"}},
                  "competitions": [{"competitors": [
                      {"homeAway": "home", "score": "24", "team": {"id": "14", "displayName": "Los Angeles Rams"}},
                      {"homeAway": "away", "score": "17", "team": {"id": "19", "displayName": "New York Giants"}}]}]}]}
    _mock_nfl_scoreboard(dated_body=dated_body)
    seed_teams_from_espn(db_session, "nfl", NFL_TEAMS)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=YESTERDAY_ET_KICKOFF,
                        status="in_progress", espn_event_id="900"))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    game = db_session.query(Game).filter_by(espn_event_id="900").one()
    assert (game.status, game.home_score, game.away_score) == ("final", 24, 17)


# --- Fix round 1: review findings ---------------------------------------------------------

@respx.mock
def test_espn_latest_body_after_restart_ignores_a_dated_row(env_settings, db_session, monkeypatch):
    """Fix round 1, Important 1: `_latest_body_from_db` (harness/recorder/tick.py:121-124) must
    not hand back a dated rollover body as "today's" scoreboard. Simulates a restart: both
    `espn:nfl` and the dated key are already fresh (so neither fetch runs this tick and a fresh
    Recorder's empty `_last_good` cache must fall through to the DB), and the dated row (stored
    later, as fix 14's own extra fetch would be) must not be the one that comes back."""
    import harness.recorder.tick as tick_mod

    _mock_nfl_scoreboard()  # neither route should be hit: both cadence keys are fresh
    db_session.add(RawResponse(fetched_at=NOW - timedelta(minutes=5), run_id=1, source="espn",
                               endpoint="/nfl/scoreboard", params={}, http_status=200, body=ESPN))
    dated_body = {"events": [{"id": "999", "date": "2026-09-08T20:20Z",
                  "status": {"type": {"name": "STATUS_IN_PROGRESS"}},
                  "competitions": [{"competitors": [
                      {"homeAway": "home", "team": {"id": "1", "displayName": "H"}},
                      {"homeAway": "away", "team": {"id": "2", "displayName": "A"}}]}]}]}
    db_session.add(RawResponse(fetched_at=NOW - timedelta(minutes=1), run_id=1, source="espn",
                               endpoint="/nfl/scoreboard", params={"dates": "20260908"}, http_status=200,
                               body=dated_body))
    store.set_source_state(db_session, "espn:nfl", NOW - timedelta(seconds=30))
    store.set_source_state(db_session, "espn_dated:nfl:20260908", NOW - timedelta(seconds=30))
    db_session.commit()

    seen: dict = {}
    real_parse = tick_mod.parse_kickoffs

    def _capture(sport, body):
        if sport == "nfl":
            seen["nfl"] = body
        return real_parse(sport, body)

    monkeypatch.setattr(tick_mod, "parse_kickoffs", _capture)

    rec, _ = _recorder(env_settings, db_session)  # a fresh Recorder: _last_good starts empty
    rec.maybe_tick()

    ids = {str(e["id"]) for e in (seen.get("nfl") or {}).get("events", [])}
    assert "401872656" in ids  # today's fixture event made it through
    assert "999" not in ids  # the dated row's event never did


def test_latest_body_from_db_excludes_dated_rows_directly(env_settings, db_session):
    """Fix round 1, Important 1, unit level: `_latest_body_from_db` itself must skip a row
    whose `params` carries `dates`, even when that row is the newest by `fetched_at`."""
    db_session.add(RawResponse(fetched_at=NOW - timedelta(minutes=5), run_id=1, source="espn",
                               endpoint="/nfl/scoreboard", params={}, http_status=200, body=ESPN))
    db_session.add(RawResponse(fetched_at=NOW, run_id=1, source="espn", endpoint="/nfl/scoreboard",
                               params={"dates": "20260908"}, http_status=200, body={"events": [{"id": "999"}]}))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session)
    body = Recorder._latest_body_from_db(rec, db_session, "espn", "/nfl/scoreboard")
    assert body == ESPN


@respx.mock
def test_espn_rollover_window_spans_the_full_eastern_day_across_dst_fallback(env_settings, db_session):
    """Fix round 1, Minor 1: the rollover window must be `[midnight ET yesterday, midnight ET
    today)`, not `day_start + 24h` -- on the US Eastern fall-back day (clocks go EDT -> EST),
    that 24h-flat window is an hour short and misses a kickoff between 23:00 and 24:00 ET on
    the DST-transition day. 2026-11-01 is that Sunday; a kickoff at 23:30 ET that day is
    04:30Z on 11-02 once EST (UTC-5) is in effect."""
    calls = _mock_nfl_scoreboard()
    dst_kickoff = datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc)  # 23:30 ET on 2026-11-01
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=dst_kickoff,
                        status="in_progress", espn_event_id="900"))
    db_session.commit()

    dst_now = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)  # 01:00 ET on 2026-11-02 (EST)
    rec, _ = _recorder(env_settings, db_session, now=dst_now)
    rec.maybe_tick()

    assert calls["dated"] == [{"dates": "20261101"}]


@respx.mock
def test_espn_rollover_uses_eastern_not_local_tz(env_settings, db_session):
    """Fix round 1, Minor 2: a kickoff between 00:00 and 01:00 ET pins the zone choice --
    00:30 ET on 2026-09-08 is 23:30 CT on 2026-09-07 (a different calendar date in
    `Settings.tz_local`, America/Chicago). The rollover must key off ESPN's Eastern date, so
    this game (Eastern date 09-08) must be picked up as "yesterday" relative to a `now` whose
    Eastern date is 09-09 -- a Chicago-keyed implementation would miss it."""
    calls = _mock_nfl_scoreboard()
    et_0030_kickoff = datetime(2026, 9, 8, 4, 30, tzinfo=timezone.utc)  # 00:30 ET / 23:30 CT
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=et_0030_kickoff,
                        status="in_progress", espn_event_id="900"))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    assert calls["dated"] == [{"dates": "20260908"}]


@respx.mock
def test_espn_rollover_window_includes_2330_et_yesterday(env_settings, db_session):
    """Fix round 1, Minor 2: pins the window's lower/upper edge -- 23:30 ET on the previous
    Eastern date is inside the window and must trigger the dated fetch."""
    calls = _mock_nfl_scoreboard()
    edge_kickoff = datetime(2026, 9, 9, 3, 30, tzinfo=timezone.utc)  # 23:30 ET on 2026-09-08
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=edge_kickoff,
                        status="in_progress", espn_event_id="900"))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    assert calls["dated"] == [{"dates": "20260908"}]


@respx.mock
def test_espn_rollover_window_excludes_0030_et_today(env_settings, db_session):
    """Fix round 1, Minor 2: pins the window's edge on the other side -- 00:30 ET on *today's*
    Eastern date is today's game, not yesterday's, and must not trigger the dated fetch."""
    calls = _mock_nfl_scoreboard()
    today_kickoff = datetime(2026, 9, 9, 4, 30, tzinfo=timezone.utc)  # 00:30 ET on 2026-09-09 (today)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=today_kickoff,
                        status="in_progress", espn_event_id="901"))
    db_session.commit()

    rec, _ = _recorder(env_settings, db_session, now=ROLLOVER_NOW)
    rec.maybe_tick()

    assert calls["dated"] == []
