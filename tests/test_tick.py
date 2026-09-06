import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx
from sqlalchemy.orm import sessionmaker

from harness.db.models import RawResponse, Run, TradeWatermark
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.recorder.tick import Recorder
from harness.venues.kalshi.public import KalshiPublic

FIXD = Path(__file__).parent / "fixtures"
ODDS = json.loads((FIXD / "odds_featured_nfl.json").read_text())
ESPN = json.loads((FIXD / "espn_nfl_scoreboard.json").read_text())
KM = json.loads((FIXD / "kalshi_markets_page.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wed 18:00 CT, 1h20m before the 19:20 kickoff


def _recorder(env_settings, db_session, now=NOW):
    clock = {"now": now}
    http = HttpClient(1, sleep=lambda s: None, clock=lambda: clock["now"])
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=0, sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return Recorder(env_settings, factory, odds, espn, kalshi, clock=lambda: clock["now"]), clock


@respx.mock
def test_first_tick_fetches_everything_and_records(env_settings, db_session):
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
        return_value=httpx.Response(200, json=ODDS, headers={"x-requests-last": "3", "x-requests-remaining": "100"}))
    alt = respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(
        return_value=httpx.Response(200, json={}, headers={"x-requests-last": "2", "x-requests-remaining": "98"}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
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
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi").count() == 6


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
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi").count() == 6
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="odds_api").count() == 2


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
