import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event

from harness.db.models import Game, OrderbookSnapshot, VenueMarket, VenueQuote, VenueTrade
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.kalshi import insert_orderbook, insert_trades, insert_venue_quotes, upsert_venue_markets

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
        _print("new-only", taker_outcome_side="no", taker_book_side="yes"),
        _print("legacy-only", taker_side="yes"),
        _print("no-side-at-all"),
    ]}
    ctx: dict = {}
    with caplog.at_level("WARNING", logger="harness.normalize.kalshi"):
        assert insert_trades(db_session, body, raw_id=7, ctx=ctx) == 2
    rows = {t.trade_id: t for t in db_session.query(VenueTrade).all()}
    assert set(rows) == {"new-only", "legacy-only"}
    assert (rows["new-only"].taker_side, rows["new-only"].taker_outcome_side, rows["new-only"].taker_book_side) == ("no", "no", "yes")
    assert (rows["legacy-only"].taker_side, rows["legacy-only"].taker_outcome_side, rows["legacy-only"].taker_book_side) == ("yes", None, None)
    assert ctx["taker_side_missing"] == 1
    # one WARNING for the raw response, not one per dropped print
    dropped = [r for r in caplog.records if "taker side missing" in r.getMessage()]
    assert len(dropped) == 1 and "raw_id=7" in dropped[0].getMessage()


def test_insert_trades_lowercases_sides_and_treats_junk_as_absent(db_session):
    # Values are normalised to lower-case yes/no; anything else counts as absent, so a print whose
    # only side is unrecognised is dropped rather than stored verbatim in a varchar(4).
    body = {"cursor": "", "trades": [
        _print("upper", taker_outcome_side="NO", taker_book_side="YES"),
        _print("junk", taker_outcome_side="maybe", taker_side="unknown"),
        _print("junk-outcome-legacy-ok", taker_outcome_side="", taker_side="No"),
    ]}
    ctx: dict = {}
    assert insert_trades(db_session, body, raw_id=8, ctx=ctx) == 2
    rows = {t.trade_id: t for t in db_session.query(VenueTrade).all()}
    assert (rows["upper"].taker_side, rows["upper"].taker_outcome_side, rows["upper"].taker_book_side) == ("no", "no", "yes")
    assert rows["junk-outcome-legacy-ok"].taker_side == "no" and rows["junk-outcome-legacy-ok"].taker_outcome_side is None
    assert "junk" not in rows and ctx["taker_side_missing"] == 1


def test_insert_trades_without_ctx_still_drops_the_sideless_print(db_session):
    # ctx is optional: reprocess() and ad-hoc callers pass none and must not crash.
    assert insert_trades(db_session, {"trades": [_print("no-side")]}, raw_id=9) == 0
    assert db_session.query(VenueTrade).count() == 0
