"""§1.9(a): one outcome schedule for every arm, and what it does at a horizon it cannot see."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.db.models import VenueMarket, VenueQuote
from harness.experiments.execution_viability.outcomes import (HORIZONS, MISSING_REASONS,
                                                              record_outcomes)
from harness.experiments.execution_viability.storage import ExperimentWriter

RUN = "0198e2b0-0000-7000-8000-000000000001"
NOW = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)
FILLED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _order(oid, *, filled_at=FILLED_AT, prob="0.4800"):
    return {"id": oid, "ticker": "KXNFLGAME-26SEP20DETBAL-DET", "venue_market_id": 1,
            "side": "yes", "prob": Decimal(prob), "contracts": Decimal("10"),
            "filled_at": filled_at}


def seed_mids(session, *, venue_market_id, mids):
    """One `venue_quotes` row per instant, quoted a point either side of the mid."""
    for at, mid in mids.items():
        session.add(VenueQuote(raw_id=int(at.timestamp()), run_id=int(at.timestamp()),
                               venue_market_id=venue_market_id,
                               yes_bid=mid - Decimal("0.0100"), yes_ask=mid + Decimal("0.0100"),
                               no_bid=Decimal("1") - (mid + Decimal("0.0100")),
                               no_ask=Decimal("1") - (mid - Decimal("0.0100")),
                               fetched_at=at))
    session.flush()
    session.commit()


def writer_for(session, settings):
    """T1's writer on the test session: `ExperimentWriter.open` connects as §1.1(b)'s role,
    which no test may need; the writer object itself is the thing under test here."""
    return ExperimentWriter(session, run_id=RUN, batch_rows=settings.exp_batch_rows)


def test_a_matured_horizon_is_computed_from_the_mid_at_that_horizon(db_session, env_settings):
    seed_mids(db_session, venue_market_id=1,
              mids={FILLED_AT: Decimal("0.4800"),
                    FILLED_AT + timedelta(seconds=1800): Decimal("0.5100")})
    rows = record_outcomes(db_session, writer_for(db_session, env_settings), run_id=RUN,
                           arm_id="A", orders=[_order(1)], now=NOW)
    by_horizon = {r["horizon"]: r for r in rows}
    assert set(by_horizon) == set(HORIZONS)
    assert by_horizon["1800"]["value"] == Decimal("0.0300")      # 0.5100 - 0.4800, YES space
    assert by_horizon["1800"]["status"] == "matured"
    assert by_horizon["1800"]["missing_reason"] is None
    assert by_horizon["1800"]["source_age_s"] is not None        # §1.9(d) travels with the number


def test_a_horizon_that_has_not_arrived_yet_is_censored_not_zero(db_session, env_settings):
    # An order filled 20 minutes ago cannot have a 30-minute markout at `now`.
    fresh = NOW - timedelta(minutes=20)
    seed_mids(db_session, venue_market_id=1, mids={fresh: Decimal("0.4800")})
    rows = record_outcomes(db_session, writer_for(db_session, env_settings), run_id=RUN,
                           arm_id="A", orders=[_order(2, filled_at=fresh)], now=NOW)
    censored = [r for r in rows if r["horizon"] == "1800"][0]
    assert censored["status"] == "censored"
    assert censored["value"] is None                             # never 0, never carried forward
    assert censored["matures_at"] == fresh + timedelta(seconds=1800)


def test_a_horizon_with_no_mid_is_missing_with_a_named_reason(db_session, env_settings):
    seed_mids(db_session, venue_market_id=1, mids={FILLED_AT: Decimal("0.4800")})
    rows = record_outcomes(db_session, writer_for(db_session, env_settings), run_id=RUN,
                           arm_id="A", orders=[_order(3)], now=NOW)
    missing = [r for r in rows if r["horizon"] == "1800"][0]
    assert missing["status"] == "missing"
    assert missing["value"] is None
    assert missing["missing_reason"] == "no_mid_at_horizon"
    assert missing["missing_reason"] in MISSING_REASONS


def test_every_arm_uses_the_same_schedule(db_session, env_settings):
    seed_mids(db_session, venue_market_id=1,
              mids={FILLED_AT: Decimal("0.4800"),
                    FILLED_AT + timedelta(seconds=1800): Decimal("0.5100")})
    writer = writer_for(db_session, env_settings)
    for arm_id in ("A", "B", "C"):
        rows = record_outcomes(db_session, writer, run_id=RUN, arm_id=arm_id,
                               orders=[_order(4)], now=NOW)
        assert [r["horizon"] for r in rows] == list(HORIZONS)
        assert {r["arm_id"] for r in rows} == {arm_id}


def test_the_rows_are_written_through_the_writer_and_read_back_as_exp_outcome(db_session,
                                                                              env_settings):
    # The observer writes `exp_outcome` and nothing else: `report.py` reads these rows rather
    # than computing a markout of its own (ruling C2).
    seed_mids(db_session, venue_market_id=1,
              mids={FILLED_AT: Decimal("0.4800"),
                    FILLED_AT + timedelta(seconds=1800): Decimal("0.5100")})
    writer = writer_for(db_session, env_settings)
    record_outcomes(db_session, writer, run_id=RUN, arm_id="A", orders=[_order(5)], now=NOW)
    writer.commit()
    got = db_session.execute(text(
        "select horizon, value, censored, missing_reason, source_age_s from exp_outcome "
        "where run_id = :r and arm_id = 'A' order by horizon"), {"r": RUN}).all()
    assert [row.horizon for row in got] == ["1800", "close", "t0"]
    matured = [row for row in got if row.horizon == "1800"][0]
    assert matured.value == Decimal("0.030000") and matured.censored is False
    assert matured.missing_reason is None and matured.source_age_s == 0


def test_an_order_that_never_filled_has_no_outcome(db_session, env_settings):
    rows = record_outcomes(db_session, writer_for(db_session, env_settings), run_id=RUN,
                           arm_id="A", orders=[_order(6, filled_at=None)], now=NOW)
    assert rows == []


def test_a_market_that_closed_before_the_horizon_names_that_reason(db_session, env_settings):
    # §1.9(a)'s third missing reason: the horizon is unobservable because the market settled
    # first, which is not the same statement as "we have no quote for it".
    closed_at = FILLED_AT + timedelta(seconds=600)
    db_session.add(VenueMarket(venue="kalshi", ticker="KXCLOSED-1", event_ticker="KXNFLGAME",
                               series_ticker="KXNFLGAME", market_type="moneyline",
                               match_status="matched", first_seen_raw_id=1,
                               last_seen_at=FILLED_AT, close_time=closed_at))
    db_session.flush()
    market = db_session.execute(
        text("select id from venue_markets where ticker = 'KXCLOSED-1'")).scalar()
    seed_mids(db_session, venue_market_id=market, mids={FILLED_AT: Decimal("0.4800")})
    order = _order(7) | {"venue_market_id": market, "ticker": "KXCLOSED-1"}
    rows = record_outcomes(db_session, writer_for(db_session, env_settings), run_id=RUN,
                           arm_id="A", orders=[order], now=NOW)
    late = [r for r in rows if r["horizon"] == "1800"][0]
    assert late["status"] == "missing" and late["missing_reason"] == "market_settled_early"
