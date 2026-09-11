import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from harness.db.models import FairValue, Game, MarketGapSnapshot, OddsSnapshot, Run, VenueMarket, VenueQuote
from harness.matching.teams import seed_teams_from_espn
from harness.pricing.fair import compute_fair_values
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.pricing.gaps import build_gap_snapshots, load_popularity

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
ROWS = json.loads((FIXD / "odds_lines_game.json").read_text())

HOME, AWAY = 14, 19  # Rams, Giants
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
TZ = "America/Chicago"


def _vm(game_id, i, market_type, threshold=None, side_team_id=None, side=None, match_status="matched"):
    return VenueMarket(
        venue="kalshi",
        ticker=f"KXNFL-G-{i}",
        event_ticker="KXNFL-EVT-G",
        series_ticker="KXNFL",
        game_id=game_id,
        market_type=market_type,
        threshold=Decimal(str(threshold)) if threshold is not None else None,
        side_team_id=side_team_id,
        side=side,
        match_confidence=Decimal("1.00") if match_status != "unmatched" else Decimal("0.00"),
        match_status=match_status,
        first_seen_raw_id=1,
        last_seen_at=NOW,
    )


def _seed(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
    db_session.add(game)
    db_session.flush()

    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()

    fetched_at = NOW - timedelta(minutes=2)
    book_last_update = NOW - timedelta(minutes=3)
    for i, r in enumerate(ROWS):
        db_session.add(OddsSnapshot(
            raw_id=i + 1,
            run_id=run.id,
            book=r["book"],
            game_id=game.id,
            market_type=r["market_type"],
            outcome_team_id=r["outcome_team_id"],
            outcome_side=r["outcome_side"],
            point=Decimal(str(r["point"])) if r["point"] is not None else None,
            price_decimal=Decimal(str(r["price_decimal"])),
            book_last_update=book_last_update,
            fetched_at=fetched_at,
        ))

    markets = [
        _vm(game.id, 1, "moneyline", side_team_id=HOME),
        _vm(game.id, 2, "moneyline", side_team_id=AWAY),
        _vm(game.id, 3, "spread", threshold="3.5", side_team_id=HOME),
        _vm(game.id, 4, "spread", threshold="6.5", side_team_id=HOME),
        _vm(game.id, 5, "spread", threshold="9.5", side_team_id=HOME),
        _vm(game.id, 6, "total", threshold="44.5", side="over"),
        _vm(game.id, 7, "total", threshold="47.5", side="over"),
        # matched but a shape compute_fair_values never produces a FairValue for -> "no_sharp"
        # population for gap snapshots (fair_source stays None).
        _vm(game.id, 8, "draw"),
        # matched_status excludes this one from gap snapshots even though it has a quote.
        _vm(game.id, 9, "moneyline", side_team_id=HOME, match_status="unmatched"),
    ]
    db_session.add_all(markets)
    db_session.flush()
    db_session.commit()
    return game, run, markets


def _add_quotes(db_session, run_id, markets, raw_id_start, fetched_at):
    for i, m in enumerate(markets):
        db_session.add(VenueQuote(
            raw_id=raw_id_start + i,
            run_id=run_id,
            venue_market_id=m.id,
            yes_bid=Decimal("0.50"),
            yes_ask=Decimal("0.52"),
            no_bid=Decimal("0.48"),
            no_ask=Decimal("0.50"),
            yes_bid_size=Decimal("100"),
            yes_ask_size=Decimal("120"),
            volume=Decimal("50"),
            volume_24h=Decimal("500"),
            open_interest=Decimal("100"),
            fetched_at=fetched_at,
        ))
    db_session.flush()
    db_session.commit()


def test_build_gap_snapshots(db_session, env_settings):
    game, run, markets = _seed(db_session)
    counts = compute_fair_values(db_session, run.id, NOW, env_settings)
    assert counts.direct == 5
    assert counts.derived == 2

    _add_quotes(db_session, run.id, markets, raw_id_start=1000, fetched_at=NOW)

    n = build_gap_snapshots(db_session, run.id, NOW, TZ, fee_model=KALSHI_FOOTBALL)
    # 9 markets seeded, minus the 1 unmatched -> 8 matched markets with quotes get a snapshot.
    assert n == 8

    rows = db_session.query(MarketGapSnapshot).filter_by(run_id=run.id).all()
    assert len(rows) == 8

    by_market = {r.venue_market_id: r for r in rows}
    market_by_ticker = {m.ticker: m for m in markets}
    unmatched_market = market_by_ticker["KXNFL-G-9"]
    assert unmatched_market.id not in by_market

    ml_home_market = market_by_ticker["KXNFL-G-1"]
    ml_home_row = by_market[ml_home_market.id]
    assert ml_home_row.fair_source == "direct"
    assert ml_home_row.fair_p is not None
    assert ml_home_row.soft_minus_sharp is not None

    fair = ml_home_row.fair_p
    expected_maker_fee = fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 100)
    expected_taker_fee = fee_per_contract(KALSHI_FOOTBALL, "taker", Decimal("0.52"), 100)
    assert ml_home_row.gap_maker_net == fair - Decimal("0.50") - expected_maker_fee
    assert ml_home_row.gap_taker_net == fair - Decimal("0.52") - expected_taker_fee
    assert ml_home_row.venue_mid == Decimal("0.51")
    assert ml_home_row.gap_mid == fair - Decimal("0.51")
    assert ml_home_row.price_bucket == 50

    popularity = load_popularity()
    assert popularity["nfl"][HOME] == 2
    assert ml_home_row.home_popularity_tier == 2
    assert ml_home_row.away_popularity_tier == popularity["nfl"].get(AWAY, 0)

    now_ct = NOW.astimezone(ZoneInfo(TZ))
    assert ml_home_row.dow == now_ct.weekday()
    assert ml_home_row.hour_ct == now_ct.hour

    ttk = int((game.kickoff_utc - NOW).total_seconds() // 60)
    assert ml_home_row.ttk_minutes == ttk

    no_fair_market = market_by_ticker["KXNFL-G-8"]
    no_fair_row = by_market[no_fair_market.id]
    assert no_fair_row.fair_source is None
    assert no_fair_row.fair_p is None
    assert no_fair_row.gap_mid is None
    assert no_fair_row.gap_taker_net is None
    assert no_fair_row.gap_maker_net is None
    assert no_fair_row.venue_mid == Decimal("0.51")  # quote data is still recorded
    # market_type "draw" isn't moneyline/spread/total, so no shape (and no reason to look
    # for a fair value at all) exists for it.
    assert no_fair_row.no_fair_reason == "unmapped_market_type"

    for row in rows:
        if row is not no_fair_row:
            assert row.no_fair_reason is None

    # idempotent: second call for the same run inserts nothing new.
    n2 = build_gap_snapshots(db_session, run.id, NOW, TZ, fee_model=KALSHI_FOOTBALL)
    assert n2 == 0
    assert db_session.query(MarketGapSnapshot).filter_by(run_id=run.id).count() == 8


def test_prev_fair_from_earlier_run(db_session, env_settings):
    game, run1, markets = _seed(db_session)
    compute_fair_values(db_session, run1.id, NOW, env_settings)
    _add_quotes(db_session, run1.id, markets, raw_id_start=1000, fetched_at=NOW)
    build_gap_snapshots(db_session, run1.id, NOW, TZ)

    ml_home_market = next(m for m in markets if m.ticker == "KXNFL-G-1")
    row1 = db_session.query(MarketGapSnapshot).filter_by(run_id=run1.id, venue_market_id=ml_home_market.id).one()
    fair1 = row1.fair_p
    assert fair1 is not None

    now2 = NOW + timedelta(minutes=5)
    run2 = Run(started_at=now2, status="running")
    db_session.add(run2)
    db_session.flush()
    db_session.commit()

    compute_fair_values(db_session, run2.id, now2, env_settings)
    _add_quotes(db_session, run2.id, markets, raw_id_start=2000, fetched_at=now2)
    build_gap_snapshots(db_session, run2.id, now2, TZ)

    row2 = db_session.query(MarketGapSnapshot).filter_by(run_id=run2.id, venue_market_id=ml_home_market.id).one()
    assert row2.prev_fair_p == fair1
    assert row2.prev_fair_ts is not None


# --- final fix wave: no_fair_reason -----------------------------------------

def test_no_fair_reason_is_no_sharp_line_when_there_is_no_pinnacle_backed_line(db_session, env_settings):
    """A matched, recognized shape (moneyline here) with quotes from a book that isn't in the
    sharp group, and no spreads data to build a margin model from, has nowhere to derive a
    fair value from at all -- not because its market type is unmapped."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
    db_session.add(game)
    db_session.flush()
    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()

    fetched_at = NOW - timedelta(minutes=2)
    book_last_update = NOW - timedelta(minutes=3)
    # draftkings is not in SHARP_BOOKS, and no pinnacle/betonlineag/lowvig data exists at all,
    # for this or any other market -- so direct_fair can never find a Pinnacle-backed pair,
    # and _build_model has no spreads data to derive one from either.
    db_session.add_all([
        OddsSnapshot(raw_id=1, run_id=run.id, book="draftkings", game_id=game.id, market_type="h2h",
                     outcome_team_id=HOME, outcome_side=None, point=None,
                     price_decimal=Decimal("1.50"), book_last_update=book_last_update, fetched_at=fetched_at),
        OddsSnapshot(raw_id=2, run_id=run.id, book="draftkings", game_id=game.id, market_type="h2h",
                     outcome_team_id=AWAY, outcome_side=None, point=None,
                     price_decimal=Decimal("2.80"), book_last_update=book_last_update, fetched_at=fetched_at),
    ])
    market = _vm(game.id, 900, "moneyline", side_team_id=HOME)
    db_session.add(market)
    db_session.flush()
    db_session.commit()

    counts = compute_fair_values(db_session, run.id, NOW, env_settings)
    assert counts.direct == 0
    assert counts.derived == 0
    assert counts.no_sharp == 1
    assert counts.errored_game_ids == frozenset()

    _add_quotes(db_session, run.id, [market], raw_id_start=9000, fetched_at=NOW)
    build_gap_snapshots(db_session, run.id, NOW, TZ, errored_game_ids=counts.errored_game_ids)

    row = db_session.query(MarketGapSnapshot).filter_by(run_id=run.id, venue_market_id=market.id).one()
    assert row.fair_p is None
    assert row.no_fair_reason == "no_sharp_line"


def test_no_fair_reason_is_pricing_error_when_the_game_raised(db_session, monkeypatch, env_settings):
    """A game whose fair-value computation raised and was rolled back has no FairValue rows
    at all for this run; its gap rows must say so was a pricing error, not a missing line."""
    from harness.pricing.margin_model import MarginModel

    game, run, markets = _seed(db_session)

    def flaky(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(MarginModel, "from_main_lines", classmethod(flaky))

    counts = compute_fair_values(db_session, run.id, NOW, env_settings)
    assert counts.direct == 0
    assert counts.derived == 0
    assert counts.errors == 1
    assert counts.errored_game_ids == frozenset({game.id})

    _add_quotes(db_session, run.id, markets, raw_id_start=9100, fetched_at=NOW)
    build_gap_snapshots(db_session, run.id, NOW, TZ, errored_game_ids=counts.errored_game_ids)

    rows = db_session.query(MarketGapSnapshot).filter_by(run_id=run.id).all()
    ml_home_market = next(m for m in markets if m.ticker == "KXNFL-G-1")
    ml_home_row = next(r for r in rows if r.venue_market_id == ml_home_market.id)
    assert ml_home_row.fair_p is None
    assert ml_home_row.no_fair_reason == "pricing_error"

    # the unmapped-market-type row keeps its own reason even when the whole game errored.
    draw_market = next(m for m in markets if m.ticker == "KXNFL-G-8")
    draw_row = next(r for r in rows if r.venue_market_id == draw_market.id)
    assert draw_row.no_fair_reason == "unmapped_market_type"


def test_gap_copies_feed_columns(db_session, env_settings):
    """F11: `feed_kind`, `feed_lag_s`, and `stale_allowance_s` are copied from the fair value
    a gap snapshot is keyed to, and stay NULL when there is no fair value to copy from."""
    game, run, markets = _seed(db_session)
    compute_fair_values(db_session, run.id, NOW, env_settings)
    _add_quotes(db_session, run.id, markets, raw_id_start=1000, fetched_at=NOW)
    build_gap_snapshots(db_session, run.id, NOW, TZ, fee_model=KALSHI_FOOTBALL)

    ml_home_market = next(m for m in markets if m.ticker == "KXNFL-G-1")
    row = db_session.query(MarketGapSnapshot).filter_by(run_id=run.id, venue_market_id=ml_home_market.id).one()
    fair = db_session.query(FairValue).filter_by(
        run_id=run.id, game_id=game.id, market_type="moneyline", outcome_team_id=HOME, fair_source="direct",
    ).one()
    assert fair.feed_kind is not None
    assert row.feed_kind == fair.feed_kind
    assert row.feed_lag_s == fair.feed_lag_s
    assert row.stale_allowance_s == fair.stale_allowance_s

    no_fair_market = next(m for m in markets if m.ticker == "KXNFL-G-8")
    no_fair_row = db_session.query(MarketGapSnapshot).filter_by(
        run_id=run.id, venue_market_id=no_fair_market.id
    ).one()
    assert no_fair_row.feed_kind is None
    assert no_fair_row.feed_lag_s is None
    assert no_fair_row.stale_allowance_s is None


# --- fix 42: the pricing read must ride an index on run_id -----------------------------------
#
# 2026-09-11 13:03 CT: every pricing run on the NAS failed. `venue_quotes` (3.58 M rows, 773 MB)
# carried no index leading with `run_id`, so `build_gap_snapshots`' select became a nested loop
# that walked `ix_quotes_market_fetched` once per matched market, `run_id` a filter rather than a
# scan key -- every quote ever recorded for the market read to keep the ~3 of this run. Cost
# 52,000, over the 30 s statement timeout on every run, so `runs.status = degraded`, no fair
# values, no signals. `ix_quotes_run_market (run_id, venue_market_id)` is the scan key.

def _index_names(node) -> set[str]:
    """Every `Index Name` anywhere in an `explain (format json)` plan tree."""
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


def _capture_quote_select(db_session, run_id):
    """The exact statement `build_gap_snapshots` issues against `venue_quotes`, with its bound
    parameters, captured off the connection rather than rebuilt here -- a copy of the select in
    this file could drift from the one that actually runs, which is the whole failure mode."""
    from sqlalchemy import event

    captured = []

    def before(conn, cursor, statement, parameters, context, executemany):
        if "venue_quotes" in statement and "market_gap_snapshots" not in statement:
            captured.append((statement, parameters))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", before)
    try:
        build_gap_snapshots(db_session, run_id, NOW, TZ, fee_model=KALSHI_FOOTBALL)
    finally:
        event.remove(bind, "before_cursor_execute", before)
    assert captured, "build_gap_snapshots issued no statement over venue_quotes"
    return captured[0]


def test_ix_quotes_run_market_is_chosen_for_the_gap_select(db_session, env_settings):
    """The plan, not just the index's presence: without a scan key on `run_id` the planner has
    `ix_quotes_market_fetched` to walk per market, which is exactly what timed out on the NAS.

    Seeded the shape that makes the difference visible -- one run's quotes among many runs' over
    the same markets, so `run_id` is the selective predicate -- and `enable_seqscan` off, so a
    sequential scan cannot stand in for the index winning on its own merits (fix 35's
    `test_ix_fair_leg_lookup_is_chosen_for_the_leg_query` is the precedent for both)."""
    _, run, markets = _seed(db_session)
    compute_fair_values(db_session, run.id, NOW, env_settings)

    # 60 earlier runs' quotes over the same markets: `run_id` is what narrows 549 rows to 9.
    raw_id = 50_000
    for older in range(60):
        for m in markets:
            db_session.add(VenueQuote(
                raw_id=raw_id, run_id=800_000 + older, venue_market_id=m.id,
                yes_bid=Decimal("0.50"), yes_ask=Decimal("0.52"),
                fetched_at=NOW - timedelta(minutes=older + 5)))
            raw_id += 1
    db_session.flush()
    _add_quotes(db_session, run.id, markets, raw_id_start=1000, fetched_at=NOW)
    db_session.execute(text("analyze venue_quotes"))

    # `build_gap_snapshots` commits, so `set local` has to come after the capture, not before.
    statement, parameters = _capture_quote_select(db_session, run.id)
    db_session.execute(text("set local enable_seqscan = off"))
    raw = db_session.connection().connection
    with raw.cursor() as cur:
        cur.execute(f"explain (format json) {statement}", parameters)
        plan = cur.fetchone()[0]
    assert "ix_quotes_run_market" in _index_names(plan), plan
