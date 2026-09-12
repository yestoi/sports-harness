import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import event, text

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


def test_settled_markets_rows_are_skipped_by_the_kalshi_markets_family(db_session):
    # Review round 1, findings 1+2: an hourly settled /markets fetch (up to 8 days of history)
    # must not touch an already-known venue_markets row's last_seen_at/match_reason, nor add a
    # venue_quotes row, because the dashboard's "in play" views key off last_seen_at and
    # build_gap_snapshots keys off venue_quotes.run_id -- both would otherwise be flooded by
    # settled markets. An open-status row for the same ticker must still update both.
    ensure_partitions(db_session, NOW)
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    old_seen = NOW - timedelta(days=3)
    vm = VenueMarket(venue="kalshi", ticker="KXNFLGAME-26SEP21NYGLAR-NYG", event_ticker="KXNFLGAME-26SEP21NYGLAR",
                     series_ticker="KXNFLGAME", market_type="moneyline", side=None,
                     match_confidence=Decimal("0"), match_status="unmatched", match_reason="OLD_REASON_SENTINEL",
                     first_seen_raw_id=1, last_seen_at=old_seen, game_id=None)
    db_session.add(vm)
    db_session.flush()
    quotes_before = db_session.query(VenueQuote).count()
    body = {"cursor": "", "markets": [{"ticker": "KXNFLGAME-26SEP21NYGLAR-NYG", "event_ticker": "KXNFLGAME-26SEP21NYGLAR",
                                       "yes_bid_dollars": "0.5000", "yes_ask_dollars": "0.5100", "volume_fp": "10.00"}]}

    settled_ts = NOW
    _raw(db_session, run.id, "kalshi", "/markets", {"series_ticker": "KXNFLGAME", "status": "settled"}, body, ts=settled_ts)
    normalize_new(db_session)

    db_session.refresh(vm)
    assert vm.last_seen_at == old_seen
    assert vm.match_reason == "OLD_REASON_SENTINEL"
    assert db_session.query(VenueQuote).count() == quotes_before

    open_ts = NOW + timedelta(minutes=1)
    _raw(db_session, run.id, "kalshi", "/markets", {"series_ticker": "KXNFLGAME"}, body, ts=open_ts)
    normalize_new(db_session)

    db_session.refresh(vm)
    assert vm.last_seen_at == open_ts
    assert vm.match_reason == "no event title recorded"
    assert db_session.query(VenueQuote).count() == quotes_before + 1


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


def test_normalize_counts_sideless_prints_into_ctx(db_session):
    # F5: the drop counter has to reach runs.notes, so _handle must hand insert_trades the
    # runner's ctx. Two raw responses so the per-response accumulation is visible.
    ensure_partitions(db_session, NOW)
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    good, bad = _trade("keep-1"), _trade("drop-1")
    bad["trades"][0].pop("taker_side")
    bad["trades"].append({**bad["trades"][0], "trade_id": "drop-2"})
    _raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": "K1"}, good)
    _raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": "K1"}, bad)
    ctx: dict = {}
    assert normalize_new(db_session, ctx=ctx)["kalshi_trades"] == 2
    assert {t.trade_id for t in db_session.query(VenueTrade).all()} == {"keep-1"}
    assert ctx["taker_side_missing"] == 2


# --- fix 45: the events-cache read must ride the (source, endpoint, id) index -----------------
#
# 23:23/23:26/23:27 CT on 2026-09-11 and 00:06 CT on 2026-09-12: `_load_events_cache`'s select
# had no index leading with (source, endpoint) in id order, so the planner walked raw_responses'
# primary key backwards across every partition with the three predicates only a filter -- cost
# 3,887 warm, past the 30 s statement timeout cold on the NAS. `ix_raw_source_endpoint_id
# (source, endpoint, id)` is the scan key.

def _index_names(node) -> set[str]:
    """Every `Index Name` anywhere in an `explain (format json)` plan tree (the same helper
    `tests/test_gaps.py`'s fix 42 precedent uses, duplicated locally rather than imported across
    test modules)."""
    names: set[str] = set()
    if isinstance(node, list):
        for item in node:
            names |= _index_names(item)
    elif isinstance(node, dict):
        if "Index Name" in node:
            names.add(node["Index Name"])
        if "Plan" in node:
            names |= _index_names(node["Plan"])
        if "Plans" in node:
            names |= _index_names(node["Plans"])
    return names


def _capture_events_cache_select(db_session):
    """The exact statement `_load_events_cache` issues, captured off the connection rather than
    rebuilt here. Distinguished from `_drain_batch`'s per-family reads (which also filter
    `raw_responses` on `http_status = 200`) by its `order by ... desc`: `_drain_batch` always
    orders ascending so a batch resumes where the last one stopped, and only the events cache
    reads newest-first."""
    captured = []

    def before(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if (lowered.lstrip().startswith("select") and "raw_responses" in lowered
                and " desc" in lowered):
            captured.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", before)
    try:
        normalize_new(db_session)
    finally:
        event.remove(bind, "before_cursor_execute", before)
    assert captured, "_load_events_cache issued no statement over raw_responses"
    return captured[0]


def test_ix_raw_source_endpoint_id_is_chosen_for_the_events_cache_select(db_session):
    """The plan, not just the index's presence: without a scan key on (source, endpoint) in id
    order the planner has raw_responses' primary key to walk backwards across every partition,
    which is exactly what timed out on the NAS.

    Seeded the shape that makes the difference visible -- hundreds of other-family rows crowding
    the id space the /events rows sit in -- and `enable_seqscan` off, so a sequential scan cannot
    stand in for the index winning on its own merits (fix 35's
    `test_ix_fair_leg_lookup_is_chosen_for_the_leg_query` and fix 42's
    `test_ix_quotes_run_market_is_chosen_for_the_gap_select` are the precedent for both)."""
    run = _seed_raw(db_session)
    for i in range(400):
        _raw(db_session, run.id, "espn", "/scoreboard", {"i": i}, {"events": []})
    db_session.execute(text("analyze raw_responses"))

    statement, parameters = _capture_events_cache_select(db_session)
    db_session.execute(text("set local enable_seqscan = off"))
    raw = db_session.connection().connection
    with raw.cursor() as cur:
        cur.execute(f"explain (format json) {statement}", parameters)
        plan = cur.fetchone()[0]
    names = _index_names(plan)
    # There is no single index named exactly like the parent to scan directly -- each partition's
    # own child carries its own name, and the two ways a child comes to exist name it two
    # different ways: `_ensure_partitioned_concurrent_indexes`'s own recipe (a populated database
    # missing the parent) spells it `ix_raw_source_endpoint_id_<suffix>`, but a fresh test
    # database builds the parent validly with zero partitions (`create_all`, immediately valid),
    # so every partition here was attached automatically by Postgres itself when `ensure_
    # partitions` created it -- and Postgres's own default child name is
    # `<partition>_source_endpoint_id_idx`. Both spellings share this substring.
    assert any("source_endpoint_id" in n for n in names), plan
