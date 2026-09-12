"""Load the frozen pre-hotfix producers without consulting git or a database at test time."""
import importlib.util
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
