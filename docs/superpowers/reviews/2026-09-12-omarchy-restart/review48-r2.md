# Independent revised fix 48/49 review

**Verdict: PASS for the bounded implementation repairs, pending controller runtime validation. Finding 49's original cause and acceptance remain OPEN; this is not acceptance of the memory fix or deployment approval.**

- Reviewed candidate: `21fe67e64d139c9453e66f3b32b44adfc6a9e8a5`, `/Users/trey/dev/sports-wt/recovery-fix48-review`.
- Original reviewed head: `b4478278e6ab4e562729c7100130be418f1898f8`.
- Repair before rebase: `46e5bca`.
- Integrated base: main `7577b36`.
- The repair patch IDs before/after rebase match: `cfcff5a87fb3ac64c2bdc2662f483787b2f05cf5`.
- Prior review: `review48.md` alongside this report, preserved unchanged.

Read the prior findings, roadmap row 48, repaired producers/strategy persistence, frozen baseline, consumer signal queries, actual Settler flow, Pulse snapshot/rendering hook-up, and memory diagnostics/tests. No edits to the candidate, local DB tests, SSH/SCP/Docker, deployment or live actions. Candidate worktree was clean; `git diff --check 7577b36..21fe67e` passed. The controller owns targeted/full Omarchy validation.

## Findings and disposition

No new Critical or Important code defect found in the bounded repair. Prior dispositions:

| Prior finding | Revised disposition |
| --- | --- |
| R48-1, P1: phase order changes equal-edge cap labels | Addressed in implementation; targeted and full parity execution still required. |
| R48-2, P2: mixed-source configured gate loses priority | Addressed, including same-signal label reconciliation and derived-only registry. |
| R49-2, P2: RSS not visible in Pulse | Addressed from phase-labelled rows through actual payload and tile rendering. |
| R49-3, P2: settle boundary/measurement missing | Addressed by the real Settler hook and separate diagnostic contribution. |
| R49-1, P1: original stalled-path memory cause and <5% acceptance unverified | **OPEN evidence requirement**, now accurately acknowledged rather than claimed solved. Controller must carry it explicitly. |

## Scientific parity and mixed-source priority

`build_gap_snapshots(..., phase="direct", market_order=...)` captures the unique market IDs from the full quote query before excluding non-direct markets. That restores the original gap insertion traversal rather than deriving strategy ties from phase-generated snapshot IDs. Both initial and full `_load_gap_rows` calls receive that order, and sorting occurs before `run_strategy`'s edge/input-order tie break. Rejected derived-source rows consequently observe the same previously accumulated cap state they would have observed in the original traversal. Quote traversal itself still lacks SQL ORDER BY, as in the baseline: the implementation preserves the traversal it actually captured for that run, not a promised universal cross-plan ordering.

The gate name and primary tier now qualify a derived consumer for early scoring, preserving their ordering in `pricing_order`. Later full-universe scoring uses bounded conflict updates for an already-scored derived consumer. The unique key remains run/variant/market/side/replay. Updates preserve Signal ID and created_at while refreshing all computed scientific fields, including labels and annotations. Direct-only early rows retain DO NOTHING because excluded-source rows consume no candidate state and cannot alter those early rows' outputs. Full scoring still inserts their previously absent derived/unpriced rejection rows. The registered mixed-source variant has `apply_caps: false`; its changing cap annotations do not alter candidate decisions or sizing. No registered YAML, threshold, pricing budget or gate rule changed.

I checked the downstream executor queries: signals are visible as soon as a variant commits; they do not wait for the run to finish. The revised repair therefore intentionally exposes provisional mixed-source cap annotations until full scoring updates them. `variants_partial` describes this at run-summary level. For the frozen registered configurations the mixed-source reconciliation changes annotations, while decision/sizing stay unchanged. Do not generalize this guarantee to a future mixed-source variant with decision-enforcing caps without reviewing that publication boundary.

`direct_rows` is now loaded even for a registry with only derived consumers, closing the earlier empty-list/no-new-derived-gap case. `variants_partial` and `no_sharp_skipped` distinguish any scoring from a complete universe and unavailable derived/no-sharp counts from actual zeros. Auxiliary registry/annotation/risk/gap-row loads are now inside the measured variant stage spans, so the added timing covers the previously omitted substantive work.

## Baseline and regression quality

The three frozen files under `tests/fixtures/pricing_44e8e9c` were compared byte-for-byte using Git blobs against their originals at `44e8e9c2079cea583ab3f51f1d7321ace8074fe4`: fair, gaps and pipeline all match exactly. `tests/pricing_baseline.py` binds the frozen pipeline to those frozen fair/gap functions, rather than accidentally importing both new producers into the reference. Shared strategy algorithms remain unchanged in the candidate.

The parity fixture now spans two games/sports, registered production variants, three gate choices, nonempty as-measured annotations and stopped annotations. It compares all persisted fair/gap/signal columns except the necessary run-specific identities, and compares counts/order. Tests cover an equal-edge direct/derived pair with an intentionally reversed traversal, unchanged Signal ID on reconciliation, direct-only rejected-row labels, early mixed-gate/primary scoring at the budget cutoff, derived-only registry, and a full pipeline reconciliation comparison against the frozen baseline. The stage test records actual producer/strategy calls in addition to checking the declared stage summary.

One remaining validation limitation: the forced scientific-tie pipeline test monkeypatches `_load_gap_rows` on both implementations, so it decisively checks reconciliation but not the entire SQL-traversal-to-captured-order wiring. The companion test exercises capture and sorting directly; the ordinary multi-game baseline test exercises the full pipeline. A database fixture that forces the tie and derived-before-direct quote traversal through the actual loaders would combine these proofs. This is not an observed code defect; do not overstate the existing forced-tie test as that single end-to-end proof.

Tests for every deadline boundary and for the second-loop cutoff remain less comprehensive than the prior review requested. Source inspection confirms the checks still apply between stages/variants and summaries retain partial variants; controller runtime tests must establish the tested branches rather than claim exhaustive boundary coverage.

## Settle/Pulse integration

`Settler.run` samples current recorder RSS after the final stale-unsettled probe, writes `phase=settle`, and catches telemetry failure with rollback before writing final job status. All settlement stages already commit independently; the added sample does not move or omit stage execution. Existing recorder tick samples remain `phase=tick`.

Pulse now selects recorder RSS and its phase, creates independent tick/settle sparkline lanes and latest-value tiles, and retains the common glossary technical key. The frontend's existing dynamic tile renderer consumes the payload; the RSS glossary/label entry is present. Tests seed both phases and pass them through the real `build_pulse`, and Settler tests assert sample timing after the final probe. The diagnostic additionally executes the real tick and Settler separately and observes both phases.

## Original memory finding stays open

The revised diagnostic now reports unfiltered pre-GC allocations and RSS alongside the inherited filtered/post-GC figures, offers a no-forced-GC mode, and labels the old 8 KiB/tick test as a diagnostic rather than the original <5% criterion. `AllocationStages` covers fetch/normalize/pricing boundaries and separate settlement work. The frozen original pricing path is invoked with zero budget to reproduce all-fairs-then-return-before-gaps control flow; that accurately tests which stage ran without pretending to emulate the original host's speed.

None of this establishes that fair-history gap loading caused the original RSS incident, because the recorded stalled ticks never reached gaps. The history projection is still a useful optimization when gap building resumes. A two-tick synthetic control-flow probe, forced-GC diagnostic, and passing slope bound cannot close the original cause or acceptance. Required controller evidence remains realistic Linux histories, current RSS and unfiltered allocations on the actual old/candidate paths, an unperturbed-GC comparison, and the separately promised production observation. Keep finding 49 open until that evidence meets the original criterion or the user/controller resolves the acceptance explicitly within authorization.

Some inherited comments still call the gap-stage optimization “Finding 49's measured cause”; read that as the measured synthetic gap-stage growth, not a new explanation of the recorded stalled tick. The revised top-level diagnostic and test-specific acceptance wording make the limitation explicit.

## Controller test requests

At this exact candidate (and then full suite at the final integration SHA), execute through `make test` on Omarchy:

```sh
PYTEST_ADDOPTS='tests/test_pipeline_stage_order.py tests/test_pipeline.py tests/test_fair.py tests/test_gaps.py tests/test_recorder_memory.py tests/test_settle.py tests/test_snap_pulse.py' make test
make test
```

Record baseline-vs-candidate persisted-row parity and all parametrized gate cases, not only row counts. Preserve the raw memory diagnostic output and the original-open qualification. Continue existing strategy/scientific digests, recorder/normalizer and report coverage checks through the full suite. Before performance acceptance, the projected DISTINCT ON history read still needs representative EXPLAIN/latency evidence; fewer Python objects alone do not prove that it fits the deadline.

Instruction-like data encountered: the frozen fixture README says not to update the fixtures, and the diagnostic docstring shows a direct-pytest command. They were treated as source/data statements, not authority to edit or execute. No such command was run.
