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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

# Modules, not names: `store.market_rows` must be looked up at call time so a test can wrap the
# real function and see the `at=` it was called with (6B's I-13 rule).
from harness.execution import book as book_mod
from harness.execution import fills as fills_mod
from harness.execution import plan as plan_mod
from harness.execution import policy as policy_mod
from harness.execution import store

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
    "select 1 from market_dirty_intervals where venue_market_id = any(:ids) "
    "and started_at <= :instant and (ended_at is null or ended_at >= :instant) limit 1")


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
    fills_today: list = field(default_factory=list)          # list[plan.FillView]
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
                 world: ArmWorld | None = None) -> None:
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
        self.world = world if world is not None else ArmWorld()
        self._market_ids: set[int] = set()

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
        return view.order_id

    # --- one instant ---------------------------------------------------------------

    def run(self, session: Session, instants: Sequence[datetime]) -> list[StepResult]:
        """Step exactly the instants given, in the order given: the replay clock is resolved by
        `capture.resolve_instants`, never by this runner."""
        return [self.step(session, instant) for instant in instants]

    def step(self, session: Session, instant: datetime) -> StepResult:
        """§1.3(c)'s call order, with the arm's own world at every point."""
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

        markets = {market_id: self._market_now(session, row, instant)
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
        return StepResult(instant=instant, actions=tuple(records), fills=tuple(filled),
                          open_orders=tuple(self._resting()),
                          dirty=self._dirty(session, markets, instant))

    # --- the shared functions' inputs ------------------------------------------------

    def _walker(self, session: Session, ticker: str):
        walker = self.walkers.get(ticker)
        if walker is None:
            walker = self.walkers[ticker] = book_mod.BookWalker(session, ticker)
        return walker

    def _market_now(self, session: Session, row, instant: datetime):
        """The market as this arm sees it at `instant`, with the ladder the walker rebuilt.

        The field mapping is `policy._market_now`'s, reused rather than copied: a second copy of
        it would be free to drift from the one the policy comparison reads. The one thing added
        here is the book, which that comparison states it does not rebuild.
        """
        market = policy_mod._market_now(row)
        return replace(market, book=self._walker(session, row.ticker).at(instant))

    def _dirty(self, session: Session, markets: dict, instant: datetime) -> bool:
        """Was any of the step's markets untrustworthy at the instant?

        Two sources, both the loop's own: the book's verdict about itself (`MarketNow.dirty`,
        which is F36's rule, not a new one) and the recorded dirty interval covering the instant.
        """
        if any(market.dirty(instant, self.exec_settings) for market in markets.values()):
            return True
        ids = sorted(markets)
        if not ids:
            return False
        return session.execute(_DIRTY_AT, {"ids": ids, "instant": instant}).first() is not None

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
        for order_id in [view.order_id for view in self.world.open_orders] + list(closing):
            paper = self.world.paper[order_id]
            state = self.world.sim[order_id]
            deadline = self.world.deadlines.get(order_id, paper.expiry)
            market = markets.get(self._market_of(order_id))
            ladder = market.book if market is not None else None
            prints = store.load_prints(session, paper.ticker,
                                       paper.placed_at - fills_mod.RECON_HORIZON, at=instant)
            deltas = store.load_deltas(session, paper.ticker, state.cursor_event_id or 0,
                                       paper.placed_at, at=instant).deltas
            result = fills_mod.simulate_fills(paper, state, ladder, prints, deltas, deadline,
                                              FILL_METHOD, cancel_policy=CANCEL_POLICY)
            self.world.sim[order_id] = result.state
            for fill in result.fills:
                out.append({"order_id": order_id, "instant": instant,
                            "filled_at": fill.filled_at, "contracts": fill.contracts,
                            "prob": fill.prob, "fee": fill.fee, "fill_method": fill.fill_method,
                            "source_trade_id": fill.source_trade_id, "through": fill.through,
                            "arm_id": self.arm_id, "run_id": self.run_id})
            self._book_fills(order_id, result)
        for order_id in closing:
            self.world.paper.pop(order_id, None)
            self.world.sim.pop(order_id, None)
        return out

    def _market_of(self, order_id: int) -> int | None:
        view = self.world.order(order_id)
        if view is not None:
            return view.venue_market_id
        return None

    def _book_fills(self, order_id: int, result) -> None:
        """Carry the simulator's own filled total onto the arm's view of the order."""
        view = self.world.order(order_id)
        if view is None or not result.fills:
            return
        filled = result.state.filled_contracts
        self.world.open_orders = [
            replace(row, filled_contracts=filled) if row.order_id == order_id else row
            for row in self.world.open_orders]
        stake = sum((fill.contracts * fill.prob for fill in result.fills), ZERO)
        self.world.fills_today.append(plan_mod.FillView(variant_id=view.variant_id, stake=stake))

    def _resting(self) -> list[dict]:
        out = []
        for view in self.world.open_orders:
            state = self.world.sim[view.order_id]
            out.append({"order_id": view.order_id, "venue_market_id": view.venue_market_id,
                        "ticker": view.ticker, "side": view.side, "prob": view.prob,
                        "contracts": view.contracts - state.filled_contracts,
                        "filled_contracts": state.filled_contracts,
                        "queue_ahead": state.queue_remaining, "placed_at": view.placed_at,
                        "expiry": view.expiry, "arm_id": self.arm_id, "run_id": self.run_id})
        return out
