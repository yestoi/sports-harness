import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.db.models import Game, NormalizeState, OddsSnapshot, RawResponse, Run, VenueMarket, VenueQuote, VenueTrade
from harness.db.schema import ensure_partitions
from harness.matching.teams import seed_teams_from_espn
from harness.normalize import runner as runner_mod
from harness.normalize.runner import normalize_new, reprocess

FIXD = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_events_cache():
    # _EVENTS is a module-level, process-wide cache (by design, see runner.py); clear it
    # between tests so one test's DB rows never leak into another test's in-memory cache.
    runner_mod._EVENTS.clear()
    yield
    runner_mod._EVENTS.clear()


def _raw(session, run_id, source, endpoint, params, body, ts=NOW):
    r = RawResponse(run_id=run_id, source=source, endpoint=endpoint, params=params, fetched_at=ts, http_status=200, body=body)
    session.add(r)
    session.flush()
    return r.id


def _seed_raw(db_session):
    ensure_partitions(db_session, NOW)
    seed_teams_from_espn(db_session, "nfl", json.loads((FIXD / "espn_teams_nfl.json").read_text()))
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    feat = [{"id": "ev1", "commence_time": "2026-09-21T00:20:00Z", "home_team": "Los Angeles Rams", "away_team": "New York Giants",
             "bookmakers": [{"key": "pinnacle", "last_update": "2026-09-09T22:00:00Z", "markets": [
                 {"key": "h2h", "outcomes": [{"name": "Los Angeles Rams", "price": 1.5}, {"name": "New York Giants", "price": 2.7}]}]}]}]
    _raw(db_session, run.id, "odds_api", "/sports/americanfootball_nfl/odds", {"markets": "featured"}, feat)
    _raw(db_session, run.id, "kalshi", "/events", {"series_ticker": "KXNFLGAME"},
         {"cursor": "", "events": [{"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"}]})
    km = json.loads((FIXD / "kalshi_markets_page.json").read_text())
    _raw(db_session, run.id, "kalshi", "/markets", {"series_ticker": "KXNFLGAME"}, km)
    _raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": "KXNFLGAME-26SEP21NYGLAR-NYG", "min_ts": "x"},
         json.loads((FIXD / "kalshi_trades_page.json").read_text()))
    return run


def test_normalize_new_is_incremental_and_ordered(db_session):
    _seed_raw(db_session)
    counts = normalize_new(db_session)
    assert counts["odds_featured"] == 1 and counts["kalshi_markets"] == 1 and counts["kalshi_trades"] == 1
    assert db_session.query(Game).count() == 1
    assert db_session.query(OddsSnapshot).count() == 2
    assert db_session.query(VenueMarket).filter_by(match_status="matched").count() == 1
    assert db_session.query(VenueQuote).count() == 2
    assert db_session.query(VenueTrade).count() == 2
    again = normalize_new(db_session)
    assert sum(again.values()) == 0
    st = {s.family: s.last_raw_id for s in db_session.query(NormalizeState).all()}
    assert st["kalshi_trades"] > 0


def test_reprocess_truncate_rebuilds_same_counts(db_session):
    _seed_raw(db_session)
    normalize_new(db_session)
    before = (db_session.query(OddsSnapshot).count(), db_session.query(VenueQuote).count(), db_session.query(VenueTrade).count())
    reprocess(db_session, truncate=True)
    after = (db_session.query(OddsSnapshot).count(), db_session.query(VenueQuote).count(), db_session.query(VenueTrade).count())
    assert before == after and before[0] == 2


def _trade(trade_id: str) -> dict:
    return {"trades": [{"trade_id": trade_id, "ticker": "KXNFLGAME-26SEP21NYGLAR-NYG",
                        "created_time": "2026-09-09T22:00:00Z", "yes_price_dollars": "0.5000",
                        "count_fp": "1.00", "taker_side": "yes", "is_block_trade": False}]}


def test_poison_row_is_isolated_by_savepoint_and_watermark_advances(db_session):
    # C1: a row that raises a real database error must not discard the batch, must be
    # recorded with its exception text, and must not be retried forever.
    ensure_partitions(db_session, NOW)
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    bad_id = _raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": "K1"}, _trade("x" * 80))
    good_id = _raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": "K1"}, _trade("good-1"))
    ctx: dict = {}
    counts = normalize_new(db_session, ctx=ctx)
    assert counts["kalshi_trades"] == 1
    assert db_session.query(VenueTrade).filter_by(trade_id="good-1").count() == 1
    errs = [e["kalshi_trades"] for e in ctx["normalize_errors"] if "kalshi_trades" in e]
    assert [e["raw_id"] for e in errs] == [bad_id]
    assert "64" in errs[0]["error"] or "too long" in errs[0]["error"]
    assert db_session.get(NormalizeState, "kalshi_trades").last_raw_id == good_id


def test_reprocess_truncate_keeps_websocket_trades(db_session):
    # C2: ws trades exist nowhere in raw_responses; a rebuild must not destroy them.
    ensure_partitions(db_session, NOW)
    db_session.add_all([
        VenueTrade(venue="kalshi", trade_id="ws-1", ticker="K1", ts=NOW, yes_price=Decimal("0.5"),
                   count=Decimal("1.00"), taker_side="yes", is_block=False, source="ws", raw_id=None),
        VenueTrade(venue="kalshi", trade_id="rest-1", ticker="K1", ts=NOW, yes_price=Decimal("0.5"),
                   count=Decimal("1.00"), taker_side="yes", is_block=False, source="rest", raw_id=1),
    ])
    db_session.flush()
    reprocess(db_session, truncate=True)
    assert {t.trade_id for t in db_session.query(VenueTrade).all()} == {"ws-1"}


def test_normalize_drains_multiple_batches_in_one_call(db_session):
    # I4: a family with more than `batch` pending rows must fully drain within one call.
    ensure_partitions(db_session, NOW)
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    for _ in range(1200):
        db_session.add(RawResponse(run_id=run.id, source="kalshi", endpoint="/events",
                                   params={"series_ticker": "KXNFLGAME"}, fetched_at=NOW,
                                   http_status=200, body={"cursor": "", "events": []}))
    db_session.flush()
    counts = normalize_new(db_session, batch=500)
    assert counts["kalshi_events"] == 1200


def test_normalize_new_stops_at_deadline_and_resumes(db_session):
    """A zero time budget processes at least one row per family, commits the watermark through the
    last processed row, and the next call continues from there."""
    ensure_partitions(db_session, NOW)
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    ids = [_raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": f"T{i}", "min_ts": "x"},
                {"trades": []}) for i in range(6)]
    db_session.commit()
    first = normalize_new(db_session, batch=500, time_budget_s=0)
    assert 1 <= first["kalshi_trades"] < 6
    st = db_session.get(NormalizeState, "kalshi_trades")
    assert st.last_raw_id == ids[first["kalshi_trades"] - 1]
    total = first["kalshi_trades"]
    for _ in range(10):
        more = normalize_new(db_session, batch=500, time_budget_s=30)
        total += more["kalshi_trades"]
        if more["kalshi_trades"] == 0:
            break
    assert total == 6
    assert db_session.get(NormalizeState, "kalshi_trades").last_raw_id == ids[-1]
