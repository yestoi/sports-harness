"""fix 85 (docket item 22, the user's ruling of 2026-09-18): `ix_orders_intent`

Revision ID: 0014_orders_intent_index
Revises: 0013_nw_executor_version
Create Date: 2026-09-18

`harness/execution/store.py`'s `orders_for_intent` runs `select count(*) from orders where
intent_id = :i` once per placement, and `orders` carried no index on `intent_id`
(`orders_pkey`, `ix_orders_game`, `ix_orders_key_placed`, the partial `ix_orders_nw`,
`ix_orders_status`, `orders_client_order_id_key` and the partial `uq_open_order` are the seven
it had). Measured read-only on production at 06:50-07:10 CT on 2026-09-18, runtime 1a12781: a
Seq Scan, 121,880 buffers, 153.9 ms cold for one intent, over a 952 MB heap holding 36,685 live
rows after 105.7 M updates -- the 145 ms per paper order in `exec.phase_place_ms`, 21-22 s on
every 150-order wave. The ruling is one additive index, not batched placement writes.

Additive only (roadmap invariant 5). `harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL` carries
the identical statement (so `init-db` puts it on a populated database) and
`Order.__table_args__` declares it (so `create_all` gives it to a fresh one); all three must
land together or `tests/test_alembic.py`'s catalogue diff fails.

CONCURRENTLY, under fix 25's F65 rule as `0002_phase45.py`'s `ix_orders_key_placed` read it:
`orders` is not one of the five bulk tape tables, but the executor writes to it on its 15 s
loop, so a plain build would hold a ShareLock against every placement for the length of the
build. The statement is taken out of the migration's transaction with
`op.get_context().autocommit_block()` -- what CONCURRENTLY requires, and what
`migrations.env.concurrent_index` does internally. It is issued here rather than through that
helper for `0006_quotes_run_index`'s reason and one more:

* the statement has to be one module-level string identical to `harness/db/schema.py`'s copy,
  which is what `tests/test_alembic.py` compares to keep the two from drifting; and
* the user's build conditions put two more statements inside the *same* autocommit block (the
  raised `statement_timeout` before the build and its restoration after). The helper opens a
  block of its own, and Alembic 1.19.2's `MigrationContext.autocommit_block` cannot nest --
  entering it a second time finds `connection.in_transaction()` true (its own `fake_trans`) and
  trips `assert self._transaction is not None`, which the outer block has already set to None.

Why the connection's 300 s (`migrations/env.py:run_migrations_online`) is not enough, and why
the timeout is raised for this one statement only. `CREATE INDEX CONCURRENTLY` is not bounded by
the size of `orders`: it waits out every transaction that can see the table, twice -- once
before each of its two heap passes -- and the executor's expiry-cohort steps have run 184-253 s
on this database. Two of those end to end, plus the build itself, is past 300 s, and a
`statement_timeout` cancellation there is exactly what leaves the half-built index behind. The
raise is scoped: the previous value is read from `pg_settings` in milliseconds, set back in a
`finally` immediately after the statement (so a failed build restores it too), and the session
keeps `lock_timeout = '5s'` unchanged -- the release still fails fast rather than queueing
behind an orphaned backend.

The `indisvalid` check is the second build condition and the release's fail-closed point.
`create index concurrently if not exists` skips an index whose name is already in the catalogue
whether or not it is valid; that is fix 71's incident (deploy 2026-09-14 23:34 CT), where a
cancelled build left `ix_intents_market_created` invalid and a retry silently accepted it. So
this revision reads `pg_index.indisvalid` for the index it has just built and raises if it is
not true, which aborts the release instead of reporting success on an index no planner will use.

One failure reaches the release without reaching that check: a build cancelled while it waits
-- `canceling statement due to lock timeout`, fix 71's own error -- raises out of the build
statement itself, so `migrate ensure` aborts on the driver's error and the message below is
never printed. The `finally` still restores `statement_timeout` (verified in review by
cancelling a real build), and the index the cancelled build left behind is still in the
catalogue as invalid, so the controller reads `pg_index.indisvalid` for this index by hand
on any failed release before deciding anything, exactly as it does after the raise below.

What the controller does then, by the user's ruling ("An invalid index is a stop for me, not a
retry"): the index stays in the catalogue, invalid, and is journaled that way. Nothing in this
file repairs it. Note what would otherwise happen without the stop --
`harness/db/migrate.py`'s `heal_invalid_indexes` runs before every `migrate ensure` upgrade and
would REINDEX it non-concurrently, because `orders` is not on `migrations.env.BULK_TABLES` and
is not a partition of one; that rebuild takes an ACCESS EXCLUSIVE lock on `orders` under the
healer's 5 s `lock_timeout`. Neither `BULK_TABLES` nor the healer is changed by this fix. The
controller therefore reads `indisvalid` for this index before any further release, and the
cleanup is the user's decision, never the loop's.

`downgrade()` is `pass` (roadmap invariant 5, every revision since 0002): additive only, and
rolling back is a code rollback through the reviewed release procedure, never a schema one.

The id is 24 characters, inside the `String(32)` Alembic creates `alembic_version.version_num`
as (0012's docstring records the 33-character id that aborted every upgrade), and the file name
equals the revision id as all thirteen revisions before it do.
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0014_orders_intent_index"
down_revision: str | None = "0013_nw_executor_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The index this revision, `harness/db/schema.py` and `Order.__table_args__` all name.
INDEX_NAME = "ix_orders_intent"

#: The identical statement as `harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL` entry;
#: `tests/test_alembic.py::test_the_orders_intent_index_ddl_agrees_between_schema_and_migration`
#: compares the two Python string values, so neither copy can be edited alone.
_INDEX_DDL = ("create index concurrently if not exists ix_orders_intent "
              "on orders (intent_id)")

#: The user's first build condition: raised for the build statement and nothing else. Chosen
#: above two expiry-cohort waits (184-253 s each) plus the build, against the connection's 300 s.
BUILD_STATEMENT_TIMEOUT = "1800s"

#: `pg_settings.setting` for `statement_timeout` is in milliseconds, and a bare integer in a SET
#: is milliseconds too, so the restore puts back exactly what the connection had -- no unit
#: rounding through `show statement_timeout`'s '5min' spelling.
_STATEMENT_TIMEOUT_MS = "select setting from pg_settings where name = 'statement_timeout'"

#: The user's second build condition, read on the connection that built the index.
_INDISVALID = ("select i.indisvalid from pg_index i "
               "join pg_class c on c.oid = i.indexrelid "
               "join pg_namespace n on n.oid = c.relnamespace "
               "where n.nspname = current_schema() and c.relname = :name")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        previous_ms = int(bind.execute(text(_STATEMENT_TIMEOUT_MS)).scalar())
        op.execute(f"set statement_timeout = '{BUILD_STATEMENT_TIMEOUT}'")
        try:
            op.execute(_INDEX_DDL)
        finally:
            op.execute(f"set statement_timeout = '{previous_ms}'")
        valid = bind.execute(text(_INDISVALID), {"name": INDEX_NAME}).scalar()
    if valid is not True:
        raise RuntimeError(
            f"{INDEX_NAME} is not usable after CREATE INDEX CONCURRENTLY: "
            f"pg_index.indisvalid = {valid!r}. The user's ruling of 2026-09-18 makes an invalid "
            f"index a stop, not a retry, so this release fails closed here; the index stays in "
            f"the catalogue as it is and the cleanup is the user's decision.")


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is
    # actually done.
    pass
