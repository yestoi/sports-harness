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

from harness.audit import VERDICTS
from harness.cli import app
from harness.corrections import (
    CONFIG_HASHES_C0,
    CORRECTIONS,
    MANIFEST_VERSION,
    ORDER_157_VERDICT,
    VARIANT_IDS_C0,
    Correction,
    measurement_version,
)

runner = CliRunner()

RECORD = (Path(__file__).resolve().parents[1]
          / "docs/superpowers/reviews/2026-09-11-correction-manifest.md")
AMENDMENT = (Path(__file__).resolve().parents[1]
             / "docs/superpowers/reviews/2026-09-07-phase2-preregistration.md")


def test_measurement_version_is_read_at_call_time(monkeypatch):
    """Bound at import, a bumped `EXECUTOR_VERSION` would be invisible to a running process and
    to a test that patches it -- the staleness `harness/execution/plan.py:120` avoids on purpose
    (review Minor 1). `measurement_version` reads the package attribute each call."""
    from harness import execution

    assert measurement_version() == execution.EXECUTOR_VERSION
    monkeypatch.setattr(execution, "EXECUTOR_VERSION", "9.9")
    assert measurement_version() == "9.9"


def test_the_manifest_carries_c0_through_c6():
    """6B ships six corrections beside the baseline, and bumps the manifest to 7."""
    assert MANIFEST_VERSION == 7
    assert [c.id for c in CORRECTIONS] == ["C0", "C1", "C2", "C3", "C4", "C5", "C6"]
    for c in CORRECTIONS[1:]:
        assert c.measurement_version_before == "4.4"
        assert c.measurement_version_after == "4.5"
        assert c.rescore_command, c.id


def test_order_157_verdict_is_a_recognised_verdict_or_not_yet_audited():
    """`ORDER_157_VERDICT` ships as the controller's real-capsule run recorded it, or as the
    unrun placeholder; either way it must be a value 6C's reader can act on, never an arbitrary
    string. Not pinned to a particular verdict: a parallel task re-scopes the audit's manifest
    gate and the controller rewrites this constant after its rerun."""
    assert ORDER_157_VERDICT in (*VERDICTS, "not yet audited")


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
    assert len(VARIANT_IDS_C0) == 7
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
    assert len(CONFIG_HASHES_C0) == 6
    for value in CONFIG_HASHES_C0:
        assert re.fullmatch(r"[0-9a-f]{64}", value), value


def test_the_ids_agree_across_the_code_the_record_and_the_amendment():
    """The three-way parity the reviews required (CR-1). A correction named in one place and not
    the other two is drift: the container reads the code, a human reads the record, and the
    pre-registration amendment is what the experiment is judged against.

    The amendment's id set is read from its own heading and its Change paragraph, so a heading
    that says "C1-C6" while the tuple holds five entries fails here rather than at the report.
    """
    ids = [c.id for c in CORRECTIONS]
    record = re.findall(r"^## (C\d+)\b", RECORD.read_text(), flags=re.MULTILINE)
    assert record == ids
    amendment = AMENDMENT.read_text()
    section = amendment.split("## Amendment 6")[1].split("\n## ")[0]
    assert sorted(set(re.findall(r"\bC[1-6]\b", section))) == ids[1:]


def test_every_6b_correction_ships_its_tuples_for_the_controller():
    """Shape only while the tuples are empty: agents have no NAS access, and the controller
    fills `config_hashes` and the two numeric ranges at merge time (D11), adding the count
    assertions in that same commit. The loop below runs in both states, so an entry of the
    wrong width fails the moment it is pasted in.
    """
    for c in CORRECTIONS[1:]:
        for value in c.config_hashes:
            assert re.fullmatch(r"[0-9a-f]{64}", value), (c.id, value)
        for field_value in (c.affected_order_id_range, c.affected_run_id_range):
            assert field_value == "<filled at merge>" or re.fullmatch(
                r"\d+-\d+|>\s*\d+", field_value), (c.id, field_value)


def test_the_6b_tuples_are_filled_with_the_release_values():
    """D11's count assertions, added in the commit that filled the tuples (2026-09-15, release
    c1066b5): one config-hash tuple of three serves C1-C6, the two ranges are the numeric
    open-ended form the release boundary produced, and no C1-C6 field still carries a
    placeholder. The values themselves are the controller's fill and are not pinned here beyond
    their width (T11 review M-4).
    """
    for c in CORRECTIONS[1:]:
        assert len(c.config_hashes) == 3, c.id
        assert c.config_hashes == CORRECTIONS[1].config_hashes, c.id
        assert re.fullmatch(r">\s*\d+", c.affected_order_id_range), (c.id, c.affected_order_id_range)
        assert re.fullmatch(r">\s*\d+", c.affected_run_id_range), (c.id, c.affected_run_id_range)
        assert c.deploy_sha != "<sha>" and c.code_version_after == c.deploy_sha, c.id
        assert c.code_version_before == CORRECTIONS[0].deploy_sha, c.id


def test_the_rescore_command_is_a_recognised_instrument():
    """§0.12: `harness rescore` stands beside `harness replay`, an order-scoped correction
    against a range-scoped one, and all three standing documents say so.

    Asserted on `Correction.__doc__`, not on the field's `#:` comment: Python discards those at
    runtime, so `__dataclass_fields__["rescore_command"].__doc__` is `dataclasses.Field`'s own
    class docstring and an assertion against it would pass whatever the field said (review
    IM-12). The sentence therefore lives in the class docstring, which is readable.
    """
    from harness.corrections import Correction

    assert "harness rescore" in Correction.__doc__
    assert "harness replay" in Correction.__doc__
    assert "harness rescore" in RECORD.read_text()
    assert "harness rescore" in AMENDMENT.read_text()


def test_manifest_command_prints_the_tuple():
    result = runner.invoke(app, ["manifest"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["manifest_version"] == MANIFEST_VERSION
    assert doc["measurement_version"] == measurement_version()
    assert [c["id"] for c in doc["corrections"]] == ["C0", "C1", "C2", "C3", "C4", "C5", "C6"]
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


#: A recorded sha is either the plan's unfilled placeholder or an abbreviated-to-full git object
#: name: 7 to 40 hexadecimal characters (T11 review M-4).
SHA_PLACEHOLDER = "<sha>"
SHA_WIDTH = re.compile(r"\A[0-9a-f]{7,40}\Z")


def test_every_recorded_sha_is_the_placeholder_or_a_git_object_name():
    """T11 review M-4: `code_version_before`, `code_version_after` and `deploy_sha` are read as
    shas by every consumer of the manifest (the record, `harness manifest`, 6C's report), so a
    value that is neither a sha nor the plan's own unfilled placeholder is a manifest that
    cannot be checked out. The width is asserted, not the value: the controller fills the
    placeholders at the deploy commit and no test may pin what it fills them with.
    """
    for correction in CORRECTIONS:
        for field in ("code_version_before", "code_version_after", "deploy_sha"):
            value = getattr(correction, field)
            assert isinstance(value, str), (correction.id, field)
            assert value == SHA_PLACEHOLDER or SHA_WIDTH.match(value), (correction.id, field,
                                                                        value)
