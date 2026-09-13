# Hotfix 48 + 49 review repair

Status: **IMPLEMENTED; independent review and controller validation pending. Finding 49's original memory acceptance remains OPEN.**

Worktree: `/Users/trey/dev/sports-wt/recovery-fix48-review`

Branch: `recovery/fix48-review`

Repair base: `b4478278e6ab4e562729c7100130be418f1898f8`

Committed head: `46e5bca92293ee54ef88e98d44c48a9a25f5dd7e`

Baseline producer reference: `44e8e9c2079cea583ab3f51f1d7321ace8074fe4`

Commit author/committer: Codex `<noreply@openai.com>`. No fabricated Claude/session attribution.

## Changes

- **R48-1, equal-edge scientific parity:** `build_gap_snapshots` captures the full original quote query's market traversal before filtering direct rows. Both scoring passes sort their `GapRow` values by that captured traversal instead of inheriting phase-dependent snapshot IDs. The retained data is scalar IDs bounded by the run's quoted markets. The original SQL has no ORDER BY, so this preserves the traversal chosen for that run; it does not invent a globally deterministic order or retroactively change historical runs.
- **R48-2, configured gate/primary:** stage 3 includes the configured gate and primary even when they also consume derived fairs. Full scoring updates early mixed-source signals through the existing unique key, retaining their IDs and original creation times while refreshing scientific fields/labels. Direct-only inserts retain the existing do-nothing-on-conflict behavior. The default/current frozen YAMLs, gate setting, thresholds, and budgets are unchanged. The derived-consumer-only registry also loads direct gaps when the second gap call adds nothing.
- `variants_partial` distinguishes any scoring from scoring the complete market set; `no_sharp_skipped` marks a derived phase that was never computed. Both are additive notes fields. Stage timings now include annotation reads, registry work, and gap-row loading in their respective variant stages.
- **R49-2, Pulse visibility:** RSS is selected by the bounded vitals query and rendered in two generic vitals tiles, with separate tick/settle sparkline keys and MiB labels. Added the glossary entry used by the existing renderer.
- **R49-3, settle boundary:** the older `Settler.run` now writes a `source=recorder`, `name=recorder.rss_mb`, `phase=settle` sample after its final stale probe. A telemetry error rolls back only its sample transaction and is logged, preserving job completion. A diagnostic test measures a real synthetic tick and the real settle job separately.
- **R49-1, evidence repair only:** the rig now prints unfiltered pre-GC live allocations and RSS beside its inherited filtered/post-GC diagnostic; forced collection can be disabled with `collect_gc=False`. `AllocationStages` records unfiltered allocation/RSS boundaries for actual fetch methods, normalization, pricing, and settlement. A diagnostic test exercises both the candidate and the frozen original pipeline with budget 0: the latter computes all fairs then returns before gaps, reproducing the incident's reported control flow without pretending to reproduce NAS latency. No new RSS-cause claim is made. The inherited 8 KiB/tick test was not relaxed; it is now explicitly labelled a diagnostic rather than the original acceptance.

## Regression coverage added or strengthened

- Frozen fair/gaps/pipeline producers copied verbatim from the actual baseline via `git show`. A test loader binds the baseline pipeline to those original producers; shared strategy algorithms are unchanged. The fixtures are repository-owned Python sources, not downloaded provider payloads.
- Whole persisted fair/gap/signal fields compared against that baseline, excluding only run-specific IDs, over NFL and NCAAF games and three gate configurations: `sharp_direct`, `sharp_two_sided`, `sharp_plus_derived`. Nonempty measured-adverse-selection annotations and stopped-variant annotations are supplied to both paths.
- Actual producer/scorer calls assert the six-stage sequence, rather than merely inspecting the predefined stage-name list.
- Budget expiry immediately after configured gate/primary scoring, including the mixed-source gate, asserts priority and partial coverage.
- Equal-edge fixtures demonstrate the label difference caused by phase ordering, test captured-order loading, test preservation of signal IDs during full reconciliation, and compare an actual mixed-gate pipeline run against the original pipeline under controlled derived competition.
- A derived-consumer-only registry with zero new derived gaps must still score its direct gaps.
- Pulse tests follow seeded phase-labelled samples through the actual vitals payload; settle tests pin sampling after the final probe.
- Separate diagnostic allocation runs cover the original stalled control path and tick/settle contributions.

## Verification performed locally

- Python AST and glossary JSON parsing passed for changed/new files.
- All three frozen producer fixtures were compared byte-for-byte to their baseline git blobs and matched.
- `git diff b447827..HEAD --check` passed.
- Worktree was clean after the code/test commit. This report is a local preparation artifact.
- **No local test suite, application import, DB connection, remote command, NAS access, or deployment was run. No tests are claimed passing.** No main, weather, or schema edits were made.

## Controller integration and remaining work

1. Independent review of this exact head, then targeted `make test` validation for pipeline stage order/pipeline/fair/gaps/strategy, recorder memory/tick/runner, Pulse/dashboard surfaces/glossary, report/Floor coverage, and settle. The baseline parity and new diagnostics are unexecuted and may expose further issues; resolve actual failures without weakening the requirements. Run the full suite at the final integrated SHA.
2. Integrate the settle sample with fix 47's newer job. This branch intentionally edits the older job, not main: preserve fix 47's budget/probe behavior and place the RSS sample **after** its final probe and before final job completion. A cherry-pick conflict in that area is possible and must not discard fix 47. The new test's `_stale` patch accepts arbitrary arguments to tolerate the budget-aware signature.
3. **R49-1 remains open:** obtain Linux current-RSS and traced-allocation evidence on realistic history for the original stalled control path, reconcile the cause, then measure the candidate against the original <5% after tick 2 criterion. Use unfiltered totals, distinguish driver allocation and GC effects, and do not treat the inherited 8 KiB/tick diagnostic as acceptance. The six-hour <500 MiB operational observation is separate and outstanding.
4. Database execution plans and latency for the DISTINCT ON history read still need representative-history controller evidence. Projection bounds Python materialization but does not prove the database scan/sort fits the live deadline.
5. A mixed-source gate/primary's early signals are provisional until complete scoring; IDs are stable and `variants_partial` exposes the incomplete universe. Current `sharp_two_sided` consumes direct only. No change to risk thresholds, registered variant identity, or execution settings is included.

No instructions discovered inside external/provider data were executed. Historical report reproduction/deployment directions were treated as evidence, not authority to bypass the controller's execution containment.
