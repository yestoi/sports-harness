# Proposed Qwen project packet: diagnostic suite timing report

Task ID: `QWEN-CANARY-01`. Status: **review draft, not dispatchable until prerequisites are filled**. Parent: [adoption review](2026-09-13-qwen-autopilot-adoption-review.md).

## Purpose and admission

Build a small manually invoked tool that displays durations already present in this project's suite logs. It establishes useful baseline visibility before test scheduling is changed. It is not a receipt validator, test runner, scheduler, release planner or scientific report.

Producer: [`scripts/test-suite.py`](../../../scripts/test-suite.py) prints an initial JSON record and a final record around pytest. It acquires the shared lock and provisions the test database **before** recording `started_at`. Therefore these logs cannot measure queue or database-provisioning time. Initial consumer: the human/Claude controller reading stdout; no runtime or automatic caller is added.

Current phase 6B Task 5 is excluded: it changes expiry/rejected-signal behavior and remains Claude-owned. This tooling task does not satisfy or reorder a roadmap milestone or its deadline.

## Preconditions to freeze before dispatch

- [ ] User/reviewer has approved this task scope, the external-route setup exception and a numeric cumulative budget; no unresolved usage remains.
- [ ] P1 isolation/budget/cancellation drills and independent trusted-boundary review pass.
- [ ] Controller records **actual base SHA**, packet hash/version, attempt ID and exact context inventory. Inspection base was `a2a179131099dbb9e8d0dc7f62e739fba0fbf0d3`, not a future checkout prescription.
- [ ] Controller creates the isolated task workspace and sanitized log fixtures; no protected host cache, other worktree or production access is granted.
- [ ] Independent Claude contract reviewer resolves the output/error rules below, freezes hidden acceptance outside worker mounts, and confirms the allowed files remain new/available at the actual base.
- [ ] An independent Claude source reviewer and the normal trusted acceptance path are available.

## Proposed allowed files

- New `scripts/suite-timing-report.py`.
- New `scripts/tests/test_suite_timing_report.py`, using standard-library `unittest` so standalone sandbox smoke needs no database or dependencies.
- New `docs/runbooks/suite-timing-report.md`, documenting only this utility's input/output and limitations.

No other edits. In particular: no Makefile, `scripts/test-suite.py`, `testdb.py`, release scripts, worker/supervisor/security/budget code, `.claude/`, roadmap/verify/state/journal, application modules, dependencies or receipt schemas. New directories may contain only the enumerated files. Do not leave cache files or generated reports in the candidate.

The test runner must be explicitly approved for this standalone no-DB smoke path. This is not authority to bypass the current project's `make test` gate; controller-side scoped/full acceptance remains separate and unchanged.

## Proposed frozen behavior

CLI accepts one or more explicitly named regular UTF-8 log files and emits one JSON document to stdout. No directory discovery, cache lookup, automatic service access, stdin commands, network, database, subprocess, Git invocation or file writes by the report utility. Standard library only. Reads may follow only controller-approved regular inputs; reject symlink/nonregular inputs. Total input limit 16 MiB across files; enforce while streaming, not only via a pre-read stat. Bound every physical line at 1 MiB; fail deterministically if either limit is exceeded.

Output schema: `schema_version: 1`, `purpose: "diagnostic_only"`, `runs: [...]`. Preserve the argument order; identify `source` by argument index plus basename, never emit full host paths. Each row contains `source_index`, `source_name`, observed `branch`, `head`, `started_at`, `finished_at`, `exit_code`, `runner_elapsed_us`, `queue_elapsed_us: null`, `database_provision_elapsed_us: null`, and `record_state` (`complete`, `incomplete`, or `invalid`) plus stable diagnostic codes. Unknown values are null, not zero. Use exact integer microseconds without float rounding; preserve timestamp strings while comparing timezone-aware instants.

Parser recognizes only top-level JSON objects whose fields identify the existing suite-runner record shape (`branch`, `head`, `started_at`; optional `pid`, `database`, `finished_at`, `exit_code`, etc.). Plain pytest output is ignored as data. A line beginning with `{` that is malformed JSON or a non-record JSON object must be counted in diagnostics (`malformed_json_line` or `unrecognized_json_record`) rather than executed or silently treated as a receipt. Diagnostics never contain the raw input line or arbitrary stderr/SQL strings. No `eval`, shell interpolation or terminal-control rendering.

One run per file: accept either one complete terminal record alone, or one initial record followed by its matching complete terminal record. If present in both, identity fields `branch`, `head`, `started_at`, `pid`, `database` must match: compare `started_at` as a timezone-aware instant, and other identity fields by exact value and type. Preserve the terminal record's timestamp strings in output when a terminal record exists. Duplicate initial/terminal records or mismatched identities are `invalid`; do not pick the newest/passing record. A start without a terminal record is `incomplete`. No recognized records is `invalid`. Malformed/unrecognized JSON diagnostics make the file `invalid` even if a plausible record appears later. Preserve an observed nonzero `exit_code` on a valid terminal record as `complete`; `complete` means the log has a terminal observation, **not that tests passed**.

Validate branch/head/start as nonempty strings; timestamps must parse to timezone-aware datetimes; terminal exit code must be an integer excluding bool; finish must not precede start. Malformed fields produce `invalid` with null derived elapsed. Do not derive missing timestamps from filesystem metadata, pytest text or current time. If an optional `head_after` differs from `head`, report `source_changed` as a diagnostic observation without declaring receipt validity; the elapsed remains calculable if the identity/timestamps themselves are consistent. Dirty/scope fields are not approval inputs and do not authorize reuse.

CLI exit 0 means every file has a well-formed terminal observation, regardless of observed test exit codes; exit 1 means at least one incomplete/invalid log; exit 2 means invocation, I/O, UTF-8, size or nonregular-input error. For exit 2, emit no partial JSON and use a stable error code on stderr without raw file contents. Empty argv is exit 2. The runbook must prominently explain these distinctions and prohibit treating the report's exit status as test/release approval.

No output field named `approved`, `deployable`, `eligible`, `receipt_valid`, `reusable`, or an inferred pass/fail verdict. Do not report queue wait as zero. Do not combine post-lock runner time with pytest's footer time, infer wasted capacity, modify receipts, or introduce a release consumer. A future optional pytest-duration field is a separate change, not part of this first packet.

## Independent acceptance, outside worker control

Use minimal fabricated fixtures plus controller-sanitized excerpts from actual producer logs. The public examples may explain the contract; additional cases must be independently derived before implementation and withheld from the worker. Sources are captured in [suite baseline evidence](../reviews/2026-09-13-qwen-autopilot/suite-baseline-evidence.json).

- `fix49-full-ebf0953.log`: runner elapsed **1,679,843,917 microseconds**, distinct from pytest's 1678.74 seconds. Queue/provisioning remain null.
- `t3-full-13f891b.log`: runner elapsed **1,554,424,044 microseconds**, distinct from pytest's 1553.27 seconds.
- Normal initial+terminal and standalone terminal; equivalent instants expressed with UTC offsets; exact microseconds and nonnegative zero duration.
- Explicit failed test exit (complete record, utility exit 0, observed nonzero code retained); incomplete run (utility exit 1, no fabricated finish); conflicting/duplicate terminal records.
- Invalid timestamps, naive timestamps, reversed time, bool exit, missing required fields, malformed JSON, unrelated JSON, no records, unknown optional fields and a changed `head_after`.
- Multiple files/order, Unicode basenames, same basename at different argument indexes, escaped control characters in fields, empty argv, invalid UTF-8, unreadable/nonregular/symlink inputs, file and line bounds including growth during read.
- Prompt-like log text remains data; stdout is valid JSON with no terminal-control side effects; no source/log writes, imports with side effects, subprocess/network/database access, or unexpected output artifacts.

Freeze the exact diagnostics/error precedence before dispatch; any ambiguity goes back to Claude rather than being invented by the worker. Unit tests written by Qwen are useful regression coverage, not the authority for these acceptance cases. Source review must verify the utility has no hidden caller or privileged effect.

## Delivery and acceptance

Worker returns the candidate files, actual smoke results, changed paths and unresolved concerns. Progress narration is distinct from the final response. The supervisor captures and hashes real artifacts and verifies input/file constraints; a success message alone is insufficient. Preserve the original attempt and permit at most one bounded repair, then explicit Claude fallback.

Claude independently reviews the frozen code and runbook. Controller runs the isolated held-out checks and all existing scoped/full acceptance required for eventual integration under the current suite slot. Running a tiny smoke does not establish a full-suite receipt. If the new unittest path is not collected by current project pytest configuration, invoke it explicitly through the approved acceptance runner and record that extra evidence; do not silently change collection policy in this task.

Measure worker time, controller preparation/handling, queue and trusted validation/review time, repair/fallback, full acceptance and integration separately. Unknown historic queue time stays unknown in the tool even though new supervisor instrumentation can measure future queues.

This packet authorizes no implementation by itself. A reviewed candidate does not auto-merge or auto-deploy; existing release policy determines consequences of any later `scripts/` change. Stop after reporting the supervised trial outcome if broader routing/activation is not explicitly approved.
