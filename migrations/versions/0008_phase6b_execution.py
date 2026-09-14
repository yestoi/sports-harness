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

import sqlalchemy as sa
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
    # Phase 6B §1.5: the counterfactual's own nominal accrual and its retry bookkeeping.
    "alter table orders add column if not exists nw_dirty_seconds integer",
    "alter table orders add column if not exists nw_next_attempt_at timestamptz",
    "alter table orders add column if not exists nw_attempts integer",
)


def _create_interval_tables() -> None:
    """§1.5's two interval tables, mirroring `harness/db/models.py` (the 0004_phase5 pattern).

    Declared as models so `create_schema` builds them; repeated here because `tests/test_alembic`
    compares a migrated database's catalogue against a `create_schema` one, and a table in only
    one of them is a failed diff, not a tolerated difference.
    """
    for name, extra in (("market_dirty_intervals",
                         [sa.Column("cause", sa.String(length=20), nullable=False)]),
                        ("market_observation_intervals", [])):
        op.create_table(
            name,
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("venue_market_id", sa.Integer(), nullable=False),
            sa.Column("ticker", sa.String(length=64), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            *extra,
            sa.Column("replay", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            if_not_exists=True,
        )
    op.create_index("ix_mdi_market_started", "market_dirty_intervals",
                    ["venue_market_id", "started_at"], unique=False, if_not_exists=True)
    op.create_index("ix_moi_market_started", "market_observation_intervals",
                    ["venue_market_id", "started_at"], unique=False, if_not_exists=True)


def _create_order_rescores() -> None:
    """§1.8's estimates table, mirroring `harness/db/models.py` (the 0004_phase5 pattern).

    Unconditional, like the interval tables above: `tests/test_alembic.py` compares a migrated
    database's catalogue with a `create_schema` one, and a table declared as a model but missing
    from the revision is a failed diff rather than a tolerated difference. It declares no
    index of its own -- the primary key is the only access path the command uses -- so nothing
    is added to `harness/db/schema.py`'s `_INDEX_DDL`.
    """
    op.create_table(
        "order_rescores",
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("correction_ids", sa.String(length=64), nullable=False),
        sa.Column("cancel_policy", sa.String(length=8), nullable=False),
        sa.Column("watched_filled", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("counterfactual_filled", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("queue_remaining", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("cancels_ahead", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("watched_dirty_s", sa.Integer(), nullable=True),
        sa.Column("counterfactual_dirty_s", sa.Integer(), nullable=True),
        sa.Column("unobserved_s", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("build_sha", sa.String(length=24), nullable=True),
        sa.PrimaryKeyConstraint("order_id", "correction_ids", "cancel_policy"),
        if_not_exists=True,
    )


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)
    _create_interval_tables()
    _create_order_rescores()


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is done.
    pass
