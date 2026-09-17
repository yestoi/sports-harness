"""The paper executor's 15 s loop: intake, books, fills, decisions, heartbeat (addendum §1).

The loop is the only part of phase 3 execution that touches a clock or a database. Everything
it decides is decided by `plan.plan_actions`, everything it infers about fills by
`fills.simulate_fills`, and everything it reads or writes goes through `store`; what is left
here is sequencing, per-order state and the guarantees that hold across steps.

Six things happen per step, in this order:

1. **The lock.** One advisory lock, taken and released inside the step. A second executor --
   a stray container, a manual `exec-once` beside the service -- returns `ExecStats(locked=False)`
   having written nothing at all, rather than racing the first one through the same orders.
2. **Intake.** Every candidate signal of an executed variant inside the intent TTL becomes an
   `intents` row. Nothing is filtered out of the record: whether the loop can act on it is the
   decision chain's business, and the intent is the evidence that it was offered the chance.
3. **Books.** One `BookState` per ticker, carried across loops and advanced from the tape. The
   step keeps the *previous* step's book as `base` and advances the cache to now, so the fill
   simulation can be handed the book its cursor actually points at while the decision chain
   reads the book as it is now.
4. **Fills.** Two independent tracks per order: the watched one, which stops when the order is
   cancelled or expires, and the `no_watcher` counterfactual, which runs to the order's natural
   expiry whatever we did. They share one tape pass and never share a fill.
5. **Actions.** `plan_actions` in list order inside one transaction, so a `Cancel` always
   precedes the `Place` that replaces it and the repriced order's capacity slot is free.
6. **The heartbeat.** One row, one commit per step. A step that raises rolls back, records the
   error on the heartbeat and lets the next loop try again.

No code path here sends an order, a quote or an RFQ answer in the posture this runs in. Since
Task 9 the loop's orders go to an `OrderGateway` rather than straight to `store`, and the one
it builds from `Settings.mode` is `PaperGateway`: it holds no transport, no writer and no
reader, no credential is ever read, and every fill is an inference drawn from the recorded
tape. The live gateway is dormant -- building it needs a production writer, and `make_writer`
refuses every one (§1.4) -- and in live mode the tape simulation is bypassed entirely, because
there the venue's own fills are the only fill source.

A replay executor (Task 13) runs the same six steps on a 15 s grid over a past range. The only
difference is the horizon: `self._at(now)` is None for the live loop and the grid instant for a
replay, and every read that could otherwise see past it -- the tape's head, a later run's
signals, a gap snapshot priced an hour afterwards, the book -- takes it. It also holds its own
advisory lock and writes no heartbeat, so a replayed day cannot stall the live service or
overwrite the row `exec-health` reads.
"""

import bisect
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from psycopg.errors import QueryCanceled
from sqlalchemy.orm import Session

from harness import execution, telemetry
from harness.db.models import IntentEpisode
from harness.execution import store
from harness.execution.book import (
    ZERO,
    BookState,
    advance_book,
    advance_book_at,
    book_age_s,
    load_book,
    load_book_at,
    newest_ws_connect,
    side_p,
)
from harness.execution.fills import (
    CROSS,
    FillResult,
    PaperOrder,
    SimState,
    fill_fee_fields,
    has_print,
    simulate_fills,
)
from harness.execution.gateway import (
    KalshiGateway,
    OrderGateway,
    PaperGateway,
    orders_by_venue_id,
    uses_the_simulator,
)
from harness.execution.plan import (
    EXPIRY,
    NO_BOOK,
    CapGate,
    Cancel,
    ExecSettings,
    Expire,
    MarketNow,
    OpenOrderView,
    Place,
    Skip,
    config_hash,
    confidently_matched,
    plan_actions,
    rebuild_state,
)
from harness.execution.risk import compute_drawdown, peak_equity_7d
from harness.execution.state import _state_columns, _state_of
from harness.ops import episodes
from harness.pricing.fees import KALSHI_FOOTBALL, fee_for_order

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
#: Loop durations kept in-process for the heartbeat's p95. Empty after a restart, deliberately:
#: a p95 carried across a restart would describe a process that is no longer running.
DURATION_WINDOW = 100
QUEUE_MODEL = "queue_model"
NO_WATCHER = "no_watcher"
#: How far before kickoff the no-watcher counterfactual stops, per the user's ruling of
#: 2026-09-14 15:38 CT (journal 206): "bound the NO_WATCHER deadline at
#: harness/execution/loop.py:900 (main) by kickoff minus 10 minutes". A literal ten minutes, not
#: `exec_kickoff_cutoff_min`: the invariant that measures it
#: (`harness/ops/checks.fills_outside_placement_window`) writes `interval '10 minutes'`, and a
#: settings change that moved one of the two without the other would make the check fail on
#: fills the loop had just been told to write.
NO_WATCHER_KICKOFF_MARGIN = timedelta(minutes=10)
#: The `order_events.kind` of a venue fill that arrived after its order left the book.
LATE_FILL = "late_fill"
#: The `fills.fill_method` of a fill the venue reported, as against one the queue model
#: inferred. Written only on the live path, which is dormant in this phase. Task 11 ruled on
#: what the exposure reads do with one: they count it. `store.MONEY_FILL_METHODS` is the list,
#: and it is what the daily stake cap, `load_positions` and the `positions` view all filter on,
#: because a real fill is the least deniable form of money put at risk.
VENUE = "venue"


@dataclass
class ExecStats:
    """What one step did. `locked=False` is the whole result of a step that never ran."""

    intents_new: int = 0
    placed: int = 0
    cancelled: int = 0
    expired: int = 0
    fills: int = 0
    nw_fills: int = 0
    skipped: int = 0
    errors: int = 0
    #: Tickers whose book could not be read this step (fix 60). Counted apart from `errors`
    #: and kept out of the heartbeat: the step itself succeeded, and `verify.md`'s heartbeat
    #: row expects `last_error` null when every other ticker is fine. The log line is the
    #: record of which ticker failed and why.
    book_errors: int = 0
    loop_ms: int = 0
    #: Fix 78b (the user's ruling of 2026-09-15 16:57 CT, decisions packet item 17 option d):
    #: where a loop's time actually went, in milliseconds, so the residual after fix 78 is
    #: measured rather than guessed at. `phase_tape_ms` is the per-ticker tape read (`_tape`),
    #: `phase_walk_ms` the pre-check that partitions the pending population and walks its
    #: counterfactuals, `phase_batch_ms` the set-based statements, and `phase_per_row_ms` the
    #: rows that still took a savepoint and an UPDATE of their own -- `per_row_n` of them.
    #: Floats because a phase can be a fraction of a millisecond and four truncations of one
    #: loop would not add up to it; published as `exec.phase_*`/`exec.per_row_n`.
    phase_tape_ms: float = 0.0
    phase_walk_ms: float = 0.0
    phase_batch_ms: float = 0.0
    phase_per_row_ms: float = 0.0
    per_row_n: int = 0
    #: Fix 78c: the same `per_row_n` population split by why each row is on that path, keyed
    #: by one of `PER_ROW_CAUSES`. The counts sum to `per_row_n` by construction -- every row
    #: the per-row loop takes is counted exactly once, under `PER_ROW_OTHER` if nothing more
    #: specific is known -- so `exec.per_row_*` answers "which cause is the residual" rather
    #: than only "how big is it".
    per_row_causes: dict = field(default_factory=dict)
    #: Fix 78c, the budget: rows this loop's rotation did not reach and therefore wrote nothing
    #: of, tickers it walked to the end, and the budget that was in force (ms). Published as
    #: `exec.walk_deferred_n` / `exec.walk_tickers_n` / `exec.walk_budget_ms`.
    walk_deferred_n: int = 0
    walk_tickers_n: int = 0
    walk_budget_ms: int = 0
    #: Fix 82 (the user's ruling of 2026-09-17, journal 262): the six phases of `_body` the
    #: fix 78b tape stops short of, so the loop's whole time is accounted for rather than left
    #: as an unattributed residual. `phase_intake_ms` is candidate_signals/insert_intents/
    #: episodes.upsert/load_intents, `phase_load_ms` working_orders/market_rows/
    #: newest_event_ts, `phase_books_ms` _advance_books and the markets build, `phase_decide_ms`
    #: everything up to and including plan_actions, `phase_place_ms` `_apply` alone (row 82's
    #: placement timer) and `phase_samples_ms` the order-watch samples and equity snapshot
    #: block. `phase_commit_prev_ms` is different in kind: a loop cannot measure its own
    #: count_open_orders + metric batch + write_heartbeat + `session.commit()`, because that
    #: block is what publishes this measurement, so it is measured on loop N and published on
    #: loop N+1 (held on the executor as `_last_commit_ms`), reading 0 on the first loop. All
    #: measurement only: no statement here is added, removed, reordered or changed, and none
    #: of these is written by a replay executor, exactly as the fix 78b four are not.
    phase_intake_ms: float = 0.0
    phase_load_ms: float = 0.0
    phase_books_ms: float = 0.0
    phase_decide_ms: float = 0.0
    phase_place_ms: float = 0.0
    phase_samples_ms: float = 0.0
    phase_commit_prev_ms: float = 0.0
    #: Fix 82: gauges of the loop that wrote them, like the phases beside them. `working_rows`
    #: is `len(working)`; `tape_tickers_n`/`tape_print_rows`/`tape_delta_rows` are the tickers
    #: `_tape` actually read this loop and the prints/deltas each read back, summed, counted
    #: right after the read and before any hold-back so a truncated batch's held-back prints
    #: still count; `expiring_n` is the population `_expiring(row, now)` is true for this loop
    #: -- the cohort the walk budget cannot defer.
    working_rows: int = 0
    tape_tickers_n: int = 0
    tape_print_rows: int = 0
    tape_delta_rows: int = 0
    expiring_n: int = 0
    locked: bool = True
    #: The first failure of the step, the same string the heartbeat's `last_error` carries.
    #: A live loop reads it off the heartbeat; a replay writes no heartbeat and needs the
    #: message here to fail its own command with it (fix round 1, I2). Real failures only:
    #: a truncated tape read is reported as `exec.tape_lag_tickers` and an INFO log line,
    #: never here, because verify.md's heartbeat row expects this null (fix 22 round 1, I1).
    last_error: str | None = None


@dataclass
class _MetricsAcc:
    """The `exec.*` counters `metric_sample_s` batches (design spec §3.1): totals since the
    previous sample, reset the moment a batch is written. Never touched by a replay executor
    (ruling: `test_replay_writes_no_telemetry`)."""

    intents_considered: int = 0
    placed: int = 0
    cancelled: dict = None
    skipped: dict = None
    filled_contracts: Decimal = ZERO
    loops_skipped: int = 0
    #: 6D §1.2: the fair-calculation age this loop saw, one entry per market the loop priced
    #: against, and the signal-to-order delay of each placement, per variant. Both are in-memory
    #: values the loop already holds -- neither costs a query -- and both are cleared with the
    #: rest of the accumulator when a batch is written. `fair_age_s` is a bounded `deque`
    #: (fix round 1, I3): the accumulator resets only when a batch is actually *written*
    #: (`_write_metric_batch`'s guard, then `reset()`), so a metric write or step commit that
    #: keeps failing would otherwise grow this list for as long as the failure lasts -- the
    #: `maxlen` bounds the deque, not the process, while that path is broken.
    fair_age_s: object = None
    signal_to_order_ms: dict = None

    def __post_init__(self) -> None:
        self.cancelled = self.cancelled or {}
        self.skipped = self.skipped or {}
        self.fair_age_s = self.fair_age_s if self.fair_age_s is not None else deque(maxlen=10_000)
        self.signal_to_order_ms = self.signal_to_order_ms or {}

    def reset(self) -> None:
        self.intents_considered = 0
        self.placed = 0
        self.cancelled = {}
        self.skipped = {}
        self.filled_contracts = ZERO
        self.loops_skipped = 0
        self.fair_age_s = deque(maxlen=10_000)
        self.signal_to_order_ms = {}


def _percentile(values: list[float], q: float) -> float | None:
    """The `percentile_disc` rule, in Python: the smallest observed value at or above the
    quantile. An observed value, never an interpolation, so the number a reader sees is a
    number the loop actually measured."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


@dataclass
class _TrackResult:
    """One track's outcome for one order: the state to persist and what was actually written."""

    state: SimState
    filled: Decimal
    inserted: int
    crossed: bool
    cross_written: bool = False


#: Fix 78c: why a row is on the per-row path. One name per cause, published as `exec.per_row_*`
#: gauges beside `exec.per_row_n`, which they sum to.
PER_ROW_OPEN = "open"            # an open order: the ruling keeps every one of them per-row
PER_ROW_FILL = "fill"            # the walk inserts a fill, which is not a column of `orders`
PER_ROW_CROSS = "cross"          # the walk crosses, and the crossing row is an insert too
PER_ROW_REANCHOR = "reanchor"    # a re-anchor or no-book row whose anchored walk still wrote
PER_ROW_BOOK_QUERY = "book_query"  # a cursor `_sim_book` has to load a historical book for
PER_ROW_LOAD_FAIL = "load_fail"  # the pre-check itself raised; the row keeps its own savepoint
PER_ROW_HELD = "held"            # kept for completeness: a held ticker is skipped, not per-row
PER_ROW_OTHER = "other"          # anything else, so the causes always sum to `per_row_n`
PER_ROW_CAUSES = (PER_ROW_OPEN, PER_ROW_FILL, PER_ROW_CROSS, PER_ROW_REANCHOR,
                  PER_ROW_BOOK_QUERY, PER_ROW_LOAD_FAIL, PER_ROW_HELD, PER_ROW_OTHER)


@dataclass
class _Walk:
    """One counterfactual walk the pre-check made, and the book it anchored on if it anchored.

    The book is carried so `_simulate_order` can check it is anchoring on the very same object
    before it takes the walk instead of repeating it: a walk from a different book is a
    different answer, and identity is the only test that cannot be fooled by two books that
    happen to be equal.
    """

    result: FillResult
    anchor: "BookState | None" = None


class _Budget:
    """The wall-clock one step may spend on the budgeted walk (fix 78c).

    A monotonic accumulator, not a deadline: the step's own injected `monotonic` is read
    before and after each row and the difference is added here, so a test clock drives the
    budget exactly as it drives the loop period, and a wall-clock step (NTP or otherwise)
    cannot end the walk early. `exhausted()` is checked *between* rows and never inside one --
    a row that has begun is always finished, because a half-walked row would be a row whose
    columns and inserts disagree. At exactly the budget the walk stops: `spent >= limit`, so a
    budget of 0 walks nothing at all and a budget that pays for exactly one row's walk pays for
    one, which is what makes the rotation tests deterministic.
    """

    def __init__(self, budget_ms) -> None:
        self.limit = max(0.0, float(budget_ms)) / 1000.0
        self.spent = 0.0

    def spend(self, seconds: float) -> None:
        self.spent += max(0.0, seconds)

    def exhausted(self) -> bool:
        return self.spent >= self.limit


@dataclass
class _Pending:
    """What `_batch_pending` did with this loop's cancelled pending population.

    `settled` are the rows whose whole step it has written (or that owed nothing), `results`
    the walk it made for a row that still needs the per-row path, `causes` why that row needs
    it, `walked` every row whose tape it actually walked -- which is the set that may never be
    deferred, because a walk that found a fill has to be persisted on the loop that found it --
    and `deferred` the rows the budget did not reach, which are written nothing at all.
    """

    settled: set[int] = field(default_factory=set)
    results: dict[int, _Walk] = field(default_factory=dict)
    causes: dict[int, str] = field(default_factory=dict)
    walked: set[int] = field(default_factory=set)
    deferred: set[int] = field(default_factory=set)


def _expiring(row, now: datetime) -> bool:
    """Whether this loop is the loop the row's counterfactual has to be closed on.

    The operative test, by the user's condition that expiry checks run on every row every
    loop: such a row is walked ahead of the rotation and is never deferred, so `nw_done` lands
    on exactly the loop it landed on before the budget existed. `_nw_deadline` is not used
    here: it is `min(expiry, kickoff - 10 min)` and is in the past for every cancelled row
    whose market has started, so testing it would put the whole population ahead of the
    rotation and leave nothing for the budget to ration.
    """
    return row.expiry is not None and row.expiry <= now


class Executor:
    """One executor process. `step()` is the whole of it; the scheduler only calls it.

    `clock` and `monotonic` are injected because the loop has two different notions of time and
    conflating them has bitten this harness before: `clock` is the UTC instant every decision
    and every row is stamped with, and `monotonic` measures how long a step took and how long
    it has been since the previous one, which a wall clock cannot do across an NTP step.

    `replay` partitions the whole loop: a replay executor reads replay signals, writes replay
    orders and never touches a live row, which is what lets Task 13 re-simulate a market the
    live executor is working.
    """

    #: Fix 78: whether a loop's cancelled pending counterfactuals are written set-based
    #: (`_batch_pending`) rather than one savepoint and one UPDATE each. A class attribute, not
    #: an instance one, because `tests/test_execution_pure.py` and
    #: `tests/test_execution_regressions.py` build an executor through `__new__` and set only
    #: what the frame under test reads -- and the production default has to be the one such an
    #: object gets. `tests/test_exec_loop.py` turns it off on an executor of its own to run the
    #: per-row path it compares column for column against this one; nothing else ever does.
    _batch_pending_writes = True

    #: Fix 78b: the phase timers read this, and `tests/test_execution_pure.py` /
    #: `tests/test_execution_regressions.py` drive `_simulate` on an executor built through
    #: `__new__`, which has no instance attributes at all. A class attribute is what such an
    #: object gets, and it is the production clock; `__init__` below shadows it with the
    #: injected one, so a test that drives the loop with a fake clock still measures that
    #: clock's milliseconds. `staticmethod`, because a plain function here would be bound as a
    #: method and handed `self` as its first argument.
    _monotonic = staticmethod(time.monotonic)

    #: Fix 78c: where the round-robin stopped last loop, as `(ticker, order_id)` -- process
    #: memory, so a restart simply begins again at the first ticker. Class attributes for the
    #: same reason `_monotonic` is one: `tests/test_execution_pure.py` and
    #: `tests/test_execution_regressions.py` drive `_simulate` on an executor built through
    #: `__new__`, which has no instance attributes at all.
    _nw_rotation: "tuple[str, int | None] | None" = None
    #: The rows the budget deferred and has not walked since. The recovery branch reads it:
    #: only a row that was actually left behind by the budget has a cursor to walk up to the
    #: dirty onset from, and every other row re-anchors exactly as it did before this fix.
    _nw_deferred: frozenset = frozenset()

    #: Fix 82: the previous loop's commit-block duration, held here because a loop cannot
    #: measure its own commit (see `ExecStats.phase_commit_prev_ms`). A class attribute for the
    #: same reason `_nw_rotation` is one: an executor built through `__new__` for
    #: `tests/test_execution_pure.py` / `tests/test_execution_regressions.py` never reaches
    #: `_locked_step` and so never needs this, but it still exists to read.
    _last_commit_ms: float = 0.0

    def __init__(self, settings, session_factory,
                 clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                 replay: bool = False, variants: list[str] | None = None,
                 gateway: OrderGateway | None = None) -> None:
        self.settings = settings
        self.exec_settings = ExecSettings.from_settings(settings)
        self.replay = replay
        self._factory = session_factory
        self._clock = clock
        self._monotonic = monotonic
        self._variant_names = list(variants if variants is not None else settings.exec_variants)
        self._engine = None
        #: The live book per ticker, carried across steps. A None value is a ticker the tape
        #: cannot anchor yet -- R10's `no_book` case, retried every loop.
        self.books: dict[str, BookState | None] = {}
        self._dirty_tickers: set[str] = set()
        #: Fix 26: the delta batch size in force per ticker, absent meaning `DELTA_BATCH_LIMIT`.
        #: It shrinks on a statement timeout and doubles back on a full read, so a cold ticker
        #: asks for what it can actually finish and a warm one returns to the cap in a few
        #: loops. Only tickers with a working order are kept; the rest are dropped each loop.
        self._delta_batch: dict[str, int] = {}
        #: Fix 78c's rotation cursor and deferred set; see the class attributes above.
        self._nw_rotation: tuple[str, int | None] | None = None
        self._nw_deferred: frozenset = frozenset()
        #: Fix 82: see the class attribute above; each executor starts as though the previous
        #: loop's commit cost nothing, which is what makes the first loop's own reading 0.
        self._last_commit_ms: float = 0.0
        self._durations: deque[int] = deque(maxlen=DURATION_WINDOW)
        self._last_mono: float | None = None
        # Task 12b telemetry: samplers keyed on this executor's own injected monotonic clock
        # (so a test's fake clock drives them exactly like it drives the loop period), the
        # counters they batch, and the once-per-process startup check.
        self._metric_sampler = telemetry.Sampler(settings.metric_sample_s, clock=self._monotonic)
        self._watch_sampler = telemetry.Sampler(settings.watch_sample_s, clock=self._monotonic)
        self._equity_sampler = telemetry.Sampler(settings.equity_sample_s, clock=self._monotonic)
        self._metrics_acc = _MetricsAcc()
        self._startup_checked = False
        #: Where an order actually goes. `PaperGateway` on the NAS and in every replay; the
        #: live one cannot be built in this phase (`make_writer` refuses every prod call), so
        #: an executor that asked for it fails at construction rather than at the first order.
        self.gateway = gateway if gateway is not None else self._build_gateway(settings)

    def _build_gateway(self, settings) -> OrderGateway:
        if getattr(settings, "mode", "paper") != "live":
            # `self.replay` partitions the gateway as it partitions the rest of the loop: a
            # replay executor's `cancel_all`, `reconcile` and `poll_fills` must read and move
            # replay rows, never the live book (review round 1, Important 1).
            return PaperGateway(replay=self.replay)
        # Unreachable in this phase: `make_writer` raises `LiveGuardRefused` on every prod
        # call (§1.4), so this returns nothing and the constructor raises. Imported here
        # because it is the one place that needs it.
        from harness.venues.kalshi.authed import make_writer

        writer = make_writer(settings, "prod")
        # The factory is what venue state is written through: an outage mark, a freeze or a
        # kill-switch trip must outlive the exception that caused it, and `_apply` rolls its
        # savepoint back on exactly that path (Task 10 fix round 1, Important 1).
        return KalshiGateway(writer, writer.reader, self._factory,
                             exec_settings=self.exec_settings, clock=self._clock)

    # --- the step ---------------------------------------------------------------------

    def step(self) -> ExecStats:
        """One loop. Never raises: a failure is rolled back and recorded on the heartbeat."""
        key = store.REPLAY_LOCK_KEY if self.replay else store.LOCK_KEY
        with self._bind().connect() as conn:
            if not store.try_lock(conn, key):
                log.info("executor lock held elsewhere; skipping this loop")
                return ExecStats(locked=False)
            # End the lock statement's own transaction: the advisory lock is session-scoped and
            # outlives it, and the ORM session below must be the one that owns the transaction.
            conn.commit()
            try:
                return self._locked_step(conn)
            finally:
                conn.rollback()
                store.unlock(conn, key)
                conn.commit()

    def _locked_step(self, conn) -> ExecStats:
        started = self._monotonic()
        now = self._clock()
        skipped_loops = self._loops_skipped(started)
        heartbeat = {"ws_last_event_at": None, "book_dirty_markets": 0, "last_error": None,
                     "tape_lag": []}
        stats = ExecStats()
        session = Session(bind=conn)
        try:
            try:
                self._body(session, now, stats, heartbeat, skipped_loops)
                # A step that survived one order's failure still committed, but a green
                # heartbeat over a hundred swallowed failures would be a lie.
                error = heartbeat["last_error"]
            except Exception as exc:  # noqa: BLE001 - the loop must survive any one step
                session.rollback()
                stats = ExecStats(errors=1)
                error = f"{type(exc).__name__}: {exc}"[:2000]
                log.exception("executor step failed")
            stats.loop_ms = int((self._monotonic() - started) * 1000)
            stats.last_error = error
            self._durations.append(stats.loop_ms)
            # Fix 82: the previous loop's commit-block duration, read before this loop's own
            # overwrites it below -- a loop cannot report a number it has not finished
            # computing yet. 0 on an executor's first loop (`_last_commit_ms`'s default).
            stats.phase_commit_prev_ms = self._last_commit_ms
            wrote_metrics = False
            commit_started = self._monotonic()
            try:
                # Fix round 1, C1: count_open_orders and the heartbeat write share one guarded
                # region again (as on the base before this task), and the metric batch runs in
                # its own savepoint so a database-level failure inside it cannot poison this
                # transaction and take the step's own orders/fills/heartbeat down with it.
                # Task 13: `exec_heartbeat` is one row for the whole harness and its
                # `last_loop_at` is what `exec-health` restarts the container on, so a replay --
                # whose clock is a past instant -- writes none of it, exactly as it writes no
                # telemetry (ruling 4). Its own step still commits.
                if not self.replay:
                    open_orders_count = store.count_open_orders(session, self.replay)
                    try:
                        with session.begin_nested():
                            wrote_metrics = self._write_metric_batch(
                                session, now, stats, heartbeat, open_orders_count)
                    except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails a step
                        log.exception("exec metric batch failed")
                        wrote_metrics = False
                    store.write_heartbeat(
                        session, last_loop_at=now,
                        open_orders=open_orders_count,
                        last_error=error, last_loop_ms=stats.loop_ms, p95_loop_ms=self._p95(),
                        loops_skipped=skipped_loops,
                        book_dirty_markets=heartbeat["book_dirty_markets"],
                        ws_last_event_at=heartbeat["ws_last_event_at"],
                        executor_version=execution.EXECUTOR_VERSION)
                session.commit()
                # Fix round 1, M3: only forget this window's counts once the batch that
                # reported them is actually durable; a rolled-back commit leaves them intact
                # for the next attempt instead of quietly under-reporting them forever.
                if wrote_metrics:
                    self._metrics_acc.reset()
            except Exception:  # noqa: BLE001 - the scheduler has no error path of its own
                # The step's own work goes with it: the heartbeat is written in the same
                # transaction. Losing one loop is survivable; killing the scheduler is not.
                log.exception("heartbeat write failed")
                stats.errors += 1
                session.rollback()
            finally:
                # Fix 82: measured whether this block succeeded or raised, so a failing commit
                # is still accounted for in the next loop's `exec.phase_commit_prev_ms` rather
                # than silently freezing it at a stale reading.
                self._last_commit_ms = (self._monotonic() - commit_started) * 1000
        finally:
            session.close()
        return stats

    def _tape_batch_min(self) -> int:
        """The smallest per-ticker delta batch in force, `DELTA_BATCH_LIMIT` when none is
        shrunk (fix 26). Taken from the executor's own state rather than from the heartbeat, so
        it reports the size the next read will use even on a step that failed before `_tape`."""
        return min(store.DELTA_BATCH_LIMIT,
                   min(self._delta_batch.values(), default=store.DELTA_BATCH_LIMIT))

    def _write_metric_batch(self, session: Session, now: datetime, stats: ExecStats,
                            heartbeat: dict, open_orders_count: int) -> bool:
        """`exec.*` metric_samples, once every `metric_sample_s` (Sampler("metrics")), never
        for a replay executor (the caller already guards that). Returns whether a batch was
        actually written, so the caller can defer resetting the accumulator until the whole
        step commits (fix round 1, M3)."""
        if not self._metric_sampler.due("metrics"):
            return False
        acc = self._metrics_acc
        samples: list[tuple[str, object, dict]] = [
            ("exec.loop_ms", stats.loop_ms, {}),
            ("exec.loops_skipped", acc.loops_skipped, {}),
            ("exec.open_orders", open_orders_count, {}),
            ("exec.dirty_markets", heartbeat["book_dirty_markets"], {}),
            # Fix 22 round 1, I1: how many tickers' delta reads hit `DELTA_BATCH_LIMIT` on the
            # loop this batch was written from -- the executor running behind the tape. It is a
            # gauge, like `dirty_markets`: a steady non-zero reading is a recorder writing
            # faster than the executor drains it, a single spike is a backlog being walked off.
            # It is not an error and never touches `last_error`, whose null the verify row
            # depends on.
            ("exec.tape_lag_tickers", len(heartbeat["tape_lag"]), {}),
            # Fix 66, M1: a ticker whose book has been unreadable for a while used to be
            # invisible to verify.md and the dashboard -- only `tests/test_exec_loop.py`
            # read `stats.book_errors`. Beside `tape_lag_tickers` for the same reason: not
            # an error, never touches `last_error`, but a steady non-zero reading is a
            # ticker an operator needs to go looking at.
            ("exec.book_errors", stats.book_errors, {}),
            # Fix 26: the smallest delta batch any ticker is reading with, `DELTA_BATCH_LIMIT`
            # when none has been shrunk. Read beside `tape_lag_tickers` it separates the two
            # ways of being behind: lag with the batch at the cap is a backlog being walked
            # off, lag with the batch on the floor is a ticker whose reads keep timing out.
            ("exec.tape_batch_min", self._tape_batch_min(), {}),
            # Fix 78b: the loop's own phase tape, from `ExecStats` above -- gauges of the loop
            # that wrote this batch, exactly as `exec.loop_ms` is, not sums over the sampling
            # window. Additive names only: no heartbeat column, no schema change, and nothing
            # here is an error, so `last_error` is untouched. They exist to answer "which phase
            # is the residual p95 in" -- the tape read, the walk, the batched writes or the
            # rows that still take a savepoint each -- with a measurement.
            ("exec.phase_tape_ms", round(stats.phase_tape_ms, 3), {}),
            ("exec.phase_walk_ms", round(stats.phase_walk_ms, 3), {}),
            ("exec.phase_batch_ms", round(stats.phase_batch_ms, 3), {}),
            ("exec.phase_per_row_ms", round(stats.phase_per_row_ms, 3), {}),
            ("exec.per_row_n", stats.per_row_n, {}),
            # Fix 78c (the user's ruling of 2026-09-16 07:20 CT, packet item 18): the same
            # population by cause, so the residual can be attributed rather than guessed at,
            # and the budget's own gauges. Every name is a gauge of the loop that wrote the
            # batch, like `per_row_n` beside them; additive names, no schema change, and none
            # of them is an error, so `last_error` is untouched. The causes are written for
            # every name every time, zero included, so a missing cause is a missing loop
            # rather than a cause that happened not to occur.
            *((f"exec.per_row_{cause}", stats.per_row_causes.get(cause, 0), {})
              for cause in PER_ROW_CAUSES),
            ("exec.walk_deferred_n", stats.walk_deferred_n, {}),
            ("exec.walk_tickers_n", stats.walk_tickers_n, {}),
            ("exec.walk_budget_ms", stats.walk_budget_ms, {}),
            # Fix 82 (the user's ruling of 2026-09-17, journal 262): the six `_body` phases
            # fix 78b's four stop short of, plus row 82's placement timer, and the counts
            # beside them -- gauges of the loop that wrote this batch, additive names, no
            # schema change, none of them an error. `phase_commit_prev_ms` is loop N-1's own
            # commit block, held on the executor because a loop cannot measure its own commit;
            # it reads 0 on an executor's first loop.
            ("exec.phase_intake_ms", round(stats.phase_intake_ms, 3), {}),
            ("exec.phase_load_ms", round(stats.phase_load_ms, 3), {}),
            ("exec.phase_books_ms", round(stats.phase_books_ms, 3), {}),
            ("exec.phase_decide_ms", round(stats.phase_decide_ms, 3), {}),
            ("exec.phase_place_ms", round(stats.phase_place_ms, 3), {}),
            ("exec.phase_samples_ms", round(stats.phase_samples_ms, 3), {}),
            ("exec.phase_commit_prev_ms", round(stats.phase_commit_prev_ms, 3), {}),
            ("exec.working_rows", stats.working_rows, {}),
            ("exec.tape_tickers_n", stats.tape_tickers_n, {}),
            ("exec.tape_print_rows", stats.tape_print_rows, {}),
            ("exec.tape_delta_rows", stats.tape_delta_rows, {}),
            ("exec.expiring_n", stats.expiring_n, {}),
            # §3 row 11: the counterfactual retry backlog, published so it is visible beside
            # criterion 4's `n_obs` and cannot silently become an exclusion. Nothing is closed,
            # so this is a queue depth, not an error.
            ("exec.nw_pending", heartbeat.get("nw_pending", 0), {}),
            ("exec.intents_considered", acc.intents_considered, {}),
            ("exec.placed", acc.placed, {}),
            ("exec.filled_contracts", acc.filled_contracts, {}),
        ]
        for quantile in (0.5, 0.95):
            samples.append(("exec.fair_age_s", _percentile(acc.fair_age_s, quantile),
                            {"q": f"p{int(quantile * 100)}"}))
        for variant_id, delays in acc.signal_to_order_ms.items():
            samples.append(("exec.signal_to_order_ms", _percentile(delays, 0.5),
                            {"variant": variant_id, "q": "p50"}))
        # Fix round 1, M2: always written, NULL when there is nothing to report yet
        # (`MetricSample.value` is nullable precisely for these two), so the verify.md query
        # "every exec.* name younger than 5 minutes" never reads a legitimate gap as a miss.
        samples.append(("exec.p95_loop_ms", self._p95(), {}))
        ws_last = heartbeat["ws_last_event_at"]
        # Fix 18: `ws_last` is the newest WS book event's timestamp, which carries the
        # exchange's clock and can run ahead of ours. A raw difference then goes negative and
        # breaks verify.md's `metric_samples.value < 0` invariant, so the age is clamped at
        # zero and the skew is kept -- non-negative -- as its own metric instead of lost.
        age = None if ws_last is None else (now - ws_last).total_seconds()
        samples.append(("exec.ws_event_age_s", None if age is None else max(age, 0.0), {}))
        samples.append(("exec.ws_event_ahead_s", None if age is None else max(-age, 0.0), {}))
        for reason, count in acc.cancelled.items():
            samples.append(("exec.cancelled", count, {"reason": reason}))
        for reason, count in acc.skipped.items():
            samples.append(("exec.skipped", count, {"reason": reason}))
        telemetry.record_many(session, "exec", samples, ts=now)
        return True

    def _at(self, now: datetime) -> datetime | None:
        """The horizon every read stops at: None live (the head), the grid instant in replay.

        One place, because the guarantee is one guarantee: a replay executor decides on exactly
        what the live loop could have seen at that instant and nothing taped afterwards.
        """
        return now if self.replay else None

    def _body(self, session: Session, now: datetime, stats: ExecStats, heartbeat: dict,
             skipped_loops: int = 0) -> None:
        s = self.exec_settings
        at = self._at(now)
        if not self.replay:
            self._metrics_acc.loops_skipped += skipped_loops
        variant_ids = store.resolve_variants(session, self._variant_names)
        if not self.replay:
            try:
                with session.begin_nested():
                    self._check_startup_events(session, variant_ids, now)
            except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails a step
                log.exception("exec startup telemetry failed")
        lower = now - timedelta(seconds=s.intent_ttl_s)

        # 2. Intake.
        intake_started = self._monotonic()
        candidates = store.candidate_signals(session, variant_ids, lower, self.replay, at)
        intent_keys: list = []
        stats.intents_new = store.insert_intents(session, candidates, now, self.replay,
                                                 intent_keys)
        if intent_keys and not self.replay:
            # 6D §1.7(c): the executor writes the episode where it writes the intent, under the
            # same cap. A replay writes none: these are live units.
            episodes.upsert(session, IntentEpisode, intent_keys, now,
                            episodes.gap_rule_s(s.period_s), kind="intent")
        intents, extras = store.load_intents(session, variant_ids, lower, self.replay, at)
        # Fix 82: measurement only -- see `ExecStats.phase_intake_ms`.
        stats.phase_intake_ms += (self._monotonic() - intake_started) * 1000

        # 3. Books and markets.
        load_started = self._monotonic()
        working = store.working_orders(session, self.replay)
        # Fix 82: measurement only -- see `ExecStats.working_rows`/`expiring_n`. Neither a
        # query nor a write: both are read off the `working` population just fetched.
        stats.working_rows = len(working)
        stats.expiring_n = sum(1 for row in working if _expiring(row, now))
        # §3 row 11: every counterfactual still running -- the whole pending population, not
        # only the tickers inside a retry backoff. Nothing is ever closed (ruling CR-4), so this
        # is a queue depth rather than an error, and it is published so criterion 4's population
        # cannot shrink without the metric saying so.
        heartbeat["nw_pending"] = sum(1 for row in working if not row.nw_done)
        rows = store.market_rows(session, ({i.venue_market_id for i in intents}
                                           | {w.venue_market_id for w in working}), at)
        ws_last = store.newest_event_ts(session, at)
        heartbeat["ws_last_event_at"] = ws_last
        # Fix 82: measurement only -- see `ExecStats.phase_load_ms`.
        stats.phase_load_ms += (self._monotonic() - load_started) * 1000
        # Section 2.2's "30 s without a ping", which is a live-only reprice rule: the gateway
        # needs the tape position this step already computed, and `PaperGateway` discards it
        # (Task 10 fix round 1, Important 3).
        self.gateway.observe_tape(ws_last)
        # F36: a recorder that stopped writing makes every ladder a stale one, and a stale
        # ladder that still looks tradeable is the failure this check exists to prevent.
        dead_recorder = (ws_last is None
                         or (now - ws_last).total_seconds() > s.book_max_age_s)
        books_started = self._monotonic()
        bases, recovering, unreadable = self._advance_books(
            session, {r.ticker for r in rows.values()},
            {r.ticker: vm_id for vm_id, r in rows.items()}, now, dead_recorder)
        stats.book_errors += len(unreadable)
        markets = {vm_id: self._market_now(row, dead_recorder or row.ticker in unreadable)
                   for vm_id, row in rows.items()}
        heartbeat["book_dirty_markets"] = sum(1 for m in markets.values() if m.dirty(now, s))
        # Fix 82: measurement only -- see `ExecStats.phase_books_ms`.
        stats.phase_books_ms += (self._monotonic() - books_started) * 1000
        if not self.replay:
            # 6D §1.2: fair-calculation age at the moment the loop used it. In memory, from the
            # `MarketNow` rows this step already built -- no query.
            self._metrics_acc.fair_age_s.extend(
                age for age in (market.fair_age_s(now) for market in markets.values())
                if age is not None)

        # 4. Fills.
        outcomes = self._simulate(session, working, markets, bases, recovering, now, stats,
                                  heartbeat)

        # 5. Decisions, applied in order.
        decide_started = self._monotonic()
        open_orders = [self._order_view(row, outcomes) for row in working
                       if outcomes.get(row.id, (row.status, row.filled_contracts))[0]
                       in store.OPEN_STATUSES]
        cfg = store.variant_configs(session, ({i.variant_id for i in intents}
                                              | {o.variant_id for o in open_orders}))
        intents = self._with_config(intents, cfg, "intent")
        open_orders = self._with_config(open_orders, cfg, "order")
        if not self.replay:
            self._metrics_acc.intents_considered += len(intents)
        positions = store.load_positions(session, self.replay)
        midnight = store.local_midnight(now, ZoneInfo(self.settings.tz_local))
        fills_today = store.load_fills_today(session, self.replay, midnight)
        state_by_variant = {variant: rebuild_state(open_orders, positions, fills_today, variant)
                            for variant in cfg}
        actions = plan_actions(intents, open_orders, markets, state_by_variant, cfg,
                               store.kill_active(session), now, s,
                               lagging=frozenset(heartbeat["tape_lag"]))
        # Fix 82: measurement only -- see `ExecStats.phase_decide_ms`.
        stats.phase_decide_ms += (self._monotonic() - decide_started) * 1000
        place_started = self._monotonic()
        self._apply(session, actions, intents, extras, markets, rows, now, stats, heartbeat)
        # Fix 82 (row 82's own timer): measurement only -- see `ExecStats.phase_place_ms`.
        stats.phase_place_ms += (self._monotonic() - place_started) * 1000

        if not self.replay:
            samples_started = self._monotonic()
            # Fix round 1, C1: each writer runs inside its own savepoint, so a database-level
            # failure rolls back only that writer's own work, never this step's orders, fills
            # or events sitting in the same transaction.
            try:
                with session.begin_nested():
                    self._write_order_watch_samples(session, working, markets, now)
            except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails a step
                log.exception("order watch samples failed")
            try:
                if self._equity_sampler.due("equity"):
                    with session.begin_nested():
                        self._write_equity_snapshots(session, variant_ids, now)
            except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails a step
                log.exception("equity snapshots failed")
            # Fix 82: measurement only -- see `ExecStats.phase_samples_ms`.
            stats.phase_samples_ms += (self._monotonic() - samples_started) * 1000

    # --- books ------------------------------------------------------------------------

    def _advance_books(self, session: Session, tickers: set[str], market_ids: dict[str, int],
                       now: datetime, dead_recorder: bool
                       ) -> tuple[dict[str, BookState | None], set[str], set[str]]:
        """Advance the cache to now and hand back the book each cursor still points at.

        `base` is the previous step's book, copied before the cache moves; the fill step gives
        it to the simulator, which walks its own copy. The cache itself is never handed out and
        never mutated by a simulation.

        The newest `ws_connect` is read once for the whole step (§0.3) and written onto the
        cached books: a book anchored before it kept folding in a new subscription's deltas, and
        the verdict has to land on the cached object so that the re-anchor branch can clear it.

        The third return is the tickers whose book could not be read at all this step. One
        ticker, not the step (fix 60): an unreadable book used to raise out of `_body` and cost
        every other ticker its whole loop -- which is what a snapshot body the reader rejected
        did to 3,426 consecutive loops from 2026-09-13 13:04 CT. Such a ticker keeps the cache
        entry it had (or none), gets no base, and joins `dirty`, and its caller marks its market
        dirty for this step: a book we could not read says nothing about the queue, so its
        orders hold rather than fill and no intent on it is placed. That is the union with 6B's
        anchoring: the guard wraps the `ws_connect`-aware load, and a book that cannot be read
        is never handed to `simulate_fills` as an empty one.

        §1.5's interval bookkeeping happens here too, because this is the one place that knows
        both which markets the step is stepping and which have just left it. `market_ids` maps
        ticker to `venue_market_id` and `dead_recorder` is the loop's own verdict about the
        recorder, both already in `_body`'s hand at the call site.
        """
        connected_at = newest_ws_connect(session, self._at(now))
        bases: dict[str, BookState | None] = {}
        re_anchored: set[str] = set()
        unreadable: set[str] = set()
        for ticker in sorted(tickers):
            cached = self.books.get(ticker)
            try:
                if cached is None:
                    book = self._book_now(session, ticker, now, None, connected_at)
                    self.books[ticker] = book
                    bases[ticker] = book
                else:
                    base = cached.copy()
                    bases[ticker] = base
                    advanced = self._book_now(session, ticker, now, cached, connected_at)
                    self.books[ticker] = advanced
                    if (advanced.anchor_id, advanced.source) != (base.anchor_id, base.source):
                        # `advance_book` re-anchors inside a single call when a gap is followed by
                        # a clean snapshot, so a gap and its resubscribe can both land between two
                        # 15 s steps and leave the book clean at either boundary. Comparing anchors
                        # is what catches that; comparing dirtiness across steps would not.
                        re_anchored.add(ticker)
            except Exception as exc:  # noqa: BLE001 - one ticker, not the step
                # The first failure of a loop carries its traceback; the rest of a loop's
                # failures are almost always the same one repeated (the `_tape` reader's rule).
                log.warning("book read failed for %s: %s: %s", ticker, type(exc).__name__, exc,
                            exc_info=not unreadable)
                unreadable.add(ticker)
                bases[ticker] = None
        dirty = ({t for t in tickers
                  if self.books.get(t) is not None and self.books[t].dirty} | unreadable)
        # A ticker that was dirty last step and is clean now, or one whose anchor moved inside
        # this step, is re-anchored by the fill step: the queue we believed in was built from a
        # tape with a hole in it.
        recovering = ({t for t in tickers if t in self._dirty_tickers} | re_anchored) - dirty
        self._dirty_tickers = dirty
        # One BookState per ticker ever traded would accumulate all season, and a dormant entry
        # would later be advanced from a very old `as_of`. `self.books` is pruned right after
        # `gone` is read, but `gone` is not derived from it (review round 1, I-1): a ticker whose
        # read *raises* on its very first step never becomes a key of `self.books` at all (the
        # cache assignment lives inside the `try`, only reached on success), while its
        # observation row was still opened unconditionally below because `market_ids` had its
        # id -- so deriving `gone` from `set(self.books) - tickers` misses exactly that market
        # when it later departs, and its row never closes.
        #
        # Fix 70 leak (journal 224 item 4): `gone` used to be read off `self._market_ids`, the
        # *previous* step's ticker -> venue_market_id map, carried in memory across steps. A
        # step that raises after this point rolls its own writes back with the rest of that
        # step's transaction, and a process restart starts every in-memory map back at empty --
        # either way, a market whose observation row is still open in the database can become
        # invisible to every later step's `gone`, and the row never closes (production rows
        # 865/866, journal 219, were exactly this). `gone` is now read from the database itself
        # -- the open `market_observation_intervals` rows for this replay flag, computed before
        # this step opens any of its own -- minus this step's own `market_ids`, so it cannot
        # drift from what is actually still open no matter what the loop's memory holds.
        # `self.books` stays a cache of what was last read, nothing here reads it for `gone`.
        currently_open = store.open_interval_market_ids(
            session, "market_observation_intervals", self.replay, now)
        gone = list(currently_open - set(market_ids.values()))
        self.books = {t: book for t, book in self.books.items() if t in tickers}

        # §1.5: dirtiness and observation are properties of the market, recorded as intervals
        # with a cause, and per-order time is derived from them at read time. A market the step
        # stepped has an open observation row; a market that is dirty has an open dirty row
        # carrying the book's own cause, `recorder_dead` when the loop has declared every ladder
        # stale and the book names no cause of its own, `book_unreadable` (row 69, 6B merge
        # review) when this ticker's own read failed this step and the book names no cause of
        # its own either, or `event_age` when this ticker's own tape has been silent past
        # `book_max_age_s` -- the same three routes `MarketNow.dirty` takes, so the elapsed
        # measure and the nominal accrual cover the same stretches.
        clean: list[int] = []
        for ticker in sorted(tickers):
            vm_id = market_ids.get(ticker)
            if vm_id is None:
                continue
            book = self.books.get(ticker)
            cause = None if book is None else book.dirty_cause
            if book is not None and (dead_recorder or ticker in unreadable):
                # Fix 60's guard already tells `_market_now` this market is `book_dirty` on
                # either route; naming a cause here keeps the interval ledger from
                # under-reporting it. A book that failed to read this step never touched the
                # cached copy (it keeps whatever cause, if any, it already carried), so
                # `book_unreadable` only fires when that cache is otherwise clean.
                cause = cause or ("recorder_dead" if dead_recorder else "book_unreadable")
            if cause is None and book is not None and \
                    book_age_s(book, now) > self.exec_settings.book_max_age_s:
                # `MarketNow.dirty` third route (spec F4): nothing applied a row for longer than
                # the ceiling, so the book is stale without being able to mark itself. Review F1.
                cause = "event_age"
            store.open_interval(session, "market_observation_intervals", vm_id, ticker, now,
                                self.replay)
            if cause is not None:
                store.open_interval(session, "market_dirty_intervals", vm_id, ticker, now,
                                    self.replay, cause=cause)
            else:
                # Collected and closed in the one statement below, never one per ticker: about
                # 110 statements per step on a 55-ticker loop is what the per-ticker shape would
                # cost against a p95 loop of 227 s (controller note, plan re-check).
                clean.append(vm_id)
        # A market that left the step's set is stamped closed at the last observation that saw
        # it (review I-6), so one whose last order closes while dirty cannot leave a row open
        # forever and §3 row 5's "no open row older than two hours at 01:00-08:00 CT" holds. It
        # goes in the same statement as the clean ones: both are "this market is not dirty as of
        # now", and the observation row is the only thing the two cases treat differently.
        store.close_intervals(session, "market_dirty_intervals", clean + gone, now, self.replay)
        store.close_intervals(session, "market_observation_intervals", gone, now, self.replay)
        return bases, recovering, unreadable

    def _book_now(self, session: Session, ticker: str, now: datetime,
                  cached: BookState | None,
                  ws_connect_at: datetime | None = None) -> BookState | None:
        """The ticker's book at `now`: the tape's head live, the past instant in replay.

        The two paths are the same two calls. `load_book`/`advance_book` run to the head of the
        tape with no upper `ts` bound, which is right for a loop whose clock *is* the head and
        catastrophic for one whose clock is three days behind it, so replay takes
        `load_book_at`/`advance_book_at` -- the same rules with the instant as their upper
        bound (fix round 1, I1 and I2). Both paths build once and advance afterwards: a replay
        that rebuilt each book from its anchor at every 15 s step would be quadratic in the
        day's tape.

        The reconnect instant goes to the load branch as well as the advance branch (review
        Important 2): the cache is pruned to the active ticker set each step, so a ticker that
        leaves and re-enters takes the load branch again, and every ticker takes it after a
        restart.
        """
        if self.replay:
            return (load_book_at(session, ticker, now, ws_connect_at) if cached is None
                    else advance_book_at(session, cached, now, ws_connect_at))
        return (load_book(session, ticker, now, ws_connect_at) if cached is None
                else advance_book(session, cached, now, ws_connect_at))

    def _market_now(self, row, book_dirty: bool) -> MarketNow:
        """`book_dirty` is the loop's own verdict on this ticker's book: the recorder is dead
        (every book) or the book could not be read at all this step (fix 60, this ticker)."""
        return MarketNow(
            venue_market_id=row.venue_market_id, ticker=row.ticker, fair_p=row.fair_p,
            fair_ts=row.fair_ts, fair_row_id=row.fair_value_id, staleness_s=row.staleness_s,
            stale_allowance_s=row.stale_allowance_s, feed_kind=row.feed_kind,
            best_bid_yes=row.best_bid, best_ask_yes=row.best_ask, mid_yes=row.venue_mid,
            book=self.books.get(row.ticker), book_dirty=book_dirty,
            matched=confidently_matched(row.match_status), match_key=row.match_key)

    # --- fills ------------------------------------------------------------------------

    def _simulate(self, session: Session, working, markets, bases, recovering, now: datetime,
                  stats: ExecStats, heartbeat: dict) -> dict[int, tuple[str, Decimal]]:
        """Run both tracks for every working order. One order's failure is one order's failure.

        A raise inside a step would otherwise cost every other order its fills for that loop,
        so each order runs inside a savepoint and a failure is counted and stepped over.

        In live mode (dormant) the queue model is bypassed entirely: the venue's own fills are
        the only fill source there (ruling B-I2), and asking a simulator what a real order did
        would be inventing a second answer to a question the venue has already settled.
        """
        if not uses_the_simulator(self.gateway):
            return self._venue_fills(session, working, now, stats, heartbeat)
        s = self.exec_settings
        tape_started = self._monotonic()
        tape, unread, lagging, deferred = self._tape(session, working, now, heartbeat, stats)
        stats.phase_tape_ms += (self._monotonic() - tape_started) * 1000
        stats.errors += len(unread)
        outcomes: dict[int, tuple[str, Decimal]] = {}
        # Fix 78: the cancelled pending population -- 5,055 rows at 13:50 CT, 4,822 of them on
        # dirty markets -- costs one statement per row per loop and a savepoint around each,
        # which is what took the loop's p95 past its bound. What those rows are owed is settled
        # set-based here, and `settled` is every row that needs no savepoint of its own: one
        # whose whole write this batch has just made -- the dirty-market accrual and close of
        # the first part, and since fix 78b the cursor-advance write of a clean-ticker row whose
        # step inserts nothing -- and one whose per-row write would have moved nothing at all.
        # All of them keep the outcome recorded above, which is the same pair `_simulate_order`
        # returns on either path: a cancelled order's status and filled total are the watched
        # track's, and the watched track of such a row is over.
        # Fix 78c: the one budget of this step, in wall-clock milliseconds, read once from the
        # settings exactly as `exec_period_s` is. It pays for the pre-check's walk below and
        # for the cancelled rows that still take the per-row path -- and for nothing else: the
        # tape read above, the books, the interval ledgers, the dirty-market accrual batch and
        # every open order are outside it by the user's ruling, which is what keeps
        # `nw_dirty_seconds` accruing exactly as it did before the budget existed.
        budget = _Budget(self.settings.exec_nw_budget_ms)
        stats.walk_budget_ms = int(self.settings.exec_nw_budget_ms)
        pending = self._batch_pending(session, working, markets, bases,
                                      recovering, tape, lagging,
                                      unread | deferred, now, stats, heartbeat, budget)
        deferred_rows: set[int] = set(pending.deferred)
        walked_here: set[int] = set()
        #: Cancelled rows this loop has actually run the per-row path for. The first one is
        #: always run, whatever the budget says (see the deferral below).
        residual_n = 0
        for row in working:
            outcomes[row.id] = (row.status, row.filled_contracts)
            if row.id in pending.settled:
                continue
            if row.id in pending.deferred:
                # The rotation did not reach this row this loop. Nothing this loop's walk or
                # its batches would have written is written -- not its cursor, its print
                # floor, its ledger, `nw_done` or the version stamp -- and the next loop's
                # rotation resumes where this one stopped. (`_tape`'s own `_note_backoff`
                # still clears a stale retry position on a ticker it read, deferred row or
                # not; that write happens with the budget and without it, so it moves no
                # value either way -- review rev-fix-78c, M2.) It is the same "sit the loop
                # out" CR-4 already gives a ticker whose tape went unread, applied to a row
                # this loop could not afford.
                continue
            if row.ticker in unread or row.ticker in deferred:
                # Unread: this ticker's tape read failed (fix 22: a statement timeout is the one
                # we have actually seen). Deferred: its counterfactual is inside its retry
                # backoff (§0.14). Either way this step read none of its tape, and simulating
                # against an empty one would move the print watermarks, the cross flags and --
                # in the no-watcher branch below -- `nw_done` itself, closing a past-expiry
                # track on a loop that saw nothing. Ruling CR-4 forbids exactly that close. The
                # whole ticker sits this loop out with its cursors where they are, and every
                # other ticker in this loop keeps its progress.
                continue
            # Fix 78b: what is left here is the population no batch can take -- every open
            # order, every row on a held ticker, and the cancelled rows that insert a fill or a
            # crossing, re-anchor, or could not be pre-checked. `per_row_n` and the time they
            # cost are published so the residual loop time can be attributed to a phase.
            market = markets.get(row.venue_market_id)
            if (budget.exhausted() and residual_n
                    and row.status not in store.OPEN_STATUSES and not row.nw_done
                    and not _expiring(row, now)
                    and not (market is not None and market.dirty(now, s))):
                # The user's ruling budgets "the clean-ticker walk **and the cancelled per-row
                # residual**", so a cancelled clean-ticker row the budget can no longer afford
                # is deferred here exactly as the rotation defers one it never reached --
                # whether or not the pre-check already walked it (controller ruling, fix round
                # 1, review I4(a)). Deferring a walked row is safe and costs only the walk: the
                # row's savepoint never opened, nothing of it has been written, and the next
                # loop walks the same tape from the same cursor and finds the same fill.
                # Never here: an open order, a dirty-market row or a row at its expiry -- the
                # three populations the ruling keeps out of the budget entirely -- and never a
                # row this loop's batches have already written (`pending.settled`, above).
                #
                # `residual_n` is the progress guarantee, and it is why the deferral is not
                # simply `budget.exhausted()`: at any real backlog the rotation spends the whole
                # budget before the per-row loop begins, so a bare test would defer every
                # cancelled per-row row on every loop and a row whose walk finds a fill would
                # never be written at all -- the fill delayed indefinitely rather than moved.
                # One such row a loop is therefore always run, which is the same "a row that has
                # begun is finished" overrun the walk allows, moved to the phase that costs
                # 40-110 ms a row instead of 0.5-0.8 ms. The loop's budgeted time is the budget
                # plus one per-row row. The population does not drain in rotation order: `working`
                # is `order by o.id` (`store._WORKING_ORDERS`), so the one row this loop's slot
                # goes to is whichever candidate has the lowest surviving `orders.id` among the
                # tickers the rotation has already walked -- oldest order first, independent of
                # ticker position (review rev-fix-78c-r1, M2).
                deferred_rows.add(row.id)
                stats.walk_deferred_n += 1
                continue
            row_started = self._monotonic()
            stats.per_row_n += 1
            cause = pending.causes.get(
                row.id,
                PER_ROW_OPEN if row.status in store.OPEN_STATUSES else PER_ROW_OTHER)
            stats.per_row_causes[cause] = stats.per_row_causes.get(cause, 0) + 1
            walked_here.add(row.id)
            if row.status not in store.OPEN_STATUSES:
                residual_n += 1
            try:
                with session.begin_nested():
                    outcomes[row.id] = self._simulate_order(
                        session, row, markets, bases, recovering, tape, lagging, now, stats,
                        walk=pending.results.get(row.id))
            except Exception as exc:  # noqa: BLE001 - one order, not the step
                log.exception("fill simulation failed for order %s", row.id)
                stats.errors += 1
                _note_error(heartbeat, f"order {row.id}: {type(exc).__name__}: {exc}")
            finally:
                elapsed = self._monotonic() - row_started
                stats.phase_per_row_ms += elapsed * 1000
                if row.status not in store.OPEN_STATUSES:
                    # Open orders are unbudgeted by the ruling, so their time is measured but
                    # never charged; every other per-row row is part of what the budget rations.
                    budget.spend(elapsed)
        # The rows still waiting for a walk, carried to the next loop: what this loop
        # deferred, less the rows whose walk this loop actually persisted, and pruned to the
        # rows this loop holds so a finished or vanished order cannot sit in the set for the
        # life of the process. A row the pre-check walked and the per-row stage then deferred
        # stays in the set -- its walk was thrown away -- and so does a row on a dirty market,
        # whose accrual moves no cursor. The recovery branch reads this set, and nothing else
        # does.
        live = {row.id for row in working if not row.nw_done}
        persisted = (pending.walked - deferred_rows) | walked_here
        self._nw_deferred = frozenset(
            ((set(self._nw_deferred) | deferred_rows) - persisted) & live)
        heartbeat["nw_deferred"] = len(self._nw_deferred)
        return outcomes

    def _batch_pending(self, session: Session, working, markets, bases, recovering, tape,
                       lagging, held: set[str], now: datetime, stats: ExecStats,
                       heartbeat: dict, budget: "_Budget") -> "_Pending":
        """Settle this loop's cancelled pending counterfactuals set-based (fix 78, 78b).

        Every row here is one the fill step has nothing to simulate for and one column-group to
        write: the order itself left the market, so only the `no_watcher` track is still
        running. There were 5,055 of them at 13:50 CT on 2026-09-15, 4,822 on dirty markets and
        6,387 by 16:01 CT, growing ~600 a day against expiries that stand until Monday. One
        savepoint and one UPDATE each is what took the loop's p95 to 8,423 ms against its
        7,500 ms bound; the first part took the median loop from 10.2 s to 7.6 s and left a p95
        of 13.9 s, which is what this second part is for.

        Three populations, and none of them changes a stored value:

        * **A dirty market.** The whole branch for such a row is `add_dirty_seconds(...,
          watched=False)` and `_close_nw_if_expired` (there is no watched accrual: a cancelled
          order is not resting against anything). Both are one statement for the whole
          population, with the same clamp, the same rows and the same version stamp.
        * **A clean ticker with nothing past its cursors.** `_writes_nothing` runs the same
          pure simulation the per-row path would and skips the write when every column that
          path would write already holds the value it would write.
        * **A clean ticker whose columns do move but whose step inserts nothing** (fix 78b, the
          user's ruling of 16:57 CT, packet item 17 option d). The cursor, the print floor, the
          ledger document, `nw_done` -- whatever moves, the row's whole write is the dict
          `_nw_columns` builds, which the pre-check has already built to decide the question
          above. Those dicts go to `store.update_orders_batch` as one `UPDATE ... FROM
          (VALUES ...)` per column set: same columns, same values, same stamp rule as the
          per-row `store.update_order` this replaces for them.

        A row that inserts a **fill** or a **crossing** is never in that third population: an
        insert is not a column of `orders`, so `_persist_track` has to run for it, and it keeps
        its savepoint and its per-row path with the walk the pre-check already made. So do the
        two re-anchoring branches (`queue_ahead_at_place is None`, a `recovering` ticker with a
        book), a cursor `_sim_book` would have to query for, and any row whose `nw_` state will
        not even load.

        An **open** order is never here at all. Its watched track is still resting, it accrues
        on the watched columns, and every write of it stays exactly where it was (the user's
        ruling). So is a row on a ticker this loop could not read (`held`): that whole ticker
        sits the loop out with its cursors where they are, which is ruling CR-4 and is decided
        by the caller for the batched and the per-row rows alike.

        A failure of a batch is one batch's failure: its own savepoint rolls it back, the rows
        it would have settled are not in the returned set, and each of them takes the per-row
        path it would have taken before this fix -- where a genuine per-row failure is counted
        and stepped over as it always was. Each failed batch counts one error of its own, as
        the per-row handler does, so a step that hit a database failure never publishes
        `exec.errors = 0` because the fallback happened to succeed (round 1, M1). The two
        batches take separate savepoints because they serve different rows: a lock wait on the
        accrual is not a reason to send the other population back to one statement each.

        The second return is the pre-check's own `FillResult` for each clean-ticker row it did
        *not* skip, keyed by order id. That row's counterfactual is simulated here to decide
        whether its write would move anything, and `_simulate_order` is handed the same result
        rather than walking the same prints and deltas a second time (round 1, M2). It is the
        same walk by construction -- same order, state, book, tape and deadline -- and
        `simulate_fills` copies both the state and the book, so neither run can perturb the
        other. A row the cursor-advance batch settled keeps its entry too, and needs it only on
        the path where that batch failed and the row falls back to its own savepoint.
        """
        pending = _Pending()
        if not self._batch_pending_writes:
            return pending
        walk_started = self._monotonic()
        s = self.exec_settings
        #: Rows whose dirty-market branch this batch owes the two statements to.
        dirty: list[int] = []
        #: Rows this loop owes nothing at all, batch or no batch.
        quiet: set[int] = set()
        #: `(order_id, columns)` for a clean-ticker row whose whole step is one `update_order`
        #: of those columns -- fix 78b's batched population, and since fix 78c the re-anchor
        #: and no-book rows whose anchored walk inserts nothing.
        moves: list[tuple[int, dict]] = []
        #: Fix 78c: the clean-ticker rows the budget rations, grouped by ticker because the
        #: rotation is by ticker (the tape window `_tape` reads is per ticker, so a ticker's
        #: rows are walked together), and the rows at their expiry, which are walked ahead of
        #: the rotation and are never deferred.
        by_ticker: dict[str, list] = {}
        expiring: list = []
        #: The dirty onset per (subscription, anchor position), read at most once a loop by
        #: the recovery branch (`_pre_onset_walk`).
        onsets: dict[tuple, datetime | None] = {}
        for row in working:
            if row.ticker in held or row.status in store.OPEN_STATUSES:
                continue
            market = markets.get(row.venue_market_id)
            if market is not None and market.dirty(now, s):
                # `nw_done` is the whole branch's remaining work: a finished track accrues
                # nothing and cannot be closed twice, so its per-row path writes nothing.
                if row.nw_done:
                    quiet.add(row.id)
                else:
                    dirty.append(row.id)
                continue
            if row.nw_done:
                continue
            if _expiring(row, now):
                expiring.append(row)
            else:
                by_ticker.setdefault(row.ticker, []).append(row)
        # The user's condition: expiry, version and lagging checks run on every row every loop.
        # These rows are walked first and are never deferred, so `nw_done` lands on the same
        # loop it landed on before the budget existed however little of the budget is left.
        # Their time is charged to the budget all the same (review rev-fix-78c, I4): fix 22's
        # lagging rule keeps a past-expiry row on a lagging ticker open, so this cohort can
        # grow without bound, and an uncharged cohort would put the whole pre-fix walk cost
        # back outside the budget. Charged, it shortens the rotation instead of lengthening
        # the loop.
        for row in expiring:
            row_started = self._monotonic()
            self._walk_pending(session, row, bases, recovering, tape, lagging, now, onsets,
                               pending, quiet, moves)
            budget.spend(self._monotonic() - row_started)
        # ... and then the rotation, ticker by ticker from where the last loop stopped, with
        # the budget checked between rows. A ticker may be left half walked; the rotation
        # resumes at the row it stopped on, not at the top of that ticker, so the rows behind
        # it are not walked twice and the ones ahead of it are not starved.
        order = self._rotation(sorted(by_ticker))
        stopped: tuple[str, int | None] | None = None
        for ticker in order:
            rows = by_ticker[ticker]
            index = self._resume_at(rows, ticker)
            while index < len(rows):
                if budget.exhausted():
                    stopped = (ticker, rows[index].id)
                    break
                row_started = self._monotonic()
                self._walk_pending(session, rows[index], bases, recovering, tape, lagging, now,
                                   onsets, pending, quiet, moves)
                budget.spend(self._monotonic() - row_started)
                index += 1
            if stopped is not None:
                break
            stats.walk_tickers_n += 1
        # Where the next loop starts: the row the budget stopped on, or -- when the whole
        # population was walked -- the first ticker of this loop's order, so a rotation that
        # never runs out begins where it began. Process memory by the ruling: a restart simply
        # starts again at the first ticker.
        if stopped is not None:
            self._nw_rotation = stopped
        elif order:
            self._nw_rotation = (order[0], None)
        for ticker, rows in by_ticker.items():
            for row in rows:
                if row.id not in pending.walked:
                    pending.deferred.add(row.id)
        stats.walk_deferred_n += len(pending.deferred)
        stats.phase_walk_ms += (self._monotonic() - walk_started) * 1000

        batch_started = self._monotonic()
        pending.settled = set(quiet)
        if dirty:
            try:
                with session.begin_nested():
                    store.add_nw_dirty_seconds_batch(session, dirty, self.settings.exec_period_s,
                                                     now)
                    store.close_nw_expired_batch(session, dirty, now)
            except Exception as exc:  # noqa: BLE001 - one batch, not the step
                log.exception("batched counterfactual writes failed for %d order(s)", len(dirty))
                stats.errors += 1
                _note_error(heartbeat, f"nw batch: {type(exc).__name__}: {exc}")
            else:
                pending.settled |= set(dirty)
        if moves:
            try:
                with session.begin_nested():
                    store.update_orders_batch(session, moves)
            except Exception as exc:  # noqa: BLE001 - one batch, not the step
                log.exception("batched counterfactual cursor writes failed for %d order(s)",
                              len(moves))
                stats.errors += 1
                _note_error(heartbeat, f"nw cursor batch: {type(exc).__name__}: {exc}")
            else:
                pending.settled |= {order_id for order_id, _ in moves}
        stats.phase_batch_ms += (self._monotonic() - batch_started) * 1000
        return pending

    def _rotation(self, tickers: list[str]) -> list[str]:
        """This loop's ticker order: sorted, rotated to start where the last loop stopped.

        Sorted rather than in `working` order so the rotation is over a stable sequence a
        cursor can be resumed into at all -- the row order the query returns is not a promise.
        A ticker that has since gone (settled, or all its rows finished) simply is not there,
        and `bisect` starts at the next one that still is, so the rotation never has to be
        repaired when the population changes underneath it.
        """
        if not tickers or self._nw_rotation is None:
            return tickers
        at = bisect.bisect_left(tickers, self._nw_rotation[0])
        if at >= len(tickers):
            return tickers
        return tickers[at:] + tickers[:at]

    def _resume_at(self, rows: list, ticker: str) -> int:
        """The index in this ticker's rows the rotation resumes at.

        Zero for every ticker except the one the budget stopped inside last loop, where it is
        the row it stopped on: the rows before it were walked last loop and the ones from it
        were not. The cursor names one ticker, so when the rotation later comes round to that
        ticker again -- on a loop that stopped somewhere else -- it walks it from the top.

        The scan leans on `store.working_orders`' own `order by o.id`: with the rows in id
        order the first row whose id is not below the cursor is the row the budget stopped on.
        Out of that order it can only resume *early* -- never past a row -- so the rotation
        would waste a walk rather than starve one, which is why it is a scan and not a bisect
        (review rev-fix-78c, M4).
        """
        if self._nw_rotation is None or self._nw_rotation[0] != ticker:
            return 0
        order_id = self._nw_rotation[1]
        if order_id is None:
            return 0
        for index, row in enumerate(rows):
            if row.id >= order_id:
                return index
        return 0

    def _walk_pending(self, session: Session, row, bases, recovering, tape, lagging,
                      now: datetime, onsets: dict, pending: "_Pending", quiet: set[int],
                      moves: list) -> None:
        """Pre-check one cancelled pending row and file the answer (fix 78, 78b, 78c).

        The walk itself is `_writes_nothing`; this is only the filing. A row that raises keeps
        the per-row path it had before this fix, where the same failure is counted against the
        step and stepped over -- nothing here has executed a statement or written anything, so
        there is nothing to undo. Either way the row is recorded as walked, which is what keeps
        the budget from deferring a row whose walk has already happened.
        """
        try:
            skip, walk, columns, cause = self._writes_nothing(
                session, row, bases, recovering, tape, lagging, now, onsets)
        except Exception:  # noqa: BLE001 - one row, and it keeps its own savepoint below
            log.debug("pre-checking order %s failed; it keeps the per-row path", row.id,
                      exc_info=True)
            pending.walked.add(row.id)
            pending.causes[row.id] = PER_ROW_LOAD_FAIL
            return
        pending.walked.add(row.id)
        if walk is not None:
            pending.results[row.id] = walk
        if cause is not None:
            pending.causes[row.id] = cause
            return
        if skip:
            quiet.add(row.id)
            return
        if columns is not None:
            moves.append((row.id, columns))

    def _writes_nothing(self, session: Session, row, bases, recovering, tape, lagging,
                        now: datetime,
                        onsets: dict) -> tuple[bool, "_Walk | None", dict | None, str | None]:
        """What this cancelled pending row's own step would do: nothing, one write, or more.

        The question is answered from the row and the tape, never from a guess. Fix 78 refused
        three branches of `_simulate_order` outright because each of them writes by
        construction; fix 78c reproduces two of them here, on the row's own copy of its
        counterfactual state, because the loop where a whole ticker recovers is the loop where
        every row on it re-anchors at once -- a savepoint and an UPDATE each, which is exactly
        the spike this part removes:

        * `queue_ahead_at_place is None` (R10/D7): with no book yet the step is
          `_close_nw_if_expired` and nothing else, so the write is `nw_done` or there is none.
          With a book, the track joins the back of it and the row's write is the anchoring
          columns plus whatever the walk from there moves.
        * a `recovering` ticker with a book: both tracks are taken down to what is actually
          resting -- never up (review I-2) -- and anchored to that book. Only the counterfactual
          is anchored here: the rows this method sees are cancelled, so the watched track is
          over and writes nothing whatever it is anchored to.

        The third, a cursor `_sim_book` would have to load a historical book from the database
        for, is still refused: it is a read this loop is trying not to make.

        The user's condition on the recovery branch is the `_pre_onset_walk` call: a row the
        budget deferred has a cursor behind the book its market was clean at, and re-anchoring
        it straight away would discard the clean stretch a row that had not been deferred would
        have consumed. Such a row walks its own tape up to the dirty onset first, and is
        re-anchored from the state that walk produced.

        Returns four things: whether the write would leave every column as it is (`_unchanged`,
        `nw_executor_version` included -- a write of the same values is a row version, a WAL
        record and a share of the 1,042 MB the table has grown to, but nothing reads it, so not
        making it changes no measured value); the walk, when one was made, so a row that is not
        skipped is simulated once for the two of us rather than once each (round 1, M2); the
        columns, when the row's whole step is that one write, so `_batch_pending` can send the
        population in one statement (fix 78b, 78c); and, for a row that has to go the whole way
        through `_simulate_order`, which of `PER_ROW_CAUSES` sent it there.
        """
        book = self.books.get(row.ticker)
        state = _state_of(row, "nw_")
        prints, deltas = tape.get(row.ticker, ([], []))
        nw_deadline = _nw_deadline(row, now)
        updates: dict = {}
        anchor: BookState | None = None
        pre: FillResult | None = None
        if row.queue_ahead_at_place is None:
            if book is None:
                # R10/D7 with no book yet: `_simulate_order` returns after
                # `_close_nw_if_expired`, so the row's whole write is that one column, or there
                # is none. The lagging rule does not reach that branch and does not reach this
                # one: a track with no queue is out of simulation, so nothing is closed early.
                if row.expiry is not None and row.expiry <= now:
                    return False, None, {"nw_done": True}, None
                return True, None, None, None
            queue = book.resting_at(row.side, row.prob)
            self._anchor_tracks((state,), book, queue=queue)
            anchor = book
            updates = {"queue_ahead_at_place": queue, "book_source": book.source,
                       "book_age_s": book_age_s(book, now), "book_first_seen_at": now}
        elif row.ticker in recovering and book is not None:
            if row.id in self._nw_deferred:
                pre = self._pre_onset_walk(session, row, state, prints, deltas, nw_deadline,
                                           now, onsets)
                if pre is not None:
                    state = pre.state
            resting = book.resting_at(row.side, row.prob)
            # Never up (review I-2); a track whose queue is None has no book to have joined
            # behind and a recovery says nothing about that (review IM-13).
            clamped = (None if state.queue_remaining is None
                       else min(state.queue_remaining, resting))
            self._anchor_tracks((state,), book, queue=clamped)
            anchor = book
        else:
            base = bases.get(row.ticker)
            if state.cursor_event_id is not None and (
                    base is None or state.cursor_event_id != base.last_event_id):
                return False, None, None, PER_ROW_BOOK_QUERY
        base = bases.get(row.ticker)
        sim_book = (anchor.copy() if anchor is not None
                    else (None if base is None else base.copy()))
        order = _paper_order(
            row, updates.get("queue_ahead_at_place", row.queue_ahead_at_place), now)
        result = simulate_fills(order, state, sim_book, prints, deltas, nw_deadline, NO_WATCHER)
        if pre is not None:
            # One track, two calls: the clean stretch up to the onset and the walk from the
            # anchor. `_persist_track` takes one result, so they are merged -- every fill of
            # both, the first crossing either saw, and the state the second left.
            result = FillResult(fills=[*pre.fills, *result.fills], state=result.state,
                                cross=pre.cross if pre.cross is not None else result.cross,
                                crossed=result.crossed)
        walk = _Walk(result, anchor)
        if result.fills or result.cross is not None:
            # An insert is not a column of `orders`, so `_persist_track` has to run for this
            # row and it keeps its savepoint and the walk this pre-check already made.
            cause = (PER_ROW_REANCHOR if anchor is not None
                     else (PER_ROW_FILL if result.fills else PER_ROW_CROSS))
            return False, walk, None, cause
        columns = {**updates,
                   **_nw_columns(row, result.state, row.nw_filled_contracts, result.crossed,
                                 lagging, now)}
        return _unchanged(row, columns), walk, columns, None

    def _pre_onset_walk(self, session: Session, row, state: SimState, prints, deltas,
                        nw_deadline: datetime, now: datetime,
                        onsets: dict) -> FillResult | None:
        """The clean stretch a deferred row still owes, walked up to the dirty onset (fix 78c).

        Two reads, each inside a savepoint of its own so a failure costs this row its walk and
        not the step its transaction:

        * the book this track's own cursor sat at -- `load_book_at`, today's historical branch
          of `_sim_book`, not the current base, which is a book from after the gap. A track that
          has consumed no delta has no cursor, and its position is its placement.
        * the first gap on that book's own subscription after the position it accounts for
          (`store.first_gap_ts(book.sid, book.gap_check_id)`), whose `ts` is the onset.

        The onset is the gap's instant rather than `market_dirty_intervals.started_at`, which is
        the instant a loop *noticed* the gap and is at or after it: the stretch between them is
        the post-gap tape the dirty verdict exists to distrust, and applying it here -- to a
        deferred row and to no other row -- is exactly the difference the budget may not make
        (review rev-fix-78c, I2). It is the *first* such gap, not the newest dirty interval, so
        a row deferred across two dirty cycles stops at the first one and cannot walk through
        the earlier dirty stretch as though the market had been clean (I3). `gap_check_id` is
        the position the anchor accounts for on either anchor kind, and `book._GAP_AFTER_AT` --
        the live loop's own *bounded* dirty test, used for a historical book rather than the
        live one -- is the same predicate (review rev-fix-78c-r1, M1).

        No book that early, or no gap after it, leaves the row to re-anchor exactly as it did
        before this fix.
        """
        try:
            with session.begin_nested():
                at = (row.placed_at if state.cursor_event_id is None
                      else store.event_ts(session, state.cursor_event_id))
                book = (None if at is None else
                        load_book_at(session, row.ticker, at, newest_ws_connect(session, at)))
        except Exception:  # noqa: BLE001 - one row's walk, not the step
            log.debug("loading the pre-onset book for order %s failed", row.id, exc_info=True)
            return None
        if book is None:
            return None
        # One read per (subscription, anchor position) per loop: every deferred row on one
        # ticker shares an anchor, so a whole recovering ticker costs one gap probe.
        key = (book.sid, book.gap_check_id or 0)
        if key not in onsets:
            try:
                with session.begin_nested():
                    onsets[key] = store.first_gap_ts(session, key[0], key[1], now)
            except Exception:  # noqa: BLE001 - one row's walk, not the step
                log.debug("reading the gap after %s for order %s failed", key, row.id,
                          exc_info=True)
                onsets[key] = None
        onset = onsets[key]
        if onset is None:
            return None
        return simulate_fills(_paper_order(row, row.queue_ahead_at_place, now), state, book,
                              prints, deltas, min(nw_deadline, onset), NO_WATCHER)

    def _venue_fills(self, session: Session, working, now: datetime, stats: ExecStats,
                     heartbeat: dict) -> dict[int, tuple[str, Decimal]]:
        """The live fill step (dormant): `fills` and `ledger(kind='fill')` from `poll_fills`.

        Everything about the row is ours except the trade: the price is read back into the
        order's own side space off *our* `side` rather than the venue's word for it, the fee is
        this harness's own model of the schedule, and the fill is keyed on the venue's trade id
        so that re-polling the same window inserts nothing twice.

        Two things the paper path has are gone here, by construction rather than by oversight:
        there is no `no_watcher` counterfactual for a live order (that track is the simulator's,
        and a real order has only the one history), and a fill cannot be attributed to a print
        on the tape.

        The running totals are carried in `outcomes` rather than read back off the ORM row, so
        two fills on one order in one poll add up whatever SQLAlchemy does with the Core UPDATE
        underneath (review round 1, minor).
        """
        outcomes: dict[int, tuple[str, Decimal]] = {
            row.id: (row.status, row.filled_contracts) for row in working}
        # The window reaches back to the last live fill we hold, not to local midnight
        # (Task 11 ruling): `reconcile` counts the fills it finds and writes none of them, so a
        # fill that landed while we were down is only ever *recorded* by the first poll whose
        # window covers it -- and a midnight anchor loses exactly the ones that straddled it.
        # Re-polling a window we have already seen inserts nothing twice: `fills` is keyed on
        # the venue's trade id.
        since = self.gateway.fills_since(session, now)
        try:
            venue_fills = self.gateway.poll_fills(session, since, now)
        except Exception as exc:  # noqa: BLE001 - one poll, not the step
            log.exception("polling venue fills failed")
            stats.errors += 1
            _note_error(heartbeat, f"poll_fills: {type(exc).__name__}: {exc}")
            return outcomes
        rows = orders_by_venue_id(session, [f.order_id for f in venue_fills])
        #: The status each order held when this step began. A fill is judged late against that,
        #: never against a status this same poll has just moved.
        was: dict[int, str] = {}
        for fill in venue_fills:
            row = rows.get(fill.order_id)
            if row is None or fill.price is None or fill.count is None:
                continue
            was.setdefault(row.id, row.status)
            _, filled = outcomes.setdefault(row.id, (row.status, row.filled_contracts))
            try:
                with session.begin_nested():
                    outcomes[row.id] = self._persist_venue_fill(
                        session, row, fill, was[row.id], filled, now, stats)
            except Exception as exc:  # noqa: BLE001 - one fill, not the step
                log.exception("recording venue fill for order %s failed", row.id)
                stats.errors += 1
                _note_error(heartbeat, f"order {row.id}: {type(exc).__name__}: {exc}")
        return outcomes

    def _persist_venue_fill(self, session: Session, row, fill, was: str, filled: Decimal,
                            now: datetime, stats: ExecStats) -> tuple[str, Decimal]:
        """One venue fill: the `fills` row, its cash movement, and the order it moved.

        `filled` is the running total for this order, and `was` is the status it held when the
        step began. A fill that arrives for an order which has already left the book -- our
        cancel and the venue's fill crossing on the wire, the ordinary live race -- is recorded
        in full and marked with a `late_fill` event carrying that status, but never moves the
        row off it: a cancelled order that came back as `partially_filled` would be put back
        into `open_orders` and cancelled at the venue a second time (review round 1,
        Important 3).
        """
        fee_type, fee_multiplier, maker_rate = fill_fee_fields(KALSHI_FOOTBALL)
        prob = side_p(fill.price, row.side)
        contracts = Decimal(fill.count).quantize(CENT)
        role = "taker" if fill.is_taker else "maker"
        fee = fee_for_order(KALSHI_FOOTBALL, role, prob, contracts)
        at = fill.created_time or now
        fill_id = store.insert_fill(
            session, order_id=row.id, prob=prob, contracts=contracts, fee=fee,
            fee_type=fee_type, fee_multiplier=fee_multiplier, maker_rate=maker_rate,
            filled_at=at, simulated=False, fill_method=VENUE,
            source_trade_id=None if fill.trade_id is None else str(fill.trade_id)[:64],
            source_event_id=None, taker_side=None, through=False, tape_source=None,
            has_print=True, replay=False)
        if fill_id is None:
            return _venue_status(was, filled, row.contracts), filled
        stats.fills += 1
        if not self.replay:
            self._metrics_acc.filled_contracts += contracts
        filled = filled + contracts
        cash = -(prob * contracts + fee)
        store.insert_ledger_fill(
            session, ts=at, variant_id=row.variant_id, kind="fill", order_id=row.id,
            fill_id=fill_id, ticker=row.ticker, side=row.side, contracts=contracts, price=prob,
            fee=fee, cash_delta=cash.quantize(CENT), replay=False)
        if was not in store.OPEN_STATUSES:
            store.insert_event(session, order_id=row.id, ts=at, kind=LATE_FILL, prob=prob,
                               contracts=contracts, reason=was, replay=False)
        status = _venue_status(was, filled, row.contracts)
        store.update_order(session, row.id, {"filled_contracts": filled, "status": status})
        return status, filled

    def _tape(self, session: Session, working, now: datetime,
              heartbeat: dict, stats: ExecStats | None = None
              ) -> tuple[dict[str, tuple[list, list]], set[str], set[str], set[str]]:
        """One print scan and one delta scan per ticker, shared by every order on it.

        Prints have no cursor (§1) and are rescanned from `placed_at - 60 s` every loop; the
        simulator's print watermark is what makes the re-feed idempotent. Deltas keep the id
        cursor, so the scan starts at the earliest cursor any track on the ticker *is still
        going to read* -- the watched cursor of a cancelled order and the no-watcher cursor of
        a finished counterfactual are both frozen where that track stopped, and folding them in
        would drag the ticker's scan back to a position no live track will ever consume from
        (fix 22). Both scans stop at `_at(now)` in replay: `simulate_fills` already refuses to
        walk past its deadline, but `has_print` reads the whole print list.

        Returns the tape, the set of tickers whose read failed, the set whose delta read came
        back full and so is still behind the tape, and the set deferred by §0.14's counterfactual
        backoff. A deferred ticker is not read at all this step, which is the cadence working
        rather than an error, which is why it is its own member and is never folded into
        `unread` -- whose length feeds `stats.errors` (review CR-2).

        Each ticker is read inside its own savepoint, so one ticker's statement timeout rolls
        back to the savepoint and leaves this transaction -- and every cursor already advanced
        in this loop -- intact.

        How full is full is per ticker and adapts (fix 26): the read asks for
        `self._delta_batch[ticker]` rows, quartered down to `DELTA_BATCH_FLOOR` every time the
        engine's 10 s statement timeout kills the read and doubled back up to
        `DELTA_BATCH_LIMIT` every time it comes back full. Cold pages, not row count, are what
        the timeout is really about, so the size a ticker can finish is the only knob that
        decides whether it makes any progress at all.
        """
        # Fix 82: measurement only. `stats` defaults to a throwaway `ExecStats` so every
        # existing direct caller of `_tape` in the test suite -- which does not pass one --
        # keeps working unchanged; only `_simulate`'s own call passes the step's real one.
        if stats is None:
            stats = ExecStats()
        windows: dict[str, tuple[datetime, int | None]] = {}
        deferred: set[str] = set()
        for row in working:
            # §0.14: a counterfactual whose ticker's tape read keeps failing is retried on an
            # exponential delay in elapsed wall seconds, evaluated *here* -- where the step
            # chooses which tickers to read -- so a ticker whose next attempt is in the future is
            # simply not read this step. The track is not closed and never will be (ruling
            # CR-4): closing it would remove its order from gate criterion 4's population,
            # non-randomly and on the worst-taped tickers.
            #
            # A row whose *watched* track is still resting is read whatever the counterfactual's
            # backoff says: the order we actually placed is not deferrable, and its cursor has
            # to keep moving.
            if (row.nw_next_attempt_at is not None and not row.nw_done
                    and row.nw_next_attempt_at > now and row.status not in store.OPEN_STATUSES):
                deferred.add(row.ticker)
                continue
            lower, cursor = windows.get(row.ticker, (row.placed_at, None))
            lower = min(lower, row.placed_at)
            live = []
            if row.status in store.OPEN_STATUSES:
                live.append(row.tape_cursor_event_id)
            if not row.nw_done:
                live.append(row.nw_tape_cursor_event_id)
            for value in live:
                # A null cursor is left out of the minimum on purpose (review rev-fix-78c, I1).
                # It belongs to a track that has consumed no delta at all, and by construction
                # that is a track with no queue either -- `_place` writes the queue and the
                # cursor in one dict, `_anchor_tracks` sets them together, and `simulate_fills`
                # returns before it reads an event when `queue_remaining is None` -- so the
                # window it is handed cannot move a stored value of its own. Folding it in as
                # position 0 would put the whole ticker back on fix 22's `ts`-bounded plan
                # (`store._DELTAS_FIRST`, a bitmap of the `ts` index against 20,000 rows) every
                # loop, inside the unbudgeted tape phase, and a read that came back full would
                # mark the ticker lagging and block `nw_done` for every track on it.
                if value is not None:
                    cursor = value if cursor is None else min(cursor, value)
            windows[row.ticker] = (lower, cursor)
        out: dict[str, tuple[list, list]] = {}
        unread: set[str] = set()
        lagging: set[str] = set()
        at = self._at(now)
        for ticker, (placed_at, cursor) in windows.items():
            lower = placed_at - store.PRINT_LOOKBACK
            limit = self._delta_batch.get(ticker, store.DELTA_BATCH_LIMIT)
            try:
                with session.begin_nested():
                    prints = store.load_prints(session, ticker, lower, at)
                    batch = store.load_deltas(session, ticker, cursor or 0, lower, at,
                                              limit=limit)
                    # Fix 82: measurement only, counted here -- after the read, before any
                    # hold-back -- so a truncated batch's held-back prints still count. See
                    # `ExecStats.tape_tickers_n`/`tape_print_rows`/`tape_delta_rows`.
                    stats.tape_tickers_n += 1
                    stats.tape_print_rows += len(prints)
                    stats.tape_delta_rows += len(batch.deltas)
            except Exception as exc:  # noqa: BLE001 - one ticker, not the step
                # The first failure of a loop carries its traceback; the rest of a loop's
                # failures are almost always the same one repeated, and 55 tracebacks a loop
                # would bury it (fix 22 round 1, minor).
                log.warning("tape read failed for %s: %s", ticker, exc, exc_info=not unread)
                unread.add(ticker)
                self._note_backoff(session, working, ticker, now, failed=True)
                _note_error(heartbeat, f"tape {ticker}: {type(exc).__name__}: {exc}")
                if _is_statement_timeout(exc):
                    # Fix 26: the batch, not the plan, is what this ticker cannot afford. A
                    # cursor a million ids behind reads cold pages, and 20 000 rows of them do
                    # not fit in 10 s, so the read dies, the cursor never moves and the watch
                    # never ends. Quartering converges in a handful of loops (20000 -> 250 in
                    # five) rather than crawling down one halving at a time, and the floor is
                    # where it stops: below `DELTA_BATCH_FLOOR` the ticker could not walk off
                    # its backlog before the market settled even if every read succeeded.
                    shrunk = max(store.DELTA_BATCH_FLOOR, limit // 4)
                    self._delta_batch[ticker] = shrunk
                    log.warning("tape batch for %s shrunk to %d after a statement timeout",
                                ticker, shrunk)
                continue
            deltas = batch.deltas
            if batch.truncated:
                # The read stopped at its limit, so this ticker's tape has more behind it than
                # this loop asked for and the last row we hold is a position, not the head. The
                # flag hangs on the read filling up, never on what survived the row filter: a
                # full batch whose rows were all dropped for a null side, price or delta
                # advances nothing at all, which is the one case that could sit still forever
                # and so is exactly the case that must be visible (fix 22 round 1, minor).
                lagging.add(ticker)
                # Fix 26: this size came back full and inside the timeout, which is evidence
                # the ticker can afford more, so it doubles -- capped, never past
                # `DELTA_BATCH_LIMIT` -- and a ticker warmed by its own catch-up reads climbs
                # back to the cap in a few loops instead of dragging a 250-row batch through
                # the rest of the game.
                self._delta_batch[ticker] = min(store.DELTA_BATCH_LIMIT, limit * 2)
                if deltas:
                    # Prints past the last delta consumed are held back rather than fed early:
                    # a print applied ahead of the deltas that belong with it is applied
                    # against the wrong queue, and the simulator's watermark would then refuse
                    # to reconsider it. Nothing is lost, and the reason is not that prints
                    # carry no cursor -- they are re-read next loop either way -- but that a
                    # track on a lagging ticker is not allowed to finish: neither the
                    # no-watcher `done` below nor `_order_action`'s `Expire` closes one until
                    # the ticker has caught up, so the held-back tape is always fed to a track
                    # that is still open to it (fix 22 round 1, I2).
                    covered = deltas[-1].ts
                    prints = [p for p in prints if p.ts <= covered]
                else:
                    log.warning("tape batch for %s filled its %d-row limit but yielded no "
                                "usable delta; its cursor cannot advance this loop",
                                ticker, limit)
            elif limit >= store.DELTA_BATCH_LIMIT:
                # Caught up at the full cap: there is nothing left to remember about this
                # ticker, so it stops costing an entry (fix 26). A shrunk ticker that read
                # short keeps its size -- one short read says the backlog is gone, not that the
                # pages are warm, and the next full read is what earns the size back.
                self._delta_batch.pop(ticker, None)
            self._note_backoff(session, working, ticker, now, failed=False)
            out[ticker] = (prints, deltas)
        for stale in set(self._delta_batch) - set(windows):
            # A ticker with no working order left is not going to be read again, and its size
            # would otherwise sit in this dict for the life of the process (fix 26).
            del self._delta_batch[stale]
        if lagging:
            # INFO, not ERROR: the executor is behind the tape and catching up by design. The
            # count is `exec.tape_lag_tickers`; the names live here (fix 22 round 1, I1).
            log.info("tape lag: %d ticker(s) behind (%s)",
                     len(lagging), ", ".join(sorted(lagging)))
        heartbeat["tape_lag"] = sorted(lagging)
        # A ticker any other row needed was read anyway, so it is not deferred for anybody.
        deferred -= set(windows)
        heartbeat["tape_deferred"] = sorted(deferred)
        return out, unread, lagging, deferred

    def _nw_backoff(self, attempts: int | None, now: datetime) -> datetime:
        """The next attempt instant after `attempts` failed reads, capped and elapsed.

        `exec_period_s` doubling per failed read, ceiling `store.NW_RETRY_MAX_S` (3600 s). Every
        bound here is elapsed wall time, never a loop count (ruling CR-5).
        """
        delay = self.settings.exec_period_s * (2 ** int(attempts or 0))
        return now + timedelta(seconds=min(store.NW_RETRY_MAX_S, delay))

    def _note_backoff(self, session: Session, working, ticker: str, now: datetime,
                      *, failed: bool) -> None:
        """Move every pending counterfactual on `ticker` forward, or reset it on a good read."""
        for row in working:
            if row.ticker != ticker or row.nw_done:
                continue
            if failed:
                attempts = int(row.nw_attempts or 0) + 1
                store.set_nw_backoff(session, row.id, attempts, self._nw_backoff(attempts - 1, now))
            elif row.nw_attempts:
                store.set_nw_backoff(session, row.id, 0, None)

    def _anchor_tracks(self, states, book: BookState, *, queue: Decimal | None) -> None:
        """Take both tracks to a freshly anchored book: queue, cursor and print floor together.

        The defect this repairs is that they were set apart (§0.4). The recovery branch took the
        queue to what was resting now and advanced the delta cursor but left the print
        watermark, so a trade from inside the gap -- already inside the snapshot it anchored on
        -- was applied a second time against the newly anchored queue; the no-book branch set
        the watermark to `now`, the recorder's clock rather than the book's.

        The two clocks are not the same clock and the floor is stated on the book's. A snapshot
        carries the recorder's receive time (`harness/recorder/ws_sink.py:151`) while a print
        carries the venue's own `ts_ms` (`ws_sink.py:124-126`), so the floor is the anchor
        instant less `DELTA_LOOKBACK` (5 s, `book.py:45-49`). A print inside that slack is
        reconciled by §1.3's ledger, not dropped.

        `queue` is the caller's, because the two branches decide it differently: the no-book
        branch joins the back of the first book it sees, and the recovery branch clamps to the
        smaller of what it believed and what is resting (review I-2). §1.3's `anchor` empties
        the ledger, keeps `cancels_ahead` and prunes the trade-id set to the new floor.
        """
        for state in states:
            state.anchor(queue=queue, cursor_event_id=book.last_event_id,
                         anchor_as_of=book.anchor_as_of)

    def _simulate_order(self, session: Session, row, markets, bases, recovering, tape, lagging,
                        now: datetime, stats: ExecStats, *,
                        walk: "_Walk | None" = None) -> tuple[str, Decimal]:
        """One order's two tracks. `walk` is `_batch_pending`'s own simulation of this row's
        counterfactual, when it has already made it (round 1, M2); None means simulate."""
        s = self.exec_settings
        market = markets.get(row.venue_market_id)
        book = self.books.get(row.ticker)
        if market is not None and market.dirty(now, s):
            # A book we cannot read tells us nothing about the queue, so neither track advances
            # and the record of how long that lasted is kept per track (§0.9). The watched
            # column accrues only while the order is actually resting -- a cancelled order is
            # not sitting against anything -- and each increment is clamped to what is left of
            # the resting interval, because `_order_action` deliberately holds `Expire` on a
            # lagging ticker and a past-expiry order can therefore stay `open`.
            period = self.settings.exec_period_s
            if row.status in store.OPEN_STATUSES:
                store.add_dirty_seconds(session, row.id, _clamped(period, row, now, watched=True),
                                        watched=True)
            if not row.nw_done:
                store.add_dirty_seconds(session, row.id,
                                        _clamped(period, row, now, watched=False), watched=False)
            self._close_nw_if_expired(session, row, now)
            return row.status, row.filled_contracts

        watched = _state_of(row, "")
        no_watcher = _state_of(row, "nw_")
        updates: dict = {}
        anchor: BookState | None = None

        if row.queue_ahead_at_place is None:
            # R10/D7: the ticker had no book when the order was placed. The order joins the
            # simulation the first time one exists, at the back of that book's queue, and the
            # prints and deltas of the no-book window are discarded rather than guessed at.
            if book is None:
                self._close_nw_if_expired(session, row, now)
                return row.status, row.filled_contracts
            queue = book.resting_at(row.side, row.prob)
            # The queue, the delta cursor, the ledger and the print floor are anchored together,
            # from the anchoring book itself, by the one helper both re-anchor branches call so
            # they cannot drift (6B §0.4, §1.2). The floor is what this branch used to spell as a
            # watermark at `now`, the recorder's clock rather than the book's: the prints of the
            # no-book window are already inside the queue this book establishes, so they are
            # discarded (D7) rather than applied against it.
            self._anchor_tracks((watched, no_watcher), book, queue=queue)
            anchor = book
            updates.update(queue_ahead_at_place=queue, book_source=book.source,
                           book_age_s=book_age_s(book, now), book_first_seen_at=now)
        elif row.ticker in recovering and book is not None:
            # The first clean book after a dirty stretch. Our queue was built from a tape with
            # a hole in it, so it is taken down to whatever is actually resting there now --
            # never up (review I-2) -- and the print floor is anchored with it, so a trade from
            # inside the gap that the snapshot already reflects cannot be applied again (§0.4).
            resting = book.resting_at(row.side, row.prob)
            for state in (watched, no_watcher):
                # A track whose queue is None has no book to have joined behind (R10/D7), and a
                # recovery says nothing about that: it is still out of simulation until the
                # no-book branch above admits it. Today's code leaves the None alone and so does
                # this -- turning it into `resting` here would be a behaviour change with no
                # correction id (review IM-13).
                clamped = (None if state.queue_remaining is None
                           else min(state.queue_remaining, resting))
                self._anchor_tracks((state,), book, queue=clamped)
            anchor = book

        prints, deltas = tape.get(row.ticker, ([], []))
        order = _paper_order(
            row, updates.get("queue_ahead_at_place", row.queue_ahead_at_place), now)
        # F41's worst case is one fill per order, not one per track. The two tracks stop at
        # different deadlines, so after a cancel their cursors diverge and `_sim_book` hands
        # them different books, which would stamp two different crossing ids on one order.
        crossed_already = bool(row.crossed or row.nw_crossed)
        # R8: the expiry is the only guarantee an order stops resting, so both tracks stop
        # there too -- one variable, read by both calls below, so they cannot drift apart again
        # the way the watched track (handed `now`) and the counterfactual (already clamped)
        # once did, which is how a print in the seconds between expiry and the loop instant
        # filled an order that was no longer on the market (§0.8).
        deadline = _deadline(row, now)
        # The user's ruling of 2026-09-14 15:38 CT (journal 206): the counterfactual is bounded
        # by `kickoff - 10 minutes` as well as by the expiry. An order placed normally already
        # expires there (`plan._intent_actions` places with `expiry = kickoff - s.cutoff`), so
        # this only bites where the two came apart -- a kickoff moved after placement, a
        # NULL-expiry order, a row whose expiry outlived the window -- which is how fills landed
        # past the window `ops/checks.fills_outside_placement_window` measures. The watched track
        # keeps `deadline` unchanged: what it did is a fact about an order we were holding, and
        # R8's expiry is still the only guarantee it stopped resting.
        # This bounds where the counterfactual's *fills* stop, not what its interval measures:
        # its dirty and unobserved seconds still accrue over §0.10's `[placed_at, expiry]`
        # (`_clamped`, `execution/dirty_time.py`, both unchanged by the ruling), so past this
        # bound `counterfactual_dirty_s` is time in which the counterfactual could no longer
        # fill -- no stored row changes, and a reader of that column needs to know it
        # (review m-2).
        # The bound itself, and why it is read with `getattr`, are in `_nw_deadline`: fix 78
        # moved both deadlines into helpers so the batched pending path and this one compute
        # them from one expression. The loop test above proves the bound bites on the real
        # projection's `kickoff_utc`, so dropping that column would fail that test rather than
        # silently unbound the counterfactual.
        nw_deadline = _nw_deadline(row, now)

        if row.status in store.OPEN_STATUSES:
            result = simulate_fills(order, watched, self._sim_book(session, row, watched, bases,
                                                                   anchor),
                                    prints, deltas, deadline, QUEUE_MODEL)
            track = self._persist_track(session, row, order, result, prints, ledger=True,
                                        crossed_already=crossed_already)
            crossed_already = crossed_already or track.cross_written
            stats.fills += track.inserted
            filled = track.filled
            status = _next_status(row.status, filled, row.contracts)
            updates.update(filled_contracts=filled, status=status,
                           **_state_columns("", track.state))
            if track.crossed:
                updates["worst_case_fill"] = True
        else:
            filled, status = row.filled_contracts, row.status

        if not row.nw_done:
            # The pre-check's walk, when there is one: same order, same state, same book, same
            # prints and deltas, same deadline, and `simulate_fills` mutates none of them.
            # Since fix 78c the pre-check reproduces the two anchoring branches too, so the
            # guard has two arms: a frame that anchors takes the walk only when the walk
            # anchored on the very same `self.books` object, compared by identity, and a frame
            # that does not anchor takes it as it always did. The second arm means an anchored
            # walk is *not* refused on a frame that did not anchor -- a case the two branches'
            # identical conditions (same row, same `recovering` set, same book) make
            # unreachable, and a future branch that anchors here and not there would have to
            # tighten this to `walk.anchor is anchor` (review rev-fix-78c, M3). A row that
            # anchored in the pre-check carries its anchored result rather than being anchored
            # and walked twice, and with it the clean stretch a deferred row walked before its
            # re-anchor.
            result = (walk.result
                      if walk is not None and (anchor is None or anchor is walk.anchor)
                      else simulate_fills(
                          order, no_watcher,
                          self._sim_book(session, row, no_watcher, bases, anchor),
                          prints, deltas, nw_deadline, NO_WATCHER))
            track = self._persist_track(session, row, order, result, prints, ledger=False,
                                        crossed_already=crossed_already)
            stats.nw_fills += track.inserted
            updates.update(_nw_columns(row, track.state, track.filled, track.crossed,
                                       lagging, now))

        store.update_order(session, row.id, updates)
        return status, filled

    def _sim_book(self, session: Session, row, state: SimState, bases, anchor):
        """The book as of this track's cursor, always a copy the simulator may walk.

        The ordinary case is the cursor sitting exactly at the previous step's book, which is
        what `base` is. A cursor anywhere else means a restart or a recovery, and the honest
        answer is the book as it stood at that tape position -- not the one we happen to hold.
        """
        if anchor is not None:
            return anchor.copy()
        base = bases.get(row.ticker)
        cursor = state.cursor_event_id
        if base is not None and cursor == base.last_event_id:
            return base.copy()
        if cursor is not None:
            at = store.event_ts(session, cursor)
            if at is not None:
                # `book_at` takes no gap verdict on purpose: every gap after the instant also
                # has a higher id, so the live test would dirty every historical book on that
                # sid for the rest of the season and take the markouts with it. But this branch
                # is asking what the *live loop* believed at that cursor, and a gap the live
                # loop had already seen dirtied its book. `load_book_at` is `book_at` plus
                # exactly that verdict, bounded at the instant (ruling I-14). The session test
                # is bounded the same way: a reconnect recorded after the cursor instant is not
                # something the live loop knew at it (review Important 2).
                return load_book_at(session, row.ticker, at,
                                    newest_ws_connect(session, at))
        return None if base is None else base.copy()

    def _persist_track(self, session: Session, row, order: PaperOrder, result, prints,
                       ledger: bool, crossed_already: bool) -> _TrackResult:
        """Write one track's fills. Only a row the database actually accepted moves a total."""
        fee_type, fee_multiplier, maker_rate = fill_fee_fields(KALSHI_FOOTBALL)
        filled = row.filled_contracts if ledger else row.nw_filled_contracts
        inserted = 0
        for fill in result.fills:
            fill_id = store.insert_fill(
                session, order_id=row.id, prob=fill.prob, contracts=fill.contracts,
                fee=fill.fee, fee_type=fee_type, fee_multiplier=fee_multiplier,
                maker_rate=maker_rate, filled_at=fill.filled_at, simulated=True,
                fill_method=fill.fill_method, source_trade_id=fill.source_trade_id,
                source_event_id=fill.source_event_id, taker_side=fill.taker_side,
                through=fill.through, tape_source=fill.tape_source,
                has_print=has_print(fill, order, prints), replay=self.replay)
            if fill_id is None:
                continue
            inserted += 1
            filled += fill.contracts
            if ledger:
                if not self.replay:
                    self._metrics_acc.filled_contracts += fill.contracts
                cash = -(fill.prob * fill.contracts + fill.fee)
                store.insert_ledger_fill(
                    session, ts=fill.filled_at, variant_id=row.variant_id, kind="fill",
                    order_id=row.id, fill_id=fill_id, ticker=row.ticker, side=row.side,
                    contracts=fill.contracts, price=fill.prob, fee=fill.fee,
                    cash_delta=cash.quantize(CENT), replay=self.replay)
        cross_written = False
        if result.cross is not None and not crossed_already:
            # Written once per order: the first track of the first step that sees the crossing
            # inserts it, the order's persisted `crossed`/`nw_crossed` flags close the door
            # afterwards, and the unique key on the crossing row is the backstop. It is never a
            # position, never in the ledger and never in P&L.
            cross_written = store.insert_fill(
                session, order_id=row.id, prob=result.cross.prob,
                contracts=result.cross.contracts, fee=result.cross.fee, fee_type=fee_type,
                fee_multiplier=fee_multiplier, maker_rate=maker_rate,
                filled_at=result.cross.filled_at, simulated=True, fill_method=CROSS,
                source_trade_id=None, source_event_id=result.cross.source_event_id,
                taker_side=None, through=result.cross.through,
                tape_source=result.cross.tape_source,
                has_print=has_print(result.cross, order, prints),
                replay=self.replay) is not None
        return _TrackResult(state=result.state, filled=filled, inserted=inserted,
                            crossed=result.crossed, cross_written=cross_written)

    # --- actions ----------------------------------------------------------------------

    def _apply(self, session: Session, actions, intents, extras, markets, rows,
               now: datetime, stats: ExecStats, heartbeat: dict) -> None:
        by_intent = {intent.intent_id: intent for intent in intents}
        for action in actions:
            try:
                with session.begin_nested():
                    self._apply_one(session, action, by_intent, extras, markets, rows, now,
                                    stats)
            except Exception as exc:  # noqa: BLE001 - one action, not the step
                log.exception("applying %r failed", action)
                stats.errors += 1
                _note_error(heartbeat, f"{type(action).__name__}: {type(exc).__name__}: {exc}")

    def _apply_one(self, session: Session, action, by_intent, extras, markets, rows,
                   now: datetime, stats: ExecStats) -> None:
        if isinstance(action, Place):
            self._place(session, action, by_intent, extras, markets, rows, now, stats)
        elif isinstance(action, Cancel):
            if self.gateway.cancel(session, action.order_id, action.reason, now):
                stats.cancelled += 1
                if not self.replay:
                    acc = self._metrics_acc.cancelled
                    acc[action.reason] = acc.get(action.reason, 0) + 1
            store.insert_event(session, order_id=action.order_id, ts=now, kind="cancel",
                               reason=action.reason, replay=self.replay)
        elif isinstance(action, Expire):
            if store.expire_order(session, action.order_id):
                stats.expired += 1
            store.insert_event(session, order_id=action.order_id, ts=now, kind="expire",
                               reason=EXPIRY, replay=self.replay)
        elif isinstance(action, CapGate):
            # Written for every exec variant whether or not it blocked, so replay can measure
            # what the caps cost the variants that do not apply them (amendment 2).
            store.insert_event(session, intent_id=action.intent_id, ts=now, kind="cap_gate",
                               reason=action.reason, replay=self.replay)
        elif isinstance(action, Skip):
            written = store.insert_event(session, intent_id=action.intent_id, ts=now,
                                         kind=action.kind, reason=action.reason,
                                         replay=self.replay)
            if written is not None:
                stats.skipped += 1
                if not self.replay:
                    acc = self._metrics_acc.skipped
                    acc[action.reason] = acc.get(action.reason, 0) + 1

    def _place(self, session: Session, action: Place, by_intent, extras, markets, rows,
               now: datetime, stats: ExecStats) -> None:
        intent = by_intent[action.intent_id]
        extra = extras.get(action.intent_id, {})
        market = markets[intent.venue_market_id]
        row = rows.get(intent.venue_market_id)
        book = None if action.no_book else market.book
        queue = None if book is None else book.resting_at(action.side, action.prob)
        cursor = None if book is None else book.last_event_id
        prefix = "replay" if self.replay else "paper"
        n = store.orders_for_intent(session, action.intent_id)
        values = {
            "intent_id": action.intent_id, "variant_id": intent.variant_id,
            "venue": extra.get("venue") or "kalshi", "mode": "paper",
            "client_order_id": f"{prefix}-{action.intent_id}-{n}", "ticker": intent.ticker,
            "venue_market_id": intent.venue_market_id, "side": action.side,
            "prob": action.prob, "contracts": action.contracts, "status": "open",
            "placed_at": now, "expiry": action.expiry,
            "fair_p_at_place": market.fair_p, "fair_row_id_at_place": market.fair_row_id,
            # `fair_books_json` stays NULL: `fair_values` has no column carrying the devigged
            # per-book inputs behind the consensus. Its `model_json` is the margin model's
            # parameters and is NULL for a direct fair value, so it is not that. Filling this
            # needs a pricing-side column first (test_fair_books_json_is_null_...).
            "venue_bid_at_place": market.best_bid_yes,
            "venue_ask_at_place": market.best_ask_yes,
            "venue_mid_at_place": market.mid_yes,
            "queue_ahead_at_place": queue, "queue_remaining": queue,
            "nw_queue_remaining": queue,
            # `traded_at_price` and `nw_traded_at_price` are left NULL from here on (6B §1.3,
            # ruling CR-3): C0's charge-against quantity is not one of the ledger's terms, and
            # the 6B boundary is visible by that nullness. Writing a 0 at placement would make
            # every order placed after the deploy but never simulated look pre-boundary.
            "book_source": book.source if book is not None else "none",
            "book_age_s": None if book is None else book_age_s(book, now),
            "book_first_seen_at": None if book is None else now,
            "edge_at_place": intent.edge, "edge_min_at_place": intent.edge_min,
            "as_at_place": extra.get("as_estimate"),
            "staleness_at_place": market.staleness_s,
            "stale_allowance_at_place": market.stale_allowance_s,
            "feed_kind": market.feed_kind,
            "config_hash": config_hash(intent.variant_id, self.exec_settings),
            "gap_snapshot_id": extra.get("gap_snapshot_id"), "game_id": intent.game_id,
            "sport": getattr(row, "sport", None), "kickoff_utc": intent.kickoff_utc,
            "match_key": market.match_key,
            "tape_cursor_event_id": cursor, "nw_tape_cursor_event_id": cursor,
            "replay": self.replay}
        # The one line of `_place` the gateway split moved: the values above are what phase 3
        # built, in the order it built them, and `PaperGateway.place` is the same insert.
        order_id = self.gateway.place(session, values, action, market, now).order_id
        if order_id is None:
            return
        stats.placed += 1
        if not self.replay:
            self._metrics_acc.placed += 1
            # 6D §1.2: decision to placement, per variant. `signal_created_at` is on the intent
            # the placement came from, so this is `orders.placed_at - signals.created_at`
            # without the join -- the two stamps are both in hand here.
            delay_ms = int((now - intent.signal_created_at).total_seconds() * 1000)
            self._metrics_acc.signal_to_order_ms.setdefault(intent.variant_id, []).append(
                max(0, delay_ms))
        store.insert_event(session, order_id=order_id, intent_id=action.intent_id, ts=now,
                           kind="place", prob=action.prob, contracts=action.contracts,
                           fair_p_at_event=market.fair_p, replay=self.replay)
        if action.no_book:
            # The order rests, but the record says the queue behind it was unknowable (R10).
            store.insert_event(session, intent_id=action.intent_id, ts=now, kind="skipped",
                               reason=NO_BOOK, replay=self.replay)

    # --- Task 12b telemetry -------------------------------------------------------------

    def _check_startup_events(self, session: Session, variant_ids: list[str],
                              now: datetime) -> None:
        """Once per process (`_startup_checked`): a `deploy` event when this build's
        `EXECUTOR_VERSION` differs from the heartbeat's, and one `config_change` event per
        variant whose current config hash differs from its own newest open order's."""
        if self._startup_checked:
            return
        self._startup_checked = True
        heartbeat_version = store.read_heartbeat_executor_version(session)
        if heartbeat_version is not None and heartbeat_version != execution.EXECUTOR_VERSION:
            telemetry.event(
                session, "deploy",
                f"executor_version {heartbeat_version} -> {execution.EXECUTOR_VERSION}",
                ref={"from": heartbeat_version, "to": execution.EXECUTOR_VERSION}, ts=now)
        for variant_id in variant_ids:
            newest_hash = store.newest_open_order_config_hash(session, variant_id)
            if newest_hash is None:
                continue
            current_hash = config_hash(variant_id, self.exec_settings)
            if current_hash != newest_hash:
                telemetry.event(
                    session, "config_change", f"{variant_id} config hash changed",
                    ref={"variant_id": variant_id, "from": newest_hash, "to": current_hash},
                    ts=now)

    def _write_order_watch_samples(self, session: Session, working, markets, now: datetime) -> None:
        """One `order_watch_samples` row per open order due for its periodic sample, plus one
        terminal row for every order that left the open set this step (design spec §3.3)."""
        prior_open = {row.id: row for row in working if row.status in store.OPEN_STATUSES}
        if not prior_open:
            return
        current = store.order_status_snapshot(session, prior_open.keys())
        rows_out: list[dict] = []
        for order_id, row in prior_open.items():
            snap = current.get(order_id)
            if snap is None:
                continue
            status, queue_remaining, nw_queue_remaining = snap
            terminal = status if status in ("filled", "cancelled", "expired") else None
            if terminal is None:
                if status not in store.OPEN_STATUSES or not self._watch_sampler.due(order_id):
                    continue
            book = self.books.get(row.ticker)
            market = markets.get(row.venue_market_id)
            rows_out.append(dict(
                order_id=order_id, ts=now, queue_remaining=queue_remaining,
                nw_queue_remaining=nw_queue_remaining,
                best_bid=None if book is None else book.best_bid(row.side),
                best_ask=None if book is None else book.best_ask(row.side),
                fair_p=None if market is None else market.fair_p,
                book_dirty=True if book is None else book.dirty, terminal=terminal))
            if terminal is not None:
                # Fix round 1, M4: a terminal order is never sampled again, so its clock would
                # otherwise sit in this dict for the rest of the process's life.
                self._watch_sampler.forget(order_id)
        if rows_out:
            store.insert_order_watch_samples(session, rows_out)

    def _write_equity_snapshots(self, session: Session, variant_ids: list[str],
                                now: datetime) -> None:
        """One `equity_snapshots` row per exec variant, every `equity_sample_s` (design spec
        §3.4): cash off the ledger, open exposure and a mark-to-market that only counts
        contracts whose ticker has a live book right now."""
        if not variant_ids:
            return
        cfg = store.variant_configs(session, variant_ids)
        for variant_id in variant_ids:
            bankroll = Decimal(str(cfg.get(variant_id, {}).get("bankroll", 0)))
            cash = bankroll + store.ledger_cash_delta(session, variant_id)
            # A fully-closed (variant, ticker, side) still rows out of the `positions` view
            # with open_contracts = 0 and avg_price NULL (its `nullif` divides 0 by 0); neither
            # an open position nor something a book can mark, so it drops out here.
            positions = [p for p in store.positions_for_variant(session, variant_id)
                        if p.open_contracts and p.avg_price is not None]
            open_stake = sum((p.open_contracts * p.avg_price for p in positions), ZERO)
            total_contracts = sum((p.open_contracts for p in positions), ZERO)
            covered_contracts = ZERO
            mtm_open = ZERO
            any_book = False
            for p in positions:
                book = self.books.get(p.ticker)
                mid = None if book is None else book.mid()
                if mid is None:
                    continue
                any_book = True
                covered_contracts += p.open_contracts
                mtm_open += p.open_contracts * side_p(mid, p.side)
            # Fix round 1, I1: one convention. NULL when there is nothing to compute a share
            # over (no open positions at all); otherwise the real contract-weighted share,
            # matching spec §3.4's "share of open contracts" rather than a share of positions.
            mtm_coverage = (covered_contracts / total_contracts) if total_contracts > ZERO \
                else None
            n_open_orders = store.count_variant_open_orders(session, variant_id, self.replay)
            # Spec §9.3, evaluated at every equity sample: the trailing-7-day peak of *cash*
            # and the drawdown against it. `mtm_open` is written beside it and never enters
            # it (decision 6, ruling B-C2).
            drawdown = compute_drawdown(cash, peak_equity_7d(session, variant_id, now, cash))
            store.insert_equity_snapshot(
                session, ts=now, variant_id=variant_id, cash=cash, open_stake=open_stake,
                mtm_open=mtm_open if any_book else None, mtm_coverage=mtm_coverage,
                n_open_positions=len(positions), n_open_orders=n_open_orders,
                peak_equity_7d=drawdown.peak_equity_7d, drawdown_pct=drawdown.drawdown_pct,
                drawdown_stop=drawdown.drawdown_stop)
        # The stop is information in paper: nothing above changes what the executor placed this
        # step, and nothing here cancels anything. The live gateway is the one that treats it as
        # a brake, and `PaperGateway.on_drawdown_stop` is a no-op it cannot reach.
        self.gateway.on_drawdown_stop(session, now)

    # --- small helpers ----------------------------------------------------------------

    def _close_nw_if_expired(self, session: Session, row, now: datetime) -> None:
        """Close the no-watcher track of an expired order on a path that ran no simulation.

        `working_orders` selects `status in ('open','partially_filled') or nw_done = false`, so
        an order whose book is dirty or has never existed at its expiry would otherwise be
        re-scanned every 15 s for the rest of the season -- and `_tape` would keep widening the
        print window for every other order on its ticker back to this one's placement.
        """
        if not row.nw_done and row.expiry is not None and row.expiry <= now:
            store.update_order(session, row.id, {"nw_done": True})

    def _order_view(self, row, outcomes) -> OpenOrderView:
        _, filled = outcomes.get(row.id, (row.status, row.filled_contracts))
        return OpenOrderView(
            order_id=row.id, intent_id=row.intent_id, variant_id=row.variant_id,
            ticker=row.ticker, venue_market_id=row.venue_market_id, side=row.side,
            prob=row.prob, contracts=row.contracts, filled_contracts=filled,
            placed_at=row.placed_at, expiry=row.expiry, fair_p_at_place=row.fair_p_at_place,
            venue_mid_at_place=row.venue_mid_at_place,
            edge_min_at_place=row.edge_min_at_place, as_at_place=row.as_at_place,
            kickoff_utc=row.kickoff_utc, game_id=row.game_id, stake=row.stake,
            match_key=row.match_key)

    def _with_config(self, views, cfg: dict, label: str) -> list:
        """Drop anything whose variant has no registered config.

        `plan_actions` raises on a missing config by design -- neither available default is a
        decision anybody asked for -- so the loop must never hand it one (Task 5 ruling 3).
        """
        kept, dropped = [], set()
        for view in views:
            if view.variant_id in cfg:
                kept.append(view)
            else:
                dropped.add(view.variant_id)
        if dropped:
            log.warning("no registered config for %s variants %s; skipped this loop",
                        label, sorted(dropped))
        return kept

    def _loops_skipped(self, started: float) -> int:
        period = max(1, int(self.settings.exec_period_s))
        skipped = 0
        if self._last_mono is not None:
            skipped = max(0, int((started - self._last_mono) / period) - 1)
        self._last_mono = started
        return skipped

    def _p95(self) -> int | None:
        if not self._durations:
            return None
        ordered = sorted(self._durations)
        return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]

    def _bind(self):
        if self._engine is None:
            with self._factory() as probe:
                self._engine = probe.get_bind()
        return self._engine


def _is_statement_timeout(exc: BaseException) -> bool:
    """Is this failure the engine's own statement timeout, rather than anything else?

    The executor's engine carries `EXEC_STATEMENT_TIMEOUT_MS = 10_000`, and PostgreSQL reports
    hitting it as SQLSTATE 57014, which psycopg raises as `QueryCanceled` and SQLAlchemy hands
    on wrapped in `OperationalError` with the driver's exception on `.orig`. Both shapes are
    accepted, and the class -- not the message -- is what decides: the wording of "canceling
    statement due to statement timeout" belongs to the server's locale and version, and a
    substring match on it would silently stop recognising the one failure this fix reacts to.

    Everything else (a programming error, a serialization failure, a dropped connection) is not
    a batch that was too big to finish, so it must leave the batch size alone (fix 26).
    """
    for err in (exc, getattr(exc, "orig", None)):
        if err is None:
            continue
        if isinstance(err, QueryCanceled) or getattr(err, "sqlstate", None) == "57014":
            return True
    return False


def _note_error(heartbeat: dict, message: str) -> None:
    """Keep the first failure of the step; a later one rarely explains more than the first."""
    if heartbeat["last_error"] is None:
        heartbeat["last_error"] = message[:2000]


def _venue_status(was: str, filled: Decimal, contracts: Decimal) -> str:
    """The status a venue fill leaves an order in. An order that had already left the book keeps
    the status it left with: the fill is recorded, the position is real, and the order is still
    not resting (review round 1, Important 3)."""
    if was not in store.OPEN_STATUSES:
        return was
    return _next_status(was, filled, contracts)


def _paper_order(row, queue, now: datetime) -> PaperOrder:
    """The order under simulation, off the row. `queue` is the caller's because a step that has
    just anchored knows a queue the row does not carry yet."""
    return PaperOrder(order_id=row.id, ticker=row.ticker, side=row.side, prob=row.prob,
                      contracts=row.contracts, placed_at=row.placed_at,
                      expiry=row.expiry or now, queue_ahead_at_place=queue)


def _deadline(row, now: datetime) -> datetime:
    """Where the watched track stops. R8: the expiry is the only guarantee an order stopped
    resting, so both tracks read this one term rather than the loop instant (§0.8)."""
    return min(now, row.expiry) if row.expiry is not None else now


def _nw_deadline(row, now: datetime) -> datetime:
    """Where the counterfactual's *fills* stop: `_deadline` and, since the user's ruling of
    2026-09-14 15:38 CT (journal 206), `kickoff - 10 minutes` as well.

    `getattr`, because the two pure-unit modules build the row by hand from what
    `_simulate_order` reads and a hand-built row without a kickoff is the same case as a row
    whose kickoff is NULL. The real projection carries the column (`store._WORKING_ORDERS`,
    `o.kickoff_utc`).
    """
    kickoff = getattr(row, "kickoff_utc", None)
    deadline = _deadline(row, now)
    return deadline if kickoff is None else min(deadline, kickoff - NO_WATCHER_KICKOFF_MARGIN)


def _nw_columns(row, state, filled: Decimal, crossed: bool, lagging, now: datetime) -> dict:
    """The columns one counterfactual step writes back. One place, so the path that makes the
    write and the one that decides the write would change nothing cannot drift apart (fix 78).

    A track on a ticker whose delta read truncated this loop is not finished, even at its own
    expiry: the tape between its cursor and now has not been fed to it yet, and closing here
    would leave those fills in the replay and out of the live record. It closes on the first
    loop that catches up (fix 22 round 1, I2).
    """
    done = (row.ticker not in lagging
            and ((row.expiry is not None and row.expiry <= now) or filled >= row.contracts))
    columns = {"nw_filled_contracts": filled, "nw_done": done,
               **_state_columns("nw_", state)}
    if crossed:
        columns["worst_case_fill"] = True
    return columns


def _unchanged(row, updates: dict) -> bool:
    """Would `store.update_order(session, row.id, updates)` leave every column as it is?

    Value equality per column, read off the same projection the writer's own caller read, plus
    the stamp `update_order` adds by itself: a write that touches any `nw_` column also writes
    `nw_executor_version`, so a row last written by an older build has a column that would move
    and is not unchanged. `stamp_nw_writer` is asked rather than re-implemented -- it returns
    the caller's own dict when nothing counterfactual is being written -- so the rule stays in
    the one place that owns it (amendment 0.17).

    `orders` has no `updated_at` and no trigger, so a write of identical values is invisible in
    the data: this is the whole test for skipping one.
    """
    for column, value in updates.items():
        if getattr(row, column) != value:
            return False
    return (store.stamp_nw_writer(updates) is updates
            or row.nw_executor_version == store.executor_version_numeric())


def _clamped(period: int, row, now: datetime, *, watched: bool) -> int:
    """This observation's nominal accrual, clamped to what is left of the track's interval.

    The watched interval is `[placed_at, min(cancelled_at, expiry)]` and the counterfactual's is
    `[placed_at, expiry]` (§0.10). An observation at `now` can only account for the part of the
    period that lies inside the interval, so the clamp is the seconds between `now` and the
    interval's end, floored at zero and capped at the period. A past-expiry order held open on a
    lagging ticker therefore accrues nothing, which is I-15's rule and what makes §3 row 5's
    first clause -- `dirty_seconds` never exceeding the resting interval -- true by construction
    rather than by luck.

    An order with no expiry at all accrues the whole period: there is no interval to clamp to,
    and R8 makes that case rare enough that guessing a bound would be worse than not having one.
    """
    end = row.expiry
    if watched and row.cancelled_at is not None:
        end = row.cancelled_at if end is None else min(end, row.cancelled_at)
    if end is None:
        return period
    return max(0, min(period, int((end - now).total_seconds())))


def _next_status(status: str, filled: Decimal, contracts: Decimal) -> str:
    if filled >= contracts:
        return "filled"
    if filled > ZERO:
        return "partially_filled"
    return status
