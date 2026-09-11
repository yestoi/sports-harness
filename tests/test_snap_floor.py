"""The Floor builder: the funnel's sourcing, the game board, the venue tile, and the
never-shown list."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from harness.dashboard.snapshots import floor
from harness.dashboard.snapshots.floor import FLOOR_KEYS, QUEUE_HISTORY_LIMIT, build_floor
from harness.db.models import (EquitySnapshot, FairValue, Fill, Game, GameScoreEvent,
                               MetricSample, OperatorEvent, Order, OrderWatchSample, Run,
                               StrategyVariant, Team, VenueMarket, VenueRequest, VenueStatus)
from harness.pricing.fair import compute_fair_values
from tests.test_fair import NOW as FAIR_NOW
from tests.test_fair import _seed as _seed_priced_game

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


def _exec_metric(db_session, name, value, reason=None, minutes=60):
    """One `exec.*` sample of the kind `harness/execution/loop.py` writes once per
    `metric_sample_s`. Fix 31: the funnel's placed, skipped and cancelled counts come off these
    instead of scanning `intents`, `orders` and `order_events`."""
    db_session.add(MetricSample(ts=NOW - timedelta(minutes=minutes), source="exec", name=name,
                                value=value, labels={} if reason is None
                                else {"reason": reason}))


def test_the_funnel_sums_the_run_notes_and_the_executors_own_per_minute_counts(db_session,
                                                                               env_settings):
    """Ticks, gaps, candidates and rejections out of `runs.notes`; placed, skipped and cancelled
    out of `metric_samples`; `intents` is placed plus skipped, the intents that reached a
    decision (fix 31)."""
    db_session.add(Run(started_at=NOW - timedelta(hours=1), status="ok", build_sha="abc",
                       notes={"pricing": {"ticks": 40, "gaps": 900, "fair_direct": 30,
                                          "signals": {"sharp_direct": {"candidate": 12,
                                                                       "rejected": 88}}}}))
    _exec_metric(db_session, "exec.placed", 3)
    _exec_metric(db_session, "exec.placed", 2, minutes=30)
    _exec_metric(db_session, "exec.skipped", 1, reason="kickoff")
    _exec_metric(db_session, "exec.cancelled", 4, reason="reprice")
    db_session.flush()

    funnel = build_floor(db_session, NOW, env_settings)["funnel"]
    assert funnel["gaps"] == 900 and funnel["ticks"] == 40
    assert funnel["candidates"] == 12 and funnel["rejected_total"] == 88
    assert funnel["orders"] == 5
    assert funnel["intents"] == 6
    assert {"reason": "kickoff", "count": 1, "plain": "too close to kickoff"} in \
        funnel["skipped"]
    assert [row["reason"] for row in funnel["cancelled"]] == ["reprice"]


def test_the_funnel_counts_only_the_window(db_session, env_settings):
    """The 6 h `FUNNEL_WINDOW` is the bound on `_FUNNEL_COUNTS`, and it rides
    `ix_metric_samples_name_ts`. A sample outside it is not a smaller number, it is no number."""
    _exec_metric(db_session, "exec.placed", 7, minutes=60)
    _exec_metric(db_session, "exec.placed", 99, minutes=60 * 7)
    db_session.flush()

    assert build_floor(db_session, NOW, env_settings)["funnel"]["orders"] == 7


def test_an_unknown_skip_reason_lands_in_sentences_gaps_sanitized(db_session, env_settings):
    """Ruling A-I2: a reason code the vocabulary has never seen must not be silently lost."""
    db_session.add(Run(started_at=NOW - timedelta(hours=1), status="ok", build_sha="abc",
                       notes={"pricing": {}}))
    _exec_metric(db_session, "exec.skipped", 1, reason="<script>brand_new_reason</script>")
    db_session.flush()

    payload = build_floor(db_session, NOW, env_settings)
    assert payload["sentences_gaps"] == ["scriptbrand_new_reason/script"]


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


def test_the_venue_tile_reads_the_gateway_row_not_the_rfq_listener(db_session, env_settings):
    """Review T13, I2. Phase 5's RFQ listener writes `('kalshi_rfq', 'prod')` and marks it `ok`
    on every subscribe ack, so it is usually the newest prod row. This tile is the gateway's
    health: a healthy listener must never paint over a venue that is actually unavailable."""
    db_session.add(VenueStatus(venue="kalshi", env="prod", status="unavailable",
                               reason="401 twice", since=NOW - timedelta(hours=2),
                               updated_at=NOW - timedelta(hours=2)))
    db_session.add(VenueStatus(venue="kalshi_rfq", env="prod", status="ok", reason=None,
                               since=NOW, updated_at=NOW))
    db_session.flush()
    status = build_floor(db_session, NOW, env_settings)["venue"]["status"]
    prod = [row for row in status if row["env"] == "prod"]
    assert len(prod) == 1
    assert prod[0]["status"] == "unavailable"


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


# --- fix 31: the bounds themselves ----------------------------------------------------------

def test_the_exposure_lane_reads_the_bounded_aggregate_and_not_the_positions_view(db_session,
                                                                                 env_settings):
    """Fix 31 reverses the phase 4.5 ruling that left `_EXPOSURE` on the `positions` view. The
    view aggregates every money fill of every unsettled order with no time bound of any kind, so
    on a database where settlement has stalled it walks the season; here the same aggregate is
    driven from `fills` under `EXPOSURE_WINDOW` off `ix_fills_filled_at`.

    The bound drops a position only when settlement has been stalled for a fortnight, which is a
    louder failure than a missing lane and is watched by its own invariant.
    """
    db_session.add(StrategyVariant(variant_id="capped", name="constrained_t", tier="secondary",
                                   config_json={}, registered_at=NOW, active=True))
    db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=5), variant_id="capped",
                                  cash=Decimal(3000), open_stake=Decimal("0.00"),
                                  n_open_positions=1, n_open_orders=1))
    game = _game_with_market(db_session)
    order = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                        variant_id="capped", game_id=game.id)
    inside = NOW - floor.EXPOSURE_WINDOW + timedelta(hours=1)
    db_session.add(Fill(order_id=order.id, prob=Decimal("0.4500"), contracts=Decimal("7.00"),
                        fee=Decimal("0.0400"), filled_at=inside, fill_method="queue_model",
                        tape_source="ws", replay=False))
    db_session.flush()
    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["open_contracts"] == pytest.approx(7.0)

    outside = NOW - floor.EXPOSURE_WINDOW - timedelta(hours=1)
    db_session.add(Fill(order_id=order.id, prob=Decimal("0.4500"), contracts=Decimal("500.00"),
                        fee=Decimal("0.0400"), filled_at=outside, fill_method="queue_model",
                        source_trade_id="stale", tape_source="ws", replay=False))
    db_session.flush()
    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["open_contracts"] == pytest.approx(7.0), "the window is the bound"


def test_a_settled_or_replay_order_is_still_excluded_from_exposure(db_session, env_settings):
    """The bound is new; the three predicates under it are `_POSITIONS_VIEW`'s own, and the
    replacement has to agree with the view on which fills are real."""
    db_session.add(StrategyVariant(variant_id="capped", name="constrained_t", tier="secondary",
                                   config_json={}, registered_at=NOW, active=True))
    db_session.add(EquitySnapshot(ts=NOW - timedelta(minutes=5), variant_id="capped",
                                  cash=Decimal(3000), open_stake=Decimal("0.00"),
                                  n_open_positions=0, n_open_orders=0))
    game = _game_with_market(db_session)
    settled = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                          variant_id="capped", status="settled", game_id=game.id)
    live = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                       variant_id="capped", game_id=game.id)
    for order, method in ((settled, "queue_model"), (live, "no_watcher")):
        db_session.add(Fill(order_id=order.id, prob=Decimal("0.4500"),
                            contracts=Decimal("11.00"), fee=Decimal("0.0400"),
                            filled_at=NOW - timedelta(hours=1), fill_method=method,
                            tape_source="ws", replay=False))
    db_session.flush()
    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["open_contracts"] == pytest.approx(0.0)


def test_the_funnel_caps_the_run_notes_it_reads(db_session, env_settings, monkeypatch):
    """`runs` carries no index on `started_at`, so the window predicate never stopped the read:
    it was a sequential scan of every run of the season with its `notes` JSONB. The cap is what
    stops it, which is why `recent_run_notes` sends the limit without the predicate and applies
    the window in Python -- a predicate beside a limit lets the backward primary-key walk run to
    the start of the table looking for matches it will never need."""
    seen = {}

    def _spy(session, cutoff, limit=None):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(floor, "recent_run_notes", _spy)
    build_floor(db_session, NOW, env_settings)
    assert seen["limit"] == floor.FUNNEL_NOTES_LIMIT


def test_the_funnel_still_counts_only_its_own_window_of_runs(db_session, env_settings):
    """The window moved from the SQL into Python for a capped caller, so it has to be asserted
    on the real read: a run older than `FUNNEL_WINDOW` comes back from the database inside the
    limit and must still be discarded before its ticks are summed."""
    db_session.add(Run(started_at=NOW - timedelta(hours=1), status="ok", build_sha="abc",
                       notes={"pricing": {"ticks": 40, "gaps": 5}}))
    db_session.add(Run(started_at=NOW - floor.FUNNEL_WINDOW - timedelta(hours=1), status="ok",
                       build_sha="abc", notes={"pricing": {"ticks": 999, "gaps": 999}}))
    db_session.flush()

    funnel = build_floor(db_session, NOW, env_settings)["funnel"]
    assert funnel["ticks"] == 40 and funnel["gaps"] == 5


def test_the_board_score_read_is_bounded_to_its_own_window(db_session, env_settings):
    """A score row older than `SCORES_WINDOW` is not this game's live score; the predicate is
    what prunes inside each game's range of `ix_game_score_events_game_ts`."""
    db_session.add(Team(sport="nfl", id=1, display_name="Saints", location="New Orleans",
                        name="Saints", abbreviation="NO", short_display_name="Saints"))
    db_session.add(Team(sport="nfl", id=2, display_name="Falcons", location="Atlanta",
                        name="Falcons", abbreviation="ATL", short_display_name="Falcons"))
    game = Game(sport="nfl", home_team_id=1, away_team_id=2, status="in_progress",
                kickoff_utc=NOW - timedelta(minutes=30))
    db_session.add(game)
    db_session.flush()
    db_session.add(GameScoreEvent(game_id=game.id, ts=NOW - floor.SCORES_WINDOW
                                  - timedelta(hours=1), status="in_progress", period=1,
                                  clock="10:00", home_score=3, away_score=0))
    db_session.flush()

    board = build_floor(db_session, NOW, env_settings)["board"]["games"][0]
    assert board["home_score"] is None and board["score_age_s"] is None

    db_session.add(GameScoreEvent(game_id=game.id, ts=NOW - timedelta(minutes=1),
                                  status="in_progress", period=2, clock="02:00",
                                  home_score=17, away_score=10))
    db_session.flush()
    board = build_floor(db_session, NOW, env_settings)["board"]["games"][0]
    assert board["home_score"] == 17


def _spread_market(session, game, *, threshold, ticker):
    """One spread line of `game`. Both markets this helper builds carry the same `side`, so
    `threshold` is the only column that separates them -- which is the point of the test below.

    The `side="yes"` is arbitrary filler, not a production shape: `upsert_venue_markets`
    (`harness/normalize/kalshi.py:112`) writes `side` NULL for every team-sided market
    (moneyline and spread) and `"over"` for a total, and `fair_values.outcome_side` is written
    from the same mapping (`harness/pricing/fair.py:113-118`). Read the NULL-keyed test below
    for the shape production actually stores.
    """
    market = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="E", series_ticker="KXNFL",
                         game_id=game.id, market_type="spread",
                         threshold=Decimal(str(threshold)), side_team_id=1, side="yes",
                         match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    session.add(market)
    session.flush()
    return market


def test_an_open_order_reads_the_fair_for_its_own_contract_not_its_games(db_session,
                                                                        env_settings):
    """Design review I3/I4: `fair_values` is keyed `(game_id, market_type)` *plus* `threshold`,
    `outcome_team_id` and `outcome_side`, and `venue_markets` spells the same three `threshold`,
    `side_team_id` and `side`. Joining on the first two alone hands a -3.5 order the -7.5 fair.
    """
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    db_session.add(game)
    db_session.flush()
    minus_three = _spread_market(db_session, game, threshold=-3.5, ticker="KXNFL-S-35")
    minus_seven = _spread_market(db_session, game, threshold=-7.5, ticker="KXNFL-S-75")
    _open_order(db_session, venue_market_id=minus_three.id, prob=Decimal("0.4500"))
    _open_order(db_session, venue_market_id=minus_seven.id, prob=Decimal("0.4500"))
    # The -7.5 fair is written *last*, so a join that takes the newest row for the game and
    # market type hands it to both orders.
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="spread",
                             outcome_team_id=1, outcome_side="yes", threshold=Decimal("-3.5"),
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=6)))
    db_session.add(FairValue(run_id=2, game_id=game.id, market_type="spread",
                             outcome_team_id=1, outcome_side="yes", threshold=Decimal("-7.5"),
                             fair_p=Decimal("0.3100"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    shown = build_floor(db_session, NOW, env_settings)["orders"]["orders"]
    assert len(shown) == 2
    # The order payload carries the ticker, not the market id, and `_open_order` gives each
    # order its own random ticker -- so the two are matched up through `id` on the stored rows.
    fairs = {order["id"]: order["fair_p"] for order in shown}
    by_market = {row.venue_market_id: row.id for row in db_session.query(Order).all()}
    assert fairs[by_market[minus_three.id]] == pytest.approx(0.52)
    assert fairs[by_market[minus_seven.id]] == pytest.approx(0.31)


def test_a_moneyline_order_reads_its_own_teams_fair(db_session, env_settings):
    """Design review I3: `fair_values.outcome_team_id` is what distinguishes the two sides of
    one `(game_id, 'moneyline')` pair. Without it the home order can read the away fair."""
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    db_session.add(game)
    db_session.flush()
    home = VenueMarket(venue="kalshi", ticker="KXNFL-ML-HOME", event_ticker="E",
                       series_ticker="KXNFL", game_id=game.id, market_type="moneyline",
                       side_team_id=1, match_status="matched", first_seen_raw_id=1,
                       last_seen_at=NOW)
    away = VenueMarket(venue="kalshi", ticker="KXNFL-ML-AWAY", event_ticker="E",
                       series_ticker="KXNFL", game_id=game.id, market_type="moneyline",
                       side_team_id=2, match_status="matched", first_seen_raw_id=1,
                       last_seen_at=NOW)
    db_session.add_all([home, away])
    db_session.flush()
    _open_order(db_session, venue_market_id=home.id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             outcome_team_id=1, fair_p=Decimal("0.6000"), fair_source="direct",
                             staleness_s=40, created_at=NOW - timedelta(minutes=6)))
    db_session.add(FairValue(run_id=2, game_id=game.id, market_type="moneyline",
                             outcome_team_id=2, fair_p=Decimal("0.4000"), fair_source="direct",
                             staleness_s=40, created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["fair_p"] == pytest.approx(0.60)


def test_a_null_keyed_fair_matches_only_a_null_keyed_market(db_session, env_settings):
    """D10: the three predicates are `is not distinct from`, so NULL matches NULL -- which is
    what keeps a moneyline market with no `side_team_id` (and every older, unkeyed fair row)
    inside the join instead of silently dropping out of it.

    Only that half is asserted here: that a NULL-keyed fair *does* reach a NULL-keyed market.
    The converse -- a NULL-keyed fair passed over for a keyed market -- is what the two tests
    above cover, each of which hands its order a keyed fair over a newer rival.
    """
    game = _game_with_market(db_session)          # no side_team_id, no side, no threshold
    _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             outcome_team_id=None, outcome_side=None, threshold=None,
                             fair_p=Decimal("0.5200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["fair_p"] == pytest.approx(0.52)


def test_a_no_orders_live_edge_uses_one_minus_the_fair_and_the_book_is_not_converted_twice(
        db_session, env_settings):
    """Design review I2: Floor already converts through `side_p` (`floor.py:547`) and the
    executor already writes `best_bid`/`best_ask` in the order's own side (`loop.py:1110-1111`),
    so this pins the existing conversions and forbids a second one. At a YES-space fair of 0.62
    a NO order resting at 0.40 has 1 - 0.62 = 0.38 of fair, so 0.38 - 0.40 - 0.0042 = -0.0242 --
    never the 0.2158 an unconverted fair would give.

    `test_a_no_side_order_prices_its_live_edge_in_its_own_side_space` already pins the
    `edge_live` half; what is new here is the book pair, which must survive untouched.
    """
    game = _game_with_market(db_session)
    order = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4000"),
                        side="no")
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="moneyline",
                             fair_p=Decimal("0.6200"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.add(OrderWatchSample(order_id=order.id, ts=NOW - timedelta(minutes=1),
                                    queue_remaining=Decimal("5"), book_dirty=False,
                                    best_bid=Decimal("0.3900"), best_ask=Decimal("0.4100")))
    db_session.flush()

    shown = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert shown["side"] == "no"
    assert shown["edge_live"] == pytest.approx(-0.0242, abs=1e-9)
    # `best_bid`/`best_ask` come off the newest watch sample, which the executor writes as
    # `book.best_bid(row.side)` (`loop.py:1110-1111`) -- already in this order's own side space.
    # They are carried through unchanged, and converting them here would be the double
    # conversion I2 warns about.
    assert shown["best_bid"] == pytest.approx(0.39)
    assert shown["best_ask"] == pytest.approx(0.41)


def test_floor_selects_no_placement_mid_to_convert():
    """Design review I2, as a structural test: `_OPEN_ORDERS` never selects
    `venue_mid_at_place` and `_BOARD` carries no mids, so there is no second quantity in YES
    space for a future edit to convert by mistake."""
    body = Path(floor.__file__).read_text()
    assert "venue_mid_at_place" not in body


def test_a_total_orders_side_discriminates_over_from_a_null_sided_decoy(db_session,
                                                                        env_settings):
    """Design review I1: `outcome_side` is the one identity predicate no existing test covers --
    the spread test above discriminates on `threshold`, the moneyline test on `outcome_team_id`,
    and deleting `floor.py`'s `f.outcome_side is not distinct from (m.side)` line leaves the
    whole file green. A total is the only market type where `fair_values.outcome_side` is ever
    non-NULL (`harness/pricing/fair.py:117`, `over` always), so this seeds an `over` market
    beside a NULL-sided decoy fair sharing its game, market type and threshold -- the shape an
    older, unkeyed row would have. The decoy is written *last*, so a join that drops this
    predicate hands the order the decoy instead of its own `over` fair.
    """
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    db_session.add(game)
    db_session.flush()
    over = VenueMarket(venue="kalshi", ticker="KXNFL-TOT-OVER", event_ticker="E",
                       series_ticker="KXNFL", game_id=game.id, market_type="total",
                       threshold=Decimal("44.5"), side="over",
                       match_status="matched", first_seen_raw_id=1, last_seen_at=NOW)
    db_session.add(over)
    db_session.flush()
    _open_order(db_session, venue_market_id=over.id, prob=Decimal("0.4500"))
    db_session.add(FairValue(run_id=1, game_id=game.id, market_type="total",
                             outcome_team_id=None, outcome_side="over", threshold=Decimal("44.5"),
                             fair_p=Decimal("0.5500"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=6)))
    # NULL-sided decoy: same game, market type and threshold, written last.
    db_session.add(FairValue(run_id=2, game_id=game.id, market_type="total",
                             outcome_team_id=None, outcome_side=None, threshold=Decimal("44.5"),
                             fair_p=Decimal("0.3500"), fair_source="direct", staleness_s=40,
                             created_at=NOW - timedelta(minutes=3)))
    db_session.flush()

    order = build_floor(db_session, NOW, env_settings)["orders"]["orders"][0]
    assert order["fair_p"] == pytest.approx(0.55)


def test_the_join_agrees_with_the_real_writer_for_every_market_shape(db_session, env_settings):
    """Design review I2: the three identity predicates in `_FAIR_FOR_ORDERS` are a third,
    independent copy of the shape mapping `harness.pricing.fair._shapes_for_game` writes and
    `harness.normalize.kalshi` / `harness.matching.kalshi` produce on `venue_markets` -- a
    future change to either side would silently empty the lateral for the affected market type,
    and per the test above, no test would fail (the visible symptom is a null `fair_p` that
    reads as "no fair yet" rather than as a bug). Rather than hand-build a `FairValue` row the
    way every test above does, this seeds a game through the same fixture
    `tests/test_fair.py`'s own tests seed and runs the real writer, `compute_fair_values`, then
    checks one order of each shape -- moneyline, spread, total -- reads back exactly the fair
    row that writer produced for its own market.
    """
    game, run = _seed_priced_game(db_session)
    compute_fair_values(db_session, run.id, FAIR_NOW, env_settings)

    markets = {m.ticker: m for m in
              db_session.query(VenueMarket).filter_by(game_id=game.id).all()}
    picks = {
        "moneyline": markets["KXNFL-1"],  # side_team_id=HOME, side=None, threshold=None
        "spread": markets["KXNFL-3"],     # side_team_id=HOME, side=None, threshold=3.5
        "total": markets["KXNFL-6"],      # side_team_id=None, side="over", threshold=44.5
    }
    orders = {}
    for key, market in picks.items():
        order = Order(intent_id=uuid.uuid4(), variant_id="sharp_direct", venue="kalshi",
                      client_order_id=str(uuid.uuid4()), ticker=f"{market.ticker}-ORD",
                      venue_market_id=market.id, side="yes", prob=Decimal("0.4500"),
                      contracts=Decimal("20.00"), status="open", game_id=game.id,
                      placed_at=FAIR_NOW - timedelta(minutes=20),
                      edge_at_place=Decimal("0.0300"))
        db_session.add(order)
        orders[key] = order
    db_session.flush()

    expected = {
        key: db_session.query(FairValue).filter_by(
            run_id=run.id, game_id=game.id, market_type=market.market_type,
            outcome_team_id=market.side_team_id, outcome_side=market.side,
            threshold=market.threshold).one()
        for key, market in picks.items()
    }

    shown = {order["id"]: order["fair_p"]
            for order in build_floor(db_session, FAIR_NOW, env_settings)["orders"]["orders"]}
    for key, order in orders.items():
        assert shown[order.id] == pytest.approx(float(expected[key].fair_p)), key


def _plan_nodes(node, relation=None):
    """Every plan node (recursively) in an `explain (format json)` result, optionally narrowed
    to the nodes reading `relation`."""
    nodes = []
    if isinstance(node, list):
        for item in node:
            nodes.extend(_plan_nodes(item, relation))
    elif isinstance(node, dict):
        if relation is None or node.get("Relation Name") == relation:
            nodes.append(node)
        if "Plan" in node:
            nodes.extend(_plan_nodes(node["Plan"], relation))
        if "Plans" in node:
            nodes.extend(_plan_nodes(node["Plans"], relation))
    return nodes


def test_the_lateral_leads_on_ix_fair_game_type_created_and_filters_the_rest(db_session,
                                                                            env_settings):
    """Design review I3: the cost note at `floor.py:271-283` was asserted, not measured, and
    `rfq_grade.py:64-67` already records this repo's answer for the identical shape -- three
    `is not distinct from` predicates over `(outcome_team_id, outcome_side, threshold)` are not
    indexable, so the planner can only ever place them in a heap `Filter`, never an
    `Index Cond`, while still leading the scan on `ix_fair_game_type_created` for
    `(game_id, market_type, created_at)`. Modelled on
    `tests/test_rfq_quote.py::test_ix_fair_leg_lookup_is_chosen_for_the_leg_query`: seed enough
    rows sharing `(game_id, market_type)` that leading on the index is the obviously cheaper
    plan, force the planner off a sequential scan the way that test does, and read the actual
    plan instead of repeating the claim.
    """
    game = Game(sport="nfl", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW + timedelta(hours=2), status="scheduled")
    db_session.add(game)
    db_session.flush()
    market = VenueMarket(venue="kalshi", ticker="KXNFL-ML-EXPLAIN", event_ticker="E",
                         series_ticker="KXNFL", game_id=game.id, market_type="moneyline",
                         side_team_id=1, match_status="matched", first_seen_raw_id=1,
                         last_seen_at=NOW)
    db_session.add(market)
    db_session.flush()

    base = NOW - timedelta(hours=1)
    for i in range(300):
        db_session.add(FairValue(run_id=800_000 + i, game_id=game.id, market_type="moneyline",
                                 outcome_team_id=100 + i, outcome_side=None, threshold=None,
                                 fair_p=Decimal("0.5000"), fair_source="direct", staleness_s=40,
                                 created_at=base + timedelta(seconds=i)))
    # The one row this market's contract actually matches, newest of all.
    db_session.add(FairValue(run_id=899_999, game_id=game.id, market_type="moneyline",
                             outcome_team_id=1, outcome_side=None, threshold=None,
                             fair_p=Decimal("0.6100"), fair_source="direct", staleness_s=40,
                             created_at=base + timedelta(seconds=301)))
    db_session.flush()
    db_session.execute(text("analyze fair_values"))
    db_session.execute(text("set local enable_seqscan = off"))
    # `enable_seqscan` alone is not enough: on this small synthetic table the planner reaches
    # for `ix_fair_created_brin` via a `Bitmap Heap Scan` instead, which is the "small-synthetic
    # -table artifact" I3 warns about -- not the plan this test means to pin. Ruling out bitmap
    # scans too leaves the b-tree `ix_fair_game_type_created` as the only usable access path,
    # which is what production's much larger, much less BRIN-friendly table also chooses.
    db_session.execute(text("set local enable_bitmapscan = off"))

    since = NOW - floor.FAIR_WINDOW
    plan = db_session.execute(
        text("explain (format json) " + floor._FAIR_FOR_ORDERS.text),
        {"since": since, "market_ids": [market.id]},
    ).scalar()

    fair_nodes = _plan_nodes(plan, relation="fair_values")
    assert fair_nodes, plan
    node = fair_nodes[0]
    assert node.get("Index Name") == "ix_fair_game_type_created", plan
    index_cond = node.get("Index Cond", "")
    filt = node.get("Filter", "")
    assert "outcome_team_id" not in index_cond, plan
    assert "outcome_side" not in index_cond, plan
    assert "threshold" not in index_cond, plan
    assert "outcome_team_id" in filt, plan
    assert "outcome_side" in filt, plan
    assert "threshold" in filt, plan
