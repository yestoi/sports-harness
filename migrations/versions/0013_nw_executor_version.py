"""roadmap row 72 / spec amendment 0.17: `orders.nw_executor_version`

Revision ID: 0013_nw_executor_version
Revises: 0012_phase6d_sustained_eval
Create Date: 2026-09-15

One additive column, nullable with no default: the `EXECUTOR_VERSION` of the build that last
wrote any of an order's `nw_` counterfactual columns. Spec amendment 0.17 (user decision
2026-09-15, journal 224 item 5) narrows the §2 "no pre-6B row is backfilled" invariant to the
pre-boundary rows that were `nw_done = true` at the boundary, because §0.14 and §1.5 always
meant the 1,176 pre-boundary tracks still pending at the c1066b5 stop instant
(2026-09-15T05:23:44Z) to run to their natural expiry. This column is what makes that visible
from the data alone: a pre-boundary row carrying non-null twins and a *null* version is the
anomaly; one carrying 4.5 is the disclosed sub-population Amendment 6 names.

`ALTER TABLE orders ADD COLUMN ... numeric` with no default and no NOT NULL is metadata-only in
Postgres: it rewrites nothing, takes a brief ACCESS EXCLUSIVE lock that
`harness/db/migrate.py`'s `lock_timeout = '5s'` already bounds, and touches no existing row. No
index: nothing reads the column as a predicate -- the verify queries that read it are already
bounded by `id <= 10886` -- and every index on a table with a live writer would have to be
CONCURRENTLY (F65, fix 25), which is a cost with no reader to pay for it.

The id is 24 characters, inside the `String(32)` Alembic creates `alembic_version.version_num`
as (0012's docstring records the 33-character id that aborted every upgrade), and the file name
equals the revision id as all twelve revisions before it do.

`downgrade()` is `pass` (roadmap invariant 5, every revision since 0002): additive only, and
rolling back is a code rollback through the reviewed release procedure, never a schema one.
Dropping this column would also destroy the only record of which simulator wrote a row's
counterfactual.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0013_nw_executor_version"
down_revision: str | None = "0012_phase6d_sustained_eval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Byte-identical to `harness/db/schema.py`'s `_COLUMN_DDL` entry, which stays the schema
#: authority; `tests/test_alembic.py`'s catalogue diff is what keeps the two copies honest.
_COLUMNS = (
    "alter table orders add column if not exists nw_executor_version numeric",
)


def upgrade() -> None:
    for statement in _COLUMNS:
        op.execute(statement)


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring.
    pass
