"""The `research_notes` writer: two rows under one call id, and the discriminators."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.research.client import CallResult
from harness.research.notes import write_notes
from harness.research.spend import Usage

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


def _result(model, decision="proceed"):
    return CallResult(model=model, output={"decision": decision, "confidence": 0.8,
                                           "reason": "no news", "evidence_ids": []},
                      usage=Usage(1000, 100, 500, 0, 2), stop_reason="end_turn",
                      request_id="req_1", latency_ms=9000,
                      tool_calls=[{"type": "web_search_x", "name": "web_search", "query": "q"}],
                      snippets={"items": [], "truncated": False}, error=None)


def test_two_rows_under_one_call_id(db_session):
    call_id = uuid.uuid4()
    write_notes(db_session, call_id=call_id, kind="veto", subject_id="4242", effort="high",
                prompt_hash="a" * 64, features={"ttk_minutes": 90},
                results=[_result("claude-opus-5"), _result("claude-sonnet-5", "veto")],
                created_at=NOW)
    rows = db_session.execute(text(
        "select model, kind, subject_id, cost_usd, replay, arm, output->>'decision' as decision "
        "from research_notes where call_id = :c order by model"), {"c": call_id}).all()
    assert [r.model for r in rows] == ["claude-opus-5", "claude-sonnet-5"]
    assert {r.kind for r in rows} == {"veto"} and {r.subject_id for r in rows} == {"4242"}
    assert [r.decision for r in rows] == ["proceed", "veto"]
    assert all(r.replay is False and r.arm is None for r in rows)
    # opus: 1,000 in = $0.005; 100 out = $0.0025; 500 cache reads = $0.00025; 2 searches = $0.02
    assert rows[0].cost_usd == Decimal("0.027750")


def test_a_replay_row_is_marked_and_carries_its_arm(db_session):
    """D12: the veto study's frozen no-search re-runs must never join into H9, and `veto_h9`
    filters on exactly these two columns."""
    call_id = uuid.uuid4()
    write_notes(db_session, call_id=call_id, kind="study", subject_id="case_7",
                effort="medium", prompt_hash="b" * 64, features={},
                results=[_result("claude-opus-5")], created_at=NOW, replay=True,
                arm="opus_medium")
    row = db_session.execute(text(
        "select replay, arm from research_notes where call_id = :c"), {"c": call_id}).first()
    assert row.replay is True and row.arm == "opus_medium"


def test_an_errored_call_still_writes_its_row(db_session):
    """A `veto_error` still cost tokens and still has a request id; the row is the audit trail
    for money that was spent, so it is written whatever the call returned."""
    call_id = uuid.uuid4()
    failed = CallResult(model="claude-opus-5", output=None, usage=Usage(900, 0, 0, 0, 1),
                        stop_reason="pause_turn", request_id="req_2", latency_ms=30_000,
                        tool_calls=[], snippets={"items": [], "truncated": False},
                        error="pause_turn")
    write_notes(db_session, call_id=call_id, kind="veto", subject_id="1", effort="high",
                prompt_hash="c" * 64, features={}, results=[failed], created_at=NOW)
    row = db_session.execute(text(
        "select output, request_id from research_notes where call_id = :c"), {"c": call_id}).first()
    assert row.output == {"error": "pause_turn", "stop_reason": "pause_turn"}
    assert row.request_id == "req_2"
