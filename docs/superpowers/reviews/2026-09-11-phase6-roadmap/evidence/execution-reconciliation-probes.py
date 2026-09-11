#!/usr/bin/env python3
"""Database-free execution review probes; no repository or production writes.

Run with the reviewed checkout's environment:
  /Users/trey/dev/sports/.venv/bin/python execution-reconciliation-probes.py
  /path/to/repo/.venv/bin/python execution-reconciliation-probes.py --repo /path/to/repo

Reviewed against HEAD 6eed2d8d49a7bf1f107dfd83141b8eeef556b620. This is an
observational review script, not a regression suite asserting desired behavior.
The actual executor recovery method runs with persistence mocked. Gate rows are
synthetic, and the gate SQL is not executed. No engine or database session is
created. Synthetic order-157-shaped quantities do not replay or attribute order 157.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch


def emit(label, value):
    print(label + ": " + json.dumps(value, default=str, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/Users/trey/dev/sports"))
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    # Source imports must not create bytecode files in the reviewed checkout.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo))

    from harness.execution.book import BookState
    from harness.execution.fills import TapePrint, TapeDelta
    from harness.execution.loop import Executor, ExecStats, _TrackResult
    from harness.report.gate import fill_events, CRITERIA

    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    emit("provenance", {"repo": str(repo), "head": head,
                        "expected_review_head": "6eed2d8d49a7bf1f107dfd83141b8eeef556b620"})
    emit("boundaries", {
        "database": "No database engine/session created; no SQL executed.",
        "executor": "Actual _simulate_order and simulate_fills; _persist_track and store writes mocked.",
        "gate": "Actual fill_events function, synthetic rows returned by a fake session.",
        "attribution": "Synthetic probes demonstrate possible defects, not what happened to order 157.",
    })

    f = runpy.run_path(str(repo / "tests/test_fills.py"))
    t = datetime(2026, 9, 11, tzinfo=timezone.utc)

    # Complete subscription stream: A seq1, B seq2, A seq3. The per-ticker reader
    # sees only A's 1 and 3, even though no subscription frame was lost.
    multiplexed = BookState.from_levels(
        "A", [[".30", "5"]], [[".60", "5"]], sid=7, seq=1,
        as_of=t, source="ws", anchor_id=1)
    multiplexed.apply_delta("yes", D(".30"), D(1), seq=3,
                            ts=t + timedelta(seconds=1), event_id=3)
    emit("multiplexed_sequence", {
        "complete_subscription_sequence": [1, 2, 3], "ticker_A_sequence": [1, 3],
        "actual_dirty": multiplexed.dirty, "expected_dirty": False,
    })

    result = f["run"](
        f["order"](queue="5"), prints=[f["tprint"](1, ".30", "3")],
        deltas=[f["tdelta"](1, "yes", ".30", "-3")])
    emit("same_event", {
        "queue_before": 5, "real_trade": 3, "book_delta": -3,
        "actual_queue": result.state.queue_remaining,
        "actual_fill": result.state.filled_contracts,
        "expected_queue": 2, "expected_fill": 0,
    })

    # Before gap: queue 5. During gap: one trade 3. Recovery snapshot reflects
    # that trade and has queue 2. The stale print watermark admits the old trade
    # against the new queue, while the new delta cursor excludes its delta.
    row = NS(
        id=1, venue_market_id=1, ticker="A", side="yes", prob=D(".30"),
        contracts=D(10), placed_at=t, expiry=t + timedelta(hours=1),
        queue_ahead_at_place=D(5), queue_remaining=D(5), traded_at_price=D(0),
        filled_contracts=D(0), tape_cursor_event_id=1, crossed=False,
        last_print_ts=t, last_print_ids=(), nw_queue_remaining=D(5),
        nw_traded_at_price=D(0), nw_filled_contracts=D(0),
        nw_tape_cursor_event_id=1, nw_crossed=False, nw_last_print_ts=t,
        nw_last_print_ids=(), nw_done=True, status="open")
    recovered_book = BookState.from_levels(
        "A", [[".30", "2"]], [[".60", "5"]], sid=7, seq=3,
        as_of=t + timedelta(seconds=20), source="ws", anchor_id=3)
    trade = TapePrint("during-gap", t + timedelta(seconds=10), D(".30"), D(3), "no", "ws")
    delta = TapeDelta(2, t + timedelta(seconds=10), "yes", D(".30"), D(-3), 7, 2)
    executor = Executor.__new__(Executor)  # Bypass runtime/gateway/session setup.
    executor.exec_settings = NS()
    executor.settings = NS(exec_period_s=15)
    executor.books = {"A": recovered_book}
    captured = []

    def persist(session, order_row, order, result, prints, ledger, crossed_already):
        captured.append(result)
        return _TrackResult(result.state, result.state.filled_contracts,
                            len(result.fills), result.crossed)

    executor._persist_track = persist
    with patch("harness.execution.store.update_order"):
        executor._simulate_order(
            None, row, {1: NS(dirty=lambda *unused: False)}, {}, {"A"},
            {"A": ([trade], [delta])}, set(), t + timedelta(seconds=30), ExecStats())
    result = captured[0]
    emit("actual_recovery_branch", {
        "old_queue": 5, "snapshot_after_trade_queue": 2, "past_trade": 3,
        "cursor_after_recovery": result.state.cursor_event_id,
        "actual_fill": result.state.filled_contracts, "expected_recovery_fill": 0,
        "actual_print_watermark": result.state.last_print_ts,
        "recovery_book_as_of": recovered_book.as_of,
    })

    # Show why the terminal quantities alone cannot identify recovery as the
    # cause. This synthetic single-batch example never calls recovery code.
    result = f["run"](
        f["order"](queue="6401", contracts="87"),
        prints=[f["tprint"](1, ".30", "25", trade_id="first25"),
                f["tprint"](1, ".30", "25", trade_id="second25"),
                f["tprint"](1, ".30", "13.92", trade_id="last13.92")],
        deltas=[f["tdelta"](1, "yes", ".30", "-6376")])
    emit("order157_shaped_without_recovery", {
        "initial_queue": 6401, "same_timestamp_delta": -6376,
        "same_timestamp_prints": ["25", "25", "13.92"],
        "fills": [x.contracts for x in result.fills],
        "queue_remaining": result.state.queue_remaining,
        "traded_at_price": result.state.traded_at_price,
        "filled_contracts": result.state.filled_contracts,
        "limitation": "Synthetic terminal-quantity counterexample, not order 157's actual tape.",
    })

    ft = runpy.run_path(str(repo / "tests/test_fills_tape.py"))
    _, prints, deltas = ft["_tape"]()
    exact = sum(any(
        d.side == ft["opp"](p.taker_side)
        and d.price == ft["price_on_side"](p, ft["opp"](p.taker_side))
        and d.delta < 0 and -d.delta >= p.count and d.ts == p.ts
        for d in deltas) for p in prints)
    emit("fixture_equal_timestamp_matching_delta", {
        "matching_prints": exact, "total_prints": len(prints),
        "limitation": "At least one matching delta per print; not unique event pairing.",
    })

    row.status, row.nw_done = "cancelled", False
    with patch("harness.execution.store.add_dirty_seconds") as add_dirty:
        executor._simulate_order(
            None, row, {1: NS(dirty=lambda *unused: True)}, {}, set(), {},
            set(), t + timedelta(seconds=30), ExecStats())
    emit("cancelled_counterfactual_dirty_accrual", {
        "watched_status": row.status, "counterfactual_done": row.nw_done,
        "shared_dirty_counter_write_calls": len(add_dirty.call_args_list),
        "seconds_added": add_dirty.call_args.args[2] if add_dirty.called else 0,
    })

    rows = [NS(id=i, game_id=i % 40, sport="nfl" if i % 2 else "ncaaf",
               book_source="ws", dirty_minutes=0) for i in range(150)]
    session = NS(execute=lambda *unused: NS(all=lambda: rows))
    criterion = fill_events(session, t, "synthetic-probe", CRITERIA[0])
    emit("criterion1_not_mathematically_impossible", criterion.as_json())
    emit("final_limitation", {
        "live_attribution": "No live query or historical replay was performed.",
        "order157": "Specific cause and validity of order 157 remain unresolved.",
        "full_gate": "Criterion 1 counterexample does not assert the full go-live gate passes.",
    })


if __name__ == "__main__":
    main()
