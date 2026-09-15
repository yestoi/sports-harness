"""`harness audit-order`: what the repaired simulator says about one recorded order.

Four synthetic capsules, one per outcome. No database, no NAS: a capsule is files, and that is
the whole input. The real capsule is order 157's, extracted by the controller; nothing here
reads it.

Every expected verdict below is derived from the capsule's own rows, never from what the
repaired simulator happens to output -- a verdict that agreed with the code by construction
would be a tautology rather than an audit.

The first five cases are the four outcomes and the command. The cases after them pin the
queries themselves (review round 1, C1, I1, I2, I4): each one is a capsule that *would* meet a
hypothesis under a looser reading of its prose and must not meet it under the simulator's own
matching rules, or a capsule whose evidence lives in the part of the 6A layout a naive reader
would miss. Those are the cases that decide the real capsule's permanent verdict.
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
             unverifiable=(), gaps=(), fills=()):
    """`gaps` and `fills` default to empty, so the five cases above are unchanged by them.

    `gaps` is its own file because that is where a 6A capsule puts them (`capsule.py:197-200`,
    a projection with no `kind` column); `fills` is the capsule's record of what C0 booked, which
    is what dates hypothesis (iii)'s "before the fills".
    """
    directory = tmp_path / "capsule"
    _write(directory, {
        "orders": [{"id": 157, "ticker": "K1", "side": "yes", "prob": "0.45",
                    "contracts": "87.00", "placed_at": at(0), "expiry": at(3600),
                    "cancelled_at": at(2119), "queue_ahead_at_place": "6401.00",
                    "filled_contracts": recorded_filled, "queue_remaining": recorded_queue,
                    "traded_at_price": "63.92", "venue_market_id": 1}],
        "venue_trades": prints,
        "orderbook_events": deltas + snapshots,
        "orderbook_events_gaps": list(gaps),
        "fills": list(fills), "order_events": [], "ledger": [], "order_watch_samples": [],
    })
    if unverifiable:
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["unverifiable_slices"] = list(unverifiable)
        # `default=str` as in `_write`: a manifest slice carries an instant, and the 6A manifest
        # holds it as the same ISO string every other capsule column is written as.
        (directory / "manifest.json").write_text(json.dumps(manifest, default=str))
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
    # Gated, not merely different: this capsule has no prints, so the replay path would also
    # read `unverifiable` and the verdict alone cannot tell the two apart. The absent simulated
    # quantities are what say the manifest decided it (0.16's fail-closed branch; with that
    # branch removed this case still passed before these two lines).
    assert result.repaired_filled is None
    assert result.repaired_queue is None


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
    assert set(result.evidence) == {"i", "ii", "iii",
                                    "manifest_slices_total", "manifest_slices_in_interval"}


def test_the_command_prints_the_verdict_and_its_evidence(tmp_path):
    """The CLI is what the controller runs, so the verdict and every hypothesis's observed count
    are on stdout as JSON -- not only the word."""
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = runner.invoke(app, ["audit-order", "--capsule", str(directory), "--order", "157"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["verdict"] in VERDICTS
    assert set(doc["evidence"]) == {"i", "ii", "iii",
                                    "manifest_slices_total", "manifest_slices_in_interval"}
    # The manifest counts are printed on every path, gated or not, so the record says what was
    # in the manifest as well as what decided (0.16).
    assert doc["evidence"]["manifest_slices_total"] == 0
    assert doc["evidence"]["manifest_slices_in_interval"] == 0


# --- the queries themselves (review round 1) --------------------------------------------------
# Helpers for the cases below: the counterexample's own rows, reused so each case differs from
# the reconciliation's tape in exactly one respect.

DOUBLE_COUNT_PRINTS = [{"trade_id": f"p{i}", "ts": at(1828.332), "yes_price": "0.45",
                        "count": c, "taker_side": "no", "source": "ws"}
                       for i, c in enumerate(["25.00", "25.00", "13.92"])]
DOUBLE_COUNT_DELTA = {"id": 9, "ts": at(1828.332), "kind": "delta", "side": "yes",
                      "price": "0.45", "delta": "-6376.00", "sid": 7, "seq": 2}


def _count(evidence_value) -> Decimal:
    """One evidence number, as the number it is: the JSON carries them as strings."""
    return Decimal(evidence_value)


def test_prints_our_order_could_not_have_filled_are_not_a_queue_collapse(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis.

    Derived from the rows: 6,401 contracts trade at 0.45 while we rest at yes 0.45, but every
    one of those prints has `taker_side = "yes"` -- the taker lifted a resting *ask*, so these
    trades happened against the other side of the book and cannot have touched our resting bid
    (`hits()`, `harness/execution/fills.py`). Nothing ahead of us traded, so there is no queue
    collapse to find and the difference from the recorded 38.92 has no supported cause.

    A query that read only "prints of 6,401 or more at 0.45" would call this `corrected` and
    name a genuine queue collapse, which is the causal story ruling IM-3 forbids -- on tape that
    is entirely ordinary: taker-yes prints at our price are a normal minute of a market.
    """
    prints = [{"trade_id": "wrong-side", "ts": at(1800), "yes_price": "0.45",
               "count": "6401.00", "taker_side": "yes", "source": "ws"}]
    directory = _capsule(tmp_path, prints=prints, deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert result.repaired_filled == Decimal("0.00")
    assert result.evidence["iii"]["met"] is False
    assert _count(result.evidence["iii"]["observed_hitting_volume"]) == 0
    # The literal reading is still reported, so the record shows what was on the tape.
    assert _count(result.evidence["iii"]["observed_volume_at_price_any_taker"]) == Decimal("6401")


def test_a_sweep_through_our_price_is_a_queue_collapse_although_the_price_differs(tmp_path):
    """Expected verdict `corrected`, hypothesis (iii).

    Derived from the rows: 6,401 contracts trade at 0.44 with `taker_side = "no"`. A taker-no
    trade at 0.44 lifted resting yes size at 0.44, which is *through* our 0.45 bid: everything
    resting at and above our price is gone, and the repaired simulator fills our whole 87
    (`price < prob`, the swept-through branch). The record says 38.92, so the two differ and the
    queue genuinely collapsed -- hypothesis (iii).

    A query demanding `yes_price == 0.45` exactly would report `observed volume 0` here and rule
    `unverifiable`, missing the one hypothesis the tape does support. The at-price-only count is
    reported beside it and is 0, which is the point.
    """
    prints = [{"trade_id": "sweep", "ts": at(1800), "yes_price": "0.44", "count": "6401.00",
               "taker_side": "no", "source": "ws"}]
    directory = _capsule(tmp_path, prints=prints, deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "corrected"
    assert result.hypothesis == "iii"
    assert result.repaired_filled == Decimal("87.00")
    before = _count(result.evidence["iii"]["observed_hitting_volume_before_fills"])
    assert before == Decimal("6401")
    assert _count(result.evidence["iii"]["observed_volume_at_price_any_taker"]) == 0


def test_a_decrement_on_the_other_side_of_the_book_is_not_the_double_count(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis.

    Derived from the rows: the counterexample's -6,376 is stamped on the **no** side. A no-side
    level at 0.45 is yes 0.55 -- a different level of a different queue -- and the repaired
    simulator ignores it (`_apply_queue_delta` acts only on our side at our price, shrinking).
    Our 6,401 are still ahead of us, the three prints take 63.92 of them, nothing reaches us, and
    the difference from 38.92 has no supported cause.

    Hypothesis (i) is "the equal-timestamp double count": a decrement that moved *our* queue at
    the same instant a print reported the same trade. A sum over both sides would call this
    `corrected` and write a double count into the record that could not have happened.
    """
    deltas = [dict(DOUBLE_COUNT_DELTA, side="no")]
    directory = _capsule(tmp_path, prints=DOUBLE_COUNT_PRINTS, deltas=deltas, snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert result.evidence["i"]["met"] is False
    assert _count(result.evidence["i"]["observed_decrement"]) == 0


def test_a_gap_in_its_own_file_on_the_anchors_sid_is_a_recovery_error(tmp_path):
    """Expected verdict `corrected`, hypothesis (ii).

    Derived from the rows: subscription 7 lost a frame at 14:51:47Z, inside our resting
    interval, and the book was re-anchored a second later from a snapshot on that same
    subscription. Our queue position was carried across a hole in the tape, which is exactly
    what hypothesis (ii) describes, and the repaired simulator -- given no tape at all after the
    anchor -- fills nothing against the recorded 38.92.

    The gap row lives in `orderbook_events_gaps.jsonl.gz` and carries no `kind` column, because
    that is what `harness/capsule.py` writes; a query that looked for `kind = "gap"` inside
    `orderbook_events` would find nothing on any real capsule and (ii) could never be met.
    """
    gaps = [{"id": 5, "ts": at(900), "sid": 7, "seq": None, "ticker": "",
             "raw": {"exposed_by": 4}}]
    snapshots = [{"id": 6, "ts": at(901), "kind": "snapshot", "sid": 7, "seq": 1,
                  "ticker": "K1", "raw": {}}]
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=snapshots, gaps=gaps,
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "corrected"
    assert result.hypothesis == "ii"
    assert result.evidence["ii"]["observed_gaps_in_interval"] == 1
    assert result.evidence["ii"]["observed_gaps_on_anchor_sid"] == 1
    assert result.evidence["ii"]["observed_snapshots"] == 1


def test_a_stale_gap_or_one_on_another_subscription_is_not_this_orders_recovery(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis.

    Derived from the rows: one gap is stamped 27 hours before this order was placed -- a period
    capsule's gap slice spans the whole capsule window, not our resting interval -- and the other
    is inside the interval but on subscription 999, while the snapshot that anchored our book is
    on subscription 7. Neither can have disturbed this order's anchor, so §1.7's "a gap row on
    the anchor's sid" is not satisfied and the difference from 38.92 keeps no cause.
    """
    gaps = [{"id": 4, "ts": at(-97200), "sid": 999, "seq": None, "ticker": "", "raw": {}},
            {"id": 5, "ts": at(900), "sid": 999, "seq": None, "ticker": "", "raw": {}}]
    snapshots = [{"id": 6, "ts": at(901), "kind": "snapshot", "sid": 7, "seq": 1,
                  "ticker": "K1", "raw": {}}]
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=snapshots, gaps=gaps,
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert result.evidence["ii"]["observed_gaps_in_interval"] == 1
    assert result.evidence["ii"]["observed_gaps_on_anchor_sid"] == 0
    assert result.evidence["ii"]["met"] is False


def test_only_a_snapshot_inside_the_resting_interval_anchors_the_recovery_hypothesis(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis.

    Derived from the rows: the only snapshot is the one every capsule carries at its own
    window's start, half an hour before we were placed. Nothing re-anchored while we rested, so
    a gap inside the interval has no recovery to point at.

    Counting that window-start anchor would make hypothesis (ii) met by construction on every
    capsule that has any gap, which is a tautology rather than evidence.
    """
    gaps = [{"id": 5, "ts": at(900), "sid": 7, "seq": None, "ticker": "", "raw": {}}]
    snapshots = [{"id": 1, "ts": at(-1800), "kind": "snapshot", "sid": 7, "seq": 1,
                  "ticker": "K1", "raw": {}}]
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=snapshots, gaps=gaps,
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert result.evidence["ii"]["observed_snapshots"] == 0
    assert result.evidence["ii"]["met"] is False


def test_another_tickers_tape_and_a_post_cancel_print_are_not_this_orders_evidence(tmp_path):
    """Expected verdict `corrected`, hypothesis (i), repaired fill 0 -- unchanged by the noise.

    Derived from the rows: this is the reconciliation's own counterexample, in a capsule that
    also carries 9,999 contracts traded on ticker K2 at the same instant and 500 more on K1 after
    we were cancelled. A period capsule merges every ticker into one `venue_trades` file, and an
    order capsule's window runs past the cancel, so both rows are ordinary.

    Neither is evidence about this order's queue: K2 is a different market, and a print after
    15:12:06Z happened when we no longer rested. Counted, the 9,999 would both meet hypothesis
    (iii) and -- fed to the simulator -- invent a fill of our whole remaining size, turning the
    audit's own answer into an artefact of the file layout.
    """
    prints = DOUBLE_COUNT_PRINTS + [
        {"trade_id": "other-ticker", "ts": at(1828.332), "yes_price": "0.45", "count": "9999.00",
         "taker_side": "no", "source": "ws", "ticker": "K2"},
        {"trade_id": "after-cancel", "ts": at(3000), "yes_price": "0.45", "count": "500.00",
         "taker_side": "no", "source": "ws", "ticker": "K1"}]
    directory = _capsule(tmp_path, prints=prints, deltas=[DOUBLE_COUNT_DELTA], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "corrected"
    assert result.hypothesis == "i"
    assert result.repaired_filled == Decimal("0.00")
    assert _count(result.evidence["iii"]["observed_hitting_volume"]) == Decimal("63.92")
    assert result.evidence["iii"]["met"] is False


def test_prints_after_the_recorded_fills_do_not_show_a_queue_collapse(tmp_path):
    """Expected verdict `unverifiable`, no hypothesis.

    Derived from the rows: the record books its fills at 14:53:27Z and the 6,401 contracts trade
    ten minutes *later*. Trading that happened after we were reported filled cannot be the
    reason we were reported filled, so hypothesis (iii)'s "before the fills" is not satisfied,
    and the difference from 38.92 keeps no cause. The whole-interval volume is reported beside
    the pre-fill one, so the record still shows that the 6,401 exist.
    """
    fills = [{"id": 1, "order_id": 157, "filled_at": at(1000), "contracts": "38.92"}]
    prints = [{"trade_id": "late-sweep", "ts": at(1600), "yes_price": "0.45",
               "count": "6401.00", "taker_side": "no", "source": "ws"}]
    directory = _capsule(tmp_path, prints=prints, deltas=[], snapshots=[], fills=fills,
                         recorded_filled="38.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert _count(result.evidence["iii"]["observed_hitting_volume_before_fills"]) == 0
    assert _count(result.evidence["iii"]["observed_hitting_volume"]) == Decimal("6401")


def test_a_capsule_that_does_not_carry_the_order_refuses(tmp_path):
    """The wrong capsule, or the wrong id, is an operator mistake in the quiet window: the
    command says so on stderr and exits 2 rather than printing a traceback."""
    directory = _capsule(tmp_path, prints=[], deltas=[], snapshots=[],
                         recorded_filled="38.92", recorded_queue="0.00")
    with pytest.raises(ValueError, match="not in this capsule"):
        audit_order(read_capsule(directory), 999)
    result = runner.invoke(app, ["audit-order", "--capsule", str(directory), "--order", "999"])
    assert result.exit_code == 2
    assert "not in this capsule" in (result.stderr or result.output)


# --- the manifest gate's scope (spec 0.16, user decision 2026-09-14 15:38 CT) -----------------
# The gate pre-empts the replay only for a slice that overlaps the order's own resting interval,
# `[placed_at, min(cancelled_at, expiry)]` -- here [at(0), at(2119)], the fixture's cancel being
# earlier than its expiry. A slice elsewhere in the capsule's window is counted in the evidence
# and decides nothing: it cannot have moved this order's queue, and the replay reads no tape
# outside the interval anyway.
#
# The tape below is the `validated` fixture's -- prints that genuinely exhaust the queue -- so an
# ungated run has a verdict of its own to reach and the two outcomes are told apart by the
# verdict as well as by the counts.

EXHAUSTING_PRINTS = [{"trade_id": "sweep", "ts": at(1800), "yes_price": "0.45",
                      "count": "6401.00", "taker_side": "no", "source": "ws"},
                     {"trade_id": "ours", "ts": at(1810), "yes_price": "0.45", "count": "38.92",
                      "taker_side": "no", "source": "ws"}]


def _gate_capsule(tmp_path, slices):
    """The `validated` capsule with a manifest that lists `slices`."""
    return _capsule(tmp_path, prints=EXHAUSTING_PRINTS, deltas=[], snapshots=[],
                    recorded_filled="38.92", recorded_queue="0.00", unverifiable=slices)


def test_a_manifest_slice_outside_the_resting_interval_does_not_pre_empt_the_replay(tmp_path):
    """Expected verdict `validated`, with the replay actually run.

    Derived from the capsule's rows: the one manifest entry is a `sink_exception` gap stamped a
    day after the order was cancelled -- the shape the real order 157 capsule carries six of.
    Nothing was lost while this order rested, the tape does cover its interval, and the prints
    inside it exhaust the recorded 6,401 and leave 38.92, so the record is reproduced.

    Reading the manifest capsule-wide would rule this `unverifiable` on a hole in another day's
    tape, which is the reading the user's 0.16 amendment replaced.
    """
    directory = _gate_capsule(tmp_path, [{"exposed_by": "sink_exception", "reason": "gap",
                                          "sid": 1, "ts": at(90_000)}])
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "validated"
    assert result.repaired_filled is not None
    assert result.evidence["manifest_slices_total"] == 1
    assert result.evidence["manifest_slices_in_interval"] == 0


def test_a_manifest_slice_inside_the_resting_interval_pre_empts_the_replay(tmp_path):
    """Expected verdict `unverifiable`, no replay.

    The same capsule with the same single entry moved to 14:46:47Z, ten minutes into the order's
    35-minute rest: the tape genuinely does not cover the interval the queue arithmetic would
    walk, so neither agreement nor disagreement with the record would mean anything. No replay is
    run, so both repaired quantities stay `None` rather than a zero that reads like a simulated
    fill of nothing.
    """
    directory = _gate_capsule(tmp_path, [{"exposed_by": "sink_exception", "reason": "gap",
                                          "sid": 1, "ts": at(600)}])
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.hypothesis is None
    assert result.repaired_filled is None
    assert result.repaired_queue is None
    assert result.evidence["manifest_slices_total"] == 1
    assert result.evidence["manifest_slices_in_interval"] == 1


@pytest.mark.parametrize("seconds, pre_empts", [
    (0, True),        # exactly at placement: the interval is closed at its lower end
    (2119, True),     # exactly at the cancel, which is this order's deadline
    (2120, False),    # one second after it: outside
])
def test_the_resting_interval_the_gate_reads_is_closed_at_both_ends(tmp_path, seconds,
                                                                    pre_empts):
    """The boundary is inclusive at both ends (0.16: `[placed_at, min(cancelled_at, expiry)]`).

    A gap stamped at the instant we were placed, or at the instant we were cancelled, cannot be
    ruled out as having touched the book this order rested in, so it gates; a second past the
    cancel is a stretch of tape the replay never reads.
    """
    directory = _gate_capsule(tmp_path, [{"exposed_by": "sink_exception", "reason": "gap",
                                          "sid": 1, "ts": at(seconds)}])
    result = audit_order(read_capsule(directory), 157)
    assert result.evidence["manifest_slices_total"] == 1
    assert result.evidence["manifest_slices_in_interval"] == (1 if pre_empts else 0)
    assert result.verdict == ("unverifiable" if pre_empts else "validated")
    assert (result.repaired_filled is None) is pre_empts


@pytest.mark.parametrize("entry, pre_empts", [
    ({"reason": "gap", "start": at(-100), "end": at(50)}, True),      # straddles placement
    ({"reason": "gap", "start": at(3000), "end": at(4000)}, False),   # wholly after the cancel
    ({"reason": "gap", "from": at(2100), "to": at(9000)}, True),      # straddles the cancel
    ({"reason": "gap", "from": at(-9000), "to": at(-100)}, False),    # wholly before placement
])
def test_a_manifest_slice_that_carries_a_range_gates_when_the_range_intersects(tmp_path, entry,
                                                                              pre_empts):
    """A slice stated as a stretch rather than an instant overlaps when it intersects.

    6A writes an instant today, so both spellings a range could take (`start`/`end` and
    `from`/`to`) are read: a gate that silently ignored the shape it did not know would replay a
    slice the manifest says is unreadable.
    """
    directory = _gate_capsule(tmp_path, [entry])
    result = audit_order(read_capsule(directory), 157)
    assert result.evidence["manifest_slices_in_interval"] == (1 if pre_empts else 0)
    assert result.verdict == ("unverifiable" if pre_empts else "validated")


def test_a_manifest_slice_with_neither_an_instant_nor_a_range_gates(tmp_path):
    """Fail closed: an entry the gate cannot place in time is treated as overlapping.

    `test_a_capsule_with_no_anchoring_snapshot_is_unverifiable` above is exactly this shape --
    the 6A `no_snapshot` entry carries a ticker and no timestamp at all -- so the rule is pinned
    here rather than left as an incident of that case. An unreadable slice cannot be ruled out
    of the interval, and replaying it would be the one direction that invents evidence.
    """
    directory = _gate_capsule(tmp_path, [{"reason": "no_snapshot", "ticker": "K1"}])
    result = audit_order(read_capsule(directory), 157)
    assert result.verdict == "unverifiable"
    assert result.repaired_filled is None
    assert result.evidence["manifest_slices_in_interval"] == 1


# --- 6B integration round: audit review Minor 4 and T9's `<=` alignment --------------------


@pytest.mark.parametrize("entry, pre_empts", [
    # Wholly after the cancel, with the range's first spelling present and explicitly null.
    ({"reason": "gap", "start": None, "end": None, "from": at(3000), "to": at(4000)}, False),
    # The same shape straddling the cancel: it still gates, so the fall-through does not turn
    # the gate off, it only stops an explicit null from being read as a stated bound.
    ({"reason": "gap", "start": None, "end": None, "from": at(2100), "to": at(9000)}, True),
])
def test_an_explicitly_null_range_bound_falls_through_to_the_other_spelling(tmp_path, entry,
                                                                           pre_empts):
    """Review Minor 4: `entry.get("start", entry.get("from"))` returns the null when the key is
    present and null -- a default only applies to a *missing* key -- so an entry that states its
    range as `from`/`to` while carrying explicit `start: null`/`end: null` was read as having no
    range at all and failed closed on every such slice, gating a replay the manifest does not
    forbid. Read each bound as "the first spelling that is not null".
    """
    directory = _gate_capsule(tmp_path, [entry])
    result = audit_order(read_capsule(directory), 157)
    assert result.evidence["manifest_slices_in_interval"] == (1 if pre_empts else 0)
    assert result.verdict == ("unverifiable" if pre_empts else "validated")


def test_a_difference_of_exactly_one_contract_is_corrected_not_validated(tmp_path):
    """T9's ruling, carried here: `validated` means the repaired simulator reproduces the record
    **strictly** within one contract, the rule `harness/rescore.py:201` already applies.

    Derived independently: 6,401 contracts ahead of us trade at at(1800) and 38.92 more go off
    at our price ten seconds later, so the repaired fill is 38.92 against a recorded 37.92 -- a
    difference of exactly 1.00. A tolerance that included its own boundary would call that
    agreement, and on a ten-contract order a one-contract difference is the whole correction.
    Hypothesis (iii) is met (the prints exhaust the queue ahead before any recorded fill), so
    the verdict is `corrected`; order 157's own verdict is untouched, its difference being 25.
    """
    directory = _capsule(tmp_path, prints=EXHAUSTING_PRINTS, deltas=[], snapshots=[],
                         recorded_filled="37.92", recorded_queue="0.00")
    result = audit_order(read_capsule(directory), 157)
    assert result.repaired_filled - result.recorded_filled == Decimal("1.00")
    assert result.verdict == "corrected"
    assert result.hypothesis == "iii"
