"""The correction manifest: versioned code, a mirrored record, and `harness manifest`.

The manifest exists so a corrected measurement can never be mistaken for the original one. It
lives in code because 6B's replay and 6C's report read it inside the container, where `docs/` is
not present (D4); the record in `docs/` is where the prose goes, and the parity test is what
stops the two from drifting.
"""

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from harness.cli import app
from harness.corrections import (
    CONFIG_HASHES_C0,
    CORRECTIONS,
    MANIFEST_VERSION,
    VARIANT_IDS_C0,
    Correction,
    measurement_version,
)

runner = CliRunner()

RECORD = (Path(__file__).resolve().parents[1]
          / "docs/superpowers/reviews/2026-09-11-correction-manifest.md")


def test_measurement_version_is_read_at_call_time(monkeypatch):
    """Bound at import, a bumped `EXECUTOR_VERSION` would be invisible to a running process and
    to a test that patches it -- the staleness `harness/execution/plan.py:120` avoids on purpose
    (review Minor 1). `measurement_version` reads the package attribute each call."""
    from harness import execution

    assert measurement_version() == execution.EXECUTOR_VERSION
    monkeypatch.setattr(execution, "EXECUTOR_VERSION", "9.9")
    assert measurement_version() == "9.9"


def test_c0_is_the_baseline_entry():
    """6A ships exactly one correction: the record of what the baseline was."""
    assert MANIFEST_VERSION == 1
    assert [c.id for c in CORRECTIONS] == ["C0"]
    c0 = CORRECTIONS[0]
    assert c0.measurement_version_before == c0.measurement_version_after == "4.4"
    assert c0.rescore_command == "none: the baseline is the record"
    assert c0.variant_ids == VARIANT_IDS_C0
    assert c0.config_hashes == CONFIG_HASHES_C0


def test_variant_ids_are_registered_variant_ids():
    """Each entry is one `strategy_variants.variant_id`: 12 lowercase hex characters
    (`VARIANT_ID_LEN = 12`, `harness/strategy/variants.py`). Seven are registered -- the six of
    the phase-2 pre-registration plus `sharp_two_sided` under U2 -- one per YAML under
    `harness/variants/`.

    Read from the database by the controller, never recomputed from the YAMLs here: the manifest
    records what was actually registered and traded under, and a module that derives its own
    contents from the same source it is meant to attest records nothing.

    Shape only while the tuple is empty -- the controller fills it at merge time from
    `select variant_id, name from strategy_variants` and adds `assert len(VARIANT_IDS_C0) == 7`
    to this test in the same commit (T3 step 9). The loop runs in both states, so an entry of
    the wrong width fails the moment it is pasted in.
    """
    for value in VARIANT_IDS_C0:
        assert re.fullmatch(r"[0-9a-f]{12}", value), value


def test_config_hashes_are_executor_config_hashes():
    """Each entry is one `orders.config_hash`: a 64-character sha256 in lowercase hex
    (`harness/execution/plan.py:118`). Six distinct values span the paper run.

    Not `config_history.config_hash`, which holds 12-hex variant ids on a table the capsule
    copies in full (task 2) and the manifest never reads. Both columns are `String(64)`
    (`harness/db/models.py:362` and `:439`); it is the values that differ in width, not the
    column, so nothing but this test catches a value put in the wrong tuple.

    Shape only while the tuple is empty -- the controller fills it at merge time from
    `select distinct config_hash from orders where replay = false` and adds
    `assert len(CONFIG_HASHES_C0) == 6` to this test in the same commit (T3 step 9). The loop
    runs in both states, so an entry of the wrong width fails the moment it is pasted in.
    """
    for value in CONFIG_HASHES_C0:
        assert re.fullmatch(r"[0-9a-f]{64}", value), value


def test_the_record_headings_equal_the_tuple_ids():
    """The record is where 6B writes prose; the tuple is what the container reads. A heading
    without an entry, or an entry without a heading, is the drift this test exists to catch."""
    headings = re.findall(r"^## (C\d+)\b", RECORD.read_text(), flags=re.MULTILINE)
    assert headings == [c.id for c in CORRECTIONS]


def test_manifest_command_prints_the_tuple():
    result = runner.invoke(app, ["manifest"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["manifest_version"] == MANIFEST_VERSION
    assert doc["measurement_version"] == measurement_version()
    assert [c["id"] for c in doc["corrections"]] == ["C0"]
    assert doc["corrections"][0]["variant_ids"] == list(VARIANT_IDS_C0)
    assert doc["corrections"][0]["config_hashes"] == list(CONFIG_HASHES_C0)


def test_correction_has_exactly_the_designed_fields():
    """The field list is roadmap decision 1's, including the strategy ids and config hashes the
    first draft omitted (review I-e). A field added without a design change is a silent widening
    of what the manifest claims."""
    assert [f.name for f in Correction.__dataclass_fields__.values()] == [
        "id", "title", "code_version_before", "code_version_after",
        "measurement_version_before", "measurement_version_after", "deploy_sha",
        "variant_ids", "config_hashes", "affected_order_id_range", "affected_run_id_range",
        "eligible_measurements", "excluded_measurements", "rescore_command"]
