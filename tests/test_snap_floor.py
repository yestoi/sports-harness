"""The Floor builder: the funnel's sourcing, the game board, the venue tile, and the
never-shown list."""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from harness.dashboard.snapshots import FLOOR_P95_BUDGET_MS, floor
from tests.test_snap_bounds import _statements, _tables
from harness.dashboard.snapshots.floor import FLOOR_KEYS, QUEUE_HISTORY_LIMIT, build_floor
from harness.db.models import (EquitySnapshot, FairValue, Fill, Game, GameScoreEvent,
                               GapOutcome, Intent, Ledger, MetricSample, OperatorEvent, Order,
                               OrderEvent, OrderWatchSample, ParlayCard, ParlayLeg, Run, Signal,
                               StrategyVariant, Team, VenueMarket, VenueRequest, VenueStatus)
from harness.pricing.fair import compute_fair_values
from tests.test_fair import NOW as FAIR_NOW
from tests.test_fair import _seed as _seed_priced_game

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
#: NOW is 13:00 in America/Chicago, so the local day opened at 05:00 UTC on the same date.
LOCAL_DAY_START = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)


def test_the_funnel_reads_runs_notes_and_the_only_signals_read_is_the_details_bounded_one():
    """Ruling A-C1: the indexes on market_gap_snapshots and signals lead on market and variant,
    not time, so a bare 6 h `created_at` predicate is a sequential scan -- 86-92 s measured, and
    permanently `{"error": ...}` under a 2000 ms timeout. Ticks, gaps and signal counts come
    from `runs.notes`, as the legacy funnel's have since fix 15.

    Task 13 narrows this rule rather than loosening it. `signals` may now be read exactly once,
    by `_DETAIL_SIGNALS`, and only the way the index it rides allows: a `venue_market_id` list
    from the detail set, a `created_at` window inside it, and the aggregation done in SQL (D12).
    That is a bounded range per market on `ix_signal_market_created`, which is the thing the
    funnel cannot have because the funnel has no market list. `market_gap_snapshots` stays
    unread: no index on it leads anywhere this builder can go.
    """
    body = Path(floor.__file__).read_text().lower()
    assert "from market_gap_snapshots" not in body
    # Over the extracted statements, not over the raw file: `from  signals` with two spaces, or a
    # newline between the words, would evade a text count and is the same read (review round 1,
    # M2). `_statements` is the scanner `tests/test_snap_bounds.py` already trusts for this file.
    reads = [(name, sql) for name, sql in _statements(floor)
             if "signals" in _tables(sql)]
    assert [name for name, _ in reads] == ["_DETAIL_SIGNALS"]
    summary = reads[0][1].lower()
    assert "venue_market_id = any(:market_ids)" in summary
    assert "created_at >= :since" in summary
    assert "group by venue_market_id, side" in summary
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


# --- carried fix 56: a settled fill leaves Floor's exposure -------------------------------------


def _cancelled_after_partial_fill(session, *, status="cancelled", settled=False):
    """Order 157's shape on the exposure lane: a `queue_model` fill inside the window, on an
    order the executor cancelled. Settlement pays the fill with a ledger `settlement` row and
    leaves the order `cancelled`, so `open_stake`/`mtm_open` carried it forever."""
    from harness.db.models import Ledger

    session.add(StrategyVariant(variant_id="capped", name="constrained_t", tier="secondary",
                                config_json={}, registered_at=NOW, active=True))
    session.add(EquitySnapshot(ts=NOW - timedelta(minutes=5), variant_id="capped",
                               cash=Decimal(3000), open_stake=Decimal("0.00"),
                               n_open_positions=1, n_open_orders=0))
    game = _game_with_market(session)
    order = _open_order(session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                        variant_id="capped", status=status, game_id=game.id)
    fill = Fill(order_id=order.id, prob=Decimal("0.4500"), contracts=Decimal("38.92"),
                fee=Decimal("0.0400"), filled_at=NOW - timedelta(hours=1),
                fill_method="queue_model", tape_source="ws", replay=False)
    session.add(fill)
    session.flush()
    if settled:
        session.add(Ledger(ts=NOW, variant_id="capped", kind="settlement", order_id=order.id,
                           fill_id=fill.id, ticker=order.ticker, side="yes",
                           contracts=fill.contracts, price=fill.prob, payout=Decimal("0.00"),
                           cash_delta=Decimal("0.00"), replay=False))
    session.flush()
    return order


def test_a_cancelled_orders_settled_fill_leaves_the_exposure_lane(db_session, env_settings):
    """(a) The lane's `open_contracts` is what the equity snapshot's `open_stake` and `mtm_open`
    are built from; a paid fill holds nothing."""
    _cancelled_after_partial_fill(db_session, settled=True)

    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["open_contracts"] == pytest.approx(0.0)


def test_a_cancelled_order_whose_fill_is_unpaid_stays_on_the_exposure_lane(db_session,
                                                                          env_settings):
    """(b) Until the settle job pays it, the position is open however the order ended."""
    _cancelled_after_partial_fill(db_session, settled=False)

    lane = build_floor(db_session, NOW, env_settings)["exposure"]["lanes"][0]
    assert lane["open_contracts"] == pytest.approx(38.92)


def test_a_settled_order_still_leaves_the_exposure_lane(db_session, env_settings):
    """(c) The existing status path, unchanged."""
    _cancelled_after_partial_fill(db_session, status="settled", settled=False)

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


def test_the_funnel_labels_each_count_with_its_own_unit(db_session, env_settings):
    """Addendum 0.11 and design review I6: a distinct-opportunity or distinct-episode count
    needs the `signals`/`intents` queries fix 31 removed, so this surface reports the counts it
    actually has and says what they are. One candidate that becomes two intent verdicts and one
    placement with a `queue_model` fill: four numbers, four units, no addition of unlike things.
    """
    db_session.add(Run(started_at=NOW - timedelta(hours=1), status="ok", build_sha="abc",
                       notes={"pricing": {"gaps": 9, "signals": {"sharp_direct":
                                                                 {"candidate": 1,
                                                                  "rejected": 0}}}}))
    _exec_metric(db_session, "exec.placed", 1)
    _exec_metric(db_session, "exec.skipped", 1, reason="kickoff")
    game = _game_with_market(db_session)
    actual = _open_order(db_session, venue_market_id=game.market_id, prob=Decimal("0.4500"),
                         game_id=game.id)
    db_session.add(Fill(order_id=actual.id, prob=Decimal("0.4500"), contracts=Decimal("5.00"),
                        fee=Decimal("0.0200"), filled_at=NOW - timedelta(hours=1),
                        fill_method="queue_model", through=False, tape_source="ws",
                        has_print=True, replay=False))
    counterfactual = _open_order(db_session, venue_market_id=game.market_id,
                                 prob=Decimal("0.4500"), game_id=game.id)
    db_session.add(Fill(order_id=counterfactual.id, prob=Decimal("0.4500"),
                        contracts=Decimal("5.00"), fee=Decimal("0.0200"),
                        filled_at=NOW - timedelta(hours=1), fill_method="no_watcher",
                        through=False, tape_source="ws", has_print=False, replay=False))
    db_session.flush()

    funnel = build_floor(db_session, NOW, env_settings)["funnel"]
    assert funnel["candidate_signals"] == 1
    assert funnel["intent_verdicts"] == 2
    assert funnel["placements"] == 1
    assert funnel["orders_filled_actual"] == 1
    assert funnel["orders_filled_counterfactual"] == 1
    assert funnel["fill_rows"] == {"queue_model": 1, "no_watcher": 1}
    # Every new count names its unit, and the label is honest about what it is not.
    assert "not distinct opportunities" in funnel["units"]["candidate_signals"]
    assert "not distinct episodes" in funnel["units"]["intent_verdicts"]
    assert set(funnel["units"]) >= {"candidate_signals", "intent_verdicts", "placements",
                                    "orders_filled_actual", "orders_filled_counterfactual",
                                    "fill_rows"}


def test_the_old_funnel_keys_stay_for_one_release(db_session, env_settings):
    """Addendum 0.11: the front end switches to the new keys, and the old ones travel beside
    them for one release so a cached page and a replayed payload both still render."""
    _exec_metric(db_session, "exec.placed", 3)
    db_session.flush()
    funnel = build_floor(db_session, NOW, env_settings)["funnel"]
    assert funnel["orders"] == funnel["placements"] == 3
    assert "intents" in funnel and "fills" in funnel and "candidates" in funnel


def test_the_exposure_section_states_its_own_coverage_limit(db_session, env_settings):
    """Addendum 0.12 and D7: fix 31's 14-day bound stays -- a complete aggregate over the whole
    `positions` view is the unbounded scan it removed -- and the figure is labelled rather than
    quietly presented as a total."""
    from harness.dashboard.snapshots.floor import EXPOSURE_WINDOW

    coverage = build_floor(db_session, NOW, env_settings)["exposure"]["coverage"]
    assert coverage["window_days"] == EXPOSURE_WINDOW.days == 14
    assert coverage["complete"] is False
    assert "not counted" in coverage["note"]


class _CountingJson:
    """`json` with a tape measure on `dumps`. `_cap_detail` reaches its serializer through the
    module global, so swapping this in counts every byte the cap spends."""

    def __init__(self, real):
        self._real = real
        self.calls = 0
        self.bytes = 0

    def dumps(self, obj, *args, **kwargs):
        out = self._real.dumps(obj, *args, **kwargs)
        self.calls += 1
        self.bytes += len(out)
        return out


def _synthetic_detail(rows: int) -> dict:
    """A detail payload with a `rows`-row story of realistic width, for the cap's own cost."""
    story = [{"ts": f"2026-09-12T{12 + i // 60:02d}:{i % 60:02d}:00+00:00", "kind": "fill",
              "text": "filled \u00b7 queue model",
              "facts": {"fill_id": 100000 + i, "order_id": 900 + i, "prob": 0.58,
                        "contracts": 40.0, "fee": 0.172, "fill_method": "queue_model",
                        "replay": False, "has_print": True, "through": False}}
             for i in range(rows)]
    return {"scoreline": {"home": "LSU Tigers", "away": "ALA Tigers", "text": "Q3 \u00b7 8:42"},
            "position": {"has_position": False, "text": "no position on this game", "rows": []},
            "story": story,
            "markets": [{"venue_market_id": i, "market_type": "moneyline", "fair_p": 0.62}
                        for i in range(12)],
            "on_your_ticket": None, "detail_available": True}


def test_the_payload_cap_is_linear_in_the_story_it_drops(monkeypatch):
    """Review round 1, I1. The first version re-serialized the whole payload once per dropped
    row: 11.2 ms for one game at `STORY_ROWS_PER_GAME`, 224 ms for a twenty-game Saturday, which
    is the whole of `FLOOR_P95_BUDGET_MS` spent before a statement runs. The bound asserted here
    is on **bytes serialized**, not on calls: the quadratic form makes fewer, much larger dumps,
    so counting calls would not tell the two apart."""
    detail = _synthetic_detail(floor.STORY_ROWS_PER_GAME)
    start = len(json.dumps(detail).encode())
    assert start > floor.DETAIL_KIB * 1024, "the fixture has to be over the cap to be a test"

    counter = _CountingJson(json)
    monkeypatch.setattr(floor, "json", counter)
    dropped = floor._cap_detail(detail)
    monkeypatch.undo()

    assert dropped > 0
    assert len(json.dumps(detail).encode()) <= floor.DETAIL_KIB * 1024
    # One dump in, one row each, one marker, one or two to verify: about two payloads. The
    # quadratic form spent 82 x 40 KB, forty times this bound.
    assert counter.bytes <= 4 * start, f"{counter.bytes} bytes serialized against {start}"


def test_capping_a_full_saturdays_details_stays_inside_the_floor_budget():
    """The same finding at the size that matters: `BOARD_LIMIT` is 60 and a college Saturday is
    about twenty games in the detail set. Forty details at `STORY_ROWS_PER_GAME` measured ~40 ms
    here; the shape this rejects measured 450 ms, against `FLOOR_P95_BUDGET_MS = 250` for the
    whole builder."""
    details = [_synthetic_detail(floor.STORY_ROWS_PER_GAME) for _ in range(40)]
    started = time.monotonic()
    for detail in details:
        floor._cap_detail(detail)
    elapsed_ms = (time.monotonic() - started) * 1000

    assert all(len(json.dumps(detail).encode()) <= floor.DETAIL_KIB * 1024 for detail in details)
    assert elapsed_ms < FLOOR_P95_BUDGET_MS, f"{elapsed_ms:.0f} ms for forty details"


def test_the_cap_never_drops_the_verdict_or_the_partial_row():
    """The two untimed rows at the head are the lines a truncated story still has to carry."""
    detail = _synthetic_detail(floor.STORY_ROWS_PER_GAME)
    detail["story"] = [{"ts": None, "kind": "not_evaluated", "text": "not evaluated", "facts": {}},
                       {"ts": None, "kind": "partial", "text": "partial", "facts": {}},
                       *detail["story"]]
    floor._cap_detail(detail)
    assert [row["kind"] for row in detail["story"][:3]] == [
        "not_evaluated", "partial", "truncated"]


# --- the game detail (addendum 7.2, design 4.2; Task 13) ---------------------------------------
#
# `T0` is the module's own instant and the four ids are fixed rather than sequence-assigned, so
# the tests below can name a game the way the brief's assertions do. The world is seeded by an
# autouse fixture on the class, not on the module: the eleven tests above build their own boards
# and a module-wide fixture would change every one of their answers.

T0 = NOW
#: In progress, LSU at home (`parlay.yaml`'s anchor), 46 matched markets, 300 signals.
IN_PROGRESS = 9101
#: Kicks off inside `DETAIL_WINDOW`; no signal ever touched it.
SOON = 9102
#: Kicks off in 20 h -- outside the window -- but carries orders, fills and a settlement.
WITH_ORDER = 9103
#: Kicks off in 20 h and carries nothing, so it is on the board with no detail.
FAR_AWAY = 9104

_DETAIL_TEAMS = ((101, "LSU"), (102, "ALA"), (103, "TEX"), (104, "OKL"),
                 (105, "UGA"), (106, "FLA"), (107, "MIA"), (108, "DUK"))


def _detail_teams(session):
    for team_id, abbr in _DETAIL_TEAMS:
        session.add(Team(sport="ncaaf", id=team_id, display_name=f"{abbr} Tigers",
                         location=abbr, name=abbr, abbreviation=abbr,
                         short_display_name=abbr))
    session.flush()


def _detail_game(session, game_id, home, away, kickoff, status="scheduled"):
    session.add(Game(id=game_id, sport="ncaaf", home_team_id=home, away_team_id=away,
                     kickoff_utc=kickoff, status=status))
    session.flush()
    return game_id


def _detail_market(session, game_id, n=0, match_status="matched", side_team_id=None):
    market = VenueMarket(venue="kalshi", ticker=f"KXNCAAF-{game_id}-{n}", event_ticker="E",
                         series_ticker="KXNCAAF", game_id=game_id, market_type="moneyline",
                         side_team_id=side_team_id,
                         match_status=match_status, first_seen_raw_id=1, last_seen_at=T0)
    session.add(market)
    session.flush()
    return market


def _detail_order(session, game_id, market_id, *, status, placed_at, gap_snapshot_id=None,
                  prob="0.5800", replay=False):
    order = Order(intent_id=uuid.uuid4(), variant_id="sharp_direct", venue="kalshi",
                  client_order_id=str(uuid.uuid4()), ticker=f"KXNCAAF-{game_id}-o",
                  venue_market_id=market_id, side="yes", prob=Decimal(prob),
                  contracts=Decimal("40.00"), status=status, game_id=game_id,
                  placed_at=placed_at, edge_at_place=Decimal("0.0300"), replay=replay,
                  queue_ahead_at_place=Decimal("120.00"), gap_snapshot_id=gap_snapshot_id)
    session.add(order)
    session.flush()
    return order


def _detail_fill(session, order, *, method, at, replay=False, contracts="40.00"):
    session.add(Fill(order_id=order.id, prob=order.prob, contracts=Decimal(contracts),
                     fee=Decimal("0.1720"), filled_at=at, fill_method=method, replay=replay,
                     has_print=True, through=False, tape_source="ws"))
    session.flush()


def _seed_detail_world(session):
    """Four games on one board: one in progress, one kicking off inside the detail window, one
    outside it that carries a position, and one outside it that carries nothing.

    The counts are the assertions' own: 46 matched markets and no resting order on the LSU game
    (`46 markets - 0 resting`), and 300 signals across two markets and two sides, which is four
    `evaluated` rows of 75 each and not 300 rows of one (D12).
    """
    _detail_teams(session)
    _detail_game(session, IN_PROGRESS, 101, 102, T0 - timedelta(hours=1), status="in_progress")
    _detail_game(session, SOON, 103, 104, T0 + timedelta(hours=2))
    _detail_game(session, WITH_ORDER, 105, 106, T0 + timedelta(hours=20))
    _detail_game(session, FAR_AWAY, 107, 108, T0 + timedelta(hours=20))

    session.add(GameScoreEvent(game_id=IN_PROGRESS, ts=T0 - timedelta(seconds=40),
                               status="in_progress", period=3, clock="8:42",
                               home_score=17, away_score=10))

    # 46 matched markets, and the two the executor was pricing.
    live_markets = [_detail_market(session, IN_PROGRESS, n) for n in range(46)]
    for market in live_markets[:2]:
        for side in ("yes", "no"):
            for i in range(75):
                session.add(Signal(
                    run_id=5000 + i, variant_id="sharp_direct", gap_snapshot_id=7000 + i,
                    venue_market_id=market.id, side=side, fair_p=Decimal("0.6200"),
                    fair_source="direct", venue_best_bid=Decimal("0.5600"),
                    venue_best_ask=Decimal("0.5800"), decision="rejected",
                    rejection_reason="edge", labels={}, replay=False,
                    created_at=T0 - timedelta(minutes=75 - i)))
    session.add(FairValue(run_id=1, game_id=IN_PROGRESS, market_type="moneyline",
                          fair_p=Decimal("0.6200"), fair_source="direct", staleness_s=30,
                          created_at=T0 - timedelta(minutes=2)))

    # The game inside the window: two intents three hours apart and no signal at all.
    soon_market = _detail_market(session, SOON, 0)
    for minutes in (240, 60):
        session.add(Intent(signal_id=8000 + minutes, variant_id="sharp_direct", venue="kalshi",
                           venue_market_id=soon_market.id, ticker=soon_market.ticker,
                           side="yes", target_prob=Decimal("0.5500"),
                           target_contracts=Decimal("20.00"), edge=Decimal("0.0400"),
                           game_id=SOON, signal_created_at=T0 - timedelta(minutes=minutes),
                           created_at=T0 - timedelta(minutes=minutes)))

    # The game with a position: one order still holding, one settled, one cancelled.
    held_market = _detail_market(session, WITH_ORDER, 0, side_team_id=105)
    held = _detail_order(session, WITH_ORDER, held_market.id, status="filled",
                         placed_at=T0 - timedelta(minutes=20), gap_snapshot_id=9500)
    _detail_fill(session, held, method="queue_model", at=T0 - timedelta(minutes=18))
    _detail_fill(session, held, method="no_watcher", at=T0 - timedelta(minutes=17))
    # The replayed fill belongs to a replay *order*: `_EXPOSURE` reads that flag on the order,
    # which is where the harness writes it, so a replay fill under a live order is a state that
    # cannot happen and a fixture that built one would be testing an impossible payload.
    replayed = _detail_order(session, WITH_ORDER, held_market.id, status="filled",
                             placed_at=T0 - timedelta(minutes=25), replay=True)
    _detail_fill(session, replayed, method="queue_model", at=T0 - timedelta(minutes=16),
                 replay=True)
    session.add(GapOutcome(gap_snapshot_id=9500, benchmark_type="kalshi_last_trade_pre_kick",
                           p_bench=Decimal("0.6400"), clv_target_p_net=Decimal("0.0500"),
                           p_used_kind="trade"))
    settled = _detail_order(session, WITH_ORDER, held_market.id, status="settled",
                            placed_at=T0 - timedelta(hours=3))
    _detail_fill(session, settled, method="queue_model", at=T0 - timedelta(hours=2))
    settled_fill = session.execute(
        text("select id from fills where order_id = :o order by id desc limit 1"),
        {"o": settled.id}).scalar()
    session.add(Ledger(ts=T0 - timedelta(minutes=30), variant_id="sharp_direct",
                       kind="settlement", order_id=settled.id, fill_id=settled_fill,
                       ticker=settled.ticker, side="yes", contracts=Decimal("40.00"),
                       price=Decimal("0.5800"), fee=Decimal("0.1720"),
                       payout=Decimal("40.00"), cash_delta=Decimal("16.80")))
    cancelled = _detail_order(session, WITH_ORDER, held_market.id, status="cancelled",
                              placed_at=T0 - timedelta(hours=5))
    session.add(OrderEvent(order_id=cancelled.id, ts=T0 - timedelta(hours=4), kind="cancel",
                           reason="venue_move", prob=Decimal("0.5800"),
                           contracts=Decimal("40.00")))

    # F02's one crossing: a placed card with a leg on that game.
    card = ParlayCard(year=2026, week=38, sport="ncaaf", kind="smart", built_at=T0,
                      stake=Decimal("10.00"), status="placed")
    session.add(card)
    session.flush()
    session.add(ParlayLeg(card_id=card.id, seq=1, game_id=WITH_ORDER, market_type="ml",
                          dk_american=-120, dk_decimal=Decimal("1.8300"),
                          plain_text="UGA to win", status="pending"))

    _detail_market(session, FAR_AWAY, 0)
    session.flush()
    return card.id


def _degraded_run_without_pricing_notes(session):
    """A run degraded by a Kalshi timeout. Its pricing stage ran and wrote nothing to complain
    about, so nothing here may put `evaluation failed` on a market (B-C7)."""
    session.add(Run(started_at=T0 - timedelta(minutes=10), status="degraded", build_sha="abc",
                    notes={"warnings": [{"kalshi_orderbook:KXNCAAF-1": "http 500"}],
                           "pricing": {"ticks": 12, "gaps": 40, "budget_exhausted": False}}))
    session.flush()


def _run_with_pricing_warning(session, sport="ncaaf"):
    """A run whose pricing stage itself raised -- `{"pricing": repr(exc)}` in `notes.warnings`,
    which is what `harness/recorder/tick.py` writes.

    `sport` names the game these tests then read. `runs.notes` carries no sport dimension for
    this evidence (one warnings list and one `budget_exhausted` flag per tick, covering every
    sport that tick priced), so the builder applies it to the window rather than to a sport; the
    argument is kept because the addendum asks the question in those terms and the writer is
    where an answer would have to start.
    """
    session.add(Run(started_at=T0 - timedelta(minutes=5), status="degraded", build_sha="abc",
                    notes={"warnings": [{"pricing": f"RuntimeError('{sport} pricing failed')"}],
                           "pricing": {}}))
    session.flush()


def _first_row_kind(session, settings, game_id):
    return build_floor(session, T0, settings)["details"][str(game_id)]["story"][0]["kind"]


class TestTheGameDetail:
    """Addendum 7.2 / design 4.2. The world above is seeded before each of these, and only these.

    The brief's assertions read `payload["board"]` as a list; this builder's board section is
    (and stays) `{"games": [...]}` -- the shape the eleven tests above and the phase 4.5 surface
    already use -- so the board assertions read `payload["board"]["games"]`. Nothing else about
    them changes.
    """

    @pytest.fixture(autouse=True)
    def _world(self, db_session):
        _seed_detail_world(db_session)

    def test_a_board_card_carries_one_figures_row_and_a_favourite_star(self, db_session,
                                                                       env_settings):
        payload = build_floor(db_session, T0, env_settings)
        board = payload["board"]["games"]
        card = board[0]
        assert card["figures"] == "46 markets \u00b7 0 resting"
        assert board[0]["favourite"] is True        # the LSU game sorts first
        assert [c["favourite"] for c in board] == sorted(
            [c["favourite"] for c in board], reverse=True)
        assert card["abbreviations"] == {"home": "LSU", "away": "ALA"}

    def test_the_detail_set_is_in_progress_or_within_six_hours_or_carrying_a_position(
            self, db_session, env_settings):
        payload = build_floor(db_session, T0, env_settings)
        assert set(payload["details"]) == {str(IN_PROGRESS), str(SOON), str(WITH_ORDER)}
        far = next(c for c in payload["board"]["games"] if c["game_id"] == FAR_AWAY)
        assert far["detail_available"] is False

    def test_each_detail_payload_stays_under_sixteen_kibibytes(self, db_session, env_settings):
        payload = build_floor(db_session, T0, env_settings)
        for game_id, detail in payload["details"].items():
            assert len(json.dumps(detail).encode()) <= 16 * 1024, game_id
        assert payload["readings"]["detail_bytes"] == sum(
            len(json.dumps(detail).encode()) for detail in payload["details"].values())
        assert payload["readings"]["detail_games"] == 3

    def test_the_decision_story_summarizes_signals_in_sql_to_one_row_per_market_and_side(
            self, db_session, env_settings):
        """D12: one `evaluated` row per market and side, never one per tick. Computed
        independently: the fixture writes 300 signals across two markets and two sides, so the
        story carries four evaluated rows and each names its count and its span."""
        story = build_floor(db_session, T0, env_settings)["details"][str(IN_PROGRESS)]["story"]
        evaluated = [r for r in story if r["kind"] == "evaluated"]
        assert len(evaluated) == 4
        assert evaluated[0]["text"].startswith("75 evaluations from ")
        # The span and the last figures, in the reader's own zone (T0 is 13:00 in Chicago), and
        # each side read against its own half of the book: the ask is what a YES pays.
        for row in evaluated:
            book = "58\u00a2" if row["facts"]["side"] == "yes" else "56\u00a2"
            assert row["text"] == (
                "75 evaluations from 11:45 AM to 12:59 PM \u00b7 "
                f"last: fair 62\u00a2 against {book} on the book \u00b7 not enough edge")
            assert row["facts"]["evaluations"] == 75

    def test_the_first_row_is_not_evaluated_when_no_signal_exists_in_the_window(self, db_session,
                                                                               env_settings):
        story = build_floor(db_session, T0, env_settings)["details"][str(SOON)]["story"]
        assert story[0]["kind"] == "not_evaluated"

    def test_evaluation_failed_comes_only_from_the_run_s_own_pricing_notes(self, db_session,
                                                                          env_settings):
        """B-C7/D12: never from `runs.status = 'degraded'` -- a run degraded by a Kalshi timeout
        evaluated this market perfectly well."""
        _degraded_run_without_pricing_notes(db_session)
        assert _first_row_kind(db_session, env_settings, SOON) == "not_evaluated"
        _run_with_pricing_warning(db_session, sport="ncaaf")
        assert _first_row_kind(db_session, env_settings, SOON) == "evaluation_failed"

    def test_a_market_that_was_evaluated_is_never_called_a_failure(self, db_session,
                                                                   env_settings):
        """The precedence the three first rows need: a pricing warning in the window says the
        step failed *somewhere*, and a market with 300 signals on it is not that somewhere."""
        _run_with_pricing_warning(db_session)
        assert _first_row_kind(db_session, env_settings, IN_PROGRESS) == "evaluated"

    def test_a_gap_longer_than_thirty_minutes_before_kickoff_is_its_own_row(self, db_session,
                                                                           env_settings):
        story = build_floor(db_session, T0, env_settings)["details"][str(SOON)]["story"]
        assert any(r["kind"] == "gap" for r in story)
        assert any(r["text"].endswith("and 12:00 PM") for r in story if r["kind"] == "gap")

    def test_a_fill_is_labelled_by_its_recorded_method_and_replay_flag(self, db_session,
                                                                      env_settings):
        story = build_floor(db_session, T0, env_settings)["details"][str(WITH_ORDER)]["story"]
        labels = {r["text"] for r in story if r["kind"] == "fill"}
        assert labels <= {"filled \u00b7 queue model", "filled \u00b7 no watcher", "replayed"}
        assert labels == {"filled \u00b7 queue model", "filled \u00b7 no watcher", "replayed"}

    def test_on_your_ticket_carries_no_amount_and_the_payload_no_fun_money_key(self, db_session,
                                                                              env_settings):
        """F02, ruling A-I15. The `$` assertion is scoped to `on_your_ticket`; the key assertion
        covers the whole payload, because a fun-money number could arrive under any name."""
        payload = build_floor(db_session, T0, env_settings)
        ticket = payload["details"][str(WITH_ORDER)]["on_your_ticket"]
        assert set(ticket) == {"card_id", "href"} and "$" not in json.dumps(ticket)
        assert ticket["href"] == f"#ticket/card/{ticket['card_id']}"
        blob = json.dumps(payload)
        for forbidden in ('"stake"', '"payout"', '"parlay_', '"dk_american"', '"dk_payout'):
            assert forbidden not in blob

    def test_a_game_with_no_live_card_has_no_ticket_line(self, db_session, env_settings):
        payload = build_floor(db_session, T0, env_settings)
        assert payload["details"][str(SOON)]["on_your_ticket"] is None

    def test_the_position_is_the_open_money_fills_and_says_what_it_needs(self, db_session,
                                                                        env_settings):
        """The `_EXPOSURE` shape: `queue_model` and `venue` fills only, the settled order's own
        fill excluded by its ledger row, and never a word implying in-play trading."""
        detail = build_floor(db_session, T0, env_settings)["details"][str(WITH_ORDER)]
        position = detail["position"]
        assert position["has_position"] is True
        assert [row["contracts"] for row in position["rows"]] == [40.0]
        assert position["rows"][0]["needs"] == "needs UGA to win"
        assert position["rows"][0]["entry_p"] == pytest.approx(0.58)
        assert position["posture"] == "placed \u00b7 waiting for kickoff"
        assert "in-play" not in json.dumps(position) and "hedge" not in json.dumps(position)

    def test_a_game_with_no_position_says_so_with_the_story_s_reason(self, db_session,
                                                                     env_settings):
        position = build_floor(db_session, T0, env_settings)["details"][str(SOON)]["position"]
        assert position["has_position"] is False
        assert position["text"] == "no position on this game"
        assert position["reason"].startswith("not evaluated \u00b7 ")

    def test_the_settlement_row_is_paper_dollars(self, db_session, env_settings):
        story = build_floor(db_session, T0, env_settings)["details"][str(WITH_ORDER)]["story"]
        settled = [row for row in story if row["kind"] == "settled"]
        assert [row["text"] for row in settled] == ["settled \u00b7 +$16.80 paper"]

    def test_the_closing_benchmark_rides_the_orders_own_gap_snapshot(self, db_session,
                                                                     env_settings):
        """`ix_gap_outcomes_order` does not exist and cannot: `gap_outcomes` has no `order_id`.
        The join is `orders.gap_snapshot_id` into that table's primary key."""
        story = build_floor(db_session, T0, env_settings)["details"][str(WITH_ORDER)]["story"]
        benchmarks = [row for row in story if row["kind"] == "benchmark"]
        assert [row["text"] for row in benchmarks] == [
            "closing 64\u00a2 \u00b7 we entered at 58\u00a2"]

    def test_the_scoreline_is_the_sources_own_state(self, db_session, env_settings):
        scoreline = build_floor(db_session, T0, env_settings)["details"][str(IN_PROGRESS)][
            "scoreline"]
        assert scoreline["home_score"] == 17 and scoreline["away_score"] == 10
        assert scoreline["clock"] == "8:42" and scoreline["period"] == 3
        assert scoreline["text"] == "Q3 \u00b7 8:42 \u00b7 40 s ago"

    def test_the_markets_table_carries_the_newest_fair_for_the_exact_contract(self, db_session,
                                                                             env_settings):
        markets = build_floor(db_session, T0, env_settings)["details"][str(IN_PROGRESS)][
            "markets"]
        assert len(markets) <= floor.DETAIL_MARKETS_PER_GAME
        assert markets and all(row["fair_p"] == pytest.approx(0.62) for row in markets)
        # No open order on this game, so there is no book and no live edge to claim.
        assert all(row["best_bid"] is None and row["edge_live"] is None for row in markets)

    def test_a_story_longer_than_the_cap_drops_its_oldest_rows_first(self, db_session,
                                                                    env_settings):
        """The cap is load-bearing: a detail is served down a tunnel to a phone. What survives
        is the part of the story that is still happening, and the truncation is a row a reader
        can see rather than a story that quietly starts late.

        Four markets at the per-market read cap, so this case holds both lines at once: the read
        cap binds and says so (`partial`), and the payload cap then drops the oldest of what was
        read and says that too (`truncated`).
        """
        markets = [_detail_market(db_session, SOON, n) for n in range(1, 4)]
        markets.append(db_session.execute(
            text("select id, ticker from venue_markets where game_id = :g order by id limit 1"),
            {"g": SOON}).one())
        seq = 0
        for market in markets:
            for minute in range(1, floor.STORY_ROWS_PER_MARKET + 1):
                seq += 1
                db_session.add(Intent(signal_id=20000 + seq, variant_id="sharp_direct",
                                      venue="kalshi", venue_market_id=market.id, ticker="KX",
                                      side="yes", target_prob=Decimal("0.5500"),
                                      target_contracts=Decimal("20.00"), edge=Decimal("0.0400"),
                                      game_id=SOON,
                                      signal_created_at=T0 - timedelta(minutes=minute),
                                      created_at=T0 - timedelta(minutes=minute)))
        db_session.flush()

        payload = build_floor(db_session, T0, env_settings)
        detail = payload["details"][str(SOON)]
        assert len(json.dumps(detail).encode()) <= floor.DETAIL_KIB * 1024
        story = detail["story"]
        assert [row["kind"] for row in story[:3]] == ["not_evaluated", "partial", "truncated"]
        assert story[2]["facts"]["dropped"] > 0
        assert payload["readings"]["detail_partial_games"] == 1
        # The newest intent is the one that survived; the oldest went.
        assert story[-1]["ts"] == (T0 - timedelta(minutes=1)).isoformat()

    def test_a_read_cap_that_binds_never_starves_a_later_game_of_the_set(self, db_session,
                                                                        env_settings):
        """Review round 1, I2. The caps are per game, per market and per order, so a game that
        fills its own cap costs itself rows and no other game one: the set-wide `limit` this
        replaced handed the first games every row and the last games none, and a game with no
        markets reads as `not evaluated` about a game that was evaluated all morning."""
        market = db_session.execute(
            text("select id from venue_markets where game_id = :g order by id limit 1"),
            {"g": SOON}).scalar()
        for minute in range(1, floor.STORY_ROWS_PER_MARKET + 20):
            db_session.add(Intent(signal_id=30000 + minute, variant_id="sharp_direct",
                                  venue="kalshi", venue_market_id=market, ticker="KX",
                                  side="yes", target_prob=Decimal("0.5500"),
                                  target_contracts=Decimal("20.00"), edge=Decimal("0.0400"),
                                  game_id=SOON,
                                  signal_created_at=T0 - timedelta(minutes=minute),
                                  created_at=T0 - timedelta(minutes=minute)))
        db_session.flush()

        payload = build_floor(db_session, T0, env_settings)
        # The game that filled its cap reads exactly its cap and says it is partial ...
        soon = payload["details"][str(SOON)]
        assert sum(1 for row in soon["story"] if row["kind"] == "intent") == \
            floor.STORY_ROWS_PER_MARKET
        assert any(row["kind"] == "partial" for row in soon["story"])
        # ... and the other two games in the set are untouched by it.
        in_progress = payload["details"][str(IN_PROGRESS)]
        assert len([row for row in in_progress["story"] if row["kind"] == "evaluated"]) == 4
        assert not any(row["kind"] == "partial" for row in in_progress["story"])
        with_order = payload["details"][str(WITH_ORDER)]
        assert with_order["position"]["has_position"] is True
        assert not any(row["kind"] == "partial" for row in with_order["story"])

    def test_a_settled_fill_is_never_counted_as_an_open_position(self, db_session, env_settings):
        """Review round 1, I2, the correctness half: `settled_fills` comes from `_DETAIL_LEDGER`,
        so a settlement row that a set-wide cap dropped would turn a closed position back into
        contracts the detail claims we are holding. The ledger cap is per order and larger than
        the per-order fills cap, so the settled order's fill stays recognised as settled."""
        position = build_floor(db_session, T0, env_settings)["details"][str(WITH_ORDER)][
            "position"]
        assert [row["order_id"] for row in position["rows"]] == [
            db_session.execute(text(
                "select id from orders where game_id = :g and status = 'filled' "
                "and replay = false order by id limit 1"), {"g": WITH_ORDER}).scalar()]
        assert position["settled_cash"] == pytest.approx(16.8)
