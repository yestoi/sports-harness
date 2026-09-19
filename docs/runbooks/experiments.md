# Experiments runbook

Phase 6D.1's execution-viability experiment (`harness exp ...`) is **exploratory**: it places
nothing at a venue, adopts nothing and is never a gate input. It reads the production tape and
writes only its own eleven `exp_*` tables and the hashed file tree under
`/srv/sports-harness/exp/`.

Two one-off steps are **the user's** and both are prerequisites of every run (addendum §4.6 step
0, §4.7, ruling C2): the secret file, then the database role. Until both exist every experiment
command **fails closed** with `IsolationError` at session open - that is the designed state, not a
defect.

## 1. The secret

On the Omarchy runtime, from the runtime directory (`/srv/sports-harness`), in the same shape as
`secrets/anthropic_api_key` (`docs/runbooks/research.md`):

```bash
printf '%s' '<password>' > secrets/exp_db_password && chmod 600 secrets/exp_db_password
```

`printf` rather than `echo`: **no trailing newline**. `secrets/` is git-ignored, so the file is
never committed. `scripts/release-omarchy.py` creates, copies and reads no secret, and a missing
file never blocks a deploy: Compose then binds an empty *directory* at the mount path,
`Settings.has_exp_db_password()`'s `is_file()`-and-non-empty test is false, and the experiment
stays dormant. `docker-compose.yml` mounts it read-only into `app-research` and into no other
service.

**The loop never reads or prints this value.** It tests `is_file()` and a non-zero size, reads the
file only inside `Settings.exp_database_url()` to build the connection string, and logs no URL.

## 2. The grant

Run once by the user against the `harness` database as its owner role (psql, on the host). The
`harness` application role cannot create a role, which is why this step is the user's:

```sql
CREATE ROLE harness_exp LOGIN PASSWORD '<the value in secrets/exp_db_password>';
GRANT CONNECT ON DATABASE harness TO harness_exp;
GRANT USAGE ON SCHEMA public TO harness_exp;
-- read everything, write nothing
GRANT SELECT ON ALL TABLES IN SCHEMA public TO harness_exp;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM harness_exp;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO harness_exp;
-- write only the experiment's own eleven tables (run after the migration creates them)
GRANT INSERT, UPDATE ON exp_run, exp_arm, exp_order, exp_fill, exp_allocation, exp_observation,
      exp_outcome, exp_book_health, exp_checkpoint, exp_mismatch, exp_limitation TO harness_exp;
GRANT USAGE, SELECT ON SEQUENCE exp_arm_id_seq, exp_order_id_seq, exp_fill_id_seq,
      exp_observation_id_seq, exp_outcome_id_seq, exp_book_health_id_seq,
      exp_mismatch_id_seq, exp_limitation_id_seq TO harness_exp;
```

**The order matters.** The `GRANT INSERT` / `GRANT USAGE, SELECT ON SEQUENCE` lines name tables and
sequences that exist only after this milestone's migration has run. Running this block before the
release simply errors on the missing objects; re-run it after the release and it completes. No
`CREATEDB`, no second database, no second backup target, no DROP and no change to any production
grant: `harness_exp` is a new role that can read everything and can write eleven additive tables.
The migration itself still runs as the owner role, so nothing about `alembic upgrade head`
changes.

## 3. Verification

```bash
docker compose exec app-research harness exp isolation-check
```

Before the **secret** is placed the expected result is a non-zero exit with `IsolationError: the
experiment secret /run/secrets/exp_db_password is absent or empty` - the fail-closed evidence.
With the secret in place and the role not yet created the expected result is a connection error
naming the missing role, `role "harness_exp" does not exist` (verify.md row 2's wording). After
the grant it prints `role=harness_exp`, `insert=False` on every listed production table and
`exp_run insert=True`.

The same read-back in psql (§3 row 2's privilege pair):

```sql
SELECT has_table_privilege('harness_exp', 'orders', 'INSERT');   -- expect f
SELECT has_table_privilege('harness_exp', 'exp_run', 'INSERT');  -- expect t (after the migration)
```

And `\du harness_exp` shows **no** `Superuser` and **no** `Create DB` attribute.

## Why a role and not a flag

`default_transaction_read_only` is a USERSET GUC: any statement could set it back, so an
in-process guard under the production role proves nothing after session open. The boundary is a
privilege one - under `harness_exp` the production tables are not a possible destination at all,
whatever statement the experiment issues. The in-process refusals in
`harness/experiments/execution_viability/storage.py` and the import tests in
`tests/test_exp_isolation.py` are defence in depth, not the boundary.
