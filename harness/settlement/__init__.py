"""Settlement, benchmarks and the rest of the batch work that scores what the executor did.

Everything in this package runs on the settler's own scheduler slot (`harness/scheduler.py`),
never inside the recorder's tick: a 600 s settlement batch on the tick's thread would delay a
30 s heartbeat, and two jobs writing the same `runs` row would lose one of the updates. The
settler writes `job_runs` instead.

`Budget` lives in `harness.settlement.job` beside the stage registry that hands it to every
stage; it is re-exported here because Tasks 8, 9 and 12 share it.
"""

from harness.settlement.job import Budget

__all__ = ["Budget"]
