"""The demo smoke (addendum §1.5): one full authenticated round trip against play money.

This is the only place in the harness that sends an order message, and it sends it to
`demo.kalshi.co` with a balance no one can lose. It exists to prove the signed transport, the
V2 order shape, the encoder's grid arithmetic and the cancel paths against the real venue
before any of them is ever pointed at production.

**What it may write.** `venue_requests` rows tagged `env = 'demo'`, written by the transport's
recorder, and on a failure one `venue_status` row for `('kalshi', 'demo')`. Nothing else: no
`orders`, no `fills`, no `venue_trades`, no `orderbook_events`, no signal or intent.
**Demo prices are not evidence** and never reach a pricing table or the tape.

**What it prints.** A table of steps whose `detail` carries numbers, booleans and enum values
drawn from an allowlist this module owns -- never a string the venue chose. A ticker, a title,
an error message or a status the venue invents is untrusted text, and the cheapest way to keep
it out of an operator's terminal is not to render it at all. The one place venue text is kept
is `venue_status.reason`, which is the column designed for it: bounded to 120 characters,
ASCII-escaped by `mark_status`, and read as data.

**The guard is not duplicated here.** `make_writer(settings, "demo")` is what asserts the demo
host and the two secret files, and its file check is `is_file()`, never `exists()` (C3): a
missing Compose bind source leaves an empty *directory* at the mount target, and `exists()`
would call that a credential.
"""
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from harness.execution.venue import STATUS_UNAVAILABLE, make_reason, mark_status
from harness.venues.kalshi.authed import (
    KalshiApiError, OrderIntent, _check_status, grid_steps, make_writer,
)
from harness.venues.kalshi.http import session_recorder
from harness.venues.kalshi.public import parse_market_summaries

log = logging.getLogger("harness")

#: The series the smoke trades. One open football market is all it needs, and football is the
#: only thing this harness prices.
SMOKE_SERIES = "KXNFLGAME"

#: The order group's contract ceiling, and the two orders' sizes. Deliberately tiny: the first
#: order is one contract at the market's lowest tick, the amend takes it to two.
SMOKE_GROUP_CONTRACTS_LIMIT = Decimal("5")
SMOKE_CONTRACTS = Decimal("1")
SMOKE_AMEND_CONTRACTS = Decimal("2")

#: The writer's own caps, sized for exactly this sequence: two contracts at two cents is four
#: cents of play money, so a bug that inflates either number is refused by `_check_caps` rather
#: than sent. `make_writer` defaults both to zero, which would refuse the smoke itself.
SMOKE_PER_BET_CAP_DOLLARS = Decimal("1.00")
SMOKE_CONTRACT_CAP = Decimal("2.00")

#: The second order's lifetime, and how long the smoke waits past it before asking whether the
#: venue expired it. The slack absorbs clock skew between us and the exchange.
SMOKE_EXPIRY_S = 60
SMOKE_EXPIRY_SLACK_S = 5

#: The first order's lifetime. It is cancelled explicitly a few steps later; the expiry only
#: bounds how long a crashed run could leave it resting.
SMOKE_FIRST_EXPIRY_S = 3600

#: `SmokeStep.detail` is truncated here (global constraints: 80 for smoke output, 120 for
#: `venue_status.reason`).
DETAIL_MAX_CHARS = 80

#: The order statuses this module will render. Anything else prints as `other`, so a venue that
#: invents a status cannot put its own text on an operator's screen.
KNOWN_ORDER_STATUSES = frozenset({
    "resting", "canceled", "cancelled", "executed", "pending", "open", "closed", "expired",
})


class SmokeAborted(RuntimeError):
    """A step failed. Carries the already-sanitized detail and the reason to store."""

    def __init__(self, detail: str, reason: str) -> None:
        super().__init__(detail)
        self.detail, self.reason = detail, reason


@dataclass(frozen=True)
class SmokeStep:
    name: str
    ok: bool
    detail: str          # numeric and enum fields only, already sanitized to 80 chars


@dataclass(frozen=True)
class SmokeResult:
    steps: list[SmokeStep]
    unfunded: bool

    def exit_code(self) -> int:
        """0 when unfunded (pre-loaded decision 1) or every step passed; 1 otherwise."""
        if self.unfunded:
            return 0
        return 0 if all(step.ok for step in self.steps) else 1


def _detail(text: str) -> str:
    """Every detail goes through here. The content is already ours -- numbers, booleans and
    allowlisted enums -- so this is the belt to the braces: strip anything that is not printable
    ASCII, flatten whitespace, and truncate to the 80-character budget."""
    flat = "".join(ch if ch.isprintable() and ch.isascii() else " " for ch in str(text))
    return " ".join(flat.split())[:DETAIL_MAX_CHARS]


def _enum(value) -> str:
    """A venue-supplied status, rendered only if this module recognises it."""
    text = str(value).strip().lower()
    return text if text in KNOWN_ORDER_STATUSES else "other"


def _num(value) -> str:
    """A venue-supplied number, already decoded to a `Decimal` (or `None`) by Task 6."""
    return "none" if value is None else str(value)


def _error_detail(exc: Exception) -> str:
    """A failure rendered without a single character the venue chose.

    `KalshiApiError` already carries a status and a `code` that Task 6 sanitized to 40
    characters of a fixed alphabet, so both are safe to show. Every other exception is reduced
    to its class name: its message may quote a response body verbatim.
    """
    if isinstance(exc, KalshiApiError):
        code = getattr(exc, "code", None)
        return _detail(f"KalshiApiError status={getattr(exc, 'status', 'none')} "
                       f"code={code if code else 'none'}")
    return _detail(type(exc).__name__)


def _default_writer_factory(settings, session_factory):
    """The guard, plus the one wiring `make_writer` does not do.

    `make_writer` takes no recorder (its signature is Task 8's and is pinned), so the transport
    it builds records nothing. The smoke's whole audit trail is its `venue_requests` rows, so
    the session recorder is attached here, to the transport the guard just built. `_recorder` is
    private to `harness.venues.kalshi.http`, which is this module's own package.
    """
    writer = make_writer(settings, "demo",
                         per_bet_cap_dollars=SMOKE_PER_BET_CAP_DOLLARS,
                         contract_cap=SMOKE_CONTRACT_CAP,
                         kill_switch_active=lambda: False)
    writer.reader._transport._recorder = session_recorder(session_factory)
    return writer


def _nearest_open_market(body, now: datetime) -> tuple[str, object, int]:
    """The open market of `SMOKE_SERIES` that closes soonest after `now`.

    Returns its ticker, its `price_ranges` and how many markets the page carried. The choice is
    made on `close_time`, a timestamp Task 1's `parse_market_summaries` already parses and
    range-checks; a market with no parseable close time is not a candidate, because "nearest"
    would then be undefined for it.
    """
    markets = body.get("markets", []) if isinstance(body, dict) else []
    raw_by_ticker = {m.get("ticker"): m for m in markets if isinstance(m, dict)}
    candidates = [s for s in parse_market_summaries(body)
                  if s.ticker.startswith(SMOKE_SERIES) and s.close_time and s.close_time > now]
    if not candidates:
        raise SmokeAborted(_detail(f"no open {SMOKE_SERIES} market in {len(markets)} rows"),
                           f"no open {SMOKE_SERIES} market")
    chosen = min(candidates, key=lambda s: s.close_time)
    raw = raw_by_ticker.get(chosen.ticker) or {}
    return chosen.ticker, raw.get("price_ranges"), len(markets)


def run_smoke(settings, session_factory, now, sleep=time.sleep, *,
              writer_factory=None) -> SmokeResult:
    """The full demo sequence. Writes nothing to `orders`, `fills`, `venue_trades`,
    `orderbook_events` or any pricing table: the only rows it produces are `venue_requests`
    tagged env='demo' (written by the transport) and, on a failure, a `venue_status` demo row.

    `writer_factory` is a test seam and nothing else. Left at its default the smoke builds its
    writer through `make_writer(settings, "demo")`, so the host assertion and the credential
    check are the guard's, not a second copy; the sequence tests hand in a writer over a fake
    transport instead, and the four refusal tests exercise the real guard through the default.
    """
    writer = (writer_factory or _default_writer_factory)(settings, session_factory)
    reader = writer.reader
    transport = reader._transport

    steps: list[SmokeStep] = []
    #: Every order group this run has created and not yet cancelled. `DELETE
    #: /portfolio/order_groups/{id}` permanently removes a group (ctx7 `/openapi/kalshi_openapi_yaml`,
    #: read 2026-09-09 09:14 CT: "Deletes an order group and cancels all orders within it. This
    #: permanently removes the group."), so a group is a one-shot resource: once cancelled it
    #: cannot take the next order (fix 29; the demo venue answered a reused group's create with
    #: 404, `docs/superpowers/autopilot/evidence/2026-09-09-demo-smoke-0912.txt`). The failure
    #: path below best-effort-cancels whichever of these are still live.
    live_groups: list[str] = []

    def record(name: str, detail: str) -> None:
        steps.append(SmokeStep(name=name, ok=True, detail=_detail(detail)))

    try:
        # 1. Balance. A zero balance is not a failure: an unfunded demo account cannot rest an
        #    order, and pre-loaded decision 1 says say so and exit 0.
        balance = reader.get_balance()
        if balance.balance is None or balance.balance <= 0:
            record("balance", f"demo unfunded balance={_num(balance.balance)}")
            log.info("kalshi demo smoke: demo unfunded; nothing sent")
            return SmokeResult(steps=steps, unfunded=True)
        record("balance", f"balance={_num(balance.balance)} payout={_num(balance.payout)}")

        # 2. The nearest open market, and the grid it quotes.
        result = transport.request("GET", "/markets", {
            "series_ticker": SMOKE_SERIES, "status": "open", "limit": "1000"})
        _check_status(result, "GET", "/markets")
        ticker, price_ranges, seen = _nearest_open_market(result.body, now)
        record("market", f"markets={seen} chosen=1 parsed_ranges={price_ranges is not None}")

        # 3. The grid, printed: the encoder floors to it, and on an asymmetric grid a NO leg's
        #    YES price can land off it, so an operator has to see which grid priced this run.
        grid = grid_steps(price_ranges)
        if len(grid) < 2:
            raise SmokeAborted(_detail(f"grid has {len(grid)} steps, needs 2"),
                               "market grid too small")
        low, second = grid[0], grid[1]
        record("grid", f"steps={len(grid)} low={low} next={second} high={grid[-1]}")

        # 4. The order group. `contracts_limit` is the venue's own ceiling on the group.
        group_id = writer.create_group(SMOKE_GROUP_CONTRACTS_LIMIT)
        live_groups.append(group_id)
        record("group", f"created=true limit={SMOKE_GROUP_CONTRACTS_LIMIT}")

        # 5. One post-only YES bid at the lowest grid price for one contract. At the bottom
        #    tick it is as far from the touch as this market allows, so it rests and never fills.
        client_order_id = str(uuid.uuid4())
        placed = writer.place_limit(OrderIntent(
            client_order_id=client_order_id, ticker=ticker, side="yes", prob=low,
            contracts=SMOKE_CONTRACTS,
            expiration_time=now + timedelta(seconds=SMOKE_FIRST_EXPIRY_S),
            exchange_index=0, order_group_id=group_id, price_ranges=price_ranges))
        record("place", f"prob={_num(placed.prob)} contracts={_num(placed.contracts)} "
                        f"status={_enum(placed.status)} reads={placed.confirm_reads}")

        # 6. Amend: one grid step up, two contracts. The echo check runs again here.
        amended = writer.amend(placed.order_id, second, SMOKE_AMEND_CONTRACTS,
                               client_order_id, str(uuid.uuid4()), ticker, "yes", 0,
                               price_ranges)
        record("amend", f"prob={_num(amended.prob)} contracts={_num(amended.contracts)} "
                        f"status={_enum(amended.status)} reads={amended.confirm_reads}")

        # 7. Read it back, and check the venue agrees with its own echo. The size is the sum of
        #    the two live counts, never `count` (fix 28): the single-order body carries no
        #    `count` at all, and its `initial_count_fp` is the size at placement, which stays
        #    at one contract after an amend to two.
        fetched = reader.get_order(placed.order_id)
        total = (None if fetched.fill_count is None or fetched.remaining_count is None
                 else fetched.fill_count + fetched.remaining_count)
        if fetched.price != second or total != SMOKE_AMEND_CONTRACTS:
            raise SmokeAborted(
                _detail(f"get_order price={_num(fetched.price)} "
                        f"remaining={_num(fetched.remaining_count)} "
                        f"fill={_num(fetched.fill_count)} "
                        f"expected {second}/{SMOKE_AMEND_CONTRACTS}"),
                "get_order disagreed with the amend echo")
        record("get_order", f"price={_num(fetched.price)} "
                            f"remaining={_num(fetched.remaining_count)} "
                            f"fill={_num(fetched.fill_count)} "
                            f"status={_enum(fetched.status)}")

        # 8. Cancel, then cancel the group. This permanently deletes it (reference above), so
        #    the expiring order below cannot go into it -- the demo venue answered that reuse
        #    with a 404 (fix 29, evidence cited above).
        cancelled = writer.cancel(placed.order_id, ticker, 0)
        record("cancel", f"reduced_by={_num(cancelled.reduced_by)}")
        writer.cancel_group(group_id)
        live_groups.remove(group_id)
        record("cancel_group", "cancelled=true")

        # 9. A second, fresh order group for the expiring order (fix 29).
        second_group_id = writer.create_group(SMOKE_GROUP_CONTRACTS_LIMIT)
        live_groups.append(second_group_id)
        record("group_2", f"created=true limit={SMOKE_GROUP_CONTRACTS_LIMIT}")

        # 10. A second order that the venue itself must expire.
        expiring = writer.place_limit(OrderIntent(
            client_order_id=str(uuid.uuid4()), ticker=ticker, side="yes", prob=low,
            contracts=SMOKE_CONTRACTS,
            expiration_time=now + timedelta(seconds=SMOKE_EXPIRY_S),
            exchange_index=0, order_group_id=second_group_id, price_ranges=price_ranges))
        record("place_expiring", f"expires_in_s={SMOKE_EXPIRY_S} "
                                 f"status={_enum(expiring.status)}")

        sleep(SMOKE_EXPIRY_S + SMOKE_EXPIRY_SLACK_S)
        resting = reader.get_orders(status="resting")
        still_there = sum(1 for o in resting if o.order_id == expiring.order_id)
        if still_there:
            raise SmokeAborted(
                _detail(f"order still resting {SMOKE_EXPIRY_S + SMOKE_EXPIRY_SLACK_S}s "
                        f"past its expiry; resting={len(resting)}"),
                "the venue did not expire an expiring order")
        record("expiry", f"expired=true resting={len(resting)}")

        # 11. The two read paths a live executor would reconcile against.
        fills = reader.get_fills()
        record("fills", f"fills={len(fills)}")
        positions = reader.get_positions()
        record("positions", f"positions={len(positions)}")

        # 12. Cancel the second group. Nothing should be resting in it by now, but a cancelled
        #     group is the clean state to leave the demo account in.
        writer.cancel_group(second_group_id)
        live_groups.remove(second_group_id)
        record("cancel_group_2", "cancelled=true")

    except Exception as exc:                    # every step failure lands here, named
        name = _next_step_name(steps)
        detail = exc.detail if isinstance(exc, SmokeAborted) else _error_detail(exc)
        reason = (exc.reason if isinstance(exc, SmokeAborted)
                  else make_reason(getattr(exc, "status", None), exc))
        steps.append(SmokeStep(name=name, ok=False, detail=detail))
        log.warning("kalshi demo smoke failed at step %s: %s", name, detail)
        _mark_demo_unavailable(session_factory, name, reason, now)
        for gid in list(live_groups):
            steps.append(_best_effort_cancel_group(writer, gid))

    return SmokeResult(steps=steps, unfunded=False)


#: The sequence's step names in order, so a failure is reported against the step that was
#: running rather than against a generic "error".
STEP_ORDER = ("balance", "market", "grid", "group", "place", "amend", "get_order", "cancel",
              "cancel_group", "group_2", "place_expiring", "expiry", "fills", "positions",
              "cancel_group_2")


def _next_step_name(steps: list[SmokeStep]) -> str:
    done = {step.name for step in steps}
    for name in STEP_ORDER:
        if name not in done:
            return name
    return "unknown"


def _mark_demo_unavailable(session_factory, step: str, reason: str, now: datetime) -> None:
    """One `venue_status` row for `('kalshi', 'demo')`. Never for `('kalshi', 'prod')`: a demo
    401 has nothing to say about the production account (A-I3). A database that cannot be
    reached must not turn a reported failure into a traceback, so this swallows its own errors
    after logging them -- the failing step is already recorded and the exit code is already 1.
    """
    try:
        with session_factory() as session:
            mark_status(session, "kalshi", "demo", STATUS_UNAVAILABLE,
                        f"{step}: {reason}", now)
            session.commit()
    except Exception as exc:
        log.warning("kalshi demo smoke could not record venue_status: %s", type(exc).__name__)


def _best_effort_cancel_group(writer, group_id: str) -> SmokeStep:
    """A failed run must not leave a demo order resting for an hour. The group cancel is the one
    call that clears whatever the sequence left behind, and its own failure is a step, not a
    second exception."""
    try:
        writer.cancel_group(group_id)
    except Exception as exc:
        return SmokeStep(name="cleanup", ok=False, detail=_error_detail(exc))
    return SmokeStep(name="cleanup", ok=True, detail="cancelled=true")


def format_steps(result: SmokeResult) -> str:
    """The printed table. Fixed-width, and every cell is already sanitized."""
    lines = [f"{'step':<16}{'ok':<6}detail"]
    for step in result.steps:
        lines.append(f"{step.name:<16}{str(step.ok).lower():<6}{step.detail}")
    lines.append(f"unfunded={str(result.unfunded).lower()} exit={result.exit_code()}")
    return "\n".join(lines)
