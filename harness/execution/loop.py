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

import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from harness import execution, telemetry
from harness.execution import store
from harness.execution.book import (
    ZERO,
    BookState,
    advance_book,
    advance_book_at,
    book_age_s,
    book_at,
    load_book,
    load_book_at,
    side_p,
)
from harness.execution.fills import (
    CROSS,
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
from harness.pricing.fees import KALSHI_FOOTBALL, fee_for_order

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
#: Loop durations kept in-process for the heartbeat's p95. Empty after a restart, deliberately:
#: a p95 carried across a restart would describe a process that is no longer running.
DURATION_WINDOW = 100
QUEUE_MODEL = "queue_model"
NO_WATCHER = "no_watcher"
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
    loop_ms: int = 0
    locked: bool = True
    #: The first failure of the step, the same string the heartbeat's `last_error` carries.
    #: A live loop reads it off the heartbeat; a replay writes no heartbeat and needs the
    #: message here to fail its own command with it (fix round 1, I2).
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

    def __post_init__(self) -> None:
        self.cancelled = self.cancelled or {}
        self.skipped = self.skipped or {}

    def reset(self) -> None:
        self.intents_considered = 0
        self.placed = 0
        self.cancelled = {}
        self.skipped = {}
        self.filled_contracts = ZERO
        self.loops_skipped = 0


@dataclass
class _TrackResult:
    """One track's outcome for one order: the state to persist and what was actually written."""

    state: SimState
    filled: Decimal
    inserted: int
    crossed: bool
    cross_written: bool = False


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
                error = _with_tape_lag(heartbeat["last_error"], heartbeat["tape_lag"])
            except Exception as exc:  # noqa: BLE001 - the loop must survive any one step
                session.rollback()
                stats = ExecStats(errors=1)
                error = f"{type(exc).__name__}: {exc}"[:2000]
                log.exception("executor step failed")
            stats.loop_ms = int((self._monotonic() - started) * 1000)
            stats.last_error = error
            self._durations.append(stats.loop_ms)
            wrote_metrics = False
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
            session.close()
        return stats

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
            ("exec.intents_considered", acc.intents_considered, {}),
            ("exec.placed", acc.placed, {}),
            ("exec.filled_contracts", acc.filled_contracts, {}),
        ]
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
        candidates = store.candidate_signals(session, variant_ids, lower, self.replay, at)
        stats.intents_new = store.insert_intents(session, candidates, now, self.replay)
        intents, extras = store.load_intents(session, variant_ids, lower, self.replay, at)

        # 3. Books and markets.
        working = store.working_orders(session, self.replay)
        rows = store.market_rows(session, ({i.venue_market_id for i in intents}
                                           | {w.venue_market_id for w in working}), at)
        ws_last = store.newest_event_ts(session, at)
        heartbeat["ws_last_event_at"] = ws_last
        # Section 2.2's "30 s without a ping", which is a live-only reprice rule: the gateway
        # needs the tape position this step already computed, and `PaperGateway` discards it
        # (Task 10 fix round 1, Important 3).
        self.gateway.observe_tape(ws_last)
        # F36: a recorder that stopped writing makes every ladder a stale one, and a stale
        # ladder that still looks tradeable is the failure this check exists to prevent.
        dead_recorder = (ws_last is None
                         or (now - ws_last).total_seconds() > s.book_max_age_s)
        bases, recovering = self._advance_books(session, {r.ticker for r in rows.values()}, now)
        markets = {vm_id: self._market_now(row, dead_recorder) for vm_id, row in rows.items()}
        heartbeat["book_dirty_markets"] = sum(1 for m in markets.values() if m.dirty(now, s))

        # 4. Fills.
        outcomes = self._simulate(session, working, markets, bases, recovering, now, stats,
                                  heartbeat)

        # 5. Decisions, applied in order.
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
                               store.kill_active(session), now, s)
        self._apply(session, actions, intents, extras, markets, rows, now, stats, heartbeat)

        if not self.replay:
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

    # --- books ------------------------------------------------------------------------

    def _advance_books(self, session: Session, tickers: set[str],
                       now: datetime) -> tuple[dict[str, BookState | None], set[str]]:
        """Advance the cache to now and hand back the book each cursor still points at.

        `base` is the previous step's book, copied before the cache moves; the fill step gives
        it to the simulator, which walks its own copy. The cache itself is never handed out and
        never mutated by a simulation.
        """
        bases: dict[str, BookState | None] = {}
        re_anchored: set[str] = set()
        for ticker in sorted(tickers):
            cached = self.books.get(ticker)
            if cached is None:
                book = self._book_now(session, ticker, now, None)
                self.books[ticker] = book
                bases[ticker] = book
            else:
                base = cached.copy()
                bases[ticker] = base
                advanced = self._book_now(session, ticker, now, cached)
                self.books[ticker] = advanced
                if (advanced.anchor_id, advanced.source) != (base.anchor_id, base.source):
                    # `advance_book` re-anchors inside a single call when a gap is followed by a
                    # clean snapshot, so a gap and its resubscribe can both land between two 15 s
                    # steps and leave the book clean at either boundary. Comparing anchors is what
                    # catches that; comparing dirtiness across steps would not.
                    re_anchored.add(ticker)
        dirty = {t for t in tickers
                 if self.books.get(t) is not None and self.books[t].dirty}
        # A ticker that was dirty last step and is clean now, or one whose anchor moved inside
        # this step, is re-anchored by the fill step: the queue we believed in was built from a
        # tape with a hole in it.
        recovering = ({t for t in tickers if t in self._dirty_tickers} | re_anchored) - dirty
        self._dirty_tickers = dirty
        # One BookState per ticker ever traded would accumulate all season, and a dormant entry
        # would later be advanced from a very old `as_of`.
        self.books = {t: book for t, book in self.books.items() if t in tickers}
        return bases, recovering

    def _book_now(self, session: Session, ticker: str, now: datetime,
                  cached: BookState | None) -> BookState | None:
        """The ticker's book at `now`: the tape's head live, the past instant in replay.

        The two paths are the same two calls. `load_book`/`advance_book` run to the head of the
        tape with no upper `ts` bound, which is right for a loop whose clock *is* the head and
        catastrophic for one whose clock is three days behind it, so replay takes
        `load_book_at`/`advance_book_at` -- the same rules with the instant as their upper
        bound (fix round 1, I1 and I2). Both paths build once and advance afterwards: a replay
        that rebuilt each book from its anchor at every 15 s step would be quadratic in the
        day's tape.
        """
        if self.replay:
            return (load_book_at(session, ticker, now) if cached is None
                    else advance_book_at(session, cached, now))
        return (load_book(session, ticker, now) if cached is None
                else advance_book(session, cached, now))

    def _market_now(self, row, dead_recorder: bool) -> MarketNow:
        return MarketNow(
            venue_market_id=row.venue_market_id, ticker=row.ticker, fair_p=row.fair_p,
            fair_ts=row.fair_ts, fair_row_id=row.fair_value_id, staleness_s=row.staleness_s,
            stale_allowance_s=row.stale_allowance_s, feed_kind=row.feed_kind,
            best_bid_yes=row.best_bid, best_ask_yes=row.best_ask, mid_yes=row.venue_mid,
            book=self.books.get(row.ticker), book_dirty=dead_recorder,
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
        tape, unread = self._tape(session, working, now, heartbeat)
        stats.errors += len(unread)
        outcomes: dict[int, tuple[str, Decimal]] = {}
        for row in working:
            outcomes[row.id] = (row.status, row.filled_contracts)
            if row.ticker in unread:
                # This ticker's tape read failed (fix 22: a statement timeout is the one we have
                # actually seen). Simulating its orders against an empty tape would move their
                # print watermarks and their cross flags on evidence we do not have, so the
                # whole ticker sits this loop out with its cursors where they are. Every other
                # ticker in this loop keeps its progress.
                continue
            try:
                with session.begin_nested():
                    outcomes[row.id] = self._simulate_order(session, row, markets, bases,
                                                            recovering, tape, now, stats)
            except Exception as exc:  # noqa: BLE001 - one order, not the step
                log.exception("fill simulation failed for order %s", row.id)
                stats.errors += 1
                _note_error(heartbeat, f"order {row.id}: {type(exc).__name__}: {exc}")
        return outcomes

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
              heartbeat: dict) -> tuple[dict[str, tuple[list, list]], set[str]]:
        """One print scan and one delta scan per ticker, shared by every order on it.

        Prints have no cursor (§1) and are rescanned from `placed_at - 60 s` every loop; the
        simulator's print watermark is what makes the re-feed idempotent. Deltas keep the id
        cursor, so the scan starts at the earliest cursor any track on the ticker *is still
        going to read* -- the watched cursor of a cancelled order and the no-watcher cursor of
        a finished counterfactual are both frozen where that track stopped, and folding them in
        would drag the ticker's scan back to a position no live track will ever consume from
        (fix 22). Both scans stop at `_at(now)` in replay: `simulate_fills` already refuses to
        walk past its deadline, but `has_print` reads the whole print list.

        Returns the tape and the set of tickers whose read failed. Each ticker is read inside
        its own savepoint, so one ticker's statement timeout rolls back to the savepoint and
        leaves this transaction -- and every cursor already advanced in this loop -- intact.
        """
        windows: dict[str, tuple[datetime, int | None]] = {}
        for row in working:
            lower, cursor = windows.get(row.ticker, (row.placed_at, None))
            lower = min(lower, row.placed_at)
            live = []
            if row.status in store.OPEN_STATUSES:
                live.append(row.tape_cursor_event_id)
            if not row.nw_done:
                live.append(row.nw_tape_cursor_event_id)
            for value in live:
                if value is not None:
                    cursor = value if cursor is None else min(cursor, value)
            windows[row.ticker] = (lower, cursor)
        out: dict[str, tuple[list, list]] = {}
        unread: set[str] = set()
        lagging: list[str] = []
        at = self._at(now)
        for ticker, (placed_at, cursor) in windows.items():
            lower = placed_at - store.PRINT_LOOKBACK
            try:
                with session.begin_nested():
                    prints = store.load_prints(session, ticker, lower, at)
                    batch = store.load_deltas(session, ticker, cursor or 0, lower, at)
            except Exception as exc:  # noqa: BLE001 - one ticker, not the step
                log.exception("tape read failed for %s", ticker)
                unread.add(ticker)
                _note_error(heartbeat, f"tape {ticker}: {type(exc).__name__}: {exc}")
                continue
            deltas = batch.deltas
            if batch.truncated and deltas:
                # The read stopped at its limit, so this ticker's tape has more behind it than
                # this loop asked for and the last row we hold is a position, not the head.
                # Prints past that position are dropped rather than fed early: a print applied
                # ahead of the deltas that belong with it is applied against the wrong queue,
                # and the simulator's watermark would then refuse to reconsider it. Nothing is
                # lost -- prints carry no cursor and the next loop rescans the same window,
                # by which time the deltas have caught up.
                covered = deltas[-1].ts
                prints = [p for p in prints if p.ts <= covered]
                lagging.append(ticker)
            out[ticker] = (prints, deltas)
        if lagging:
            heartbeat["tape_lag"] = sorted(lagging)
        return out, unread

    def _simulate_order(self, session: Session, row, markets, bases, recovering, tape,
                        now: datetime, stats: ExecStats) -> tuple[str, Decimal]:
        s = self.exec_settings
        market = markets.get(row.venue_market_id)
        book = self.books.get(row.ticker)
        if market is not None and market.dirty(now, s):
            # A book we cannot read tells us nothing about the queue, so neither track advances
            # and the order records how long it spent in that state (D6).
            store.add_dirty_seconds(session, row.id, self.settings.exec_period_s)
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
            for state in (watched, no_watcher):
                state.queue_remaining = queue
                state.traded_at_price = ZERO
                state.cursor_event_id = book.last_event_id
                state.last_print_ts = now
                state.last_print_ids = ()
            anchor = book
            updates.update(queue_ahead_at_place=queue, book_source=book.source,
                           book_age_s=book_age_s(book, now), book_first_seen_at=now)
        elif row.ticker in recovering and book is not None:
            # The first clean book after a dirty stretch. Our queue was built from a tape with
            # a hole in it, so it is taken down to whatever is actually resting there now.
            resting = book.resting_at(row.side, row.prob)
            for state in (watched, no_watcher):
                if state.queue_remaining is not None:
                    state.queue_remaining = min(state.queue_remaining, resting)
                state.cursor_event_id = book.last_event_id
            anchor = book

        prints, deltas = tape.get(row.ticker, ([], []))
        order = PaperOrder(order_id=row.id, ticker=row.ticker, side=row.side, prob=row.prob,
                           contracts=row.contracts, placed_at=row.placed_at,
                           expiry=row.expiry or now,
                           queue_ahead_at_place=updates.get("queue_ahead_at_place",
                                                            row.queue_ahead_at_place))
        # F41's worst case is one fill per order, not one per track. The two tracks stop at
        # different deadlines, so after a cancel their cursors diverge and `_sim_book` hands
        # them different books, which would stamp two different crossing ids on one order.
        crossed_already = bool(row.crossed or row.nw_crossed)

        if row.status in store.OPEN_STATUSES:
            result = simulate_fills(order, watched, self._sim_book(session, row, watched, bases,
                                                                   anchor),
                                    prints, deltas, now, QUEUE_MODEL)
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
            deadline = min(now, row.expiry) if row.expiry is not None else now
            result = simulate_fills(order, no_watcher,
                                    self._sim_book(session, row, no_watcher, bases, anchor),
                                    prints, deltas, deadline, NO_WATCHER)
            track = self._persist_track(session, row, order, result, prints, ledger=False,
                                        crossed_already=crossed_already)
            stats.nw_fills += track.inserted
            done = ((row.expiry is not None and row.expiry <= now)
                    or track.filled >= row.contracts)
            updates.update(nw_filled_contracts=track.filled, nw_done=done,
                           **_state_columns("nw_", track.state))
            if track.crossed:
                updates["worst_case_fill"] = True

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
                return book_at(session, row.ticker, at)
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
                               reason="expiry", replay=self.replay)
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
            "traded_at_price": ZERO, "nw_queue_remaining": queue,
            "nw_traded_at_price": ZERO,
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
        store.insert_event(session, order_id=order_id, intent_id=action.intent_id, ts=now,
                           kind="place", prob=action.prob, contracts=action.contracts,
                           fair_p_at_event=market.fair_p, replay=self.replay)
        if action.no_book:
            # The order rests, but the record says the queue behind it was unknowable (R10).
            store.insert_event(session, intent_id=action.intent_id, ts=now, kind="skipped",
                               reason="no_book", replay=self.replay)

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


def _note_error(heartbeat: dict, message: str) -> None:
    """Keep the first failure of the step; a later one rarely explains more than the first."""
    if heartbeat["last_error"] is None:
        heartbeat["last_error"] = message[:2000]


def _with_tape_lag(error: str | None, lagging: list[str]) -> str | None:
    """Append the loop's tape lag to whatever it is already reporting.

    A truncated delta read means the executor is behind that ticker's tape and its books are
    only current to the last row it consumed (fix 22). That is not a failure -- the loop is
    catching up by design and will close the gap over the next few loops -- so it neither
    raises nor displaces a real error, but a heartbeat that said nothing about it would let the
    executor run minutes behind the tape looking perfectly healthy. It is appended rather than
    written through `_note_error`, which keeps only the first message of a step.
    """
    if not lagging:
        return error
    note = f"tape_lag: {len(lagging)} ticker(s) behind ({','.join(lagging[:5])})"
    return note[:2000] if error is None else f"{error}; {note}"[:2000]


def _state_of(row, prefix: str) -> SimState:
    """The persisted `SimState` of one track, read off the order row."""
    return SimState(
        queue_remaining=getattr(row, f"{prefix}queue_remaining"),
        traded_at_price=getattr(row, f"{prefix}traded_at_price") or ZERO,
        filled_contracts=getattr(row, f"{prefix}filled_contracts"),
        cursor_event_id=getattr(row, f"{prefix}tape_cursor_event_id"),
        crossed=bool(getattr(row, f"{prefix}crossed")),
        last_print_ts=getattr(row, f"{prefix}last_print_ts"),
        last_print_ids=tuple(getattr(row, f"{prefix}last_print_ids") or ()))


def _state_columns(prefix: str, state: SimState) -> dict:
    """The columns one track's `SimState` is persisted in.

    The cursor is the last tape row the simulation actually consumed and nothing else. The
    cache's own head runs ahead of it -- `advance_book` folds a delta in on `id` with no upper
    `ts` bound while `_merge_events` stops at the track's deadline -- so writing the head back
    would jump the order over a delta stamped ahead of our clock. The next step reaches that
    delta through `_sim_book`'s `book_at(ts_of(cursor))` branch instead.
    """
    cursor = state.cursor_event_id
    return {f"{prefix}queue_remaining": state.queue_remaining,
            f"{prefix}traded_at_price": state.traded_at_price,
            f"{prefix}tape_cursor_event_id": cursor,
            f"{prefix}crossed": state.crossed,
            f"{prefix}last_print_ts": state.last_print_ts,
            f"{prefix}last_print_ids": list(state.last_print_ids)}


def _venue_status(was: str, filled: Decimal, contracts: Decimal) -> str:
    """The status a venue fill leaves an order in. An order that had already left the book keeps
    the status it left with: the fill is recorded, the position is real, and the order is still
    not resting (review round 1, Important 3)."""
    if was not in store.OPEN_STATUSES:
        return was
    return _next_status(was, filled, contracts)


def _next_status(status: str, filled: Decimal, contracts: Decimal) -> str:
    if filled >= contracts:
        return "filled"
    if filled > ZERO:
        return "partially_filled"
    return status
