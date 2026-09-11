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

**Fix 36 (journal 109, 2026-09-11 incident).** The pass used to build its view with
`render_for_model(weekly_tables(...))`: a full recomputation of every weekly report table, on
every 30 s research-worker sweep, until an annotation landed. On the NAS that render cannot
finish inside the statement timeout, so a report stuck in that state locked the worker into a
heavy query stream once a minute, forever, each attempt failing the same way. Two changes fix
that:

* The pass now renders from `report_cells`, the rows already written when the report was
  generated (`render_from_cells`, `harness/report/render_for_model.py`), and never calls
  `weekly_tables` at all -- reading a report_run_id-keyed set of rows is bounded regardless of
  how heavy the report was to compute in the first place.
* A pass that fails -- an exception, or a captured model-call error (what
  `harness/research/veto.py` calls its `veto_error` decision) -- backs the *report* off for an
  hour (a day after three failures), so a report that keeps failing stops being retried every
  sweep. The backoff is recorded in `job_state` under `annotate:<report_run_id>` (and a sibling
  `annotate:<report_run_id>:attempts` counter): `job_state.value` is `BigInteger` only, shaped
  for a resumable cursor (`harness/venues/kalshi/futures.py`'s `RESUME_KEY`), not a JSON
  payload, so the backoff key holds `next_attempt_at` as a Unix timestamp and the attempts key
  holds the count; the failing exception's class name is logged, never persisted (worker.py's
  own rule: a message can carry row content, a class name cannot).
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import JobState, ReportAnnotation, ReportRun
from harness.report.render_for_model import (BULLET_MAX, BULLETS_MAX, check_bullet,
                                             render_from_cells)
from harness.research.client import PRIMARY_MODEL, ResearchClient, prompt_hash
from harness.research.notes import write_notes
from harness.research.spend import BudgetRefused, chicago_day, cost_usd, release_spend, reserve_spend
from harness.research.text import sanitize_model_text
from harness.research.worker import register_pass

log = logging.getLogger(__name__)

EFFORT = "high"
MAX_OUTPUT_TOKENS = 1024

#: A report's first backoff, and what three failed attempts escalate to (fix 36).
BACKOFF_SHORT = timedelta(hours=1)
BACKOFF_LONG = timedelta(hours=24)
BACKOFF_STRIKES = 3

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
""")

#: Bound: one `report_runs.id`. Index: `report_cells`' own primary key
#: `(report_run_id, table_key, row_key, col_key)` both selects the rows and supplies the order,
#: so this reads a contiguous range of one index and sorts nothing.
_CELLS = text("""
    select table_key, row_key, col_key, text
    from report_cells
    where report_run_id = :run_id
    order by table_key, row_key, col_key
""")


class _ModelCallFailed(RuntimeError):
    """`client.call` returned a captured error (`result.error`) rather than raising -- the same
    outcome `harness/research/veto.py` records as its `veto_error` decision (a `pause_turn`, a
    schema mismatch, or similar). Raised here purely to route that outcome through the one
    backoff handler every other failure already goes through."""


def _backoff_key(run_id: int) -> str:
    return f"annotate:{run_id}"


def _attempts_key(run_id: int) -> str:
    return f"annotate:{run_id}:attempts"


def _backed_off(session: Session, run_id: int, now: datetime) -> bool:
    """Whether this report's `next_attempt_at` is still in the future. `now` is the sweep's own
    clock (`ResearchWorker._clock`, UTC-aware), and `_record_failure` writes the deadline from
    the same clock, so the two agree on what a Unix timestamp means."""
    state = session.get(JobState, _backoff_key(run_id))
    return state is not None and state.value is not None and state.value > now.timestamp()


def _record_failure(session: Session, now: datetime, run_id: int, exc: Exception) -> None:
    """Backs `run_id` off an hour, or a day once three attempts in a row have failed. Committed
    here, in its own transaction, because `ResearchWorker.run_once` rolls the whole session back
    when the pass it drove raises, and `annotate_pass` re-raises right after calling this --
    the backoff has to be durable before that rollback runs, not after (journal 109)."""
    attempts_key = _attempts_key(run_id)
    astate = session.get(JobState, attempts_key)
    attempts = (astate.value or 0) + 1 if astate is not None else 1
    if astate is None:
        session.add(JobState(key=attempts_key, value=attempts, updated_at=now))
    else:
        astate.value, astate.updated_at = attempts, now

    backoff = BACKOFF_LONG if attempts >= BACKOFF_STRIKES else BACKOFF_SHORT
    until = int((now + backoff).timestamp())
    bkey = _backoff_key(run_id)
    bstate = session.get(JobState, bkey)
    if bstate is None:
        session.add(JobState(key=bkey, value=until, updated_at=now))
    else:
        bstate.value, bstate.updated_at = until, now

    # Never `str(exc)`: an exception's message can carry SQL and row content
    # (harness/research/worker.py's own rule for the same reason). ERROR fires once, exactly at
    # the third strike -- a report still failing past that keeps backing off 24 h quietly rather
    # than paging the operator every sweep for a report already known to be stuck.
    if attempts == BACKOFF_STRIKES:
        log.error("annotator: report %s failed %d times (last: %s), backing off %s", run_id,
                  attempts, type(exc).__name__, backoff)
    else:
        log.info("annotator: report %s failed (%s), backing off %s (attempt %d)", run_id,
                 type(exc).__name__, backoff, attempts)
    session.commit()


def _clear_backoff(session: Session, run_id: int) -> None:
    session.query(JobState).filter(
        JobState.key.in_((_backoff_key(run_id), _attempts_key(run_id)))).delete(
        synchronize_session=False)


def pending_report(session: Session, now: datetime) -> ReportRun | None:
    """The current ISO week's newest final report that has no annotation yet and is not
    currently backed off (fix 36).

    "Current" is America/Chicago's week, not UTC's (review round 1): `spend.py`'s
    `chicago_day` is the same conversion `reserve_spend` already anchors its own week-lock to,
    and reusing it keeps this pass and the budget gate agreeing on when a week turns over. In
    the ~5 h window where UTC has rolled to Monday but Chicago has not (00:00-05:00 UTC
    Monday, i.e. Sunday evening/night CT), a raw `now.isocalendar()` would look for next week's
    number while a just-written Sunday-slate final report still carries this week's -- and once
    Chicago also rolls over, that report's week is behind "current" for good, so it would never
    be annotated.

    Ordinarily there is at most one non-provisional, unannotated run for the week; a re-run
    (`harness report` run twice) can leave more than one, and this returns the newest of those
    that is not backed off, falling through to an older one rather than stopping at the first
    backed-off row it finds.
    """
    run, _ = _pending_with_backoff_status(session, now)
    return run


def _pending_with_backoff_status(session: Session, now: datetime) -> tuple[ReportRun | None, bool]:
    """`pending_report`'s run, plus whether a still-pending report was skipped because it is
    currently backed off (used only for `annotate_pass`'s `"backed_off"` count)."""
    iso = chicago_day(now).isocalendar()
    rows = session.execute(_PENDING, {"year": iso.year, "week": iso.week}).all()
    skipped = False
    for row in rows:
        if _backed_off(session, row.id, now):
            skipped = True
            continue
        return session.get(ReportRun, row.id), skipped
    return None, skipped


def annotate_pass(session: Session, now: datetime, settings, client=None) -> dict:
    """One sweep. Returns `{"annotated", "bullets", "dropped", "backed_off"}`."""
    counts = {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 0}
    run, skipped_backoff = _pending_with_backoff_status(session, now)
    if run is None:
        counts["backed_off"] = 1 if skipped_backoff else 0
        return counts
    if client is None:
        if not settings.has_anthropic_key():
            return counts
        client = ResearchClient(settings)

    # One bounded query on `report_cells`' `report_run_id` index -- never `weekly_tables`, whose
    # recomputation is what locked the research worker into a query stream it could not finish
    # (fix 36; journal 109). A report with no stored cells (should not happen for a final run,
    # but the annotator must not crash a sweep on it) is skipped and logged, not rendered.
    cells = session.execute(_CELLS, {"run_id": run.id}).all()
    if not cells:
        log.info("annotator skipping report %s: no stored cells", run.id)
        return counts
    view = render_from_cells(cells)

    try:
        reservation = reserve_spend(session, now, settings, "annotate", [PRIMARY_MODEL],
                                    searches=0)
    except BudgetRefused as refused:
        # `reserve_spend` raised while holding the ISO-week advisory lock, which lives until
        # this transaction ends -- ending it here releases the lock on the error path too,
        # exactly as `veto_pass`'s own `BudgetRefused` handler does (fix round 2, I1).
        session.commit()
        # Deliberately leaves the report pending: the annotator is weekly and the budget resets
        # tomorrow, so a refusal today is a delay and not a loss, and not a backoff-worthy
        # failure of the report itself.
        log.info("annotator skipped on budget: %s", refused)
        return counts
    # The advisory lock lives until this transaction ends, so it is ended immediately: holding it
    # across the Anthropic call would block every other reservation on the ISO week for as long
    # as the call takes (fix round 2, I1). `harness/research/veto.py`'s `_call_pair` commits at
    # exactly this point for the same reason.
    session.commit()

    result = None
    call_exc: Exception | None = None
    try:
        result = client.call(model=PRIMARY_MODEL, system=SYSTEM_BLOCKS, user=view.text,
                             schema=OUTPUT_SCHEMA, effort=EFFORT,
                             max_output_tokens=MAX_OUTPUT_TOKENS, tools=())
    except Exception as exc:  # noqa: BLE001 - recorded as a backoff below, then re-raised
        call_exc = exc
    finally:
        release_spend(session, reservation,
                      {PRIMARY_MODEL: result.usage} if result is not None else {})

    if call_exc is not None:
        _record_failure(session, now, run.id, call_exc)
        raise call_exc

    write_notes(session, call_id=uuid.uuid4(), kind="annotate", subject_id=str(run.id),
                effort=EFFORT, prompt_hash=PROMPT_HASH,
                features={"year": run.year, "week": run.week}, results=[result],
                created_at=now)

    if result.error is not None:
        # A captured model-call error (veto.py's `veto_error` shape): recorded and retried
        # later, never written as a zero-bullet annotation -- that would satisfy `pending_report`
        # forever on a report that was never actually read by the model.
        _record_failure(session, now, run.id, _ModelCallFailed(result.error))
        counts["backed_off"] = 1
        return counts

    kept: list[str] = []
    if isinstance(result.output, dict):
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
    _clear_backoff(session, run.id)
    counts["annotated"] = 1
    counts["bullets"] = len(kept)
    return counts


register_pass("annotate", lambda session, now, settings: annotate_pass(session, now, settings))
