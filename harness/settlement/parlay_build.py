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
into `parlay_slot_state` under this slot's key, because that is what the dashboard's Ideas section
renders (Task 12): a slot with no card and no reason would look like the builder forgot it.

**The slot's card is resolved by shape, never guessed from a card property** (review round 1,
Critical 1). `parlay_cards.kind` only ever holds `smart`/`lottery` -- there is no column for the
three-way shape (`smart`, `lottery`, `lottery_same_game`) this module actually schedules -- and
`correlated` cannot stand in for it: a plain cross-game `lottery` build is *routinely*
`correlated = True` (`harness.parlay.build` only dedupes games for `smart`), so keying the
same-game slot on `kind == "lottery" and correlated` let a cross-game lottery card satisfy the
same-game slot and starved it, while the plain lottery slot rebuilt (and spent a rationale call)
every hour. `_resolve_slot_card` fixes this two ways: once this stage has built a slot, its own
`parlay_slot_state` pointer (written *by shape*) is authoritative and no card property is
consulted at all; before that (a slot's first-ever run, or a card built by hand through
`harness/cli.py`
before this stage ever saw it), the fallback derives each candidate's *actual* shape from its
own legs -- exact, never a guess -- because a `smart` card is always cross-game by construction
and a `lottery`-kind card is `lottery_same_game` exactly when every leg shares one `game_id`.

**Where the per-slot state lives (ruling 13, user decision journal 211).** A dedicated
additive table, `parlay_slot_state` (`harness.db.models.ParlaySlotState`: `key`, `state` JSONB,
`updated_at`), added by this phase's own migration -- `harness.db.models.JobState`
("resumable cursors for the batch jobs") is unchanged and unrelated, and this module no longer
imports it. `state` holds `{"built": <card_id>}` for a built slot or `{"reason": <code>}` for an
empty one; `STAGE_REASON_CODES` below pins the closed set of reasons this stage can ever write
(review round 1, Important 3: never derived from another module's tuple position, so a later
reorder of `harness.parlay.build.REASON_CODES` cannot silently remap an already-written row's
meaning) -- `_write_reason` raises `KeyError` on anything outside that set, loudly, rather than
storing a code no reader can decode. `read_slot_state` below is the one place that decodes a row
back into the `{"built": ...}` / `{"reason": ..., "at": ...}` dicts the brief promises, and
`updated_at` is the "at". An earlier task draft reused `job_state`'s `BigInteger` column with a
private negative-integer encoding instead (see the task report); ruling 13 replaced it with this
dedicated table, so only `read_slot_state`/`_write_built`/`_write_reason`/`slot_key` are the
surface Task 12 or any other reader should ever use.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from harness.config.settings import get_settings
from harness.db.models import ParlayCard, ParlayLeg, ParlaySlotState
from harness.parlay.build import REASON_CODES as _BUILD_REASON_CODES
from harness.parlay.build import BuildRefused, build_card
from harness.parlay.config import load_config
from harness.parlay.placement import expire_cards, week_staked
from harness.settlement.job import Budget, StageResult, register_stage
from harness.weeks import CHICAGO, chicago_iso_week

log = logging.getLogger(__name__)

#: The six `(sport, shape)` slots (addendum §2.4). `nfl`/`ncaaf` cross `smart`/`lottery`/
#: `lottery_same_game`. `parlay_cards.kind` only ever holds `smart` or `lottery` (Task 4's
#: schema) -- there is no shape column -- so a card's *actual* shape is resolved from its own
#: legs by `_card_shape`, never guessed from `kind` alone or from `correlated` (review round 1,
#: Critical 1).
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

#: Every reason this stage can write, pinned explicitly as a closed tuple (review round 1,
#: Important 3, carried into the additive `parlay_slot_state` table by ruling 13): never derived
#: from `harness.parlay.build.REASON_CODES`'s tuple *position*, because a later insertion or
#: reordering in that tuple must not silently remap an already-written row's meaning --
#: `_write_reason` raises `KeyError` on any code outside this set instead. `not_built_yet`
#: (before the build time, or a declined replacement that is not replaced again),
#: `builder_failed` (an uncaught exception from `build_card`), `replacement_pending` (written the
#: moment a decline is noticed and a replacement is about to be attempted; §2.4).
STAGE_REASON_CODES: tuple[str, ...] = (
    *_BUILD_REASON_CODES, "not_built_yet", "builder_failed", "replacement_pending")


def slot_key(year: int, week: int, sport: str, shape: str) -> str:
    """The `job_state` key for one slot, one week (§2.4). Shared by the writer below and by
    Task 12's reader, so the format string is stated once."""
    return f"parlay_build:{year}-{week}:{sport}:{shape}"


def read_slot_state(session: Session, key: str) -> dict | None:
    """This slot's last-written outcome, decoded back into `{"built": id}` or
    `{"reason": code, "at": iso}` -- the one function a reader needs; the row's `state` JSONB is
    private to this module. `populate_existing=True` (review round 1, Minor 2): the Core
    `pg_insert` statements `_write_built`/`_write_reason` run do not expire this row in the ORM
    identity map, so a caller that already loaded it earlier in the same session -- this
    module's own `_resolve_slot_card`, mid-run -- must not read a stale value back."""
    row = session.get(ParlaySlotState, key, populate_existing=True)
    if row is None:
        return None
    state = row.state
    if isinstance(state, dict) and isinstance(state.get("built"), int):
        return {"built": state["built"]}
    code = state.get("reason") if isinstance(state, dict) else None
    if code not in STAGE_REASON_CODES:
        # An unrecognized reason (a row from a build this module no longer knows how to decode,
        # or a state with neither key) is reported as `"unknown"`, never the bare value Task
        # 12's sentence table cannot key on (review round 1, Minor 3).
        code = "unknown"
    return {"reason": code, "at": row.updated_at.isoformat()}


def _write_built(session: Session, key: str, card_id: int, now: datetime) -> None:
    state = {"built": card_id}
    stmt = pg_insert(ParlaySlotState).values(key=key, state=state, updated_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["key"],
                                      set_={"state": state, "updated_at": now})
    session.execute(stmt)


def _write_reason(session: Session, key: str, code: str, now: datetime) -> None:
    if code not in STAGE_REASON_CODES:
        raise KeyError(code)
    state = {"reason": code}
    stmt = pg_insert(ParlaySlotState).values(key=key, state=state, updated_at=now)
    stmt = stmt.on_conflict_do_update(index_elements=["key"],
                                      set_={"state": state, "updated_at": now})
    session.execute(stmt)


def _is_due(now: datetime, sport: str) -> bool:
    """Whether `sport`'s build weekday/hour (America/Chicago, §2.4) has passed for `now`'s own
    ISO week."""
    local = now.astimezone(ZoneInfo(CHICAGO))
    weekday, hour = BUILD_TIMES[sport]
    return (local.weekday(), local.hour) >= (weekday, hour)


def _card_shape(session: Session, card: ParlayCard) -> str:
    """The shape `card` was actually built as, from its own legs -- never from `correlated`
    (review round 1, Critical 1). A `smart` card is always cross-game by construction
    (`harness.parlay.build.build_card` skips an already-used game only for `kind == "smart"`),
    so it is never ambiguous. A `lottery`-kind card is the same-game shape exactly when every
    leg shares one `game_id`; a plain cross-game lottery card routinely has two legs on one game
    too (`correlated` is often `True` for it), which is exactly why `correlated` cannot be the
    discriminator."""
    if card.kind != "lottery":
        return "smart"
    # Rides `ix_parlay_legs_card_seq (card_id, seq)`'s leading column; one card's legs (3-8 rows)
    # per call, never a table walk.
    game_ids = {row[0] for row in
               session.query(ParlayLeg.game_id).filter_by(card_id=card.id).all()}
    return "lottery_same_game" if len(game_ids) == 1 else "lottery"


def _resolve_slot_card(session: Session, year: int, week: int, sport: str,
                       shape: str) -> ParlayCard | None:
    """The card this exact slot currently owns, or `None`.

    Once this stage has built (or replaced) a slot, its own `parlay_slot_state` pointer --
    written *by shape* -- is authoritative on every later run and no card property is ever
    consulted again. Before a slot's first `parlay_slot_state` row exists (a card built by hand
    through `harness/cli.py`
    before this stage ever ran, or this stage's very first pass), the fallback below finds the
    slot's card by deriving each same-`(year, week, sport, kind)` candidate's *actual* shape
    from its legs (`_card_shape`) rather than by guessing from `correlated`.
    """
    state = read_slot_state(session, slot_key(year, week, sport, shape))
    if state is not None and "built" in state:
        return session.get(ParlayCard, state["built"])

    kind = "lottery" if shape.startswith("lottery") else "smart"
    # Rides `ix_parlay_cards_week (year, week)`; one slot's handful of candidate cards for one
    # sport and kind, never a season-wide walk.
    candidates = session.query(ParlayCard).filter_by(
        year=year, week=week, sport=sport, kind=kind).order_by(ParlayCard.built_at.desc()).all()
    for card in candidates:
        if _card_shape(session, card) == shape:
            return card
    return None


def _expire_with_reason(session: Session, now: datetime, config) -> int:
    """`expire_cards`, plus the `declined_reason = 'expired'` the design (§2.4) and
    `parlay.yaml`'s own comment both promise but `harness.parlay.placement.expire_cards` does
    not yet write (neither on this base nor on Task 9's sibling branch, checked at dispatch).

    **Integration TODO (review round 1, concern 2 ruling): delete this function and call
    `expire_cards(session, now)` directly from `run_parlay_build` once
    `harness/parlay/placement.py`'s `expire_cards` writes `declined_reason = 'expired'` itself.**
    This is a duplicate of that function's own cutoff/status predicate and must not survive as
    a second, divergeable copy once the real fix lands -- it is kept for this round only because
    editing Task 9's file is out of this task's Files.
    """
    cutoff = now - timedelta(days=config.expiry_days)
    # A walk of `parlay_cards` filtered to `status = 'proposed'`: the table holds a handful of
    # rows a week, never a bulk scan.
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


def _run_slot(session: Session, settings, config, sport: str, shape: str, year: int, week: int,
             now: datetime, counts: dict) -> None:
    key = slot_key(year, week, sport, shape)
    kind = "lottery" if shape.startswith("lottery") else "smart"
    same_game = shape == "lottery_same_game"
    live = _resolve_slot_card(session, year, week, sport, shape)

    # Any card that is not an unreplaced `declined` void is this slot's card for the week --
    # `proposed`/`placed`/`alive` because it is still live, and `cashed`/`busted` too (review
    # round 1, Important 1): a settled card must keep the slot's `built` pointer, not lose it to
    # a fresh build the moment it grades.
    if live is not None and live.status != "void":
        _write_built(session, key, live.id, now)
        return

    if not _is_due(now, sport):
        _write_reason(session, key, "not_built_yet", now)
        return

    if week_staked(session, year, week) >= config.weekly_budget:
        _write_reason(session, key, "week_at_cap", now)
        return

    parent_id = None
    if live is not None:  # live.status == "void" here
        if live.declined_reason == "declined" and live.parent_card_id is None:
            parent_id = live.id            # one replacement, exactly once (§2.4)
            # §2.4: "records replacement_pending on the slot until the child exists" -- written
            # before the attempt below, so the slot never reads a stale `built` pointer to a
            # card the operator just declined while the replacement is being built.
            _write_reason(session, key, "replacement_pending", now)
        else:
            # Either `live` is itself a replacement that was declined too (`parent_card_id` is
            # set: "a declined replacement is not replaced again"), or it went void some other
            # way (expired). Either way this slot has had its one build, and its one
            # replacement if any, for the week.
            _write_reason(session, key, "not_built_yet", now)
            return

    try:
        # Review round 1, Important 2: `build_card` flushes the card and its legs *before*
        # `write_rationale` can raise, and a bare `except Exception` on a session left holding
        # that half-built `proposed` row -- or left in a failed transaction by a DB error --
        # would either leave a rationale-less card behind or abort every earlier slot's work and
        # this run's expiry along with it. The savepoint (the same pattern
        # `harness.settlement.parlay_grade._grade_card` uses) makes a raise here cost only this
        # one slot: nothing it did is visible outside the `with` block once it rolls back.
        with session.begin_nested():
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
    # Review round 1, Minor 4: `load_config()` re-parses `parlay.yaml` from disk on every call
    # (no cache); loaded once here and threaded down rather than re-read per slot and again
    # inside `_expire_with_reason`.
    config = load_config()
    counts = {"built": 0, "replaced": 0, "expired": 0, "refused": 0, "errors": 0, "skipped": 0}
    counts["expired"] = _expire_with_reason(session, now, config)
    year, week = chicago_iso_week(now)
    #: Yield when the stage has spent its own allowance out of the job's shared budget, the
    #: `parlay_grade.MIN_BUDGET_S` pattern: one slot must never run the job past its period.
    floor_s = budget.remaining_s() - STAGE_BUDGET_S
    exhausted = False
    for index, (sport, shape) in enumerate(SLOTS):
        if not budget.ok() or budget.remaining_s() <= floor_s:
            exhausted = True
            # Review round 1, Minor 6: the slots this run never looked at are counted, not just
            # silently absent from `counts["built"]` -- `job_runs.notes` can then tell "five
            # slots fine" apart from "five slots never checked".
            counts["skipped"] += len(SLOTS) - index
            break
        _run_slot(session, settings, config, sport, shape, year, week, now, counts)
    return StageResult(name="parlay_build", counts=counts, budget_exhausted=exhausted)


register_stage("parlay_build", run_parlay_build)
