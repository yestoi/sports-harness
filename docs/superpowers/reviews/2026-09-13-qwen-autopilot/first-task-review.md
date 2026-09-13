# First real-project Qwen task selection

Read-only review, 2026-09-13. No provider calls, credentials, project edits, operational actions or project tests. This memo proposes work for a review-ready adoption package; it does not dispatch or authorize a live-loop change.

## Recommendation

Use a new, manually invoked suite-timing report as the first real-project task. Keep the first canary outside every runtime, scheduling and acceptance decision. The next roadmap task, 6B T5, is unsuitable despite its current Sonnet implementation allocation.

T5 edits `harness/execution/loop.py`, `fills.py` and `plan.py`, changes expiry handling and rejected-signal placement, and removes scientific regression xfails (`.superpowers/sdd/2026-09-11-phase6b-repair-execution/task-5-omarchy-brief.md:47-63`). The resulting behavior determines which fills and placements are counted, precisely the correctness work called out by `docs/superpowers/autopilot/roadmap.md:364-379`. T5 stays with the existing Claude route and strong independent execution review. A short patch or lower existing implementer tier is not positive evidence for low-risk admission.

## Candidate 1: standalone suite timing report — recommended first

Purpose: make existing acceptance-run time visible without manually reading long logs. It helps establish the build-harness baseline before any scheduler or provider changes.

Proposed allowed files: new `scripts/suite_timing_report.py` and one new standalone stdlib test module under `scripts/tests/`. The exact test path and output contract should be frozen in the final packet. Do not edit `scripts/test-suite.py`, `scripts/testdb.py`, Makefile, worker tools, release scripts, `.claude/`, roadmap, or any application file.

Existing producer and evidence:

- `scripts/test-suite.py:20-30` waits for the suite lock, provisions the database, then timestamps execution. It prints the initial JSON object at line 32 and the final object at lines 47-52.
- `.superpowers/sdd/hotfix-2026-09-12-omarchy/fix49-full-ebf0953.log:171-172` contains the pytest footer and final JSON. Runner start/end difference is 1679.843917 seconds; pytest reports 1678.74 seconds. These are distinct observations, not contradictory measurements.
- `.superpowers/sdd/2026-09-11-phase6b-repair-execution/t3-full-13f891b.log:171-172` gives runner elapsed 1554.424044 seconds and pytest elapsed 1553.27 seconds.
- The shared suite is invoked by Makefile:175. This new tool has no existing caller; its initial consumer is the human/controller manually inspecting explicitly supplied copied log files. No Makefile, controller or deployment wiring belongs to this task.

Suggested bounded contract:

- Accept explicit local log filenames; never discover host caches or services. Controller supplies sanitized copies for development and review.
- Parse the known initial/final JSON shape among ordinary pytest output, one run per input file. Freeze behavior for duplicate/mismatched final records, incomplete runs, malformed JSON and missing timestamps before dispatch; report uncertainty explicitly rather than invent a duration.
- Output deterministic JSON or a table with source, observed branch/head, execution start/end, runner elapsed, observed exit code and completeness. If pytest elapsed is included, keep it in a separately named field.
- Queue wait and database-provisioning time are unavailable in this log format: timestamps begin after both. Show them as unavailable, never zero. Do not estimate a complete job lead time or wasted-time total from these fields.
- This is diagnostic display only. It must not emit deployable/approved/valid-receipt judgments, select a passing run, copy/write receipts, match candidates for release, change test classifications, or hide failed/interrupted runs. Input log text is untrusted data.
- Stdlib only, bounded input handling, stdout output only, no subprocess/network/database/process control. No automatic changes to existing evidence.

Independent acceptance examples should include the two arithmetic pairs above, an incomplete log with a start but no finish, an explicit failed run, timezone offsets, malformed final data and conflicting run identities. Test the new pure module using a standalone trusted runner; run all existing integration checks required for any eventual merge separately under the unchanged policy. Review actual source for writes/import side effects and ensure nothing imports the utility as a gate.

Why this is a real task rather than another toy: it consumes the project's current producer format and real historical artifacts, exposes a known nightly bottleneck, and must cope with partial logs. Its initial adoption still cannot establish that Qwen is suitable for multi-file application or scientific work.

## Candidate 2: navigation index for existing review diff packages

Purpose: reduce reviewer orientation time by listing changed files and hunk locations from controller-created unified diff artifacts. This is a lower-priority alternate if the timing reporter is already handled elsewhere.

Proposed allowed files: new `scripts/review_diff_index.py` and its new standalone tests only. Input is an explicitly supplied copied diff; output is stdout JSON with file paths, old/new hunk ranges and hunk labels. Start with the actual unified-diff grammar present in the supplied packages; unsupported quoted paths, binary sections or malformed hunks must be surfaced as unsupported instead of silently omitted.

Existing producer/consumers: `.claude/skills/autopilot/SKILL.md:123` assigns exact-SHA review-package creation to the controller; `references/phase.md:20-23` describes packaging unreviewed work and reviewing a shared finding diff once. Concrete inputs include `.superpowers/sdd/2026-09-11-phase6b-repair-execution/review-round2-e6975ab..13f891b.diff` and `.superpowers/sdd/hotfix-2026-09-12-omarchy/review-fix49-round3-0743911..ebf0953.diff`. The consumer is a human/Claude reviewer opening these files. There is no existing automated caller to modify.

Exclusions: no model/risk selection by line count, review verdicts, file-ownership admission, patch application, Git invocation, merge checks, prompt generation or changes to the source diff. Preserve unusual paths as data; never execute hunk labels or render unescaped markup. Independently verify counts/ranges against tiny hand-derived fixtures and a controller-supplied real package. Passing the index's tests never substitutes for reading the diff.

This candidate has less direct payoff than the timing report and must justify its saved review time; do not build it merely to increase the number of external-worker tasks.

## Admission and rollout constraints for the package

Freeze the selected task's base SHA, allowed files, producer examples, exact output/error contract and independent acceptance cases. Admit one external attempt at a time, retain Claude's controller ownership, use one bounded repair then Claude fallback, and record initial plus repair/review/validation time. All source remains a candidate until the current independent review and merge checks pass.

This package may explicitly propose the new tooling task within the user's requested harness-efficiency work. Do not relabel it as completion of 6B/6C/6D or alter roadmap acceptance. Broader provider routing, supervisor/security implementation, test-lock policy, receipt authority and deployment changes require their own Claude-owned reviewed changes. No canary should be attached to the active loop until its isolated runner and stop/cancel behavior have been separately accepted.
