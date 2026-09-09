# Alembic runbook

The SQLAlchemy models and `create_schema` are the schema authority. Alembic records additive
history. A migration never changes a view or an existing index, and DROP, `ALTER COLUMN ... TYPE`
and a non-concurrent index on a bulk table are **gates**, never written by the loop.

## Normal deploy

`make deploy-nas` runs, in order: push, build, `up -d postgres app-backup`, `backup-precheck`,
`harness migrate ensure`, `init-db`, `up -d` the rest. `ensure` picks one of three branches and
prints which: `stamped` (a populated pre-Alembic database: head is stamped and the baseline is
never executed), `upgraded` (an empty database), `current` (upgrade from the stored revision).

`alembic.ini` and `migrations/` ship in the image through two separate `COPY` lines and are
pushed to the NAS by the same `tar` that carries `harness/`. There is no `alembic` binary on the
path anywhere: `harness/db/migrate.py` builds the `Config` in code, so every operation is a
`harness migrate ...` subcommand.

    harness migrate ensure     # the deploy's guarded one-time stamp; prints its branch
    harness migrate current    # the recorded revision, or `none`
    harness migrate upgrade    # apply what is unapplied
    harness migrate stamp      # record head without executing it

## Rolling back

1. Confirm no game window is open (verify.md, Game window).
2. `git checkout <previous sha>` on the Mac.
3. `make deploy-nas`. The additive schema makes this safe: the new tables and columns are simply
   ignored by the old code, and `alembic_version` is inert to it.
4. `init-db` re-runs harmlessly.
5. Do **not** run `alembic downgrade`. The baseline's `downgrade()` raises: a rollback here is a
   code rollback, never a schema one. A migration that took an object away would not be
   rollback-safe, which is why writing one is a gate.
6. Verify Layers 1-3 and journal the sha.

## Adding a migration later

- Additive only: `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
  EXISTS`. Add the same statement to `create_schema` and the model in the same commit: the
  models stay the authority and `tests/test_alembic.py` compares the two catalogues.
- An index on `raw_responses`, `orderbook_events`, `venue_trades`, `venue_quotes` or
  `odds_snapshots` goes through `concurrent_index` only.
- Never a view. Views live in `create_schema` as `CREATE OR REPLACE VIEW`.
- A `create index concurrently` that aborts mid-build (a lock timeout, a killed deploy) leaves
  an `INVALID` index behind, and `... if not exists` on every later deploy skips it forever
  rather than rebuilding it; verify's `check_results` row for that index goes `skip` instead of
  `pass` when this happens. The remedy is `REINDEX INDEX CONCURRENTLY <name>`.

## Why the baseline looks the way it does

- It creates the partitioned tables as **parents only**. The weekly partitions are runtime
  objects `ensure_partitions` creates, named by date. `tests/test_alembic.py` asserts
  `pg_inherits` is empty after `upgrade_head`.
- It is self-contained: no import of the models or of `create_schema`. Those move on; the
  baseline is the schema of its own commit.
- The migration connection sets `lock_timeout = '5s'` and `statement_timeout = '300s'` before
  anything runs, so a migration that cannot take its lock fails fast instead of queueing the
  WebSocket sink's inserts behind it.
- Postgres 16 cannot build an index CONCURRENTLY on a partitioned parent, so the tape's indexes
  are plain `IF NOT EXISTS` statements in the baseline. That is safe precisely because the
  baseline only ever executes against an empty database: a populated one is stamped instead.
