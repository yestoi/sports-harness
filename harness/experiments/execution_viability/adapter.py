"""§1.3(c): the stateful adapter around the **shared** decision and fill functions.

Approach 3 of the three the addendum weighs. `replay.py::_execute` was rejected (a 15 s grid, a
real `Executor` writing production tables under `replay = true`, the replay advisory lock) and
`policy.compare` was rejected (it carries no state). What is here is a runner that steps the
retained decision instants `capture.resolve_instants()` resolved and, at each one, calls the same
functions the live executor calls, with the **arm's own** world instead of an empty one.

Nothing in this module re-implements a rule. `plan.plan_actions` decides, `plan.rebuild_state`
rebuilds exposure, `book.BookWalker` reconstructs the ladder and `fills.simulate_fills` fills; the
adapter only carries the arm's own orders between instants and turns the shared functions'
results into records. Where a step would need a decision the shared functions do not expose, the
task stops and reports rather than writing a second implementation.

One dependency is named here because it is the module's only reach into a production
implementation detail: `_market_now` reuses `policy._market_now` (a private function of
`harness/execution/policy.py`) for the row-to-`MarketNow` field mapping, and adds the two
things that comparison states it does not carry -- the rebuilt book and the recorded dirty
verdict. If that function's signature or field mapping changes, this adapter changes with it;
T3/T4 must not copy the mapping instead.

Two properties are structural rather than asserted:

* **No lookahead.** Every read is taken at the instant (`at=instant`), so nothing taped after it
  is in the data the shared functions are handed -- including `simulate_fills`, whose `deadline`
  is the arm's own cancel/expiry instant but whose tape ends at the instant being stepped.
* **No production write.** Every statement here is a select, and in a run the session is
  §1.1(b)'s read-only reader, which the server itself refuses a write on.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, fields as dc_fields, is_dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

# Modules, not names: `store.market_rows` must be looked up at call time so a test can wrap the
# real function and see the `at=` it was called with (6B's I-13 rule).
from harness.execution import book as book_mod
from harness.execution import fills as fills_mod
from harness.execution import plan as plan_mod
from harness.execution import policy as policy_mod
from harness.execution import store
from harness.experiments.execution_viability.liquidity import LedgerKey, PortfolioLedger

log = logging.getLogger("harness.exp")   # the runner's own reads, never a write

ZERO = Decimal("0")

#: The fill method an arm's own queue simulation writes, matching the live watched track.
FILL_METHOD = "queue_model"

#: §1.5/ruling I13(b): the shared simulator's cancel policy, not a second one.
CANCEL_POLICY = fills_mod.AHEAD

#: Is this market inside a recorded dirty interval at the instant? `market_dirty_intervals` is the
#: loop's own record of when a book could not be trusted (models.py:1010), read here rather than
#: recomputed. Bounded by the markets of the step and the instant, on
#: `ix_mdi_market_started (venue_market_id, started_at)`.
_DIRTY_AT = text(
    "select distinct venue_market_id from market_dirty_intervals "
    "where venue_market_id = any(:ids) "
    "and started_at <= :instant and (ended_at is null or ended_at >= :instant)")


@dataclass(frozen=True, slots=True)
class StepResult:
    """What one instant did: the actions planned, the fills the tape produced, the orders left
    resting afterwards, and whether any of the step's markets was inside a dirty interval."""

    instant: datetime
    actions: tuple[dict, ...]
    fills: tuple[dict, ...]
    open_orders: tuple[dict, ...]
    dirty: bool


@dataclass
class ArmWorld:
    """One arm's own world (§1.4): its orders, its exposure and its per-order queue state.

    Independent by construction -- two arms in one process share nothing but the read-only
    session -- and the object T3 persists into `exp_checkpoint` and resumes from.
    """

    open_orders: list = field(default_factory=list)          # list[plan.OpenOrderView]
    positions: list = field(default_factory=list)            # list[plan.PositionView]
    #: Derived, never appended to directly: `ArmRunner.step` rebuilds it from `fill_log` at the
    #: local midnight of the instant being stepped, so it is *today's* fills at that instant.
    fills_today: list = field(default_factory=list)          # list[plan.FillView]
    #: Every fill the arm has taken, with the stamp the day boundary is applied to:
    #: `(filled_at, variant_id, stake)`. `FillView` carries no time of its own.
    fill_log: list = field(default_factory=list)
    paper: dict = field(default_factory=dict)                # order_id -> fills.PaperOrder
    sim: dict = field(default_factory=dict)                  # order_id -> fills.SimState
    deadlines: dict = field(default_factory=dict)            # order_id -> the arm's own deadline
    cursor_event_id: int | None = None
    next_order_id: int = -1                                  # arm ids are negative: never a row's

    def order(self, order_id: int):
        for view in self.open_orders:
            if view.order_id == order_id:
                return view
        return None


class ArmRunner:
    """One arm stepping the retained decision instants through the shared functions."""

    def __init__(self, *, run_id: str, arm_id: str, policy, variant_cfg: dict[str, dict],
                 exec_settings, walkers: dict | None = None,
                 world: ArmWorld | None = None, tz: str | None = None,
                 ledger: PortfolioLedger | None = None) -> None:
        self.run_id = run_id
        self.arm_id = arm_id
        # §1.6: the arms differ only in the policy argument and in which observations they may
        # read. `None` is the baseline, which is what `plan_actions` defaults to.
        self.policy = policy if policy is not None else plan_mod.BASELINE
        self.variant_cfg = variant_cfg
        # `ExecSettings` is the frozen copy every decision is hashed against; a caller holding a
        # whole `Settings` is converted here rather than at every call site.
        self.exec_settings = (exec_settings if isinstance(exec_settings, plan_mod.ExecSettings)
                              else plan_mod.ExecSettings.from_settings(exec_settings))
        #: One walker per ticker for the whole slice (§1.3c): `book_at` rebuilds from the anchor
        #: on every call, which row 86 measured at 233 ms per reconstruction. Missing tickers are
        #: added as they are met, so a caller may pass none at all.
        self.walkers: dict = dict(walkers or {})
        #: The zone the daily cap's day boundary is taken in (`store.local_midnight`, amendment
        #: 2). `ExecSettings` carries no timezone, so a caller holding only that one passes it.
        self.tz = ZoneInfo(tz or getattr(exec_settings, "tz_local", None) or "America/Chicago")
        self.world = world if world is not None else ArmWorld()
        #: §1.5: the layer above `simulate_fills`. One ledger per arm, keyed by portfolio
        #: identity inside it, so two arms in one process share no consumed quantity either.
        self.ledger = ledger if ledger is not None else PortfolioLedger()
        #: trade_id -> (ticker, taker_side, ts), so an allocation is keyed exactly as the print
        #: was observed. The tape's own `taker_side`, never a side inferred from our order. The
        #: stamp is carried only to bound this dict (`_prune_print_keys`).
        self._print_keys: dict[str, tuple[str, str, datetime | None]] = {}
        #: order_id -> the `exp_order` row as it stands, updated in place through the order's
        #: lifecycle (I5: no `exp_order_event` table; the row carries every transition).
        self.orders: dict[int, dict] = {}
        self._market_ids: set[int] = set()
        #: order_id -> its market, kept for the step in which an order is closed: the view is
        #: gone from `open_orders` by then and a fill of that step still has to name its market.
        self._order_markets: dict[int, int] = {}
        #: The last instant this arm has decided, carried in the checkpoint so a resumed chunk
        #: never re-decides one (§1.4's chunk boundary).
        self.last_instant: datetime | None = None
        #: The intents whose placement the shared pool refused at the previous instant (§1.4).
        #: A blocked *episode* is one row; an intent that is still blocked at the next instant
        #: is the same episode, not a new one (fix round 1, I4).
        self.capacity_blocked: set[str] = set()

    # --- world ---------------------------------------------------------------------

    def adopt(self, order, *, queue_ahead: Decimal | None = None) -> int:
        """Take ownership of a resting order the arm starts the slice holding (§1.3e).

        `order` is an `orders` row (or anything with its columns): the arm copies it into its own
        world and never writes to it again.
        """
        view = plan_mod.OpenOrderView(
            order_id=order.id, intent_id=order.intent_id, variant_id=order.variant_id,
            ticker=order.ticker, venue_market_id=order.venue_market_id, side=order.side,
            prob=order.prob, contracts=order.contracts,
            filled_contracts=getattr(order, "filled_contracts", ZERO) or ZERO,
            placed_at=order.placed_at, expiry=order.expiry,
            fair_p_at_place=getattr(order, "fair_p_at_place", None),
            venue_mid_at_place=getattr(order, "venue_mid_at_place", None),
            edge_min_at_place=getattr(order, "edge_min_at_place", None),
            as_at_place=getattr(order, "as_at_place", None),
            kickoff_utc=getattr(order, "kickoff_utc", None), game_id=getattr(order, "game_id", None),
            stake=(order.prob * order.contracts), match_key=getattr(order, "match_key", None))
        ahead = queue_ahead if queue_ahead is not None else getattr(
            order, "queue_ahead_at_place", None)
        paper = fills_mod.PaperOrder(
            order_id=view.order_id, ticker=view.ticker, side=view.side, prob=view.prob,
            contracts=view.contracts, placed_at=view.placed_at,
            expiry=view.expiry if view.expiry is not None else view.placed_at,
            queue_ahead_at_place=ahead)
        self.world.open_orders.append(view)
        self.world.paper[view.order_id] = paper
        self.world.sim[view.order_id] = fills_mod.SimState.initial(paper)
        self.world.deadlines[view.order_id] = paper.expiry
        self._market_ids.add(view.venue_market_id)
        self._order_markets[view.order_id] = view.venue_market_id
        return view.order_id

    # --- one instant ---------------------------------------------------------------

    def run(self, session: Session, instants: Sequence[datetime]) -> list[StepResult]:
        """Step exactly the instants given, in the order given: the replay clock is resolved by
        `capture.resolve_instants`, never by this runner."""
        return [self.step(session, instant) for instant in instants]

    def step(self, session: Session, instant: datetime) -> StepResult:
        """§1.3(c)'s call order, with the arm's own world at every point."""
        self._roll_day(instant)
        known = {view.venue_market_id for view in self.world.open_orders} | self._market_ids
        markets_rows = store.market_rows(session, known, at=instant)
        lower = instant - timedelta(seconds=self.exec_settings.intent_ttl_s)
        # §0.12: `replay` is the production executor's own flag and is not reused -- the arm
        # reads the live tape at a past instant, which is what `at=` is for.
        intents, _extras = store.load_intents(session, list(self.variant_cfg), lower,
                                              replay=False, at=instant)
        fresh = {view.venue_market_id for view in intents} - set(markets_rows)
        if fresh:
            # A market this arm has not priced before, read at the same instant rather than
            # carried over from the previous one: a second bounded read, never a wider first one.
            markets_rows |= store.market_rows(session, fresh, at=instant)
        self._market_ids |= {view.venue_market_id for view in intents}

        # The loop's own recorded verdict about these books at this instant, read before the
        # markets are built so it is an **input** to the decision (`MarketNow.book_dirty`) and
        # not only a label on the result.
        dirty_ids = self._dirty_ids(session, sorted(markets_rows), instant)
        markets = {market_id: self._market_now(session, row, instant,
                                               book_dirty=market_id in dirty_ids)
                   for market_id, row in markets_rows.items()}
        variants = {view.variant_id for view in intents} | {
            view.variant_id for view in self.world.open_orders}
        state_by_variant = {variant: plan_mod.rebuild_state(
            self.world.open_orders, self.world.positions, self.world.fills_today, variant)
            for variant in variants}
        actions = plan_mod.plan_actions(
            intents, list(self.world.open_orders), markets, state_by_variant, self.variant_cfg,
            kill_active=False, now=instant, s=self.exec_settings, lagging=frozenset(),
            policy=self.policy)
        records, closing = self._apply(actions, intents, markets, instant)
        filled = self._simulate(session, markets, instant, closing)
        self._prune_print_keys(instant)
        return StepResult(instant=instant, actions=tuple(records), fills=tuple(filled),
                          open_orders=tuple(self._resting()),
                          dirty=self._dirty(markets, instant, dirty_ids))

    def _roll_day(self, instant: datetime) -> None:
        """Drop the fills of previous local days before the caps are rebuilt (amendment 2).

        `rebuild_state` sums `fills_today` into `daily_exposure`, so an arm that never pruned
        would carry yesterday's stake into today's daily cap and refuse placements the live
        executor made. The boundary is `store.local_midnight`, the executor's own.
        """
        midnight = store.local_midnight(instant, self.tz)
        self.world.fill_log = [row for row in self.world.fill_log if row[0] >= midnight]
        self.world.fills_today = [plan_mod.FillView(variant_id=variant, stake=stake)
                                  for _ts, variant, stake in self.world.fill_log]

    # --- the shared functions' inputs ------------------------------------------------

    def _walker(self, session: Session, ticker: str):
        walker = self.walkers.get(ticker)
        if walker is None:
            walker = self.walkers[ticker] = book_mod.BookWalker(session, ticker)
        return walker

    def _market_now(self, session: Session, row, instant: datetime, *,
                    book_dirty: bool = False):
        """The market as this arm sees it at `instant`, with the ladder the walker rebuilt.

        The field mapping is `policy._market_now`'s, reused rather than copied: a second copy of
        it would be free to drift from the one the policy comparison reads. Two things are added
        here, both of which that comparison states it does not carry: the rebuilt book, and the
        loop's own recorded dirty verdict, which `MarketNow.dirty` reads as `book_dirty` and
        which is a **shared input** to the decision under §1.3(c).
        """
        market = policy_mod._market_now(row)
        return replace(market, book=self._walker(session, row.ticker).at(instant),
                       book_dirty=book_dirty)

    def _dirty_ids(self, session: Session, ids: Sequence[int], instant: datetime) -> set[int]:
        """The step's markets inside a recorded dirty interval at the instant.

        `market_dirty_intervals` is the loop's own record of when a book could not be trusted
        (models.py:1010), read here rather than recomputed, bounded by the markets of the step
        and the instant on `ix_mdi_market_started (venue_market_id, started_at)`.
        """
        if not ids:
            return set()
        return {row.venue_market_id for row in
                session.execute(_DIRTY_AT, {"ids": list(ids), "instant": instant}).all()}

    def _dirty(self, markets: dict, instant: datetime, dirty_ids: set[int]) -> bool:
        """Was any of the step's markets untrustworthy at the instant?

        Two sources, both the loop's own: the book's verdict about itself (`MarketNow.dirty`,
        which is F36's rule, not a new one) and the recorded dirty interval covering the
        instant. The second is kept even where there is no book to apply it to, because a
        market with no book is not `MarketNow.dirty` (R10) and the interval still happened.
        """
        if any(market.dirty(instant, self.exec_settings) for market in markets.values()):
            return True
        return bool(dirty_ids)

    # --- bookkeeping (the arm's own world, never a rule) -------------------------------

    def _apply(self, actions, intents, markets, instant: datetime):
        by_intent = {view.intent_id: view for view in intents}
        records: list[dict] = []
        closing: list[int] = []
        for action in actions:
            if isinstance(action, plan_mod.Place):
                records.append(self._place(action, by_intent[action.intent_id], markets, instant))
            elif isinstance(action, plan_mod.Cancel):
                records.append(self._close(action.order_id, "cancel", instant, instant,
                                           reason=action.reason))
                closing.append(action.order_id)
            elif isinstance(action, plan_mod.Expire):
                view = self.world.order(action.order_id)
                deadline = view.expiry if view is not None and view.expiry else instant
                records.append(self._close(action.order_id, "expire", instant, deadline))
                closing.append(action.order_id)
            elif isinstance(action, plan_mod.Skip):
                records.append({"kind": "skip", "instant": instant, "reason": action.reason,
                                "intent_id": action.intent_id, "arm_id": self.arm_id,
                                "run_id": self.run_id})
            elif isinstance(action, plan_mod.CapGate):
                records.append({"kind": "cap_gate", "instant": instant, "reason": action.reason,
                                "blocking": action.blocking, "intent_id": action.intent_id,
                                "arm_id": self.arm_id, "run_id": self.run_id})
        return records, closing

    def _place(self, action, intent, markets, instant: datetime) -> dict:
        order_id = self.world.next_order_id
        self.world.next_order_id -= 1
        market = markets.get(intent.venue_market_id)
        ladder = market.book if market is not None else None
        # The queue the order joins is the book's own count at our price on our side; R10's
        # `no_book` placement has none, and `SimState.initial` keeps it out of simulation.
        queue_ahead = None if ladder is None else ladder.resting_at(action.side, action.prob)
        view = plan_mod.OpenOrderView(
            order_id=order_id, intent_id=intent.intent_id, variant_id=intent.variant_id,
            ticker=intent.ticker, venue_market_id=intent.venue_market_id, side=action.side,
            prob=action.prob, contracts=action.contracts, filled_contracts=ZERO,
            placed_at=instant, expiry=action.expiry,
            fair_p_at_place=market.fair_p if market is not None else None,
            venue_mid_at_place=market.mid_yes if market is not None else None,
            edge_min_at_place=intent.edge_min, as_at_place=None, kickoff_utc=intent.kickoff_utc,
            game_id=intent.game_id,
            stake=intent.stake if intent.stake is not None else action.prob * action.contracts,
            match_key=market.match_key if market is not None else None)
        paper = fills_mod.PaperOrder(order_id=order_id, ticker=view.ticker, side=view.side,
                                     prob=view.prob, contracts=view.contracts, placed_at=instant,
                                     expiry=action.expiry, queue_ahead_at_place=queue_ahead)
        self.world.open_orders.append(view)
        self.world.paper[order_id] = paper
        self.world.sim[order_id] = fills_mod.SimState.initial(paper)
        self.world.deadlines[order_id] = action.expiry
        self._order_markets[order_id] = view.venue_market_id
        # `id` is the database's surrogate key (D22) and is never written here: the arm's own
        # negative id is `arm_order_id`, which is what the writer upserts on.
        self.orders[order_id] = {
            "run_id": self.run_id, "arm_id": self.arm_id, "arm_order_id": order_id,
            "variant_id": intent.variant_id, "intent_id": None,
            "venue_market_id": view.venue_market_id, "ticker": view.ticker, "side": view.side,
            "prob": view.prob, "contracts": view.contracts, "filled_contracts": ZERO,
            "placed_at": instant, "expiry": action.expiry,
            "queue_ahead_at_place": queue_ahead, "cancelled_at": None, "cancel_reason": None,
            "status": "open", "episode_id": None}
        return {"kind": "place", "instant": instant, "status": "open", "order_id": order_id,
                "intent_id": intent.intent_id, "variant_id": intent.variant_id,
                "venue_market_id": view.venue_market_id, "ticker": view.ticker,
                "side": view.side, "prob": view.prob, "contracts": view.contracts,
                "deadline": action.expiry, "queue_ahead": queue_ahead, "no_book": action.no_book,
                "book_source": ladder.source if ladder is not None else "none",
                "arm_id": self.arm_id, "run_id": self.run_id}

    def _close(self, order_id: int, kind: str, instant: datetime, deadline: datetime,
               *, reason: str | None = None) -> dict:
        view = self.world.order(order_id)
        self.world.deadlines[order_id] = deadline
        self.world.open_orders = [row for row in self.world.open_orders
                                  if row.order_id != order_id]
        # §1.4: the slot is released the instant the order stops resting, on cancel and on
        # expiry alike; `open_orders` is the capacity counter and it has already shrunk above.
        record = self.orders.get(order_id)
        if record is not None:
            # The order's own row carries the transition (I5). A cancel stamps `cancelled_at`
            # and its reason; an expiry stamps neither, because `expiry` already says when.
            record["status"] = "cancelled" if kind == "cancel" else "expired"
            if kind == "cancel":
                record["cancelled_at"] = instant
                record["cancel_reason"] = reason
        # §1.5: the order's own claim is forgotten, and **no contract is returned to the pool**
        # -- a cancel does not un-print a recorded trade (fix round 1, minor 2). `release_order`
        # drops only this order's diagnostic entries; `_allocated` is untouched by construction.
        variant = (view.variant_id if view is not None
                   else (record or {}).get("variant_id", ""))
        self.ledger.release_order(
            LedgerKey(run_id=self.run_id, arm=self.arm_id, variant=variant), order_id)
        return {"kind": kind, "instant": instant,
                "status": "cancelled" if kind == "cancel" else "expired", "order_id": order_id,
                "reason": reason, "deadline": deadline,
                "venue_market_id": view.venue_market_id if view is not None else None,
                "side": view.side if view is not None else None,
                "prob": view.prob if view is not None else None,
                "arm_id": self.arm_id, "run_id": self.run_id}

    def _simulate(self, session: Session, markets: dict, instant: datetime,
                  closing: Sequence[int]) -> list[dict]:
        """`fills.simulate_fills` per arm-owned order, with the arm's own deadline (§1.3c).

        The tape handed to it is read at the instant, so a deadline in the future cannot reach
        rows the instant could not see; the deadline is what stops a cancelled order's queue,
        never a bound on the data.
        """
        out: list[dict] = []
        for order_id in self._allocation_order(closing):
            paper = self.world.paper[order_id]
            state = self.world.sim[order_id]
            deadline = self.world.deadlines.get(order_id, paper.expiry)
            market = markets.get(self._market_of(order_id))
            ladder = market.book if market is not None else None
            prints = store.load_prints(session, paper.ticker,
                                       paper.placed_at - fills_mod.RECON_HORIZON, at=instant)
            deltas = store.load_deltas(session, paper.ticker, state.cursor_event_id or 0,
                                       paper.placed_at, at=instant).deltas
            self._observe_prints(paper.ticker, prints)
            result = fills_mod.simulate_fills(paper, state, ladder, prints, deltas, deadline,
                                              FILL_METHOD, cancel_policy=CANCEL_POLICY)
            self.world.sim[order_id] = result.state
            accepted = []
            for fill in result.fills:
                # §1.5: the portfolio may take no more of a recorded print than the print had,
                # however many of its hypothetical orders were resting on that key. The
                # simulator has already decided this track *may* take `fill.contracts`; the
                # ledger only caps it (ruling I13b).
                contracts = self._allocate(order_id, fill)
                if contracts <= ZERO:
                    continue
                accepted.append((fill, contracts))
                out.append({"kind": "fill", "order_id": order_id, "instant": instant,
                            "venue_market_id": self._market_of(order_id),
                            "side": paper.side, "ticker": paper.ticker,
                            "filled_at": fill.filled_at, "contracts": contracts,
                            "prob": fill.prob, "fee": fill.fee, "fill_method": fill.fill_method,
                            "source_trade_id": fill.source_trade_id, "through": fill.through,
                            "arm_id": self.arm_id, "run_id": self.run_id})
            self._book_fills(order_id, accepted)
        for order_id in closing:
            self.world.paper.pop(order_id, None)
            self.world.sim.pop(order_id, None)
        return out

    def _allocation_order(self, closing: Sequence[int]) -> list[int]:
        """§1.5's allocation order inside one instant: `(placed_at, arm order id)`.

        Two orders asking for the same print in a different order would divide it differently,
        so the order is fixed by the orders' own placement stamps and, for two placed at the
        same instant, by the order the arm issued them in -- never by dict or list insertion
        order, which a resume would not reproduce (fix round 1, ruling D23/I6). The arm's ids
        count **down** from -1, so the tie breaks on `-arm_order_id` (equivalently on
        `abs(arm_order_id)`): earlier-issued first, which is what §1.5's "by placement instant,
        then the order's id" says and what ascending negative ids would invert (fix round 2).
        """
        ids = [view.order_id for view in self.world.open_orders] + list(closing)
        seen: set[int] = set()
        unique = [order_id for order_id in ids
                  if order_id in self.world.paper and not (order_id in seen or seen.add(order_id))]
        return sorted(unique,
                      key=lambda order_id: (self.world.paper[order_id].placed_at, -order_id))

    def _market_of(self, order_id: int) -> int | None:
        view = self.world.order(order_id)
        if view is not None:
            return view.venue_market_id
        return self._order_markets.get(order_id)

    def _observe_prints(self, ticker: str, prints) -> None:
        """Every recorded print this step read, once, under the tape's own `taker_side`.

        `TapePrint` carries no ticker of its own -- `store.load_prints` is asked for one
        ticker at a time -- so the ticker of the read is the ticker of the print. A print whose
        `taker_side` the venue never told us (F5) can never hit an order, and is keyed under
        `unknown` rather than being silently merged with a real side.
        """
        for row in prints:
            trade_id = getattr(row, "trade_id", None)
            if trade_id is None or str(trade_id) in self._print_keys:
                continue
            key = (ticker, row.taker_side or "unknown", getattr(row, "ts", None))
            self._print_keys[str(trade_id)] = key
            self.ledger.observe(key[0], key[1], str(trade_id), row.count)

    def _prune_print_keys(self, instant: datetime) -> None:
        """Bound the observed-print index to the simulator's own reconciliation horizon.

        The bound is exact rather than arbitrary: `_simulate` reads prints from
        `paper.placed_at - fills.RECON_HORIZON` up to the instant, so the oldest print any fill
        of a later step can cite is `min(placed_at over the resting orders) - RECON_HORIZON`
        (and `instant - RECON_HORIZON` when nothing rests). Anything older can never be named
        by a future `SimFill`, and dropping it costs nothing: the ledger keeps the consumed
        quantity, and a print read again is observed again as the same trade id (§1.5's
        `observe` is idempotent). Without this the index would grow with every print of the
        window and be carried in every checkpoint (fix round 1, ruling D23/I3).
        """
        oldest = min((paper.placed_at for paper in self.world.paper.values()), default=instant)
        cutoff = min(oldest, instant) - fills_mod.RECON_HORIZON
        self._print_keys = {trade_id: key for trade_id, key in self._print_keys.items()
                            if key[2] is None or key[2] >= cutoff}

    def _allocate(self, order_id: int, fill) -> Decimal:
        """What this portfolio may take of `fill`, after everything it has already taken.

        A fill with no source print (the crossed-book case) is not print volume and has nothing
        to conserve, so it passes through; a print this run never observed grants nothing.
        """
        if fill.source_trade_id is None:
            return fill.contracts
        key = self._print_keys.get(str(fill.source_trade_id))
        if key is None:
            return ZERO
        view = self.world.order(order_id)
        variant = view.variant_id if view is not None else (
            self.orders.get(order_id, {}).get("variant_id", ""))
        return self.ledger.allocate(
            LedgerKey(run_id=self.run_id, arm=self.arm_id, variant=variant),
            key[0], key[1], str(fill.source_trade_id), order_id, fill.contracts)

    def _book_fills(self, order_id: int, accepted) -> None:
        """Carry the **granted** fills onto the arm's view of the order (§1.5).

        Not `result.state.filled_contracts`: that is the simulator's per-track total, which is
        what the ledger sits above. Where nothing was capped the two are the same number.
        """
        view = self.world.order(order_id)
        if view is None or not accepted:
            return
        filled = sum((contracts for _fill, contracts in accepted), ZERO)
        filled += view.filled_contracts or ZERO
        self.world.open_orders = [
            replace(row, filled_contracts=filled) if row.order_id == order_id else row
            for row in self.world.open_orders]
        for fill, contracts in accepted:
            self.world.fill_log.append((fill.filled_at, view.variant_id, contracts * fill.prob))
        stake = sum((contracts * fill.prob for fill, contracts in accepted), ZERO)
        self.world.fills_today.append(plan_mod.FillView(variant_id=view.variant_id, stake=stake))
        record = self.orders.get(order_id)
        if record is not None:
            record["filled_contracts"] = filled
        if filled >= view.contracts:
            # A fully filled order does not rest: the venue takes it out of the book, so the
            # arm's capacity counter is released on a fill exactly as it is on a cancel and an
            # expiry (§1.4). Its remaining lifecycle is over, and its row says so.
            self.world.open_orders = [row for row in self.world.open_orders
                                      if row.order_id != order_id]
            self.world.paper.pop(order_id, None)
            self.world.sim.pop(order_id, None)
            self.world.deadlines.pop(order_id, None)
            if record is not None:
                record["status"] = "filled"

    def _resting(self) -> list[dict]:
        out = []
        for view in self.world.open_orders:
            state = self.world.sim[view.order_id]
            # The **granted** total, not `SimState.filled_contracts`: that is the simulator's
            # per-track number, which the portfolio ledger sits above and may have capped, and
            # the resting row has to agree with the `exp_order` row (fix round 1, minor 3).
            filled = view.filled_contracts or ZERO
            out.append({"order_id": view.order_id, "venue_market_id": view.venue_market_id,
                        "ticker": view.ticker, "side": view.side, "prob": view.prob,
                        "contracts": view.contracts - filled,
                        "filled_contracts": filled,
                        "queue_ahead": state.queue_remaining, "placed_at": view.placed_at,
                        "expiry": view.expiry, "arm_id": self.arm_id, "run_id": self.run_id})
        return out


# --- §1.4: the persisted per-arm state ---------------------------------------------------------
#
# One `(run, arm)`'s whole world, encoded into `exp_checkpoint.state` and decoded back. The
# encoding is by value and by tag -- no pickle, no `eval`, and every class it can rebuild is on
# `_STATE_CLASSES` below -- so a checkpoint is readable, diffable and refusable on its own terms.

_STATE_CLASSES: dict[str, type] = {
    "plan.OpenOrderView": plan_mod.OpenOrderView,
    "plan.PositionView": plan_mod.PositionView,
    "plan.FillView": plan_mod.FillView,
    "fills.PaperOrder": fills_mod.PaperOrder,
    "fills.SimState": fills_mod.SimState,
}
_STATE_NAMES = {cls: name for name, cls in _STATE_CLASSES.items()}


def encode_state(value):
    """JSON-safe, lossless for every type the arm's world holds."""
    if isinstance(value, Decimal):
        return {"__d": str(value)}
    if isinstance(value, datetime):
        return {"__t": value.isoformat()}
    if isinstance(value, UUID):
        return {"__u": str(value)}
    if isinstance(value, (set, frozenset)):
        return {"__s": [encode_state(v) for v in sorted(value, key=repr)]}
    if isinstance(value, tuple):
        return {"__tu": [encode_state(v) for v in value]}
    if is_dataclass(value) and not isinstance(value, type):
        name = _STATE_NAMES.get(type(value))
        if name is None:
            raise TypeError(f"{type(value)!r} is not a checkpointable part of the arm's world")
        return {"__c": name,
                "f": {f.name: encode_state(getattr(value, f.name)) for f in dc_fields(value)}}
    if isinstance(value, list):
        return [encode_state(v) for v in value]
    if isinstance(value, dict):
        return {"__m": [[encode_state(k), encode_state(v)] for k, v in value.items()]}
    return value


def decode_state(value):
    if isinstance(value, list):
        return [decode_state(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "__d" in value:
        return Decimal(value["__d"])
    if "__t" in value:
        return datetime.fromisoformat(value["__t"])
    if "__u" in value:
        return UUID(value["__u"])
    if "__s" in value:
        return {decode_state(v) for v in value["__s"]}
    if "__tu" in value:
        return tuple(decode_state(v) for v in value["__tu"])
    if "__m" in value:
        return {decode_state(k): decode_state(v) for k, v in value["__m"]}
    if "__c" in value:
        cls = _STATE_CLASSES[value["__c"]]
        return cls(**{k: decode_state(v) for k, v in value["f"].items()})
    return {k: decode_state(v) for k, v in value.items()}


@dataclass(frozen=True, slots=True)
class ChunkResult:
    """What one chunk of a run did, and why it stopped."""

    run_id: str
    arm_id: str
    instants: int
    last_instant: datetime | None
    orders: int
    fills: int
    open_orders: int
    stopped: str | None


def snapshot(runner: "ArmRunner") -> dict:
    """The arm's whole world as `exp_checkpoint.state` (§1.4).

    The cursor, the liquidity ledger, the open-order set, the capacity counter, the exposure
    (carried as the fill log the caps are rebuilt from) and the order rows themselves. A chunk
    boundary is a resume point, so everything a later instant could read has to be in here.
    """
    world = runner.world
    # The top level is a plain JSON object with its own keys: `exp_checkpoint.state` has to be
    # readable by a `state->>'...'` query (§5 reads the capacity counter that way), so only the
    # *values* are tagged.
    return {key: encode_state(value) for key, value in {
        "open_orders": world.open_orders,
        "positions": world.positions,
        "fill_log": world.fill_log,
        "paper": world.paper,
        "sim": world.sim,
        "deadlines": world.deadlines,
        "cursor_event_id": world.cursor_event_id,
        "next_order_id": world.next_order_id,
        "orders": runner.orders,
        # The **whole** ledger, observed prints included: `as_rows()` alone would restore only
        # the prints this portfolio has already taken from, so a print observed before the
        # boundary and hit after it would grant nothing at all (fix round 1, ruling D23/I1).
        "ledger": runner.ledger.as_state(),
        "print_keys": runner._print_keys,
        "market_ids": sorted(runner._market_ids),
        "order_markets": runner._order_markets,
        # The capacity counter, written out rather than left to be recounted: §1.4's "two arms
        # cannot see each other's capacity counter" is a statement about this number.
        "open_orders_count": len(world.open_orders),
        "last_instant": runner.last_instant,
        # The blocked episodes still open at the boundary: a resumed chunk that finds the same
        # intent still refused writes no second `capacity` row for it (I4).
        "capacity_blocked": sorted(runner.capacity_blocked),
    }.items()}


def restore(runner: "ArmRunner", state: dict) -> None:
    """Put a decoded checkpoint back into `runner`. The inverse of `snapshot`."""
    decoded = {k: decode_state(v) for k, v in state.items() if k != "cursor_event_id"}
    world = runner.world
    world.open_orders = list(decoded.get("open_orders") or [])
    world.positions = list(decoded.get("positions") or [])
    world.fill_log = [tuple(row) for row in (decoded.get("fill_log") or [])]
    world.fills_today = [plan_mod.FillView(variant_id=variant, stake=stake)
                         for _ts, variant, stake in world.fill_log]
    world.paper = {int(k): v for k, v in (decoded.get("paper") or {}).items()}
    world.sim = {int(k): v for k, v in (decoded.get("sim") or {}).items()}
    world.deadlines = {int(k): v for k, v in (decoded.get("deadlines") or {}).items()}
    world.cursor_event_id = state.get("cursor_event_id")
    world.next_order_id = int(decoded.get("next_order_id", -1))
    runner.orders = {int(k): v for k, v in (decoded.get("orders") or {}).items()}
    runner.ledger.restore_state(decoded.get("ledger") or {})
    runner._print_keys = {str(k): tuple(v) for k, v in (decoded.get("print_keys") or {}).items()}
    # A checkpoint written before the stamp was carried (a 2-tuple) still restores: the entry
    # is simply unbounded until the next prune reads it.
    runner._print_keys = {k: (v + (None,))[:3] if len(v) < 3 else v
                          for k, v in runner._print_keys.items()}
    runner._market_ids = set(decoded.get("market_ids") or [])
    runner._order_markets = {int(k): v for k, v in (decoded.get("order_markets") or {}).items()}
    runner.last_instant = decoded.get("last_instant")
    runner.capacity_blocked = {str(i) for i in (decoded.get("capacity_blocked") or [])}


def run_chunk(session: Session, runner: "ArmRunner", instants: Sequence[datetime], *,
              writer, manifest_hash: str, stop_after: int | None = None,
              mismatch_max: int | None = None) -> ChunkResult:
    """Step `instants`, persist what the arm did, and leave a resume point behind (§1.4).

    Every instant at or before the checkpoint's `last_instant` is skipped, which is what makes
    a chunk boundary a resume point and 3 x 1 h identical to 1 x 3 h: the second chunk does not
    re-decide the first chunk's instants, and it starts holding exactly what the first left.

    `stop_after` is the mid-slice stop (§5's kill case and §4.3's cooperative yield): the run
    stops after that many *stepped* instants with its checkpoint written, and a resume
    continues from the next one.

    `mismatch_max` is §4.3's ceiling on **unexplained** mismatches (fix round 1, ruling D23/I4):
    an explained row is a recorded, understood difference, and a run that stopped on those
    would stop on its own capacity bookkeeping rather than on a reproduction failure.
    """
    from harness.experiments.execution_viability import baseline as baseline_mod
    from harness.experiments.execution_viability import storage as storage_mod

    stepped = 0
    stopped = None
    fills: list[dict] = []
    mismatches: list = []
    for instant in instants:
        if runner.last_instant is not None and instant <= runner.last_instant:
            continue
        result = runner.step(session, instant)
        runner.last_instant = instant
        stepped += 1
        fills.extend(result.fills)
        # The intent id as text: it is a UUID in `plan.Skip`, and both the mismatch row's
        # `actual` document and the checkpoint's blocked set are JSON.
        blocked = {str(action["intent_id"]) for action in result.actions
                   if action.get("kind") == "skip"
                   and action.get("reason") == plan_mod.EXEC_CAPACITY}
        for intent_id in sorted(blocked - runner.capacity_blocked):
            # §1.4: the 150-slot pool is shared inside the arm, and a blocked placement is
            # recorded rather than silently dropped. `capacity` is §2's own mismatch kind;
            # `exp_limitation`'s vocabulary is closed and does not carry this (ruling I5).
            # One row per key per blocked **episode** -- the first instant it was refused at --
            # rather than one per instant, which would write the same fact every 15 s for as
            # long as the pool stayed full (fix round 1, ruling D23/I4).
            mismatches.append(baseline_mod.Mismatch(
                run_id=runner.run_id, arm_id=runner.arm_id, instant=instant,
                venue_market_id=None, kind="capacity",
                expected={"placed": True},
                actual={"placed": False, "intent_id": intent_id,
                        "open_orders": len(runner.world.open_orders),
                        "max_open_orders": runner.exec_settings.max_open_orders},
                cause=plan_mod.EXEC_CAPACITY, explained=True))
        runner.capacity_blocked = blocked
        unexplained = sum(1 for row in mismatches if not row.explained)
        if mismatch_max is not None and unexplained > mismatch_max:
            stopped = "mismatch_max"
            break
        if stop_after is not None and stepped >= stop_after:
            stopped = "stop_after"
            break

    # The tape cursor of the arm's furthest-advanced track: `exp_checkpoint.cursor_event_id`.
    cursor = max((state.cursor_event_id or 0 for state in runner.world.sim.values()), default=0)
    runner.world.cursor_event_id = cursor or runner.world.cursor_event_id
    # `exp_order.id` is assigned by the database (D22), so the write comes back with the id each
    # of this chunk's fills has to point at.
    order_ids = storage_mod.write_orders(writer, list(runner.orders.values()))
    written = storage_mod.write_fills(writer, [
        {"run_id": row["run_id"], "arm_id": row["arm_id"],
         "exp_order_id": order_ids[row["order_id"]],
         "filled_at": row["filled_at"], "contracts": row["contracts"], "prob": row["prob"],
         "fee": row["fee"], "fill_method": row["fill_method"],
         "source_trade_id": row["source_trade_id"], "through": row["through"]}
        for row in fills])
    storage_mod.write_allocations(writer, runner.ledger.as_rows())
    if mismatches:
        storage_mod.write_mismatches(writer, mismatches)
    # A closed order's row is final: it is written once more here and then leaves the runner, so
    # neither the upsert nor the checkpoint carries the whole run's order history forward
    # (fix round 1, ruling D23/I3). Its lifecycle is already in `exp_order`.
    closed = [order_id for order_id, row in runner.orders.items() if row["status"] != "open"]
    for order_id in closed:
        runner.orders.pop(order_id, None)
        runner._order_markets.pop(order_id, None)
    storage_mod.save_checkpoint(
        writer, run_id=runner.run_id, arm_id=runner.arm_id,
        cursor_event_id=runner.world.cursor_event_id, state=snapshot(runner),
        manifest_hash=manifest_hash)
    return ChunkResult(run_id=runner.run_id, arm_id=runner.arm_id, instants=stepped,
                       last_instant=runner.last_instant, orders=len(order_ids), fills=written,
                       open_orders=len(runner.world.open_orders), stopped=stopped)


@dataclass(frozen=True, slots=True)
class WindowResult:
    """What a whole `--since/--until` window did, chunk by chunk, and why it stopped."""

    chunks: tuple[ChunkResult, ...]
    stopped: str | None

    @property
    def stepped(self) -> int:
        return sum(chunk.instants for chunk in self.chunks)

    @property
    def fills(self) -> int:
        return sum(chunk.fills for chunk in self.chunks)


def run_window(session: Session, runner: "ArmRunner", instants: Sequence[datetime], *,
               writer, manifest_hash: str, since: datetime, until: datetime,
               chunk: timedelta, mismatch_max: int | None = None,
               busy=None) -> WindowResult:
    """`run_chunk` over the window's chunks, yielding to the live executor between them.

    §4.3: `busy` is asked **before every chunk** -- `capture._yield_if_executor_busy` in a run,
    a stub in a test. When it trips, the chunk that has just finished keeps its checkpoint and
    the window stops with `executor_busy`: the experiment never competes with the executor's
    own loop, and the next invocation resumes from the checkpoint (fix round 1, ruling D21/C2).
    """
    chunks: list[ChunkResult] = []
    stopped = None
    start = since
    while start < until:
        if busy is not None and busy():
            stopped = "executor_busy"
            break
        end = min(start + chunk, until)
        window = [instant for instant in instants if start <= instant < end]
        result = run_chunk(session, runner, window, writer=writer,
                           manifest_hash=manifest_hash, mismatch_max=mismatch_max)
        chunks.append(result)
        writer.commit()
        start = end
        if result.stopped is not None:
            stopped = result.stopped
            break
    return WindowResult(chunks=tuple(chunks), stopped=stopped)
