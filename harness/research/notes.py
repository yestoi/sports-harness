"""Writing `research_notes`: two rows under one call id (addendum §1.4 "Records", R:225-229).

The record is the phase's audit trail for money that was spent and for a decision that will be
scored later, so an **errored** call writes its row too: it still consumed tokens, it still has a
request id, and a table that only recorded successes would make the spend column unreconcilable.

Nothing here renders. `snippets` is stored capped and is never re-rendered anywhere (F60), and
this table is on the snapshot builders' forbidden list (ruling B-I9).
"""
import uuid
from datetime import datetime
from typing import Sequence

from sqlalchemy.orm import Session

from harness.db.models import ResearchNote
from harness.research.client import CallResult
from harness.research.spend import cost_usd


def write_notes(session: Session, *, call_id: uuid.UUID, kind: str, subject_id: str,
                effort: str, prompt_hash: str, features: dict, results: Sequence[CallResult],
                created_at: datetime, replay: bool = False, arm: str | None = None) -> None:
    """One row per model of one call. `results` is the pair (primary, shadow) for the veto and a
    single result for the annotator and the parlay rationale.

    `effort` is the caller's own constant and is passed in rather than read off the result: the
    response carries no effort field, and inferring one would be a guess in a column the veto
    study's arms are selected by.
    """
    for result in results:
        error_body: dict = {"error": result.error, "stop_reason": result.stop_reason}
        if result.error_detail is not None:
            # Fix 39: the API's own message for an `anthropic.APIStatusError`, sanitized and
            # capped by the client already -- `error` stays the class name (ruling: the error
            # field is the class), and this is the diagnosable detail beside it. Fix 41 (journal
            # 112) writes the same column for a `max_tokens` response, where `error` is that
            # stop reason rather than a class name and the detail is the output token count the
            # response stopped at; either way the client decides both fields and this stores
            # whatever it set.
            error_body["error_detail"] = result.error_detail
        if result.raw is not None:
            error_body["raw"] = result.raw
        # `code_execution_calls` is no schema change -- it is a key inside the JSONB `usage`
        # column, sourced from `tool_calls` rather than from `Usage` (T4's dataclass is shared
        # and carries no field for it). Opus 5 wraps its searches in a `code_execution` sandbox
        # on this tool version, and the day-one worst-case re-fit reads these notes, so the
        # sandbox rounds it actually ran have to be visible here (review round 1 minor).
        code_execution_calls = sum(1 for c in result.tool_calls if c.get("name") == "code_execution")
        session.add(ResearchNote(
            call_id=call_id,
            model=result.model,
            kind=kind,
            subject_id=str(subject_id)[:64],
            effort=effort,
            prompt_hash=prompt_hash,
            features=features,
            snippets=result.snippets,
            tool_calls=result.tool_calls,
            output=result.output if result.error is None else error_body,
            usage={"input_tokens": result.usage.input_tokens,
                   "output_tokens": result.usage.output_tokens,
                   "cache_read_input_tokens": result.usage.cache_read_tokens,
                   "cache_creation_input_tokens": result.usage.cache_write_tokens,
                   "searches": result.usage.searches,
                   "code_execution_calls": code_execution_calls},
            cost_usd=cost_usd(result.model, result.usage),
            latency_ms=result.latency_ms,
            request_id=result.request_id,
            created_at=created_at,
            replay=replay,
            arm=arm,
        ))
    session.flush()
