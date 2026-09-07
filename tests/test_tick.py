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

from harness.db.models import RawResponse, Run, TradeWatermark
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.health import create_app
from harness.normalize import runner as runner_mod
from harness.recorder import store
from harness.recorder.tick import Recorder
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
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets").count() == 6


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
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets").count() == 6
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
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/markets").count() == 12


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
