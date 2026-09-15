"""phase 6D: sustained evaluation -- the coverage tables and fix 51's intents index

Revision ID: 0009_phase6d_sustained_eval
Revises: 0008_positions_open_fill
Create Date: 2026-09-13

Additive only (roadmap invariant 5): this revision creates new tables and new indexes and
alters nothing that exists. It is built in three passes by the 6D plan -- Task 1 adds fix 51's
`ix_intents_created`, Task 4 adds `coverage_samples`, Task 8 adds `opportunity_episodes` and
`intent_episodes` -- and `harness/db/schema.py` plus the models carry the identical statements,
which is what `tests/test_alembic.py`'s catalogue diff compares.

**The number and the parent are the controller's at merge time** (4.6 addendum ruling D9). Two
unmerged revisions sit beside this one -- 6B's `0008_phase6b_execution` on branch
`phase6b-repair-execution` and 4.6's `00NN_phase46_fun_tickets` -- and the 2026-09-13 hotfix for
the stranded position takes `0008_positions_open_fill`, which is why this file is written as
0009 on top of it. If the branch this merges onto carries a different newest revision, the
controller re-numbers this file and rewrites `down_revision` and `harness/db/migrate.py`'s
`HEAD_REVISION` to match; nothing else about the revision changes.

**Why the id is `..._eval` and not `..._evaluation`** (T1 deviation, for the controller's merge
note): Alembic stores the stamp in `alembic_version.version_num`, which it creates as
`Column("version_num", String(32))` (`alembic/ddl/impl.py`) with no option to widen, and the
deployed database already carries that column, so widening it would be a non-additive ALTER.
`0009_phase6d_sustained_evaluation` is 33 characters and every upgrade to it aborts with
`StringDataRightTruncation` (measured on the T1 test database: 17 of `tests/test_alembic.py`'s
scratch-database cases failed on it). `0009_phase6d_sustained_eval` is 27, and the file name
matches the revision id exactly as all eight revisions before it do. Tasks 4 and 8 add their
table passes to *this* file and must use this id.

`ix_intents_created` is built CONCURRENTLY (F65, fix 25: every index on a table with a live
writer, no carve-out) through `migrations.env.concurrent_index`, which takes the statement out
of the migration's transaction with `autocommit_block` -- what CREATE INDEX CONCURRENTLY
requires. `intents` is not one of the five bulk tape tables, but the executor writes to it on
its 15 s loop and `init-db` runs on every deploy, so the rule applies to it for the same reason
it applied to `orders` in `0002_phase45`.

`downgrade()` is `pass` (roadmap invariant 5, every revision since 0002): additive only, and
rolling back is a code rollback (`git checkout <sha> && make deploy-omarchy`), never a schema
one. Removing an index or a table is non-additive and therefore a gate the user opens by hand.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.env import concurrent_index

revision: str = "0009_phase6d_sustained_eval"
down_revision: str | None = "0008_positions_open_fill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Task 4 (6D §1.1, D1): `coverage_samples` -- what a tick scheduled and what became of it.
    op.create_table(
        "coverage_samples",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("domain", sa.String(length=12), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("sport", sa.String(length=8), nullable=True),
        sa.Column("ttk_bucket", sa.String(length=12), nullable=True),
        sa.Column("feed", sa.String(length=9), nullable=True),
        sa.Column("market_type", sa.String(length=16), nullable=True),
        sa.Column("variant_id", sa.String(length=12), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("overdue_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    # Plain, not CONCURRENTLY: `coverage_samples` is new, so it is empty and has no writer
    # attached while this runs. F65's rule is about a populated table with a live writer.
    op.create_index("ix_coverage_ts_domain", "coverage_samples", ["ts", "domain"],
                    if_not_exists=True)
    op.create_index("ix_coverage_run", "coverage_samples", ["run_id"], if_not_exists=True)

    # Task 8 (6D §1.7(b)/(c), D6): the two episode tables -- 6C's deferred funnel units as
    # rows, so the count is an indexed `count(*)` instead of a `distinct` over `signals`.
    op.create_table(
        "opportunity_episodes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("variant_id", sa.String(length=12), nullable=False),
        sa.Column("venue_market_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gap_rule_s", sa.Integer(), nullable=False),
        sa.Column("n_signals", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variant_id", "venue_market_id", "side", "started_at",
                            name="uq_opportunity_episode"),
        if_not_exists=True,
    )
    op.create_table(
        "intent_episodes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("variant_id", sa.String(length=12), nullable=False),
        sa.Column("venue_market_id", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gap_rule_s", sa.Integer(), nullable=False),
        sa.Column("n_intents", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variant_id", "venue_market_id", "side", "started_at",
                            name="uq_intent_episode"),
        if_not_exists=True,
    )
    # Plain, not CONCURRENTLY, for `coverage_samples`' reason above: both tables are created
    # empty by this statement and have no writer attached while it runs. The unique constraints
    # come with `create_table` and need no statement of their own; these two `started_at`
    # indexes are what the bounded `count(*)` in t14 and Floor's funnel ride.
    op.create_index("ix_opportunity_started", "opportunity_episodes", ["started_at"],
                    if_not_exists=True)
    op.create_index("ix_intent_started", "intent_episodes", ["started_at"], if_not_exists=True)

    # Fix 51 (6D §1.9, D9): the index `intents_without_order_or_skip`'s 24 h bound needs.
    concurrent_index("ix_intents_created", "intents", ["created_at"])


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is
    # actually done.
    pass
