"""`harness audit-order`: the repaired simulator's verdict on one recorded order.

Order 157 is the roadmap's one validated fill and the reconciliation's one unexplained
quantity, so it is audited before it is used as either. The input is a 6A capsule -- files, no
database, no NAS -- and the comparison is against the **recorded** quantities rather than
against a C0 code path, because C1-C5 remove that path from the tree (ruling IM-2). The 6A
capsule plus C0's recorded values are the reference.

Three verdicts and three hypotheses, each hypothesis a query over the capsule with a stated
expected count, so the verdict is evidence rather than a preference. A difference no hypothesis
explains is `unverifiable`, not `corrected`: that is the reconciliation's "requires tape audit",
and naming a cause the evidence does not support would be the worse answer.

The verdict string also lives in `harness/corrections.py`, because `docs/` is absent inside the
container and 6C's t13 reads the status there (D9). The record beside it,
`docs/superpowers/reviews/order-157-audit.md`, is undated: the run date is the controller's.

Every query here is written against the capsule layout `harness.capsule` actually writes, which
is not quite the shape a reader would guess (§0.1, `capsule.py:250-290`):

* `orderbook_events.jsonl.gz` carries the window's **deltas** (`kind = "delta"`, with `side`,
  `price`, `delta`, `sid`, `seq`) and the anchoring **snapshot** (`kind = "snapshot"`, with
  `raw` and no price columns at all), each row tagged with its `ticker`.
* the window's **gap** rows are a separate file, `orderbook_events_gaps.jsonl.gz`, projected
  without the `kind` column (`capsule.py:197-200`), so a gap is recognised by the file it is in
  rather than by a `kind` field that is not there.
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
    simulate_fills,
)

VERDICTS = ("validated", "corrected", "unverifiable")

#: Hypothesis (i)'s predicted decrement, and the tolerance it is recognised within. The
#: reconciliation's counterexample is -6,376 at our price stamped 15:07:15.332Z; a venue that
#: split the report across two rows would still sum to about that, so the test is on the sum
#: within 1 % rather than on one row's exact size.
H1_DECREMENT = Decimal("-6376")
H1_TOLERANCE = Decimal("0.01")
#: Hypothesis (iii)'s predicted print volume: the whole queue ahead of us at placement.
H3_QUEUE = Decimal("6401")
#: `validated` means the repaired simulation reproduces the recorded fills within one contract.
FILL_TOLERANCE = Decimal("1")


@dataclass(frozen=True)
class AuditResult:
    """One order's verdict, with what the repaired simulator said and what each hypothesis saw."""

    order_id: int
    verdict: str
    hypothesis: str | None
    repaired_filled: Decimal
    repaired_queue: Decimal | None
    recorded_filled: Decimal
    recorded_queue: Decimal | None
    evidence: dict = field(default_factory=dict)


def read_capsule(path: str | Path) -> dict:
    """Every table of one capsule directory, plus its manifest, as plain dictionaries.

    Streams each member rather than reading it whole: a period capsule at the row cap is
    150,000 rows a file, and an audit that needed all of them resident would be a memory bound
    on the controller's ssh session rather than on the database.
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


def _prints(capsule: dict, order: dict) -> list[dict]:
    """The order's own ticker's prints, as the capsule holds them."""
    return _mine(capsule.get("venue_trades", []), order)


def _events(capsule: dict, order: dict) -> list[dict]:
    """The order's own ticker's `orderbook_events` rows: deltas and the anchoring snapshot."""
    return _mine(capsule.get("orderbook_events", []), order)


def _gap_rows(capsule: dict, order: dict) -> list[dict]:
    """The window's gap rows, from either place they can be.

    The capsule writes them to `orderbook_events_gaps` **without** the `kind` column, so the
    file is the evidence; `orderbook_events` is still scanned for a `kind = "gap"` row so a
    capsule (or a fixture) that carries them inline is read too. A gap is subscription-level and
    the row carries `ticker = ''` (`capsule.py` `unverifiable`), so it is not filtered by ticker.
    """
    rows = list(capsule.get("orderbook_events_gaps", []))
    rows += [e for e in capsule.get("orderbook_events", []) if e.get("kind") == "gap"]
    return rows


def _hypotheses(capsule: dict, order: dict) -> dict:
    """Each hypothesis's observed count beside the count it predicts.

    Recorded as numbers, not booleans, so the audit record can say what was seen where a
    hypothesis was not met -- which is what turns an `unverifiable` verdict into something a
    reader can act on rather than a shrug.

    Each count is taken over the order's own resting interval, `(placed_at, deadline]`, which is
    the interval the replay walks: a print an hour after the cancel says nothing about the queue
    this order rested in. Hypothesis (ii)'s snapshot is the one the spec names -- a snapshot
    *inside* that interval, a re-anchor while we rested -- not the anchor every capsule carries
    at its window's start, which would make (ii) met by construction.
    """
    price = _d(order["prob"])
    placed_at, deadline = _ts(order["placed_at"]), _deadline(order)
    events = [e for e in _events(capsule, order) if placed_at < _ts(e["ts"]) <= deadline]
    at_price = [p for p in _prints(capsule, order)
                if _d(p["yes_price"]) == price and placed_at < _ts(p["ts"]) <= deadline]
    print_stamps = {_ts(p["ts"]) for p in at_price}
    same_ts = sum((_d(e.get("delta") or 0) for e in events
                   if e.get("kind") == "delta" and e.get("price") is not None
                   and _d(e["price"]) == price and _ts(e["ts"]) in print_stamps), Decimal("0"))
    # The volume the equal-timestamp hypothesis says was reported twice: the prints stamped at
    # the same instant as that decrement (63.92 across order 157's six `last_print_ids`).
    at_stamp = sum((_d(p["count"]) for p in at_price
                    if any(_ts(e["ts"]) == _ts(p["ts"]) for e in events
                           if e.get("kind") == "delta" and e.get("price") is not None
                           and _d(e["price"]) == price and _d(e.get("delta") or 0) < 0)),
                   Decimal("0"))
    gaps = _gap_rows(capsule, order)
    snapshots = [e for e in events if e.get("kind") == "snapshot"]
    # "Before the fills": the capsule's own `fills` rows for this order are what dates them, and
    # the resting interval is the fallback when the record has none.
    recorded_fills = [f for f in capsule.get("fills", [])
                      if int(f.get("order_id", -1)) == int(order["id"])]
    cutoff = min((_ts(f["filled_at"]) for f in recorded_fills), default=None)
    before_fills = sum((_d(p["count"]) for p in at_price
                        if cutoff is None or _ts(p["ts"]) < cutoff), Decimal("0"))
    return {
        "i": {"predicts": f"a same-timestamp decrement near {H1_DECREMENT} at {price}, beside "
                          "the prints stamped with it",
              "observed_decrement": str(same_ts),
              "observed_print_volume_at_that_stamp": str(at_stamp),
              "observed_print_volume": str(sum((_d(p["count"]) for p in at_price),
                                               Decimal("0"))),
              "met": bool(same_ts)
                     and abs(same_ts - H1_DECREMENT) <= abs(H1_DECREMENT) * H1_TOLERANCE},
        "ii": {"predicts": "a gap row on the anchor's sid and a snapshot inside the window",
               "observed_gaps": len(gaps),
               "observed_gap_sids": sorted({g["sid"] for g in gaps if g.get("sid") is not None}),
               "observed_snapshots": len(snapshots),
               "met": bool(gaps) and bool(snapshots)},
        "iii": {"predicts": f"prints of {H3_QUEUE} or more at {price} before the fills",
                "observed_print_volume_before_fills": str(before_fills),
                "observed_print_volume": str(sum((_d(p["count"]) for p in at_price),
                                                 Decimal("0"))),
                "met": before_fills >= H3_QUEUE},
    }


def _replay(capsule: dict, order: dict):
    """The repaired simulator over the capsule's own tape, from the order's placement.

    No book is handed in: a capsule's anchoring snapshot is the book at its window's start, not
    at ours, so the audit runs the queue arithmetic alone. The one thing that costs is F41's
    worst-case `snapshot_cross` fill, which is never a position and is deliberately outside the
    quantity this audit compares (`filled_contracts`).
    """
    queue = order.get("queue_ahead_at_place")
    paper = PaperOrder(order_id=int(order["id"]), ticker=order["ticker"], side=order["side"],
                       prob=_d(order["prob"]), contracts=_d(order["contracts"]),
                       placed_at=_ts(order["placed_at"]), expiry=_ts(order["expiry"]),
                       # None is R10's "no book existed at placement": the simulator then says
                       # nothing rather than assuming an empty queue.
                       queue_ahead_at_place=None if queue is None else _d(queue))
    prints = [TapePrint(p["trade_id"], _ts(p["ts"]), _d(p["yes_price"]), _d(p["count"]),
                        p.get("taker_side"), p.get("source", "ws"))
              for p in _prints(capsule, order)]
    deltas = [TapeDelta(int(e["id"]), _ts(e["ts"]), e["side"], _d(e["price"]), _d(e["delta"]),
                        int(e.get("sid") or 0), e.get("seq"))
              for e in _events(capsule, order) if e.get("kind") == "delta"]
    return simulate_fills(paper, SimState.initial(paper), None, prints, deltas,
                          _deadline(order), "queue_model")


def audit_order(capsule: dict, order_id: int) -> AuditResult:
    """Replay one capsule's order under the repaired simulator and rule on the difference."""
    orders = [row for row in capsule.get("orders", []) if int(row["id"]) == order_id]
    if not orders:
        raise ValueError(f"order {order_id} is not in this capsule")
    order = orders[0]
    evidence = _hypotheses(capsule, order)
    recorded_filled = _d(order["filled_contracts"])
    recorded_queue = (None if order.get("queue_remaining") is None
                      else _d(order["queue_remaining"]))
    if capsule["manifest"].get("unverifiable_slices"):
        # The 6A manifest already recorded that this slice cannot be replayed from its own
        # tape, which is that call's input (§1.7): nothing anchors it, so neither agreement nor
        # disagreement would mean anything. The hypotheses' counts are still reported, so the
        # record says what was in the capsule as well as why it could not be ruled on.
        return AuditResult(order_id, "unverifiable", None, Decimal("0.00"), None,
                           recorded_filled, recorded_queue, evidence)
    result = _replay(capsule, order)
    repaired_filled = result.state.filled_contracts
    if abs(repaired_filled - recorded_filled) <= FILL_TOLERANCE:
        return AuditResult(order_id, "validated", None, repaired_filled,
                           result.state.queue_remaining, recorded_filled, recorded_queue,
                           evidence)
    met = [name for name, row in evidence.items() if row["met"]]
    verdict = "corrected" if met else "unverifiable"
    return AuditResult(order_id, verdict, met[0] if met else None, repaired_filled,
                       result.state.queue_remaining, recorded_filled, recorded_queue, evidence)
