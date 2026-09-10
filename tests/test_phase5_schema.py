"""The ten phase 5 tables and the veto_h9 view, as behaviour rather than as prose.

Nothing here asserts a column list by reflection: the catalogue diff in tests/test_alembic.py
already does that on both build paths. These tests pin the things a later task will rely on and
a rename would silently break -- the enums a check reads, the view's three filters, and the
partial index the worker's claim rides.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, SAWarning

from harness.db.models import (FuturesSnapshot, ReportAnnotation, ResearchNote, ResearchSpend,
                               Rfq, RfqQuote, VetoDecision, VetoQueue, WeatherPoint,
                               WeatherSnapshot)

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


def _note(session, call_id, model, *, kind="veto", replay=False, subject_id="1"):
    row = ResearchNote(call_id=call_id, model=model, kind=kind, subject_id=subject_id,
                       effort="high", prompt_hash="a" * 64, features={}, snippets={},
                       tool_calls=[], output={}, usage={}, cost_usd=Decimal("0.12"),
                       latency_ms=1234, request_id="req_1", created_at=NOW, replay=replay)
    session.add(row)
    return row


def _decision(session, signal_id, call_id, decision, *, from_cache=False):
    row = VetoDecision(signal_id=signal_id, call_id=call_id, decision=decision,
                       confidence=Decimal("0.7"), from_cache=from_cache, feature_delta={},
                       signal_created_at=NOW - timedelta(minutes=5), decided_at=NOW,
                       reason_code=None)
    session.add(row)
    return row


def test_every_phase5_table_exists(db_session):
    names = set(inspect(db_session.get_bind()).get_table_names())
    expected = {"futures_snapshots", "weather_points", "weather_snapshots", "veto_queue",
                "research_notes", "veto_decisions", "research_spend", "report_annotations",
                "rfqs", "rfq_quotes"}
    assert expected <= names


def test_the_veto_h9_view_exists(db_session):
    assert "veto_h9" in set(inspect(db_session.get_bind()).get_view_names())


def test_two_rows_share_one_call_id(db_session):
    """1.4 Records: "two rows per call under one `call_id`". The primary key is (call_id, model),
    so the pair is expressible and a third row for the same model is not."""
    call_id = uuid.uuid4()
    _note(db_session, call_id, "claude-opus-5")
    _note(db_session, call_id, "claude-sonnet-5")
    db_session.flush()
    assert db_session.execute(
        text("select count(*) from research_notes where call_id = :c"), {"c": call_id}
    ).scalar() == 2


def test_veto_h9_keeps_only_the_decided_primary_non_replay_rows(db_session):
    """The view's three filters, one row each: a shadow row, a replay row and a
    veto_skipped_budget decision must all be absent, and the plain primary row present."""
    keep, shadow, replayed, skipped = (uuid.uuid4() for _ in range(4))
    _note(db_session, keep, "claude-opus-5")
    _note(db_session, keep, "claude-sonnet-5")
    _note(db_session, shadow, "claude-sonnet-5")
    _note(db_session, replayed, "claude-opus-5", replay=True)
    _note(db_session, skipped, "claude-opus-5")
    _decision(db_session, 1, keep, "veto")
    _decision(db_session, 2, shadow, "veto")
    _decision(db_session, 3, replayed, "veto")
    _decision(db_session, 4, skipped, "veto_skipped_budget")
    db_session.flush()

    rows = db_session.execute(text("select signal_id, model from veto_h9 order by signal_id")).all()
    assert [(r.signal_id, r.model) for r in rows] == [(1, "claude-opus-5")]


def test_a_budget_skipped_decision_needs_no_call(db_session):
    """0.3/B-M1: a skipped signal decides `veto_skipped_budget` with `call_id` null."""
    _decision(db_session, 9, None, "veto_skipped_budget")
    db_session.flush()
    assert db_session.execute(
        text("select call_id from veto_decisions where signal_id = 9")).scalar() is None


def test_the_open_queue_index_is_partial_on_unclaimed_rows(db_session):
    indexes = {i["name"]: i for i in inspect(db_session.get_bind()).get_indexes("veto_queue")}
    assert "ix_veto_queue_open" in indexes
    # `postgresql_where` is filled verbatim from `pg_get_expr(...)`, which Postgres renders as
    # `(claimed_at IS NULL)` -- uppercase. Lowercase before the substring test.
    predicate = (indexes["ix_veto_queue_open"].get("dialect_options", {})
                 .get("postgresql_where") or "").lower()
    assert "claimed_at is null" in predicate


def test_one_quote_per_rfq(db_session):
    db_session.add(Rfq(id="rfq_1", received_at=NOW, created_ts=NOW, event_ticker="KXNFLGAME-26",
                       market_ticker="KXNFLGAME-26-DAL", contracts_fp=Decimal("10.00"),
                       target_cost_dollars=None, mve_collection_ticker=None, legs=[],
                       raw={"truncated": False}, status="open", deleted_ts=None))
    db_session.add(RfqQuote(rfq_id="rfq_1", computed_at=NOW, legs=2, fair=Decimal("0.2500"),
                            margin_per_leg=Decimal("0.0300"), yes_bid=Decimal("0.1900"),
                            no_bid=Decimal("0.6900"), fee_branch_game=True,
                            fee_branch_event=False, fee_subtracted=Decimal("0.0000"),
                            yes_bid_other_branch=Decimal("0.1900"),
                            no_bid_other_branch=Decimal("0.6900"), declined_reason=None,
                            unmatched_legs=0))
    db_session.flush()
    db_session.add(RfqQuote(rfq_id="rfq_1", computed_at=NOW, legs=2, margin_per_leg=Decimal("0.03"),
                            unmatched_legs=0))
    with pytest.raises(Exception):
        db_session.flush()
    db_session.rollback()


def test_research_spend_is_keyed_by_day_kind_and_model(db_session):
    day = date(2026, 9, 15)
    for kind in ("veto", "annotate"):
        for model in ("claude-opus-5", "claude-sonnet-5"):
            db_session.add(ResearchSpend(day=day, kind=kind, model=model, calls=0,
                                         input_tokens=0, output_tokens=0, cache_read_tokens=0,
                                         cache_write_tokens=0, searches=0,
                                         usd_reserved=Decimal("0"), usd=Decimal("0")))
    db_session.flush()
    assert db_session.execute(
        text("select count(*) from research_spend where day = :d"), {"d": day}).scalar() == 4


def test_housekeeping_never_deletes_from_a_phase_5_table(db_session):
    """Ruling B-M7: every one of the ten tables is retained for the season. That holds today
    only because `harness/ops/housekeeping.py` contains no delete at all, and nothing pinned it.
    This is the pin: the retention rule is a property of that module's text, so the test reads
    the text."""
    from pathlib import Path

    import harness.ops.housekeeping as housekeeping

    body = Path(housekeeping.__file__).read_text().lower()
    for table in ("futures_snapshots", "weather_points", "weather_snapshots", "veto_queue",
                  "research_notes", "veto_decisions", "research_spend", "report_annotations",
                  "rfqs", "rfq_quotes"):
        assert f"delete from {table}" not in body
        assert f"truncate {table}" not in body


def test_the_other_six_tables_accept_a_row(db_session):
    """One insert each, so a column that a later task names cannot be missing or misspelled."""
    db_session.add(FuturesSnapshot(
        run_id=1, snapshot_week="2026-W38", series_ticker="KXNFLSB", event_ticker="KXNFLSB-27",
        market_ticker="KXNFLSB-27-KC", title="Super Bowl winner", yes_sub_title="Kansas City",
        kalshi_market_type="binary", strike_type="custom", floor_strike=None, cap_strike=None,
        yes_bid=Decimal("0.1200"), yes_ask=Decimal("0.1400"), last_price=Decimal("0.1300"),
        volume=Decimal("1000.00"), open_interest=Decimal("500.00"), close_time=NOW,
        fetched_at=NOW))
    db_session.add(WeatherPoint(sport="nfl", team_id=1, office="LIX", grid_x=60, grid_y=91,
                                forecast_hourly_url="https://api.weather.gov/gridpoints/LIX/60,91/forecast/hourly",
                                fetched_at=NOW))
    db_session.add(WeatherSnapshot(run_id=1, game_id=1, fetched_at=NOW, period_start=NOW,
                                   temperature_f=78, wind_mph=6, wind_dir="SSE", precip_pct=20,
                                   short_forecast="Partly Cloudy", roof="open"))
    db_session.add(VetoQueue(signal_id=1, game_id=1, market_type="moneyline",
                             bucket_start=NOW, enqueued_at=NOW, claimed_at=None))
    db_session.add(ReportAnnotation(report_run_id=1, model="claude-opus-5", prompt_hash="b" * 64,
                                    bullets=[], cost_usd=Decimal("0.05"), created_at=NOW))
    db_session.flush()


# --- fix round 1: the three foreign-id primary keys are never generated ----------------------
# `veto_queue.signal_id` and `veto_decisions.signal_id` are copies of `signals.id`, and
# `report_annotations.report_run_id` a copy of `report_runs.id`. SQLAlchemy makes a single-column
# integer primary key a BIGSERIAL unless told not to, and a sequence on one of these would turn
# an insert that forgot the id into a row pointing at a signal or a run that does not exist --
# silently, and only discoverable later as an orphan. `autoincrement=False` on all three (and on
# their columns in `0004_phase5`) is what makes the omission an error at write time instead.

@pytest.mark.parametrize("table, column", [
    ("veto_queue", "signal_id"),
    ("veto_decisions", "signal_id"),
    ("report_annotations", "report_run_id"),
])
def test_the_foreign_id_primary_keys_carry_no_sequence(db_session, table, column):
    """No default at all on the column: `pg_get_serial_sequence` is null for a plain BIGINT
    primary key and names a sequence for a BIGSERIAL one."""
    sequence = db_session.execute(
        text("select pg_get_serial_sequence(:t, :c)"), {"t": table, "c": column}).scalar()
    assert sequence is None, f"{table}.{column} is a serial: {sequence}"


#: Both halves of the refusal, asserted together. SQLAlchemy warns at flush time that a primary
#: key column has no generator and no value -- which is the ORM saying out loud what
#: `autoincrement=False` bought us -- and Postgres then raises NotNullViolation, which SQLAlchemy
#: wraps as IntegrityError. `pytest.warns` both pins the warning and consumes it, so the suite
#: stays free of warnings.
_REFUSED = "is marked as a member of the primary key"


def test_a_queue_row_without_its_signal_id_is_refused(db_session):
    db_session.add(VetoQueue(game_id=1, market_type="moneyline", bucket_start=NOW,
                             enqueued_at=NOW, claimed_at=None))
    with pytest.warns(SAWarning, match=_REFUSED), pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_decision_without_its_signal_id_is_refused(db_session):
    db_session.add(VetoDecision(call_id=None, decision="veto_skipped_budget", confidence=None,
                                from_cache=False, feature_delta={}, signal_created_at=NOW,
                                decided_at=NOW, reason_code=None))
    with pytest.warns(SAWarning, match=_REFUSED), pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_an_annotation_without_its_report_run_id_is_refused(db_session):
    db_session.add(ReportAnnotation(model="claude-opus-5", prompt_hash="c" * 64, bullets=[],
                                    cost_usd=Decimal("0.05"), created_at=NOW))
    with pytest.warns(SAWarning, match=_REFUSED), pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
