"""phase 5: the research layer's ten tables and the veto_h9 view

Revision ID: 0004_phase5
Revises: 0003_brin_autosummarize
Create Date: 2026-09-10

An additive mirror of `harness/db/models.py` and `harness/db/schema.py`. The models and
`create_schema` stay the schema authority; this file records the history, and
`tests/test_alembic.py` builds one database each way and compares the catalogues -- columns,
types, nullability, keys, indexes and views -- so the two cannot drift apart quietly.

Every statement is `IF NOT EXISTS` or `CREATE OR REPLACE`. Nothing here is built CONCURRENTLY:
none of the ten tables is a bulk table, all ten are empty on the deploy that creates them, and
`uq_rfq_quote_rfq` is a unique index on a table with no rows.

`downgrade()` is `pass`, deliberately. Roadmap invariant 5 is that nothing non-additive ever
runs, and the phase audit greps every migration for non-additive statements. Rolling back is
`git checkout <sha> && make deploy-nas-app`, which never runs `migrate ensure`, and old code
simply ignores the new tables. A later *full* deploy on a rolled-back sha aborts at `ensure` and
needs a hand `alembic stamp 0003_brin_autosummarize` first; that is the user's action, never the
loop's.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase5"
down_revision: str | None = "0003_brin_autosummarize"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('futures_snapshots',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('snapshot_week', sa.String(length=8), nullable=False),
    sa.Column('series_ticker', sa.String(length=32), nullable=False),
    sa.Column('event_ticker', sa.String(length=64), nullable=False),
    sa.Column('market_ticker', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=True),
    sa.Column('yes_sub_title', sa.String(length=200), nullable=True),
    sa.Column('kalshi_market_type', sa.String(length=8), nullable=False),
    sa.Column('strike_type', sa.String(length=16), nullable=True),
    sa.Column('floor_strike', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('cap_strike', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('yes_bid', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('yes_ask', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('last_price', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('volume', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('open_interest', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('close_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )

    op.create_table('weather_points',
    sa.Column('sport', sa.String(length=8), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('office', sa.String(length=8), nullable=False),
    sa.Column('grid_x', sa.Integer(), nullable=False),
    sa.Column('grid_y', sa.Integer(), nullable=False),
    sa.Column('forecast_hourly_url', sa.String(length=256), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('sport', 'team_id'),
    if_not_exists=True,
    )

    op.create_table('weather_snapshots',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('temperature_f', sa.SmallInteger(), nullable=True),
    sa.Column('wind_mph', sa.SmallInteger(), nullable=True),
    sa.Column('wind_dir', sa.String(length=8), nullable=True),
    sa.Column('precip_pct', sa.SmallInteger(), nullable=True),
    sa.Column('short_forecast', sa.String(length=80), nullable=True),
    sa.Column('roof', sa.String(length=11), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )

    op.create_table('veto_queue',
    # autoincrement=False on all three of these foreign-id primary keys (veto_queue.signal_id,
    # veto_decisions.signal_id, report_annotations.report_run_id): they are copies of signals.id
    # and report_runs.id, never generated, and without it both builders emit a BIGSERIAL.
    sa.Column('signal_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=True),
    sa.Column('market_type', sa.String(length=16), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('enqueued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('signal_id'),
    if_not_exists=True,
    )

    op.create_table('research_notes',
    sa.Column('call_id', sa.Uuid(), nullable=False),
    sa.Column('model', sa.String(length=24), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('subject_id', sa.String(length=64), nullable=False),
    sa.Column('effort', sa.String(length=8), nullable=False),
    sa.Column('prompt_hash', sa.String(length=64), nullable=False),
    sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('snippets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('usage', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.Column('arm', sa.String(length=16), nullable=True),
    sa.PrimaryKeyConstraint('call_id', 'model'),
    if_not_exists=True,
    )

    op.create_table('veto_decisions',
    sa.Column('signal_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('call_id', sa.Uuid(), nullable=True),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('from_cache', sa.Boolean(), nullable=False),
    sa.Column('feature_delta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('signal_created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason_code', sa.String(length=32), nullable=True),
    sa.PrimaryKeyConstraint('signal_id'),
    if_not_exists=True,
    )

    op.create_table('research_spend',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('model', sa.String(length=24), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.BigInteger(), nullable=False),
    sa.Column('output_tokens', sa.BigInteger(), nullable=False),
    sa.Column('cache_read_tokens', sa.BigInteger(), nullable=False),
    sa.Column('cache_write_tokens', sa.BigInteger(), nullable=False),
    sa.Column('searches', sa.Integer(), nullable=False),
    sa.Column('usd_reserved', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('usd', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.PrimaryKeyConstraint('day', 'kind', 'model'),
    if_not_exists=True,
    )

    op.create_table('report_annotations',
    sa.Column('report_run_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('model', sa.String(length=24), nullable=False),
    sa.Column('prompt_hash', sa.String(length=64), nullable=False),
    sa.Column('bullets', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('report_run_id'),
    if_not_exists=True,
    )

    op.create_table('rfqs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('event_ticker', sa.String(length=64), nullable=True),
    sa.Column('market_ticker', sa.String(length=64), nullable=False),
    sa.Column('contracts_fp', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('target_cost_dollars', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('mve_collection_ticker', sa.String(length=64), nullable=True),
    sa.Column('legs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('deleted_ts', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )

    op.create_table('rfq_quotes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('rfq_id', sa.String(length=64), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('legs', sa.SmallInteger(), nullable=False),
    sa.Column('fair', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('margin_per_leg', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('yes_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('no_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fee_branch_game', sa.Boolean(), nullable=True),
    sa.Column('fee_branch_event', sa.Boolean(), nullable=True),
    sa.Column('fee_subtracted', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('yes_bid_other_branch', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('no_bid_other_branch', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('declined_reason', sa.String(length=16), nullable=True),
    sa.Column('unmatched_legs', sa.SmallInteger(), nullable=False),
    sa.Column('graded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closing_fair', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('closing_stale', sa.Boolean(), nullable=True),
    sa.Column('pnl_yes', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('pnl_no', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('voided', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )

    # --- the indexes (create_schema's _INDEX_DDL, phase 5 block) --------------------------
    # Spelled as raw statements rather than op.create_index, exactly as 0001_baseline does for
    # this same block: two of them are partial or descending, and one SQL text on both sides is
    # what keeps the catalogue diff from turning on a dialect option Alembic renders differently.
    op.execute("create index if not exists ix_futures_series_week on futures_snapshots "
               "(series_ticker, snapshot_week)")
    op.execute("create index if not exists ix_weather_game_fetched on weather_snapshots "
               "(game_id, fetched_at)")
    op.execute("create index if not exists ix_research_notes_subject on research_notes "
               "(subject_id)")
    op.execute("create index if not exists ix_veto_queue_open on veto_queue "
               "(bucket_start, game_id, market_type) where claimed_at is null")
    op.execute("create index if not exists ix_veto_decisions_decided on veto_decisions "
               "(decided_at desc)")
    op.execute("create index if not exists ix_rfqs_received on rfqs (received_at desc)")
    op.execute("create unique index if not exists uq_rfq_quote_rfq on rfq_quotes (rfq_id)")
    op.execute("create index if not exists ix_rfq_quotes_computed on rfq_quotes "
               "(computed_at desc)")

    # --- the view (create_schema's _VIEW_DDL) --------------------------------------------
    op.execute("""
create or replace view veto_h9 as
select d.signal_id,
       d.call_id,
       d.decision,
       d.confidence,
       d.from_cache,
       d.signal_created_at,
       d.decided_at,
       n.model,
       n.effort,
       n.prompt_hash,
       n.cost_usd,
       n.latency_ms
from veto_decisions d
join research_notes n on n.call_id = d.call_id
where n.kind = 'veto'
  and n.replay = false
  and n.model = 'claude-opus-5'
  and d.decision in ('proceed', 'reduce', 'veto')
""")


def downgrade() -> None:
    # Additive only (roadmap invariant 5). Nothing here is undone by the loop; see the module
    # docstring for how a rollback is actually done.
    pass
