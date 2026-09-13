"""phase 6B: the reconciliation ledger, dirty and observation intervals, and order_rescores

Revision ID: 0008_phase6b_execution
Revises: 0007_raw_events_lookup
Create Date: 2026-09-11

Additive only, and additive in three instalments: §1.3's ledger columns land here first,
§1.5's two interval tables and retry columns are appended by that task, and §1.8's
`order_rescores` by its own. Each instalment adds statements to `_STATEMENTS` below and the
identical statements to `harness/db/schema.py`, which stays the schema authority;
`tests/test_alembic.py`'s catalogue diff between a `create_schema` database and a migrated one
is what keeps the two copies honest.

Nothing here is a bulk table, so no statement is CONCURRENTLY and none needs
`migrations.env.concurrent_index`: `orders` sees one writer per executor step, not a continuous
insert stream, and the three new tables are empty when this runs.

`downgrade()` is `pass` (roadmap invariant 5, as every revision since 0002): a rollback is a
controller-managed code rollback through the reviewed Omarchy release procedure, never a schema
one. The additive columns and tables stay, a rolled-back build ignores them, and the correction
record says which build produced which rows.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008_phase6b_execution"
down_revision: str | None = "0007_raw_events_lookup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The identical statements as `harness/db/schema.py`'s `_COLUMN_DDL` additions for 6B.
_STATEMENTS = (
    "alter table orders add column if not exists print_unmatched numeric(14,2)",
    "alter table orders add column if not exists pending_unmatched numeric(14,2)",
    "alter table orders add column if not exists pending_surplus numeric(14,2)",
    "alter table orders add column if not exists cancels_ahead numeric(14,2)",
    "alter table orders add column if not exists nw_print_unmatched numeric(14,2)",
    "alter table orders add column if not exists nw_pending_unmatched numeric(14,2)",
    "alter table orders add column if not exists nw_pending_surplus numeric(14,2)",
    "alter table orders add column if not exists nw_cancels_ahead numeric(14,2)",
    "alter table orders add column if not exists recon_state jsonb",
    "alter table orders add column if not exists nw_recon_state jsonb",
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is done.
    pass
