"""§1.3(f): the baseline comparison, at the retained action instants.

`exp baseline-check` asks one question: where the input history is reconstructible, does arm A
produce the actions the executor recorded **at the instants at which those actions occurred**?
It is deliberately not the question "does A reproduce every loop the executor ran" -- the loops
that acted are retained and compared, the idle loops between them are retained nowhere and are
`loop_spacing_unreconstructable`, the named limitation of §1.3(d).

Two rules the renderer enforces rather than leaves to the reader:

* missing input history reads **incomplete/unverifiable**, never as a passing result;
* the pass condition is **zero unexplained mismatches** at the retained action instants, not zero
  mismatches, and the resolved instant count is always printed beside the live loop estimate so
  the thinned clock cannot be mistaken for the live one.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

#: §2's `exp_mismatch` vocabulary, shared so T3's model and this module cannot drift.
MISMATCH_KINDS: tuple[str, ...] = ("action", "price", "fill", "cancel_instant", "expiry",
                                   "capacity")

#: The action kinds a lifecycle is compared on. `skip` and `cap_gate` are decisions *not* to act
#: and carry no instant of their own on the tape, so they are not part of the action comparison.
COMPARED_KINDS: tuple[str, ...] = ("place", "cancel", "expire", "fill")


@dataclass(frozen=True, slots=True)
class Mismatch:
    """One difference between the recorded slice and an arm's reproduction (§2; T3 persists it)."""

    run_id: str
    arm_id: str
    instant: datetime
    venue_market_id: int | None
    kind: str
    expected: dict
    actual: dict
    cause: str | None
    explained: bool

    def __post_init__(self) -> None:
        if self.kind not in MISMATCH_KINDS:
            raise ValueError(f"{self.kind!r} is not one of §2's mismatch kinds")
        if self.explained and self.cause is None:
            raise ValueError("an explained mismatch names its cause (§2's invariant query)")


def _key(action: dict) -> tuple:
    return (action.get("instant"), action.get("venue_market_id"), action.get("side"),
            action.get("kind"))


def _loose(action: dict) -> tuple:
    """The same identity without the instant: what a late or early action is matched on."""
    return (action.get("venue_market_id"), action.get("side"), action.get("kind"))


#: Which field disagreeing makes which kind of mismatch. Ordered: the first difference found is
#: the one reported, so one action never produces four rows saying the same thing.
_FIELD_KINDS: tuple[tuple[str, str], ...] = (("prob", "price"), ("contracts", "capacity"),
                                             ("deadline", "expiry"), ("filled", "fill"))


def compare_actions(recorded: Sequence[dict], produced: Sequence[dict], *, run_id: str,
                    arm_id: str) -> list[Mismatch]:
    """Pair the two action lists and report every unpaired or differing item.

    Pairing is by `(instant, venue_market_id, side, kind)` first. What is left over is paired a
    second time **ignoring the instant**, because an action taken at a different instant is the
    `cancel_instant`/`expiry` finding §1.3(f) asks for and not two separate absences; only what
    survives both passes is an `action` mismatch.
    """
    left = [row for row in recorded if row.get("kind") in COMPARED_KINDS]
    right = [row for row in produced if row.get("kind") in COMPARED_KINDS]
    out: list[Mismatch] = []
    pending_right = list(right)
    paired: list[tuple[dict, dict]] = []
    unpaired_left: list[dict] = []
    index: dict[tuple, list[dict]] = {}
    for row in pending_right:
        index.setdefault(_key(row), []).append(row)
    for row in left:
        bucket = index.get(_key(row))
        if bucket:
            match = bucket.pop(0)
            pending_right.remove(match)
            paired.append((row, match))
        else:
            unpaired_left.append(row)
    loose: dict[tuple, list[dict]] = {}
    for row in pending_right:
        loose.setdefault(_loose(row), []).append(row)
    still_unpaired: list[dict] = []
    for row in unpaired_left:
        bucket = loose.get(_loose(row))
        if bucket:
            match = bucket.pop(0)
            pending_right.remove(match)
            kind = "cancel_instant" if row.get("kind") == "cancel" else "expiry"
            if row.get("kind") == "place":
                kind = "action"
            out.append(Mismatch(run_id=run_id, arm_id=arm_id, instant=row.get("instant"),
                                venue_market_id=row.get("venue_market_id"), kind=kind,
                                expected={"instant": _iso(row.get("instant"))},
                                actual={"instant": _iso(match.get("instant"))},
                                cause=None, explained=False))
        else:
            still_unpaired.append(row)
    for row in still_unpaired:
        out.append(Mismatch(run_id=run_id, arm_id=arm_id, instant=row.get("instant"),
                            venue_market_id=row.get("venue_market_id"), kind="action",
                            expected=_summary(row), actual={}, cause=None, explained=False))
    for row in pending_right:
        out.append(Mismatch(run_id=run_id, arm_id=arm_id, instant=row.get("instant"),
                            venue_market_id=row.get("venue_market_id"), kind="action",
                            expected={}, actual=_summary(row), cause=None, explained=False))
    for expected, actual in paired:
        for field, kind in _FIELD_KINDS:
            # A field one side never records is not a difference of value: the recorded cancel
            # row carries the order's own expiry where the arm's carries its simulation
            # deadline, and comparing the two would report every reprice as an `expiry`
            # mismatch. Only `place` states a deadline both sides mean the same thing by.
            if field not in expected or field not in actual:
                continue
            if field == "deadline" and expected.get("kind") != "place":
                continue
            if _differs(expected.get(field), actual.get(field)):
                out.append(Mismatch(
                    run_id=run_id, arm_id=arm_id, instant=expected.get("instant"),
                    venue_market_id=expected.get("venue_market_id"), kind=kind,
                    expected={field: _plain(expected.get(field))},
                    actual={field: _plain(actual.get(field))}, cause=None, explained=False))
                break
    return sorted(out, key=lambda m: (m.instant or datetime.min, m.kind))


def _differs(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is not actual
    return str(expected) != str(actual)


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _plain(value):
    return _iso(value) if isinstance(value, datetime) else (
        str(value) if value is not None else None)


def _summary(action: dict) -> dict:
    return {key: _plain(action.get(key))
            for key in ("kind", "instant", "venue_market_id", "side", "prob", "contracts")}


# --- the recorded side of the comparison ---------------------------------------------------

#: The slice's own orders. Bounded by the window and the variants, on
#: `ix_orders_key_placed (variant_id, venue_market_id, side, placed_at)`; `replay = false` because
#: §0.12 forbids reusing the replay flag and the comparison is against the production executor.
_RECORDED_ORDERS = text(
    "select id, variant_id, venue_market_id, side, prob, contracts, placed_at, expiry, "
    "cancelled_at, cancel_reason from orders "
    "where placed_at >= :start and placed_at <= :end and replay = false "
    "and variant_id = any(:variant_ids)")

#: The lifecycle rows of exactly those orders, on the order-id-leading indexes
#: `ix_order_events_order_ts (order_id, ts)` / `uq_order_event (order_id, kind, ts)`.
_RECORDED_EVENTS = text(
    "select order_id, ts, kind, reason, prob, contracts from order_events "
    "where order_id = any(:order_ids) and ts >= :start and ts <= :end")


def recorded_actions(session: Session, *, warmup_start: datetime, observation_end: datetime,
                     variant_ids: Sequence[str]) -> list[dict]:
    """What the executor actually did in the slice, in the adapter's own action shape.

    Two bounded reads: the window's orders, then those orders' own events. The `order_events`
    kinds are the recorded vocabulary (`place`, `cancel`, `expire`); anything else -- `skipped`,
    `cap_gate` -- is a decision not to act and is not part of the action comparison.
    """
    bounds = {"start": warmup_start, "end": observation_end}
    orders = session.execute(_RECORDED_ORDERS,
                             bounds | {"variant_ids": list(variant_ids)}).all()
    if not orders:
        return []
    by_id = {row.id: row for row in orders}
    events = session.execute(_RECORDED_EVENTS,
                             bounds | {"order_ids": sorted(by_id)}).all()
    out: list[dict] = []
    for event in events:
        if event.kind not in COMPARED_KINDS:
            continue
        order = by_id[event.order_id]
        out.append({"kind": event.kind, "instant": event.ts, "order_id": order.id,
                    "venue_market_id": order.venue_market_id, "side": order.side,
                    "variant_id": order.variant_id,
                    "prob": event.prob if event.prob is not None else order.prob,
                    "contracts": (event.contracts if event.contracts is not None
                                  else order.contracts),
                    "deadline": order.expiry, "reason": event.reason})
    return sorted(out, key=lambda row: (row["instant"], row["order_id"], row["kind"]))


# --- the report -----------------------------------------------------------------------------

def render_baseline(mismatches: Sequence[Mismatch], *, instants: int, live_estimate: int,
                    limitations: Sequence) -> str:
    """§1.3(d)/(f)'s report: the clock beside the estimate, and a verdict that cannot overclaim."""
    unexplained = [m for m in mismatches if not m.explained]
    by_kind = {kind: sum(1 for m in mismatches if m.kind == kind) for kind in MISMATCH_KINDS}
    lines = ["baseline check (§1.3f): arm A against the recorded slice",
             f"  retained action instants   {instants}",
             f"  live loop estimate         {live_estimate}   "
             "(a number beside the clock, never the clock: C1)",
             f"  mismatches                 {len(mismatches)} total, "
             f"{len(unexplained)} unexplained"]
    for kind in MISMATCH_KINDS:
        if by_kind[kind]:
            lines.append(f"    {kind:<16} {by_kind[kind]}")
    lines.append(f"  limitations                {len(limitations)}")
    for limitation in limitations:
        lines.append(f"    {limitation.kind:<32} {limitation.detail}")
    if limitations or instants == 0:
        lines.append("  verdict: incomplete/unverifiable - the input history of this slice is "
                     "not fully reconstructible, so no result here is a reproduction of the "
                     "live executor and no parity claim is made.")
    elif unexplained:
        lines.append(f"  verdict: fail - {len(unexplained)} unexplained mismatches at the "
                     "retained action instants; each one's cause is unrecorded.")
    else:
        lines.append("  verdict: pass - 0 unexplained mismatches at the retained action "
                     "instants. This is a statement about the instants at which the executor "
                     "acted, never about the idle loops between them.")
    return "\n".join(lines)
