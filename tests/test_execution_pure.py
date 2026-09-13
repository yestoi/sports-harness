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

from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch

from harness.execution import store
from harness.execution.book import DELTA_LOOKBACK, BookState
from harness.execution.fills import TapeDelta, TapePrint
from harness.execution.loop import ExecStats, Executor, _TrackResult, _clamped
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
    # `uses_the_simulator` (`harness/execution/gateway.py:851-858`) reads
    # `getattr(gateway, "simulates_fills", None)` and raises `TypeError` unless it is a `bool`:
    # the contract is declared, never inferred, so one attribute is the whole gateway a pure
    # case needs. `_simulate` asks it before it reads a single order.
    executor.gateway = NS(simulates_fills=True)

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


def test_the_watched_track_fills_a_print_stamped_exactly_at_the_expiry():
    """Expected fill 4: a print stamped exactly at the expiry is still within the deadline.

    Review round 1, minor 3: the plan's own case here was a line-for-line duplicate of
    `tests/test_execution_regressions.py::test_the_watched_track_takes_no_fill_after_expiry`.
    This is the boundary that case does not reach. `_merge_events` bounds each event by
    `... <= deadline` (`fills.py:430,433`), an inclusive test, and `_order_action` expires an
    order only once `now >= expiry` -- the expiry instant is the last one the order is still
    resting. A print stamped at that exact instant is therefore one this order could still have
    traded against.
    """
    base = BookState.from_levels("A", [[".30", "0"]], [[".60", "5"]], sid=SID, seq=1,
                                 as_of=at(0), source="ws", anchor_id=1)
    on_time = TapePrint("at-expiry", at(20), D(".30"), D(4), "no", "ws")
    row = _order_row(expiry=at(20), queue_ahead_at_place=D(0), queue_remaining=D(0),
                     nw_done=True)
    executor, captured = _executor({"A": base})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, row, CLEAN_MARKET, {"A": base}, set(),
                                 {"A": ([on_time], [])}, set(), at(30), ExecStats())
    assert captured[0].state.filled_contracts == D(4)


# --- Task 6 (C5): the dirty scope, the clamp and the counterfactual backoff -------------------

def test_a_cancelled_order_accrues_on_the_counterfactual_column_only():
    """Expected: zero watched calls, `exec_period_s` added to `nw_dirty_seconds`.

    Derived independently: `dirty_minutes` is a property of the watched order -- how long the
    order we placed sat against a book we could not read. A cancelled order is not sitting
    against anything; it left the market when it was cancelled. The 15 s belongs to the
    counterfactual, which is still running, and it has a column of its own for exactly this
    reason. Order 157 reached 181,200 seconds on a 35-minute order because the accrual happened
    before any status test.
    """
    row = _order_row(status="cancelled", nw_done=False)
    executor, _ = _executor({})
    with patch("harness.execution.store.add_dirty_seconds") as add_dirty:
        executor._simulate_order(None, row, DIRTY_MARKET, {}, set(), {}, set(),
                                 at(30), ExecStats())
    watched = [c for c in add_dirty.call_args_list if c.kwargs.get("watched") is True]
    counterfactual = [c for c in add_dirty.call_args_list if c.kwargs.get("watched") is False]
    assert watched == []
    assert [c.args[2] for c in counterfactual] == [15]


def test_the_watched_accrual_is_clamped_to_the_resting_interval():
    """Expected: 5 s added, not 15, for an order with 5 s of resting interval left.

    Derived independently: the accrual is a count of observation opportunities while the order
    rested, so it cannot exceed the interval the order rested for. An order placed at T0 with an
    expiry of T0+10 s, observed dirty at T0+5 s under a 15 s period, has 5 s of interval left,
    not 15. Without the clamp §3 row 5's first clause -- `dirty_seconds` never exceeding
    `coalesce(cancelled_at, expiry) - placed_at` -- would be an assertion about luck.
    `_order_action` deliberately holds `Expire` on a lagging ticker, so a past-expiry order can
    stay `open` and would otherwise accrue for as long as the ticker lags.
    """
    row = _order_row(status="open", expiry=at(10), nw_done=True)
    executor, _ = _executor({})
    with patch("harness.execution.store.add_dirty_seconds") as add_dirty:
        executor._simulate_order(None, row, DIRTY_MARKET, {}, set(), {}, set(),
                                 at(5), ExecStats())
    assert [c.args[2] for c in add_dirty.call_args_list] == [5]


def test_an_order_with_no_expiry_accrues_the_whole_period_on_both_tracks():
    """Expected 15 s on each track for a row whose `expiry` is NULL.

    Derived independently from `orders.expiry` being nullable in the model: there is no
    interval to clamp to, so the clamp has nothing to say and the observation counts in full.
    Guessing a bound would be worse than not having one (R8 makes the case rare), and the
    alternative -- treating a missing expiry as a zero-length interval -- would silently stop
    recording dirty time for exactly the orders whose end nobody wrote down. The whole-branch
    list records that no other test on either track reaches `row.expiry is None`.
    """
    row = _order_row(status="open", expiry=None, cancelled_at=None, nw_done=False)
    executor, _ = _executor({})
    with patch("harness.execution.store.add_dirty_seconds") as add_dirty:
        executor._simulate_order(None, row, DIRTY_MARKET, {}, set(), {}, set(),
                                 at(30), ExecStats())
    assert [c.args[2] for c in add_dirty.call_args_list] == [15, 15]
    assert _clamped(15, row, at(30), watched=True) == 15


def test_a_failed_counterfactual_read_backs_off_and_is_never_closed():
    """Expected next-attempt delays of 15, 30 and 60 s, a cap at `NW_RETRY_MAX_S`, `nw_done`
    still False, and `nw_attempts` back to 0 on the first complete read.

    Derived independently: `exec_period_s` is 15 s and the delay doubles per failed read, so
    the first three are 15, 30 and 60. The cap is 3600 elapsed seconds, and the bound is elapsed
    wall time rather than a loop count because the executor ran 27 loops in an hour on the
    sampled evening, where a loop-counted bound would stretch by an order of magnitude in
    exactly the conditions it exists for (ruling CR-5). Nothing is ever closed: a closed track
    would take its order out of gate criterion 4's population, which `_MARKOUTS` builds from the
    `nw_fill` anchor with no fill predicate, and 834 of the 836 non-cross fills on the record are
    no-watcher fills (ruling CR-4).
    """
    delays = []
    row = _order_row(status="cancelled", nw_done=False, nw_attempts=0, nw_next_attempt_at=None)
    executor, _ = _executor({})
    for attempt in range(3):
        nxt = executor._nw_backoff(row.nw_attempts, at(attempt * 100))
        delays.append(int((nxt - at(attempt * 100)).total_seconds()))
        row = _order_row(status="cancelled", nw_done=False, nw_attempts=row.nw_attempts + 1,
                         nw_next_attempt_at=nxt)
    assert delays == [15, 30, 60]
    assert executor._nw_backoff(99, at(0)) == at(0) + timedelta(seconds=store.NW_RETRY_MAX_S)
    assert row.nw_done is False


def test_a_deferred_ticker_is_not_simulated_and_its_track_stays_open():
    """Expected: `nw_done` still False and the cursors unmoved, for a past-expiry track whose
    ticker was deferred by the backoff (ruling CR-2).

    Derived independently from the closing rule, not from the code. `_simulate_order` closes a
    counterfactual when `row.ticker not in lagging` and the expiry has passed. A ticker skipped
    by the backoff is read by nobody that step, so it is in neither `unread` nor `lagging` --
    and a past-expiry track would therefore be closed on a loop that read none of its tape.
    Before the backoff a failed read put the ticker in `unread` and `_simulate` skipped the row,
    which is exactly why the track survived. Closing it is the abandonment ruling CR-4 forbids:
    it removes the order from gate criterion 4's population, non-randomly and on the
    worst-taped tickers.

    So `_tape` reports the deferred set separately, `_simulate` skips those rows, and the track
    comes out of the step exactly as it went in.
    """
    row = _order_row(status="cancelled", nw_done=False, expiry=at(10),
                     nw_tape_cursor_event_id=41, nw_attempts=2,
                     nw_next_attempt_at=at(900))
    executor, captured = _executor({})
    with patch.object(Executor, "_tape",
                      return_value=({}, set(), set(), {"A"})):
        outcomes = executor._simulate(None, [row], CLEAN_MARKET, {}, set(), at(30),
                                      ExecStats(), {"tape_lag": [], "last_error": None})
    assert captured == []
    assert row.nw_done is False
    assert row.nw_tape_cursor_event_id == 41
    assert outcomes[row.id] == (row.status, row.filled_contracts)
