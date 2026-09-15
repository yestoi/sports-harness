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

6B appends six more, `C1`-`C6` (spec §0.1, §1.11): collectively **Amendment 6** to
`docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`, because a bug fix that changes a
label falls under the pre-registration record's amendment protocol item 1 and the Amendment 5
precedent governs. `MANIFEST_VERSION` becomes 7. `EXECUTOR_VERSION` moves 4.4 -> 4.5 once for the
whole milestone (§7.3, D10): the boundary and the amendment are one act, so every 6B correction
records the same before/after measurement version.
"""

from dataclasses import asdict, dataclass

#: Bumped by every 6B entry appended to `CORRECTIONS`. `harness manifest` prints it and 6C's
#: t13 prints it beside `MEASUREMENT_VERSION`, so a report always says which manifest it was
#: written under.
MANIFEST_VERSION = 7


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
    """One correction: everything needed to decide what a number from before it still means.

    `rescore_command` names the instrument that re-derives the affected range, and there are two
    (§0.12). An **order-scoped** correction -- one whose repair changes what a given order's
    simulation produces -- is re-derived by `harness rescore --from-order A --to-order B
    --correction <ids>`, which writes `order_rescores` rows beside the originals. A
    **range-scoped** one -- a repair that changes which orders exist at all -- is re-derived by
    `harness replay --from-run A --to-run B --population range`, whose `replay = true` rows are
    the estimate. Either way the originals are never rewritten.
    """

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

# --- FILLED BY THE CONTROLLER AT THE RELEASE (Amendment 6, C1-C6; D11) ---------------------------
# Agents have no production access, so each of the six 6B corrections shipped with an empty
# config_hashes tuple and a "<filled at merge>" order/run range placeholder. Filled by the
# controller on 2026-09-15 00:50 CT from the Omarchy production database after the release of
# c1066b5 (full recipe, 00:23:44-00:26:34 CT; journal 218), the same pattern as
# VARIANT_IDS_C0/CONFIG_HASHES_C0, with the count assertions in the same commit
# (tests/test_corrections.py). `variant_ids` for C1-C6 stands at VARIANT_IDS_C0 unchanged: no
# variant config changed and the registered ids stand (Amendment 6's Change paragraph).
# `deploy_sha` and `code_version_after` are the one release sha for all six, because one deploy
# carries the whole amendment; `code_version_before` is C0's own `deploy_sha` (7c3d555), the
# last measurement-affecting production state before any 6B repair.
#
# The boundary is the release's stop instant, 2026-09-15T05:23:44Z: the last pre-release order is
# 10886 (fill 1878, order event 2562407, ledger 4) and the last pre-release recorder run is
# 17016 (17015 real, 17016 a skipped heartbeat); the first c1066b5 order is 10887 and the first
# c1066b5 run 17017 (docs/superpowers/autopilot/evidence/2026-09-15-release-boundary-6b.txt).
# The 23:22 CT capture (orders 10883) was superseded by the rolled-back 23:34 CT attempt, after
# which the old runtime ran another 50 minutes.
#
#   select distinct config_hash from orders where replay = false and config_hash not in (<C0's>);
#
# returned the three hashes below at 00:40 CT (252 orders, ids 10887-11138, all placed by the
# c1066b5 executor). One tuple serves all six corrections: the amendment is one deploy.
CONFIG_HASHES_6B: tuple[str, ...] = (
    "29d26852169bc0b1057de891f2ad73a7cc5c1d35f59ee5669da5fc1ccd063adb",
    "68718edb44fceeb2a1f0adbb092c3deb132069f87fc62d3e095c1e71ca8f7775",
    "ab68a5e5525e8b57b5036306645a12663df82b2d218d2ac3e0ab7ddbc272c414",
)
CONFIG_HASHES_C1: tuple[str, ...] = CONFIG_HASHES_6B
CONFIG_HASHES_C2: tuple[str, ...] = CONFIG_HASHES_6B
CONFIG_HASHES_C3: tuple[str, ...] = CONFIG_HASHES_6B
CONFIG_HASHES_C4: tuple[str, ...] = CONFIG_HASHES_6B
CONFIG_HASHES_C5: tuple[str, ...] = CONFIG_HASHES_6B
CONFIG_HASHES_C6: tuple[str, ...] = CONFIG_HASHES_6B
# ----------------------------------------------------------------------------------------------

# --- FILLED BY THE CONTROLLER AFTER THE AUDIT RUN ---------------------------------------------
# `harness audit-order --capsule <dir> --order 157` on the real 6A capsule, in the quiet window.
# Agents have no NAS access and never run it, so this ships as the unrun state and its test
# asserts only that it is one of the four verdicts plus that state (`tests/test_corrections.py`,
# under §1.11). 6C's t13 reads this constant inside the container, where `docs/` is absent (D9);
# the undated record is `docs/superpowers/reviews/order-157-audit.md`.
ORDER_157_VERDICT = "unverifiable_differs"  # run 2026-09-14 17:38 CT on the committed
# order-157 capsule, with the manifest gate scoped to the resting interval (amendment 0.16,
# journal 210): the six gap slices all lie outside the interval, the replay ran and differs
# (63.92 filled / 0.00 queue against the recorded 38.92 / 0.0), and none of the three hypotheses
# was met -- the "differs and no hypothesis" definition, not the gated one. Spec amendment 0.18
# (journal 224 item 14) splits the old single `unverifiable` string into `unverifiable_uncovered`
# and `unverifiable_differs`; this run is the latter. See the audit document's Result.


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
    Correction(
        id="C1",
        title="Subscription continuity: the per-book seq check read multiplexed interleaving as a lost frame",
        code_version_before="7c3d555",
        code_version_after="c1066b5",
        measurement_version_before="4.4",
        measurement_version_after="4.5",
        deploy_sha="c1066b5",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C1,
        affected_order_id_range="> 10886",
        affected_run_id_range="> 17016",
        eligible_measurements="post-boundary book_source/dirty_minutes classifications, read from the subscription's own gap rows",
        excluded_measurements="pre-boundary book_source/dirty_minutes classifications, which counted ordinary interleaving as dirty",
        rescore_command="harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]",
    ),
    Correction(
        id="C2",
        title="Recovery anchoring: the print floor was not anchored with the queue",
        code_version_before="7c3d555",
        code_version_after="c1066b5",
        measurement_version_before="4.4",
        measurement_version_after="4.5",
        deploy_sha="c1066b5",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C2,
        affected_order_id_range="> 10886",
        affected_run_id_range="> 17016",
        eligible_measurements="post-boundary filled_contracts, anchored with the queue on both re-anchor branches",
        excluded_measurements="pre-boundary filled_contracts on any order that recovered from a dirty stretch",
        rescore_command="harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]",
    ),
    Correction(
        id="C3",
        title="Trade and decrement reconciliation: a print and its own delta moved the queue twice",
        code_version_before="7c3d555",
        code_version_after="c1066b5",
        measurement_version_before="4.4",
        measurement_version_after="4.5",
        deploy_sha="c1066b5",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C3,
        affected_order_id_range="> 10886",
        affected_run_id_range="> 17016",
        eligible_measurements="post-boundary queue_remaining, reconciled against a trade inside its own horizon",
        excluded_measurements="pre-boundary queue_remaining and traded_at_price; the two are not comparable across the boundary, and traded_at_price is null afterwards",
        rescore_command="harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]",
    ),
    Correction(
        id="C4",
        title="Expiry clamp and rejected-signal placement",
        code_version_before="7c3d555",
        code_version_after="c1066b5",
        measurement_version_before="4.4",
        measurement_version_after="4.5",
        deploy_sha="c1066b5",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C4,
        affected_order_id_range="> 10886",
        affected_run_id_range="> 17016",
        eligible_measurements="post-boundary fills clamped to the order's own expiry, and skip-reason counts re-attributed ahead of capacity",
        excluded_measurements="pre-boundary fills stamped after their order's expiry, and pre-boundary skip-reason counts, which C4 re-attributes",
        rescore_command="harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]",
    ),
    Correction(
        id="C5",
        title="Dirty-time scope, observation coverage and counterfactual backoff",
        code_version_before="7c3d555",
        code_version_after="c1066b5",
        measurement_version_before="4.4",
        measurement_version_after="4.5",
        deploy_sha="c1066b5",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C5,
        affected_order_id_range="> 10886",
        affected_run_id_range="> 17016",
        eligible_measurements="post-boundary dirty_seconds/dirty_minutes scoped to the watched resting interval, with the counterfactual's own nw_dirty_seconds column and a bounded backoff on unreadable tickers",
        excluded_measurements="pre-boundary dirty_seconds/dirty_minutes on any cancelled or expired order",
        rescore_command="harness rescore --from-order <a> --to-order <b> --correction C1,C2,C3,C4,C5 [--limit N] [--resume]",
    ),
    Correction(
        id="C6",
        title="Capacity-equivalent baseline replay",
        code_version_before="7c3d555",
        code_version_after="c1066b5",
        measurement_version_before="4.4",
        measurement_version_after="4.5",
        deploy_sha="c1066b5",
        variant_ids=VARIANT_IDS_C0,
        config_hashes=CONFIG_HASHES_C6,
        affected_order_id_range="> 10886",
        affected_run_id_range="> 17016",
        eligible_measurements="a range replay under the executor configuration in force over that range, sharing one capacity counter",
        excluded_measurements=(
            "pre-boundary single-variant replay counts as a baseline for a shared-capacity live "
            "loop. The population is resolved from the placed-order chain of the range, spec "
            "§1.6's parenthetical '(config_history by the range's runs)' not being "
            "implementable (config_history has no run column); that population is a lower bound "
            "on the executed set, because a configured variant that placed nothing in the range "
            "is invisible to it. A per-run exact comparison is out of scope."
        ),
        rescore_command="harness replay --from-run <a> --to-run <b> --population range",
    ),
)


def as_json() -> dict:
    """The manifest as `harness manifest` prints it and 6C's report reads it."""
    return {"manifest_version": MANIFEST_VERSION,
            "measurement_version": measurement_version(),
            "corrections": [asdict(c) for c in CORRECTIONS]}
