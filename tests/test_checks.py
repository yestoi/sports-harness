"""`harness/ops/checks.py`: the Layer 2b invariant registry as data, the static tape-access
guarantee, and `run_checks`' timeout handling.
"""

from datetime import datetime, timezone
from unittest import mock

import pytest
from sqlalchemy import event as sa_event

from harness.db.models import JobRun
from harness.ops import checks as checks_mod
from harness.ops.checks import CHECKS, Check, assert_no_tape_reads, run_checks

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
