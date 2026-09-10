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
            output=result.output if result.error is None else {
                "error": result.error, "stop_reason": result.stop_reason},
            usage={"input_tokens": result.usage.input_tokens,
                   "output_tokens": result.usage.output_tokens,
                   "cache_read_input_tokens": result.usage.cache_read_tokens,
                   "cache_creation_input_tokens": result.usage.cache_write_tokens,
                   "searches": result.usage.searches},
            cost_usd=cost_usd(result.model, result.usage),
            latency_ms=result.latency_ms,
            request_id=result.request_id,
            created_at=created_at,
            replay=replay,
            arm=arm,
        ))
    session.flush()
