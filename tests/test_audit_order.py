"""`harness audit-order`: what the repaired simulator says about one recorded order.

Four synthetic capsules, one per outcome. No database, no NAS: a capsule is files, and that is
the whole input. The real capsule is order 157's, extracted by the controller; nothing here
reads it.

Every expected verdict below is derived from the capsule's own rows, never from what the
repaired simulator happens to output -- a verdict that agreed with the code by construction
would be a tautology rather than an audit.
"""

import gzip
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from harness.audit import VERDICTS, audit_order, read_capsule
from harness.cli import app

runner = CliRunner()
T0 = datetime(2026, 9, 8, 14, 36, 47, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _write(directory, tables: dict) -> None:
    """One capsule on disk: a gzipped JSON-lines file per table plus a manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    for table, rows in tables.items():
        body = "".join(json.dumps(row, default=str) + "\n" for row in rows).encode()
        (directory / f"{table}.jsonl.gz").write_bytes(gzip.compress(body))
        files.append({"name": f"{table}.jsonl.gz", "table": table, "rows": len(rows)})
    (directory / "manifest.json").write_text(json.dumps(
        {"files": files, "unverifiable_slices": [], "row_cap": 150_000}))


def _capsule(tmp_path, *, prints, deltas, snapshots, recorded_filled, recorded_queue,
             unverifiable=()):
    directory = tmp_path / "capsule"
    _write(directory, {
        "orders": [{"id": 157, "ticker": "K1", "side": "yes", "prob": "0.45",
                    "contracts": "87.00", "placed_at": at(0), "expiry": at(3600),
                    "cancelled_at": at(2119), "queue_ahead_at_place": "6401.00",
                    "filled_contracts": recorded_filled, "queue_remaining": recorded_queue,
                    "traded_at_price": "63.92", "venue_market_id": 1}],
        "venue_trades": prints,
        "orderbook_events": deltas + snapshots,
        "fills": [], "order_events": [], "ledger": [], "order_watch_samples": [],
    })
    if unverifiable:
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["unverifiable_slices"] = list(unverifiable)
        (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def test_the_equal_timestamp_counterexample_is_corrected(tmp_path):
    """Expected verdict `corrected`, hypothesis (i), repaired fill 0.

    Derived from the capsule's rows in the ledger's own terms, not from the simulator. 6,401
    contracts rest ahead of us at 0.45. One decrement of -6,376 lands at 15:07:15.332Z; no print
    has been applied yet, so `print_unmatched` is 0, the whole 6,376 is unexplained decrement
    volume, and under the `ahead` policy it takes queue: 6,401 - 6,376 = 25 still ahead, with
    6,376 sitting in a `pending` bucket stamped at that instant. The three prints at the same
    timestamp -- 25, 25 and 13.92, 63.92 in total -- then claim that bucket, which is what a
    claim means: those contracts were ahead of us and have now been reported as traded, so they
    move nothing a second time and none of them is ours. Queue 25, fill **0**.

    The recorded 38.92 is what counting the decrement and the prints as separate removals
    produces: the queue is drained twice, goes past zero, and the overflow crosses into our own
    order. Hypothesis (i) predicts exactly that decrement at that timestamp beside prints
    summing to 63.92, and the capsule carries both, so the verdict is `corrected` rather than
    `unverifiable`.
    """
    prints = [{"trade_id": f"p{i}", "ts": at(1828.332), "yes_price": "0.45", "count": c,
               "taker_side": "no", "source": "ws"}
              for i, c in enumerate(["25.00", "25.00", "13.92"])]
    deltas = [{"id": 9, "ts": at(1828.332), "kind": "delta", "side": "yes", "price": "0.45",
               "delta": "-6376.00", "sid": 7, "seq": 2}]
    directory = _capsule(tmp_path, prints=prints, deltas=deltas, snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "corrected"
    assert result.hypothesis == "i"
    assert result.repaired_filled == Decimal("0.00")


def test_a_capsule_with_no_anchoring_snapshot_is_unverifiable(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis.

    Derived independently: the 6A manifest records a slice it cannot verify, and a slice with no
    anchoring snapshot has no book to start from -- there is no queue to simulate against, so
    neither agreement nor disagreement with the record would mean anything. U8's rule is that
    such a slice is marked, not guessed at.
    """
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00",
                         unverifiable=[{"reason": "no_snapshot", "ticker": "K1"}])
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None


def test_prints_that_genuinely_exhaust_the_queue_validate_the_record(tmp_path):
    """Expected verdict `validated`.

    Derived independently: if 6,401 contracts ahead of us really did trade and 38.92 more went
    off at our price afterwards, then 38.92 is ours and the record is right. The repaired
    simulator must agree with the record here, or the repair would be removing real fills as
    well as invented ones -- which is what the `validated` outcome exists to catch.
    """
    prints = [{"trade_id": "sweep", "ts": at(1800), "yes_price": "0.45", "count": "6401.00",
               "taker_side": "no", "source": "ws"},
              {"trade_id": "ours", "ts": at(1810), "yes_price": "0.45", "count": "38.92",
               "taker_side": "no", "source": "ws"}]
    directory = _capsule(tmp_path, prints=prints, deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "validated"


def test_a_difference_no_hypothesis_explains_is_unverifiable(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis (ruling IM-3).

    Derived independently: the repaired simulation differs from the record, and none of the
    three hypotheses' expected counts is met -- no same-timestamp decrement near -6,376, no gap
    row and no anchoring snapshot in the window, no prints of 6,401 or more. The honest verdict
    is the reconciliation's "requires tape audit": a difference with no supported cause is not a
    correction, and naming one anyway would be a preference dressed as evidence.
    """
    prints = [{"trade_id": "small", "ts": at(1800), "yes_price": "0.45", "count": "5.00",
               "taker_side": "no", "source": "ws"}]
    directory = _capsule(tmp_path, prints=prints, deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert set(result.evidence) == {"i", "ii", "iii"}


def test_the_command_prints_the_verdict_and_its_evidence(tmp_path):
    """The CLI is what the controller runs, so the verdict and every hypothesis's observed count
    are on stdout as JSON -- not only the word."""
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = runner.invoke(app, ["audit-order", "--capsule", str(directory), "--order", "157"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["verdict"] in VERDICTS
    assert set(doc["evidence"]) == {"i", "ii", "iii"}
