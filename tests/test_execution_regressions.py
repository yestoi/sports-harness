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
