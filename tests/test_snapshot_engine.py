"""The snapshot layer's spine: the bounded second engine, the section guard, the name pattern
and `run_builder`'s upsert semantics."""

import inspect
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.dashboard import snapshots
from harness.dashboard.snapshots import (SNAPSHOT_STATEMENT_TIMEOUT_MS, base_payload,
                                         make_snapshot_engine, run_builder, section,
                                         valid_snapshot_name)
from harness.db.models import DashboardSnapshot, MetricSample

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)

#: The five tables `deploy/backup/dump.sh` excludes from the nightly dump. No snapshot builder
#: may name one: the surfaces read `metric_samples` and `runs.notes` instead.
FORBIDDEN_TABLES = ("orderbook_events", "venue_trades", "raw_responses", "odds_snapshots",
                    "venue_quotes")


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)


def test_the_snapshot_timeout_sits_at_or_below_the_checks_timeout():
    """Both bounds are read out of the code that enforces them, never restated here: the checks
    registry's own `SET LOCAL`, and the default `make_engine` gives the WebSocket sink's
    engine (`harness/cli.py::ws_record`)."""
    from harness.db.engine import make_engine
    from harness.ops.checks import STATEMENT_TIMEOUT_MS

    ws_sink_timeout_ms = inspect.signature(make_engine).parameters["statement_timeout_ms"].default

    assert SNAPSHOT_STATEMENT_TIMEOUT_MS == 2000
    assert SNAPSHOT_STATEMENT_TIMEOUT_MS <= STATEMENT_TIMEOUT_MS
    assert SNAPSHOT_STATEMENT_TIMEOUT_MS < ws_sink_timeout_ms


def test_the_snapshot_engine_is_bounded_at_two_connections(env_settings):
    """`max_overflow=0` is the load-bearing half: SQLAlchemy's default overflow of 10 would let
    a pool of two become twelve connections on a Postgres already shared with app-run,
    app-exec, app-ws and the backup sidecar (ruling A-I10)."""
    engine = make_snapshot_engine(env_settings)
    try:
        assert engine.pool.size() == 2
        assert engine.pool._max_overflow == 0
    finally:
        engine.dispose()


def test_the_snapshot_engine_carries_the_timeout_into_postgres(env_settings):
    """Asserted against a live session rather than the engine's kwargs: `connect_args` is merged
    into the connect parameters inside `create_engine`, so nothing on the Engine object reports
    it back and only the server's own `show` proves the option arrived."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    engine = make_snapshot_engine(env_settings.model_copy(update={"database_url": url}))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("show statement_timeout")).scalar() == "2s"
    finally:
        engine.dispose()


def test_a_failing_section_marks_only_itself_and_records_a_class_name():
    payload = {}
    section(payload, "good", lambda: {"n": 1})
    section(payload, "bad", lambda: (_ for _ in ()).throw(ZeroDivisionError("1/0 secret sql")))
    assert payload["good"] == {"n": 1}
    assert payload["bad"] == {"error": "ZeroDivisionError"}
    assert "secret sql" not in repr(payload)


def test_the_name_pattern_is_anchored_and_accepts_only_the_five_shapes():
    for good in ("pulse", "floor", "gate", "ticket", "study:2026-37", "study:2026-7"):
        assert valid_snapshot_name(good)
    for bad in ("pulse ", " pulse", "study:2026-37x", "study:26-3", "study:2026-370",
                "../etc", "pulse\npulse", "study:2026-37\r", "", "PULSE"):
        assert not valid_snapshot_name(bad)


def test_base_payload_carries_the_five_shared_keys(env_settings):
    payload = base_payload("pulse", NOW, env_settings, cadence_s=30)
    assert payload["build_sha"] == env_settings.build_sha
    assert payload["now"] == NOW.isoformat()
    assert payload["cadence_s"] == 30
    assert payload["sentences"] == {}
    assert payload["readings"] == {}


def test_run_builder_stamps_the_cadence_the_job_is_running_at(db_session, env_settings):
    """Floor and Ticket decide their cadence per run, so the payload has to carry the one in
    force -- not the constant the builder's `base_payload` defaulted to."""
    snapshots.register_builder(
        "t6cadence",
        lambda session, now, settings: base_payload("t6cadence", now, settings, cadence_s=15))
    try:
        out = run_builder(_factory(db_session), "t6cadence", NOW, env_settings, cadence_s=60)
    finally:
        snapshots.BUILDERS.pop("t6cadence", None)

    assert out["payload"]["cadence_s"] == 60
    assert out["cadence_s"] == 60
    row = db_session.get(DashboardSnapshot, "t6cadence")
    assert row.payload["cadence_s"] == 60


def test_run_builder_writes_the_row_and_the_elapsed_metric(db_session, env_settings):
    snapshots.register_builder("t6probe", lambda session, now, settings: {"n": 1})
    try:
        out = run_builder(_factory(db_session), "t6probe", NOW, env_settings, cadence_s=30)
    finally:
        snapshots.BUILDERS.pop("t6probe", None)

    assert out["payload"]["n"] == 1
    assert out["error"] is None
    row = db_session.get(DashboardSnapshot, "t6probe")
    assert row is not None and row.elapsed_ms >= 0 and row.error is None
    samples = [s for s in db_session.query(MetricSample).all()
               if s.name == "serve.snapshot_ms"]
    assert samples and samples[0].labels == {"name": "t6probe"}
    assert samples[0].source == "serve"


def test_a_rejected_metric_write_still_leaves_the_snapshot_row_written(
        db_session, env_settings, monkeypatch):
    """Ruling 1: telemetry never fails its caller. The trap is that `telemetry.record` is a bare
    `session.add`, so the metric's INSERT is not issued until a flush -- a metric row Postgres
    rejects fails at the *commit*, not at the `record` call, and would take the snapshot upsert
    down with it if the two shared one transaction."""
    def bad_record(session, source, name, value, labels=None, ts=None):
        # `metric_samples.source` is String(12); this is rejected by the server on flush.
        session.add(MetricSample(ts=ts, source="x" * 40, name=name, value=value,
                                 labels=labels or {}))

    monkeypatch.setattr(snapshots.telemetry, "record", bad_record)
    snapshots.register_builder("t6probe", lambda session, now, settings: {"n": 1})
    try:
        out = run_builder(_factory(db_session), "t6probe", NOW, env_settings, cadence_s=30)
    finally:
        snapshots.BUILDERS.pop("t6probe", None)

    assert out["error"] is None and out["payload"] == {"n": 1, "cadence_s": 30}
    row = db_session.get(DashboardSnapshot, "t6probe")
    assert row is not None, "a rejected metric write must not roll back the snapshot row"
    assert db_session.query(MetricSample).count() == 0


def test_a_raising_telemetry_call_still_leaves_the_snapshot_row_written(
        db_session, env_settings, monkeypatch):
    def boom_record(session, source, name, value, labels=None, ts=None):
        raise RuntimeError("telemetry is down")

    monkeypatch.setattr(snapshots.telemetry, "record", boom_record)
    snapshots.register_builder("t6probe", lambda session, now, settings: {"n": 1})
    try:
        out = run_builder(_factory(db_session), "t6probe", NOW, env_settings, cadence_s=30)
    finally:
        snapshots.BUILDERS.pop("t6probe", None)

    assert out["error"] is None
    assert db_session.get(DashboardSnapshot, "t6probe") is not None


def test_run_builder_sets_the_name_context_var_for_the_build(db_session, env_settings):
    """Study is one builder instantiated per week, and reads its week out of the name (T12).
    Threading the name as a context variable is what keeps the `Builder` signature the same for
    all five surfaces, and it must be reset once the build is over."""
    seen = []
    snapshots.register_builder(
        "t6probe", lambda session, now, settings: seen.append(snapshots.current_name.get()) or {})
    try:
        run_builder(_factory(db_session), "t6probe", NOW, env_settings, cadence_s=30)
    finally:
        snapshots.BUILDERS.pop("t6probe", None)

    assert seen == ["t6probe"]
    assert snapshots.current_name.get() == ""


def test_the_name_context_var_is_reset_even_when_the_build_raises(db_session, env_settings):
    def boom(session, now, settings):
        raise ValueError("no")

    snapshots.register_builder("t6probe", boom)
    try:
        run_builder(_factory(db_session), "t6probe", NOW, env_settings, cadence_s=30)
    finally:
        snapshots.BUILDERS.pop("t6probe", None)

    assert snapshots.current_name.get() == ""


def test_a_builder_that_raises_keeps_the_previous_payload_and_records_a_class_name(
        db_session, env_settings):
    factory = _factory(db_session)
    snapshots.register_builder("t6probe", lambda session, now, settings: {"n": 1})
    run_builder(factory, "t6probe", NOW, env_settings, cadence_s=30)

    def boom(session, now, settings):
        raise ValueError("select * from orders where secret = 'x'")

    snapshots.register_builder("t6probe", boom)
    try:
        out = run_builder(factory, "t6probe", NOW + timedelta(seconds=30), env_settings,
                          cadence_s=30)
    finally:
        snapshots.BUILDERS.pop("t6probe", None)

    assert out["error"] == "ValueError"
    row = db_session.get(DashboardSnapshot, "t6probe")
    db_session.refresh(row)
    assert row.error == "ValueError"
    # The good build's payload, exactly as it was stored -- `run_builder` stamped the running
    # cadence into it then, and the failed build touched neither key.
    assert row.payload == {"n": 1, "cadence_s": 30}, \
        "a failed build must not empty the last good payload"
    assert "secret" not in (row.error or "")


def test_run_builder_refuses_an_unregistered_name(db_session, env_settings):
    with pytest.raises(KeyError):
        run_builder(_factory(db_session), "nosuch", NOW, env_settings, cadence_s=30)


def test_a_study_name_resolves_to_the_study_builder(db_session, env_settings):
    seen = {}

    def study(session, now, settings):
        seen["called"] = True
        return {"n": 2}

    snapshots.register_builder("study", study)
    try:
        out = run_builder(_factory(db_session), "study:2026-37", NOW, env_settings,
                          cadence_s=600)
    finally:
        snapshots.BUILDERS.pop("study", None)

    assert seen["called"] and out["payload"]["n"] == 2
    assert db_session.get(DashboardSnapshot, "study:2026-37") is not None


def test_no_snapshot_module_names_a_forbidden_table():
    """The package-wide half of the forbidden-table rule: whatever builder modules land beside
    this spine, none of them may read one of the five bulk tables `deploy/backup/dump.sh`
    excludes from the nightly dump. Each builder task also carries its own copy of this check."""
    package = Path(snapshots.__file__).parent
    modules = sorted(package.glob("*.py"))
    assert modules, "the snapshots package has no modules to check"
    for module in modules:
        body = module.read_text().lower()
        for table in FORBIDDEN_TABLES:
            assert table not in body, f"{module.name} names {table}"


def test_settings_carry_the_scheduler_off_switch(env_settings):
    assert env_settings.snapshots_enabled is True
