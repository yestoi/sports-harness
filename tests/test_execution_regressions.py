"""Phase 6A: the execution-reconciliation probes as runnable regressions (design addendum §0.5).

Each defect is one `xfail(strict=True, raises=AssertionError)` case whose docstring states the
expected value and how it was computed, independently of the code under test. `raises` is not
decoration: without it a changed signature or a failed import would be swallowed as an expected
failure and the defect would look documented when nothing ran (review I-d). 6B removes a marker
when it repairs the defect; a strict XPASS is a hard failure, which is how the suite notices.

Beside them sit passing guards -- behaviours a repair must not break. Case 1b is the guard for
case 1a: a repair that simply stops detecting gaps would turn 1a green and 1b red.

No database. Every case is either a pure object, the real `_simulate_order` with persistence
mocked (as the probe runs it), the real `plan_actions`, or the real `fill_events` over a fake
session. One fixed, tz-aware clock throughout: `tests.test_fills.T0`.
"""

from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from harness.execution.book import BookState
from harness.execution.fills import TapeDelta, TapePrint
from harness.execution.loop import ExecStats, Executor, _TrackResult
from harness.execution.plan import Place, plan_actions
from harness.recorder.ws_sink import WsSink
from harness.report.gate import CRITERIA, fill_events
from tests.test_exec_plan import KICKOFF, NOW, S, cfg, intent, market
from tests.test_fills import DEADLINE, T0, at, order, run, tdelta, tprint

#: The subscription every case 1 book and frame belongs to.
SID = 7


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="6B: per-market streams legitimately skip subscription sequence "
                          "numbers; the per-ticker seq check reads that as a lost frame")
def test_multiplexed_subscription_sequence_does_not_dirty_the_book():
    """Probe `multiplexed_sequence`. Expected `dirty` is False.

    Computed independently of the code: subscription 7 carried frames 1, 2 and 3 with nothing
    missing; frame 2 was ticker B's. Ticker A therefore received every frame addressed to it,
    its ladders are the anchor plus its own delta, and no level of A is stale. A book is dirty
    only when a message that would have changed it was lost, and none was. The captured probe
    output is `actual_dirty true`.
    """
    a = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=SID, seq=1,
                              as_of=at(0), source="ws", anchor_id=1)
    # Frame 2 went to ticker B on the same subscription; A's next frame is 3.
    a.apply_delta("yes", D(".30"), D(1), seq=3, ts=at(1), event_id=3)
    assert a.dirty is False


def _sink() -> tuple[WsSink, list]:
    """A `WsSink` with only the attributes `_check_seq` touches, and the rows it would add.

    `WsSink.__init__` opens a session factory and a telemetry accumulator, neither of which
    `_check_seq` reads; constructing through `__new__` keeps the case to the one method under
    test (the same bypass the probe uses for `Executor`).
    """
    added: list = []
    sink = WsSink.__new__(WsSink)
    sink._last_seq = {}
    sink._session = NS(add=added.append)
    sink._pending = 0
    sink._pending_sids = set()
    sink.gap_sids = set()
    sink._gaps_since = 0
    return sink, added


def test_a_real_missing_subscription_frame_still_writes_a_gap_row():
    """The guard for case 1a (review I-c1). Expected gap rows: 0 for the complete stream, 1 for
    the incomplete one.

    Computed independently: `_check_seq` remembers the last seq per subscription, so a complete
    stream 1, 2, 3 -- whichever tickers those frames addressed -- never skips a number and
    records nothing. A stream 1, 3 with no frame 2 anywhere on the subscription skipped one, and
    exactly one gap row is due, carrying `sid` and the exposing ticker. 6B may not make case 1a
    green by weakening this.
    """
    complete, rows = _sink()
    complete._check_seq(SID, 1, "A", at(0))
    complete._check_seq(SID, 2, "B", at(1))
    complete._check_seq(SID, 3, "A", at(2))
    assert rows == []

    lossy, rows = _sink()
    lossy._check_seq(SID, 1, "A", at(0))
    lossy._check_seq(SID, 3, "A", at(2))
    assert len(rows) == 1
    assert rows[0].kind == "gap" and rows[0].sid == SID
    assert rows[0].raw["expected"] == 2 and rows[0].raw["got"] == 3


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="6B: a print and the delta that records it are one event; counting "
                          "both drains the queue twice")
def test_a_print_and_its_own_delta_are_one_event():
    """Probe `same_event`. Expected queue 2 and fill 0.

    Computed independently: 5 contracts rest ahead of ours at 0.30. One real trade of 3 lifts
    3 of them, leaving 2 ahead and nothing for us -- our order is still behind a queue. The
    book delta of -3 at the same timestamp and price is the exchange reporting that same trade,
    not a second removal: a cancellation and a trade cannot both be the whole of a -3 that a
    print of 3 already explains. So the queue moves once, by 3, to 2, and we fill 0. The probe
    captured `actual_queue 0.00` and `actual_fill 1.00`, i.e. the 3 was applied twice and the
    overflow crossed into our own order.
    """
    result = run(order(queue="5"),
                 prints=[tprint(1, ".30", "3")],
                 deltas=[tdelta(1, "yes", ".30", "-3")])
    assert result.state.queue_remaining == D(2)
    assert result.state.filled_contracts == D(0)


def _executor(books: dict) -> tuple[Executor, list]:
    """An `Executor` with only what `_simulate_order` reads, and the track results it produced.

    `Executor.__init__` builds a runtime, a gateway and a session factory; none of them is part
    of the fill decision. `_persist_track` is replaced with a capture, exactly as the probe does
    it, so no row is written and the simulator's own result is what the case asserts on.
    """
    captured: list = []
    executor = Executor.__new__(Executor)
    executor.exec_settings = NS()
    executor.settings = NS(exec_period_s=15)
    executor.books = books

    def persist(session, order_row, order_obj, result, prints, ledger, crossed_already):
        captured.append(result)
        return _TrackResult(result.state, result.state.filled_contracts,
                            len(result.fills), result.crossed)

    executor._persist_track = persist
    return executor, captured


def _order_row(**over):
    """One `orders` row as `_simulate_order` reads it: attribute access only, no ORM."""
    row = NS(id=1, venue_market_id=1, ticker="A", side="yes", prob=D(".30"),
             contracts=D(10), placed_at=T0, expiry=DEADLINE,
             queue_ahead_at_place=D(5), queue_remaining=D(5), traded_at_price=D(0),
             filled_contracts=D(0), tape_cursor_event_id=1, crossed=False,
             last_print_ts=T0, last_print_ids=(), nw_queue_remaining=D(5),
             nw_traded_at_price=D(0), nw_filled_contracts=D(0),
             nw_tape_cursor_event_id=1, nw_crossed=False, nw_last_print_ts=T0,
             nw_last_print_ids=(), nw_done=True, status="open")
    for key, value in over.items():
        setattr(row, key, value)
    return row


#: A market that is never dirty, and one that always is. `_simulate_order` calls
#: `market.dirty(now, exec_settings)` and nothing else on it.
CLEAN_MARKET = {1: NS(dirty=lambda *unused: False)}
DIRTY_MARKET = {1: NS(dirty=lambda *unused: True)}


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="6B: after a recovery the print watermark is stale while the delta "
                          "cursor has advanced, so a trade from inside the gap fills against "
                          "the post-gap queue")
def test_recovery_takes_no_fill_from_a_trade_inside_the_gap():
    """Probe `actual_recovery_branch`. Expected fill 0.

    Computed independently: before the gap, 5 rest ahead of us. During the gap one trade of 3
    happens. The recovery snapshot is taken after that trade and shows 2 resting ahead -- which
    is the same 5 minus the same 3, already accounted for. Applying the gap's trade to the
    recovered queue would subtract those 3 a second time, taking 2 to -1 and paying us 1
    contract we were never in line for. The recovery snapshot is the queue; the trade that
    produced it is spent. The probe captured `actual_fill 1.00` with the print watermark left
    at the trade's own timestamp while the delta cursor had moved to the recovery anchor.
    """
    recovered = BookState.from_levels("A", [[".30", "2"]], [[".60", "5"]], sid=SID, seq=3,
                                      as_of=at(20), source="ws", anchor_id=3)
    trade = TapePrint("during-gap", at(10), D(".30"), D(3), "no", "ws")
    delta = TapeDelta(2, at(10), "yes", D(".30"), D(-3), SID, 2)
    executor, captured = _executor({"A": recovered})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, _order_row(), CLEAN_MARKET, {}, {"A"},
                                 {"A": ([trade], [delta])}, set(), at(30), ExecStats())
    assert captured[0].state.filled_contracts == D(0)
