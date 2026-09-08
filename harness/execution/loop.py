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

No code path here sends an order, a quote or an RFQ answer. There is no venue client in this
module and no credential is ever read: every fill is an inference drawn from the recorded tape.
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

from harness import execution
from harness.execution import store
from harness.execution.book import ZERO, BookState, advance_book, book_age_s, book_at, load_book
from harness.execution.fills import (
    CROSS,
    PaperOrder,
    SimState,
    fill_fee_fields,
    has_print,
    simulate_fills,
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
from harness.pricing.fees import KALSHI_FOOTBALL

log = logging.getLogger(__name__)

CENT = Decimal("0.01")
#: Loop durations kept in-process for the heartbeat's p95. Empty after a restart, deliberately:
#: a p95 carried across a restart would describe a process that is no longer running.
DURATION_WINDOW = 100
QUEUE_MODEL = "queue_model"
NO_WATCHER = "no_watcher"


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


@dataclass
class _TrackResult:
    """One track's outcome for one order: the state to persist and what was actually written."""

    state: SimState
    filled: Decimal
    inserted: int
    crossed: bool


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
                 replay: bool = False, variants: list[str] | None = None) -> None:
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

    # --- the step ---------------------------------------------------------------------

    def step(self) -> ExecStats:
        """One loop. Never raises: a failure is rolled back and recorded on the heartbeat."""
        with self._bind().connect() as conn:
            if not store.try_lock(conn):
                log.info("executor lock held elsewhere; skipping this loop")
                return ExecStats(locked=False)
            # End the lock statement's own transaction: the advisory lock is session-scoped and
            # outlives it, and the ORM session below must be the one that owns the transaction.
            conn.commit()
            try:
                return self._locked_step(conn)
            finally:
                conn.rollback()
                store.unlock(conn)
                conn.commit()

    def _locked_step(self, conn) -> ExecStats:
        started = self._monotonic()
        now = self._clock()
        skipped_loops = self._loops_skipped(started)
        heartbeat = {"ws_last_event_at": None, "book_dirty_markets": 0}
        stats = ExecStats()
        session = Session(bind=conn)
        try:
            try:
                self._body(session, now, stats, heartbeat)
                error = None
            except Exception as exc:  # noqa: BLE001 - the loop must survive any one step
                session.rollback()
                stats = ExecStats(errors=1)
                error = f"{type(exc).__name__}: {exc}"[:2000]
                log.exception("executor step failed")
            stats.loop_ms = int((self._monotonic() - started) * 1000)
            self._durations.append(stats.loop_ms)
            store.write_heartbeat(
                session, last_loop_at=now, open_orders=store.count_open_orders(session, self.replay),
                last_error=error, last_loop_ms=stats.loop_ms, p95_loop_ms=self._p95(),
                loops_skipped=skipped_loops,
                book_dirty_markets=heartbeat["book_dirty_markets"],
                ws_last_event_at=heartbeat["ws_last_event_at"],
                executor_version=execution.EXECUTOR_VERSION)
            session.commit()
        finally:
            session.close()
        return stats

    def _body(self, session: Session, now: datetime, stats: ExecStats, heartbeat: dict) -> None:
        s = self.exec_settings
        variant_ids = store.resolve_variants(session, self._variant_names)
        lower = now - timedelta(seconds=s.intent_ttl_s)

        # 2. Intake.
        candidates = store.candidate_signals(session, variant_ids, lower, self.replay)
        stats.intents_new = store.insert_intents(session, candidates, now, self.replay)
        intents, extras = store.load_intents(session, variant_ids, lower, now + s.cutoff,
                                             self.replay)

        # 3. Books and markets.
        working = store.working_orders(session, self.replay)
        rows = store.market_rows(session, ({i.venue_market_id for i in intents}
                                           | {w.venue_market_id for w in working}))
        ws_last = store.newest_event_ts(session)
        heartbeat["ws_last_event_at"] = ws_last
        # F36: a recorder that stopped writing makes every ladder a stale one, and a stale
        # ladder that still looks tradeable is the failure this check exists to prevent.
        dead_recorder = (ws_last is None
                         or (now - ws_last).total_seconds() > s.book_max_age_s)
        bases, recovering = self._advance_books(session, {r.ticker for r in rows.values()}, now)
        markets = {vm_id: self._market_now(row, dead_recorder) for vm_id, row in rows.items()}
        heartbeat["book_dirty_markets"] = sum(1 for m in markets.values() if m.dirty(now, s))

        # 4. Fills.
        outcomes = self._simulate(session, working, markets, bases, recovering, now, stats)

        # 5. Decisions, applied in order.
        open_orders = [self._order_view(row, outcomes) for row in working
                       if outcomes.get(row.id, (row.status, row.filled_contracts))[0]
                       in store.OPEN_STATUSES]
        cfg = store.variant_configs(session, ({i.variant_id for i in intents}
                                              | {o.variant_id for o in open_orders}))
        intents = self._with_config(intents, cfg, "intent")
        open_orders = self._with_config(open_orders, cfg, "order")
        positions = store.load_positions(session, self.replay)
        midnight = store.local_midnight(now, ZoneInfo(self.settings.tz_local))
        fills_today = store.load_fills_today(session, self.replay, midnight)
        state_by_variant = {variant: rebuild_state(open_orders, positions, fills_today, variant)
                            for variant in cfg}
        actions = plan_actions(intents, open_orders, markets, state_by_variant, cfg,
                               store.kill_active(session), now, s)
        self._apply(session, actions, intents, extras, markets, rows, now, stats)

    # --- books ------------------------------------------------------------------------

    def _advance_books(self, session: Session, tickers: set[str],
                       now: datetime) -> tuple[dict[str, BookState | None], set[str]]:
        """Advance the cache to now and hand back the book each cursor still points at.

        `base` is the previous step's book, copied before the cache moves; the fill step gives
        it to the simulator, which walks its own copy. The cache itself is never handed out and
        never mutated by a simulation.
        """
        bases: dict[str, BookState | None] = {}
        for ticker in sorted(tickers):
            cached = self.books.get(ticker)
            if cached is None:
                book = load_book(session, ticker, now)
                self.books[ticker] = book
                bases[ticker] = book
            else:
                bases[ticker] = cached.copy()
                self.books[ticker] = advance_book(session, cached, now)
        dirty = {t for t in tickers
                 if self.books.get(t) is not None and self.books[t].dirty}
        # A ticker that was dirty last step and is clean now is re-anchored by the fill step:
        # the queue we believed in was built from a tape with a hole in it.
        recovering = {t for t in tickers if t in self._dirty_tickers} - dirty
        self._dirty_tickers = (self._dirty_tickers - tickers) | dirty
        return bases, recovering

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
                  stats: ExecStats) -> dict[int, tuple[str, Decimal]]:
        """Run both tracks for every working order. One order's failure is one order's failure.

        A raise inside a step would otherwise cost every other order its fills for that loop,
        so each order runs inside a savepoint and a failure is counted and stepped over.
        """
        tape = self._tape(session, working)
        outcomes: dict[int, tuple[str, Decimal]] = {}
        for row in working:
            outcomes[row.id] = (row.status, row.filled_contracts)
            try:
                with session.begin_nested():
                    outcomes[row.id] = self._simulate_order(session, row, markets, bases,
                                                            recovering, tape, now, stats)
            except Exception:  # noqa: BLE001 - one order, not the step
                log.exception("fill simulation failed for order %s", row.id)
                stats.errors += 1
        return outcomes

    def _tape(self, session: Session, working) -> dict[str, tuple[list, list]]:
        """One print scan and one delta scan per ticker, shared by every order on it.

        Prints have no cursor (§1) and are rescanned from `placed_at - 60 s` every loop; the
        simulator's print watermark is what makes the re-feed idempotent. Deltas keep the id
        cursor, so the scan starts at the earliest cursor any track on the ticker still holds.
        """
        windows: dict[str, tuple[datetime, int | None]] = {}
        for row in working:
            lower, cursor = windows.get(row.ticker, (row.placed_at, None))
            lower = min(lower, row.placed_at)
            for value in (row.tape_cursor_event_id, row.nw_tape_cursor_event_id):
                if value is not None:
                    cursor = value if cursor is None else min(cursor, value)
            windows[row.ticker] = (lower, cursor)
        out = {}
        for ticker, (placed_at, cursor) in windows.items():
            lower = placed_at - store.PRINT_LOOKBACK
            out[ticker] = (store.load_prints(session, ticker, lower),
                           store.load_deltas(session, ticker, cursor or 0, lower))
        return out

    def _simulate_order(self, session: Session, row, markets, bases, recovering, tape,
                        now: datetime, stats: ExecStats) -> tuple[str, Decimal]:
        s = self.exec_settings
        market = markets.get(row.venue_market_id)
        book = self.books.get(row.ticker)
        if market is not None and market.dirty(now, s):
            # A book we cannot read tells us nothing about the queue, so neither track advances
            # and the order records how long it spent in that state (D6).
            store.add_dirty_seconds(session, row.id, self.settings.exec_period_s)
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
        head = book.last_event_id if book is not None else None

        if row.status in store.OPEN_STATUSES:
            result = simulate_fills(order, watched, self._sim_book(session, row, watched, bases,
                                                                   anchor),
                                    prints, deltas, now, QUEUE_MODEL)
            track = self._persist_track(session, row, order, result, prints, ledger=True)
            stats.fills += track.inserted
            filled = track.filled
            status = _next_status(row.status, filled, row.contracts)
            updates.update(filled_contracts=filled, status=status,
                           **_state_columns("", track.state, head))
            if track.crossed:
                updates["worst_case_fill"] = True
        else:
            filled, status = row.filled_contracts, row.status

        if not row.nw_done:
            deadline = min(now, row.expiry) if row.expiry is not None else now
            result = simulate_fills(order, no_watcher,
                                    self._sim_book(session, row, no_watcher, bases, anchor),
                                    prints, deltas, deadline, NO_WATCHER)
            track = self._persist_track(session, row, order, result, prints, ledger=False)
            stats.nw_fills += track.inserted
            done = ((row.expiry is not None and row.expiry <= now)
                    or track.filled >= row.contracts)
            updates.update(nw_filled_contracts=track.filled, nw_done=done,
                           **_state_columns("nw_", track.state, head))
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
                       ledger: bool) -> _TrackResult:
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
                cash = -(fill.prob * fill.contracts + fill.fee)
                store.insert_ledger_fill(
                    session, ts=fill.filled_at, variant_id=row.variant_id, kind="fill",
                    order_id=row.id, fill_id=fill_id, ticker=row.ticker, side=row.side,
                    contracts=fill.contracts, price=fill.prob, fee=fill.fee,
                    cash_delta=cash.quantize(CENT), replay=self.replay)
        if result.cross is not None:
            # F41's worst case, written once per order: whichever track sees the crossing first
            # inserts it, and the unique key on the crossing row dedupes the other one. It is
            # never a position, never in the ledger and never in P&L.
            store.insert_fill(
                session, order_id=row.id, prob=result.cross.prob,
                contracts=result.cross.contracts, fee=result.cross.fee, fee_type=fee_type,
                fee_multiplier=fee_multiplier, maker_rate=maker_rate,
                filled_at=result.cross.filled_at, simulated=True, fill_method=CROSS,
                source_trade_id=None, source_event_id=result.cross.source_event_id,
                taker_side=None, through=result.cross.through,
                tape_source=result.cross.tape_source,
                has_print=has_print(result.cross, order, prints), replay=self.replay)
        return _TrackResult(state=result.state, filled=filled, inserted=inserted,
                            crossed=result.crossed)

    # --- actions ----------------------------------------------------------------------

    def _apply(self, session: Session, actions, intents, extras, markets, rows,
               now: datetime, stats: ExecStats) -> None:
        by_intent = {intent.intent_id: intent for intent in intents}
        for action in actions:
            try:
                with session.begin_nested():
                    self._apply_one(session, action, by_intent, extras, markets, rows, now,
                                    stats)
            except Exception:  # noqa: BLE001 - one action, not the step
                log.exception("applying %r failed", action)
                stats.errors += 1

    def _apply_one(self, session: Session, action, by_intent, extras, markets, rows,
                   now: datetime, stats: ExecStats) -> None:
        if isinstance(action, Place):
            self._place(session, action, by_intent, extras, markets, rows, now, stats)
        elif isinstance(action, Cancel):
            store.update_order(session, action.order_id,
                               {"status": "cancelled", "cancel_reason": action.reason,
                                "cancelled_at": now})
            store.insert_event(session, order_id=action.order_id, ts=now, kind="cancel",
                               reason=action.reason, replay=self.replay)
            stats.cancelled += 1
        elif isinstance(action, Expire):
            store.update_order(session, action.order_id, {"status": "expired"})
            store.insert_event(session, order_id=action.order_id, ts=now, kind="expire",
                               reason="expiry", replay=self.replay)
            stats.expired += 1
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
        order_id = store.insert_order(session, {
            "intent_id": action.intent_id, "variant_id": intent.variant_id,
            "venue": extra.get("venue") or "kalshi", "mode": "paper",
            "client_order_id": f"{prefix}-{action.intent_id}-{n}", "ticker": intent.ticker,
            "venue_market_id": intent.venue_market_id, "side": action.side,
            "prob": action.prob, "contracts": action.contracts, "status": "open",
            "placed_at": now, "expiry": action.expiry,
            "fair_p_at_place": market.fair_p, "fair_row_id_at_place": market.fair_row_id,
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
            "replay": self.replay})
        if order_id is None:
            return
        stats.placed += 1
        store.insert_event(session, order_id=order_id, intent_id=action.intent_id, ts=now,
                           kind="place", prob=action.prob, contracts=action.contracts,
                           fair_p_at_event=market.fair_p, replay=self.replay)
        if action.no_book:
            # The order rests, but the record says the queue behind it was unknowable (R10).
            store.insert_event(session, intent_id=action.intent_id, ts=now, kind="skipped",
                               reason="no_book", replay=self.replay)

    # --- small helpers ----------------------------------------------------------------

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


def _state_columns(prefix: str, state: SimState, head: int | None) -> dict:
    """The columns one track's `SimState` is persisted in.

    The cursor lands on the tape's head for this ticker rather than on the last row the
    simulator happened to consume, so every cursor on a ticker agrees after the step and the
    next loop re-reads nothing it has already seen.
    """
    cursor = state.cursor_event_id
    if head is not None:
        cursor = head if cursor is None else max(cursor, head)
    return {f"{prefix}queue_remaining": state.queue_remaining,
            f"{prefix}traded_at_price": state.traded_at_price,
            f"{prefix}tape_cursor_event_id": cursor,
            f"{prefix}crossed": state.crossed,
            f"{prefix}last_print_ts": state.last_print_ts,
            f"{prefix}last_print_ids": list(state.last_print_ids)}


def _next_status(status: str, filled: Decimal, contracts: Decimal) -> str:
    if filled >= contracts:
        return "filled"
    if filled > ZERO:
        return "partially_filled"
    return status
