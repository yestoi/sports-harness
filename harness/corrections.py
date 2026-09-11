"""The correction manifest: what was measured, what changed it, and what may still be read.

Spec §6.7's amendment protocol says original rows are never rewritten. A corrected replay writes
`replay = true` rows beside them and this manifest is the index of those corrections: for each
one, the code and measurement versions either side, the deploy that carried it, the strategies
and executor configurations in force, the id ranges it touches, which measurements survive it,
which do not, and the exact command that re-scores the affected range.

It lives in code, not only in `docs/`, because 6B's replay and 6C's report read it inside the
container where `docs/` is not present (D4). `docs/superpowers/reviews/2026-09-11-correction-
manifest.md` mirrors it and is where the prose goes; `tests/test_corrections.py` asserts the two
carry the same ids.

6A ships one entry, `C0`: the baseline. It corrects nothing -- it records what the measurement
was before 6B touched it, so that every later entry has something to be "before" of.
"""

from dataclasses import asdict, dataclass

#: Bumped by every 6B entry appended to `CORRECTIONS`. `harness manifest` prints it and 6C's
#: t13 prints it beside `MEASUREMENT_VERSION`, so a report always says which manifest it was
#: written under.
MANIFEST_VERSION = 1


def measurement_version() -> str:
    """`EXECUTOR_VERSION` as of this call, never as of import.

    `harness/execution/plan.py:120` reads it off the package at call time on purpose: binding it
    at import makes a bump invisible to a running process and to a test that patches it. The
    same reasoning applies here, and a manifest that reports a stale measurement version is
    worse than one that reports none.
    """
    from harness import execution

    return execution.EXECUTOR_VERSION


@dataclass(frozen=True)
class Correction:
    """One correction: everything needed to decide what a number from before it still means."""

    id: str
    title: str
    code_version_before: str
    code_version_after: str
    measurement_version_before: str
    measurement_version_after: str
    deploy_sha: str
    #: The registered strategy ids in force: `strategy_variants.variant_id`, 12 hex characters
    #: each (roadmap decision 1: "strategy and config hashes").
    variant_ids: tuple[str, ...]
    #: The executor configurations in force: the distinct `orders.config_hash` values, 64 hex
    #: characters each (`harness/execution/plan.py:118`).
    config_hashes: tuple[str, ...]
    affected_order_id_range: str
    affected_run_id_range: str
    eligible_measurements: str
    excluded_measurements: str
    #: The exact `harness replay` invocation, or a sentence saying why there is none.
    rescore_command: str


# --- FILLED BY THE CONTROLLER AT MERGE TIME ---------------------------------------------------
# Both tuples are read off the NAS by the controller with a journaled query and pasted here
# verbatim. Agents have no NAS access, so both ship empty, and their tests assert the width of
# every entry and nothing about the count: the count assertions arrive in the controller's own
# merge commit, alongside the values that make them assertable. Nothing skips.
#
# The seven registered strategy ids, 12 hex characters each -- the six of the phase-2
# pre-registration plus `sharp_two_sided` under U2, one per YAML in `harness/variants/`
# (constrained, nfl_only, no_velocity, sharp_direct, sharp_plus_derived, sharp_two_sided,
# wide_band). Recorded as registered, never recomputed from the YAMLs: the manifest attests what
# was traded under, and deriving that from the same files it attests would record nothing.
#
#   select variant_id, name from strategy_variants;
#
VARIANT_IDS_C0: tuple[str, ...] = ("ff363c8ac08d", "e549e693e117", "64ba3ef09642", "f259ca109084", "49af716f8708", "c2bc45377328", "5632da729fa7")  # filled 2026-09-11 18:55 CT from strategy_variants (the eighth row, e82fcd0a1e99, is a tier=replay artifact of a phase 3 replay command, journal 60-61, not a registered id)

# The distinct executor configurations the paper run placed orders under: `orders.config_hash`,
# 64 hex characters each (`harness/execution/plan.py:118`), six values across the run. Not
# `config_history.config_hash`, which holds 12-hex variant ids (`harness/strategy/variants.py:213`
# writes `config_hash=variant.variant_id`) on a table the capsule copies in full and this module
# never reads. Both columns are `String(64)`: the values differ in width, the columns do not.
#
#   select distinct config_hash from orders where replay = false;
#
CONFIG_HASHES_C0: tuple[str, ...] = (
    "15a491be8fad9588d9d5465d5a159a6cef8b32642afea11680ba71d36c5d176b",
    "51afb46a112c5d930d2156985bda3ebe92966609828f43c8e1b6538fb3631139",
    "776b1ccac8d4e903f0e2cd0b9a18d17c38540317a0d5da1d39de55fa6a789f66",
    "82768f6793c9318b0aabb067f26eef4b052c2bc724120cbe9d047b6256b2ce75",
    "d6962419ca950dd003f7f8283bc12ab01c36b9fd40e5654901353780d9a9cb9f",
    "ef06087f4af152a62a85ca59aef4efbe08c9ca6ce24fbf1277562e28938267bd",
)  # filled 2026-09-11 18:55 CT from select distinct config_hash from orders where replay = false
# ----------------------------------------------------------------------------------------------

CORRECTIONS: tuple[Correction, ...] = (
    Correction(
        id="C0",
        title="Baseline: the paper record as measured before any 6B repair",
        code_version_before="6eed2d8",
        code_version_after="7c3d555",
        measurement_version_before="4.4",
        measurement_version_after="4.4",
        deploy_sha="7c3d555",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C0,
        affected_order_id_range="all orders through the 6B deploy",
        affected_run_id_range="all runs through the 6B deploy",
        eligible_measurements="raw historical cleanliness and counts, labelled retrospective",
        excluded_measurements="none yet",
        rescore_command="none: the baseline is the record",
    ),
)


def as_json() -> dict:
    """The manifest as `harness manifest` prints it and 6C's report reads it."""
    return {"manifest_version": MANIFEST_VERSION,
            "measurement_version": measurement_version(),
            "corrections": [asdict(c) for c in CORRECTIONS]}
