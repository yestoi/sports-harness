"""The weekly report annotator (addendum §1.5, roadmap item (e), R:232-234).

**Data-triggered, not clock-triggered** (review B, C2). There is no scheduled weekly report:
`harness report --week N` is a hand-run command and the only path that writes a `report_runs` row
with `provisional = false`. A 09:15 wall-clock trigger would race a person -- fire before the
operator runs the report and it annotates last week's row or none at all; fire after a re-run and
it cites a superseded rendering. So the pass looks for the newest non-provisional run of the
current ISO week that has no annotation, and annotates that.

**The model sees indices, never keys** (ruling B-I5). `harness/report/render_for_model.py` builds
the view; several tables key their rows on a ticker or a variant name, and F60 keeps venue text
out of a prompt.

**A bullet survives two checks or it is dropped** (ruling B-I4): it must carry a citation that
resolves to a real cell of this week's tables, and every number in it must appear in a cited
cell's rendered text. Dropped, never repaired: an annotation that is wrong about a number is
worse than no annotation.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ReportAnnotation, ReportRun
from harness.report.render_for_model import (BULLET_MAX, BULLETS_MAX, check_bullet,
                                             render_for_model)
from harness.report.tables import weekly_tables
from harness.research.client import PRIMARY_MODEL, ResearchClient, prompt_hash
from harness.research.notes import write_notes
from harness.research.spend import BudgetRefused, cost_usd, release_spend, reserve_spend
from harness.research.text import sanitize_model_text
from harness.research.worker import register_pass

log = logging.getLogger(__name__)

EFFORT = "high"
MAX_OUTPUT_TOKENS = 1024

SYSTEM_BLOCKS: list[dict] = [{
    "type": "text",
    "text": ("You are annotating one week of a betting research harness's fixed report tables. "
             "Write at most five short bullets, each at most 240 characters.\n\n"
             "Rows are addressed by index. Cite every claim as t<table>[<row>,<column>], for "
             "example t4[2,5]. Every number you write must appear in a cell you cite; a bullet "
             "with an uncited number, or with no citation at all, is discarded.\n\n"
             "Say what changed and what it means. Do not give advice, do not recommend a change "
             "to the strategy, and do not speculate about a cause the tables do not show. If the "
             "week is unremarkable, say so in one bullet rather than inventing five."),
    "cache_control": {"type": "ephemeral"},
}]

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {"bullets": {"type": "array", "maxItems": BULLETS_MAX,
                               "items": {"type": "string", "maxLength": BULLET_MAX}}},
    "required": ["bullets"],
    "additionalProperties": False,
}
PROMPT_HASH = prompt_hash(SYSTEM_BLOCKS)

_PENDING = text("""
    select r.id, r.year, r.week
    from report_runs r
    where r.provisional = false and r.year = :year and r.week = :week
      and not exists (select 1 from report_annotations a where a.report_run_id = r.id)
    order by r.generated_at desc
    limit 1
""")


def pending_report(session: Session, now: datetime) -> ReportRun | None:
    """The current ISO week's newest final report that has no annotation yet."""
    iso = now.isocalendar()
    row = session.execute(_PENDING, {"year": iso.year, "week": iso.week}).first()
    return session.get(ReportRun, row.id) if row is not None else None


def annotate_pass(session: Session, now: datetime, settings, client=None) -> dict:
    """One sweep. Returns `{"annotated", "bullets", "dropped"}`."""
    counts = {"annotated": 0, "bullets": 0, "dropped": 0}
    run = pending_report(session, now)
    if run is None:
        return counts
    if client is None:
        if not settings.has_anthropic_key():
            return counts
        client = ResearchClient(settings)

    view = render_for_model(weekly_tables(session, run.year, run.week, settings))
    try:
        reservation = reserve_spend(session, now, settings, "annotate", [PRIMARY_MODEL],
                                    searches=0)
    except BudgetRefused as refused:
        # Deliberately leaves the report pending: the annotator is weekly and the budget resets
        # tomorrow, so a refusal today is a delay and not a loss.
        log.info("annotator skipped on budget: %s", refused)
        return counts

    result = None
    try:
        result = client.call(model=PRIMARY_MODEL, system=SYSTEM_BLOCKS, user=view.text,
                             schema=OUTPUT_SCHEMA, effort=EFFORT,
                             max_output_tokens=MAX_OUTPUT_TOKENS, tools=())
    finally:
        release_spend(session, reservation,
                      {PRIMARY_MODEL: result.usage} if result is not None else {})

    write_notes(session, call_id=uuid.uuid4(), kind="annotate", subject_id=str(run.id),
                effort=EFFORT, prompt_hash=PROMPT_HASH,
                features={"year": run.year, "week": run.week}, results=[result],
                created_at=now)

    kept: list[str] = []
    if result.error is None and isinstance(result.output, dict):
        for bullet in (result.output.get("bullets") or [])[:BULLETS_MAX * 2]:
            cleaned = sanitize_model_text(bullet, BULLET_MAX)
            reason = check_bullet(view, cleaned)
            if reason is not None:
                log.info("annotator bullet dropped: %s", reason)
                counts["dropped"] += 1
                continue
            kept.append(cleaned)
            if len(kept) == BULLETS_MAX:
                break

    session.add(ReportAnnotation(report_run_id=run.id, model=PRIMARY_MODEL,
                                 prompt_hash=PROMPT_HASH, bullets=kept,
                                 cost_usd=cost_usd(PRIMARY_MODEL, result.usage),
                                 created_at=now))
    session.flush()
    counts["annotated"] = 1
    counts["bullets"] = len(kept)
    return counts


register_pass("annotate", lambda session, now, settings: annotate_pass(session, now, settings))
