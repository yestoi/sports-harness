"""phase 4.5: dashboard snapshots, the parlay tables, and the orders key index

Revision ID: 0002_phase45
Revises: 0001_baseline
Create Date: 2026-09-09

An additive mirror of `harness/db/schema.py` and the six models phase 4.5 adds. The models and
`create_schema` stay the schema authority; this file records the history, and
`tests/test_alembic.py` builds one database each way and compares the catalogues, so the two
cannot drift apart quietly.

Every statement is `IF NOT EXISTS`. The one index built CONCURRENTLY is the one `create_schema`
builds that way (`ix_orders_key_placed`, which the corrected `intents_without_order_or_skip`
check rides).

`downgrade()` is `pass`, deliberately. Roadmap invariant 5 is that nothing non-additive ever
runs, and the phase audit greps every migration for non-additive statements; a downgrade that
undid this would put them in the file. Rolling back is `git checkout <sha> && make
deploy-nas-app`, which never runs `migrate ensure`, and old code simply ignores the new tables.
A later *full* deploy on a rolled-back sha aborts at `ensure` and needs a hand
`alembic stamp 0001_baseline` first; that is the user's action, never the loop's.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from migrations.env import concurrent_index

revision: str = "0002_phase45"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dashboard_snapshots',
    sa.Column('name', sa.String(length=32), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('elapsed_ms', sa.Integer(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.String(length=80), nullable=True),
    sa.PrimaryKeyConstraint('name'),
    if_not_exists=True,
    )

    op.create_table('parlay_cards',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('week', sa.SmallInteger(), nullable=False),
    sa.Column('sport', sa.String(length=5), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('built_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('stake', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('dk_payout_est', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('true_prob_est', sa.Numeric(precision=8, scale=6), nullable=True),
    sa.Column('hold_est', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('rationale', sa.String(length=600), nullable=True),
    sa.Column('anchor_leg_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('correlated', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_index('ix_parlay_cards_week', 'parlay_cards', ['year', 'week'], unique=False,
                    if_not_exists=True)

    op.create_table('parlay_legs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('seq', sa.SmallInteger(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('market_type', sa.String(length=6), nullable=False),
    sa.Column('side_team_id', sa.Integer(), nullable=True),
    sa.Column('side', sa.String(length=5), nullable=True),
    sa.Column('threshold', sa.Numeric(precision=5, scale=1), nullable=True),
    sa.Column('dk_american', sa.Integer(), nullable=False),
    sa.Column('dk_decimal', sa.Numeric(precision=8, scale=4), nullable=False),
    sa.Column('plain_text', sa.String(length=80), nullable=False),
    sa.Column('odds_snapshot_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('graded_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_index('ix_parlay_legs_card_seq', 'parlay_legs', ['card_id', 'seq'], unique=False,
                    if_not_exists=True)

    op.create_table('parlay_placements',
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('stake_actual', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('dk_payout_actual', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('dk_odds_actual', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('card_id'),
    if_not_exists=True,
    )

    op.create_table('parlay_ledger',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('card_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=6), nullable=False),
    sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('week', sa.SmallInteger(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )

    op.create_table('parlay_leg_probs',
    sa.Column('leg_id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sharp_p', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('book_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.PrimaryKeyConstraint('leg_id', 'ts'),
    if_not_exists=True,
    )

    # The one index create_schema builds CONCURRENTLY in this phase: the corrected
    # `intents_without_order_or_skip` check asks, per candidate intent, whether an order was
    # working on its (variant_id, venue_market_id, side) key at the intent's own created_at.
    concurrent_index("ix_orders_key_placed", "orders",
                     ['variant_id', 'venue_market_id', 'side', 'placed_at'])


def downgrade() -> None:
    # Additive only (roadmap invariant 5). Nothing here is undone by the loop; see the module
    # docstring for how a rollback is actually done.
    pass
