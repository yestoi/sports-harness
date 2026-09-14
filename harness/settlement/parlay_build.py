"""The scheduled parlay builder stage, and expiry (addendum §2.4, §1.3; D11).

**Why a settle stage, not a scheduler job (D11).** The serve process stays read-only -- it never
writes a card -- so the write has to live somewhere the operator already trusts with real
money. Running it as a stage of the hourly settle job means it inherits three things for free:
the job's own shared wall-clock budget (`Budget`), the `job_runs.notes` record of what it did
and how long it took (fix 47's `elapsed_s`), and the job's idempotence discipline -- a stage
that raises rolls its own session back and the next hour tries again, exactly like every other
stage in `STAGE_MODULES`.

**A decline is replaced once, and only once** (addendum §2.4). A `proposed` card the operator
declined (`status = 'void'`, `declined_reason = 'declined'`) gets one replacement card, linked by
`parent_card_id`. If that replacement is declined too, it is *not* replaced again -- there is no
column that says "this is a second-generation replacement", so the rule is enforced on shape: a
void card that already carries a `parent_card_id` of its own is never itself replaced. The slot
then has nothing left to try for the week and reads `not_built_yet`.

**An empty slot always has a written reason.** Every branch below -- not due yet, the week's $50
cap, a `BuildRefused` reason code, an unexpected exception -- ends by writing this run's outcome
into `job_state` under this slot's key, because that is what the dashboard's Ideas section
renders (Task 12): a slot with no card and no reason would look like the builder forgot it.

**Where the per-slot state lives.** `job_state` (`harness.db.models.JobState`) is the table
already named for exactly this ("resumable cursors for the batch jobs"), and it is untouched by
this phase's migration -- no schema change. Its `value` column is `BigInteger` only, so this
module's own encoding puts a built card's positive id there directly, and an empty slot's reason
code as a small negative index into `_REASONS` (`read_slot_state` below is the one place that
decodes either shape back into the `{"built": ...}` / `{"reason": ..., "at": ...}` dicts the
brief promises); `updated_at`, already a plain timestamp column on that table, is the "at". This
is a deviation from a plan that named no concrete storage for a JSON-shaped per-slot value with
no table of its own (see the task report) -- additive, and the only public surface Task 12 needs
is `read_slot_state`/`slot_key` below, not the encoding.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from harness.config.settings import get_settings
from harness.db.models import JobState, ParlayCard
from harness.parlay.build import REASON_CODES as _BUILD_REASON_CODES
from harness.parlay.build import BuildRefused, build_card
from harness.parlay.config import load_config
from harness.parlay.placement import expire_cards, week_staked
from harness.settlement.job import Budget, StageResult, register_stage
from harness.weeks import CHICAGO, chicago_iso_week

log = logging.getLogger(__name__)

#: The six `(sport, shape)` slots (addendum §2.4). `nfl`/`ncaaf` cross `smart`/`lottery`/
#: `lottery_same_game` -- three shapes, but `parlay_cards.kind` only ever holds `smart` or
#: `lottery` (Task 4's schema), so the two lottery shapes share one `kind` and are told apart
#: by `correlated` (see `_existing_card`): exact for `lottery_same_game` (a same-game build is
#: always `correlated = true`), a documented convention for plain `lottery` (which is usually,
#: but not provably always, uncorrelated -- addendum §2.4 does not add a column for it).
SLOTS: tuple[tuple[str, str], ...] = tuple(
    (sport, shape) for sport in ("nfl", "ncaaf")
    for shape in ("smart", "lottery", "lottery_same_game"))

#: America/Chicago weekday (Monday = 0) and hour a slot's build becomes due (§2.4).
BUILD_TIMES: dict[str, tuple[int, int]] = {"ncaaf": (4, 18), "nfl": (5, 18)}  # Friday, Saturday

#: The stage stops *starting* builds once it has spent this much of the settler's shared budget
#: (§2.4). A build already running finishes; the next run picks the slot up.
STAGE_BUDGET_S = 60
#: B-I9: the one model call a build makes is bounded, and a timeout falls back to the template.
RATIONALE_TIMEOUT_S = 30

#: Reason codes this stage can write beside `harness.parlay.build`'s own (design §2.2's nine
#: sentences minus `anchor_bye`/`gamelog_budget_spent`'s siblings, which are already there):
#: `not_built_yet` (before the build time, or a declined replacement that is not replaced
#: again), `builder_failed` (an uncaught exception from `build_card`), `replacement_pending`
#: (design §2.4's wording for the interval between noticing a decline and the replacement
#: existing -- never actually written by this stage, since the replacement build runs
#: synchronously in the same pass; kept in the encoding so Task 12's sentence table has
#: somewhere to point a code that a future asynchronous builder could write).
_EXTRA_REASONS = ("not_built_yet", "builder_failed", "replacement_pending")
_REASONS = _BUILD_REASON_CODES + _EXTRA_REASONS
_REASON_INDEX = {code: -(index + 1) for index, code in enumerate(_REASONS)}
_REASON_BY_VALUE = {value: code for code, value in _REASON_INDEX.items()}


def slot_key(year: int, week: int, sport: str, shape: str) -> str:
    """The `job_state` key for one slot, one week (§2.4). Shared by the writer below and by
    Task 12's reader, so the format string is stated once."""
    return f"parlay_build:{year}-{week}:{sport}:{shape}"


def read_slot_state(session: Session, key: str) -> dict | None:
    """This slot's last-written outcome, decoded back into `{"built": id}` or
    `{"reason": code, "at": iso}` -- the one function a reader needs; the encoding above is
    private to this module."""
    row = session.get(JobState, key)
    if row is None or row.value is None:
        return None
    if row.value > 0:
        return {"built": row.value}
    code = _REASON_BY_VALUE.get(row.value, str(row.value))
    return {"reason": code, "at": row.updated_at.isoformat()}


def _write_built(session: Session, key: str, card_id: int, now: datetime) -> None:
    stmt = pg_insert(JobState).values(key=key, value=card_id, updated_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["key"],
                                      set_={"value": card_id, "updated_at": now})
    session.execute(stmt)


def _write_reason(session: Session, key: str, code: str, now: datetime) -> None:
    value = _REASON_INDEX[code]
    stmt = pg_insert(JobState).values(key=key, value=value, updated_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["key"],
                                      set_={"value": value, "updated_at": now})
    session.execute(stmt)


def _is_due(now: datetime, sport: str) -> bool:
    """Whether `sport`'s build weekday/hour (America/Chicago, §2.4) has passed for `now`'s own
    ISO week."""
    local = now.astimezone(ZoneInfo(CHICAGO))
    weekday, hour = BUILD_TIMES[sport]
    return (local.weekday(), local.hour) >= (weekday, hour)


def _existing_card(session: Session, year: int, week: int, sport: str, kind: str,
                   same_game: bool) -> ParlayCard | None:
    """The most recently built card for this exact slot, or `None`. The chain's tail (a
    replacement is always built after the card it replaces) is always the most recent row, so
    ordering by `built_at` finds it without walking `parent_card_id` explicitly."""
    query = session.query(ParlayCard).filter_by(year=year, week=week, sport=sport, kind=kind)
    if kind == "lottery":
        query = query.filter(ParlayCard.correlated == same_game)
    return query.order_by(ParlayCard.built_at.desc()).first()


def _expire_with_reason(session: Session, now: datetime) -> int:
    """`expire_cards`, plus the `declined_reason = 'expired'` the design (§2.4) and
    `parlay.yaml`'s own comment both promise but `harness.parlay.placement.expire_cards` does
    not yet write (neither on this base nor on Task 9's sibling branch, checked at dispatch) --
    that module is Task 9's, out of this task's Files, so the gap is filled here rather than
    there: the same cutoff query `expire_cards` runs, read-only, before the call, and only the
    `declined_reason` of the cards it is about to void is written afterwards. Carried to Task 9
    and the controller in the task report."""
    cutoff = now - timedelta(days=load_config().expiry_days)
    stale = session.query(ParlayCard).filter(
        ParlayCard.status == "proposed", ParlayCard.built_at < cutoff).all()
    count = expire_cards(session, now)
    # `stale` holds the *same* ORM instances `expire_cards` mutated (one session, one identity
    # map keyed by primary key), so setting the attribute here -- never a bulk
    # `Query.update(synchronize_session=False)` -- keeps them in sync rather than leaving a
    # caller's already-loaded `ParlayCard` reading the pre-update value.
    for card in stale:
        if card.declined_reason is None:
            card.declined_reason = "expired"
    if stale:
        session.flush()
    return count


def _run_slot(session: Session, settings, sport: str, shape: str, year: int, week: int,
             now: datetime, counts: dict) -> None:
    key = slot_key(year, week, sport, shape)
    kind = "lottery" if shape.startswith("lottery") else "smart"
    same_game = shape == "lottery_same_game"
    live = _existing_card(session, year, week, sport, kind, same_game)

    if live is not None and live.status in ("proposed", "placed", "alive"):
        _write_built(session, key, live.id, now)
        return

    if not _is_due(now, sport):
        _write_reason(session, key, "not_built_yet", now)
        return

    if week_staked(session, year, week) >= load_config().weekly_budget:
        _write_reason(session, key, "week_at_cap", now)
        return

    parent_id = None
    if live is not None and live.status == "void":
        if live.declined_reason == "declined" and live.parent_card_id is None:
            parent_id = live.id            # one replacement, exactly once (§2.4)
        else:
            # Either `live` is itself a replacement that was declined too (`parent_card_id` is
            # set: "a declined replacement is not replaced again"), or it went void some other
            # way (expired). Either way this slot has had its one build, and its one
            # replacement if any, for the week.
            _write_reason(session, key, "not_built_yet", now)
            return

    try:
        card = build_card(session, settings, sport, week, kind, now, year=year,
                          same_game=same_game, parent_card_id=parent_id,
                          timeout_s=RATIONALE_TIMEOUT_S)
    except BuildRefused as exc:
        counts["refused"] += 1
        _write_reason(session, key, exc.reason_code, now)
        return
    except Exception:  # noqa: BLE001 - one slot must not take the settle job down
        log.exception("parlay build: slot %s failed", key)
        counts["errors"] += 1
        _write_reason(session, key, "builder_failed", now)
        return

    counts["built"] += 1
    if parent_id is not None:
        counts["replaced"] += 1
    _write_built(session, key, card.id, now)


def run_parlay_build(session: Session, now: datetime, budget: Budget) -> StageResult:
    """The stage. The signature is fixed at `(session, now, budget)` by `job.py`'s `StageFn`, so
    settings come from `get_settings()` here rather than from a parameter, and the clock is the
    budget's own -- `Budget` holds the `monotonic` the job injected, which a test can pin, and
    this module reads no clock of its own.
    """
    settings = get_settings()
    counts = {"built": 0, "replaced": 0, "expired": 0, "refused": 0, "errors": 0}
    counts["expired"] = _expire_with_reason(session, now)
    year, week = chicago_iso_week(now)
    #: Yield when the stage has spent its own allowance out of the job's shared budget, the
    #: `parlay_grade.MIN_BUDGET_S` pattern: one slot must never run the job past its period.
    floor_s = budget.remaining_s() - STAGE_BUDGET_S
    exhausted = False
    for sport, shape in SLOTS:
        if not budget.ok() or budget.remaining_s() <= floor_s:
            exhausted = True
            break
        _run_slot(session, settings, sport, shape, year, week, now, counts)
    return StageResult(name="parlay_build", counts=counts, budget_exhausted=exhausted)


register_stage("parlay_build", run_parlay_build)
