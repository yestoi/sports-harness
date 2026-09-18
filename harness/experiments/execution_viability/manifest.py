"""§1.2: the immutable run/arm manifest."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

#: §1.2 and `exp_run.clock_mode`'s invariant query (§2). `ideal_grid_15s` is always a labelled
#: sensitivity beside a `retained_action_instants` result, never a silent substitute (§1.3d).
CLOCK_MODES = ("retained_action_instants", "ideal_grid_15s")

#: `supersedes` is None for a first run: a manifest names its predecessor only when it has one.
_OPTIONAL_FIELDS = frozenset({"supersedes"})


class ManifestMismatch(RuntimeError):
    """A resume was attempted against a manifest whose hash differs from the stored one."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"naive datetime in the manifest: {value!r}")
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    return value


def canonical_json(obj: Any) -> str:
    """The one serialisation the hash is taken over: sorted keys, no whitespace (§1.2)."""
    return json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class Manifest:
    # identity
    run_id: str
    code_sha: str
    schema_version: str
    simulator_version: str
    pricing_version: str
    baseline_settings: dict
    variant_configs: dict
    arms: tuple[dict, ...]          # canonical dicts, not ArmSpec objects (plan choice 3)
    arm_hashes: tuple[str, ...]
    # capture
    capture_hashes: dict
    run_id_bounds: tuple[int, int]
    order_id_bounds: tuple[int, int]
    fill_id_bounds: tuple[int, int]
    placement_start: datetime
    placement_end: datetime
    warmup_start: datetime
    observation_end: datetime
    extracted_at: datetime
    exclusions: tuple[str, ...]
    # economics
    portfolio_identity: tuple[str, str, str]
    shared_slot_limit: int
    clock_mode: str
    cohort: tuple[str, ...]
    selection_seed: int
    opportunity_definition: dict
    # measurement
    scheduled_observations: int
    available_observations: int
    timestamp_semantics: dict
    credit_budget: int
    request_budget: int
    resource_limits: dict
    markout_horizons: tuple[int, ...]
    missingness_policy: str
    review_deadline: str
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if self.clock_mode not in CLOCK_MODES:
            raise ValueError(f"clock_mode {self.clock_mode!r} is not one of {CLOCK_MODES}")

    def as_json(self) -> str:
        return canonical_json({f.name: getattr(self, f.name) for f in fields(self)})

    def freeze(self) -> str:
        """The 64-character sha256 of the canonical JSON. Refuses while any field is None."""
        for f in fields(self):
            if getattr(self, f.name) is None and f.name not in _OPTIONAL_FIELDS:
                raise ValueError(f"manifest field {f.name!r} is None; freeze() refuses (§1.2)")
        return hashlib.sha256(self.as_json().encode()).hexdigest()


def check_resume(stored_hash: str, m: Manifest) -> None:
    """§1.2: a resume against a changed manifest raises and leaves the checkpoint untouched.

    T3's `storage.resume()` calls this **before** it reads or writes an `exp_checkpoint` row, which
    is what makes "leaves the checkpoint byte-identical" true by construction.
    """
    current = m.freeze()
    if current != stored_hash:
        raise ManifestMismatch(
            f"manifest hash {current[:12]} does not match the stored {stored_hash[:12]}; "
            f"a changed field is a new run_id naming its predecessor in `supersedes` (§1.2)")
