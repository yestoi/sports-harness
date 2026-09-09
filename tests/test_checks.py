"""`harness/ops/checks.py`: the Layer 2b invariant registry as data, the static tape-access
guarantee, and `run_checks`' timeout handling.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock

import pytest
from sqlalchemy import event as sa_event, text

from harness.db.models import FairValue, Intent, JobRun, Order, OrderEvent, Run, VenueTrade
from harness.ops import checks as checks_mod
from harness.ops.checks import CHECKS, Check, current_trades_partition, assert_no_tape_reads, run_checks


def _check(name):
    return next(c for c in CHECKS if c.name == name)

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)

EXPECTED_NAMES = {
    "duplicate_trades", "clv_p_used_matches_order_prob", "settle_errors_24h",
    "derived_without_venue_row_48h", "build_sha_drift", "orders_open_past_expiry",
    "fills_without_print", "gate_rows_one_gate_variant",
    # Fix round 1, coverage: every remaining Layer 2b invariant statement in
    # verify.md:127-144 (fills_without_print above is already one of the thirteen).
    "fair_values_negative_staleness", "runs_taker_side_missing_24h",
    "orders_without_place_event", "intents_without_order_or_skip",
    "fill_contracts_exceed_order_contracts", "orders_filled_exceeds_contracts",
    "settlement_result_mismatch", "fair_values_negative_feed_lag",
    "benchmarks_source_after_target", "fills_outside_placement_window",
    "markouts_at_after_horizon",
    # Final fix wave, I1: the eight Task 12b telemetry statements verify.md:191-208 added
    # after the registry was built, so "all checks pass" is again "every Layer 2b invariant
    # in verify.md is zero".
    "metric_samples_negative_24h", "operator_events_empty_summary_24h",
    "order_watch_negative_queue_24h", "equity_mtm_coverage_out_of_range_24h",
    "game_score_went_down_24h", "check_results_unknown_status_25h",
    "report_runs_generated_in_future", "report_cells_orphan",
}

#: verify.md:145-208 holds 27 invariant statements; the registry must hold one check each.
EXPECTED_COUNT = 27


def test_checks_all_have_timeout_and_no_tape_reads(db_session):
    """Every Layer 2b invariant from verify.md is registered exactly once, and the static
    tape-access guarantee holds over the real registry (it also runs at import of the module,
    so this re-asserts it directly rather than only relying on that side effect).

    Fix round 1, M6: also proves every check actually runs under the `SET LOCAL
    statement_timeout` -- not just the hand-picked ones in
    `test_run_checks_writes_pass_fail_and_skip_on_timeout` -- by counting that statement once
    per check, captured directly off the wire against the real `CHECKS` list.
    """
    assert {c.name for c in CHECKS} == EXPECTED_NAMES
    assert len(CHECKS) == EXPECTED_COUNT
    for check in CHECKS:
        assert check.threshold
        assert callable(check.ok)
    assert_no_tape_reads(CHECKS)  # must not raise

    engine = db_session.get_bind()
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().lower())

    sa_event.listen(engine, "before_cursor_execute", capture)
    try:
        job = JobRun(job="settle", started_at=NOW, status="running", notes={})
        db_session.add(job)
        db_session.flush()
        results = run_checks(db_session, NOW, job.id, checks=CHECKS)
    finally:
        sa_event.remove(engine, "before_cursor_execute", capture)

    timeout_sets = [s for s in statements if s.startswith("set local statement_timeout")]
    assert len(timeout_sets) == len(CHECKS)
    assert len(results) == len(CHECKS)
    assert all(r.status in ("pass", "fail", "skip") for r in results)


def test_assert_no_tape_reads_rejects_orderbook_events():
    bad = Check("bad", "select count(*) from orderbook_events", "== 0", lambda v: v == 0)
    with pytest.raises(ValueError):
        assert_no_tape_reads([bad])


def test_assert_no_tape_reads_rejects_raw_responses():
    bad = Check("bad", "select count(*) from raw_responses", "== 0", lambda v: v == 0)
    with pytest.raises(ValueError):
        assert_no_tape_reads([bad])


def test_assert_no_tape_reads_rejects_venue_trades_without_the_week_bound():
    bad = Check("bad", "select count(*) from venue_trades", "== 0", lambda v: v == 0)
    with pytest.raises(ValueError):
        assert_no_tape_reads([bad])


def test_run_checks_writes_pass_fail_and_skip_on_timeout(db_session, monkeypatch):
    job = JobRun(job="settle", started_at=NOW, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    fake = [
        Check("always_pass", "select 0", "== 0", lambda v: float(v) == 0.0),
        Check("always_fail", "select 1", "== 0", lambda v: float(v) == 0.0),
        Check("always_timeout", "select pg_sleep(1)", "== 0", lambda v: True),
    ]
    # A 50ms statement_timeout guarantees Postgres cancels the pg_sleep(1) check.
    monkeypatch.setattr(checks_mod, "STATEMENT_TIMEOUT_MS", 50)

    results = run_checks(db_session, NOW, job.id, checks=fake)
    db_session.flush()

    by_name = {r.check_name: r for r in results}
    assert by_name["always_pass"].status == "pass"
    assert by_name["always_fail"].status == "fail"
    assert by_name["always_timeout"].status == "skip"
    assert by_name["always_timeout"].detail == "timeout"
    assert all(r.job_run_id == job.id and r.ts == NOW for r in results)

    # The session survived the timeout: a later statement on it still works.
    assert db_session.execute(checks_mod.text("select 1")).scalar() == 1


def test_run_checks_resets_statement_timeout_after_the_last_check(db_session):
    """Fix round 1, M1: `SET LOCAL` inside a savepoint that RELEASEs (no error, no timeout)
    survives past the savepoint for the rest of the transaction -- so without an explicit
    reset, whatever housekeeping does after `run_checks` would keep running under a 2 s
    timeout it never asked for.

    Final fix wave, M2: this used to prove its point only by not raising on a `pg_sleep`, which
    reads as an assertion-free test to a scanner. It now asserts the setting itself -- the
    session's `statement_timeout` is back to the value it had before the call -- and keeps the
    `pg_sleep` as the behavioural half, with its result asserted.
    """
    job = JobRun(job="settle", started_at=NOW, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    before = db_session.execute(checks_mod.text("show statement_timeout")).scalar()

    fake = [Check("always_pass", "select 0", "== 0", lambda v: float(v) == 0.0)]
    with mock.patch.object(checks_mod, "STATEMENT_TIMEOUT_MS", 50):
        run_checks(db_session, NOW, job.id, checks=fake)

    after = db_session.execute(checks_mod.text("show statement_timeout")).scalar()
    assert after == before
    assert after != "50ms"

    # And behaviourally: 150ms comfortably exceeds the 50ms timeout run_checks used, so a leak
    # would cancel this statement instead of returning its row.
    assert db_session.execute(
        checks_mod.text("select 1 from pg_sleep(0.15)")).scalar() == 1


def test_current_trades_partition_matches_the_schema_naming():
    from harness.db.schema import _partition_name, week_bounds
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    start, _ = week_bounds(now)
    assert current_trades_partition(now) == _partition_name("venue_trades", start)


def test_duplicate_trades_names_the_partition_not_the_parent():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    sql = _check("duplicate_trades").sql_for(now)
    assert current_trades_partition(now) in sql
    assert "from venue_trades\n" not in sql and "from venue_trades " not in sql


def test_fair_values_staleness_check_is_bounded_to_24_hours():
    sql = _check("fair_values_negative_staleness").sql
    assert "created_at" in sql and "24 hours" in sql


def test_both_bounded_checks_pass_on_a_seeded_database(db_session):
    now = datetime.now(timezone.utc)
    job = JobRun(job="settle", started_at=now, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    # One in-window fair value with staleness 5, one trade in the current week.
    db_session.add(FairValue(run_id=1, game_id=1, market_type="moneyline",
                             fair_p=Decimal("0.5500"), fair_source="direct",
                             staleness_s=5, created_at=now))
    db_session.add(VenueTrade(venue="kalshi", trade_id="t1", ticker="T", ts=now,
                              yes_price=Decimal("0.2300"), count=Decimal("5.00"),
                              taker_side="yes", is_block=False, source="rest", raw_id=None))
    db_session.flush()

    results = {r.check_name: r for r in run_checks(
        db_session, now, job_run_id=job.id,
        checks=[_check("duplicate_trades"), _check("fair_values_negative_staleness")])}
    assert results["duplicate_trades"].status == "pass"
    assert results["fair_values_negative_staleness"].status == "pass"


def test_a_negative_staleness_row_inside_the_window_still_fails(db_session):
    now = datetime.now(timezone.utc)
    job = JobRun(job="settle", started_at=now, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    db_session.add(FairValue(run_id=1, game_id=1, market_type="moneyline",
                             fair_p=Decimal("0.5500"), fair_source="direct",
                             staleness_s=-1, created_at=now))
    db_session.flush()

    results = run_checks(db_session, now, job_run_id=job.id,
                         checks=[_check("fair_values_negative_staleness")])
    assert results[0].status == "fail"


def test_duplicate_trades_flags_a_duplicate_in_the_current_partition(db_session):
    """Fix round 1, Important 2: prove the dedupe actually catches a duplicate, not just that
    it stays under the timeout on a clean database."""
    now = datetime.now(timezone.utc)
    job = JobRun(job="settle", started_at=now, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    for i in range(2):
        db_session.add(VenueTrade(venue="kalshi", trade_id="dup-1", ticker="T",
                                  ts=now - timedelta(seconds=i), yes_price=Decimal("0.2300"),
                                  count=Decimal("5.00"), taker_side="yes", is_block=False,
                                  source="rest", raw_id=None))
    db_session.flush()

    result = run_checks(db_session, now, job_run_id=job.id,
                        checks=[_check("duplicate_trades")])[0]
    assert result.status == "fail"
    assert float(result.value) == 1.0


def test_duplicate_trades_ignores_a_duplicate_confined_to_an_older_partition(db_session):
    """Fix round 1, Important 2: a duplicate three weeks back must not leak into the
    current-week bounded check -- proving the partition bound actually excludes older weeks,
    not just that it names the right partition."""
    from harness.db.schema import _partition_name, week_bounds

    now = datetime.now(timezone.utc)
    start, _ = week_bounds(now)
    old_start = start - timedelta(weeks=3)
    old_end = old_start + timedelta(days=7)
    name = _partition_name("venue_trades", old_start)
    db_session.execute(checks_mod.text(
        f"create table if not exists {name} partition of venue_trades "
        f"for values from ('{old_start.isoformat()}') to ('{old_end.isoformat()}')"))
    db_session.flush()

    job = JobRun(job="settle", started_at=now, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    old_ts = old_start + timedelta(days=1)
    for i in range(2):
        db_session.add(VenueTrade(venue="kalshi", trade_id="dup-old", ticker="T",
                                  ts=old_ts - timedelta(seconds=i), yes_price=Decimal("0.2300"),
                                  count=Decimal("5.00"), taker_side="yes", is_block=False,
                                  source="rest", raw_id=None))
    db_session.flush()

    result = run_checks(db_session, now, job_run_id=job.id,
                        checks=[_check("duplicate_trades")])[0]
    assert result.status == "pass"
    assert float(result.value) == 0.0


def test_duplicate_trades_falls_back_to_the_parent_table_when_the_partition_is_missing(db_session):
    """Fix round 1, Important 1: before the first recorder tick after a cold start (or on a
    fresh database) the current week's venue_trades partition does not exist yet. Naming it in
    the query must not turn into a `skip` -- it must fall back to the static, parent-table
    statement, which is always valid."""
    now = datetime.now(timezone.utc)
    name = current_trades_partition(now)
    db_session.execute(checks_mod.text(f"drop table if exists {name}"))

    job = JobRun(job="settle", started_at=now, status="running", notes={})
    db_session.add(job)
    db_session.flush()

    result = run_checks(db_session, now, job_run_id=job.id,
                        checks=[_check("duplicate_trades")])[0]
    assert result.status == "pass"
    assert result.detail is None
    assert float(result.value) == 0.0


def test_the_orders_key_index_is_built_concurrently(db_session):
    from sqlalchemy import text

    from harness.db.schema import _CONCURRENT_INDEX_DDL

    ddl = " ".join(_CONCURRENT_INDEX_DDL).lower()
    assert "create index concurrently if not exists ix_orders_key_placed" in ddl
    present = db_session.execute(text(
        "select 1 from pg_indexes where indexname = 'ix_orders_key_placed'")).first()
    assert present is not None, "create_schema did not build ix_orders_key_placed"


# --- Phase 4.5, T1 (addendum 0.4): the three corrected definitions. -----------------------


def _intent(session, *, created_at, variant_id="v1", venue_market_id=7, side="yes",
            signal_id=None):
    row = Intent(id=uuid.uuid4(), signal_id=signal_id or int(created_at.timestamp() * 1000) % 10**9,
                 variant_id=variant_id, venue="kalshi", venue_market_id=venue_market_id,
                 ticker="KXNFL-T", side=side, signal_created_at=created_at,
                 created_at=created_at, replay=False)
    session.add(row)
    session.flush()
    return row


def _order(session, *, placed_at, status, variant_id="v1", venue_market_id=7, side="yes"):
    row = Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi",
                client_order_id=str(uuid.uuid4()), ticker="KXNFL-T",
                venue_market_id=venue_market_id, side=side, prob=Decimal("0.5000"),
                contracts=Decimal("10.00"), status=status, placed_at=placed_at)
    session.add(row)
    session.flush()
    return row


def _run_one(session, name, now):
    job = JobRun(job="settle", started_at=now, status="running", notes={})
    session.add(job)
    session.flush()
    return run_checks(session, now, job_run_id=job.id, checks=[_check(name)])[0]


def test_intents_check_excuses_an_intent_whose_key_had_a_working_order(db_session):
    """The executor's hold path: it considered the intent, saw an order already working on the
    same (variant, market, side) key, and wrote no event. Not a lost intent."""
    now = datetime.now(timezone.utc)
    _order(db_session, placed_at=now - timedelta(hours=3), status="open")
    _intent(db_session, created_at=now - timedelta(minutes=30))

    assert _run_one(db_session, "intents_without_order_or_skip", now).status == "pass"


def test_intents_check_excuses_an_order_cancelled_after_the_intent(db_session):
    """Working at `created_at` and cancelled later still excuses it: the cancel event's `ts` is
    after the intent, so the order was resting when the intent was made."""
    now = datetime.now(timezone.utc)
    order = _order(db_session, placed_at=now - timedelta(hours=3), status="cancelled")
    db_session.add(OrderEvent(order_id=order.id, ts=now - timedelta(minutes=10), kind="cancel",
                              reason="reprice", replay=False))
    _intent(db_session, created_at=now - timedelta(minutes=30))
    db_session.flush()

    assert _run_one(db_session, "intents_without_order_or_skip", now).status == "pass"


def test_intents_check_still_fails_on_an_order_cancelled_before_the_intent(db_session):
    """The boundary (ruling B-(c)): a settled or cancelled order from hours earlier must not
    excuse a real loss."""
    now = datetime.now(timezone.utc)
    order = _order(db_session, placed_at=now - timedelta(hours=3), status="cancelled")
    db_session.add(OrderEvent(order_id=order.id, ts=now - timedelta(hours=2), kind="cancel",
                              reason="reprice", replay=False))
    _intent(db_session, created_at=now - timedelta(minutes=30))
    db_session.flush()

    assert _run_one(db_session, "intents_without_order_or_skip", now).status == "fail"


def test_intents_check_still_fails_on_a_key_with_no_order_at_all(db_session):
    now = datetime.now(timezone.utc)
    _intent(db_session, created_at=now - timedelta(minutes=30))

    assert _run_one(db_session, "intents_without_order_or_skip", now).status == "fail"


def test_intents_check_ignores_an_intent_older_than_24_hours(db_session):
    now = datetime.now(timezone.utc)
    _intent(db_session, created_at=now - timedelta(hours=30))

    assert _run_one(db_session, "intents_without_order_or_skip", now).status == "pass"


def test_build_sha_drift_ignores_runs_that_predate_the_new_build(db_session):
    """A deploy day: three runs on the old sha, then the new build's first run. The old rows
    are before the new build existed, so they are not drift."""
    now = datetime.now(timezone.utc)
    for minutes in (240, 180, 120):
        db_session.add(Run(started_at=now - timedelta(minutes=minutes), status="ok",
                           notes={}, build_sha="oldsha"))
    db_session.add(Run(started_at=now - timedelta(minutes=60), status="ok", notes={},
                       build_sha="newsha"))
    db_session.flush()

    assert _run_one(db_session, "build_sha_drift", now).status == "pass"


def test_build_sha_drift_still_fails_on_a_container_left_behind(db_session):
    """The failure the check exists for: a run carrying the old sha *after* the new one
    appeared, i.e. a container still on the old image."""
    now = datetime.now(timezone.utc)
    db_session.add(Run(started_at=now - timedelta(minutes=180), status="ok", notes={},
                       build_sha="oldsha"))
    db_session.add(Run(started_at=now - timedelta(minutes=120), status="ok", notes={},
                       build_sha="newsha"))
    db_session.add(Run(started_at=now - timedelta(minutes=30), status="ok", notes={},
                       build_sha="oldsha"))
    db_session.flush()

    assert _run_one(db_session, "build_sha_drift", now).status == "fail"


def test_feed_lag_check_is_bounded_to_24_hours(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(FairValue(run_id=1, game_id=1, market_type="moneyline",
                             fair_p=Decimal("0.5500"), fair_source="direct",
                             feed_lag_s=-4, created_at=now - timedelta(hours=30)))
    db_session.flush()

    assert _run_one(db_session, "fair_values_negative_feed_lag", now).status == "pass"
    assert "24 hours" in _check("fair_values_negative_feed_lag").sql


def test_a_negative_feed_lag_inside_the_window_still_fails(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(FairValue(run_id=1, game_id=1, market_type="moneyline",
                             fair_p=Decimal("0.5500"), fair_source="direct",
                             feed_lag_s=-4, created_at=now - timedelta(hours=1)))
    db_session.flush()

    assert _run_one(db_session, "fair_values_negative_feed_lag", now).status == "fail"


def test_the_intents_check_rides_the_orders_key_index(db_session):
    """T5 builds `ix_orders_key_placed`; this check is why it exists. Without it the correlated
    lookup is a sequential scan per candidate intent and the check degrades to a daily `skip`."""
    present = db_session.execute(text(
        "select 1 from pg_indexes where indexname = 'ix_orders_key_placed'")).first()
    assert present is not None, "T5's ix_orders_key_placed is missing; this check will time out"
