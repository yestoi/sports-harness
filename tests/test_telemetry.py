"""`harness/telemetry.py`: the writer API every Task 12b writer shares -- one metric sample,
one batch of them, one operator event, and the in-memory sampling clock -- plus the eight new
tables and their five named indexes (design spec §3).
"""

from datetime import datetime, timezone

from sqlalchemy import event as sa_event
from sqlalchemy import text

from harness.db.models import MetricSample, OperatorEvent
from harness.telemetry import Sampler, event, record, record_many, sanitize_reason

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

TABLES = ("metric_samples", "operator_events", "order_watch_samples", "equity_snapshots",
          "game_score_events", "check_results", "report_runs", "report_cells")
INDEXES = ("ix_metric_samples_name_ts", "ix_operator_events_ts", "ix_game_score_events_game_ts",
          "ix_check_results_ts", "ix_report_runs_week")


def test_eight_tables_exist_with_indexes(db_session):
    existing_tables = set(db_session.execute(
        text("select tablename from pg_tables where schemaname = 'public'")).scalars().all())
    for table in TABLES:
        assert table in existing_tables, table

    existing_indexes = set(db_session.execute(
        text("select indexname from pg_indexes where schemaname = 'public'")).scalars().all())
    for index in INDEXES:
        assert index in existing_indexes, index


def test_record_writes_one_row_with_default_labels(db_session):
    record(db_session, "exec", "exec.loop_ms", 42, ts=NOW)
    db_session.flush()
    row = db_session.query(MetricSample).one()
    assert (row.source, row.name, float(row.value), row.labels, row.ts) == (
        "exec", "exec.loop_ms", 42.0, {}, NOW)


def test_record_many_one_statement_and_labels_default(db_session):
    engine = db_session.get_bind()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().lower().startswith("insert into metric_samples"):
            statements.append(statement)

    sa_event.listen(engine, "before_cursor_execute", capture)
    try:
        n = record_many(db_session, "exec",
                        [("exec.placed", 3, {}), ("exec.cancelled", 1, {"reason": "reprice"})],
                        ts=NOW)
        db_session.flush()
    finally:
        sa_event.remove(engine, "before_cursor_execute", capture)

    assert n == 2
    assert len(statements) == 1, statements  # one INSERT ... VALUES batch, not two inserts

    rows = db_session.query(MetricSample).order_by(MetricSample.id).all()
    assert [(r.source, r.name, float(r.value), r.labels) for r in rows] == [
        ("exec", "exec.placed", 3.0, {}),
        ("exec", "exec.cancelled", 1.0, {"reason": "reprice"}),
    ]


def test_record_many_empty_writes_nothing(db_session):
    assert record_many(db_session, "exec", [], ts=NOW) == 0
    db_session.flush()
    assert db_session.query(MetricSample).count() == 0


def test_event_sanitizes_and_truncates_summary(db_session):
    dirty = "kill switch! <script>alert(1)</script> " + "x" * 250
    event_id = event(db_session, "kill_on", dirty, ref={"n": 1}, ts=NOW)
    db_session.flush()

    row = db_session.get(OperatorEvent, event_id)
    assert row.kind == "kill_on"
    assert len(row.summary) <= 200
    assert "<" not in row.summary and ">" not in row.summary
    assert row.ref == {"n": 1}
    assert row.ts == NOW


def test_sanitize_reason_keeps_the_allowed_punctuation():
    clean = "reprice: a-b_c (1/2), fine."
    assert sanitize_reason(clean) == clean
    assert sanitize_reason(None) == ""


def test_sampler_first_call_due_then_period():
    t = [0.0]
    sampler = Sampler(60, clock=lambda: t[0])

    assert sampler.due("k") is True  # first call for a key is always due

    t[0] = 30
    assert sampler.due("k") is False

    t[0] = 59.9
    assert sampler.due("k") is False

    t[0] = 60.0
    assert sampler.due("k") is True

    # A different key has its own clock, independent of "k"'s.
    assert sampler.due("k2") is True
    t[0] = 61
    assert sampler.due("k2") is False
