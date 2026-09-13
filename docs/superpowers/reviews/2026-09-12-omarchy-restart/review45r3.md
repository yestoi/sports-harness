# Independent review: fix 45 round 3

Reviewed exact head `5e0d1626eca96d7f2a2ebecb4a82d3bba069e837` against base `003375ebd9d09fd8b010879ef695b108b185f6f5`, read-only in `/Users/trey/dev/sports-wt/recovery-fix45-round3`. Reviewer: OpenAI Codex. Original review, both rereviews, current implementation report, actual helper/callers/producers, and current tests were inspected. Implementation claims were checked against code rather than accepted as evidence.

**Exact verdict: APPROVE WITH KNOWN MINOR; no Critical or Important findings remain in the bounded round-3 change. Controller PostgreSQL 16 targeted/full-suite results remain required before operational closure.** This is a code-review verdict, not a runtime test pass or deployment approval.

## Findings by severity

**Critical — previous repaired-child/invalid-parent wedge: FIXED.** `harness/db/schema.py:535-551` now checks parent validity after every repair/build pass, including when both work lists are empty. It reattaches an existing valid child through `_retry_once`, then explicitly raises if the parent remains invalid. The former empty-work-list `continue` is gone. The initial valid-parent skip at `:490-492` remains intact.

**Important — none identified in this change.** No recorder cadence, timeout constant, read predicate, index definition, migration identity, model metadata, or producer is changed by round 3.

**Minor — inherited concurrent-REINDEX artifacts remain (known, nonblocking for this bounded repair).** `harness/db/schema.py:511-514` retries REINDEX without identifying/removing orphan `_ccnew`/`_ccold` objects left by interrupted attempts. Later success can leave an unattached invalid artifact, potentially consuming disk and write work. `_attached_child` selects attached indexes by parent/partition, so such artifacts do not conceal the repaired child or reopen the parent-invalid wedge. This limitation existed before round 3 and is explicitly retained in the implementation report. The PostgreSQL 16 documentation confirms failed concurrent reindexes can leave these artifacts, including numbered suffixes, and distinguishes their recovery meanings. [PostgreSQL 16 REINDEX](https://www.postgresql.org/docs/16/sql-reindex.html). No unsafe name-based production deletion was added; separately scoped cleanup remains appropriate.

## Independent verification of parent validation

I read PostgreSQL 16.15's `ATExecAttachPartitionIdx` directly. Its already-attached branch calls `validatePartitionedIndex` when the parent is invalid (`tablecmds.c`, lines 18299-18305 in the fetched source). The validator counts valid attached children against the table's partitions and marks the parent valid when complete. Thus reattaching any already-valid attached child after the repair loop is sufficient; it need not be the repaired child. [PostgreSQL REL_16_15 tablecmds.c](https://raw.githubusercontent.com/postgres/postgres/REL_16_15/src/backend/commands/tablecmds.c).

That supports the selected mechanism independently of both implementer prose and the earlier review's claimed scratch-database experiment. I did not reproduce that experiment locally.

## State and retry audit

| Starting state | Current behavior | Assessment |
| --- | --- | --- |
| Parent already valid | Initial skip before any CREATE/REINDEX/ATTACH or timeout change | Preserves prior no-DDL behavior |
| Missing children, no attached invalid children | Concurrent build, child validity check, ATTACH; final parent verification | Clean construction and legacy resume preserved |
| Invalid unattached build leftover | Drop-if-invalid is inside the combined retried build callable; replacement must be valid before ATTACH | Prior retry Critical stays closed |
| Attached invalid child, other missing children | REINDEX first, then builds/attaches missing children, then verifies/revalidates parent | Mixed repair/build converges |
| Attached invalid child, all partitions already attached | REINDEX repairs child; explicit final re-ATTACH validates parent | Original round-2 Critical closed |
| Every child already valid, parent invalid from prior repair | No build/repair needed, but final re-ATTACH still runs and parent is checked | Former permanent empty-work-list wedge closed |
| Multiple attached invalid children | Repair loop processes every listed child before parent validation | Same final completeness check covers the set |
| No usable child or ineffective validation | Final invalid-parent check raises RuntimeError | Cannot silently claim success; no explicit zero-child regression added |
| New auto-named partition child | `_attached_child` checks actual parent/partition attachment, independent of generated name | Original first-review Critical stays closed |

The new ATTACH is inside the existing `try/finally` timeout scope (`schema.py:508-553`) and uses `_retry_once`. That helper retries only DBAPI errors with `55P03`, `57014`, or `40P01`; non-retriable failures propagate and the second attempt is unguarded. The combined drop/create callable remains inside its outer retry, while DROP retains its own bounded retry. An interrupted final ATTACH therefore either retries successfully or fails loudly; a later invocation still reaches validation even when all child work previously committed.

Both real entrypoints remain AUTOCOMMIT: `create_schema` (`schema.py:969-979`) and migration 0007's `autocommit_block` (`migrations/versions/0007_raw_events_lookup.py:76-78`). Migration timeout/lock setup was checked in `migrations/env.py:112-123`. Timeout restoration therefore is not blocked by a failed DDL leaving an aborted explicit transaction. A broken connection can still replace an in-flight exception during restoration; this is the previously documented marginal limitation, not a round-3 regression.

## Actual producers and readers checked

`Recorder._kalshi_events` writes each returned page under source `kalshi`, endpoint `/events` (`harness/recorder/tick.py:608-609`). `store_raw` stores the HTTP result's status and fetch timestamp (`harness/recorder/store.py:18-23`). `_load_events_cache` still reads the newest 200 successful pages ordered by raw id (`harness/normalize/runner.py:54-62`). The model and helper both declare `(source, endpoint, id)` (`harness/db/models.py:52-60`, `schema.py:320-322`). No filter or semantics change is hidden in round 3.

`ensure_partitions` creates actual table partitions using `CREATE TABLE ... PARTITION OF` (`schema.py:54-84`), allowing PostgreSQL's automatic index creation/attachment. `harness/db/partition.py` delegates weekly creation to that same function. The partition-aware attachment query continues to handle those names; it does not search merely for the recipe's preferred child name.

## Test audit

The new CREATE-boundary regression (`tests/test_schema.py:1118-1167`) runs real concurrent CREATE, marks its resulting catalog row invalid, and injects `OperationalError(QueryCanceled)` afterward. It explicitly checks two CREATE executions, one DROP, a new OID, validity, and attachment. This is correctly described as a deterministic injected failure with a real catalog leftover. Moving cleanup outside the combined retry would leave the stale OID and fail this test. The separate lock test (`:1171-1251`) now accurately claims DROP retry and asserts the blocked statement is DROP.

The repair test (`:1254-1324`) is parametrized for mixed repair/build and repair-only. The latter attaches all fixture partitions first, then invalidates both parent and auto-named child, so an unrelated fresh ATTACH cannot hide the defect. Parent validity is explicitly asserted after recovery.

The all-valid-children/invalid-parent test (`:1327-1379`) checks real revalidation without CREATE CONCURRENTLY/REINDEX/DROP, separately suppresses the ATTACH to prove final failure detection, checks timeout restoration on success/failure, repairs afterward, and asserts the next run issues no DDL. No direct contention injection is added for the final ATTACH; its common retry wrapper was inspected above.

The cleanup fixture (`:1002-1012`) rolls back the session, clears the deliberately invalid recipe state, and rebuilds. It is requested by all intentionally invalid-state tests modified here. The refuse-to-attach test's monkeypatch ends before cleanup (`:1409-1413`). Temporary future partitions retain `finally` cleanup. Sessions commit before concurrent work; only the intentional lock-blocker retains a blocking transaction. These changes close the prior test-coverage/hygiene Minors by inspection.

## Validation boundary and remaining action

Reviewed exact commit and clean worktree; `git diff --check 003375e..5e0d162` passed. No local pytest, PostgreSQL, Docker, SSH, SCP, deployment, or main code mutation occurred. The only requested workspace write is this report. Controller must run the targeted partitioned-index regressions and full suite on actual PostgreSQL 16 through `make test` using localhost:5433; runtime failures can supersede this code-review verdict. No live lock-duration or production query-plan claim is made here.

Instruction-like text encountered as data: previous review's suggested one-statement fix and PostgreSQL's suggested DROP recovery commands. They were treated as technical proposals, independently evaluated, and never executed or taken as authorization to broaden scope.
