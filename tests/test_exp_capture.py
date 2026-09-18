"""§1.3: what the replay clock is, and what the capture may not see."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness.experiments.execution_viability.capture import (
    CAPTURE_STREAMS, LIMITATION_KINDS, TIMESTAMP_SEMANTICS, kickoffs_asof, live_loop_estimate,
    resolve_instants,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
START, END = NOW - timedelta(hours=1), NOW + timedelta(hours=1)
VARIANT = "c0ffee123456"


def _market(session, *, ticker="KXNFLGAME-1", game_id=None):
    """One `venue_markets` row: every decision row below carries its id."""
    from harness.db.models import VenueMarket

    market = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME",
                         series_ticker="KXNFLGAME", game_id=game_id, market_type="moneyline",
                         match_status="confident", first_seen_raw_id=1, last_seen_at=NOW)
    session.add(market)
    session.flush()
    return market


def _seed_game(session, *, kickoff, sport="nfl", espn_event_id="4017"):
    from harness.db.models import Game

    game = Game(sport=sport, home_team_id=1, away_team_id=2, kickoff_utc=kickoff,
                espn_event_id=espn_event_id, status="scheduled")
    session.add(game)
    session.flush()
    return game.id


def _seed_intent(session, *, game_id, created_at, kickoff_utc, market=None, signal_id=None):
    from harness.db.models import Intent

    market = market or _market(session, game_id=game_id)
    intent = Intent(signal_id=signal_id if signal_id is not None else int(created_at.timestamp()),
                    variant_id=VARIANT, venue="kalshi", venue_market_id=market.id,
                    ticker=market.ticker, side="yes", target_prob=Decimal("0.4800"),
                    target_contracts=Decimal("20"), edge=Decimal("0.0300"),
                    edge_min=Decimal("0.0100"), fair_p=Decimal("0.5100"), game_id=game_id,
                    kickoff_utc=kickoff_utc, signal_created_at=created_at, created_at=created_at,
                    replay=False)
    session.add(intent)
    session.flush()
    return intent


def _seed_actions(session, *, duplicate_at=None, outside_at=None):
    """One order placed at 12:00:03, one order_event at 12:00:19, one intent at 11:59:58, one
    fill at 12:04:41 and two `exec.loop_ms` samples at 12:00:00 and 12:01:14."""
    from harness.db.models import Fill, MetricSample, Order, OrderEvent

    game_id = _seed_game(session, kickoff=datetime(2026, 9, 16, 17, 0, tzinfo=timezone.utc))
    market = _market(session, game_id=game_id)
    intent = _seed_intent(session, game_id=game_id,
                          created_at=datetime(2026, 9, 16, 11, 59, 58, tzinfo=timezone.utc),
                          kickoff_utc=datetime(2026, 9, 16, 17, 0, tzinfo=timezone.utc),
                          market=market, signal_id=1)
    order = Order(intent_id=intent.id, variant_id=VARIANT, venue="kalshi",
                  client_order_id="prod-1", ticker=market.ticker, venue_market_id=market.id,
                  side="yes", prob=Decimal("0.4800"), contracts=Decimal("20"), status="open",
                  placed_at=datetime(2026, 9, 16, 12, 0, 3, tzinfo=timezone.utc),
                  expiry=datetime(2026, 9, 16, 12, 3, 43, tzinfo=timezone.utc),
                  game_id=game_id, sport="nfl", replay=False)
    session.add(order)
    session.flush()
    session.add(OrderEvent(order_id=order.id,
                           ts=datetime(2026, 9, 16, 12, 0, 19, tzinfo=timezone.utc),
                           kind="cancel", prob=order.prob, contracts=order.contracts,
                           replay=False))
    session.add(Fill(order_id=order.id, prob=Decimal("0.4800"), contracts=Decimal("5"),
                     fee=Decimal("0.0500"),
                     filled_at=datetime(2026, 9, 16, 12, 4, 41, tzinfo=timezone.utc),
                     fill_method="queue_model", replay=False))
    for ts, value in ((datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc), 24_165),
                      (datetime(2026, 9, 16, 12, 1, 14, tzinfo=timezone.utc), 16_759)):
        session.add(MetricSample(ts=ts, source="exec", name="exec.loop_ms", labels={},
                                 value=Decimal(value)))
    if duplicate_at is not None:
        # A second retained stamp on the same instant: a fill of the same order.
        session.add(Fill(order_id=order.id, prob=Decimal("0.4800"), contracts=Decimal("1"),
                         fee=Decimal("0.0100"), filled_at=duplicate_at,
                         fill_method="queue_model", source_trade_id="dup-1", replay=False))
        session.add(MetricSample(ts=duplicate_at, source="exec", name="exec.loop_ms", labels={},
                                 value=Decimal(15_000)))
    if outside_at is not None:
        session.add(Fill(order_id=order.id, prob=Decimal("0.4800"), contracts=Decimal("1"),
                         fee=Decimal("0.0100"), filled_at=outside_at, fill_method="queue_model",
                         source_trade_id="outside-1", replay=False))
        session.add(MetricSample(ts=outside_at, source="exec", name="exec.loop_ms", labels={},
                                 value=Decimal(15_000)))
    session.commit()


def test_resolve_instants_is_the_union_of_the_four_action_stamps_and_the_samples(db_session):
    # Rows written by hand: one order placed at 12:00:03, one order_event at 12:00:19, one intent
    # at 11:59:58, one fill at 12:04:41, and two exec.loop_ms samples at 12:00:00 and 12:01:14.
    # The expected clock is those six instants, sorted and deduplicated - written out here, not
    # read back from the implementation.
    _seed_actions(db_session)
    got = resolve_instants(db_session, warmup_start=START, observation_end=END,
                           variant_ids=[VARIANT])
    assert got == (
        datetime(2026, 9, 16, 11, 59, 58, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 12, 0, 3, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 12, 0, 19, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 12, 1, 14, tzinfo=timezone.utc),
        datetime(2026, 9, 16, 12, 4, 41, tzinfo=timezone.utc),
    )


def test_a_duplicate_stamp_appears_once(db_session):
    _seed_actions(db_session, duplicate_at=datetime(2026, 9, 16, 12, 0, 3, tzinfo=timezone.utc))
    got = resolve_instants(db_session, warmup_start=START, observation_end=END,
                           variant_ids=[VARIANT])
    assert len(got) == len(set(got))


def test_instants_outside_the_slice_are_not_in_the_clock(db_session):
    _seed_actions(db_session, outside_at=END + timedelta(minutes=5))
    got = resolve_instants(db_session, warmup_start=START, observation_end=END,
                           variant_ids=[VARIANT])
    assert all(START <= i <= END for i in got)


def test_the_sample_ts_is_the_decision_instant_not_a_completion_stamp():
    # I1: `_locked_step` takes `now` before the body and `_write_metric_batch` records
    # `ts=now`, so a 24,165 ms sample (the live p95; the p50 is 16,759 ms) at 12:00:00 is a
    # step that *began* at 12:00:00.
    from harness.experiments.execution_viability.capture import sample_instant

    assert sample_instant(datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc), 24_165) == \
        datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    assert sample_instant(datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc), 24_165,
                          sensitivity=True) == \
        datetime(2026, 9, 16, 11, 59, 35, 835000, tzinfo=timezone.utc)


def test_the_live_loop_estimate_is_printed_beside_the_resolved_count():
    # C1's arithmetic: 1,158 samples over 24 h is one per 74.6 s; at the 24.165 s **p95** the
    # stepped about 3,576 times. The estimate is a *number beside* the clock, never the clock.
    assert live_loop_estimate(1158, 86_400, 24_165) == 3575


def test_a_run_records_the_loop_spacing_limitation(db_session):
    from harness.experiments.execution_viability.capture import spacing_limitation

    lim = spacing_limitation("run-1", warmup_start=START, observation_end=END, instants=6,
                             live_estimate=3575, now=NOW)
    assert lim.kind == "loop_spacing_unreconstructable"
    assert lim.scope["instants"] == 6 and lim.scope["live_loop_estimate"] == 3575
    assert lim.kind in LIMITATION_KINDS


def test_a_kickoff_revised_after_the_decision_never_reaches_the_cadence(db_session):
    # I6: `games.kickoff_utc` (models.py:108) is **overwritten in place** when a kickoff is
    # revised, so it is today's schedule, not the schedule a 12:00 decision saw. The as-of value
    # is the snapshot the executor already froze on the decision's own row --
    # `intents.kickoff_utc` (models.py:455), written at creation and never updated.
    game_id = _seed_game(db_session, kickoff=datetime(2026, 9, 20, 20, 15, tzinfo=timezone.utc))
    _seed_intent(db_session, game_id=game_id, created_at=NOW - timedelta(minutes=5),
                 kickoff_utc=datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc))
    db_session.commit()
    [kickoff] = kickoffs_asof(db_session, at=NOW, sport="nfl")
    assert kickoff.kickoff_utc == datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    assert kickoff.sport == "nfl"            # a `Kickoff` row, which is what interval_for takes


def test_an_unreconstructable_kickoff_is_labelled_not_guessed(db_session):
    from harness.experiments.execution_viability.capture import kickoff_limitation

    # A game inside the window that no intent and no order ever referenced has no frozen
    # snapshot, so there is nothing to reconstruct and nothing is invented.
    game_id = _seed_game(db_session, kickoff=datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc))
    db_session.commit()
    assert kickoffs_asof(db_session, at=NOW, sport="nfl") == []
    lim = kickoff_limitation("run-1", game_id=game_id, now=NOW)
    assert lim.kind == "kickoff_not_asof" and lim.scope["game_id"] == game_id


def test_availability_time_is_what_a_decision_may_read():
    # §1.3(b): a REST backfill taped at 12:05 is invisible to a 12:00 decision even though its
    # event time is 11:58.
    from harness.experiments.execution_viability.capture import visible_at

    row = {"event_ts": datetime(2026, 9, 16, 11, 58, tzinfo=timezone.utc),
           "available_at": datetime(2026, 9, 16, 12, 5, tzinfo=timezone.utc),
           "tape_source": "rest"}
    assert visible_at(row, NOW) is False
    assert visible_at(row, datetime(2026, 9, 16, 12, 6, tzinfo=timezone.utc)) is True


def test_every_stream_declares_its_timestamp_semantics():
    assert set(TIMESTAMP_SEMANTICS) == set(CAPTURE_STREAMS)
    assert len(CAPTURE_STREAMS) == 13


def test_the_capture_writes_one_hashed_ndjson_file_per_stream(db_session, env_settings, tmp_path):
    """The thirteen streams of §1.3(a), each bounded, each hashed into the manifest.

    An eleventh case beside the ten the plan lists: every stream's statement is executed here,
    so a column this package names and the schema does not is a failure in this suite rather
    than on the first real slice.
    """
    from harness.experiments.execution_viability.capture import capture_slice

    _seed_actions(db_session)
    settings = env_settings.model_copy(update={"exp_dir": tmp_path, "exp_batch_rows": 2})
    hashes, limitations = capture_slice(
        settings, db_session, run_id="run-1", warmup_start=START, observation_end=END,
        tickers=["KXNFLGAME-1"], variant_ids=[VARIANT])
    assert set(hashes) == set(CAPTURE_STREAMS)
    assert all(len(digest) == 64 for digest in hashes.values())
    written = sorted(p.name for p in (tmp_path / "run-1").glob("*.ndjson"))
    assert written == sorted(f"{stream}.ndjson" for stream in CAPTURE_STREAMS)
    # The window's own rows are in the files, and the empty streams are empty rather than absent.
    orders = (tmp_path / "run-1" / "orders.ndjson").read_text().splitlines()
    assert len(orders) == 1 and '"placed_at": "2026-09-16T12:00:03+00:00"' in orders[0]
    assert (tmp_path / "run-1" / "prints.ndjson").read_text() == ""
    assert [lim.kind for lim in limitations] == ["loop_spacing_unreconstructable"]


def test_a_capture_larger_than_its_ceiling_is_refused_before_the_first_write(db_session,
                                                                            env_settings,
                                                                            tmp_path):
    from harness.experiments.execution_viability.capture import CaptureRefused, capture_slice

    from harness.db.models import VenueTrade

    _seed_actions(db_session)
    # One print in the window, so the projection is a positive number of bytes rather than the
    # trivially-passing zero of an empty slice.
    db_session.add(VenueTrade(venue="kalshi", trade_id="t1", ticker="KXNFLGAME-1",
                              ts=NOW, yes_price=Decimal("0.4800"), count=Decimal("12"),
                              taker_side="no", source="ws"))
    db_session.commit()
    # A ceiling of 0 GB: the projection of any slice exceeds it, and the refusal must come
    # before the run directory is written into (§2's EXP_CAPTURE_MAX_GB).
    settings = env_settings.model_copy(update={"exp_dir": tmp_path, "exp_capture_max_gb": 0})
    with pytest.raises(CaptureRefused):
        capture_slice(settings, db_session, run_id="run-2", warmup_start=START,
                      observation_end=END, tickers=["KXNFLGAME-1"], variant_ids=[VARIANT])
    assert list(tmp_path.glob("run-2/*.ndjson")) == []
