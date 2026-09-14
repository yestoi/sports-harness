"""Load the frozen pre-hotfix producers without consulting git or a database at test time."""
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
