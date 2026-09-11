"""Read-only review probes against HEAD 6eed2d8; no database or network.

Run from the repository root:
  /path/to/sports/.venv/bin/python /path/to/review/reproduce.py --repo /path/to/sports

These print observed behavior, not regression-test assertions of desired behavior.
They intentionally reuse the repository's committed pure fixture helpers.
"""
import argparse
import os
from pathlib import Path
import sys
import runpy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", type=Path, default=Path("/Users/trey/dev/sports"))
args = parser.parse_args()
repo_path = args.repo.resolve()
os.chdir(repo_path)
sys.path.insert(0, str(repo_path))
from datetime import timedelta
from decimal import Decimal

from harness.execution.book import BookState
from harness.execution.fills import PaperOrder, SimState, TapePrint, simulate_fills
from harness.report.tables import Table
from harness.report.weekly import CELL_KEY, restrict_to_selection

p = runpy.run_path("tests/test_exec_plan.py")
f = runpy.run_path("tests/test_fills.py")
t = p["NOW"]

# A complete subscription stream has A seq1, B seq2, A seq3.
b = BookState.from_levels("A", [["0.30", "5"]], [["0.60", "5"]],
                          sid=7, seq=1, as_of=t, source="ws", anchor_id=1)
b.apply_delta("yes", Decimal(".30"), Decimal("1"), seq=3,
              ts=t + timedelta(seconds=1), event_id=3)
print("Complete multiplexed stream [1,2,3], A sees [1,3]:", {"dirty": b.dirty})

rejected = p["intent"](decision="rejected")
print("Rejected intent, no order:", p["plan"](intents=[rejected]))
print("Rejected intent, existing order:",
      p["plan"](intents=[rejected], orders=[p["order"]()]))

order = PaperOrder(order_id=1, ticker="K1", side="yes", prob=Decimal(".45"),
                   contracts=Decimal("10"), placed_at=t,
                   expiry=t + timedelta(seconds=10), queue_ahead_at_place=Decimal("0"))
trade = TapePrint(trade_id="after-expiry", ts=t + timedelta(seconds=11),
                  yes_price=Decimal(".45"), count=Decimal("10"), taker_side="no", source="ws")
result = simulate_fills(order, SimState.initial(order), None, [trade], [],
                        t + timedelta(seconds=15), "queue_model")
print("Watched deadline=now, expiry=+10s:",
      [("fill_seconds", (x.filled_at-t).total_seconds(), "contracts", x.contracts)
       for x in result.fills])

result = f["run"](f["order"](queue="5"), prints=[f["tprint"](1, ".30", "3")],
                  deltas=[f["tdelta"](1, "yes", ".30", "-3")])
print("One trade=3, matching delta=-3, queue initially=5:",
      {"fills": [str(x.contracts) for x in result.fills],
       "remaining_queue": str(result.state.queue_remaining)})

key = ["direct", "35-50", "3-24 h", "nfl", "moneyline"]
table = Table("probe", "probe", [*CELL_KEY, "posterior", "bh"],
              [[*key, (0.02, 100, 2, 0.01, 0.03), "grey"]])
selection = {"cells": [dict(zip(CELL_KEY, key))], "contrasts": []}
print("Week-3 confirmation, only two games:",
      restrict_to_selection({"t4": table}, selection)["t4"].note)
