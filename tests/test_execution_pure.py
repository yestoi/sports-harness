"""The executor's fill decision as pure objects: no database, no pipeline, one fixed clock.

`tests/test_exec_loop.py` drives a real `Executor` against a seeded database, which is the right
shape for the loop's own wiring and the wrong shape for the arithmetic. These cases construct
`Executor` through `__new__` with only the attributes `_simulate_order` reads and replace
`_persist_track` with a capture, exactly as `tests/test_execution_regressions.py` does for the
6A probes -- so what is asserted is the simulator's own answer, not what a row in a table ended
up holding.

Owned by 6B Task 4 (recovery anchoring), then Task 5 (the expiry clamp), then Task 6 (the dirty
scope and the backoff), in that order.
"""

from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch

from harness.execution.book import DELTA_LOOKBACK, BookState
from harness.execution.fills import TapeDelta, TapePrint
from harness.execution.loop import ExecStats, Executor, _TrackResult
from harness.execution.state import _state_columns
from tests.test_fills import DEADLINE, T0, at

#: The subscription every book and frame in this file belongs to.
SID = 7


def _executor(books: dict) -> tuple[Executor, list]:
    """An `Executor` with only what `_simulate_order` reads, and the track results it produced.

    `Executor.__init__` builds a runtime, a gateway and a session factory; none of them is part
    of the fill decision. `_persist_track` is replaced with a capture, so no row is written and
    the simulator's own result is what the case asserts on.
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
    """One `orders` row as `_simulate_order` reads it: attribute access only, no ORM.

    The same shape `tests/test_execution_regressions.py`'s `_order_row` carries after Task 3
    reworked it, repeated here rather than imported: a test module that reaches into another
    module's private helper breaks the moment either file's owner changes it, and these two files
    have different owners.

    `nw_next_attempt_at` and `nw_attempts` are in the row from the start even though Task 6 is
    what reads them: a helper extended twice by two tasks is a helper whose second owner
    rewrites the first owner's line.
    """
    row = NS(id=1, venue_market_id=1, ticker="A", side="yes", prob=D(".30"),
             contracts=D(10), placed_at=T0, expiry=DEADLINE, cancelled_at=None,
             queue_ahead_at_place=D(5), queue_remaining=D(5), traded_at_price=D(0),
             filled_contracts=D(0), tape_cursor_event_id=1, crossed=False,
             last_print_ts=T0, last_print_ids=(),
             cancels_ahead=D(0), recon_state=None,
             nw_queue_remaining=D(5),
             nw_traded_at_price=D(0), nw_filled_contracts=D(0),
             nw_tape_cursor_event_id=1, nw_crossed=False, nw_last_print_ts=T0,
             nw_last_print_ids=(),
             nw_cancels_ahead=D(0), nw_recon_state=None,
             nw_done=True, nw_next_attempt_at=None, nw_attempts=0, status="open")
    for key, value in over.items():
        setattr(row, key, value)
    return row


#: A market that is never dirty, and one that always is. `_simulate_order` calls
#: `market.dirty(now, exec_settings)` and nothing else on it.
CLEAN_MARKET = {1: NS(dirty=lambda *unused: False)}
DIRTY_MARKET = {1: NS(dirty=lambda *unused: True)}


def test_a_recovery_anchors_the_print_floor_with_the_queue():
    """Expected fill 0, queue 2, `print_floor` at the anchor instant less the slack.

    Derived from the tape, not from the code: five contracts rest ahead of us before the gap.
    One trade of three happens inside it. The recovery snapshot is taken after that trade and
    shows two resting -- which is the same five minus the same three, already accounted for.
    Applying the gap's trade again would take the queue to -1 and pay us one contract we were
    never in line for. So: fill 0, queue 2, and the print floor is the anchoring book's own
    instant less `DELTA_LOOKBACK`, which is the slack between the recorder's receive clock and
    the venue's trade clock.
    """
    recovered = BookState.from_levels("A", [[".30", "2"]], [[".60", "5"]], sid=SID, seq=3,
                                      as_of=at(20), source="ws", anchor_id=3)
    trade = TapePrint("during-gap", at(10), D(".30"), D(3), "no", "ws")
    delta = TapeDelta(2, at(10), "yes", D(".30"), D(-3), SID, 2)
    executor, captured = _executor({"A": recovered})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, _order_row(), CLEAN_MARKET, {}, {"A"},
                                 {"A": ([trade], [delta])}, set(), at(30), ExecStats())
    state = captured[0].state
    assert state.filled_contracts == D(0)
    assert state.queue_remaining == D(2)
    assert state.print_floor == at(20) - DELTA_LOOKBACK


def test_a_recovery_never_lengthens_the_queue():
    """Expected queue 5, not 9 (the `min()` clamp, review I-2).

    Derived independently: our track believes five rest ahead of us and the recovery snapshot
    shows nine at our price. The four extra joined the level while the book was dirty, and under
    price-time priority a contract that arrives after ours sits behind ours. Taking the queue up
    to nine would charge us for liquidity that is not ahead of us, so the clamp keeps five.
    """
    recovered = BookState.from_levels("A", [[".30", "9"]], [[".60", "5"]], sid=SID, seq=3,
                                      as_of=at(20), source="ws", anchor_id=3)
    executor, captured = _executor({"A": recovered})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, _order_row(), CLEAN_MARKET, {}, {"A"},
                                 {"A": ([], [])}, set(), at(30), ExecStats())
    assert captured[0].state.queue_remaining == D(5)


def test_a_late_rest_print_above_the_floor_is_applied_exactly_once():
    """Expected: fill 3 after the first feed, and still fill 3 after the same print is re-fed.

    Derived independently: the executor keeps no print cursor and rescans prints from
    `placed_at - 60 s` every loop, so every print is offered again on the next loop. A print
    stamped *above* the anchor floor is a real trade the anchored queue has not yet seen: the
    first feed must apply it, and the trade-id set is what stops the second feed applying it
    again. A timestamp watermark alone could not do both jobs, which is why §0.6 separates them.
    """
    recovered = BookState.from_levels("A", [[".30", "0"]], [[".60", "5"]], sid=SID, seq=3,
                                      as_of=at(20), source="ws", anchor_id=3)
    late = TapePrint("after-anchor", at(25), D(".30"), D(3), "no", "ws")
    executor, captured = _executor({"A": recovered})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, _order_row(), CLEAN_MARKET, {}, {"A"},
                                 {"A": ([late], [])}, set(), at(30), ExecStats())
    first = captured[0].state
    assert first.filled_contracts == D(3)

    # The second loop: the same print, re-read, against the state the first loop persisted. The
    # ticker is no longer recovering, so the cached book is handed in as the step's base at the
    # cursor the first loop wrote -- which is what `_sim_book` reads when no branch anchored,
    # and the only way to ask this question without a session to look an event timestamp up in.
    row = _order_row(**_state_columns("", first), filled_contracts=first.filled_contracts)
    executor, captured = _executor({"A": recovered})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, row, CLEAN_MARKET, {"A": recovered}, set(),
                                 {"A": ([late], [])}, set(), at(40), ExecStats())
    assert captured[0].state.filled_contracts == D(3)
