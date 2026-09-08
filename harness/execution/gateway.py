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
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select

from harness.db.models import Order
from harness.execution import store
from harness.venues.kalshi.authed import OrderIntent

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
    def poll_fills(self, session, since) -> list: ...

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

    def poll_fills(self, session, since) -> list:
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
                 order_group_id: str | None = None, exchange_index: int = 0) -> None:
        self._writer = writer
        self._reader = reader
        #: For the reconciliation, which runs outside a step's session (Task 10).
        self._session_factory = session_factory
        self.order_group_id = order_group_id
        self.exchange_index = int(exchange_index)

    # -- placement -----------------------------------------------------------------------

    def place(self, session, values: dict, action, market, now) -> PlacedOrder:
        """Send one order, then record it with the three venue columns Task 4 added.

        The send comes first because `client_order_id` is Kalshi's own idempotency key: a
        resent order is refused venue-side, while a row written for an order that never
        reached the venue would be a position we do not hold. The action runs inside the
        step's savepoint, so a transport failure takes the row with it and the startup
        reconciliation is what closes the remaining window (Task 10).
        """
        if self.order_group_id is None:
            raise NoOrderGroup("a live order needs an order group; reconcile has not run")
        if values.get("expiry") is None:
            raise ValueError("a live order needs an expiry (R8: kickoff - 10 min)")
        venue = self._writer.place_limit(OrderIntent(
            client_order_id=values["client_order_id"],
            ticker=values["ticker"],
            side=values["side"],
            prob=values["prob"],
            contracts=values["contracts"],
            expiration_time=values["expiry"],
            exchange_index=self.exchange_index,
            order_group_id=self.order_group_id,
            price_ranges=getattr(market, "price_ranges", None),
        ))
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
            self._writer.cancel(row.venue_order_id, row.ticker,
                                row.exchange_index_at_place or 0)
        return store.cancel_order(session, order_id, reason, now)

    def amend(self, session, order_id: int, prob: Decimal, contracts: Decimal, market,
              now) -> bool:
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
        updates: dict = {"prob": prob, "contracts": contracts}
        if row.venue_order_id:
            updated_client_order_id = str(uuid.uuid4())
            self._writer.amend(row.venue_order_id, prob, contracts, row.client_order_id,
                               updated_client_order_id, row.ticker, row.side,
                               row.exchange_index_at_place or 0,
                               getattr(market, "price_ranges", None))
            updates["client_order_id"] = updated_client_order_id
        store.update_order(session, order_id, updates)
        return True

    def cancel_all(self, session, reason: str, now) -> int:
        """One message pulls the whole group back; the rows are then closed one by one so that
        every cancel carries its reason. Returns how many of our rows actually moved."""
        group = self.order_group_id or self._group_of_open_orders(session)
        if group is not None:
            self._writer.cancel_group(group)
        ids = session.execute(
            select(Order.id).where(Order.status.in_(OPEN_STATUSES),
                                   Order.replay.is_(False), Order.mode == "live")
            .order_by(Order.id)).scalars().all()
        return sum(1 for order_id in ids
                   if store.cancel_order(session, order_id, reason, now))

    # -- reads ---------------------------------------------------------------------------

    def reconcile(self, session, now) -> ReconcileReport:
        """The startup sequence -- resting orders, unknown cancels, positions -- is Task 10's.
        Until then this reports nothing found, which is what a gateway that has asked the venue
        no questions is entitled to say."""
        return ReconcileReport()

    def poll_fills(self, session, since) -> list:
        """The only live fill source (ruling B-I2): `GET /portfolio/fills` since `since`. The
        queue-model simulator is bypassed entirely in live mode."""
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
