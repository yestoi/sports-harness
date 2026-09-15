"""`harness audit-order`: the repaired simulator's verdict on one recorded order.

Order 157 is the roadmap's one validated fill and the reconciliation's one unexplained
quantity, so it is audited before it is used as either. The input is a 6A capsule -- files, no
database, no NAS -- and the comparison is against the **recorded** quantities rather than
against a C0 code path, because C1-C5 remove that path from the tree (ruling IM-2). The 6A
capsule plus C0's recorded values are the reference.

Four verdicts and three hypotheses, each hypothesis a query over the capsule with a stated
expected count, so the verdict is evidence rather than a preference. Spec amendment 0.18
(journal 224 item 14) splits what used to be one `unverifiable` string into two: a resting
interval with no tape to replay at all -- a manifest slice inside `[placed_at, min(cancelled_at,
expiry)]`, §0.16 -- is `unverifiable_uncovered`, and a replayed difference no hypothesis explains
is `unverifiable_differs`, not `corrected`: that is the reconciliation's "requires tape audit",
and naming a cause the evidence does not support would be the worse answer. The single string
collapsed two readings 6C's reader could not distinguish without the audit document.

Every hypothesis query is written with the **simulator's own matching rules**, not with a looser
reading of the prose that states it (review round 1, C1 and I1). A print is evidence about this
order only if `hits()` says its canonical taker side is the opposite of ours and
`price_on_side()` puts it at or through our level; a delta is evidence only if it is a negative
delta at our price on our side, which is exactly the test `_apply_queue_delta` applies. A
hypothesis a print that could not have lifted us can satisfy would produce a causal verdict the
evidence does not support, which is the failure ruling IM-3 exists to prevent.

The verdict string also lives in `harness/corrections.py`, because `docs/` is absent inside the
container and 6C's t13 reads the status there (D9). The record beside it,
`docs/superpowers/reviews/order-157-audit.md`, is undated: the run date is the controller's.

Every query here is written against the capsule layout `harness.capsule` actually writes, which
is not quite the shape a reader would guess (§0.1, `capsule.py:250-290`):

* `orderbook_events.jsonl.gz` carries the window's **deltas** (`kind = "delta"`, with `side`,
  `price`, `delta`, `sid`, `seq`) and the anchoring **snapshot** (`kind = "snapshot"`, with
  `raw` and `sid` and no price columns at all), each row tagged with its `ticker`. There is at
  most **one** snapshot per ticker: `export_ws_tape` takes `order by ts desc, id desc limit 1`
  (`harness/fixtures.py:116-120`), so an absent in-interval snapshot is weak evidence, not proof
  that no re-anchor happened.
* the window's **gap** rows are a separate file, `orderbook_events_gaps.jsonl.gz`, projected
  without the `kind` column (`capsule.py:197-200`), so a gap is recognised by the file it is in
  rather than by a `kind` field that is not there. They carry `sid` and `ts`, which is what lets
  §1.7's "on the anchor's sid" be tested rather than assumed.
* `venue_trades.jsonl.gz` is `_WS_PRINTS`' projection: `trade_id`, `ts`, `yes_price`, `count`,
  `taker_side` (already canonical), `is_block`, `source`, plus `ticker`.

A period capsule carries several tickers in those same files, so every read below is filtered to
the audited order's own ticker: mixing another ticker's tape into this order's queue would be a
silent arithmetic error rather than a visible one.
"""

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from harness.execution.fills import (
    PaperOrder,
    SimState,
    TapeDelta,
    TapePrint,
    hits,
    price_on_side,
    simulate_fills,
)

VERDICTS = ("validated", "corrected", "unverifiable_uncovered", "unverifiable_differs")
#: The three hypothesis keys of the evidence dict, in the order §1.7 states them, which is the
#: order a `corrected` verdict names them in. The dict also carries the plain-integer manifest
#: counts of §0.16, so the ruling is read off these keys rather than off everything in it.
HYPOTHESES = ("i", "ii", "iii")

#: Hypothesis (i)'s predicted decrement, and the tolerance it is recognised within. The
#: reconciliation's counterexample is -6,376 at our price stamped 15:07:15.332Z; a venue that
#: split the report across two rows would still sum to about that, so the test is on the sum
#: within 1 % rather than on one row's exact size.
H1_DECREMENT = Decimal("-6376")
H1_TOLERANCE = Decimal("0.01")
#: Hypothesis (iii)'s predicted print volume: the whole queue ahead of us at placement.
H3_QUEUE = Decimal("6401")
#: `validated` means the repaired simulation reproduces the recorded fills **strictly** within
#: one contract, the same rule and the same tolerance as `harness/rescore.py`'s `_verdict`
#: (T9's ruling, aligned in 6B's integration round).
FILL_TOLERANCE = Decimal("1")
ZERO = Decimal("0")


@dataclass(frozen=True)
class AuditResult:
    """One order's verdict, with what the repaired simulator said and what each hypothesis saw.

    Both repaired quantities are `None` when no replay was run -- the manifest-gated path, where
    the capsule's own 6A manifest says a slice **overlapping the order's resting interval** cannot
    be replayed (§0.16). A gated result therefore carries no simulated quantities at all, rather
    than a zero that reads like a simulated fill of nothing (review round 1, I3).
    """

    order_id: int
    verdict: str
    hypothesis: str | None
    repaired_filled: Decimal | None
    repaired_queue: Decimal | None
    recorded_filled: Decimal
    recorded_queue: Decimal | None
    evidence: dict = field(default_factory=dict)


def read_capsule(path: str | Path) -> dict:
    """Every table of one capsule directory, plus its manifest, as plain dictionaries.

    Each member is decompressed and parsed line by line, but the rows are **materialised**: the
    result holds every row of every file, so its footprint is the capsule's own, and a period
    capsule at the row cap is 150,000 rows a file across a dozen files. That is the same bound
    `harness.capsule` itself works under (`_rows` materialises before anything is written), and
    an order capsule -- which is what this audit is for -- is far smaller. A caller who wants
    less can drop the tables it does not read: `audit_order` consumes `orders`, `venue_trades`,
    `orderbook_events`, `orderbook_events_gaps` and `fills`.
    """
    directory = Path(path)
    out: dict = {"manifest": json.loads((directory / "manifest.json").read_text())}
    for member in sorted(directory.glob("*.jsonl.gz")):
        table = member.name[: -len(".jsonl.gz")]
        with gzip.open(member, "rt") as handle:
            out[table] = [json.loads(line) for line in handle if line.strip()]
    return out


def _ts(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def _d(value) -> Decimal:
    return Decimal(str(value))


def _mine(rows, order: dict):
    """`rows` restricted to the audited order's ticker.

    A row with no `ticker` at all is kept: a slice that does not carry the column cannot be
    said to belong to another ticker, and dropping it would silently empty the tape.
    """
    ticker = order.get("ticker")
    return [r for r in rows if r.get("ticker") in (None, ticker)]


def _deadline(order: dict) -> datetime:
    """When the track stopped: the cancel if there was one, otherwise the natural expiry."""
    expiry = _ts(order["expiry"])
    if order.get("cancelled_at") is None:
        return expiry
    return min(expiry, _ts(order["cancelled_at"]))


def _slice_overlaps(entry: dict, placed_at: datetime, deadline: datetime) -> bool:
    """Whether one 6A `unverifiable_slices` entry touches `[placed_at, deadline]` (§0.16).

    The manifest gate is scoped to the order's own resting interval: a slice elsewhere in the
    capsule's window cannot have moved this order's queue, and the interval already bounds every
    tape read the replay makes. The interval is **closed at both ends**, so a hole stamped at the
    instant of placement or at the instant of the cancel still gates -- a gap exactly at
    placement anchors nothing, and the deadline's own instant is inside the walk.

    6A writes an entry as an instant today (`exposed_by`, `reason`, `sid`, `ts`), so `ts` is the
    ordinary case. An entry that carries a stretch is read as one, under either spelling a range
    could take (`start`/`end` or `from`/`to`), and overlaps when the stretch intersects the
    interval; a half-stated range is unbounded on the side it omits.

    An entry with no usable instant and no usable range **fails closed** and is treated as
    overlapping: a slice the gate cannot place in time cannot be ruled out of the interval, and
    replaying it would be the one direction that invents evidence. 6A's own no-anchor entry --
    `{"reason": "no anchor", "ticker": ...}` (`harness/capsule.py:447`), which the fixtures spell
    `no_snapshot` -- carries a ticker and no timestamp at all, and is exactly that shape.
    """
    if not isinstance(entry, dict):
        return True
    # The first spelling that is not null, rather than `entry.get("start", entry.get("from"))`:
    # a `dict.get` default applies only to a *missing* key, so an entry that states its range as
    # `from`/`to` while carrying an explicit `start: null` was read as having no range at all and
    # failed closed -- gating a replay the manifest does not forbid (review Minor 4).
    start = entry.get("start")
    start = entry.get("from") if start is None else start
    end = entry.get("end")
    end = entry.get("to") if end is None else end
    try:
        if start is not None or end is not None:
            lower = None if start is None else _ts(start)
            upper = None if end is None else _ts(end)
            return ((upper is None or upper >= placed_at)
                    and (lower is None or lower <= deadline))
        instant = entry.get("ts")
        if instant is None:
            return True
        return placed_at <= _ts(instant) <= deadline
    except (TypeError, ValueError):
        # An unparseable stamp, or one whose awareness does not match the order's, is a slice we
        # cannot place in time: fail closed, as above.
        return True


def _tape_print(row: dict) -> TapePrint:
    """One `venue_trades` row as the simulator's own print, so the same rules can be applied."""
    return TapePrint(row["trade_id"], _ts(row["ts"]), _d(row["yes_price"]), _d(row["count"]),
                     row.get("taker_side"), row.get("source", "ws"))


def _prints(capsule: dict, order: dict) -> list[dict]:
    """The order's own ticker's prints, as the capsule holds them."""
    return _mine(capsule.get("venue_trades", []), order)


def _events(capsule: dict, order: dict) -> list[dict]:
    """The order's own ticker's `orderbook_events` rows: deltas and the anchoring snapshot."""
    return _mine(capsule.get("orderbook_events", []), order)


def _gap_rows(capsule: dict) -> list[dict]:
    """The capsule's gap rows, from either place they can be, each counted once.

    The capsule writes them to `orderbook_events_gaps` **without** the `kind` column, so the
    file is the evidence; `orderbook_events` is still scanned for a `kind = "gap"` row so a
    capsule (or a fixture) that carries them inline is read too, deduplicated on `(id, ts)` in
    case one capsule does both. A gap is subscription-level and the row carries `ticker = ''`
    (`capsule.py`'s `unverifiable`), so it is not filtered by ticker; it is bounded by time and
    matched to the anchor's `sid` in `_hypotheses` instead.
    """
    rows = list(capsule.get("orderbook_events_gaps", []))
    rows += [e for e in capsule.get("orderbook_events", []) if e.get("kind") == "gap"]
    out, seen = [], set()
    for row in rows:
        key = (row.get("id"), row.get("ts"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _hypotheses(capsule: dict, order: dict) -> dict:
    """Each hypothesis's observed count beside the count it predicts.

    Recorded as numbers, not booleans, so the audit record can say what was seen where a
    hypothesis was not met -- which is what turns an `unverifiable_differs` verdict into
    something a reader can act on rather than a shrug.

    Three bounds apply to every count, and each is the simulator's own (review round 1, C1, I1,
    I2):

    * **Time and ticker.** The order's own resting interval, `(placed_at, deadline]`, on the
      order's own ticker -- the interval `_merge_events` walks. A print an hour after the cancel,
      or on another ticker in a period capsule's shared file, says nothing about this queue.
    * **What can have touched us.** Prints are selected with `hits()` and `price_on_side()`: only
      a print whose canonical taker side is the opposite of ours, at or through our level, can
      have lifted our resting size. Deltas are selected as `_apply_queue_delta` selects them:
      our side, our price, negative. The literal "at 0.45, whoever the taker was" volume is
      reported beside the discriminating one, never used to meet a hypothesis.
    * **The anchor.** §1.7's hypothesis (ii) is "a gap row **on the anchor's sid**, and a
      snapshot between 14:36:47Z and 15:07:15Z", so the gap is bounded to the interval and its
      `sid` compared with the in-interval snapshots'.

    One limit of (ii) is worth stating where the query is: a capsule carries at most one snapshot
    per ticker (`harness/fixtures.py:116-120`, `limit 1`), so `observed_snapshots = 0` means the
    capsule's single snapshot is outside the interval, not that no re-anchor happened. (ii) not
    being met is therefore weak evidence, and the record document says so.
    """
    price = _d(order["prob"])
    side = order["side"]
    placed_at, deadline = _ts(order["placed_at"]), _deadline(order)
    events = [e for e in _events(capsule, order) if placed_at < _ts(e["ts"]) <= deadline]
    window_prints = [p for p in _prints(capsule, order)
                     if placed_at < _ts(p["ts"]) <= deadline]
    # What the simulator would have let touch us, and -- reported beside it, never used to meet
    # a hypothesis -- the literal "at our price, whoever the taker was" volume.
    hitting = [(p, _tape_print(p)) for p in window_prints]
    hitting = [(p, tp) for p, tp in hitting
               if hits(tp, side) and price_on_side(tp, side) <= price]
    at_price_any_taker = [p for p in window_prints
                          if price_on_side(_tape_print(p), side) == price]
    hitting_volume = sum((tp.count for _p, tp in hitting), ZERO)
    # Deltas exactly as `_apply_queue_delta` reads them: our side, our price, shrinking.
    decrements = [e for e in events
                  if e.get("kind") == "delta" and e.get("side") == side
                  and e.get("price") is not None and _d(e["price"]) == price
                  and _d(e.get("delta") or 0) < ZERO]
    print_stamps = {tp.ts for _p, tp in hitting}
    same_ts = sum((_d(e["delta"]) for e in decrements if _ts(e["ts"]) in print_stamps), ZERO)
    # The volume the equal-timestamp hypothesis says was reported twice: the hitting prints
    # stamped at the same instant as such a decrement (63.92 across 157's six `last_print_ids`).
    decrement_stamps = {_ts(e["ts"]) for e in decrements}
    at_stamp = sum((tp.count for _p, tp in hitting if tp.ts in decrement_stamps), ZERO)
    # (ii): gaps inside the interval, on the sid the in-interval snapshot anchored.
    gaps = [g for g in _gap_rows(capsule) if placed_at < _ts(g["ts"]) <= deadline]
    snapshots = [e for e in events if e.get("kind") == "snapshot"]
    anchor_sids = {e["sid"] for e in snapshots if e.get("sid") is not None}
    if anchor_sids:
        on_anchor = [g for g in gaps if g.get("sid") in anchor_sids]
        sid_rule = f"gap sid compared with the in-interval snapshots' sids {sorted(anchor_sids)}"
    else:
        # No in-interval snapshot carries a sid, so there is nothing to compare against; any gap
        # inside the interval counts, and the evidence says that is what happened.
        on_anchor = gaps
        sid_rule = "no in-interval snapshot carries a sid: any gap inside the interval counts"
    # (iii) "before the fills": the capsule's own `fills` rows for this order are what date them,
    # and the resting interval is the fallback when the record has none.
    recorded_fills = [f for f in capsule.get("fills", [])
                      if f.get("order_id") is not None and int(f["order_id"]) == int(order["id"])]
    cutoff = min((_ts(f["filled_at"]) for f in recorded_fills), default=None)
    before_fills = sum((tp.count for _p, tp in hitting if cutoff is None or tp.ts < cutoff), ZERO)
    return {
        "i": {"predicts": f"a decrement near {H1_DECREMENT} at {price} on the {side} side, "
                          "stamped at an instant a print that could have lifted us also carries",
              "observed_decrement": str(same_ts),
              "observed_hitting_volume_at_that_stamp": str(at_stamp),
              "observed_hitting_volume": str(hitting_volume),
              "met": abs(same_ts - H1_DECREMENT) <= abs(H1_DECREMENT) * H1_TOLERANCE},
        "ii": {"predicts": "a gap row on the anchor's sid inside the resting interval, and a "
                           "snapshot inside it",
               "observed_gaps_in_interval": len(gaps),
               "observed_gaps_on_anchor_sid": len(on_anchor),
               "observed_gap_sids": sorted(str(g.get("sid")) for g in gaps),
               "observed_snapshots": len(snapshots),
               "sid_rule": sid_rule,
               "met": bool(on_anchor) and bool(snapshots)},
        "iii": {"predicts": f"prints of {H3_QUEUE} or more that could have lifted us at or "
                            f"through {price}, before the fills",
                "observed_hitting_volume_before_fills": str(before_fills),
                "observed_hitting_volume": str(hitting_volume),
                "observed_volume_at_price_any_taker": str(
                    sum((_d(p["count"]) for p in at_price_any_taker), ZERO)),
                "met": before_fills >= H3_QUEUE},
    }


def _replay(capsule: dict, order: dict):
    """The repaired simulator over the capsule's own tape, from the order's placement.

    No book is handed in: a capsule's anchoring snapshot is the book at its window's start, not
    at ours, so the audit runs the queue arithmetic alone. The one thing that costs is F41's
    worst-case `snapshot_cross` fill, which is never a position and is deliberately outside the
    quantity this audit compares (`filled_contracts`).

    The whole ticker tape is handed over unfiltered in time: `_merge_events` drops everything
    outside `(placed_at, deadline]` itself, and letting it do so keeps this replay identical to
    the executor's own walk.
    """
    queue = order.get("queue_ahead_at_place")
    paper = PaperOrder(order_id=int(order["id"]), ticker=order["ticker"], side=order["side"],
                       prob=_d(order["prob"]), contracts=_d(order["contracts"]),
                       placed_at=_ts(order["placed_at"]), expiry=_ts(order["expiry"]),
                       # None is R10's "no book existed at placement": the simulator then says
                       # nothing rather than assuming an empty queue.
                       queue_ahead_at_place=None if queue is None else _d(queue))
    prints = [_tape_print(p) for p in _prints(capsule, order)]
    deltas = [TapeDelta(int(e["id"]), _ts(e["ts"]), e["side"], _d(e["price"]), _d(e["delta"]),
                        int(e.get("sid") or 0), e.get("seq"))
              for e in _events(capsule, order) if e.get("kind") == "delta"]
    return simulate_fills(paper, SimState.initial(paper), None, prints, deltas,
                          _deadline(order), "queue_model")


def audit_order(capsule: dict, order_id: int) -> AuditResult:
    """Replay one capsule's order under the repaired simulator and rule on the difference."""
    orders = [row for row in capsule.get("orders", [])
              if row.get("id") is not None and int(row["id"]) == order_id]
    if not orders:
        raise ValueError(f"order {order_id} is not in this capsule")
    order = orders[0]
    evidence = _hypotheses(capsule, order)
    recorded_filled = _d(order["filled_contracts"])
    recorded_queue = (None if order.get("queue_remaining") is None
                      else _d(order["queue_remaining"]))
    # The 6A manifest recorded which slices cannot be replayed from their own tape, which is
    # that call's input (§1.7), but only the ones overlapping this order's resting interval
    # `[placed_at, min(cancelled_at, expiry)]` bear on this order (§0.16, the user's decision of
    # 2026-09-14): a hole in another day's tape moved nothing this queue rested in, and the
    # interval already bounds every tape read the replay makes. A slice inside the interval is
    # decisive: nothing anchors it, so neither agreement nor disagreement would mean anything.
    # No replay is then run, so both repaired quantities are None rather than a zero
    # indistinguishable from a simulated fill of nothing (I3). Both counts are reported on every
    # path, gated or not, so the record says what was in the manifest as well as what decided;
    # the hypotheses' counts are reported either way for the same reason.
    slices = capsule["manifest"].get("unverifiable_slices") or []
    placed_at, deadline = _ts(order["placed_at"]), _deadline(order)
    in_interval = [s for s in slices if _slice_overlaps(s, placed_at, deadline)]
    evidence["manifest_slices_total"] = len(slices)
    evidence["manifest_slices_in_interval"] = len(in_interval)
    if in_interval:
        return AuditResult(order_id, "unverifiable_uncovered", None, None, None,
                           recorded_filled, recorded_queue, evidence)
    result = _replay(capsule, order)
    repaired_filled = result.state.filled_contracts
    # Strictly within the tolerance, the rule `harness/rescore.py:201` applies (T9's ruling): a
    # difference of exactly one contract is a difference, and on a ten-contract order it is the
    # whole correction. Order 157's own verdict is unaffected -- its difference is 25.
    if abs(repaired_filled - recorded_filled) < FILL_TOLERANCE:
        return AuditResult(order_id, "validated", None, repaired_filled,
                           result.state.queue_remaining, recorded_filled, recorded_queue,
                           evidence)
    met = [name for name in HYPOTHESES if evidence[name]["met"]]
    verdict = "corrected" if met else "unverifiable_differs"
    return AuditResult(order_id, verdict, met[0] if met else None, repaired_filled,
                       result.state.queue_remaining, recorded_filled, recorded_queue, evidence)
