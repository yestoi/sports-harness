# Independent release tooling review

Reviewer: OpenAI Codex. Review scope: current main drafts of `scripts/release-omarchy.py` and `scripts/test-suite.py`, original Makefile deploy/test behavior, `docs/runbooks/omarchy-operations.md`, migration runtime inventory, journal 128's college-window exception, and actual health/recorder/variant-registration producers.

## Exact reviewed identity and verdict

- Release script SHA-256: `b3d0132568f741bce9858d38aa1ec14ea0f98e79f76e158648879789499dc20d`.
- Test-suite script SHA-256: `47ec6052473930fb606c01069b42e7cc3f44dfdeb4462aa06b5dc6d78ffb102e`.
- New tests SHA-256: `6ca94a94ecb26c5b9fee46fda3acfd4c31cc9a0ccd706ddf79b74c04af13ab7e`.

**Final verdict at this exact identity: APPROVE WITH NONBLOCKING OPERATING LIMITATIONS. No Critical or Important findings remain in the reviewed change.** The final registry-rollback correction was independently reread against the actual registry writer. The controller reports all 51 isolated tests passed on Omarchy in 0.20 seconds. This is code/control-flow approval; actual service/migration validation remains the controller's responsibility.

## Final rereview: variant registry rollback — fixed

The prior Important affected source `02423fafd20ab5efd8561d723f832e639ed2dd07a17f2811f84390f5879ade3b`: candidate `variants register` commits active-state changes, so reverting Compose/images alone left candidate variants active after a later promotion/startup/health failure. `harness/strategy/variants.py:161-230` retires/deactivates superseded rows, activates current configs, and prunes removed names; `harness/strategy/pipeline.py:193` consumes those active database rows.

The final release script initializes `variants_attempted = False` before the protected deployment stage and sets it True immediately before invoking the candidate registration. This correctly includes a command that commits and then exits unsuccessfully. In rollback, both previous Compose files are restored first. When registration was attempted, the script invokes `variants register` through `existing`, whose arguments point at those now-restored runtime Compose paths. That selects the previous image and its variant definitions. Only afterward does it issue rollback `up` for the old app services and check their expected stamps/images/recorder evidence.

The existing registrar can reactivate previous variant identities and retire candidate identities while retaining history. No schema/data drop is introduced. A previous-registry restoration failure remains inside the rollback failure handler and records `rollback-failed`; it cannot claim old apps were restored. Earlier stop/migrate/init-db failures that never attempted registration do not perform unnecessary registry writes.

`test_full_rollback_reactivates_previous_variants_before_restarting_writers` pins the prior-config registration before rollback restart. The controller's final 51-case pass includes this case. Its command boundary is mocked, so the code inspection of the real registrar supplies the complementary evidence about persistent active-state semantics.

The final source also checks that the recorder query result is a dictionary before reading its build. A JSON null result becomes not-ready rather than raising AttributeError; empty textual SQL output remains a caught JSON decoding failure and is retried. Neither can authorize a healthy release.

## Earlier findings corrected by controller, checked in the frozen source

1. **Important: hidden pytest filtering could attest a full suite.** The original runner inherited `PYTEST_ADDOPTS` while recording only argv as scope. Runner now records `pytest_addopts` and `pytest_plugins`; release requires both present and exactly empty, exact clean before/after HEAD, empty explicit scope, and success. Legacy receipts lacking metadata, dirty runs, filtered runs, and stale SHA receipts are rejected.
2. **Important: serving stamp alone did not prove deployed recorder/images.** Readiness now compares each changed container's actual image ID and BUILD_SHA against the candidate mapping and requires a completed recorder row on the expected serving SHA. It reads real `ok` or `skipped` rows; the latter are necessary for legitimate quiet-hour heartbeats. An old image with a newly stamped environment cannot pass by comparing against its own declared old image tag.
3. **Important: rollback could falsely report restoration.** Old health is now checked before `failed-old-apps-restored`. The entire rollback, including file restoration and restart, is inside failure handling; failures record `rollback-failed` with separate original/rollback error fields. Per-service baseline image/build mappings preserve the legitimately older app-ws stamp after prior app-only releases.
4. **Important: same-SHA/tag rebuild could destroy rollback identity.** Same currently deployed SHA is rejected, and an already existing candidate release tag is never rebuilt. Prior app image pins are retained; no pruning is introduced.
5. **Important: registration/alias refresh semantics were lost.** Full release restores required variant registration and warning-only team seeding from the old Makefile. Variant and matching changes now require full mode. Registry rollback is also corrected as described above.
6. **Important: mounted backup assets would be silently ignored.** Dockerfile does not copy runtime-mounted `deploy/backup` scripts, and this tool promotes only Compose files. It now rejects backup scripts/public-recipient changes outright for a separate infrastructure release; merely classifying them as full was insufficient.
7. **Important: expected stale health blocked repair releases.** `/healthz` can legitimately return 503 with a known build when recorder health is stale. `health()` now parses that 503 body while requiring a nonempty stamp; other HTTP failures remain errors.
8. **Important: SIGTERM/SIGHUP bypassed Python rollback.** Main now converts catchable termination signals into SystemExit and restores prior handlers afterward, letting the protected deployment section unwind through rollback.
9. **Runtime config drift:** paper mode, disabled RFQ, and Omarchy's 600GB capacity budget are checked before and after candidate rendering. PostgreSQL and backup service rendered configurations must remain identical; app-only mode also requires app-ws config equality. Runtime `.env` is not regenerated or edited. Existing PostgreSQL digest pin is carried forward.

## Tests added and containment

Only the authorized new file `tests/test_omarchy_release.py` was written by this reviewer; source changes were made by the controller. No commit was created. It contains 51 parametrized cases across 27 test functions (the final registry-rollback case was added after the controller's first 50-case run).

Tests use importlib, temporary directories, fake command runners, fake HTTP and SQL, and deterministic readiness clocks. Every subprocess boundary in these tests is mocked. Test-runner lock paths are explicitly redirected via `SPORTS_TEST_STATE_DIR` into each temporary directory so nested runner tests cannot acquire or overwrite the controller's actual suite lock/receipt.

Coverage includes app-only WS image/stamp/config/environment preservation; partial stop; migrate/init-db/registration failure; partial two-file promotion; startup and health failure; rollback-copy/up/health failure and truthful receipts; original versus candidate registry restoration; stale/dirty/partial/failed/legacy/filtered test receipts; Chicago day-boundary college exception and NFL/full-mode rejection; build crossing a game-window boundary; expected image/env/tick evidence and quiet heartbeats; inherited lock descriptor and nonzero pytest exit receipt; SIGTERM/SIGHUP handler dispatch without sending real signals; backup asset rejection; 503 known-build recovery; DB budget pin; alias refresh; seed warning semantics.

Local validation: Python AST parsing passed; the final release, runner, and test file hashes above were verified from disk. No local pytest or database test was run. The controller reported the final isolated Omarchy run against the corrected release script: **51 passed in 0.20 seconds**. This supersedes the earlier 49-pass/1-seed-warning-failure preliminary run. The result is attributed controller evidence, not a locally reproduced run; retain the exact command/output in the controller's execution record. No Docker, PostgreSQL migration, production deployment, or runtime rollback pass is inferred from mocked tests.

## Nonblocking limitations and operating boundaries

- The suite's default state directory provides one shared lock for the intended single Omarchy account; changing `SPORTS_TEST_STATE_DIR` creates another lock/receipt namespace. Release currently reads the default namespace only. Standard release-attesting `make test` must therefore use the default shared directory. The override is used only for isolated tests here.
- SIGKILL, power loss, or storage failure can interrupt between the two atomic file replacements. No two-file atomic transaction is claimed; preserved previous files and receipt status are recovery evidence. Automated continuation from arbitrary incomplete receipts is not implemented.
- Initial serving stamp must remain readable. A total app-serve outage needs separate controller recovery; this tool handles a known-build stale/error response, not reconstruction of an absent stamp.
- The tests deliberately mock Compose rendering, Docker, HTTP, SQL, and actual process behavior. They establish Python decision/control-flow contracts, not real service convergence, actual mount/environment correctness, backup validity, or PostgreSQL migration safety. The controller owns those runtime checks.
- Operations documentation still describes a reviewed future procedure rather than fully documenting these new commands/receipt recovery; update it before handing the tool to an autonomous controller. Existing daily failure/wave limits remain controller duties, not newly implemented script counters.

No SSH, SCP, Docker, production SQL, or network command was executed by this reviewer. Instruction-like historical deployment commands in project documents were treated as review evidence, never executed. No secrets were read or printed; mock environment content is synthetic.
