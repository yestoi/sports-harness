import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import FairValue, Game, OddsSnapshot, Run, VenueMarket
from harness.matching.teams import seed_teams_from_espn
from harness.pricing import PRICING_VERSION
from harness.pricing.fair import compute_fair_values, stale_allowance_s
from harness.pricing.margin_model import MarginModel

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


def test_compute_fair_values_direct_and_derived(db_session, env_settings):
    game, run = _seed(db_session)

    counts = compute_fair_values(db_session, run.id, NOW, env_settings)

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
    counts2 = compute_fair_values(db_session, run.id, NOW, env_settings)
    assert counts2.direct == 0
    assert counts2.derived == 0
    assert counts2.no_sharp == 0


def test_main_spread_never_anchors_on_alternate(db_session, env_settings):
    """A Pinnacle leg that only exists via alternate_spreads must not become the main-line anchor,
    even when its point value happens to be the smallest |point| among real `spreads` rows from
    other (non-sharp) books."""
    game, run = _seed(db_session)
    fetched_at = NOW - timedelta(minutes=2)
    book_last_update = NOW - timedelta(minutes=3)
    db_session.add_all([
        # A real `spreads` row at 1.5 from a non-sharp book puts 1.5 into the candidate point list.
        OddsSnapshot(raw_id=9101, run_id=run.id, book="draftkings", game_id=game.id, market_type="spreads",
                     outcome_team_id=HOME, outcome_side=None, point=Decimal("-1.5"),
                     price_decimal=Decimal("1.90"), book_last_update=book_last_update, fetched_at=fetched_at),
        # Pinnacle only offers 1.5 via its alternate ladder, not the real spreads market.
        OddsSnapshot(raw_id=9102, run_id=run.id, book="pinnacle", game_id=game.id, market_type="alternate_spreads",
                     outcome_team_id=HOME, outcome_side=None, point=Decimal("-1.5"),
                     price_decimal=Decimal("1.50"), book_last_update=book_last_update, fetched_at=fetched_at),
        OddsSnapshot(raw_id=9103, run_id=run.id, book="pinnacle", game_id=game.id, market_type="alternate_spreads",
                     outcome_team_id=AWAY, outcome_side=None, point=Decimal("1.5"),
                     price_decimal=Decimal("2.60"), book_last_update=book_last_update, fetched_at=fetched_at),
    ])
    db_session.flush()
    db_session.commit()

    compute_fair_values(db_session, run.id, NOW, env_settings)

    row = db_session.query(FairValue).filter_by(
        run_id=run.id, game_id=game.id, market_type="spread", outcome_team_id=HOME, threshold=Decimal("9.5"),
    ).one()
    assert row.fair_source == "derived"
    assert row.model_json["source"]["home_point"] == "-3.5"


def _seed_totals_median_game(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
    db_session.add(game)
    db_session.flush()
    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()

    fetched_at = NOW - timedelta(minutes=2)
    book_last_update = NOW - timedelta(minutes=3)
    db_session.add_all([
        OddsSnapshot(raw_id=1, run_id=run.id, book="pinnacle", game_id=game.id, market_type="spreads",
                     outcome_team_id=HOME, outcome_side=None, point=Decimal("-3.5"),
                     price_decimal=Decimal("1.9425"), book_last_update=book_last_update, fetched_at=fetched_at),
        OddsSnapshot(raw_id=2, run_id=run.id, book="pinnacle", game_id=game.id, market_type="spreads",
                     outcome_team_id=AWAY, outcome_side=None, point=Decimal("3.5"),
                     price_decimal=Decimal("1.904"), book_last_update=book_last_update, fetched_at=fetched_at),
        # 44.5: Pinnacle absent (only a soft book quotes it).
        OddsSnapshot(raw_id=3, run_id=run.id, book="betonlineag", game_id=game.id, market_type="totals",
                     outcome_team_id=None, outcome_side="over", point=Decimal("44.5"),
                     price_decimal=Decimal("1.90"), book_last_update=book_last_update, fetched_at=fetched_at),
        OddsSnapshot(raw_id=4, run_id=run.id, book="betonlineag", game_id=game.id, market_type="totals",
                     outcome_team_id=None, outcome_side="under", point=Decimal("44.5"),
                     price_decimal=Decimal("1.90"), book_last_update=book_last_update, fetched_at=fetched_at),
        # 45.5: Pinnacle present.
        OddsSnapshot(raw_id=5, run_id=run.id, book="pinnacle", game_id=game.id, market_type="totals",
                     outcome_team_id=None, outcome_side="over", point=Decimal("45.5"),
                     price_decimal=Decimal("1.9231"), book_last_update=book_last_update, fetched_at=fetched_at),
        OddsSnapshot(raw_id=6, run_id=run.id, book="pinnacle", game_id=game.id, market_type="totals",
                     outcome_team_id=None, outcome_side="under", point=Decimal("45.5"),
                     price_decimal=Decimal("1.9231"), book_last_update=book_last_update, fetched_at=fetched_at),
    ])

    def vm(i, market_type, threshold=None, side_team_id=None, side=None):
        return VenueMarket(
            venue="kalshi", ticker=f"KXNFL-M-{i}", event_ticker="KXNFL-EVT-M", series_ticker="KXNFL",
            game_id=game.id, market_type=market_type,
            threshold=Decimal(str(threshold)) if threshold is not None else None,
            side_team_id=side_team_id, side=side, match_confidence=Decimal("1.00"), match_status="matched",
            first_seen_raw_id=1, last_seen_at=NOW,
        )

    db_session.add_all([
        vm(1, "spread", threshold="3.5", side_team_id=HOME),
        vm(2, "total", threshold="50.5", side="over"),  # no book data at 50.5 -> forces derived
    ])
    db_session.flush()
    db_session.commit()
    return game, run


def test_main_total_searches_outward_from_median(db_session, env_settings):
    """When the exact median totals line lacks a Pinnacle leg, the search must keep looking
    outward instead of giving up (so the game still gets a total model)."""
    game, run = _seed_totals_median_game(db_session)

    compute_fair_values(db_session, run.id, NOW, env_settings)

    row = db_session.query(FairValue).filter_by(
        run_id=run.id, game_id=game.id, market_type="total", outcome_side="over", threshold=Decimal("50.5"),
    ).one()
    assert row.fair_source == "derived"
    assert row.model_json["source"]["total_line"] == "45.5"


def test_per_game_isolation_continues_after_one_game_errors(db_session, monkeypatch, env_settings):
    """One game raising during fair-value computation must not prevent other games' rows from
    being inserted, and must be counted in FairCounts.errors rather than aborting the whole run."""
    game_a, run = _seed(db_session)

    game_b = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=3))
    db_session.add(game_b)
    db_session.flush()
    assert game_b.id > game_a.id  # processing order relies on ascending game id

    fetched_at = NOW - timedelta(minutes=2)
    book_last_update = NOW - timedelta(minutes=3)
    for i, r in enumerate(ROWS):
        db_session.add(OddsSnapshot(
            raw_id=2000 + i, run_id=run.id, book=r["book"], game_id=game_b.id, market_type=r["market_type"],
            outcome_team_id=r["outcome_team_id"], outcome_side=r["outcome_side"],
            point=Decimal(str(r["point"])) if r["point"] is not None else None,
            price_decimal=Decimal(str(r["price_decimal"])),
            book_last_update=book_last_update, fetched_at=fetched_at,
        ))

    def vm_b(i, market_type, threshold=None, side_team_id=None, side=None):
        return VenueMarket(
            venue="kalshi", ticker=f"KXNFL-B-{i}", event_ticker="KXNFL-EVT-B", series_ticker="KXNFL",
            game_id=game_b.id, market_type=market_type,
            threshold=Decimal(str(threshold)) if threshold is not None else None,
            side_team_id=side_team_id, side=side, match_confidence=Decimal("1.00"), match_status="matched",
            first_seen_raw_id=1, last_seen_at=NOW,
        )

    db_session.add_all([
        vm_b(1, "moneyline", side_team_id=HOME),
        vm_b(2, "moneyline", side_team_id=AWAY),
        vm_b(3, "spread", threshold="3.5", side_team_id=HOME),
        vm_b(5, "spread", threshold="9.5", side_team_id=HOME),
    ])
    db_session.flush()
    db_session.commit()

    original = MarginModel.from_main_lines.__func__
    calls = {"n": 0}

    def flaky(cls, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(MarginModel, "from_main_lines", classmethod(flaky))

    counts = compute_fair_values(db_session, run.id, NOW, env_settings)

    assert counts.errors == 1
    assert calls["n"] == 2

    rows_a = db_session.query(FairValue).filter_by(run_id=run.id, game_id=game_a.id).all()
    rows_b = db_session.query(FairValue).filter_by(run_id=run.id, game_id=game_b.id).all()
    # game_a errored (first call to from_main_lines) and was rolled back entirely.
    assert rows_a == []
    # game_b succeeded and its rows (including the direct ones) were inserted.
    assert len(rows_b) > 0


# --- F11: staleness amendment ------------------------------------------------

def test_stale_allowance_table(env_settings, monkeypatch):
    """spec F11: featured is a flat 120s cadence plus the tick budget; alternate uses the
    near/far Odds API cadence for the given time-to-kickoff, also plus the tick budget."""
    assert env_settings.tick_budget_s == 100
    assert stale_allowance_s("featured", None, env_settings) == 220
    assert stale_allowance_s("alternate", 120, env_settings) == 220
    # U1: odds_alt_interval_far_s defaults to 120 too, so the far branch still gives 220.
    assert stale_allowance_s("alternate", 600, env_settings) == 220

    monkeypatch.setattr(env_settings, "odds_alt_interval_far_s", 900)
    assert stale_allowance_s("alternate", 600, env_settings) == 1000


def test_direct_fair_records_feed_kind_and_lag(db_session, env_settings):
    """One rung's fair value comes from a featured-market line fetched just now; a neighboring
    rung's comes from an alternate-market line fetched 800s ago. Each fair value must record
    which feed it came from, how stale that feed's fetch is, and the allowance that implies."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
    db_session.add(game)
    db_session.flush()
    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()

    featured_ts = NOW
    alt_ts = NOW - timedelta(seconds=800)
    db_session.add_all([
        OddsSnapshot(raw_id=1, run_id=run.id, book="pinnacle", game_id=game.id, market_type="spreads",
                     outcome_team_id=HOME, outcome_side=None, point=Decimal("-3.5"),
                     price_decimal=Decimal("1.90"), book_last_update=featured_ts, fetched_at=featured_ts),
        OddsSnapshot(raw_id=2, run_id=run.id, book="pinnacle", game_id=game.id, market_type="spreads",
                     outcome_team_id=AWAY, outcome_side=None, point=Decimal("3.5"),
                     price_decimal=Decimal("1.95"), book_last_update=featured_ts, fetched_at=featured_ts),
        OddsSnapshot(raw_id=3, run_id=run.id, book="pinnacle", game_id=game.id, market_type="alternate_spreads",
                     outcome_team_id=HOME, outcome_side=None, point=Decimal("-4.5"),
                     price_decimal=Decimal("2.10"), book_last_update=alt_ts, fetched_at=alt_ts),
        OddsSnapshot(raw_id=4, run_id=run.id, book="pinnacle", game_id=game.id, market_type="alternate_spreads",
                     outcome_team_id=AWAY, outcome_side=None, point=Decimal("4.5"),
                     price_decimal=Decimal("1.75"), book_last_update=alt_ts, fetched_at=alt_ts),
    ])
    db_session.add_all([
        VenueMarket(venue="kalshi", ticker="KXNFL-F1", event_ticker="KXNFL-EVT-F", series_ticker="KXNFL",
                    game_id=game.id, market_type="spread", threshold=Decimal("3.5"), side_team_id=HOME,
                    match_confidence=Decimal("1.00"), match_status="matched", first_seen_raw_id=1,
                    last_seen_at=NOW),
        VenueMarket(venue="kalshi", ticker="KXNFL-F2", event_ticker="KXNFL-EVT-F", series_ticker="KXNFL",
                    game_id=game.id, market_type="spread", threshold=Decimal("4.5"), side_team_id=HOME,
                    match_confidence=Decimal("1.00"), match_status="matched", first_seen_raw_id=1,
                    last_seen_at=NOW),
    ])
    db_session.flush()
    db_session.commit()

    counts = compute_fair_values(db_session, run.id, NOW, env_settings)
    assert counts.direct == 2  # both rungs matched a book pair on their own point; nothing derived

    rows = {
        r.threshold: r
        for r in db_session.query(FairValue).filter_by(run_id=run.id, game_id=game.id).all()
    }
    row_35, row_45 = rows[Decimal("3.5")], rows[Decimal("4.5")]

    assert row_35.feed_kind == "featured"
    assert 0 <= row_35.feed_lag_s <= 5
    assert row_35.stale_allowance_s == 220

    assert row_45.feed_kind == "alternate"
    assert 795 <= row_45.feed_lag_s <= 805
    assert row_45.stale_allowance_s == 220


def test_fair_value_records_pricing_version(db_session, env_settings):
    game, run = _seed(db_session)
    compute_fair_values(db_session, run.id, NOW, env_settings)

    rows = db_session.query(FairValue).filter_by(run_id=run.id, game_id=game.id).all()
    assert rows
    assert all(row.pricing_version == PRICING_VERSION for row in rows)
