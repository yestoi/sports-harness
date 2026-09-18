"""§1.7: was the book quiet, or did we lose the feed?"""
from datetime import datetime, timedelta, timezone

from harness.experiments.execution_viability.bookhealth import (
    BOOK_VERIFY_MAX, CAVEATS, CLASSIFICATIONS, HealthInput, classify, persist, sample_intervals,
    summarize,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
START, END = NOW, NOW + timedelta(minutes=10)


def _obs(**over) -> HealthInput:
    base = dict(ticker="KXNFLGAME-26SEP20DETBAL-DET", interval_start=START, interval_end=END,
                anchor_source="ws", anchor_id=9100, sid=7, seq_before=400, seq_after=400,
                events_in_interval=0, reconnect_at=START - timedelta(hours=2), first_gap_ts=None,
                last_event_ts=START - timedelta(minutes=30), reanchored=False, prints_in_interval=0)
    base.update(over)
    return HealthInput(**base)


def test_a_quiet_ticker_with_an_intact_subscription_is_confirmed_inactive():
    # No reconnect inside [START, END], no gap row, seq unchanged with zero events: the two are
    # consistent only with "nothing happened", so the classification is positive, not unknown.
    row = classify(_obs())
    assert row.classification == "inactive_confirmed"
    assert row.evidence["reconnect_inside_interval"] is False
    assert row.evidence["seq_advance"] == 0 and row.evidence["events_in_interval"] == 0


def test_a_missing_frame_is_confirmed_data_loss():
    row = classify(_obs(first_gap_ts=START + timedelta(minutes=3)))
    assert row.classification == "data_loss_confirmed"
    assert row.evidence["cause"] == "gap_row"


def test_a_sequence_skip_is_confirmed_data_loss_even_with_no_gap_row():
    # 5 events arrived but the sequence advanced 9: four frames never reached us.
    row = classify(_obs(events_in_interval=5, seq_after=409))
    assert row.classification == "data_loss_confirmed"
    assert row.evidence["cause"] == "seq_skip" and row.evidence["seq_advance"] == 9


def test_a_healthy_global_heartbeat_with_a_lost_per_ticker_subscription_is_data_loss():
    # The reconnect lands inside the interval: the socket was healthy globally, this ticker's
    # subscription was not. Global health is not per-ticker evidence (§1.7's third fixture).
    row = classify(_obs(reconnect_at=START + timedelta(minutes=4)))
    assert row.classification == "data_loss_confirmed"
    assert row.evidence["cause"] == "reconnect_inside_interval"


def test_a_reanchor_with_intervening_trades_is_unresolved_and_says_why():
    row = classify(_obs(reanchored=True, prints_in_interval=3, anchor_source="rest",
                        sid=0, seq_before=0, seq_after=0))
    assert row.classification == "unresolved"
    assert "queue" in row.evidence["queue_consequence"].lower()


def test_unknown_continuity_stays_unknown():
    row = classify(_obs(sid=0, seq_before=0, seq_after=0, anchor_source="rest"))
    assert row.classification == "unresolved"


def test_the_sample_is_bounded_and_deterministic():
    many = [_obs(interval_start=START + timedelta(minutes=i),
                 interval_end=START + timedelta(minutes=i + 1)) for i in range(1000)]
    first = sample_intervals(many, seed=20260916)
    assert len(first) == BOOK_VERIFY_MAX
    assert [o.interval_start for o in sample_intervals(many, seed=20260916)] == \
           [o.interval_start for o in first]


def test_the_summary_reports_three_counts_and_never_a_gate_percentage():
    rows = [classify(_obs()), classify(_obs(first_gap_ts=START + timedelta(minutes=1))),
            classify(_obs(reanchored=True, prints_in_interval=1, sid=0, seq_before=0, seq_after=0,
                          anchor_source="rest"))]
    counts = summarize(rows)
    assert counts == {"inactive_confirmed": 1, "data_loss_confirmed": 1, "unresolved": 1}
    assert set(counts) == set(CLASSIFICATIONS)
    assert len(CAVEATS) == 2 and all(isinstance(c, str) and c for c in CAVEATS)


def test_no_classification_changes_a_production_dirty_verdict():
    # §1.7(d): the module exposes no writer for `market_dirty_intervals` and no eligibility hook.
    import harness.experiments.execution_viability.bookhealth as bh

    text_ = open(bh.__file__).read()
    assert "market_dirty_intervals" not in text_ or "read" in text_
    assert "update" not in text_.lower().split("def persist")[0]


class _RecordingWriter:
    """T1's `ExperimentWriter` shape (`table` then `insert`), recording what it was handed.

    T3 creates `exp_book_health`; until then the real writer's `table()` refuses by name, so the
    only thing this task can assert is the rows `persist` hands over - never a real insert.
    """

    run_id = "exp-t5-test"

    def __init__(self) -> None:
        self.asked_for: str | None = None
        self.rows: list[dict] = []

    def table(self, name: str) -> str:
        self.asked_for = name
        return name

    def insert(self, table: str, rows) -> int:
        self.rows.extend(rows)
        return len(rows)


def test_persist_hands_the_writer_one_exp_book_health_row_per_classification():
    writer = _RecordingWriter()
    rows = [classify(_obs()), classify(_obs(first_gap_ts=START + timedelta(minutes=2)))]
    assert persist(writer, rows) == 2
    assert writer.asked_for == "exp_book_health"          # §2's row, and no other table
    assert [r["classification"] for r in writer.rows] == \
           ["inactive_confirmed", "data_loss_confirmed"]
    assert all(set(r) == {"run_id", "ticker", "interval_start", "interval_end", "classification",
                          "evidence"} for r in writer.rows)
    assert writer.rows[0]["run_id"] == "exp-t5-test"
    assert writer.rows[0]["interval_start"] == START and writer.rows[0]["interval_end"] == END


def test_the_interval_reads_are_valid_sql_and_answer_an_empty_tape(db_session):
    """The command's own per-interval reads, run against the schema rather than inspected.

    Nothing in this database has ever ticked, so the ticker has no WebSocket anchor at all: the
    two intervals come back with no sequence to continue, which is `unresolved` and not a quiet
    market. A typo in one of the bounded statements would otherwise surface first on production
    (the ops read-back), which is the one place this milestone must not discover it.
    """
    from harness.experiments.execution_viability.cli import _observe_intervals

    observed = _observe_intervals(db_session, "KXNFLGAME-26SEP20DETBAL-DET", START,
                                  START + timedelta(minutes=20), 600)
    assert [(o.interval_start, o.interval_end) for o in observed] == [
        (START, START + timedelta(minutes=10)),
        (START + timedelta(minutes=10), START + timedelta(minutes=20))]
    assert all(o.anchor_source == "rest" and o.sid == 0 and o.events_in_interval == 0
               and o.prints_in_interval == 0 and o.first_gap_ts is None for o in observed)
    assert summarize([classify(o) for o in observed]) == {
        "inactive_confirmed": 0, "data_loss_confirmed": 0, "unresolved": 2}
