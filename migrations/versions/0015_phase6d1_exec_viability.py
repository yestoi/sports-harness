"""phase 6D.1: the execution-viability experiment's eleven additive `exp_*` tables

Revision ID: 0015_phase6d1_exec_viability
Revises: 0014_orders_intent_index
Create Date: 2026-09-18

Additive only (roadmap invariant 5, addendum §2): this revision creates eleven new tables, four
plain indexes on them and one new view, and it takes nothing away: no existing object is
altered, re-labelled, removed, emptied or backfilled by it. It reads no existing row and writes no existing row, so no production table is touched
by an upgrade to it. The eleven are `exp_run`, `exp_arm`, `exp_order`, `exp_fill`,
`exp_allocation`, `exp_observation`, `exp_outcome`, `exp_book_health`, `exp_checkpoint`,
`exp_mismatch` and `exp_limitation` (ruling I5: the raw response bodies live in the hashed file
tree, so no `exp_raw_body` table exists), and `harness/db/models.py` declares each of them with
the identical columns so `create_all` gives a fresh database exactly what this gives a populated
one -- which is what `tests/test_alembic.py`'s catalogue diff compares.

**No `CONCURRENTLY` and no `autocommit_block`.** F65's rule (fix 25: every index on a bulk table
is built CONCURRENTLY, no carve-out) is about a *populated* table with a live writer attached.
None of these eleven is one of the five bulk tape families or a partition of one, and every one
of them is created empty by this same revision, with no writer able to reach it while the
migration runs; nothing can hold a lock against these builds. 0014's autocommit block is the
opposite case -- an index on `orders`, which the executor writes on its 15 s loop.

The `exp_veto_coverage` view (§3 row 4, ruling I8) is created here with the same
`create or replace view` text `harness/db/schema.py`'s `_EXP_VETO_COVERAGE_VIEW` carries;
`create or replace` is idempotent and takes nothing away.

The id is 28 characters, inside the `String(32)` Alembic creates `alembic_version.version_num`
as (0012's docstring records the 33-character id that aborted every upgrade), and the file name
equals the revision id as all fourteen revisions before it do. **The controller may renumber
this revision at merge** (4.6 addendum ruling D9, applied five times on this chain): the id,
`down_revision` and `harness/db/migrate.py`'s `HEAD_REVISION` move together and nothing else
about the revision changes.

`downgrade()` is `pass` (roadmap invariant 5, every revision since 0002): additive only, and a
rollback is a code rollback through the reviewed release procedure, never a schema one.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from harness.db.schema import _EXP_VETO_COVERAGE_VIEW

revision: str = "0015_phase6d1_exec_viability"
down_revision: str | None = "0014_orders_intent_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The same three Numeric shapes the models use (`models.CONTRACTS`, `models.PROB`).
CONTRACTS = sa.Numeric(14, 2)
PROB = sa.Numeric(6, 4)
UUID = sa.Uuid(as_uuid=False)
TS = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # --- §1.2: the frozen run and its arms -----------------------------------------------
    op.create_table(
        "exp_run",
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("code_sha", sa.String(length=40), nullable=True),
        sa.Column("clock_mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("supersedes", UUID, nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_arm",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("spec", JSONB, nullable=False),
        sa.Column("spec_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "arm_id", name="uq_exp_arm"),
        if_not_exists=True,
    )

    # --- §1.4: one arm's own orders, fills, print allocations and resume point ------------
    op.create_table(
        "exp_order",
        # A surrogate key, so §2's invariant `join exp_order o on o.id = f.exp_order_id` is
        # unambiguous (fix round 1, ruling D22).
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        # The arm's own (negative) order id, never a sequence: the same run chunked two ways
        # must produce the same ids, which a serial could not promise. It restarts at -1 for
        # every `(run, arm)`, which is why it is unique only together with both of them.
        sa.Column("arm_order_id", sa.BigInteger(), nullable=False),
        sa.Column("variant_id", sa.String(length=12), nullable=False),
        sa.Column("intent_id", sa.BigInteger(), nullable=True),
        sa.Column("venue_market_id", sa.Integer(), nullable=True),
        sa.Column("ticker", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("prob", PROB, nullable=False),
        sa.Column("contracts", CONTRACTS, nullable=False),
        sa.Column("filled_contracts", CONTRACTS, nullable=False),
        sa.Column("placed_at", TS, nullable=False),
        sa.Column("expiry", TS, nullable=True),
        sa.Column("queue_ahead_at_place", CONTRACTS, nullable=True),
        sa.Column("cancelled_at", TS, nullable=True),
        sa.Column("cancel_reason", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("episode_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "arm_id", "arm_order_id", name="uq_exp_order_arm"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_fill",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        # `exp_order.id`, the surrogate key (D22).
        sa.Column("exp_order_id", sa.BigInteger(), nullable=False),
        sa.Column("filled_at", TS, nullable=False),
        sa.Column("contracts", CONTRACTS, nullable=False),
        sa.Column("prob", PROB, nullable=False),
        sa.Column("fee", sa.Numeric(12, 4), nullable=True),
        sa.Column("fill_method", sa.String(length=16), nullable=False),
        sa.Column("source_trade_id", sa.String(length=64), nullable=True),
        sa.Column("through", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_allocation",
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        sa.Column("variant_id", sa.String(length=12), nullable=False),
        sa.Column("source_trade_id", sa.String(length=64), nullable=False),
        sa.Column("available", CONTRACTS, nullable=False),
        sa.Column("allocated", CONTRACTS, nullable=False),
        sa.PrimaryKeyConstraint("run_id", "arm_id", "variant_id", "source_trade_id"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_checkpoint",
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        sa.Column("cursor_event_id", sa.BigInteger(), nullable=True),
        sa.Column("state", JSONB, nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("run_id", "arm_id"),
        if_not_exists=True,
    )

    # --- §1.6/§1.7/§1.9: observations, outcomes and book health ---------------------------
    op.create_table(
        "exp_observation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("available_at", TS, nullable=False),
        sa.Column("sport", sa.String(length=8), nullable=True),
        sa.Column("game_id", sa.BigInteger(), nullable=True),
        sa.Column("venue_market_id", sa.Integer(), nullable=True),
        sa.Column("fair_p", PROB, nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("credits_last", sa.Integer(), nullable=True),
        sa.Column("credits_remaining", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("body_path", sa.Text(), nullable=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_outcome",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        sa.Column("exp_order_id", sa.BigInteger(), nullable=False),
        sa.Column("horizon", sa.String(length=8), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("value", sa.Numeric(12, 6), nullable=True),
        sa.Column("source_age_s", sa.Integer(), nullable=True),
        sa.Column("censored", sa.Boolean(), nullable=False),
        sa.Column("missing_reason", sa.String(length=24), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_book_health",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("ticker", sa.String(length=64), nullable=False),
        sa.Column("interval_start", TS, nullable=False),
        sa.Column("interval_end", TS, nullable=False),
        sa.Column("classification", sa.String(length=24), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )

    # --- §1.3(f)/§2: what a run could not reproduce and what it could not know ------------
    op.create_table(
        "exp_mismatch",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("arm_id", sa.String(length=8), nullable=False),
        sa.Column("instant", TS, nullable=False),
        sa.Column("venue_market_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("expected", JSONB, nullable=False),
        sa.Column("actual", JSONB, nullable=False),
        sa.Column("cause", sa.String(length=32), nullable=True),
        sa.Column("explained", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "exp_limitation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("scope", JSONB, nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )

    # Plain indexes, for the reason in the docstring: each table above is created empty by this
    # same revision and has no writer attached while it runs.
    op.create_index("ix_exp_order_run_arm", "exp_order", ["run_id", "arm_id", "placed_at"],
                    if_not_exists=True)
    op.create_index("ix_exp_fill_trade", "exp_fill", ["run_id", "arm_id", "source_trade_id"],
                    if_not_exists=True)
    op.create_index("ix_exp_mismatch_run", "exp_mismatch", ["run_id", "explained"],
                    if_not_exists=True)
    op.create_index("ix_exp_limitation_run", "exp_limitation", ["run_id", "kind"],
                    if_not_exists=True)

    # §3 row 4 / ruling I8, the same text `create_schema` runs.
    op.execute(_EXP_VETO_COVERAGE_VIEW)


def downgrade() -> None:
    # Additive only (roadmap invariant 5); a rollback is a code rollback through the reviewed
    # release procedure, never a schema one.
    pass
