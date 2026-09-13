# Fix 45 round 3 implementation report

- Task: bounded repair of fix-45-rereview-2 findings before resuming 6B.
- Branch: `recovery/fix45-round3`
- Worktree: `/Users/trey/dev/sports-wt/recovery-fix45-round3`
- Base: `003375ebd9d09fd8b010879ef695b108b185f6f5`
- Exact committed head: `5e0d1626eca96d7f2a2ebecb4a82d3bba069e837`
- Changed files: `harness/db/schema.py`, `tests/test_schema.py` only.
- Worktree clean after commit. Original branch preserved. No 6B edits, main code edits, NAS access, Docker, SSH, SCP, deployment, or DB execution performed.

## Findings addressed

**Critical: parent remains invalid after child recovery.** The helper no longer exits early when both work lists are empty. After child repairs/builds it checks the parent. If invalid, it selects an already attached valid child and retries `ALTER INDEX ... ATTACH PARTITION` to trigger PostgreSQL's parent validation. It checks the parent again and raises `RuntimeError` if recovery did not make it valid. This covers repair-only and all-valid-children/invalid-parent states. If there is no usable child, invalidity raises rather than silently succeeding. Already-valid parents retain the initial skip and issue no DDL. The recovery statement runs under the existing timeout and retry machinery; existing exception propagation and timeout restoration are unchanged.

**Minor 2: old retry regression only tested a DROP failure.** The old real-lock regression remains, renamed to describe DROP retry and strengthened to assert the failed statement is DROP. A separate deterministic CREATE-boundary regression executes the actual concurrent CREATE, marks its resulting catalog index invalid, then injects an SQLAlchemy `OperationalError` with psycopg `QueryCanceled` (`57014`). This is explicitly a simulated build cancellation with a real invalid leftover, not a claimed real interrupted build. It proves CREATE executes twice, DROP executes once after the injected failure, and the replacement has a different OID and is valid/attached. Moving drop outside the combined retry would fail this test.

**Minor 3: invalid shared fixtures persist.** A targeted cleanup fixture now restores the raw-events index after invalid-index tests, including the refuse-to-attach case and the new regressions. The disabled-drop monkeypatch is scoped to its assertion block and removed before restoration. Temporary partitions retain their existing `finally` cleanup.

## Coverage added or strengthened

- Attached-invalid repair test now runs both mixed build/repair and repair-only cases. Repair-only first attaches all existing partitions, then invalidates the auto-named child and parent, so another child's new ATTACH cannot hide the bug.
- All children valid / parent invalid: actual re-ATTACH must validate the parent without CREATE CONCURRENTLY, REINDEX, or DROP.
- Suppressed re-ATTACH: helper must raise while parent remains invalid and restore statement_timeout.
- Normal rerun after recovery remains a no-DDL skip.
- Combined-build retry and drop retry are separate tests.

## PostgreSQL 16 evidence

Checked the PostgreSQL 16.15 source for `ATExecAttachPartitionIdx`: its already-attached branch calls `validatePartitionedIndex` when the parent is invalid. The implementation uses that exact path, matching the review's PostgreSQL 16.15 experiment. Source: [PostgreSQL REL_16_15 tablecmds.c](https://raw.githubusercontent.com/postgres/postgres/REL_16_15/src/backend/commands/tablecmds.c).

The [PostgreSQL 16 REINDEX documentation](https://www.postgresql.org/docs/16/sql-reindex.html) confirms failed concurrent reindexes can leave invalid `_ccnew` or `_ccold` artifacts, including numbered suffixes. Those objects require distinct recovery handling.

## Validation and controller requests

Completed locally: Python AST parsing of both changed files, `git diff --check`, clean post-commit worktree check. No runtime or DB test pass is claimed.

Controller should run in the corresponding Omarchy checkout at the exact head above, through the repository Makefile (localhost:5433, isolated branch DB):

```sh
PYTEST_ADDOPTS='tests/test_schema.py -k ensure_partitioned_concurrent_indexes' make test
make test
```

The new PostgreSQL integration regressions are the requested runtime proof of parent recovery semantics. For an isolated fixture-hygiene check, run the refuse-to-attach case immediately followed by the already-valid skip case through `PYTEST_ADDOPTS` and `make test`; both should pass without relying on another test to sweep invalid leftovers.

## Remaining limitations / disposition

**Minor 1 remains: orphaned reindex artifacts.** No new production DROP behavior was introduced. Automatic deletion based on `_ccnew`/`_ccold` names or a partition-wide before/after catalog difference cannot safely establish ownership under concurrent DDL and would broaden destructive behavior beyond this bounded repair. Interrupted reindex may therefore retain invalid artifacts (some maintained by inserts), including artifacts from earlier runs. They require separately scoped identification and cleanup. The parent-critical repair does not mistake unattached artifacts for attached children. The controller explicitly agreed ambiguous pre-existing orphan cleanup may remain a documented Minor.

The source check targets PostgreSQL 16.15. The final validity guard also prevents silent success on a server that accepts re-ATTACH without revalidating. Catalog invalidity in regressions uses the project's existing superuser test-fixture technique, not production catalog writes.

## Instruction-like text encountered as data

The prior review says, “The fix is one statement,” followed by a suggested ATTACH or validity assertion. PostgreSQL documentation recommends dropping named recovery artifacts. These were treated as technical proposals/evidence, not authorization to run SQL or broaden destructive cleanup. No instruction-like content in data was used to change scope or access.

Next action: controller executes targeted/full DB checks, then independent review of this exact commit. Fix 45 is not represented as verified/closed by this implementation report alone.
