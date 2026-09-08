import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event

from harness.db.models import Game, OrderbookSnapshot, VenueMarket, VenueQuote, VenueTrade
from harness.matching.kalshi import compose_match_key
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.kalshi import (apply_series_fee, insert_orderbook, insert_trades, insert_venue_quotes,
                                      upsert_venue_markets)

FIXD = Path(__file__).parent / "fixtures"
KM = json.loads((FIXD / "kalshi_markets_page.json").read_text())["markets"]  # phase 0 fixture: NYG game + KC spread
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
EVENTS = {"KXNFLGAME-26SEP21NYGLAR": {"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"},
          "KXNFLSPREAD-26SEP14DENKC": {"event_ticker": "KXNFLSPREAD-26SEP14DENKC", "title": "Denver vs Kansas City"}}


def test_upsert_venue_markets_matches_and_classifies(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)  # must include Giants, Rams, Chiefs (fixture) — Broncos absent on purpose
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    res = upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    assert res.new == 2 and res.matched == 1 and res.unmatched == 1
    nyg = db_session.query(VenueMarket).filter_by(ticker="KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert nyg.market_type == "moneyline" and nyg.match_status == "matched" and nyg.side_team_id == 19
    kc = db_session.query(VenueMarket).filter_by(ticker="KXNFLSPREAD-26SEP14DENKC-KC7").one()
    assert kc.market_type == "spread" and kc.threshold == Decimal("6.5") and kc.match_status == "unmatched"
    assert "unresolved: Denver" in kc.match_reason
    res2 = upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=2, fetched_at=NOW)
    assert res2.new == 0


def test_upsert_venue_markets_records_grid_fee_shard_expiration_and_match_key(db_session):
    """Task 3b item 2 (F8/R10): the executor-facing metadata a Kalshi market carries, refreshed
    on every fetch regardless of match outcome, plus `match_key` -- Task 2's matched shape,
    composed the moment a market resolves to a game and left NULL while it doesn't."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    grid = [{"start": "0.0000", "end": "1.0000", "step": "0.0001"}]
    markets = [
        dict(KM[0], expected_expiration_time="2026-09-24T00:20:00Z", price_level_structure="linear_cent",
             price_ranges=grid, exchange_index=2),
        dict(KM[1], expected_expiration_time="2026-09-17T00:20:00Z", price_level_structure="linear_cent",
             price_ranges=[], exchange_index=0),
    ]
    upsert_venue_markets(db_session, "nfl", markets, EVENTS, raw_id=1, fetched_at=NOW)

    nyg = db_session.query(VenueMarket).filter_by(ticker="KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert nyg.expected_expiration_time == datetime(2026, 9, 24, 0, 20, tzinfo=timezone.utc)
    assert nyg.price_level_structure == "linear_cent"
    assert nyg.price_ranges == grid
    assert nyg.exchange_index == 2
    assert nyg.match_status == "matched"
    assert nyg.match_key == compose_match_key(nyg.game_id, "moneyline", nyg.side_team_id, nyg.side, nyg.threshold)
    assert nyg.match_key == f"{nyg.game_id}:moneyline:19::"

    kc = db_session.query(VenueMarket).filter_by(ticker="KXNFLSPREAD-26SEP14DENKC-KC7").one()
    assert kc.match_status == "unmatched"
    assert kc.match_key is None
    assert kc.exchange_index == 0

    # A metadata refresh on an already-matched market updates the grid/expiration/shard without
    # re-running the matcher or clearing match_key.
    refreshed = [dict(KM[0], expected_expiration_time="2026-09-25T00:20:00Z", price_level_structure="custom_grid",
                      price_ranges=[], exchange_index=3)]
    upsert_venue_markets(db_session, "nfl", refreshed, EVENTS, raw_id=2, fetched_at=NOW)
    db_session.refresh(nyg)
    assert nyg.expected_expiration_time == datetime(2026, 9, 25, 0, 20, tzinfo=timezone.utc)
    assert nyg.price_level_structure == "custom_grid"
    assert nyg.exchange_index == 3
    assert nyg.match_key == f"{nyg.game_id}:moneyline:19::"


def test_fuzzy_match_key_survives_a_tick_with_no_event_cache_entry(db_session):
    """Controller ruling (Task 3b fix round 1, Important 1): match_key is cleared only in the
    branches that null game_id -- a key exists exactly when a game_id does. A fuzzy row re-runs
    matching every tick (the early-continue only covers matched/manual), so a tick whose event
    cache lacks this market's event ticker must not erase its key while game_id and
    match_status stay put."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    nyg = db_session.query(VenueMarket).filter_by(ticker="KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert nyg.match_status == "matched"
    game_id, key = nyg.game_id, nyg.match_key
    assert game_id is not None and key is not None
    # Simulate a fuzzy match: this status re-runs the matcher on every tick, unlike matched/manual.
    nyg.match_status = "fuzzy"
    db_session.flush()

    events_missing = {k: v for k, v in EVENTS.items() if k != "KXNFLGAME-26SEP21NYGLAR"}
    upsert_venue_markets(db_session, "nfl", KM, events_missing, raw_id=2, fetched_at=NOW)
    db_session.refresh(nyg)
    assert nyg.game_id == game_id
    assert nyg.match_status == "fuzzy"
    assert nyg.match_key == key


def test_non_linear_cent_matched_market_is_counted_in_run_notes(db_session):
    """F44: a matched market whose price grid isn't the football default is a pricing risk the
    dashboard alarms on; the count is written into runs.notes every tick, not just once."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    custom = [dict(KM[0], price_level_structure="custom_grid")]

    ctx = {}
    upsert_venue_markets(db_session, "nfl", custom, EVENTS, raw_id=1, fetched_at=NOW, ctx=ctx)
    assert ctx["non_linear_cent"] == 1

    # Already matched: the second fetch skips re-matching but still counts the grid every tick.
    ctx2 = {}
    upsert_venue_markets(db_session, "nfl", custom, EVENTS, raw_id=2, fetched_at=NOW, ctx=ctx2)
    assert ctx2["non_linear_cent"] == 1

    # linear_cent never counts, and an unmatched market never counts either.
    linear = [dict(KM[0], ticker="KXNFLGAME-26SEP21NYGLAR-OTHER", price_level_structure="linear_cent")]
    ctx3 = {}
    upsert_venue_markets(db_session, "nfl", linear, EVENTS, raw_id=3, fetched_at=NOW, ctx=ctx3)
    assert ctx3.get("non_linear_cent", 0) == 0

    ctx4 = {}
    unmatched_custom = [dict(KM[1], price_level_structure="custom_grid")]
    upsert_venue_markets(db_session, "nfl", unmatched_custom, EVENTS, raw_id=4, fetched_at=NOW, ctx=ctx4)
    assert ctx4.get("non_linear_cent", 0) == 0

    # Minor 1 (Task 3b fix round 1): a matched market with no price_level_structure at all
    # (the field absent from the fetch, so None) counts too -- the brief says "not linear_cent",
    # and the docstring's own rationale (snap_to_grid falls back to whole cents for a grid it
    # has never seen) is exactly the None case.
    ctx5 = {}
    none_grid = [dict(KM[0], ticker="KXNFLGAME-26SEP21NYGLAR-NONEGRID")]
    assert "price_level_structure" not in none_grid[0]
    upsert_venue_markets(db_session, "nfl", none_grid, EVENTS, raw_id=6, fetched_at=NOW, ctx=ctx5)
    assert ctx5["non_linear_cent"] == 1

    # ctx is optional: callers that don't care about the alarm never see a KeyError.
    upsert_venue_markets(db_session, "nfl", custom, EVENTS, raw_id=5, fetched_at=NOW)


def test_apply_series_fee_writes_fee_type_and_multiplier_by_series(db_session):
    """Task 3b item 3: GET /series/{series_ticker}'s fee shape written onto every
    venue_markets row already recorded for that series, and only that series."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    db_session.commit()

    apply_series_fee(db_session, "KXNFLGAME", {"series": {"fee_type": "quadratic", "fee_multiplier": "0.5"}})
    db_session.commit()

    nyg = db_session.query(VenueMarket).filter_by(ticker="KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert nyg.fee_type == "quadratic" and nyg.fee_multiplier == Decimal("0.5000")
    kc = db_session.query(VenueMarket).filter_by(ticker="KXNFLSPREAD-26SEP14DENKC-KC7").one()
    assert kc.fee_type is None and kc.fee_multiplier is None  # different series, untouched

    # An empty/absent body is a no-op, not a write of NULLs over an already-recorded fee shape.
    apply_series_fee(db_session, "KXNFLGAME", {})
    db_session.commit()
    db_session.refresh(nyg)
    assert nyg.fee_type == "quadratic"


def test_quotes_orderbook_trades_idempotent(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    assert insert_venue_quotes(db_session, KM, raw_id=1, run_id=1, fetched_at=NOW) == 2
    assert insert_venue_quotes(db_session, KM, raw_id=1, run_id=1, fetched_at=NOW) == 0
    q = db_session.query(VenueQuote).join(VenueMarket, VenueMarket.id == VenueQuote.venue_market_id).filter(VenueMarket.ticker == "KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert (q.yes_bid, q.yes_ask, q.volume) == (Decimal("0.2200"), Decimal("0.2300"), Decimal("1234.00"))
    ob = json.loads((FIXD / "kalshi_orderbook.json").read_text())
    assert insert_orderbook(db_session, "KXNFLGAME-26SEP21NYGLAR-NYG", ob, raw_id=5, fetched_at=NOW) is True
    assert insert_orderbook(db_session, "KXNFLGAME-26SEP21NYGLAR-NYG", ob, raw_id=5, fetched_at=NOW) is False
    tr = json.loads((FIXD / "kalshi_trades_page.json").read_text())
    assert insert_trades(db_session, tr, raw_id=6) == 2
    assert insert_trades(db_session, tr, raw_id=6) == 0
    t = db_session.query(VenueTrade).order_by(VenueTrade.ts).first()
    assert t.source == "rest" and t.yes_price == Decimal("0.2300") and t.taker_side in ("yes", "no")


def test_upsert_venue_markets_preloads_existing_rows_in_one_query(db_session):
    # Kalshi pages carry up to 1,000 markets; querying VenueMarket once per market stalls the
    # tick for minutes on a backlog. upsert_venue_markets must preload the page's existing
    # VenueMarket rows in a single query (ticker.in_(...)) instead of one query per market.
    # Use a page of markets larger than the smallest possible page so a per-market query
    # strategy is caught (5 classifiable markets here vs. the 2-item phase-0 fixture).
    seed_teams_from_espn(db_session, "nfl", NFL)
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()

    page = list(KM) + [
        {"ticker": f"KXNFLGAME-26SEP21XXX{i}-X{i}", "event_ticker": f"KXNFLGAME-26SEP21XXX{i}",
         "yes_sub_title": "Nobody", "close_time": "2026-09-24T00:15:00Z"}
        for i in range(3)
    ]

    counts = {"n": 0}

    def _count_venue_markets_query(conn, cursor, statement, parameters, context, executemany):
        if "FROM venue_markets" in statement:
            counts["n"] += 1

    event.listen(db_session.get_bind(), "before_cursor_execute", _count_venue_markets_query)
    try:
        res = upsert_venue_markets(db_session, "nfl", page, EVENTS, raw_id=1, fetched_at=NOW)
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", _count_venue_markets_query)

    assert counts["n"] <= 2, counts["n"]
    # unchanged matching/results semantics for this page (2 from KM matched/unmatched as before,
    # plus 3 new unmatched markets with no recorded event title)
    assert res.new == 5 and res.matched == 1 and res.unmatched == 4


TRADE_BASE = {"ticker": "KXNFLGAME-26SEP21NYGLAR-NYG", "created_time": "2026-09-09T22:58:00Z",
              "count_fp": "5.00", "yes_price_dollars": "0.2300", "is_block_trade": False}


def _print(trade_id: str, **sides) -> dict:
    return {**TRADE_BASE, "trade_id": trade_id, **sides}


def test_insert_trades_prefers_taker_outcome_side_and_drops_a_sideless_print(db_session, caplog):
    # F5: Kalshi's `taker_side` is deprecated in favour of `taker_outcome_side`/`taker_book_side`.
    # The canonical side is `taker_outcome_side or taker_side`; a print carrying neither is dropped
    # and counted, never defaulted to "yes" (which would poison the phase 3 fill model).
    body = {"cursor": "", "trades": [
        _print("new-only", taker_outcome_side="no", taker_book_side="ask"),
        _print("legacy-only", taker_side="yes"),
        _print("no-side-at-all"),
    ]}
    ctx: dict = {}
    with caplog.at_level("WARNING", logger="harness.normalize.kalshi"):
        assert insert_trades(db_session, body, raw_id=7, ctx=ctx) == 2
    rows = {t.trade_id: t for t in db_session.query(VenueTrade).all()}
    assert set(rows) == {"new-only", "legacy-only"}
    assert (rows["new-only"].taker_side, rows["new-only"].taker_outcome_side, rows["new-only"].taker_book_side) == ("no", "no", "ask")
    assert (rows["legacy-only"].taker_side, rows["legacy-only"].taker_outcome_side, rows["legacy-only"].taker_book_side) == ("yes", None, None)
    assert ctx["taker_side_missing"] == 1
    # one WARNING for the raw response, not one per dropped print
    dropped = [r for r in caplog.records if "taker side missing" in r.getMessage()]
    assert len(dropped) == 1 and "raw_id=7" in dropped[0].getMessage()


def test_insert_trades_lowercases_sides_and_treats_junk_as_absent(db_session):
    # Values are normalised to lower case; anything outside a field's documented domain counts as
    # absent, so a print whose only side is unrecognised is dropped rather than stored verbatim in
    # a varchar(4). The two domains differ: taker_outcome_side is yes|no, taker_book_side is
    # bid|ask ("bid means buy YES, ask means sell YES"), so one filter cannot serve both.
    body = {"cursor": "", "trades": [
        _print("upper", taker_outcome_side="NO", taker_book_side="ASK"),
        _print("junk", taker_outcome_side="maybe", taker_side="unknown"),
        _print("junk-outcome-legacy-ok", taker_outcome_side="", taker_side="No"),
        _print("book-out-of-domain", taker_outcome_side="yes", taker_book_side="yes"),
    ]}
    ctx: dict = {}
    assert insert_trades(db_session, body, raw_id=8, ctx=ctx) == 3
    rows = {t.trade_id: t for t in db_session.query(VenueTrade).all()}
    assert (rows["upper"].taker_side, rows["upper"].taker_outcome_side, rows["upper"].taker_book_side) == ("no", "no", "ask")
    assert rows["junk-outcome-legacy-ok"].taker_side == "no" and rows["junk-outcome-legacy-ok"].taker_outcome_side is None
    # a yes/no book side is not a book side: it is recorded as absent, the outcome side still stands
    b = rows["book-out-of-domain"]
    assert (b.taker_side, b.taker_outcome_side, b.taker_book_side) == ("yes", "yes", None)
    assert "junk" not in rows and ctx["taker_side_missing"] == 1


def test_insert_trades_without_ctx_still_drops_the_sideless_print(db_session):
    # ctx is optional: reprocess() and ad-hoc callers pass none and must not crash.
    assert insert_trades(db_session, {"trades": [_print("no-side")]}, raw_id=9) == 0
    assert db_session.query(VenueTrade).count() == 0


def test_insert_trades_accumulates_kalshi_trades_normalized_in_ctx(db_session):
    """Fix 17 round 1 (roadmap row 17, Important): the dashboard's `no_taker_side_share_24h`
    denominator moved from a live `venue_trades` count to `runs.notes`' `kalshi_trades_normalized`
    -- the same count of REST trade rows this function newly inserts, summed in `ctx` across
    every `/markets/trades` page one run processes (mirroring how `taker_side_missing` already
    accumulates), so `harness.recorder.tick.Recorder` can write the running total into notes."""
    ctx: dict = {}
    body_1 = {"cursor": "", "trades": [_print("t-1", taker_outcome_side="no", taker_book_side="ask"),
                                       _print("t-2", taker_outcome_side="yes", taker_book_side="bid"),
                                       _print("t-no-side")]}
    assert insert_trades(db_session, body_1, raw_id=10, ctx=ctx) == 2
    assert ctx["kalshi_trades_normalized"] == 2
    assert ctx["taker_side_missing"] == 1

    # A second page in the same run adds to the running total rather than resetting it.
    body_2 = {"cursor": "", "trades": [_print("t-3", taker_outcome_side="no", taker_book_side="ask")]}
    assert insert_trades(db_session, body_2, raw_id=11, ctx=ctx) == 1
    assert ctx["kalshi_trades_normalized"] == 3
    assert ctx["taker_side_missing"] == 1

    # A page that inserts nothing new (already-recorded trade_ids) still adds zero, not nothing.
    assert insert_trades(db_session, body_2, raw_id=11, ctx=ctx) == 0
    assert ctx["kalshi_trades_normalized"] == 3
