import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import FairValue, Game, OddsSnapshot, Run, VenueMarket
from harness.matching.teams import seed_teams_from_espn
from harness.pricing.fair import compute_fair_values

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
ROWS = json.loads((FIXD / "odds_lines_game.json").read_text())

HOME, AWAY = 14, 19  # Rams, Giants
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


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

    def vm(i, market_type, threshold=None, side_team_id=None, side=None):
        return VenueMarket(
            venue="kalshi",
            ticker=f"KXNFL-{i}",
            event_ticker="KXNFL-EVT",
            series_ticker="KXNFL",
            game_id=game.id,
            market_type=market_type,
            threshold=Decimal(str(threshold)) if threshold is not None else None,
            side_team_id=side_team_id,
            side=side,
            match_confidence=Decimal("1.00"),
            match_status="matched",
            first_seen_raw_id=1,
            last_seen_at=NOW,
        )

    db_session.add_all([
        vm(1, "moneyline", side_team_id=HOME),
        vm(2, "moneyline", side_team_id=AWAY),
        vm(3, "spread", threshold="3.5", side_team_id=HOME),
        vm(4, "spread", threshold="6.5", side_team_id=HOME),
        vm(5, "spread", threshold="9.5", side_team_id=HOME),
        vm(6, "total", threshold="44.5", side="over"),
        vm(7, "total", threshold="47.5", side="over"),
    ])
    db_session.flush()
    db_session.commit()
    return game, run


def test_compute_fair_values_direct_and_derived(db_session):
    game, run = _seed(db_session)

    counts = compute_fair_values(db_session, run.id, NOW)

    assert counts.games == 1
    assert counts.direct == 5
    assert counts.derived == 2
    assert counts.no_sharp == 0

    rows = db_session.query(FairValue).filter_by(run_id=run.id, game_id=game.id).all()
    by_shape = {}
    for row in rows:
        key = (row.market_type, row.outcome_team_id, row.outcome_side, row.threshold)
        by_shape[key] = row

    ml_home = by_shape[("moneyline", HOME, None, None)]
    ml_away = by_shape[("moneyline", AWAY, None, None)]
    spread_35 = by_shape[("spread", HOME, None, Decimal("3.5"))]
    spread_65 = by_shape[("spread", HOME, None, Decimal("6.5"))]
    spread_95 = by_shape[("spread", HOME, None, Decimal("9.5"))]
    total_445 = by_shape[("total", None, "over", Decimal("44.5"))]
    total_475 = by_shape[("total", None, "over", Decimal("47.5"))]

    for row in (ml_home, ml_away, spread_35, spread_65, total_445):
        assert row.fair_source == "direct"
        assert row.staleness_s is not None
        assert 170 <= row.staleness_s <= 190

    for row in (spread_95, total_475):
        assert row.fair_source == "derived"
        assert row.model_json is not None
        assert abs(row.model_json["mu"] - 3.5) <= 0.5

    # idempotent: second call at the same run inserts nothing new (candidate game count is unaffected)
    counts2 = compute_fair_values(db_session, run.id, NOW)
    assert counts2.direct == 0
    assert counts2.derived == 0
    assert counts2.no_sharp == 0
