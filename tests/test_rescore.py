"""`harness rescore`: retrospective estimates as new rows, never as edits.

A seeded world of four orders, one per outcome. The load-bearing assertions are that the
originals are untouched, that the result is a partition rather than a ratio, and that a second
run with `--resume` writes nothing.

The world is stated here rather than derived from the code (spec §1.8's "expected result,
computed independently"). Every order is `replay = false`, `nw_done = true`, a YES bid at 0.30
for 10 contracts, and every print is a `no` taker -- which is the counterparty who lifts a
resting YES bid:

| Order | `queue_ahead_at_place` | Tape | Recorded `filled_contracts` | Expected verdict |
|---|---|---|---|---|
| A | 2 | one print of 5 at T+1, no delta | 3 | `validated` |
| B | 5 | one print of 3 at T+1 *and* a delta of -3 at the same instant | 1 | `corrected` |
| C | 5 | nothing inside the window | 1 | `unverifiable`, no tape |
| D | 5 | the same tape as B | 1 | `unverifiable`, read cancelled |

A's arithmetic: the print of 5 hits our price, 2 of it is the queue ahead of us, 3 reaches us --
the recorded 3, so the record stands. B's: the delta and the print are one event, so the
repaired ledger takes the queue down once and nothing reaches us (0) where the defective
simulator counted the decrement and the print separately and reported 1. C has nothing to
anchor either agreement or disagreement. D's read is cancelled by its own statement timeout,
which is a different reason from C's and is counted separately.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import harness.rescore as rescore_module
from harness.rescore import rescore

#: One instant inside a week `tests/conftest.py` builds partitions for.
T0 = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)
CORRECTIONS_IN_FORCE = ["C1", "C2", "C3", "C4", "C5"]
#: The four orders of the world above, in id order.
A, B, C, D = 9101, 9102, 9103, 9104
#: Sentinel for "this order has no expiry at all", which `_order` cannot express by default.
EXPIRY_NONE = object()


def _order(session, order_id: int, ticker: str, queue_ahead, recorded_fill,
           expiry: datetime | None = None) -> None:
    """One `nw_done` paper order of the world above. `queue_ahead` and `expiry` are passed as
    None for the two absence cases rather than nulled by a later UPDATE: `Session.execute` does
    not autoflush a `text()` statement, so an UPDATE issued before the commit would match no
    row and the test would pass for the wrong reason."""
    from harness.db.models import Order

    session.add(Order(
        id=order_id, intent_id=uuid4(), variant_id="p00000000001", venue="kalshi",
        client_order_id=f"rescore-{order_id}", ticker=ticker, venue_market_id=order_id,
        side="yes", prob=Decimal("0.30"), contracts=Decimal("10.00"), status="expired",
        placed_at=T0,
        expiry=(T0 + timedelta(minutes=30) if expiry is None
                else None if expiry is EXPIRY_NONE else expiry),
        queue_ahead_at_place=None if queue_ahead is None else Decimal(queue_ahead),
        filled_contracts=Decimal(recorded_fill),
        nw_filled_contracts=Decimal("0.00"), nw_done=True, replay=False))


def _print(session, ticker: str, trade_id: str, count: str, ts: datetime) -> None:
    from harness.db.models import VenueTrade

    session.add(VenueTrade(venue="kalshi", trade_id=trade_id, ticker=ticker, ts=ts,
                           yes_price=Decimal("0.30"), count=Decimal(count), taker_side="no",
                           taker_outcome_side="no", source="ws"))


def _delta(session, ticker: str, ts: datetime, delta: str, seq: int) -> None:
    from harness.db.models import OrderbookEvent

    session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=1, seq=seq, kind="delta", side="yes",
                               price=Decimal("0.30"), delta=Decimal(delta), raw={}))


@pytest.fixture
def seeded_world(db_session, monkeypatch):
    """The four orders above, with D's read cancelled the way its statement timeout cancels it.

    D is patched at `harness.rescore._order_tape` rather than by making a real query slow: the
    case under test is what the command does with a cancellation, and a test that depended on
    a starved host to produce one would be a test of the host.
    """
    at = T0 + timedelta(seconds=1)
    _order(db_session, A, "KXRESCOREA", "2.00", "3.00")
    _print(db_session, "KXRESCOREA", "a-1", "5.00", at)
    _order(db_session, B, "KXRESCOREB", "5.00", "1.00")
    _print(db_session, "KXRESCOREB", "b-1", "3.00", at)
    _delta(db_session, "KXRESCOREB", at, "-3.00", 1)
    _order(db_session, C, "KXRESCOREC", "5.00", "1.00")
    _order(db_session, D, "KXRESCORED", "5.00", "1.00")
    _print(db_session, "KXRESCORED", "d-1", "3.00", at)
    _delta(db_session, "KXRESCORED", at, "-3.00", 1)
    db_session.commit()

    real = rescore_module._order_tape

    def cancelled_for_d(session, row, cap):
        if row.id == D:
            raise OperationalError("select ...", {}, Exception("statement timeout"))
        return real(session, row, cap)

    monkeypatch.setattr(rescore_module, "_order_tape", cancelled_for_d)

    def mark_pending(order_id: int) -> None:
        db_session.execute(text("update orders set nw_done = false where id = :i"),
                           {"i": order_id})
        db_session.commit()

    return SimpleNamespace(first=A, last=D, mark_pending=mark_pending)


def _rows(session, policy: str):
    return session.execute(text("select * from order_rescores where cancel_policy = :p "
                                "order by order_id"), {"p": policy}).all()


def _fills_triple(session):
    return session.execute(text("select count(*), coalesce(sum(contracts), 0), "
                                "coalesce(max(id), 0) from fills where replay = false")).one()


def _rescore_count(session) -> int:
    return session.execute(text("select count(*) from order_rescores")).scalar()


def test_the_four_outcomes_partition_the_denominator(db_session, seeded_world):
    """Expected verdicts `validated`, `corrected`, `unverifiable`, `unverifiable`, and a
    partition of 3 + 1 summing to the denominator 4.

    Derived independently from the seeded world, not from the code: order A's recorded fill is
    one the repaired simulator reproduces, so `validated`. Order B's fill is the equal-timestamp
    case the repair removes, so `corrected`. Order C's tape has a gap with no anchoring
    snapshot, so `unverifiable` with no tape. Order D's read is cancelled by its own statement
    timeout, so `unverifiable` for a different reason, and the two reasons are counted
    separately because collapsing them would make a starved host look like a gap-ridden tape.
    """
    counts = rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
                     corrections=CORRECTIONS_IN_FORCE)
    assert counts.denominator == 4
    assert (counts.completed + counts.unverifiable_no_tape
            + counts.unverifiable_read_cancelled) == 4
    assert (counts.completed, counts.unverifiable_no_tape,
            counts.unverifiable_read_cancelled) == (2, 1, 1)
    verdicts = {r.order_id: r.verdict for r in _rows(db_session, policy="ahead")}
    assert sorted(verdicts.values()) == ["corrected", "unverifiable", "unverifiable",
                                         "validated"]
    assert verdicts[A] == "validated" and verdicts[B] == "corrected"
    # The two repaired quantities the verdicts rest on, asserted so a verdict cannot come out
    # right from the wrong arithmetic: A's print leaves 3 after the queue, B's is one event
    # with its own decrement and leaves nothing.
    measured = {r.order_id: r.watched_filled for r in _rows(db_session, policy="ahead")}
    assert measured[A] == Decimal("3.00") and measured[B] == Decimal("0.00")
    # Review Minor 4: every row carries the elapsed seconds, the two unverifiable ones included
    # (C's tape is silent, D's read was cancelled; neither fact says anything about how long
    # their markets were dirty).
    timing = {r.order_id: (r.watched_dirty_s, r.counterfactual_dirty_s, r.unobserved_s)
              for r in _rows(db_session, policy="ahead")}
    assert timing[C] == (0, 0, 1800) and timing[D] == (0, 0, 1800)


def test_the_originals_are_untouched(db_session, seeded_world):
    """Expected: `fills`' row count, `sum(contracts)` and `max(id)` identical before and after.

    Derived independently: §6.7's amendment protocol says original rows are never rewritten. A
    re-score that edited one would destroy the only record of what the measurement was, which is
    the whole reason 6A preserved it first.
    """
    before = _fills_triple(db_session)
    orders_before = db_session.execute(text(
        "select count(*), sum(filled_contracts), sum(queue_remaining) from orders "
        "where replay = false")).one()
    rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
            corrections=CORRECTIONS_IN_FORCE)
    assert _fills_triple(db_session) == before
    assert db_session.execute(text(
        "select count(*), sum(filled_contracts), sum(queue_remaining) from orders "
        "where replay = false")).one() == orders_before


def test_two_policy_rows_per_order_and_equality_only_when_nothing_retired(db_session,
                                                                         seeded_world):
    """Expected: two rows per order, equal watched fills whenever `cancels_ahead` is 0.

    Derived independently: the two policies differ only about decrement volume nobody claimed
    (§0.7). `cancels_ahead = 0` proves an order insensitive to the choice; the converse does not
    hold, so the test asserts the implication and reports the disagreement rate rather than
    asserting an equality in both directions (ruling I-10).
    """
    rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
            corrections=CORRECTIONS_IN_FORCE)
    ahead = {r.order_id: r for r in _rows(db_session, policy="ahead")}
    behind = {r.order_id: r for r in _rows(db_session, policy="behind")}
    assert set(ahead) == set(behind)
    assert len(ahead) == 4
    for order_id, row in ahead.items():
        if row.cancels_ahead == Decimal("0.00"):
            assert behind[order_id].watched_filled == row.watched_filled
    # The band as a rate over the range, which is what the implication leaves reportable: an
    # asserted equality in the other direction is a claim this data cannot support.
    disagreed = sum(1 for oid, row in ahead.items()
                    if behind[oid].watched_filled != row.watched_filled)
    assert disagreed == 0


def test_a_pending_counterfactual_is_never_read_as_a_completed_one(db_session, seeded_world):
    """Expected: an `nw_done = false` order produces no completed row.

    Derived independently: `nw_done` is the only mark that distinguishes a completed
    counterfactual from a pending one -- 6B closes no track (ruling CR-4) -- so every consumer
    of the `nw_*` columns has to filter or label on it. An unfiltered re-score would read a
    track that is still running as one that finished with whatever it has so far, which
    understates every counterfactual on a starved host.
    """
    seeded_world.mark_pending(seeded_world.first)
    counts = rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
                     corrections=CORRECTIONS_IN_FORCE)
    assert not any(r.order_id == seeded_world.first and r.verdict == "validated"
                   for r in _rows(db_session, policy="ahead"))
    assert counts.denominator == 3


def test_a_resumed_run_writes_nothing(db_session, seeded_world):
    """Expected: the second run inserts zero rows.

    Derived independently: the primary key is `(order_id, correction_ids, cancel_policy)`, so a
    resumed run's every write conflicts. That is what makes the command abandonable under §4.4:
    an abandoned run costs only the orders it had not reached.
    """
    rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
            corrections=CORRECTIONS_IN_FORCE)
    before = _rescore_count(db_session)
    rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
            corrections=CORRECTIONS_IN_FORCE, resume=True)
    assert _rescore_count(db_session) == before
    # And without `--resume`: the run starts at `from_order` again and every insert conflicts
    # harmlessly, which is what makes an interrupted run safe to simply repeat.
    rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
            corrections=CORRECTIONS_IN_FORCE)
    assert _rescore_count(db_session) == before


def test_the_gate_render_discloses_the_mixed_population(monkeypatch):
    """Expected: the correction ids in force and the mixed-population sentence, in
    `render_gate`'s output (ruling IM-10, §3 row 10).

    Derived independently: until §0.13a is answered the gate reads the whole non-replay history,
    which after the 6B deploy is orders placed under two different simulators. A reader of the
    gate has to be told that in the gate's own output, not in a document beside it.

    Two halves, because C1-C6 are appended to `harness.corrections.CORRECTIONS` by §1.11 (Task
    11) and this branch's manifest still holds C0 alone: the line renders *the manifest's* ids,
    so it is asserted against today's manifest unpatched and against a manifest carrying C1-C5
    patched. When Task 11 lands, the unpatched half prints C1-C5 of its own accord.
    """
    from dataclasses import replace

    from harness.report import gate
    from harness.report.gate import GateResult, criteria_hash, render_gate

    result = GateResult(variant_id="v1", passed=False, criteria={},
                        criteria_hash=criteria_hash(), gate_variant=True)
    text_now = render_gate([result], {"v1": "one"}, {"v1": "primary"})
    assert "mixed population" in text_now
    assert f"corrections_in_force={','.join(c.id for c in gate.CORRECTIONS)}" in text_now

    monkeypatch.setattr(gate, "CORRECTIONS",
                        tuple(replace(gate.CORRECTIONS[0], id=f"C{n}") for n in range(1, 6)))
    text = render_gate([result], {"v1": "one"}, {"v1": "primary"})
    assert "mixed population" in text
    assert "C1" in text and "C5" in text


def test_no_gate_criterion_reads_order_rescores():
    """§3 row 9. An estimate reaching a criterion is an integrity anomaly, not a feature.

    Derived independently: `order_rescores` holds retrospective estimates under a named
    correction set. A criterion that read one would be judging the phase on a number produced by
    the code the phase is meant to be judging, and the pinned `criteria_hash` would no longer
    describe what the gate measures.
    """
    import inspect

    from harness.report import gate
    from harness.report.gate import CRITERIA, criteria_hash

    # `Criterion` is `(name, definition, fn, threshold)` -- `harness/report/gate.py`'s dataclass.
    # There is no SQL attribute at all: a criterion names a function and the SQL lives in module
    # constants, so the criterion side of the claim is asserted on the two text fields.
    for c in CRITERIA:
        assert "order_rescores" not in c.definition, c.name
        assert "order_rescores" not in c.fn, c.name
    # And the module side: the only mention of the table anywhere in `gate.py` is the render
    # block's correction line, which is text after the results and reads no criterion.
    source = inspect.getsource(gate)
    assert source.count("order_rescores") == 0, "gate.py must not name the estimates table"
    assert criteria_hash() == (
        "5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5")


def test_no_report_builder_counts_a_pending_counterfactual_as_complete():
    """§3 row 12. Every report module that reads an `nw_` column filters or labels on
    `nw_done`.

    Derived independently from the no-close rule: 6B closes no counterfactual track (ruling
    CR-4), so `nw_done` is the *only* mark separating a track that finished from one that is
    still running. A report cell that summed `nw_filled_contracts` without it would read a
    track still in flight as one that finished with whatever it has so far -- which understates
    every counterfactual on a starved host, and understates them most on the worst-taped
    tickers, making it a bias rather than noise.

    No builder under `harness/report/` reads an `nw_` column today, so this is a guard rather
    than a repair: it is what makes 6C's actual-versus-counterfactual separation state the
    filter when it adds one. 6C's own separation is out of scope here.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "harness" / "report"
    for path in sorted(root.glob("*.py")):
        source = path.read_text()
        # `nw_fill` is criterion 4's markout *anchor* -- a string label, not a column on
        # `orders` (`harness/report/gate.py`'s MARKOUT_ANCHOR) -- so it is excluded by name.
        column = re.search(r"\bnw_(?!fill\b)[a-z_]+\b", source)
        if column is None:
            continue
        assert "nw_done" in source, (
            f"{path.name} reads {column.group(0)} without naming nw_done: a pending "
            f"counterfactual would be counted as a completed one")


def test_an_order_with_no_book_at_placement_is_unverifiable_not_corrected(db_session):
    """Expected: `queue_ahead_at_place = NULL` gives `unverifiable`, never a repaired fill of 0.

    Derived independently from R10: a NULL means no book existed at placement, so the simulator
    is out of the order entirely (`simulate_fills` returns without walking the tape). Scoring
    that as a result would report a repaired fill of zero for every such order and label each
    one `corrected` -- a fabricated correction manufactured by an absence, which is the
    tautology ruling IM-3 forbids. It joins the no-tape cell because it is the same kind of
    thing: no evidence, as against a read the host cancelled.
    """
    at = T0 + timedelta(seconds=1)
    _order(db_session, 9105, "KXRESCOREE", None, "3.00")
    _print(db_session, "KXRESCOREE", "e-1", "5.00", at)
    db_session.commit()
    counts = rescore(db_session, from_order=9105, to_order=9105,
                     corrections=CORRECTIONS_IN_FORCE)
    assert (counts.denominator, counts.completed, counts.unverifiable_no_tape) == (1, 0, 1)
    rows = _rows(db_session, policy="ahead")
    assert [(r.order_id, r.verdict, r.watched_filled) for r in rows] == [
        (9105, "unverifiable", None)]
    # Review Minor 4: the elapsed seconds do not depend on whether the order could be scored,
    # so they are written on this path too -- otherwise any later aggregate over them would
    # silently condition on scorability.
    assert (rows[0].watched_dirty_s, rows[0].counterfactual_dirty_s,
            rows[0].unobserved_s) == (0, 0, 1800)


def test_an_order_with_no_expiry_is_unverifiable_rather_than_a_failed_run(db_session):
    """Expected: a NULL `expiry` gives `unverifiable`, and the run continues.

    Derived independently: `expiry` is both tape bound and track deadline (§0.14), so an order
    without one has no resting interval to read and no instant to stop either track at. The
    alternatives are worse than a label: excluding it from the driving query would shrink the
    denominator invisibly, and letting it through would compare `ts <= NULL` -- which matches no
    row -- and read as an empty tape.
    """
    _order(db_session, 9106, "KXRESCOREF", "2.00", "3.00", expiry=EXPIRY_NONE)
    db_session.commit()
    assert db_session.execute(text("select expiry from orders where id = 9106")).scalar() is None
    counts = rescore(db_session, from_order=9106, to_order=9106,
                     corrections=CORRECTIONS_IN_FORCE)
    assert (counts.denominator, counts.unverifiable_no_tape) == (1, 1)
    rows = _rows(db_session, policy="behind")
    assert [r.verdict for r in rows] == ["unverifiable"]
    # The one path whose timing columns stay NULL (review Minor 4): `order_dirty_time` excludes
    # NULL-expiry orders by construction (T6's F6), and an order with no resting interval has no
    # interval for the seconds to be measured over.
    assert (rows[0].watched_dirty_s, rows[0].unobserved_s) == (None, None)


def test_a_correction_set_that_does_not_fit_the_column_is_refused(db_session):
    """Expected: `ValueError` before anything is written.

    Derived independently: `correction_ids` is `varchar(64)` and part of the primary key. A set
    that overflowed it would either abort mid-run or, worse, label rows with a truncated set --
    a row that says it was scored under corrections it was not. Refusing costs one run.
    """
    with pytest.raises(ValueError):
        rescore(db_session, from_order=1, to_order=2, corrections=[f"C{n}" for n in range(30)])
    with pytest.raises(ValueError):
        rescore(db_session, from_order=1, to_order=2, corrections=[])
    assert _rescore_count(db_session) == 0


def test_the_verdict_vocabulary_is_the_audits(db_session, seeded_world):
    """Expected: the same three verdicts `harness/audit.py` uses, and nothing else written.

    Derived independently from §2's invariant query for `order_rescores`, which asserts
    `verdict not in ('validated','corrected','unverifiable')` returns no row. Two instruments
    answering the same question -- `harness audit-order` for one order, `harness rescore` for a
    range -- have to answer it in one vocabulary or the query is written twice and drifts.
    """
    from harness.audit import VERDICTS as AUDIT_VERDICTS
    from harness.rescore import VERDICTS

    assert VERDICTS == AUDIT_VERDICTS == ("validated", "corrected", "unverifiable")
    rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
            corrections=CORRECTIONS_IN_FORCE)
    written = db_session.execute(text("select distinct verdict from order_rescores")).scalars()
    assert set(written) <= set(VERDICTS)


def test_the_command_prints_the_partition_and_its_caveat_and_never_a_ratio(db_session,
                                                                          seeded_world,
                                                                          monkeypatch):
    """Expected: the four counts, the right-censoring caveat, and no ratio anywhere.

    Derived independently from ruling IM-4: the reportable result is a partition over an
    order-level denominator, so the command has to print the cells and the caveat and must not
    divide one cell by another -- two numbers from different denominators read side by side look
    like a trend and are not one. 27/445 is a pooled key-level count from the defective
    simulator and is not quoted beside these numbers, so it is absent from the output too.
    """
    import os

    from typer.testing import CliRunner

    from harness.cli import app
    from harness.config.settings import get_settings

    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["rescore", "--from-order", str(seeded_world.first),
                                          "--to-order", str(seeded_world.last),
                                          "--correction", "C1,C2,C3,C4,C5"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert ("denominator=4 completed=2 unverifiable_no_tape=1 "
            "unverifiable_read_cancelled=1") in result.output
    assert "right-censored" in result.output and "not a rate" in result.output
    # Review IMP-2: what the table got, beside what the partition says, and never folded in.
    assert "rows: written=8 existing=0" in result.output
    assert "range not exhausted" not in result.output
    assert "27/445" not in result.output
    assert "%" not in result.output and "/4" not in result.output
    # The rows are there, and the originals are not touched by the command either.
    assert _rescore_count(db_session) == 8
    assert db_session.execute(text("select count(*) from fills")).scalar() == 0


def test_a_tape_read_that_hits_its_row_cap_is_unverifiable_rather_than_scored(db_session,
                                                                              monkeypatch):
    """Expected: a read that returns `DELTA_BATCH_LIMIT` rows scores nothing.

    Derived independently: each order's tape read is bounded by `store.DELTA_BATCH_LIMIT`
    (§1.8), and a bound that is reached returns a *prefix* of the window. A fill computed from
    part of the evidence and labelled `validated` or `corrected` would be wrong exactly on the
    tickers with the most tape -- a bias, not noise -- which is the failure T7's population read
    was made to refuse rather than to truncate silently. Counted with the cancelled reads: the
    read's own bound stopped it, the tape was not silent.
    """
    from harness.execution import store

    at = T0 + timedelta(seconds=1)
    _order(db_session, 9107, "KXRESCOREG", "2.00", "3.00")
    _print(db_session, "KXRESCOREG", "g-1", "5.00", at)
    _delta(db_session, "KXRESCOREG", at, "-1.00", 1)
    db_session.commit()
    monkeypatch.setattr(store, "DELTA_BATCH_LIMIT", 1)
    counts = rescore(db_session, from_order=9107, to_order=9107,
                     corrections=CORRECTIONS_IN_FORCE)
    assert (counts.denominator, counts.completed, counts.unverifiable_no_tape,
            counts.unverifiable_read_cancelled) == (1, 0, 0, 1)
    rows = _rows(db_session, policy="ahead")
    assert [(r.verdict, r.watched_filled) for r in rows] == [("unverifiable", None)]
    assert rows[0].unobserved_s == 1800          # review Minor 4, as above


def test_a_run_that_reaches_its_read_limit_says_the_range_was_not_exhausted(db_session,
                                                                            seeded_world,
                                                                            monkeypatch):
    """Expected: `exhausted` false, the last order reached named, and the partition still
    printed (review IMP-1).

    Derived independently from the module's own rule about bounds: a read that returns exactly
    its limit returns a *prefix*, which is why a tape read at `DELTA_BATCH_LIMIT` refuses to
    score. The driving read has the same property one level up -- a partition printed over the
    first N orders of a longer range reads exactly like a partition over the whole of it -- so a
    run that hits the ceiling has to say so, and has to name the order `--resume` continues
    from, or the operator is guessing.
    """
    import os

    from typer.testing import CliRunner

    from harness.cli import app
    from harness.config.settings import get_settings

    counts = rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
                     corrections=CORRECTIONS_IN_FORCE, limit=2)
    assert counts.denominator == 2 and counts.exhausted is False
    assert counts.last_order_id == B
    # And the whole range, which stops short of the limit, is reported as exhausted.
    full = rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
                   corrections=CORRECTIONS_IN_FORCE)
    assert full.denominator == 4 and full.exhausted is True and full.last_order_id is None

    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["rescore", "--from-order", str(seeded_world.first),
                                          "--to-order", str(seeded_world.last),
                                          "--correction", "C1,C2,C3,C4,C5", "--limit", "2"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "range not exhausted" in result.output
    assert f"last order {B}" in result.output
    assert f"--from-order {B}" in result.output
    assert "denominator=2" in result.output          # the partition is still printed


def test_a_rerun_reports_the_rows_it_did_not_write_and_names_a_stale_verdict(db_session,
                                                                             seeded_world,
                                                                             caplog):
    """Expected: `written`/`existing` count the inserts, the pre-existing row survives
    unchanged, and its disagreement with the freshly computed verdict is logged (review IMP-2).

    Derived independently from the no-edit rule: `on conflict do nothing` is what makes a
    correction new rows rather than an edit, so a second run over a range whose tape has since
    been backfilled recomputes every order and keeps every old row. The counts it prints would
    then describe rows that are not in the table, and Task 12's verify rows (which read the
    table) and the journal (which reads the run) would disagree with nothing in the record
    saying why. Counting the inserts and naming the stale row is what makes the divergence
    visible without touching the stored row.
    """
    import logging

    from harness.db.models import OrderRescore

    ids = ",".join(sorted(CORRECTIONS_IN_FORCE))
    # Order A's stored verdict is `corrected`; the repaired simulator says `validated`.
    db_session.add(OrderRescore(order_id=A, correction_ids=ids, cancel_policy="ahead",
                                verdict="corrected", computed_at=T0, build_sha="oldbuild"))
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="harness.rescore"):
        counts = rescore(db_session, from_order=seeded_world.first,
                         to_order=seeded_world.last, corrections=CORRECTIONS_IN_FORCE,
                         build_sha="newbuild")
    # Eight rows for four orders; one of them was already there, so seven were written.
    assert (counts.written, counts.existing) == (7, 1)
    assert counts.written + counts.existing == 2 * counts.denominator
    stored = db_session.execute(text(
        "select verdict, build_sha, watched_filled from order_rescores "
        "where order_id = :i and cancel_policy = 'ahead'"), {"i": A}).one()
    # Never updated: the stored row is the record of what the earlier run computed.
    assert stored == ("corrected", "oldbuild", None)
    divergence = [r.message % r.args for r in caplog.records if "already scored" in r.message]
    assert len(divergence) == 1, divergence
    assert (f"order {A}" in divergence[0] and "ahead=corrected" in divergence[0]
            and "oldbuild" in divergence[0] and "computed validated" in divergence[0])

    # A second full run writes nothing at all and says so, with no stale-verdict line for the
    # three orders whose stored verdict still agrees.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="harness.rescore"):
        again = rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
                        corrections=CORRECTIONS_IN_FORCE, build_sha="newbuild")
    assert (again.written, again.existing) == (0, 8)
    assert len([r for r in caplog.records if "already scored" in r.message]) == 1


# --- T9 review Minors 2 and 5 (the 6B integration round) ----------------------------------


def test_the_elapsed_seconds_are_read_once_per_page_not_twice_per_order(db_session,
                                                                       seeded_world,
                                                                       monkeypatch):
    """Review Minor 2: `order_dirty_time` is a *bounded page* read, so asking it for one order
    at a time costs two statements per order over a 10,000-order range for an answer the same
    two statements give for the whole page.

    Derived independently of the implementation: the driving read returns the page, so the page
    is what the timing read is bounded to. The four orders of the seeded world are one page, so
    one call -- and the per-order values must still be the per-order values, which is what the
    second assertion is for (C and D are the two orders whose timing is written on an
    unverifiable path).
    """
    calls = []
    real = rescore_module.order_dirty_time

    def counted(session, now, boundary_order_id, limit=1000):
        calls.append((boundary_order_id, limit))
        return real(session, now, boundary_order_id, limit)

    monkeypatch.setattr(rescore_module, "order_dirty_time", counted)

    counts = rescore(db_session, from_order=seeded_world.first, to_order=seeded_world.last,
                     corrections=CORRECTIONS_IN_FORCE)

    assert counts.denominator == 4
    assert len(calls) == 1, calls
    timing = {r.order_id: (r.watched_dirty_s, r.counterfactual_dirty_s, r.unobserved_s)
              for r in _rows(db_session, policy="ahead")}
    assert timing == {A: (0, 0, 1800), B: (0, 0, 1800), C: (0, 0, 1800), D: (0, 0, 1800)}


def test_the_replay_reads_the_stores_public_tape_builder(db_session):
    """Review Minor 5: the re-score built its deltas through `store._tape_deltas`, a private
    name in another module. The builder is public (`store.tape_deltas`) and is the one this
    command calls -- it is what drops a row whose side, price or delta is NULL, and a second
    copy of that rule here is exactly the drift the shared helper exists to prevent."""
    from harness.execution import store

    rows = [SimpleNamespace(id=1, ts=T0, side="yes", price=Decimal("0.30"),
                            delta=Decimal("-1.00"), sid=1, seq=1),
            SimpleNamespace(id=2, ts=T0, side=None, price=None, delta=None, sid=1, seq=2)]

    assert hasattr(store, "tape_deltas")
    assert not hasattr(store, "_tape_deltas")
    built = rescore_module._as_deltas(rows)
    assert [d.event_id for d in built] == [1]
    assert built == store.tape_deltas(rows)


def test_the_rescore_command_echoes_through_typer_and_disposes_its_engine(db_session,
                                                                         seeded_world,
                                                                         monkeypatch):
    """Review Minor 5: the command is a Typer command, so its output goes through `typer.echo`
    like every other command's, and the engine it builds for itself is disposed rather than
    left to the interpreter -- the controller runs this command repeatedly over ssh in the
    quiet window beside a live loop, and each run holding a pool open is a connection the
    executor cannot have."""
    import inspect
    import os

    from sqlalchemy.engine import Engine
    from typer.testing import CliRunner

    import harness.cli as cli_module
    from harness.cli import app
    from harness.config.settings import get_settings

    source = inspect.getsource(cli_module.rescore_cmd)
    assert "typer.echo(" in source
    assert "\n    print(" not in source

    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")

    built, disposed = [], []
    real_make_engine = cli_module.make_engine
    real_dispose = Engine.dispose

    def spy(url, *args, **kwargs):
        engine = real_make_engine(url, *args, **kwargs)
        built.append(engine)
        return engine

    def dispose(self, *args, **kwargs):
        disposed.append(self)
        return real_dispose(self, *args, **kwargs)

    monkeypatch.setattr(cli_module, "make_engine", spy)
    monkeypatch.setattr(Engine, "dispose", dispose)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["rescore", "--from-order", str(seeded_world.first),
                                          "--to-order", str(seeded_world.last),
                                          "--correction", "C1,C2,C3,C4,C5"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert "denominator=4" in result.output
    assert built and all(engine in disposed for engine in built)


def test_a_sparse_page_still_carries_every_orders_elapsed_seconds(db_session):
    """Review I-1: the page's ids are not contiguous, so one bounded `order_dirty_time` read of
    `len(rows)` rows can stop short of the page's last id.

    Derived independently of the implementation: the driving read selects `nw_done` orders inside
    an id range (`_ORDERS`), while `order_dirty_time` selects every order with an expiry, so a
    page of {1, 8} drawn from eight orders is answered by a two-row read as {1, 2} -- and order 8
    would silently get NULL timing columns where the per-order read wrote numbers. Eight orders,
    six of them pending, and the two scored rows must carry exactly what the old per-order read
    (`boundary_order_id = id - 1, limit = 1`) says.
    """
    from harness.execution.dirty_time import order_dirty_time

    for order_id in range(1, 9):
        _order(db_session, order_id, f"KXSPARSE{order_id}", "2.00", "3.00")
    db_session.commit()
    db_session.execute(text("update orders set nw_done = false where id between 2 and 7"))
    db_session.commit()
    page = db_session.execute(
        text("select id from orders where nw_done = true order by id")).scalars().all()
    assert page == [1, 8]

    instant = T0 + timedelta(hours=1)
    counts = rescore(db_session, from_order=1, to_order=8, corrections=CORRECTIONS_IN_FORCE,
                     now=instant)
    assert counts.denominator == 2

    written = {r.order_id: (r.watched_dirty_s, r.counterfactual_dirty_s, r.unobserved_s)
               for r in _rows(db_session, policy="ahead")}
    expected = {}
    for order_id in page:
        elapsed = order_dirty_time(db_session, instant, boundary_order_id=order_id - 1, limit=1)
        assert elapsed and elapsed[0].order_id == order_id
        expected[order_id] = (elapsed[0].watched_dirty_s, elapsed[0].counterfactual_dirty_s,
                              elapsed[0].unobserved_s)
    assert written == expected
    assert written == {1: (0, 0, 1800), 8: (0, 0, 1800)}
