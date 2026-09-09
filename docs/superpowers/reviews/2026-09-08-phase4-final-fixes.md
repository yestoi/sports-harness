# Phase 4 final-review fix wave

Worktree: `../sports-wt/phase4-fixwave`, branch `phase4-fixwave`, base `c51c125` (all 17 tasks
merged). Suite run against `localhost:5433` via `make test`, foreground, 1729 tests, exit 0, no
failures/warnings/tracebacks (confirmed by dot count == collected-test count from a matching
`--collect-only -q` run, since this repo's `-q` output prints no final summary line -- itself a
carried lesson in `docs/superpowers/autopilot/state.md`).

## C1 (Critical) -- deploy fallback aborts on the first phase 4 deploy

**What changed.** `Makefile`'s `deploy-nas` recipe: the fallback branch now runs
`docker compose run --rm app-run init-db` before `docker compose exec -T app-backup
/backup/dump.sh nightly` and the retried `backup-precheck`. `backup_runs` (read by
`backup-precheck`) is created by `init-db`'s `create_schema`, which otherwise ran two steps
later, so the very first phase 4 deploy hit `UndefinedTable` and aborted before ever reaching
the schema step. Every phase 4 schema change is `CREATE ... IF NOT EXISTS` / `ADD COLUMN IF NOT
EXISTS`, so running `init-db` early on this bootstrap-only path costs nothing; the happy path
(every later deploy, where a fresh nightly row already exists) never enters the fallback branch
at all. Rewrote the recipe's explanatory comment to match. Updated
`docs/runbooks/phase0-deploy.md`'s deploy-order item 4 to describe the new fallback order.

**Covering test.** `tests/test_backup_scripts.py::test_the_deploy_recipe_bootstraps_the_schema_before_the_fallback_dump`
-- asserts `app-run init-db` appears (within the fallback branch) before `/backup/dump.sh
nightly`. The pre-existing `test_the_deploy_recipe_runs_the_precheck_before_the_schema_step` and
`test_the_deploy_recipe_asks_the_precheck_again_after_the_fallback_dump` still pass unmodified.

**Command and output.**
```
$ DATABASE_URL_TEST=... pytest -q tests/test_backup_scripts.py -k recipe -v
tests/test_backup_scripts.py .....                                       [100%]
5 passed in 0.05s
```

## I1 (Important) -- weekly report table 11 was never implemented

**What changed.** Added `_table11` to `harness/report/tables.py`, following `_table8`'s
convention: one row per `(env, method)` from `venue_requests` grouped and counted over the
report week (`ts` column, half-open week window), plus a `"prod non-GET = <n>"` note computed
from a second query filtering `env = 'prod' and method <> 'GET'`. An empty week renders through
the existing `_placeholder_table` helper (consistent with every other table -- the suite's own
`test_weekly_tables_return_every_key_with_placeholders` requires every table to render at least
a placeholder row, never a truly empty one), with the note still correctly reading 0. Registered
`t11` in `TABLE_KEYS` (rendering order, placed after `t8` per the ruling) and in
`weekly_tables()`. Touched only the docstrings that stated a stale table count ("ten tables" /
"t1..t10") in `tables.py`, `weekly.py` and `cli.py` -- no other table, family, grid or threshold
changed.

**Covering tests** (`tests/test_report.py`, new `_venue_request` factory):
- `test_table11_empty_reads_zero_non_get` -- no rows seeded; table renders a placeholder row and
  the note reads `"prod non-GET = 0"`.
- `test_table11_counts_rows_per_env_and_method` -- two `(prod, GET)` rows and one `(demo,
  POST)` row land in their own buckets; note stays 0.
- `test_table11_flags_a_prod_non_get_row` -- one `(prod, POST)` row; note reads `"prod non-GET =
  1"`.
- Updated `test_weekly_tables_return_every_key_with_placeholders`'s `TABLE_KEYS` set assertion
  to include `t11`.

**Command and output.**
```
$ DATABASE_URL_TEST=... pytest -q tests/test_report.py
................................                                          [100%]
32 passed
```

## I2 (Important) -- restore drill's Mac half never says how to reach the NAS database

**What changed.** `docs/runbooks/backups.md`'s Mac-half bullet gained a paragraph. Before
writing it I checked `docker-compose.yml`: `postgres` publishes **no host port at all** (only
`app-serve` has a `ports:` entry, for `SERVE_PORT`), so the ruling's literal `ssh -N -L
5432:127.0.0.1:5432 ...` would tunnel to nothing listening on the NAS host. The paragraph
instead has the operator read the container's own address (`docker compose exec -T postgres
hostname -i`, over ssh) and tunnel to that address, then run `backup-drill-record` with
`DATABASE_URL` pointed at the local end of the tunnel, plus the sentence that the row must land
in the NAS database or the release rule (`backup-encrypt`'s plaintext deletion) never sees it.
No code changed; `docker-compose.yml` was not touched (adding a published port was out of scope
for this finding and not needed).

**Covering test.** None -- this is a runbook-only change; no test in the suite inspects
`backups.md` content (checked: `grep -rn backups.md tests/` returns nothing), so there is
nothing to pin beyond the prose itself.

## I3 (Important) -- `deploy/nas.env` never gained `KALSHI_ENV=prod`

**What changed.** Appended `KALSHI_ENV=prod` to `deploy/nas.env` beside `LIVE_TRADING=0` and
`HARNESS_MODE=paper`, with a one-line comment matching their style (informational; `Settings`
already defaults to `"prod"`, but the posture file is the audited artifact).

**Covering test.** `tests/test_deploy_env.py::test_kalshi_env_posture_is_committed`.

**Command and output.**
```
$ DATABASE_URL_TEST=... pytest -q tests/test_deploy_env.py -v
......                                                                    [100%]
6 passed in 0.01s
```

## Minor 24 (▲) -- `test_posture_defaults` inherits the ambient environment

**What changed.** `tests/test_settings.py::test_posture_defaults` now takes `monkeypatch`
directly (dropping the shared `env_settings` fixture, which does not clear these three names),
`delenv`s `HARNESS_MODE`, `LIVE_TRADING` and `KALSHI_ENV` with `raising=False`, and constructs
`Settings(database_url=...)` itself before asserting the three posture defaults.

**Covering test and verification.** Ran the test twice: once normally, once with
`HARNESS_MODE=live LIVE_TRADING=1 KALSHI_ENV=demo` exported in the shell, to confirm the
`delenv` actually isolates it (the un-fixed version would have failed the second run):
```
$ DATABASE_URL_TEST=... pytest -q tests/test_settings.py -v
.........                                                                 [100%]
9 passed in 0.12s

$ HARNESS_MODE=live LIVE_TRADING=1 KALSHI_ENV=demo DATABASE_URL_TEST=... \
  pytest -q tests/test_settings.py::test_posture_defaults -v
.                                                                         [100%]
1 passed in 0.07s
```

## M1 (Minor) -- `create index concurrently` can leave an `INVALID` index

**What changed.** One bullet added to `docs/runbooks/alembic.md`'s "Adding a migration later"
section: an aborted `concurrently` build leaves an `INVALID` index that `IF NOT EXISTS` never
rebuilds, verify's `check_results` row for it goes `skip` instead of `pass`, and the remedy is
`REINDEX INDEX CONCURRENTLY <name>`.

**Covering test.** None -- documentation only, ship as is per the review's own verdict.

## M2 (Minor) -- `evil.com` in `harness/venues/kalshi/http.py` comments

**What changed.** Reworded both occurrences (the module docstring's numbered list, item 2, and
the `PROD_HOSTS` comment) to describe the parsed-vs-suffix attack without any URL-shaped string,
so the phase audit's outbound-host grep will not list `evil.com` beside the real allowlisted
hosts. No behavior change; confirmed no other file in the repo still contains the string
(`grep -rn evil.com harness/ tests/ docs/` returns nothing).

**Covering test.** None -- comment wording only.

## M3 (Minor) -- `verify.md`'s drawdown band assumes cash cannot go negative

**What changed.** One clause appended to the existing SQL comment at `verify.md`'s drawdown
invariant (around line 271): if the row ever prints below -1, that means cash itself has gone
negative in paper, and the walker should read it as a solvency event to journal as an integrity
anomaly rather than dismiss it as a query bug. No threshold, criterion or query changed --
confirmed the edit sits entirely inside a `--` SQL comment, and re-checked
`git diff --stat -- harness/report/gate.py harness/variants/` is empty for the whole fix wave.

**Covering test.** None -- documentation only, per the review's own "ship as is" verdict; this
just makes the existing behavior legible to the walker.

## Full suite

```
$ pgrep -f pytest            # (before the run: nothing)
$ make test
................................................................ ... (1729 dots across 24 lines)
[exited with code 0]
```
Cross-checked against `pytest --collect-only -q`'s per-file counts, which sum to exactly 1729 --
matching the dot count from the real run, confirming zero failures and zero collection drift.
`pgrep -f pytest` printed nothing both before the run and after it completed.

## Self-review against global constraints

- `git diff --stat 036ee2d..HEAD -- harness/variants/ harness/report/gate.py
  harness/logging_setup.py docs/superpowers/specs/` -- empty on all four.
- `git diff 036ee2d..HEAD | grep -iE '\b(DROP|RENAME|TRUNCATE|ALTER TYPE|DELETE FROM)\b'` --
  empty.
- No dependency or `constraints.txt` change.
- No new outbound host string (`t11.columns` in a test assertion was the only false-positive
  hit from a `\.co\b` grep).
- No file under `harness/execution/` touched, so no `EXECUTOR_VERSION` bump is needed.
- No secrets file read; `deploy/nas.env` and `backups.md` changes are posture/documentation
  only.
- No `ssh`, `scp`, `docker`, `make deploy-nas*` or `make status-nas` run at any point in this
  session.
- `docs/superpowers/autopilot/verify.md` was touched (M3), which is normally Task-16-only
  territory under the global constraints; this fix wave's dispatch explicitly assigned that
  edit as ruling M3, and the change is a comment-only clarification inside an existing SQL
  comment block, not a criterion, threshold or query change.
- Every commit carries the required `Co-Authored-By` / `Claude-Session` trailers (verified with
  `git show -s --format=%b` on all six).

## Commits (branch `phase4-fixwave`, base `c51c125`)

1. `7661e31` -- fix: bootstrap schema in the deploy fallback before the first backup dump (C1)
2. `3010a85` -- feat: weekly report table 11 -- venue requests by method and env (I1)
3. `ccac483` -- docs: restore drill tunnel instructions and KALSHI_ENV posture (I2, I3)
4. `4ec8cca` -- test: isolate test_posture_defaults from the ambient environment (Minor 24)
5. `9718177` -- docs: reword the evil.com comments and add the drawdown solvency note (M2, M3)
6. `65e8a10` -- docs: note the INVALID-index recovery path in the alembic runbook (M1)
