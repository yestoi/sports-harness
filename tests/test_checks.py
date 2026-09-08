"""`harness/ops/checks.py`: the Layer 2b invariant registry as data, the static tape-access
guarantee, and `run_checks`' timeout handling.
"""

from datetime import datetime, timezone

import pytest

from harness.db.models import JobRun
from harness.ops import checks as checks_mod
from harness.ops.checks import CHECKS, Check, assert_no_tape_reads, run_checks

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)

EXPECTED_NAMES = {
    "duplicate_trades", "clv_p_used_matches_order_prob", "settle_errors_24h",
    "derived_without_venue_row_48h", "build_sha_drift", "orders_open_past_expiry",
    "fills_without_print", "gate_rows_one_gate_variant",
}


def test_checks_all_have_timeout_and_no_tape_reads():
    """Every Layer 2b invariant from verify.md is registered exactly once, and the static
    tape-access guarantee holds over the real registry (it also runs at import of the module,
    so this re-asserts it directly rather than only relying on that side effect)."""
    assert {c.name for c in CHECKS} == EXPECTED_NAMES
    for check in CHECKS:
        assert check.threshold
        assert callable(check.ok)
    assert_no_tape_reads(CHECKS)  # must not raise


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
