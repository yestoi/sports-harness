"""fix 45: raw_responses' lookup index for the kalshi /events cache read

Revision ID: 0007_raw_events_lookup
Revises: 0006_quotes_run_index
Create Date: 2026-09-12

`_load_events_cache` (`harness/normalize/runner.py`) selects the newest 200 `raw_responses` rows
for `source = 'kalshi', endpoint = '/events', http_status = 200`, newest first by `id`.
`raw_responses` is partitioned by range on `fetched_at` (one new weekly partition growing past
2.2 GB each) and carried only `raw_responses_pkey (id, fetched_at)`, `ix_raw_source_fetched
(source, fetched_at)`, `ix_raw_fetched_brin` and `ix_raw_run (run_id)` -- nothing led with
`(source, endpoint)` in id order, so the live plan was a backward walk of the primary key across
every partition with the three predicates only a filter: cost 3,887 warm, and past the 30 s
statement timeout cold on the NAS (`psycopg.errors.QueryCanceled` at 23:23, 23:26 and 23:27 CT on
2026-09-11, runs 12100-12103 `degraded`, and again at 00:06 CT on 2026-09-12). A degraded run
normalizes nothing for that tick: no new games or markets from Kalshi events until the next one.

`(source, endpoint, id)` makes the predicate the scan key and rides the same column order the
read already sorts by, so the scan is index-ordered rather than merely index-assisted.

The partitioned recipe, not a `_CONCURRENT_INDEX_DDL` / `concurrent_index` entry: Postgres 16
refuses `create index concurrently` on a partitioned parent at all (`migrations/env.py`'s own
`concurrent_index` docstring says so), and a plain `create index` on one is not metadata-only --
it recurses into every partition and takes a ShareLock on each, exactly what F65 (fix 25)
forbids on a bulk table with no carve-out. The three-step recipe instead: `create index if not
exists <name> on only raw_responses (...)` builds the parent alone, metadata-only and marked
invalid until every partition has a matching child; each partition then gets its own `create
index concurrently if not exists` build, holding only a ShareUpdateExclusiveLock the recorder's
inserts do not queue behind; and `alter index <name> attach partition <child>` is metadata-only
once that child exists. `if not exists` throughout makes every step a genuine no-op on a rerun,
and a parent already fully attached (`pg_index.indisvalid`) is skipped before any statement runs
at all -- the same "a no-op must take no lock" rule fix 37 established for `create_schema`.

This is `harness/db/schema.py`'s `_ensure_partitioned_concurrent_indexes`, called directly rather
than duplicated as a second copy of the recipe: unlike `0005_rfq_lookup`'s and
`0006_quotes_run_index`'s single `create index concurrently` statement, there is no one DDL
string to keep two textually-identical copies of, and the loop over `pg_inherits` this recipe
needs is exactly what `create_schema` already carries. `RawResponse.__table_args__` declares the
same index by name so `create_all` gives it to a fresh database (the test database included)
directly, and the model, `schema.py`'s `_PARTITIONED_CONCURRENT_INDEXES` tuple and this revision
must all name it identically or `tests/test_alembic.py`'s catalogue diff fails.

Taken out of the migration's own transaction with `op.get_context().autocommit_block()`, the same
mechanism `0005_rfq_lookup` and `0006_quotes_run_index` use, because CREATE INDEX CONCURRENTLY
cannot run inside one.

Phase 6B's own plan names its revision `0007_phase6b_execution`; the controller's ruling
(hotfix 2026-09-12) is that 6B's revision becomes `0008_phase6b_execution` on top of this one, so
this file keeps `0007`.

`downgrade()` drops the parent index, which drops every attached partition child with it
(intrinsic to a partitioned index in Postgres -- no CASCADE needed) -- unlike every revision
since `0002_phase45`, whose `downgrade()` is `pass` under roadmap invariant 5. A plain, additive
index carries none of the data-loss risk that rule guards against, and leaving a partitioned
parent half-built by a rolled-back-and-reapplied revision is worse than removing it cleanly.
"""
from collections.abc import Sequence

from alembic import op

from harness.db.schema import _ensure_partitioned_concurrent_indexes

revision: str = "0007_raw_events_lookup"
down_revision: str | None = "0006_quotes_run_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _ensure_partitioned_concurrent_indexes(op.get_bind())


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("drop index if exists ix_raw_source_endpoint_id")
