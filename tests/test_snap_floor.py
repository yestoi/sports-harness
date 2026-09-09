"""The Floor builder: the funnel's sourcing, the game board, the venue tile, and the
never-shown list."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.dashboard.snapshots import floor
from harness.dashboard.snapshots.floor import FLOOR_KEYS, QUEUE_HISTORY_LIMIT, build_floor
from harness.db.models import (EquitySnapshot, FairValue, Fill, Game, GameScoreEvent, Intent,
                               MetricSample, OperatorEvent, Order, OrderEvent, OrderWatchSample,
                               Run, StrategyVariant, Team, VenueMarket, VenueRequest,
                               VenueStatus)

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
#: NOW is 13:00 in America/Chicago, so the local day opened at 05:00 UTC on the same date.
LOCAL_DAY_START = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)


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


def test_no_section_reports_an_error_on_an_empty_database(db_session, env_settings):
    """Every one of the seven sections must build, not land in `section`'s guard."""
    payload = build_floor(db_session, NOW, env_settings)
    errored = {k: v for k, v in payload.items()
               if isinstance(v, dict) and "error" in v}
    assert errored == {}


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


def test_the_board_sanitizes_the_espn_game_status(db_session, env_settings):
    """I3: `link_espn_scoreboard` keeps an unrecognised ESPN status raw and lowercased, which is
    why `games.status` is String(24). It is ESPN scoreboard text and there is no exemption
    list."""
    db_session.add(Game(sport="nfl", home_team_id=1, away_team_id=2,
                        kickoff_utc=NOW + timedelta(hours=2), status="<b>odd</b>"))
    db_session.flush()

    card = build_floor(db_session, NOW, env_settings)["board"]["games"][0]
    assert card["status"] == "bodd/b" and "<" not in card["status"]


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


def test_a_smoke_older_than_the_window_is_not_reported_as_the_last_one(db_session,
                                                                      env_settings):
    """M2: the lookup is bounded, so "no smoke recorded" costs a window rather than the whole
    index. A note older than the window reads the same as none."""
    db_session.add(OperatorEvent(ts=NOW - floor.SMOKE_WINDOW - timedelta(days=1),
                                 kind="verify_pass", summary="demo smoke ok 9 steps", ref={}))
    db_session.flush()
    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert tile["last_smoke"] == "no smoke recorded"


def test_a_venue_status_reason_is_sanitized(db_session, env_settings):
    db_session.add(VenueStatus(venue="kalshi", env="prod", status="unavailable",
                               reason='503 <b>go away</b>', since=NOW, updated_at=NOW))
    db_session.flush()
    tile = build_floor(db_session, NOW, env_settings)["venue"]
    assert "<" not in tile["status"][0]["reason"]


def test_open_orders_carry_a_queue_bar_and_a_sparkline(db_session, env_settings):
    order = Order(intent_id=uuid.uuid4(), variant_id="sharp_direct",
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


def test_an_open_order_carries_our_price_against_the_book(db_session, env_settings):
    """Spec §2.2 layout (3) names our price against best bid and ask. The pair comes off the
    newest watch sample, not the oldest."""
    order = _open_order(db_session, venue_market_id=1, prob=Decimal("0.4500"))
    db_session.add(OrderWatchSample(order_id=order.id, ts=NOW - timedelta(minutes=15),
                                    queue_remaining=Decimal("90.00"), best_bid=Decimal("0.4000"),
                                    best_ask=Decimal("0.5000"), book_dirty=False))
    db_session.add(OrderWatchSample(order_id=order.id, ts=NOW - timedelta(minutes=2),
                                    queue_remaining=Decimal("70.00"), best_bid=Decimal("0.4400"),
                                    best_ask=Decimal("0.4800"), book_dirty=False))
    db_session.flush()

    row = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert row["best_bid"] == pytest.approx(0.44) and row["best_ask"] == pytest.approx(0.48)
    assert row["book_age_s"] == pytest.approx(120, abs=5)


def test_the_queue_history_keeps_only_the_newest_samples(db_session, env_settings):
    """Ruling (b): 100 orders x a 2 h window at a 60 s sample was 12,000 pairs and 472 KB of
    payload, rewritten every 15 s. The read is still bounded by the window; the payload is
    bounded per order."""
    order = _open_order(db_session, venue_market_id=1, prob=Decimal("0.4500"))
    for minute in range(40, 0, -1):
        db_session.add(OrderWatchSample(order_id=order.id,
                                        ts=NOW - timedelta(minutes=minute),
                                        queue_remaining=Decimal(minute), book_dirty=False))
    db_session.flush()

    history = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]["queue_history"]
    assert len(history) == QUEUE_HISTORY_LIMIT == 30
    # Newest last, and the oldest ten were dropped rather than the newest ten.
    assert history[-1][1] == 1.0 and history[0][1] == 30.0


def test_an_open_order_carries_the_current_fair_and_a_live_edge(db_session, env_settings):
    """Spec §2.2: "current fair and edge per open order". The edge frozen at placement under a
    live label is the failure this spec is written against, so both travel under their own keys
    -- and on the same basis: ruling (a) makes `edge_live` net of the maker fee at the resting
    price, exactly as `harness/strategy/run.py` computes `edge_at_place`.

    At fair 0.5200 and a resting 0.4500 on a market whose `fee_type` is NULL (which
    `fee_model_for` resolves to KALSHI_FOOTBALL), the maker fee per contract is 0.0043, so the
    live edge is 0.0657 and not the gross 0.0700.
    """
    game = _game_with_market(db_session)
    _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["fair_p"] == pytest.approx(0.52)
    assert order["fair_age_s"] == pytest.approx(180, abs=5)
    assert order["edge_live"] == pytest.approx(0.0657, abs=1e-9)
    assert order["edge_at_place"] == pytest.approx(0.03)
    # Same basis: both are net of the maker fee at their own price, so the gross difference is
    # never what the front end shows.
    assert order["edge_live"] < 0.52 - 0.45


def test_a_no_side_order_prices_its_live_edge_in_its_own_side_space(db_session, env_settings):
    """`side_p` is what keeps a NO order's edge from being the YES leg's with a sign error: at a
    YES-space fair of 0.5200 the NO fair is 0.4800, so a NO order resting at 0.4500 has
    0.4800 - 0.4500 - 0.0043 = 0.0257 of edge, not 0.0657."""
    game = _game_with_market(db_session)
    _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"), side="no")
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["side"] == "no"
    assert order["edge_live"] == pytest.approx(0.0257, abs=1e-9)


def test_an_unpriceable_fee_shape_omits_the_live_edge_and_keeps_the_section(db_session,
                                                                          env_settings):
    """`fee_model_for` raises on an unrecognised `fee_type` by design, so an unpriced shape is
    never scored at football rates. Letting that out of the builder would mark the whole
    `orders` section `{"error": "ValueError"}` -- the surface's centre panel gone over one odd
    market."""
    game = _game_with_market(db_session, fee_type="brand_new_shape")
    _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    payload = build_floor(db_session, NOW, env_settings)
    assert "error" not in payload["orders"]
    order = payload["orders"]["orders"][0]
    assert order["edge_live"] is None
    # The fair itself is still known and still shown; only the fee-net edge is unavailable.
    assert order["fair_p"] == pytest.approx(0.52)
    assert order["edge_at_place"] == pytest.approx(0.03)


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


def _teams(session):
    for team_id, name in ((1, "Saints"), (2, "Falcons")):
        if session.get(Team, ("nfl", team_id)) is None:
            session.add(Team(sport="nfl", id=team_id, display_name=f"<b>{name}</b>",
                             location="X", name=name, abbreviation=name[:3].upper(),
                             short_display_name=name))
    session.flush()


def _game_with_market(session, *, market_type="moneyline", fee_type=None):
    """A game with one matched venue market, so an order's `venue_market_id` can be translated
    to the `(game_id, market_type)` key `fair_values` is stored under."""
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    session.add(game)
    session.flush()
    market = VenueMarket(venue="kalshi", ticker=f"KXNFL-T-{game.id}", event_ticker="E",
                         series_ticker="KXNFL", game_id=game.id, market_type=market_type,
                         match_status="matched", first_seen_raw_id=1, last_seen_at=NOW,
                         fee_type=fee_type)
    session.add(market)
    session.flush()
    game.market_id = market.id
    return game


def _open_order(session, *, venue_market_id, prob, side="yes", variant_id="sharp_direct",
                status="open", game_id=None):
    row = Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi",
                client_order_id=str(uuid.uuid4()), ticker=f"KXNFL-T-{uuid.uuid4().hex[:8]}",
                venue_market_id=venue_market_id, side=side, prob=prob,
                contracts=Decimal("20.00"), status=status, game_id=game_id,
                placed_at=NOW - timedelta(minutes=20), edge_at_place=Decimal("0.0300"))
    session.add(row)
    session.flush()
    return row


def test_the_fills_stream_carries_the_print_the_tape_and_the_game(db_session, env_settings):
    """I2 plus spec §2.2 layout (4): `through`, the tape source, and the game the fill was on --
    a ticker is not a game name to a phone reader. The stream is today's in local time, so a
    fill an hour before local midnight belongs to yesterday and must not appear."""
    _teams(db_session)
    game = _game_with_market(db_session)
    order = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                        game_id=game.id)
    db_session.add(Fill(order_id=order.id, prob=Decimal("0.4500"), contracts=Decimal("5.00"),
                        fee=Decimal("0.0200"), filled_at=NOW - timedelta(hours=1),
                        fill_method="queue_model", through=True, tape_source="ws",
                        has_print=True, replay=False))
    # An hour before the local day opened: yesterday's fill.
    db_session.add(Fill(order_id=order.id, prob=Decimal("0.4400"), contracts=Decimal("3.00"),
                        fee=Decimal("0.0100"), filled_at=LOCAL_DAY_START - timedelta(hours=1),
                        fill_method="queue_model", through=False, tape_source="rest",
                        has_print=True, replay=False, source_trade_id="yesterday"))
    db_session.flush()

    fills = build_floor(db_session, NOW, env_settings)["fills"]["fills"]
    assert len(fills) == 1, "only the fill inside today's local day belongs in the stream"
    row = fills[0]
    assert row["through"] is True and row["tape_source"] == "ws"
    assert row["game_id"] == game.id
    assert row["home"] == "bSaints/b" and "<" not in row["home"]
    assert row["away"] == "bFalcons/b"
    assert row["contracts"] == 5.0


def test_the_vitals_sparkline_key_is_sanitized(db_session, env_settings):
    """I2: `exec.skipped` carries a reason label, and the key it builds is a payload string."""
    db_session.add(MetricSample(ts=NOW - timedelta(minutes=5), source="exec",
                                name="exec.skipped", labels={"reason": "<b>kickoff</b>"},
                                value=Decimal("3")))
    db_session.add(MetricSample(ts=NOW - timedelta(minutes=4), source="exec",
                                name="exec.loop_ms", labels={}, value=Decimal("120")))
    db_session.flush()

    vitals = build_floor(db_session, NOW, env_settings)["vitals"]
    assert "exec.skipped:bkickoff/b" in vitals["sparklines"]
    assert not any("<" in key for key in vitals["sparklines"])
    assert vitals["sparklines"]["exec.loop_ms"] == [
        [(NOW - timedelta(minutes=4)).isoformat(), 120.0]]


def test_exposure_carries_the_newest_equity_per_variant(db_session, env_settings):
    for minutes, cash in ((30, 1000), (5, 1100)):
        db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=minutes),
                                      variant_id="sharp_direct", cash=Decimal(cash),
                                      open_stake=Decimal("0.00"), n_open_positions=0,
                                      n_open_orders=0))
    db_session.flush()
    lanes = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"]
    assert lanes[0]["cash"] == 1100.0


def test_an_equity_row_older_than_the_window_leaves_no_lane(db_session, env_settings):
    """I1: `_EQUITY` is bounded, because unbounded it is a 181,440-row disk-spilling sort at a
    season's volume. Seven days is generous enough that only a week-long executor outage can
    empty the lane."""
    db_session.add(EquitySnapshot(ts=NOW - floor.EQUITY_WINDOW - timedelta(days=1),
                                  variant_id="sharp_direct", cash=Decimal(1000),
                                  open_stake=Decimal("0.00"), n_open_positions=0,
                                  n_open_orders=0))
    db_session.flush()
    assert build_floor(db_session, NOW, env_settings)["exposure"]["lanes"] == []


def test_the_exposure_lane_carries_fills_today_and_daily_cap_use(db_session, env_settings):
    """Spec §2.2 layout (5): fills today and daily cap use. `daily_exposure` is the open orders'
    stake plus today's fills, which is what `harness/execution/plan.py` measures `cap_daily`
    against; the cap is `daily_cap * bankroll` out of the variant's own registered config.

    One open order at 0.45 x 20 contracts is 9.00 of resting stake (no intent row, so the
    `coalesce` falls back to the order's own cost basis) and one fill of 20 contracts at 0.45 is
    another 9.00, so 18.00 against a cap of 0.15 x 3000 = 450.00, which is 4 % used.
    """
    db_session.add(StrategyVariant(variant_id="capped", name="constrained_t", tier="secondary",
                                   config_json={"apply_caps": True, "daily_cap": 0.15,
                                                "bankroll": 3000},
                                   registered_at=NOW, active=True))
    db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=5), variant_id="capped",
                                  cash=Decimal(3000), open_stake=Decimal("9.00"),
                                  n_open_positions=1, n_open_orders=1))
    game = _game_with_market(db_session)
    order = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                        variant_id="capped", game_id=game.id)
    db_session.add(Fill(order_id=order.id, prob=Decimal("0.4500"), contracts=Decimal("20.00"),
                        fee=Decimal("0.0400"), filled_at=NOW - timedelta(hours=1),
                        fill_method="queue_model", tape_source="ws", replay=False))
    db_session.flush()

    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["variant"] == "capped"
    assert lane["fills_today"] == 1
    assert lane["fills_today_stake"] == pytest.approx(9.0)
    assert lane["daily_exposure"] == pytest.approx(18.0)
    assert lane["daily_cap"] == pytest.approx(450.0)
    assert lane["caps_enforced"] is True
    assert lane["cap_use"] == pytest.approx(0.04)
    # The fill is on an unsettled order, so it is a held position too.
    assert lane["open_contracts"] == pytest.approx(20.0)


def test_a_variant_that_only_labels_the_caps_reports_no_cap_use(db_session, env_settings):
    """`apply_caps: false` variants record the cap labels without blocking on them
    (`harness/strategy/run.py`), so a "% of cap" reading would describe a limit that does not
    exist. The cap itself is still reported, so the front end can say what it would have been."""
    db_session.add(StrategyVariant(variant_id="loose", name="sharp_direct_t", tier="primary",
                                   config_json={"apply_caps": False, "daily_cap": 0.15,
                                                "bankroll": 3000},
                                   registered_at=NOW, active=True))
    db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=5), variant_id="loose",
                                  cash=Decimal(3000), open_stake=Decimal("0.00"),
                                  n_open_positions=0, n_open_orders=0))
    db_session.flush()

    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["caps_enforced"] is False
    assert lane["cap_use"] is None
    assert lane["daily_cap"] == pytest.approx(450.0)
    assert lane["fills_today"] == 0 and lane["daily_exposure"] == pytest.approx(0.0)


def test_the_payload_carries_sentences_for_every_section(db_session, env_settings):
    payload = build_floor(db_session, NOW, env_settings)
    assert set(payload["sentences"]) == {"board", "funnel", "orders"}
    assert all(payload["sentences"][k] for k in payload["sentences"])
