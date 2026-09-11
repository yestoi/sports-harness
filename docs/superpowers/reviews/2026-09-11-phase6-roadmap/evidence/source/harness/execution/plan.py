"""The executor's decisions: pure, ordered, replayable (addendum §1, R8, F33-F38).

`plan_actions` is the whole of the executor's judgement. It takes a snapshot of the world --
the newest intents, the orders resting on the venue, one `MarketNow` per market, the rebuilt
exposure state and an explicit `now` -- and returns the actions the loop should carry out. It
touches no database and reads no clock, because Task 6 runs it live and Task 13 runs it again
over a recorded tape on a 15 s grid: the same snapshot must produce the same actions in both.

There are exactly two rule chains and both are ordered. The order is the specification, not an
implementation detail, so `_order_action` and `_intent_actions` read line by line in the order
the addendum states them and each rule returns rather than falling through:

*Per open order* -- expiry first (R8's guarantee outranks everything, including the kill
switch, because an expired order is already gone; its one exception is a ticker whose tape
read truncated this loop, where the order holds instead -- see `_order_action`), then the kill
switch, then the market's identity (`unmatched`), then the dirty-book hold, then fair
staleness, the venue's own move, edge decay, a rejected newest signal and finally a reprice.
Anything else holds. The hold sits at position four on purpose: a dirty book is a book we
cannot read, so we make no *pricing* decision on it, while the three rules above it depend on
nothing the book could tell us.

*Per intent with no order resting* (in descending `edge`, so the ceiling truncates the least
valuable candidates, F33) -- kill switch, kickoff cutoff, match, fair staleness, dirty book, a
missing target, post-only reject (F38), the caps and finally capacity. Only then is a `Place`
emitted. `no_target` sits where it does because it is the first rule that has to read
`target_prob`: an intent with no price or size still earns whichever of the five reasons above
it names first, and a null target is one intent's `Skip` rather than an exception that would
take the whole tick down with it.

Nothing here mutates its arguments: the exposure state is copied per variant before placements
accumulate against the caps, and a `BookState` is only ever read. `Renew` does not exist (R8):
an order is placed once with `expiry = kickoff - exec_kickoff_cutoff_min` and expiry is the
only guarantee that it stops.
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from harness import execution
from harness.execution.book import FOUR, QTY, SIDES, YES, ZERO, BookState, book_age_s, side_p
from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.strategy.run import CAP_LABELS, CONFIDENT_MATCHES, NO_EDGE, StrategyState

CENT = Decimal("0.01")
#: The fee reference size: a per-contract maker fee is size-independent at this schedule (F11).
FEE_REFERENCE = 100

# Cancel reasons and skip reasons, as they are written to `order_events.reason`.
KILL_SWITCH = "kill_switch"
UNMATCHED = "unmatched"
FAIR_STALE = "fair_stale"
VENUE_MOVE = "venue_move"
EDGE_DECAY = "edge_decay"
SIGNAL_REJECTED = "signal_rejected"
REPRICE = "reprice"
KICKOFF = "kickoff"
BOOK_DIRTY = "book_dirty"
POST_ONLY_REJECT = "post_only_reject"
EXEC_CAPACITY = "exec_capacity"
NO_TARGET = "no_target"

REJECTED = "rejected"


def _p(x) -> Decimal:
    return Decimal(str(x)).quantize(FOUR, rounding=ROUND_HALF_UP)


def _q(x) -> Decimal:
    return Decimal(str(x)).quantize(QTY, rounding=ROUND_HALF_UP)


def _money(x) -> Decimal:
    return ZERO if x is None else Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(x) -> Decimal:
    return Decimal(str(x))


@dataclass(frozen=True)
class ExecSettings:
    """The executor's knobs, frozen into every order's `config_hash`.

    A copy of the `exec_*` settings rather than a reference to `Settings`, so the hash covers
    exactly the values a decision was made under and nothing else in the environment.
    """

    period_s: int = 15
    cancel_venue_move_pts: Decimal = Decimal("0.02")
    reprice_fair_move_pts: Decimal = Decimal("0.01")
    kickoff_cutoff_min: int = 10
    max_open_orders: int = 150
    intent_ttl_s: int = 900
    book_max_age_s: int = 120

    @classmethod
    def from_settings(cls, s) -> "ExecSettings":
        return cls(period_s=s.exec_period_s,
                   cancel_venue_move_pts=_dec(s.exec_cancel_venue_move_pts),
                   reprice_fair_move_pts=_dec(s.exec_reprice_fair_move_pts),
                   kickoff_cutoff_min=s.exec_kickoff_cutoff_min,
                   max_open_orders=s.exec_max_open_orders,
                   intent_ttl_s=s.exec_intent_ttl_s,
                   book_max_age_s=s.exec_book_max_age_s)

    @property
    def cutoff(self) -> timedelta:
        """How long before kickoff placement stops and a resting order expires (R8)."""
        return timedelta(minutes=self.kickoff_cutoff_min)


def config_hash(variant_id: str, exec_settings: ExecSettings) -> str:
    """`sha256(variant_id, ExecSettings, EXECUTOR_VERSION)`, stored on every order.

    `EXECUTOR_VERSION` is read off the package at call time rather than bound at import, so a
    bump is visible to a running process and to a test that patches it (D11).
    """
    body = json.dumps({"variant_id": variant_id, "exec": asdict(exec_settings),
                       "executor_version": execution.EXECUTOR_VERSION},
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def confidently_matched(match_status: str | None) -> bool:
    """The `matched` flag on a `MarketNow`: spec §6.4's confidence, shared with the strategy.

    A market downgraded to `fuzzy` is therefore no longer matched, which cancels its orders.
    """
    return match_status in CONFIDENT_MATCHES


@dataclass(frozen=True)
class OpenOrderView:
    """The columns of an `orders` row the decision chain reads. Probabilities in the order's
    own side space, except `fair_p_at_place` and `venue_mid_at_place`, which are stored in YES
    space exactly as the venue quotes them and are converted with `side_p` at comparison."""

    order_id: int
    intent_id: uuid.UUID
    variant_id: str
    ticker: str
    venue_market_id: int
    side: str
    prob: Decimal
    contracts: Decimal
    filled_contracts: Decimal
    placed_at: datetime
    expiry: datetime | None
    fair_p_at_place: Decimal | None
    venue_mid_at_place: Decimal | None
    edge_min_at_place: Decimal | None
    as_at_place: Decimal | None
    kickoff_utc: datetime | None
    game_id: int | None
    stake: Decimal | None
    match_key: str | None


@dataclass(frozen=True)
class IntentView:
    """The newest intent for one `(variant_id, venue_market_id, side)`.

    `latest_decision` is the decision of the newest signal behind that key, so an intent whose
    signal has since been rejected cancels the order it placed without needing a second query.
    """

    intent_id: uuid.UUID
    signal_id: int
    variant_id: str
    venue_market_id: int
    ticker: str
    side: str
    target_prob: Decimal | None
    target_contracts: Decimal | None
    edge: Decimal | None
    edge_min: Decimal | None
    fair_p: Decimal | None
    game_id: int | None
    kickoff_utc: datetime | None
    stake: Decimal | None
    signal_created_at: datetime
    latest_decision: str


@dataclass(frozen=True)
class MarketNow:
    """One market as the loop sees it now: the newest fair value, the venue's quote and book.

    `staleness_s` and `stale_allowance_s` are the signal's gap-snapshot values -- pricing-time
    quantities, carried through to the order and never recomputed at the loop clock. The
    executor's own freshness quantity is `fair_age_s(now)`, which is what the `fair_stale` rule
    compares against the variant's allowance.
    """

    venue_market_id: int
    ticker: str
    fair_p: Decimal | None
    fair_ts: datetime | None
    fair_row_id: int | None
    staleness_s: int | None
    stale_allowance_s: int | None
    feed_kind: str | None
    best_bid_yes: Decimal | None
    best_ask_yes: Decimal | None
    mid_yes: Decimal | None
    book: BookState | None
    book_dirty: bool
    matched: bool
    match_key: str | None

    def best_ask(self, side: str) -> Decimal | None:
        """The cheapest price to buy `side`, from the book when there is one else the quote.

        The venue quotes one book in YES space, so the ask on NO is the complement of the YES
        bid -- which is exactly `side_p` of the other side's quote.
        """
        if self.book is not None:
            return self.book.best_ask(side)
        quote = self.best_ask_yes if side == YES else self.best_bid_yes
        return None if quote is None else side_p(quote, side)

    def mid(self, side: str) -> Decimal | None:
        """The midpoint in `side`'s space, from the book when there is one else the quote.

        None when the source quotes only one side, which holds the `venue_move` rule rather
        than firing it: half a book says nothing about where the market has moved to.
        """
        mid = self.book.mid() if self.book is not None else self.mid_yes
        return None if mid is None else side_p(mid, side)

    def fair_age_s(self, now: datetime) -> int | None:
        """Whole seconds since the fair value was computed; None when there is no fair value.

        Floored at 0 like `book_age_s`: a fair value stamped a moment ahead of our clock is not
        fresher than fresh.
        """
        if self.fair_ts is None:
            return None
        return max(0, int((now - self.fair_ts).total_seconds()))

    def dirty(self, now: datetime, s: ExecSettings) -> bool:
        """Whether the book cannot be trusted for a pricing decision (§1, F36).

        Three ways: the tape lost a frame or gapped (`BookState.dirty`), the loop declared every
        book dirty because the recorder is dead (`book_dirty`), or this ticker's own newest row
        is older than `book_max_age_s`. A market with no book at all is not dirty -- it is the
        `no_book` placement path (R10), which has no book decision to get wrong.
        """
        if self.book is None:
            return False
        return (self.book_dirty or self.book.dirty
                or book_age_s(self.book, now) > s.book_max_age_s)


@dataclass(frozen=True)
class Place:
    """Place a new paper order for `intent_id`. `no_book` marks R10's missing-book placement:
    the order is still placed, with `queue_ahead_at_place = NULL` and a `no_book` marker, and
    stays out of fill simulation until a book first exists."""

    intent_id: uuid.UUID
    side: str
    prob: Decimal
    contracts: Decimal
    expiry: datetime
    no_book: bool

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"unknown side {self.side!r}")
        if self.prob is None or self.contracts is None:
            raise ValueError("a placement needs a target price and size")
        object.__setattr__(self, "prob", _p(self.prob))
        object.__setattr__(self, "contracts", _q(self.contracts))


@dataclass(frozen=True)
class Cancel:
    order_id: int
    reason: str


@dataclass(frozen=True)
class Skip:
    """An intent the loop declined to act on, written as `order_events(kind = skipped)` once
    per `(intent_id, kind, reason)` (F35). `kind` is a parameter because the loop writes the
    same shape for its own markers, such as `no_book` against a placed intent."""

    intent_id: uuid.UUID
    reason: str
    kind: str = "skipped"


@dataclass(frozen=True)
class CapGate:
    """A False cap label, written as `order_events(kind = cap_gate)` for every exec variant so
    replay can measure what the caps would have cost; it blocks the placement only for a
    variant that applies them (amendment 2)."""

    intent_id: uuid.UUID
    reason: str
    blocking: bool


@dataclass(frozen=True)
class Expire:
    order_id: int


Action = Place | Cancel | Skip | CapGate | Expire


# --- exposure ------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionView:
    """An unsettled position: one variant's filled, unsettled exposure on one market shape."""

    variant_id: str
    game_id: int | None
    side_team_id: int | None
    side: str
    stake: Decimal
    edge: Decimal | None


@dataclass(frozen=True)
class FillView:
    """A fill counted against the daily cap. The caller selects the day (00:00 America/Chicago,
    amendment 2); this only sums what it is given."""

    variant_id: str
    stake: Decimal


def rebuild_state(open_orders: list[OpenOrderView], positions: list[PositionView],
                  fills_today: list[FillView], variant_id: str) -> StrategyState:
    """One variant's exposure, rebuilt from the world rather than carried across loops (§1).

    The executor owns exposure: the tick's `run_strategy` labels the caps against a fresh state
    and never places, so only this rebuild knows what a variant is actually carrying. Rows for
    other variants are ignored, so the caller can pass one query's worth of rows per loop.

    The three inputs overlap, because a fill both creates a position and is one of today's
    fills, so each contract's stake enters each aggregate exactly once:

    * `daily_exposure` = the open orders' stake + `fills_today`. Positions are excluded: a
      position from an earlier day's fill is not exposure taken *today*, and a position from
      today's fill is already in `fills_today`.
    * `game_exposure` and `positions` = the open orders' stake + the unsettled positions. Fills
      are excluded: they are already the positions they created.

    Adding all three to one sum would count a contract filled today twice in `daily_exposure`
    and bind `cap_daily` at roughly half its intended level.
    """
    state = StrategyState()
    for order in open_orders:
        if order.variant_id != variant_id:
            continue
        state.open_orders += 1
        _add_exposure(state, order.game_id, _money(order.stake))
    for position in positions:
        if position.variant_id != variant_id:
            continue
        _add_game_exposure(state, position.game_id, _money(position.stake))
        key = (position.game_id, position.side_team_id, position.side)
        if position.edge is not None and None not in key[:2]:
            held = state.positions.get(key)
            state.positions[key] = position.edge if held is None else max(held, position.edge)
    for fill in fills_today:
        if fill.variant_id != variant_id:
            continue
        state.daily_exposure += _money(fill.stake)
    return state


def _add_exposure(state: StrategyState, game_id: int | None, stake: Decimal) -> None:
    """An open order (or a placement this loop): exposure taken today, and against the game."""
    state.daily_exposure += stake
    _add_game_exposure(state, game_id, stake)


def _add_game_exposure(state: StrategyState, game_id: int | None, stake: Decimal) -> None:
    """An unsettled position: exposure against the game, but not against today's total."""
    if game_id is not None:
        state.game_exposure[game_id] = state.game_exposure.get(game_id, ZERO) + stake


def cap_labels(intent: IntentView, cfg: dict, state: StrategyState) -> dict[str, bool]:
    """The four `CAP_LABELS`, evaluated against the executor's own rebuilt exposure.

    `_apply_caps` in the strategy labels `cap_per_bet` against the *uncapped* Kelly stake,
    which the intent does not carry; here the recorded stake is the only stake there is, so the
    label reads as "this order still fits the per-bet ceiling". The strategy's dedupe term in
    `cap_per_game` is likewise absent: an intent carries no `side_team_id`, so the position key
    cannot be formed at this layer (the controller parked this; the state still carries
    `positions` for the variants that need it).
    """
    bankroll = _dec(cfg["bankroll"])
    stake = _money(intent.stake)
    held = state.game_exposure.get(intent.game_id, ZERO) if intent.game_id is not None else ZERO
    return {
        "cap_per_bet": stake <= _dec(cfg["per_bet_cap"]) * bankroll,
        "cap_per_game": held + stake <= _dec(cfg["per_game_cap"]) * bankroll,
        "cap_daily": state.daily_exposure + stake <= _dec(cfg["daily_cap"]) * bankroll,
        "max_open": state.open_orders + 1 <= cfg["max_open"],
    }


def first_false_cap(labels: dict[str, bool]) -> str | None:
    """The first False cap label in `CAP_LABELS` order, which is what `_decide` reports."""
    for label in CAP_LABELS:
        if not labels[label]:
            return label
    return None


# --- the two rule chains --------------------------------------------------------------


def _key(row) -> tuple[str, int, str]:
    return (row.variant_id, row.venue_market_id, row.side)


def _newest_by_key(intents: list[IntentView]) -> dict[tuple[str, int, str], IntentView]:
    """The newest intent per key, by `signal_created_at` then input order.

    Task 6 already selects the newest per key; doing it again here costs one pass and makes the
    function total over whatever it is handed, which replay depends on.
    """
    best: dict[tuple[str, int, str], tuple[datetime, int, IntentView]] = {}
    for i, intent in enumerate(intents):
        key = _key(intent)
        seen = best.get(key)
        if seen is None or (intent.signal_created_at, i) >= (seen[0], seen[1]):
            best[key] = (intent.signal_created_at, i, intent)
    return {key: value[2] for key, value in best.items()}


def _fair_stale(market: MarketNow, cfg: dict, now: datetime) -> bool:
    """`now - fair_ts > max(variant.stale_s, stale_allowance_s)` (F36).

    A market with no fair value at all reads as stale: there is nothing to price against, and
    holding an order on a market we can no longer value is the mistake this rule exists to
    prevent.
    """
    age = market.fair_age_s(now)
    if market.fair_p is None or age is None:
        return True
    return age > max(int(cfg["stale_s"]), int(market.stale_allowance_s or 0))


def _venue_moved(order: OpenOrderView, market: MarketNow, s: ExecSettings) -> bool:
    """The venue's own mid has moved `cancel_venue_move_pts` against the order since placement.

    Against, not merely away: the mid falling towards our resting bid is the market coming to
    us, which is the trade we wanted. Both quantities are read in the order's own side space.
    """
    mid = market.mid(order.side)
    if mid is None or order.venue_mid_at_place is None:
        return False
    return mid <= side_p(order.venue_mid_at_place, order.side) - s.cancel_venue_move_pts


def _edge_now(order: OpenOrderView, market: MarketNow) -> Decimal | None:
    """The order's edge repriced against the newest fair value, in the order's side space."""
    if market.fair_p is None or order.as_at_place is None:
        return None
    fee = fee_per_contract(KALSHI_FOOTBALL, "maker", order.prob, FEE_REFERENCE)
    return side_p(market.fair_p, order.side) - order.prob - fee - order.as_at_place


def _order_action(order: OpenOrderView, market: MarketNow | None, intent: IntentView | None,
                  cfg: dict, kill_active: bool, now: datetime, s: ExecSettings,
                  lagging: frozenset[str] = frozenset()) -> Action | None:
    """The open-order chain. Returns the one action for this order, or None to hold it."""
    if order.expiry is not None and now >= order.expiry:
        # `lagging` is the tickers whose delta read filled its batch limit this loop, so the
        # tape between the order's cursor and now has not been fed to it yet. Expiring here
        # would close the track with those fills never simulated -- the replay would find them
        # and the live record would not -- so the order holds for this loop and expires on the
        # first one that catches up. It holds rather than falling through the rest of the
        # chain: R8 says expiry outranks every other reason an order stops resting, and a
        # `Cancel` picked up further down would close the same track just as early
        # (fix 22 round 1, I2). `lagging` empties within a few loops by construction.
        return None if order.ticker in lagging else Expire(order.order_id)
    if kill_active:
        return Cancel(order.order_id, KILL_SWITCH)
    # No `MarketNow` means the market is no longer in the loop's working set at all, which is
    # the same statement about its identity that a match downgrade makes.
    if market is None or not market.matched or market.match_key != order.match_key:
        return Cancel(order.order_id, UNMATCHED)
    if market.dirty(now, s):
        return None
    if _fair_stale(market, cfg, now):
        return Cancel(order.order_id, FAIR_STALE)
    if _venue_moved(order, market, s):
        return Cancel(order.order_id, VENUE_MOVE)
    # An order placed without a recorded edge floor or adverse-selection seed cannot have its
    # edge repriced, so the rule holds rather than guessing at the threshold it decayed past.
    edge = _edge_now(order, market)
    if edge is not None and order.edge_min_at_place is not None:
        if edge < order.edge_min_at_place / 2:
            return Cancel(order.order_id, EDGE_DECAY)
    if intent is not None and intent.latest_decision == REJECTED:
        return Cancel(order.order_id, SIGNAL_REJECTED)
    if (intent is not None and intent.target_prob is not None
            and abs(intent.target_prob - order.prob) >= s.reprice_fair_move_pts):
        return Cancel(order.order_id, REPRICE)
    return None


def _intent_actions(intent: IntentView, market: MarketNow | None, cfg: dict,
                    state: StrategyState, kill_active: bool, now: datetime, s: ExecSettings,
                    has_capacity: bool) -> list[Action]:
    """The intent chain. Returns the actions for this intent, at most one of them a `Place`."""
    if kill_active:
        return [Skip(intent.intent_id, KILL_SWITCH)]
    # No kickoff is no expiry, and R8's expiry is the only guarantee an order stops resting.
    if intent.kickoff_utc is None or intent.kickoff_utc - now < s.cutoff:
        return [Skip(intent.intent_id, KICKOFF)]
    if market is None or not market.matched:
        return [Skip(intent.intent_id, UNMATCHED)]
    if _fair_stale(market, cfg, now):
        return [Skip(intent.intent_id, FAIR_STALE)]
    if market.dirty(now, s):
        return [Skip(intent.intent_id, BOOK_DIRTY)]
    # The first rule that needs the target: everything above it holds for an intent with no
    # price or size too, and this is where such an intent stops. `Place` still refuses a null
    # target, but as a backstop rather than as the way the loop finds out.
    if intent.target_prob is None or intent.target_contracts is None:
        return [Skip(intent.intent_id, NO_TARGET)]
    ask = market.best_ask(intent.side)
    if ask is not None and intent.target_prob >= ask:
        # A live post-only order at or through the ask is rejected by the venue (F38).
        return [Skip(intent.intent_id, POST_ONLY_REJECT)]

    out: list[Action] = []
    label = first_false_cap(cap_labels(intent, cfg, state))
    if label is not None:
        blocking = bool(cfg["apply_caps"])
        out.append(CapGate(intent.intent_id, label, blocking))
        if blocking:
            return out
    if not has_capacity:
        out.append(Skip(intent.intent_id, EXEC_CAPACITY))
        return out
    out.append(Place(intent.intent_id, intent.side, intent.target_prob, intent.target_contracts,
                     intent.kickoff_utc - s.cutoff, market.book is None))
    return out


def _copy_state(state: StrategyState | None) -> StrategyState:
    if state is None:
        return StrategyState()
    return StrategyState(open_orders=state.open_orders, daily_exposure=state.daily_exposure,
                         game_exposure=dict(state.game_exposure), positions=dict(state.positions))


def plan_actions(intents: list[IntentView], open_orders: list[OpenOrderView],
                 markets: dict[int, MarketNow], state_by_variant: dict[str, StrategyState],
                 variant_cfg: dict[str, dict], kill_active: bool, now: datetime,
                 s: ExecSettings, lagging: frozenset[str] = frozenset()) -> list[Action]:
    """Every action this loop should take, open orders first and then intents by edge.

    A `Cancel` therefore always precedes the `Place` that replaces it, which is what makes a
    reprice one decision rather than two. `lagging` is the tickers whose delta read truncated
    this loop; an expired order on one of them is held rather than expired, so its track is
    never closed over tape it has not been fed (fix 22 round 1, I2).

    `variant_cfg` must carry a config for every variant with an intent or a resting order: a
    missing one raises rather than defaulting, because both defaults available -- cancel
    everything, or hold everything -- would be a decision nobody asked for.
    """
    newest = _newest_by_key(intents)
    states = {variant_id: _copy_state(state_by_variant.get(variant_id))
              for variant_id in {row.variant_id for row in list(intents) + list(open_orders)}}

    actions: list[Action] = []
    # Keys whose resting order stops this loop's placement: held, expired, or cancelled for a
    # reason that is not a reprice. A reprice is the one cancel that asks to be replaced.
    blocked: set[tuple[str, int, str]] = set()
    still_open = 0
    for order in open_orders:
        key = _key(order)
        action = _order_action(order, markets.get(order.venue_market_id), newest.get(key),
                               variant_cfg[order.variant_id], kill_active, now, s, lagging)
        if action is None:
            still_open += 1
            blocked.add(key)
            continue
        actions.append(action)
        if not (isinstance(action, Cancel) and action.reason == REPRICE):
            blocked.add(key)

    placeable = [intent for key, intent in newest.items() if key not in blocked]
    placeable.sort(key=lambda intent: (-(intent.edge if intent.edge is not None else NO_EDGE),
                                       intent.signal_created_at, intent.signal_id))
    for intent in placeable:
        state = states[intent.variant_id]
        emitted = _intent_actions(intent, markets.get(intent.venue_market_id),
                                  variant_cfg[intent.variant_id], state, kill_active, now, s,
                                  still_open < s.max_open_orders)
        actions.extend(emitted)
        if any(isinstance(action, Place) for action in emitted):
            still_open += 1
            state.open_orders += 1
            _add_exposure(state, intent.game_id, _money(intent.stake))
    return actions
