"""§1.2: the run manifest is frozen, canonical and hashed, and a changed manifest refuses resume."""
from datetime import datetime, timezone

import pytest

from harness.experiments.execution_viability.manifest import (
    CLOCK_MODES, Manifest, ManifestMismatch, canonical_json, check_resume,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _manifest(**over) -> Manifest:
    """A complete manifest. Every field is set: `freeze()` refuses a None (§1.2)."""
    base = dict(
        run_id="6f1a2b3c-0000-4000-8000-000000000001",
        code_sha="e3c5463", schema_version="0015_phase6d1_exec_viability",
        simulator_version="4.5", pricing_version="2.3",
        baseline_settings={"exec_period_s": 15, "stale_s": 180},
        variant_configs={"sharp_two_sided": {"stale_s": 180}},
        arms=({"arm_id": "A", "label": "baseline"},),
        arm_hashes=("a" * 64,),
        capture_hashes={"orders.ndjson": "b" * 64},
        run_id_bounds=(14400, 14486), order_id_bounds=(1, 32081), fill_id_bounds=(1, 3669),
        placement_start=NOW, placement_end=NOW, warmup_start=NOW, observation_end=NOW,
        extracted_at=NOW, exclusions=("no_watcher",),
        portfolio_identity=("run", "arm", "variant"), shared_slot_limit=150,
        clock_mode="retained_action_instants", cohort=("KXNFLGAME-26SEP20DETBAL",),
        selection_seed=20260916, opportunity_definition={"gap_rule_s": 600},
        scheduled_observations=3600, available_observations=3598,
        timestamp_semantics={"fair_values.created_at": "availability"},
        credit_budget=60000, request_budget=7200,
        resource_limits={"statement_timeout_s": 25, "batch_rows": 20000},
        markout_horizons=(1800,), missingness_policy="censored_recorded",
        review_deadline="14 calendar days after activation",
    )
    base.update(over)
    return Manifest(**base)


def test_the_same_inputs_freeze_to_the_same_sixty_four_character_hash():
    first, second = _manifest().freeze(), _manifest().freeze()
    assert first == second
    assert len(first) == 64 and set(first) <= set("0123456789abcdef")


def test_one_changed_variant_config_byte_changes_the_hash():
    other = _manifest(variant_configs={"sharp_two_sided": {"stale_s": 181}})
    assert other.freeze() != _manifest().freeze()


def test_canonical_json_is_sorted_and_separator_tight():
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_freeze_refuses_an_incomplete_manifest():
    incomplete = _manifest(cohort=None)
    with pytest.raises(ValueError, match="cohort"):
        incomplete.freeze()


def test_supersedes_may_be_none_because_a_first_run_supersedes_nothing():
    assert _manifest().supersedes is None
    assert len(_manifest().freeze()) == 64


def test_an_unknown_clock_mode_is_refused_at_construction():
    with pytest.raises(ValueError, match="clock_mode"):
        _manifest(clock_mode="recorded")
    assert CLOCK_MODES == ("retained_action_instants", "ideal_grid_15s")


def test_resume_refuses_a_manifest_whose_hash_moved():
    stored = _manifest().freeze()
    changed = _manifest(selection_seed=20260917)
    with pytest.raises(ManifestMismatch, match=stored[:12]):
        check_resume(stored, changed)
    check_resume(stored, _manifest())   # the unchanged manifest resumes
