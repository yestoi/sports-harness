import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

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


def test_build_gap_snapshots(db_session):
    game, run, markets = _seed(db_session)
    counts = compute_fair_values(db_session, run.id, NOW)
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

    # idempotent: second call for the same run inserts nothing new.
    n2 = build_gap_snapshots(db_session, run.id, NOW, TZ, fee_model=KALSHI_FOOTBALL)
    assert n2 == 0
    assert db_session.query(MarketGapSnapshot).filter_by(run_id=run.id).count() == 8


def test_prev_fair_from_earlier_run(db_session):
    game, run1, markets = _seed(db_session)
    compute_fair_values(db_session, run1.id, NOW)
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

    compute_fair_values(db_session, run2.id, now2)
    _add_quotes(db_session, run2.id, markets, raw_id_start=2000, fetched_at=now2)
    build_gap_snapshots(db_session, run2.id, now2, TZ)

    row2 = db_session.query(MarketGapSnapshot).filter_by(run_id=run2.id, venue_market_id=ml_home_market.id).one()
    assert row2.prev_fair_p == fair1
    assert row2.prev_fair_ts is not None
