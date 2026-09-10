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
        "select model, kind, subject_id, effort, prompt_hash, features, snippets, tool_calls, "
        "usage, cost_usd, latency_ms, request_id, created_at, replay, arm, "
        "output->>'decision' as decision "
        "from research_notes where call_id = :c order by model"), {"c": call_id}).all()
    assert [r.model for r in rows] == ["claude-opus-5", "claude-sonnet-5"]
    assert {r.kind for r in rows} == {"veto"} and {r.subject_id for r in rows} == {"4242"}
    assert [r.decision for r in rows] == ["proceed", "veto"]
    assert all(r.replay is False and r.arm is None for r in rows)
    # opus: 1,000 in = $0.005; 100 out = $0.0025; 500 cache reads = $0.00025; 2 searches = $0.02
    assert rows[0].cost_usd == Decimal("0.027750")
    # Every remaining column carries what the caller and the result gave it: a row that silently
    # dropped `usage` or `tool_calls` would leave the spend column unreconcilable.
    assert {r.effort for r in rows} == {"high"}
    assert {r.prompt_hash for r in rows} == {"a" * 64}
    assert all(r.features == {"ttk_minutes": 90} for r in rows)
    assert all(r.snippets == {"items": [], "truncated": False} for r in rows)
    assert all(r.tool_calls == [{"type": "web_search_x", "name": "web_search", "query": "q"}]
               for r in rows)
    assert all(r.usage == {"input_tokens": 1000, "output_tokens": 100,
                           "cache_read_input_tokens": 500, "cache_creation_input_tokens": 0,
                           "searches": 2, "code_execution_calls": 0} for r in rows)
    assert all(r.latency_ms == 9000 and r.request_id == "req_1" for r in rows)
    assert all(r.created_at == NOW for r in rows)


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


def test_usage_json_carries_the_code_execution_call_count(db_session):
    """Review round 1 minor: no schema change -- `code_execution_calls` lives inside the JSONB
    `usage` column, counted from `tool_calls` rather than from `Usage` (T4's dataclass is shared
    and carries no field for it)."""
    call_id = uuid.uuid4()
    result = CallResult(model="claude-opus-5", output={"decision": "proceed", "confidence": 0.8,
                                                        "reason": "ok", "evidence_ids": []},
                        usage=Usage(500, 50, 0, 0, 1), stop_reason="end_turn",
                        request_id="req_ce", latency_ms=5000,
                        tool_calls=[{"name": "code_execution", "code": "import json"},
                                    {"type": "web_search_x", "name": "web_search", "query": "q"},
                                    {"name": "code_execution", "code": "print(1)"}],
                        snippets={"items": [], "truncated": False}, error=None)
    write_notes(db_session, call_id=call_id, kind="veto", subject_id="1", effort="high",
                prompt_hash="d" * 64, features={}, results=[result], created_at=NOW)
    row = db_session.execute(text(
        "select usage from research_notes where call_id = :c"), {"c": call_id}).first()
    assert row.usage["code_execution_calls"] == 2
    assert row.usage["searches"] == 1


def test_a_schema_failure_stores_the_raw_text_alongside_the_error(db_session):
    """Review round 1 Important 3: the note's `output` carries the sanitized raw text on a
    `schema` failure, so it is distinguishable from a `pause_turn` or a `refusal`."""
    call_id = uuid.uuid4()
    failed = CallResult(model="claude-opus-5", output=None, usage=Usage(800, 300, 0, 0, 0),
                        stop_reason="end_turn", request_id="req_trunc", latency_ms=8000,
                        tool_calls=[], snippets={"items": [], "truncated": False},
                        error="schema", raw="{\"decision\": \"proceed\", \"confidence\":")
    write_notes(db_session, call_id=call_id, kind="veto", subject_id="1", effort="high",
                prompt_hash="e" * 64, features={}, results=[failed], created_at=NOW)
    row = db_session.execute(text(
        "select output from research_notes where call_id = :c"), {"c": call_id}).first()
    assert row.output == {"error": "schema", "stop_reason": "end_turn",
                          "raw": "{\"decision\": \"proceed\", \"confidence\":"}
