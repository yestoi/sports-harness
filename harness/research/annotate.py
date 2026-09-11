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
* A pass that fails anywhere after the pending run is found -- an exception, or a captured
  model-call error (what `harness/research/veto.py` calls its `veto_error` decision) -- backs
  the *report* off for an hour (a day after three failures), so a report that keeps failing
  stops being retried every sweep. The backoff is recorded in `job_state` under
  `annotate:<report_run_id>` (and a sibling `annotate:<report_run_id>:attempts` counter):
  `job_state.value` is `BigInteger` only, shaped for a resumable cursor
  (`harness/venues/kalshi/futures.py`'s `RESUME_KEY`), not a JSON payload, so the backoff key
  holds `next_attempt_at` as a Unix timestamp and the attempts key holds the count; the failing
  exception's class name is logged, never persisted (worker.py's own rule: a message can carry
  row content, a class name cannot).

**Fix round 1.** Two things the first cut of the above got wrong, found in review:

* The row-key protection (F60/B-I5) used to be recovered by *value* -- whichever stored column's
  text equalled the row's `row_key` -- which silently failed for any first-column value over 60
  characters (`row_key` truncates there; the matching cell's `text` truncates at 64), and table
  12's `variant/kind:reason` routinely is. `render_for_model.IDENTITY_COLUMNS` now looks it up by
  *name*, a table's first column being a structural fact of its builder, not a per-row guess.
* The backoff only wrapped `client.call`. Everything after it -- `write_notes`, the bullet
  checks, the `report_annotations` insert -- could raise past it, leaving a paid call's
  reservation release uncommitted (so a `session.rollback()` restored it as a phantom
  reservation) and the report retried, and re-paid for, every sweep. The reservation's release
  is now committed the moment the call returns (`release_spend`, `_call_pair`'s own pattern in
  `harness/research/veto.py`), independent of anything that fails afterward, and everything from
  the pending run to the annotation write is one failure domain: any exception in it records the
  backoff and is swallowed, never re-raised, with `counts["backed_off"] = 1` carrying the
  failure into the sweep's own report instead.

Also fixed: `render_from_cells` is now passed `titles` recovered from the run's own
`report_runs.markdown` (`render_for_model.titles_from_markdown`) rather than none at all, so the
model reads the same title, header and note lines a person does.

**Fix round 2.** Round 1's single failure domain was still one transaction: a *database*
failure -- `write_notes` or the annotation insert raising a `DataError` or similar, both
flushing writes -- left the session's transaction aborted, and `_record_failure`'s own SQL then
ran inside that aborted transaction and raised in turn, escaping with no backoff written at all
and the report retried, and re-paid for, every sweep regardless of round 1's fix. Each write
worth keeping now commits as soon as it lands (reserve, release, `write_notes`), and the shared
failure handler (`_fail`, inside `annotate_pass`) always rolls the session back before
`_record_failure` runs, putting its writes on a clean transaction rather than one still holding
a prior statement's error. Also: `render_from_cells`'s identity-column recovery now fails closed
(a table it cannot recover the identity column for is omitted from the view entirely, logged at
ERROR and counted in `ModelView.tables_omitted` and the sweep's own counts) rather than open,
and a failure past the point bullets have been checked now reports the real `"dropped"` count
instead of a fresh, zeroed one.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import JobState, ReportAnnotation, ReportRun
from harness.report.render_for_model import (BULLET_MAX, BULLETS_MAX, check_bullet,
                                             render_from_cells, titles_from_markdown)
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


def _write_backoff(session: Session, run_id: int, now: datetime, until: datetime) -> None:
    """Sets `annotate:<run_id>`'s `next_attempt_at`. Shared by `_record_failure` (which also
    bumps the attempts counter, since a failure might resolve itself on retry) and
    `_skip_empty_view` below (which does not: an empty view is a structural fact about a
    report's stored data that a retry cannot fix, so there is no strike count to escalate).
    Neither commits here -- each caller has its own reason to control exactly when."""
    bkey = _backoff_key(run_id)
    bstate = session.get(JobState, bkey)
    value = int(until.timestamp())
    if bstate is None:
        session.add(JobState(key=bkey, value=value, updated_at=now))
    else:
        bstate.value, bstate.updated_at = value, now


def _record_failure(session: Session, now: datetime, run_id: int, exc: Exception) -> None:
    """Backs `run_id` off an hour, or a day once three attempts in a row have failed. Committed
    here, in its own transaction: `annotate_pass` no longer re-raises after a failure (fix round
    1, Critical 2 -- re-raising left an already-called `release_spend`'s changes sitting
    uncommitted in the same session, so `ResearchWorker.run_once`'s rollback on the raising pass
    silently restored the reservation as a phantom, un-released and re-spent every retry), so a
    caller's own eventual commit would likely cover this anyway -- but committing here keeps the
    backoff durable independent of that, including when a test calls `annotate_pass` directly
    with no such caller at all."""
    attempts_key = _attempts_key(run_id)
    astate = session.get(JobState, attempts_key)
    attempts = (astate.value or 0) + 1 if astate is not None else 1
    if astate is None:
        session.add(JobState(key=attempts_key, value=attempts, updated_at=now))
    else:
        astate.value, astate.updated_at = attempts, now

    backoff = BACKOFF_LONG if attempts >= BACKOFF_STRIKES else BACKOFF_SHORT
    _write_backoff(session, run_id, now, now + backoff)

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


def _skip_empty_view(session: Session, now: datetime, run_id: int, tables_omitted: int) -> None:
    """A rendered view with no tables at all (fix round 3): every table `render_from_cells`
    could have shown was structurally unable to render -- an identity-column drift (new defect
    2) that happened to empty every table, or, theoretically, a `report_cells` set with no rows
    whose `table_key` is in `TABLE_KEYS` at all. Skipped like a report with no stored cells
    (`annotate_pass`'s own check just above this function's call site): no reservation, no call,
    no annotation. Unlike that case, though, this backs the report off a full day outright
    rather than leaving it to retry every sweep -- an empty render is a structural drift a retry
    cannot fix, not a transient failure that might clear on its own, so quiet 30 s retries would
    only repeat the same ERROR log forever for no benefit. One ERROR here, once, is the signal
    an operator needs to go looking."""
    log.error("annotator skipping report %s: rendered view has no tables (tables_omitted=%s)",
              run_id, tables_omitted)
    _write_backoff(session, run_id, now, now + BACKOFF_LONG)
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
    """One sweep. Returns `{"annotated", "bullets", "dropped", "backed_off", "tables_omitted"}`.

    **Fix round 2: every valuable write commits before anything that can still fail.** Round 1's
    single failure domain (reserve through the annotation insert, one `try/except`) left a gap a
    re-review found: a *database* failure between the call and the insert -- `write_notes` or
    the insert itself, both flushing writes, a `DataError` on an over-long field or a JSONB
    serialization failure being the obvious triggers -- aborts the transaction, and
    `_record_failure`'s own SQL then runs inside that aborted transaction and raises in turn,
    escaping with no backoff written at all. So each stage below commits as soon as its write is
    worth keeping (reserve, release, `write_notes`), and only the last stage -- the bullet
    checks and the `report_annotations` insert, which have nothing worth keeping if they fail --
    is left uncommitted going in. `_fail` below always calls `session.rollback()` before
    `_record_failure`: safe by construction, since nothing still uncommitted at the point it is
    called is worth keeping, and it puts `_record_failure`'s own writes on a clean transaction
    rather than one still holding a prior statement's error.
    """
    counts = {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 0, "tables_omitted": 0}
    run, skipped_backoff = _pending_with_backoff_status(session, now)
    # Set unconditionally, not only when nothing else is pending (fix round 1, Minor): a week
    # with two unannotated final runs where the newer is backed off and the older gets annotated
    # is still a sweep that skipped a backed-off report, and the operator's only view of that is
    # this count.
    counts["backed_off"] = 1 if skipped_backoff else 0
    if run is None:
        return counts
    if client is None:
        if not settings.has_anthropic_key():
            return counts
        client = ResearchClient(settings)

    # Captured now, not read off `run` again after a rollback below: SQLAlchemy expires ORM
    # objects on rollback, and re-reading `run.id`/`run.year`/`run.week` would cost a needless
    # round trip for values that cannot have changed since they were read here.
    run_id, run_year, run_week, run_markdown = run.id, run.year, run.week, run.markdown

    # One bounded query on `report_cells`' `report_run_id` index -- never `weekly_tables`, whose
    # recomputation is what locked the research worker into a query stream it could not finish
    # (fix 36; journal 109). A report with no stored cells (should not happen for a final run,
    # but the annotator must not crash a sweep on it) is skipped and logged, not rendered.
    cells = session.execute(_CELLS, {"run_id": run_id}).all()
    if not cells:
        log.info("annotator skipping report %s: no stored cells", run_id)
        return counts
    # `titles_from_markdown` reads the run's own stored Markdown for each table's title, header
    # and note (fix round 1, Important 1); a run with no markdown (should not happen for a
    # `provisional = false` run -- `harness report` always writes one) falls back to the bare
    # `== <key> ==` rendering `render_from_cells` already supports.
    titles = titles_from_markdown(run_markdown) if run_markdown else None
    view = render_from_cells(cells, titles=titles)
    # Fix round 2, new defect 2: a table `render_from_cells` could not recover the identity
    # column for is left out of the view entirely (fails closed) rather than shown with it
    # visible; carried into the sweep's own counts so an operator can see it happened.
    counts["tables_omitted"] = view.tables_omitted
    if not view.columns:
        # Fix round 3: a view with no tables at all -- every one omitted, or none stored to
        # begin with -- makes no call and writes no annotation, exactly like the no-cells check
        # above, but (unlike that check) backs the report off a day outright, since a retry
        # cannot fix a structural rendering gap the way it might a transient failure.
        _skip_empty_view(session, now, run_id, view.tables_omitted)
        counts["backed_off"] = 1
        return counts

    def _fail(exc: Exception) -> dict:
        """The one way this pass ends unhappily past this point: roll back whatever this stage
        left uncommitted, back the report off on the now-clean session, and return `counts` as
        it actually stands -- fix round 2, new defect 3: a fresh `{"dropped": 0, ...}` literal
        here used to discard bullets this same sweep had already, correctly, counted as
        dropped. Logs with `exc_info=exc` rather than `log.exception` (fix round 3): two of this
        function's call sites are outside any active `except` block (the raising `client.call`,
        reached only after its `try/except/finally` has already completed and cleared
        `sys.exc_info()`, and the captured `result.error` case), where `log.exception` prints no
        traceback and no exception type at all. Passing the exception object directly works on
        every call site, active `except` block or not, since it carries its own traceback."""
        log.error("annotator failed for report %s", run_id, exc_info=exc)
        session.rollback()
        _record_failure(session, now, run_id, exc)
        counts["annotated"] = 0
        counts["backed_off"] = 1
        return counts

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
    except Exception as exc:  # noqa: BLE001 - handled by `_fail` below, once release is tried
        call_exc = exc
    finally:
        # Always, and committed here, immediately, before anything downstream can fail: a
        # reservation committed (above) and then rolled back without its release eats the U4
        # cap for the rest of the America/Chicago day, shared across `veto`, `parlay` and
        # `study` too. `harness/research/veto.py`'s `_call_pair` releases and commits at exactly
        # this point, in its own nested `try`, for the same reason.
        try:
            release_spend(session, reservation,
                          {PRIMARY_MODEL: result.usage} if result is not None else {})
            session.commit()
        except Exception:  # noqa: BLE001 - a lost release must not lose the failure below
            log.exception("annotator could not release its reservation for report %s", run_id)

    if call_exc is not None:
        return _fail(call_exc)

    # `write_notes` is the audit of a call that has already been paid for, and it must survive
    # any later failure in this pass -- so it commits immediately, on its own, rather than
    # riding on the annotation insert's commit at the end (fix round 2, new defect 1). A failure
    # here (or in the insert below) rolls back only itself: nothing since the release above was
    # uncommitted going in.
    try:
        write_notes(session, call_id=uuid.uuid4(), kind="annotate", subject_id=str(run_id),
                    effort=EFFORT, prompt_hash=PROMPT_HASH,
                    features={"year": run_year, "week": run_week}, results=[result],
                    created_at=now)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    if result.error is not None:
        # A captured model-call error (veto.py's `veto_error` shape): recorded and retried
        # later, never written as a zero-bullet annotation -- that would satisfy
        # `pending_report` forever on a report the model never actually read. `write_notes`
        # above already committed, so the record of the call survives this.
        return _fail(_ModelCallFailed(result.error))

    try:
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

        session.add(ReportAnnotation(report_run_id=run_id, model=PRIMARY_MODEL,
                                     prompt_hash=PROMPT_HASH, bullets=kept,
                                     cost_usd=cost_usd(PRIMARY_MODEL, result.usage),
                                     created_at=now))
        session.flush()
        _clear_backoff(session, run_id)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    counts["annotated"] = 1
    counts["bullets"] = len(kept)
    return counts


register_pass("annotate", lambda session, now, settings: annotate_pass(session, now, settings))
