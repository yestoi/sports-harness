"""§1.3(f): what the comparison may claim, and what it must call incomplete."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness.experiments.execution_viability.baseline import (
    MISMATCH_KINDS, Mismatch, compare_actions, recorded_actions, render_baseline,
)
from harness.experiments.execution_viability.capture import Limitation

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 16, 17, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=15)


def _lim(kind="loop_spacing_unreconstructable") -> Limitation:
    return Limitation(run_id="r", kind=kind, scope={"slice": "all"}, detail=f"{kind} detail",
                      created_at=NOW)


def _action(kind="place", *, instant=NOW, vm=1, side="yes", **over) -> dict:
    row = {"kind": kind, "instant": instant, "venue_market_id": vm, "side": side,
           "prob": Decimal("0.4800"), "contracts": Decimal("20"),
           "deadline": instant + timedelta(seconds=220)}
    row.update(over)
    return row


def test_a_slice_with_missing_input_history_reads_incomplete_not_parity():
    out = render_baseline([], instants=0, live_estimate=3575,
                          limitations=[_lim("availability_unreconstructable")])
    assert "incomplete/unverifiable" in out and "parity" not in out.replace("no parity", "")


def test_the_report_prints_the_resolved_count_beside_the_live_loop_estimate():
    out = render_baseline([], instants=842, live_estimate=3575, limitations=[_lim()])
    assert "842" in out and "3575" in out
    assert "retained action instants" in out


def test_the_pass_condition_is_zero_unexplained_mismatches_not_zero_mismatches():
    explained = [Mismatch(run_id="r", arm_id="A", instant=NOW, venue_market_id=1,
                          kind="price", expected={"prob": "0.48"}, actual={"prob": "0.47"},
                          cause="kickoff_not_asof", explained=True)]
    assert "0 unexplained" in render_baseline(explained, instants=842, live_estimate=3575,
                                              limitations=[])


def test_an_explained_mismatch_must_name_its_cause():
    # §2's invariant query: `explained and cause is null` must return no rows, so the record
    # refuses to be built that way rather than being caught after it is written.
    with pytest.raises(ValueError):
        Mismatch(run_id="r", arm_id="A", instant=NOW, venue_market_id=1, kind="price",
                 expected={}, actual={}, cause=None, explained=True)
    with pytest.raises(ValueError):
        Mismatch(run_id="r", arm_id="A", instant=NOW, venue_market_id=1, kind="invented",
                 expected={}, actual={}, cause=None, explained=False)


def test_identical_lists_produce_no_mismatch():
    recorded = [_action(), _action("cancel", instant=NOW + STEP)]
    assert compare_actions(recorded, list(recorded), run_id="r", arm_id="A") == []


def test_a_different_price_at_the_same_instant_is_a_price_mismatch():
    recorded = [_action()]
    produced = [_action(prob=Decimal("0.4700"))]
    [mismatch] = compare_actions(recorded, produced, run_id="r", arm_id="A")
    assert mismatch.kind == "price" and mismatch.expected == {"prob": "0.4800"}
    assert mismatch.actual == {"prob": "0.4700"} and mismatch.explained is False
    assert mismatch.kind in MISMATCH_KINDS


def test_a_different_size_at_the_same_instant_is_a_capacity_mismatch():
    [mismatch] = compare_actions([_action()], [_action(contracts=Decimal("5"))],
                                 run_id="r", arm_id="A")
    assert mismatch.kind == "capacity"


def test_a_cancel_at_another_instant_is_one_cancel_instant_mismatch_not_two_absences():
    recorded = [_action("cancel", instant=NOW + STEP)]
    produced = [_action("cancel", instant=NOW + 3 * STEP)]
    [mismatch] = compare_actions(recorded, produced, run_id="r", arm_id="A")
    assert mismatch.kind == "cancel_instant"
    assert mismatch.expected == {"instant": (NOW + STEP).isoformat()}
    assert mismatch.actual == {"instant": (NOW + 3 * STEP).isoformat()}


def test_an_action_the_arm_never_took_is_an_action_mismatch():
    [mismatch] = compare_actions([_action()], [], run_id="r", arm_id="A")
    assert mismatch.kind == "action" and mismatch.actual == {}
    [extra] = compare_actions([], [_action()], run_id="r", arm_id="A")
    assert extra.kind == "action" and extra.expected == {}


def test_a_skip_is_not_part_of_the_action_comparison():
    # A skip is a decision *not* to act: it has no instant on the tape to compare against, and
    # counting it as an unpaired action would fail every slice.
    produced = [_action(), {"kind": "skip", "instant": NOW, "reason": "kickoff"}]
    assert compare_actions([_action()], produced, run_id="r", arm_id="A") == []


def test_the_recorded_side_is_read_from_the_orders_and_their_own_events(db_session):
    """§1.3(f)'s recorded slice: the two bounded reads, in the adapter's action shape."""
    from tests.test_exp_adapter import _game, _intent, _market, _order, _priced

    game = _game(db_session)
    market = _market(db_session, game_id=game.id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    intent = _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    order = _order(db_session, intent, market, placed_at=NOW)
    from harness.db.models import OrderEvent

    db_session.add(OrderEvent(order_id=order.id, ts=NOW + STEP, kind="cancel", reason="reprice",
                              prob=order.prob, contracts=order.contracts, replay=False))
    db_session.commit()
    rows = recorded_actions(db_session, warmup_start=NOW - timedelta(hours=1),
                            observation_end=NOW + timedelta(hours=1), variant_ids=["v_base"])
    assert [(row["kind"], row["instant"]) for row in rows] == [("place", NOW),
                                                               ("cancel", NOW + STEP)]
    assert rows[0]["venue_market_id"] == market.id and rows[0]["prob"] == Decimal("0.4800")


def test_the_arm_reproduces_the_recorded_cancel_and_its_replacement_on_the_fixture(db_session,
                                                                                   env_settings):
    """The §1.3 reproduction, written out from the fixture's own stamps.

    The tape holds: a place at 12:00, a `reprice` cancel of it at 12:00:15 and the replacement
    placed at 12:00:15. The arm **adopts** the first order rather than placing it -- it was
    placed before the slice's first instant -- so the comparison must show exactly one unpaired
    recorded action, the 12:00 place, and nothing else: the cancel and the replacement pair.
    """
    from harness.experiments.execution_viability.adapter import ArmRunner
    from tests.test_exp_adapter import (RUN, VARIANT_CFG, _game, _intent, _market, _order,
                                        _priced, _rest_book)

    game = _game(db_session)
    market = _market(db_session, game_id=game.id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _priced(db_session, market, at=NOW + STEP - timedelta(seconds=1))
    _rest_book(db_session, market, fetched_at=NOW - timedelta(seconds=10))
    intent = _intent(db_session, market, created_at=NOW - timedelta(minutes=5), signal_id=1)
    # The recorded first order is already cancelled on the tape: `uq_open_order` allows one
    # open order per (venue, ticker, side, variant), which is exactly why a reprice is a cancel
    # followed by a placement rather than two open orders.
    order = _order(db_session, intent, market, placed_at=NOW, status="cancelled",
                   expiry=NOW + timedelta(seconds=600))
    order.cancelled_at = NOW + STEP
    order.cancel_reason = "reprice"
    db_session.flush()
    _intent(db_session, market, created_at=NOW + STEP, target_prob="0.5000", signal_id=2)
    # The replacement's own expiry is the executor's R8 rule -- kickoff minus
    # `exec_kickoff_cutoff_min` (10 min) -- which is the deadline `plan_actions` gives a
    # placement, so the two sides are stating the same quantity.
    replacement = _order(db_session, intent, market, placed_at=NOW + STEP, prob="0.5000",
                         expiry=KICKOFF - timedelta(minutes=10), n=2)
    from harness.db.models import OrderEvent

    db_session.add(OrderEvent(order_id=order.id, ts=NOW + STEP, kind="cancel", reason="reprice",
                              prob=order.prob, contracts=order.contracts, replay=False))
    db_session.commit()
    runner = ArmRunner(run_id=RUN, arm_id="A", policy=None, variant_cfg=VARIANT_CFG,
                       exec_settings=env_settings, walkers={})
    runner.adopt(order, queue_ahead=Decimal("0"))
    produced = [action for result in runner.run(db_session, [NOW, NOW + STEP])
                for action in result.actions]
    recorded = recorded_actions(db_session, warmup_start=NOW - timedelta(hours=1),
                                observation_end=NOW + timedelta(hours=1),
                                variant_ids=["v_base"])
    mismatches = compare_actions(recorded, produced, run_id=RUN, arm_id="A")
    assert [(m.kind, m.instant) for m in mismatches] == [("action", NOW)]
    assert mismatches[0].expected["kind"] == "place" and mismatches[0].actual == {}
    assert replacement.id != order.id
    out = render_baseline(mismatches, instants=2, live_estimate=3575, limitations=[])
    assert "1 total, 1 unexplained" in out and "verdict: fail" in out
