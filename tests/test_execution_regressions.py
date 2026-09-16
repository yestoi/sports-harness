"""Phase 6A: the execution-reconciliation probes as runnable regressions (design addendum §0.5).

Each defect arrived as one `xfail(strict=True, raises=AssertionError)` case whose docstring
states the expected value and how it was computed, independently of the code under test.
`raises` was not decoration: without it a changed signature or a failed import would have been
swallowed as an expected failure and the defect would have looked documented when nothing ran
(review I-d). 6B removes a marker when it repairs the defect; a strict XPASS is a hard failure,
which is how the suite notices. **Every marker in this file is now gone** -- Task 6 took the
last one, case 4 -- so §3 row 7's "0 xfailed, 0 XPASS from this file" holds for the rest of the
season and each case below is a plain assertion about repaired behaviour.

Beside them sit passing guards -- behaviours a repair must not break. Case 1b exercises
`WsSink._check_seq`, not `BookState.apply_delta` where case 1a's defect lives; it guards the
subscription-level input a sid-level replacement for the per-book check would depend on, and it
does not itself flip when 1a does. The naive repair -- deleting the per-object dirty flag with
nothing at the subscription level to replace it -- is instead caught by `tests/test_book.py`'s
subscription-level cases turning red, not by 1b:
`test_rest_anchor_dirties_on_a_sid_zero_gap_after_its_tape_position` and
`test_a_book_rebuilt_at_a_cursor_carries_its_subscriptions_gap_verdict`. C1 retired the
per-ticker `test_seq_gap_marks_dirty` this paragraph used to name, exactly as case 1a's docstring
below required.

No database. Every case is either a pure object, the real `_simulate_order` with persistence
mocked (as the probe runs it), the real `plan_actions`, or the real `fill_events` over a fake
session. One fixed, tz-aware clock throughout: `tests.test_fills.T0`.
"""

from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch

from harness.execution.book import BookState
from harness.execution.fills import TapeDelta, TapePrint
from harness.execution.loop import ExecStats, Executor, _TrackResult
from harness.execution.plan import Place, plan_actions
from harness.recorder.ws_sink import SEQ_ADVANCE_TTL_S, WsSink
from harness.report.gate import CRITERIA, fill_events
from tests.test_exec_plan import NOW, S, cfg, intent, market
from tests.test_fills import DEADLINE, T0, at, order, run, tdelta, tprint

#: The subscription every case 1 book and frame belongs to.
SID = 7


def test_multiplexed_subscription_sequence_does_not_dirty_the_book():
    """Probe `multiplexed_sequence`. Expected `dirty` is False.

    Computed independently of the code: subscription 7 carried frames 1, 2 and 3 with nothing
    missing; frame 2 was ticker B's. Ticker A therefore received every frame addressed to it,
    its ladders are the anchor plus its own delta, and no level of A is stale. A book is dirty
    only when a message that would have changed it was lost, and none was. The captured probe
    output is `actual_dirty true`.

    `tests/test_book.py`'s `test_seq_gap_marks_dirty` pinned the opposite outcome on the same
    method and the same call shape -- `apply_delta` with a real per-ticker seq gap, asserting
    `dirty is True` -- so 6B could not unmark this case by deleting the per-object check in
    `harness/execution/book.py` alone. C1 did what this paragraph required instead: gap
    detection moved to the subscription level (where `_check_seq` already lives), the per-object
    check left `book.py`, and that test was retired with it. The subscription-level guards that
    now hold the line are `test_rest_anchor_dirties_on_a_sid_zero_gap_after_its_tape_position`
    and `test_a_book_rebuilt_at_a_cursor_carries_its_subscriptions_gap_verdict`.
    """
    a = BookState.from_levels("A", [[".30", "5"]], [[".60", "5"]], sid=SID, seq=1,
                              as_of=at(0), source="ws", anchor_id=1)
    # Frame 2 went to ticker B on the same subscription; A's next frame is 3.
    a.apply_delta("yes", D(".30"), D(1), seq=3, ts=at(1), event_id=3)
    assert a.dirty is False


def _sink() -> tuple[WsSink, list]:
    """A `WsSink` with only the attributes `_check_seq` and its helpers touch, and the rows it
    would add: `_last_seq`, `_session`, `_pending`, `_pending_sids`, `gap_sids`, `_gaps_since`,
    and, since fix 77, the seq-advance allowance `_check_seq` consults -- `_advances`,
    `_advance_ttl_s`, `_advances_since` and `_acks_out_since` -- each initialised exactly as
    `__init__` does.

    `WsSink.__init__` opens a session factory and a telemetry accumulator, neither of which
    `_check_seq` reads; constructing through `__new__` keeps the case to the one method under
    test (the same bypass the probe uses for `Executor`). The cost is that this helper has to
    track what that method reads: when fix 77 taught `_check_seq` to spend an allowance of seq
    numbers bought by the recorder's own `update_subscription` frames, the missing `_advances`
    raised `AttributeError` here. That is the bypass's bill, not a defect in the sink -- a
    `getattr` default in production code would hide a genuinely half-built sink -- so the
    helper is what grows. With no allowance ever booked (this case books none, as a tape that
    lost a frame has), every assertion below is unchanged: the skip is a real loss and writes
    its row.
    """
    added: list = []
    sink = WsSink.__new__(WsSink)
    sink._last_seq = {}
    sink._session = NS(add=added.append)
    sink._pending = 0
    sink._pending_sids = set()
    sink.gap_sids = set()
    sink._gaps_since = 0
    sink._advances = {}
    sink._advance_ttl_s = SEQ_ADVANCE_TTL_S
    sink._advances_since = 0
    sink._acks_out_since = 0
    return sink, added


def test_a_real_missing_subscription_frame_still_writes_a_gap_row():
    """The guard for case 1a (review I-c1). Expected gap rows: 0 for the complete stream, 1 for
    the incomplete one.

    Computed independently: `_check_seq` remembers the last seq per subscription, so a complete
    stream 1, 2, 3 -- whichever tickers those frames addressed -- never skips a number and
    records nothing. A stream 1, 3 with no frame 2 anywhere on the subscription skipped one, and
    exactly one gap row is due. That row carries `sid` and, in `raw`, the expected and received
    seq plus `exposed_by`; its own `ticker` column is `""`, the whole-subscription sentinel,
    because a gap invalidates every ticker on the sid and not just the one whose frame exposed
    it (`harness/recorder/ws_sink.py:84-95`). The sentinel is load-bearing, not cosmetic: a
    sid-level consumer meaning to invalidate every ticker on the subscription can only do that
    by reading gap rows on `ticker == ""`, and a row scoped to the exposing ticker would tell
    it to invalidate just that one. 6B may not make case 1a green by weakening this.
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
    assert rows[0].ticker == ""
    assert rows[0].raw["expected"] == 2 and rows[0].raw["got"] == 3


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
    # `exec_nw_budget_ms` beside `exec_period_s` because `_simulate` reads it once per
    # step for fix 78c's walk budget; a frame driven by hand never spends it (the walk
    # and the per-row step are what charge it), so any value leaves these cases alone.
    executor.settings = NS(exec_period_s=15, exec_nw_budget_ms=2000)
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
             nw_done=True, status="open")
    for key, value in over.items():
        setattr(row, key, value)
    return row


#: A market that is never dirty, and one that always is. `_simulate_order` calls
#: `market.dirty(now, exec_settings)` and nothing else on it.
CLEAN_MARKET = {1: NS(dirty=lambda *unused: False)}
DIRTY_MARKET = {1: NS(dirty=lambda *unused: True)}


def test_recovery_takes_no_fill_from_a_trade_inside_the_gap():
    """Probe `actual_recovery_branch`. Expected fill 0.

    Computed independently: before the gap, 5 rest ahead of us. During the gap one trade of 3
    happens. The recovery snapshot is taken after that trade and shows 2 resting ahead -- which
    is the same 5 minus the same 3, already accounted for. Applying the gap's trade to the
    recovered queue would subtract those 3 a second time, taking 2 to -1 and paying us 1
    contract we were never in line for. The recovery snapshot is the queue; the trade that
    produced it is spent. The probe captured `actual_fill 1.00` with the print watermark left
    at the trade's own timestamp while the delta cursor had moved to the recovery anchor. The
    asymmetry is visible in `harness/execution/loop.py`: the no-book branch resets both the
    cursor (line 822) and the print watermark (line 823), while the recovery branch at line 828
    advances only the cursor (line 835) and leaves `last_print_ts` where it was.
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


def test_a_cancelled_order_accrues_no_dirty_seconds():
    """Probe `cancelled_counterfactual_dirty_accrual`. Expected watched calls 0, watched
    seconds added 0.

    Computed independently: `dirty_minutes` is a property of the watched order -- how long the
    order we placed sat against a book we could not read. A cancelled order is not sitting
    against anything: it left the market when it was cancelled. The 15 s belongs to the
    no-watcher counterfactual, which is still running, and before 6B §1.5
    `store.add_dirty_seconds` had nowhere to put it but the order's own
    `dirty_seconds`/`dirty_minutes` columns. So no write to the watched counter is due. The
    probe captured one call adding 15 s to order 157's own counter, which is how a cancelled
    order reached `dirty_minutes 3020`.

    The assertion is on the *watched* calls rather than on every call because §1.5 gave the
    counterfactual a column of its own (`orders.nw_dirty_seconds`) behind the same function
    name, so the repaired loop makes exactly one call here, `watched=False`. `is not False`
    rather than `is True` is deliberate: the defect's own call passed no `watched` keyword at
    all, so this still reddens if the status guard is removed.
    """
    row = _order_row(status="cancelled", nw_done=False)
    executor, _ = _executor({})
    with patch("harness.execution.store.add_dirty_seconds") as add_dirty:
        executor._simulate_order(None, row, DIRTY_MARKET, {}, set(), {}, set(),
                                 at(30), ExecStats())
    assert [c for c in add_dirty.call_args_list if c.kwargs.get("watched") is not False] == []


def test_the_watched_track_takes_no_fill_after_expiry():
    """Review I-c2 / addendum §0.5 case 5. Expected fill 0.

    Computed independently: an order with an expiry of T0 + 10 s is off the market from T0 +
    10 s. A print at T0 + 20 s happened after the order stopped existing, so nothing of ours
    could have traded against it. The no-watcher track computes its own deadline as
    `min(now, expiry)` (`harness/execution/loop.py:867`); the watched track is handed `now`
    (`loop.py:852`, the `now` argument of the call spanning 850-852), so a print in the ten seconds between expiry and the loop instant fills an
    order that is no longer there. Queue is 0 here so the print reaches us immediately.
    """
    base = BookState.from_levels("A", [[".30", "0"]], [[".60", "5"]], sid=SID, seq=1,
                                 as_of=at(0), source="ws", anchor_id=1)
    late = TapePrint("after-expiry", at(20), D(".30"), D(4), "no", "ws")
    row = _order_row(expiry=at(10), queue_ahead_at_place=D(0), queue_remaining=D(0),
                     nw_done=True)
    executor, captured = _executor({"A": base})
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(None, row, CLEAN_MARKET, {"A": base}, set(),
                                 {"A": ([late], [])}, set(), at(30), ExecStats())
    assert captured[0].state.filled_contracts == D(0)


def test_a_rejected_latest_verdict_yields_no_place():
    """Addendum §0.5 case 6. Expected `Place` count 0.

    Computed independently: `latest_decision` is the newest verdict the strategy reached for
    this market and side. `rejected` means the strategy has since decided this is not a bet. An
    order placed on it is an order placed against the strategy's own current answer. The cancel
    path already agrees -- a resting order whose latest verdict is `rejected` is cancelled with
    reason `signal_rejected` (`harness/execution/plan.py:511`) -- so placing one in the same
    loop would cancel it in the next. The placement path never makes that test
    (`_intent_actions`, defined at `plan.py:519` and called from `plan_actions` at
    `plan.py:616`), so a rejected intent still produces a Place.
    """
    rejected = intent(decision="rejected")
    actions = plan_actions([rejected], [], {1: market()}, {}, {"v1": cfg()},
                           kill_active=False, now=NOW, s=S)
    assert [a for a in actions if isinstance(a, Place)] == []


def test_criterion_one_is_satisfiable_by_a_realistic_population():
    """Probe `criterion1_not_mathematically_impossible`, the reconciliation's correction of
    "criterion 1 cannot pass at any fill count". Expected: passed, value 150, 40 game clusters.

    Computed independently from the definition text: criterion 1 needs at least 150 non-replay
    orders carrying a `queue_model` fill, spread over at least 40 distinct games, with both
    sports present, and at least 80 % of them on a clean WS book. 150 orders over game ids
    0..39 is 150 observations in exactly 40 clusters; alternating `nfl` and `ncaaf` puts both
    sports in the set; every row carries `book_source = 'ws'` and `dirty_minutes = 0`, so the
    clean share is 1.00, above 0.80. Nothing in the definition scales a threshold with the
    population, so the criterion is satisfiable and the gate's first item is not the blocker.
    This is a passing guard, not a claim that the gate passes: eleven other criteria are
    unexamined here and two are False by construction.
    """
    rows = [NS(id=i, game_id=i % 40, sport="nfl" if i % 2 else "ncaaf",
               book_source="ws", dirty_minutes=0) for i in range(150)]
    session = NS(execute=lambda *unused, **unused_kw: NS(all=lambda: rows))
    result = fill_events(session, NOW, "synthetic-guard", CRITERIA[0])
    assert result.passed is True
    assert result.value == 150 and result.n_clusters == 40
    assert result.detail["ws_clean_share"] == 1.0
    assert sorted(result.detail["sports"]) == ["ncaaf", "nfl"]
