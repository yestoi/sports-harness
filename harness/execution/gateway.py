"""The order gateway: where an order goes once the loop has decided to place it.

Phase 3 wrote every order straight into `orders` and every state change into `order_events`:
the paper harness *is* the venue. Phase 4 needs a second answer to the same question -- a real
Kalshi order -- without moving the first one by a single row, because the paper record is the
evidence the gate is judged on. So the loop keeps every decision it makes today and hands the
result to an `OrderGateway`, of which there are exactly two:

* `PaperGateway` is phase 3's write path, moved behind the interface and not otherwise changed.
  Every write it makes is the same `store.insert_order` / `store.cancel_order` call with the
  same values in the same order, which is what makes the golden replay's diff empty by
  construction (ruling D4).
* `KalshiGateway` is the dormant live path, over Task 7's writer and Task 8's reader. Nothing
  in the deployed posture constructs it: `Settings.mode` is `paper` on the NAS, and `live`
  additionally needs `make_writer(settings, "prod")` to succeed, which it cannot (§1.4).

The seam is drawn where a *venue message* would be sent, not where a decision is made. `Place`,
`Cancel` and the fill source cross it; `Expire`, `CapGate` and `Skip` do not, because those are
our own bookkeeping and no venue ever hears about them.

`PaperGateway` holds no transport, no writer and no reader, and neither does this module import
one into the paper path by accident: `test_paper_gateway_never_touches_transport` asserts the
attributes do not exist.

Task 10 gave `KalshiGateway` the rest of section 9: the startup reconciliation, the outage
counter behind `venue_status`, the freeze after an echo mismatch or a third consecutive reject,
the kill switch on a message-budget breach, and the live-only refusal to reprice against a dirty
book. All of it hangs off `_venue_call`, which is the single place a venue's answer becomes
venue *state*, and none of it is on the paper path: the rules themselves live in
`harness/execution/venue.py`, and `PaperGateway` imports nothing from it.
"""

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from harness.db.models import KillSwitch, Order
from harness.execution import store
from harness.execution.plan import ExecSettings
from harness.execution.risk import stopped_variants
from harness.execution.venue import (
    OUTAGE_ENV,
    STATUS_UNAVAILABLE,
    OutageCounter,
    RejectTracker,
    VenueNotRoutable,
    enable_venue,
    freeze_market,
    is_routable,
    make_reason,
    mark_status,
    may_reprice,
    read_status,
    sanitize_venue_text,
)
from harness.venues.kalshi.authed import (
    EchoMismatch,
    KalshiApiError,
    MessageBudgetExceeded,
    OrderIntent,
)
from harness.venues.kalshi.http import VenueTransportError

log = logging.getLogger(__name__)

#: The one venue this gateway speaks to, and the key `venue_status` rows are written under.
VENUE = "kalshi"

#: The status a resting order carries at Kalshi, which is what the startup reconciliation asks
#: for (section 9.1).
RESTING_STATUS = "resting"

#: How far back reconciliation reads fills when we hold none at all: far enough to cover a
#: process that was down overnight, bounded so a first run does not page the whole account.
RECONCILE_FILL_LOOKBACK = timedelta(hours=24)

#: A send that never came back. The venue may or may not have the order, so the answer is a
#: lookup by `client_order_id`, never a resend (section 9.1).
_TRANSPORT_FAILURES = (VenueTransportError, httpx.TimeoutException, httpx.TransportError)

#: "The caller made no claim about the book", which is a different thing from "there is no
#: book" -- the second one blocks a reprice and the first one is simply not this gateway's
#: question. See `KalshiGateway.amend`.
_UNSET = object()


def _is_reject(status: int | None) -> bool:
    """Whether a non-2xx is the venue *refusing this order* rather than refusing us.

    401 and 403 are the outage counter's, 429 is the rate limiter's and 5xx is the venue having
    a bad afternoon; none of them says anything about the order. What is left -- a 400 on a
    price, a 409 on a duplicate -- is a reject, and three consecutive ones on the same order
    mean the venue will not take it at any price we are choosing.
    """
    return status is not None and 400 <= status < 500 and status not in (401, 403, 429)

#: The statuses a live cancel or reconcile still has something to say about, shared with
#: `store.OPEN_STATUSES` rather than re-listed here.
OPEN_STATUSES = store.OPEN_STATUSES


@dataclass(frozen=True)
class PlacedOrder:
    """What a gateway hands back after a placement. `order_id` is our own row id; the three
    venue fields are NULL for paper and filled by `KalshiGateway`.

    `order_id is None` is the duplicate: `store.insert_order` refused the row on its
    `client_order_id` conflict, and `_place` must return before it counts a placement.
    """

    order_id: int | None
    venue_order_id: str | None = None
    order_group_id: str | None = None
    exchange_index_at_place: int | None = None


@dataclass(frozen=True)
class ReconcileReport:
    """What a startup reconciliation found. Every field is a count, so an empty report is the
    honest description of a venue we hold nothing at."""

    resting: int = 0
    cancelled_unknown: int = 0
    cancelled_past_deadline: int = 0
    positions_rebuilt: int = 0
    fills_seen: int = 0


class NoOrderGroup(RuntimeError):
    """A live placement was attempted before an order group existed. The group is what
    `cancel_all` cancels in one message, so an order placed outside one could not be pulled
    back by the kickoff or outage path; failing closed is the only safe answer."""


class OrderNotPlaced(RuntimeError):
    """A send timed out and the follow-up lookup by `client_order_id` found no such order at the
    venue, so the order was not placed.

    Raised rather than resent. `client_order_id` is Kalshi's idempotency key and a blind resend
    of a message that may in fact have arrived is how one intent becomes two positions; the
    caller's answer is to let the next loop decide again from a clean slate (section 9.1).
    """

    def __init__(self, client_order_id: str) -> None:
        super().__init__(f"no venue order for client_order_id {client_order_id!r} after a "
                         "send timeout; not placed, and not resent")
        self.client_order_id = client_order_id


class OrderGateway(Protocol):
    """The whole of what the executor asks of a venue.

    `values` is the dict `_place` already builds, unchanged and complete. Passing it in rather
    than rebuilding it inside the gateway is what keeps `_place` byte-for-byte: the dict needs
    `self.replay`, `self.exec_settings` and `config_hash`, none of which belongs on a gateway.
    The gateway's only job on the paper path is the insert.
    """

    def place(self, session, values: dict, action, market, now) -> PlacedOrder: ...

    #: Returns whether a row actually moved, because `_apply_one` drives `stats.cancelled` and
    #: `self._metrics_acc.cancelled[reason]` off exactly that boolean today.
    def cancel(self, session, order_id: int, reason: str, now) -> bool: ...

    #: `market` carries the venue's published tick grid (`price_ranges`), which is what the
    #: live amend snaps its new price to; the paper path ignores it. Returns whether our own
    #: row moved, so the caller reads the same boolean it reads off `cancel`.
    #:
    #: `KalshiGateway.amend` additionally takes a `book` keyword and refuses to reprice against
    #: a dirty, stale or absent one (section 2.2). It is deliberately not in this signature:
    #: `PaperGateway.amend` does not consult a book, because phase 3 reprices by cancel + place
    #: and the golden replay pins that behaviour unchanged.
    def amend(self, session, order_id: int, prob: Decimal, contracts: Decimal, market,
              now) -> bool: ...

    def cancel_all(self, session, reason: str, now) -> int: ...

    def reconcile(self, session, now) -> ReconcileReport: ...

    #: Live only, and per *trade*: a list of `authed.VenueFillView` records, each carrying the
    #: venue's `order_id`, `price` and `count`. `PaperGateway` has no venue to poll and returns
    #: `[]` -- paper fills are written by the simulator, and the daily cap reads them through
    #: `store.load_fills_today`, which is a per-*variant* aggregate and a different shape
    #: entirely. A caller that wants the cap's numbers must call `store`, not a gateway
    #: (review round 1, Important 5; Task 11 owns the cap).
    def poll_fills(self, session, since, now=None) -> list: ...

    #: The newest tape row this step saw, handed over once a step. `PaperGateway` ignores it;
    #: `KalshiGateway` feeds it to section 2.2's "30 s without a ping" reprice test.
    def observe_tape(self, ws_last_event_at) -> None: ...

    #: Where the equity sampler is polled for `since`. `KalshiGateway` answers with its own
    #: fill watermark; `PaperGateway` has no venue to poll and its answer is never used.
    def fills_since(self, session, now) -> datetime: ...

    #: Section 9.3's response to a tripped drawdown stop, called once per equity sample with
    #: the sample already written. `PaperGateway` does nothing at all -- in paper the stop is
    #: information (decision 6) and the executor keeps placing. `KalshiGateway` trips the kill
    #: switch, which is why the branch lives on the gateway rather than in the loop: the paper
    #: path has no code to reach.
    def on_drawdown_stop(self, session, now) -> set[str]: ...

    #: Declared, not inferred: the fill-branch dispatch asks the gateway whether the tape
    #: simulator answers for its fills. A gateway that neither sets it nor inherits it fails
    #: loudly in `uses_the_simulator` rather than silently taking the live branch.
    simulates_fills: bool


class PaperGateway:
    """Today's paper path, moved behind the interface and not otherwise changed.

    `action`, `market` and `now` are part of the interface because the live path needs them --
    a venue order carries the market's tick grid and its own expiry -- and are unused here: the
    paper path's whole placement is the row `_place` already built.

    `replay` partitions this gateway exactly as it partitions the executor that holds it: a
    replay executor's gateway must never select, cancel or count a live row, so `Executor`
    passes its own flag through (review round 1, Important 1).
    """

    #: The tape simulator answers for every paper fill.
    simulates_fills = True

    def __init__(self, replay: bool = False) -> None:
        self.replay = bool(replay)

    def place(self, session, values: dict, action, market, now) -> PlacedOrder:
        return PlacedOrder(order_id=store.insert_order(session, values))

    def cancel(self, session, order_id: int, reason: str, now) -> bool:
        return store.cancel_order(session, order_id, reason, now)

    def amend(self, session, order_id: int, prob: Decimal, contracts: Decimal, market,
              now) -> bool:
        """Phase 3 reprices by cancelling and placing again, and the loop never amends a paper
        order. Raised rather than returning False so that a caller which one day routes a
        reprice through the gateway fails loudly here instead of silently dropping it."""
        raise NotImplementedError(
            "the paper path reprices by cancel + place; it has no amend")

    def cancel_all(self, session, reason: str, now) -> int:
        """Cancel every resting paper order, returning how many rows actually moved. The
        `order_events` rows are the caller's, exactly as they are for a single `Cancel`."""
        ids = session.execute(
            select(Order.id).where(Order.status.in_(OPEN_STATUSES),
                                   Order.replay.is_(self.replay))
            .order_by(Order.id)).scalars().all()
        return sum(1 for order_id in ids
                   if store.cancel_order(session, order_id, reason, now))

    def reconcile(self, session, now) -> ReconcileReport:
        """Paper has no venue to disagree with us, so there is nothing to reconcile: the report
        is a count of what we hold and nothing was found to be out of step."""
        return ReconcileReport(resting=store.count_open_orders(session, self.replay))

    def observe_tape(self, ws_last_event_at) -> None:
        """Ignored. Section 2.2's ping rule is live-only, and phase 3's reprice behaviour on a
        quiet socket is what the golden replay pins."""

    def fills_since(self, session, now) -> datetime:
        """`now`, and it is never read: `poll_fills` below returns `[]` whatever window it is
        given, and the live fill step is the only caller."""
        return now

    def on_drawdown_stop(self, session, now) -> set[str]:
        """Nothing. Decision 6: in paper the drawdown stop is information, so the executor
        keeps placing and the label on the signal is the whole of the effect. The kill-switch
        response lives on the live gateway, where this method is overridden -- so the paper
        path does not merely decline to trip the switch, it holds no code that could."""
        return set()

    def poll_fills(self, session, since, now=None) -> list:
        """Empty, always: paper has no venue to poll. The simulator writes every paper fill and
        `_persist_track` records it in the same step, so there is nothing left for a poll to
        discover, and the daily cap's numbers come from `store.load_fills_today` -- a
        per-variant aggregate, not the per-trade records this method returns in live mode.
        Returning that aggregate here would hand the caller the wrong shape under the same name
        (review round 1, Important 5)."""
        return []


class KalshiGateway:
    """Dormant. Constructed only when `Settings.mode == 'live'`, which additionally requires
    `make_writer(settings, 'prod')` to succeed, which it cannot (§1.4).

    `order_group_id` is the group every order of this process rests in, so that one message can
    pull all of them back at kickoff or during an outage. It is established by the startup
    reconciliation (Task 10); until it exists this gateway places nothing.
    """

    #: The venue answers for every live fill; the tape simulator is bypassed entirely.
    simulates_fills = False

    def __init__(self, writer, reader, session_factory=None, *,
                 order_group_id: str | None = None, exchange_index: int = 0,
                 env: str = OUTAGE_ENV, exec_settings: ExecSettings | None = None,
                 clock=None) -> None:
        self._writer = writer
        self._reader = reader
        #: For the reconciliation, which runs outside a step's session (Task 10).
        self._session_factory = session_factory
        self.order_group_id = order_group_id
        self.exchange_index = int(exchange_index)
        #: Which `venue_status` row this gateway's failures write. A demo gateway can never mark
        #: production, which is A-I3 enforced at the only place that marks anything.
        self.env = str(env)
        #: The reprice gate's staleness ceiling. A copy of the executor's own settings, so the
        #: gate and the loop agree on what "stale" means.
        self.exec_settings = exec_settings or ExecSettings()
        self._now = clock or (lambda: datetime.now(timezone.utc))
        #: Section 9.4, per gateway: two consecutive auth failures and this venue is marked.
        self._outage = OutageCounter()
        #: Three consecutive rejects on one order cancel it and freeze the venue 15 min.
        self._rejects = RejectTracker()
        #: What the venue says we hold, rebuilt by `reconcile` and empty until it has run.
        self.positions: dict[str, Decimal] = {}
        #: Section 2.2's ping half: the newest tape row the loop has seen, handed over by
        #: `observe_tape` once per step. None until a step has run, which reads as "no claim"
        #: and leaves the silence test dormant rather than blocking every reprice at startup.
        self._ws_last_event_at: datetime | None = None

    def observe_tape(self, ws_last_event_at: datetime | None) -> None:
        """The newest tape timestamp this step saw, which the loop already computes for the
        heartbeat and the dead-recorder test. Handing it to the gateway is what makes section
        2.2's "30 s without a ping" actually fire on a live reprice: a book can still read as
        fresh for a while after the frames stop arriving (fix round 1, Important 3)."""
        self._ws_last_event_at = ws_last_event_at

    def enable(self, session, now) -> bool:
        """`harness venue-enable` as this process sees it: clear the row *and* the in-process
        outage counter.

        Without the reset the counter is still latched from the outage, so the very next single
        401 would re-mark a venue an operator has just cleared -- one auth failure, not two (fix
        round 1, Minor 1). A CLI in another process has no counter to reset and calls
        `venue.enable_venue` directly.
        """
        self._outage.reset()
        return enable_venue(session, VENUE, self.env, now)

    # -- venue health --------------------------------------------------------------------

    @contextmanager
    def _venue_state_session(self, session):
        """A short transaction of its own for every venue-state write, committed before the
        exception that caused it is re-raised.

        This is not a nicety. `Executor._apply` runs each action inside `session.begin_nested()`
        and catches the exception *outside* the savepoint, so a row written on the caller's
        session and then followed by a `raise` is rolled back with the savepoint -- taking the
        outage mark, the freeze, the reject cancel and the kill-switch trip with it. The safety
        state has to outlive the failure it describes, so it is written on a session of its own
        and committed (fix round 1, Important 1).

        `session_factory` is what that constructor argument has always been for. When there is
        none, the caller's session is used and the risk is logged loudly rather than hidden:
        a write that might be rolled back still beats no write at all -- `poll_fills`, for one,
        is not inside a savepoint -- but a live gateway must be given a factory.
        """
        if self._session_factory is None:
            log.error("no session_factory: venue state is being written on the caller's "
                      "session and may be rolled back with its savepoint")
            yield session
            return
        state = self._session_factory()
        try:
            yield state
            state.commit()
        except Exception:
            state.rollback()
            raise
        finally:
            state.close()

    @contextmanager
    def _venue_call(self, session, now, *, reject_key: str | None = None,
                    on_reject_limit=None):
        """Every message this gateway sends goes through here, and this is where the venue's
        answer becomes venue *state*.

        Four outcomes are folded in, in the order they must be:

        * A message-budget breach (section 9.2) trips the kill switch and re-raises. The writer
          refused the message before the wire, so nothing was sent; the kill switch is the
          caller's half of that rule.
        * An echo mismatch means Task 7's writer has already cancelled the order; recording the
          15-minute freeze is this half of the same rule.
        * A `KalshiApiError` feeds the outage counter, and -- when it is a reject rather than a
          refusal of *us* -- the per-order reject tracker.
        * Anything else propagates untouched. In particular a `VenueTransportError` neither
          counts toward an outage nor resets it: a network failure is not evidence about
          authentication either way.

        A success resets both counters, which is what makes them *consecutive* counters.
        """
        try:
            yield
        except MessageBudgetExceeded as exc:
            with self._venue_state_session(session) as state:
                self._trip_kill_switch(state, now, f"message budget exceeded: {exc}")
            raise
        except EchoMismatch as exc:
            # The window comes off the exception rather than off this module's constant, so
            # Task 7's `ECHO_FREEZE_MINUTES` and `FREEZE_MINUTES` cannot drift apart silently.
            with self._venue_state_session(session) as state:
                freeze_market(state, VENUE, self.env, exc.reason, now,
                              minutes=exc.freeze_minutes)
            raise
        except KalshiApiError as exc:
            rejected = (reject_key is not None and _is_reject(exc.status)
                        and self._rejects.record_reject(reject_key))
            with self._venue_state_session(session) as state:
                self._note_api_error(state, exc, now)
                if rejected:
                    freeze_market(state, VENUE, self.env, "three_consecutive_rejects", now)
                    if on_reject_limit is not None:
                        on_reject_limit(state)
            raise
        else:
            self._outage.reset()
            if reject_key is not None:
                self._rejects.record_success(reject_key)

    def _note_api_error(self, session, exc: KalshiApiError, now) -> None:
        """Section 9.4's counter, and the mark it drives.

        A-I3 is enforced by the first line: a demo gateway does not even count, so a demo
        smoke's 401s cannot reach production's row. The reason carries the status code we
        decided on plus a bounded, sanitized excerpt of the venue's own `code` field -- the only
        venue text `KalshiApiError` keeps, which is deliberate (the message and the raw body may
        echo request details back).
        """
        if self.env != OUTAGE_ENV:
            return
        if self._outage.record(exc.status, exc.code):
            mark_status(session, VENUE, self.env, STATUS_UNAVAILABLE,
                        make_reason(exc.status, exc.code), now)

    def _trip_kill_switch(self, session, now, reason: str) -> None:
        """Section 9.2: a message-budget breach trips the kill switch.

        The only kill-switch write in this harness, and it is inside a gateway nothing in the
        deployed posture constructs (roadmap invariant 9 forbids the *loop* toggling it in
        production). Sanitized like everything else that ends up in a text column, even though
        this string is our own.
        """
        stmt = insert(KillSwitch).values(
            id=1, active=True, reason=sanitize_venue_text(reason, 200), set_at=now)
        session.execute(stmt.on_conflict_do_update(
            index_elements=[KillSwitch.id],
            set_={"active": True, "reason": stmt.excluded.reason,
                  "set_at": stmt.excluded.set_at}))
        log.error("kill switch tripped by the venue gateway: %s", reason)

    def _require_routable(self, session, now) -> None:
        """Nothing new is routed to an `unavailable` or frozen venue (section 9.4). Cancels do
        not come through here: an outage stops new risk, and stranding a resting order at the
        venue would be the opposite of that."""
        if not is_routable(session, VENUE, self.env, now):
            raise VenueNotRoutable(VENUE, self.env,
                                   read_status(session, VENUE, self.env) or "unknown")

    # -- placement -----------------------------------------------------------------------

    def place(self, session, values: dict, action, market, now) -> PlacedOrder:
        """Send one order, then record it with the three venue columns Task 4 added.

        The send comes first because `client_order_id` is Kalshi's own idempotency key: a
        resent order is refused venue-side, while a row written for an order that never
        reached the venue would be a position we do not hold. The action runs inside the
        step's savepoint, so a transport failure takes the row with it and the startup
        reconciliation is what closes the remaining window.

        Task 10 closes the window a good deal further. A send that times out is resolved by
        asking the venue what it holds under our `client_order_id`; the order is written back
        if it is there and `OrderNotPlaced` is raised if it is not, and either way exactly one
        POST was sent. An `unavailable` or frozen venue refuses the placement before the intent
        is even built, and a message-budget breach trips the kill switch on the way out.
        """
        if self.order_group_id is None:
            raise NoOrderGroup("a live order needs an order group; reconcile has not run")
        if values.get("expiry") is None:
            raise ValueError("a live order needs an expiry (R8: kickoff - 10 min)")
        self._require_routable(session, now)
        client_order_id = str(values["client_order_id"])
        intent = OrderIntent(
            client_order_id=client_order_id,
            ticker=values["ticker"],
            side=values["side"],
            prob=values["prob"],
            contracts=values["contracts"],
            expiration_time=values["expiry"],
            exchange_index=self.exchange_index,
            order_group_id=self.order_group_id,
            price_ranges=getattr(market, "price_ranges", None),
        )
        with self._venue_call(session, now, reject_key=client_order_id):
            try:
                venue = self._writer.place_limit(intent)
            except _TRANSPORT_FAILURES:
                # Never a blind resend: the send may well have arrived. The exception itself is
                # dropped here rather than chained, because an httpx one carries `.request` and
                # that request's headers hold the live signature (see `venues/kalshi/http.py`).
                venue = self._resolve_after_timeout(client_order_id)
        group = venue.order_group_id or self.order_group_id
        order_id = store.insert_order(session, {
            **values,
            "mode": "live",
            "venue_order_id": venue.order_id,
            "order_group_id": group,
            "exchange_index_at_place": self.exchange_index,
        })
        return PlacedOrder(order_id=order_id, venue_order_id=venue.order_id,
                           order_group_id=group,
                           exchange_index_at_place=self.exchange_index)

    # -- cancels -------------------------------------------------------------------------

    def cancel(self, session, order_id: int, reason: str, now) -> bool:
        """Cancel at the venue first, then locally. A row we never sent (no `venue_order_id`)
        is cancelled locally alone rather than guessed at."""
        row = self._row(session, order_id)
        if row is not None and row.venue_order_id:
            with self._venue_call(session, now):
                self._writer.cancel(row.venue_order_id, row.ticker,
                                    row.exchange_index_at_place or 0)
        return store.cancel_order(session, order_id, reason, now)

    def amend(self, session, order_id: int, prob: Decimal, contracts: Decimal, market,
              now, book=_UNSET) -> bool:
        """Reprice a resting live order in place. Returns whether our own row moved, exactly as
        `cancel` does, so the loop's counters read the same boolean on both paths.

        The venue's `client_order_id` is an idempotency key, and V2's amend replaces it with
        `updated_client_order_id`: the new one is therefore a fresh uuid4 and is written back
        onto our row, so a second amend sends the id the order actually carries rather than the
        one it was placed with (review round 1, Important 4). The price snaps to the market's
        own published grid, not to the fallback cent grid, which is why `market` is part of the
        interface; Task 10, which owns the reprice decision, threads through the `MarketNow`
        the loop is already holding.
        """
        row = self._row(session, order_id)
        if row is None or row.status not in OPEN_STATUSES:
            return False
        # A reprice is new risk at a new price, so section 9.4's "nothing new is routed" covers
        # it: an unavailable or frozen venue blocks it, which is the whole point of the reject
        # freeze (fix round 1, Important 2). Cancels stay ungated.
        self._require_routable(session, now)
        if not self._may_reprice(market, book, now):
            return False
        updates: dict = {"prob": prob, "contracts": contracts}
        if row.venue_order_id:
            updated_client_order_id = str(uuid.uuid4())
            with self._venue_call(
                    session, now, reject_key=str(order_id),
                    on_reject_limit=lambda state: self._pull_back(state, order_id, now)):
                self._writer.amend(row.venue_order_id, prob, contracts, row.client_order_id,
                                   updated_client_order_id, row.ticker, row.side,
                                   row.exchange_index_at_place or 0,
                                   getattr(market, "price_ranges", None))
            updates["client_order_id"] = updated_client_order_id
        store.update_order(session, order_id, updates)
        return True

    def _may_reprice(self, market, book, now) -> bool:
        """Section 2.2's live-only rule, applied at the one place a live reprice is sent.

        `book` is the caller's explicit claim and takes precedence; `_UNSET` means the caller
        made none, in which case the `MarketNow` it is already passing is asked for the book it
        carries. A caller that offers neither is not gated here -- that is the loop's decision
        to make and Task 11 owns it -- but a caller that offers a book, including an explicit
        `None`, gets the full rule, and `may_reprice(None, ...)` is False, because repricing
        against a book we do not have is exactly what the rule forbids.
        """
        claimed = book if book is not _UNSET else getattr(market, "book", _UNSET)
        if claimed is _UNSET:
            return True
        if may_reprice(claimed, now, self.exec_settings,
                       ws_last_event_at=self._ws_last_event_at):
            return True
        log.info("skipping a live reprice: the book is dirty, stale, absent, or the socket "
                 "has been silent")
        return False

    def _pull_back(self, state, order_id: int, now) -> None:
        """The third consecutive reject on one order: cancel it, here and at the venue.

        `state` is the venue-state session, not the caller's: this cancel is safety state and
        has to survive the reject that is on its way up (fix round 1, Important 1). It sees the
        order because a repriced order was placed in an earlier step, which committed; the loop
        never places and amends the same order inside one step.

        A failure to cancel at the venue must not mask the reject, so the class name is kept and
        the exception is not -- a transport exception's `.request` carries the live signed
        headers.
        """
        row = state.get(Order, order_id)
        if row is not None and row.venue_order_id:
            try:
                self._writer.cancel(row.venue_order_id, row.ticker,
                                    row.exchange_index_at_place or 0)
            except Exception as exc:
                log.warning("cancel after three rejects failed: %s", type(exc).__name__)
        store.cancel_order(state, order_id, "rejects", now)

    def cancel_all(self, session, reason: str, now) -> int:
        """One message pulls the whole group back; the rows are then closed one by one so that
        every cancel carries its reason. Returns how many of our rows actually moved."""
        group = self.order_group_id or self._group_of_open_orders(session)
        if group is not None:
            with self._venue_call(session, now):
                self._writer.cancel_group(group)
        ids = session.execute(
            select(Order.id).where(Order.status.in_(OPEN_STATUSES),
                                   Order.replay.is_(False), Order.mode == "live")
            .order_by(Order.id)).scalars().all()
        return sum(1 for order_id in ids
                   if store.cancel_order(session, order_id, reason, now))

    # -- reads ---------------------------------------------------------------------------

    def reconcile(self, session, now) -> ReconcileReport:
        """Section 9.1's startup sequence, run before any loop.

        Three questions, in this order, and then the cancels:

        1. `GET /portfolio/orders?status=resting` -- what is the venue holding for us?
        2. `GET /portfolio/fills?min_ts=...` -- what filled while we were not looking? `since`
           is the newest live fill we already have, or a bounded lookback when we hold none.
        3. `GET /portfolio/positions` -- what do we actually own? This is the rebuild: the
           venue's answer replaces whatever this process thought it held, because after a crash
           our own view is the one that might be wrong.

        Then every resting order that is *not* ours (no row with that `client_order_id`) or is
        past its deadline is cancelled. Both are the same failure seen from two sides: an order
        outstanding at the venue that no live decision of ours is managing. The reads come first
        so a cancel can never change the answer to a question still being asked.

        No group is created here. Creating one is a message, and reconciliation asks questions;
        a group the resting orders already carry is adopted instead, which is what lets
        `cancel_all` pull them all back with one message afterwards.
        """
        with self._venue_call(session, now):
            resting = self._reader.get_orders(RESTING_STATUS)
        with self._venue_call(session, now):
            fills = self._reader.get_fills(self.fills_since(session, now))
        with self._venue_call(session, now):
            positions = self._reader.get_positions()

        self.positions = {p.ticker: p.position for p in positions
                          if p.ticker and p.position}
        known = self._live_open_orders(session)
        if self.order_group_id is None:
            self.order_group_id = next(
                (v.order_group_id for v in resting if v.order_group_id), None)

        cancelled_unknown = cancelled_past_deadline = 0
        for view in resting:
            row = known.get(view.client_order_id)
            if row is None:
                # We hold no row for this order, so its shard is a guess: this gateway's own
                # `exchange_index`, which is the one every order it places carries. An order
                # from another shard would refuse the cancel and be reported by
                # `_cancel_at_venue` rather than silently left resting (fix round 1, Minor 5).
                self._cancel_at_venue(session, view, now, self.exchange_index)
                cancelled_unknown += 1
            elif row.expiry is not None and row.expiry <= now:
                self._cancel_at_venue(session, view, now,
                                      row.exchange_index_at_place or 0)
                store.cancel_order(session, row.id, "reconcile_expired", now)
                cancelled_past_deadline += 1

        report = ReconcileReport(
            resting=len(resting), cancelled_unknown=cancelled_unknown,
            cancelled_past_deadline=cancelled_past_deadline,
            positions_rebuilt=len(self.positions), fills_seen=len(fills))
        log.info("reconciled %s/%s: %r", VENUE, self.env, report)
        return report

    def _live_open_orders(self, session) -> dict:
        """Our own resting live orders, keyed the way the venue keys them: `client_order_id` is
        the one identifier both sides agree on before a venue order id exists."""
        rows = session.execute(
            select(Order).where(Order.status.in_(OPEN_STATUSES), Order.replay.is_(False),
                                Order.mode == "live")).scalars().all()
        return {row.client_order_id: row for row in rows}

    def _cancel_at_venue(self, session, view, now, exchange_index: int) -> None:
        """Cancel one resting order at the venue by the id the venue gave it. A cancel that
        fails is logged and does not stop the rest of the reconciliation: the remaining orders
        are exactly the ones that most need pulling back."""
        if not view.order_id:
            return
        try:
            with self._venue_call(session, now):
                self._writer.cancel(view.order_id, view.ticker, exchange_index)
        except Exception as exc:
            log.warning("reconcile cancel of %r failed: %s",
                        sanitize_venue_text(view.order_id, 64), type(exc).__name__)

    def fills_since(self, session, now) -> datetime:
        """The newest live fill we already hold, or a bounded lookback when we hold none.

        Reading from the newest fill rather than from process start is what makes this a
        reconciliation: a fill that landed while we were down is still new to us.

        Task 11 gave the loop's own fill poll the same answer. `reconcile` counts the fills it
        finds and writes none of them (it is the read half of section 9.1), so a fill that
        landed while we were down is discovered by the reconciliation and *recorded* by the
        first poll after it -- which can only happen if that poll's window reaches back past
        the fill. A window anchored on local midnight would lose exactly the fills that
        straddled it. The watermark is the last fill we hold, so the window closes as soon as
        one is written and re-polling costs one bounded request; `fills` is keyed on the
        venue's trade id, so a re-poll of the same window inserts nothing twice.
        """
        newest = session.execute(text(
            "select max(f.filled_at) from fills f join orders o on o.id = f.order_id "
            "where o.mode = 'live' and o.replay = false")).scalar()
        return newest or (now - RECONCILE_FILL_LOOKBACK)

    def _resolve_after_timeout(self, client_order_id: str):
        """Placed or not, decided by `client_order_id` and never by a resend (section 9.1).

        The filter is applied here rather than in the query because Task 6's reader is GET-only
        and pinned: `get_orders` takes a `status` and nothing else. The decision is still made
        on the idempotency key -- the one identifier that is ours, unique, and known to both
        sides before the venue has assigned anything.

        The returned `OrderView` carries the two fields `place` reads off a placement,
        `order_id` and `order_group_id`, so the caller does not care which of the two shapes it
        got. The echo check is not re-run: we asked for one specific `client_order_id` and the
        venue named it, and the next reconciliation is the backstop for anything else.
        """
        for view in self._reader.get_orders():
            if view.client_order_id == client_order_id:
                log.warning("send timed out; the venue holds %r for client_order_id %r",
                            sanitize_venue_text(view.order_id, 64), client_order_id)
                return view
        raise OrderNotPlaced(client_order_id)

    def on_drawdown_stop(self, session, now) -> set[str]:
        """Section 9.3 on the live path: a stopped variant trips the kill switch.

        Live is the half of decision 6 that is a brake rather than a label -- "alert and label"
        is the paper response, and real money that is 20 % below its trailing peak stops. The
        switch is global and `plan_actions` already reads it, so one stopped variant halts new
        placement for all of them; that is deliberate, because the switch is an operator-cleared
        latch and this is the dormant path.

        Written through `_venue_state_session` for the same reason every other safety write is:
        the equity sampler runs inside `session.begin_nested()`, and a trip rolled back with
        its savepoint would be no trip at all.

        The trip is re-asserted at every sample while the condition holds, which is deliberate:
        clearing the switch with the curve still 20 % below its trailing peak would put money
        back at risk under exactly the condition that stopped it, so an operator's clear takes
        effect once the variant recovers or its last verdict ages out of the 7-day window.
        """
        stopped = stopped_variants(session, now)
        if not stopped:
            return set()
        reason = ", ".join(f"drawdown_stop:{variant_id}" for variant_id in sorted(stopped))
        with self._venue_state_session(session) as state:
            self._trip_kill_switch(state, now, reason)
        return stopped

    def poll_fills(self, session, since, now=None) -> list:
        """The only live fill source (ruling B-I2): `GET /portfolio/fills` since `since`. The
        queue-model simulator is bypassed entirely in live mode.

        `now` is the loop's clock, so a mark or freeze written from a fill poll carries the same
        instant `is_routable` is later asked with; it falls back to this gateway's own clock for
        a caller that has none (fix round 1, Minor 3).
        """
        with self._venue_call(session, now or self._now()):
            return self._reader.get_fills(since)

    # -- our rows behind a venue order ---------------------------------------------------

    def _row(self, session, order_id: int):
        return session.get(Order, order_id)

    def _group_of_open_orders(self, session) -> str | None:
        return session.execute(
            select(Order.order_group_id)
            .where(Order.status.in_(OPEN_STATUSES), Order.replay.is_(False),
                   Order.order_group_id.isnot(None))
            .order_by(Order.id.desc()).limit(1)).scalar()


def orders_by_venue_id(session, venue_order_ids) -> dict:
    """Our own order rows keyed by the venue's id for them, for the loop's live fill step.

    The mapping lives here rather than in `store` because it is the venue seam's own question:
    only a gateway ever holds a `venue_order_id` to look up.
    """
    ids = sorted({str(v) for v in venue_order_ids if v})
    if not ids:
        return {}
    rows = session.execute(
        select(Order).where(Order.venue_order_id.in_(ids), Order.replay.is_(False))).scalars()
    return {row.venue_order_id: row for row in rows}


def uses_the_simulator(gateway) -> bool:
    """Whether this gateway's fills come from the tape simulator, which is the paper path.

    Two guards, not one. The declared `simulates_fills` is the answer, because a gateway that
    *wraps* `PaperGateway` rather than subclassing it would fail an `isinstance` test and take
    the live branch -- silently ceasing to simulate anything, which is exactly the failure this
    task's own golden test hit while it was being written. A gateway that declares nothing
    raises here rather than being assumed to be either (review round 1, minor).

    The `isinstance` half stays as the belt: a `PaperGateway` that has somehow been told it does
    not simulate is a contradiction, not a configuration.
    """
    declared = getattr(gateway, "simulates_fills", None)
    if not isinstance(declared, bool):
        raise TypeError(
            f"{type(gateway).__name__} declares no simulates_fills; a gateway must say whether "
            "its fills come from the simulator or from a venue")
    if isinstance(gateway, PaperGateway) and not declared:
        raise TypeError("a PaperGateway always simulates its fills")
    return declared
