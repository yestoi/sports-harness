"""Load the frozen pre-hotfix producers without consulting git or a database at test time.

Phase 6D, decision D12 (addendum §1.5(d)): the parity contract these producers anchor was
amended. `test_the_stage_order_produces_exactly_what_the_single_pass_produced` no longer
compares the **whole** stored signal population against the single pass, because stage 6 stopped
re-scoring direct-only variants over derived rows and therefore stores fewer rejected rows.
What is still asserted identical is the candidate population and every candidate's `edge`,
`stake`, `contracts` and labels, plus `fair_direct`, `fair_derived`, `no_sharp`, `gaps`, `order`
and the fair-row and gap-row parity; the rejected-row difference is asserted separately against
`notes->'pricing'->'rescore_suppressed'`, per variant and in total. Nothing this module loads
changed, and no candidate, price, size, label or order moves.
"""
import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def baseline_pipeline():
    root = Path(__file__).parent / "fixtures" / "pricing_44e8e9c"
    loaded = {}
    for name in ("fair", "gaps", "pipeline"):
        spec = importlib.util.spec_from_file_location(f"_pricing_44e8e9c_{name}", root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    pipeline = loaded["pipeline"]
    pipeline.compute_fair_values = loaded["fair"].compute_fair_values
    pipeline.build_gap_snapshots = loaded["gaps"].build_gap_snapshots
    return pipeline


def recorded_pricing_notes(name: str = "run_notes_pricing_14307") -> dict:
    """A `notes->'pricing'` block production really wrote (6D addendum §5).

    `name` is the fixture stem: `run_notes_pricing_14307` (the six-stage shape of live fact (a))
    or `run_notes_pricing_exhausted` (the pre-fix-48 exhausted shape). Returned fresh each call,
    so a caller that mutates it cannot leak into the next test.
    """
    path = Path(__file__).parent / "fixtures" / f"{name}.json"
    return json.loads(path.read_text())
