"""The Floor builder: the funnel's sourcing, the game board, the venue tile, and the
never-shown list."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.dashboard.snapshots import floor
from harness.dashboard.snapshots.floor import FLOOR_KEYS, build_floor
from harness.db.models import (EquitySnapshot, FairValue, Game, GameScoreEvent, Intent,
                               Order, OrderEvent, OrderWatchSample, Run, Team, VenueMarket,
                               VenueRequest, VenueStatus, OperatorEvent)

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)


def test_the_funnel_reads_runs_notes_and_never_scans_the_gap_or_signal_tables():
    """Ruling A-C1: the indexes on market_gap_snapshots and signals lead on market and variant,
    not time, so a bare 6 h `created_at` predicate is a sequential scan -- 86-92 s measured, and
    permanently `{"error": ...}` under a 2000 ms timeout. Ticks, gaps and signal counts come
    from `runs.notes`, as the legacy funnel's have since fix 15."""
    body = Path(floor.__file__).read_text().lower()
    assert "from market_gap_snapshots" not in body
    assert "from signals" not in body
    assert "recent_run_notes" in body and "signals_by_variant_from_notes" in body


def test_no_floor_sql_names_a_forbidden_table():
    body = Path(floor.__file__).read_text().lower()
    for table in ("orderbook_events", "venue_trades", "raw_responses", "odds_snapshots",
                  "venue_quotes"):
        assert table not in body


def test_the_payload_carries_only_the_allowed_keys_and_no_quality_figure(db_session,
                                                                        env_settings):
    """Spec §2.2 never-shown: CLV, markouts or anything that judges the strategy."""
    payload = build_floor(db_session, NOW, env_settings)
    assert set(payload) == FLOOR_KEYS
    for banned in ("clv", "markout", "markouts", "gate", "contrasts"):
        assert banned not in payload


def test_the_funnel_sums_the_run_notes_and_reads_the_small_tables_directly(db_session,
                                                                          env_settings):
    db_session.add(Run(started_at=NOW - timedelta(hours=1), status="ok", build_sha="abc",
                       notes={"pricing": {"ticks": 40, "gaps": 900, "fair_direct": 30,
                                          "signals": {"sharp_direct": {"candidate": 12,
                                                                       "rejected": 88}}}}))
    intent = Intent(signal_id=1, variant_id="sharp_direct", venue="kalshi", venue_market_id=1,
                    ticker="KXNFL-T", side="yes", signal_created_at=NOW - timedelta(hours=1),
                    created_at=NOW - timedelta(hours=1), replay=False)
    db_session.add(intent)
    db_session.flush()
    db_session.add(OrderEvent(intent_id=intent.id, ts=NOW - timedelta(hours=1), kind="skipped",
                              reason="kickoff", replay=False))
    db_session.flush()

    funnel = build_floor(db_session, NOW, env_settings)["funnel"]
    assert funnel["gaps"] == 900 and funnel["ticks"] == 40
    assert funnel["candidates"] == 12 and funnel["rejected_total"] == 88
    assert funnel["intents"] == 1
    assert {"reason": "kickoff", "count": 1, "plain": "too close to kickoff"} in \
        funnel["skipped"]


def test_the_game_board_carries_the_live_score_with_its_age_and_sanitized_team_text(
        db_session, env_settings):
    """Ruling A-I6 / B-I2: ESPN team text and scoreboard text are the feed's, not ours."""
    db_session.add(Team(sport="nfl", id=1, display_name="<b>Saints</b>", location="New Orleans",
                        name="Saints", abbreviation="NO", short_display_name="Saints"))
    db_session.add(Team(sport="nfl", id=2, display_name="Falcons", location="Atlanta",
                        name="Falcons", abbreviation="ATL", short_display_name="Falcons"))
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW - timedelta(hours=1), status="in_progress")
    db_session.add(game)
    db_session.flush()
    db_session.add(GameScoreEvent(game_id=game.id, ts=NOW - timedelta(seconds=90),
                                  status="in_progress", period=3, clock="<script>",
                                  home_score=17, away_score=10))
    db_session.add(VenueMarket(venue="kalshi", ticker="KXNFL-T", event_ticker="E",
                               series_ticker="KXNFL", game_id=game.id, market_type="moneyline",
                               match_status="matched", first_seen_raw_id=1, last_seen_at=NOW))
    db_session.flush()

    board = build_floor(db_session, NOW, env_settings)["board"]
    card = board["games"][0]
    assert card["home"] == "bSaints/b" and "<" not in card["home"]
    assert card["clock"] == "script" and card["score_age_s"] == pytest.approx(90, abs=2)
    assert card["home_score"] == 17 and card["matched_markets"] == 1


def test_the_venue_tile_shows_the_prod_tripwire_and_the_last_smoke(db_session, env_settings):
    """Addendum §0.3, roadmap phase 4.5 item 5."""
    db_session.add(VenueRequest(venue="kalshi", env="prod", method="GET",
                                path="/account/limits", status=200,
                                ts=NOW - timedelta(hours=2), elapsed_ms=120))
    db_session.add(VenueRequest(venue="kalshi", env="demo", method="POST",
                                path="/portfolio/events/orders", status=201,
                                ts=NOW - timedelta(hours=3), elapsed_ms=90))
    db_session.add(VenueStatus(venue="kalshi", env="prod", status="ok", reason=None,
                               since=NOW - timedelta(days=1), updated_at=NOW))
    db_session.add(OperatorEvent(ts=NOW - timedelta(hours=4), kind="verify_pass",
                                 summary="demo smoke ok 9 steps", ref={}))
    db_session.flush()

    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert tile["prod_non_get_24h"] == 0 and tile["tripwire_ok"] is True
    assert {"env": "prod", "method": "GET", "count": 1} in tile["by_env_method"]
    assert tile["status"][0]["status"] == "ok"
    assert tile["last_smoke"].startswith("demo smoke")


def test_a_demo_non_get_never_trips_the_production_tripwire(db_session, env_settings):
    db_session.add(VenueRequest(venue="kalshi", env="demo", method="DELETE", path="/x",
                                status=200, ts=NOW - timedelta(hours=1), elapsed_ms=10))
    db_session.flush()
    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert tile["prod_non_get_24h"] == 0 and tile["tripwire_ok"] is True


def test_a_production_non_get_trips_it(db_session, env_settings):
    db_session.add(VenueRequest(venue="kalshi", env="prod", method="POST", path="/x",
                                status=201, ts=NOW - timedelta(hours=1), elapsed_ms=10))
    db_session.flush()
    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert tile["prod_non_get_24h"] == 1 and tile["tripwire_ok"] is False


def test_the_venue_tile_says_so_when_no_smoke_has_run(db_session, env_settings):
    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert tile["last_smoke"] == "no smoke recorded"


def test_a_venue_status_reason_is_sanitized(db_session, env_settings):
    db_session.add(VenueStatus(venue="kalshi", env="prod", status="unavailable",
                               reason='503 <b>go away</b>', since=NOW, updated_at=NOW))
    db_session.flush()
    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert "<" not in tile["status"][0]["reason"]


def test_open_orders_carry_a_queue_bar_and_a_sparkline(db_session, env_settings):
    order = Order(intent_id=__import__("uuid").uuid4(), variant_id="sharp_direct",
                  venue="kalshi", client_order_id="c1", ticker="KXNFL-T", venue_market_id=1,
                  side="yes", prob=Decimal("0.4500"), contracts=Decimal("20.00"),
                  status="open", placed_at=NOW - timedelta(minutes=20),
                  queue_ahead_at_place=Decimal("100.00"), queue_remaining=Decimal("40.00"),
                  book_source="ws")
    db_session.add(order)
    db_session.flush()
    db_session.add(OrderWatchSample(order_id=order.id, ts=NOW - timedelta(minutes=10),
                                    queue_remaining=Decimal("70.00"), book_dirty=False))
    db_session.flush()

    orders = build_floor(db_session, NOW, env_settings)["orders"]["orders"]
    assert orders[0]["queue_ahead_at_place"] == 100.0
    assert orders[0]["queue_remaining"] == 40.0
    assert len(orders[0]["queue_history"]) == 1


def test_an_open_order_carries_the_current_fair_and_a_live_edge(db_session, env_settings):
    """Spec §2.2: "current fair and edge per open order". The edge frozen at placement under a
    live label is the failure this spec is written against, so both travel under their own
    keys."""
    game = _game_with_market(db_session)
    _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["fair_p"] == pytest.approx(0.52)
    assert order["fair_age_s"] == pytest.approx(180, abs=5)
    assert order["edge_live"] == pytest.approx(0.07, abs=1e-6)
    assert "edge_at_place" in order


def test_an_order_whose_market_has_no_recent_fair_value_says_so(db_session, env_settings):
    """No stale number under a live label: the payload says None and the front end falls back to
    the placement edge under its own label."""
    game = _game_with_market(db_session)
    _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(hours=6)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["fair_p"] is None and order["edge_live"] is None
    assert order["fair_age_s"] is None


def _game_with_market(session, *, market_type="moneyline"):
    """A game with one matched venue market, so an order's `venue_market_id` can be translated
    to the `(game_id, market_type)` key `fair_values` is stored under."""
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    session.add(game)
    session.flush()
    market = VenueMarket(venue="kalshi", ticker="KXNFL-T", event_ticker="E",
                         series_ticker="KXNFL", game_id=game.id, market_type=market_type,
                         match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    session.add(market)
    session.flush()
    game.market_id = market.id
    return game


def _open_order(session, *, venue_market_id, prob):
    row = Order(intent_id=__import__("uuid").uuid4(), variant_id="sharp_direct", venue="kalshi",
                client_order_id=str(__import__("uuid").uuid4()), ticker="KXNFL-T",
                venue_market_id=venue_market_id, side="yes", prob=prob,
                contracts=Decimal("20.00"), status="open",
                placed_at=NOW - timedelta(minutes=20), edge_at_place=Decimal("0.0300"))
    session.add(row)
    session.flush()
    return row


def test_exposure_carries_the_newest_equity_per_variant(db_session, env_settings):
    for minutes, cash in ((30, 1000), (5, 1100)):
        db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=minutes),
                                      variant_id="sharp_direct", cash=Decimal(cash),
                                      open_stake=Decimal("0.00"), n_open_positions=0,
                                      n_open_orders=0))
    db_session.flush()
    lanes = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"]
    assert lanes[0]["cash"] == 1100.0


def test_the_payload_carries_sentences_for_every_section(db_session, env_settings):
    payload = build_floor(db_session, NOW, env_settings)
    assert set(payload["sentences"]) == {"board", "funnel", "orders"}
    assert all(payload["sentences"][k] for k in payload["sentences"])
