import json
from datetime import datetime, timezone
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
