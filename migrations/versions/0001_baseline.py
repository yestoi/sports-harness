"""phase 4 baseline: the schema as create_schema builds it

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-08

Written by hand from `harness/db/schema.py`, and self-contained: nothing here imports the models
or `create_schema`, so the baseline stays the schema of this commit while they move on.

Three things about it are load-bearing.

* The partitioned tables are created as **parents only**. Their weekly children are runtime
  objects `ensure_partitions` creates, named by date; a migration never makes one.
* Every statement is `IF NOT EXISTS`, and the only index built CONCURRENTLY is the one
  `create_schema` builds that way. Postgres 16 cannot build an index CONCURRENTLY on a
  partitioned parent, which is why the tape's own indexes are plain statements here: on the
  empty database this baseline is executed against they are instant, and against a populated
  database the baseline is never executed at all -- `harness migrate ensure` stamps it.
* `tests/test_alembic.py` builds one database with `create_schema` and one with `upgrade_head`
  and compares the two catalogues, so this file and `create_schema` cannot drift apart quietly.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from migrations.env import concurrent_index

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- tables, in dependency order, and the indexes the models declare -------------------

    op.create_table('backup_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('build_sha', sa.String(length=24), nullable=True),
    sa.Column('path', sa.String(length=256), nullable=True),
    sa.Column('bytes', sa.BigInteger(), nullable=True),
    sa.Column('plaintext_sha256', sa.String(length=64), nullable=True),
    sa.Column('ciphertext_sha256', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rows_match', sa.Boolean(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('benchmarks',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('market_type', sa.String(length=16), nullable=False),
    sa.Column('outcome_team_id', sa.Integer(), nullable=True),
    sa.Column('outcome_side', sa.String(length=8), nullable=True),
    sa.Column('threshold', sa.Numeric(precision=6, scale=1), nullable=True),
    sa.Column('benchmark_type', sa.String(length=32), nullable=False),
    sa.Column('p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('target_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stale', sa.Boolean(), nullable=False),
    sa.Column('kickoff_moved', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('check_results',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('job_run_id', sa.BigInteger(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('check_name', sa.String(length=48), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('value', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.Column('threshold', sa.String(length=48), nullable=True),
    sa.Column('detail', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('config_history',
    sa.Column('config_hash', sa.String(length=64), nullable=False),
    sa.Column('config_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('config_hash'),
    if_not_exists=True,
    )
    op.create_table('equity_snapshots',
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('cash', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('open_stake', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('mtm_open', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('mtm_coverage', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('n_open_positions', sa.Integer(), nullable=False),
    sa.Column('n_open_orders', sa.Integer(), nullable=False),
    sa.Column('peak_equity_7d', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('drawdown_pct', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('drawdown_stop', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('ts', 'variant_id'),
    if_not_exists=True,
    )
    op.create_table('exec_heartbeat',
    # Not a SERIAL: the model gives this a client-side default of 1 (the table holds one row),
    # so `create_schema` emits a plain integer and the baseline has to as well. Alembic would
    # otherwise infer autoincrement from "integer primary key" and attach a sequence.
    sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('last_loop_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('loops', sa.Integer(), nullable=False),
    sa.Column('open_orders', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_loop_ms', sa.Integer(), nullable=True),
    sa.Column('p95_loop_ms', sa.Integer(), nullable=True),
    sa.Column('loops_skipped', sa.Integer(), nullable=False),
    sa.Column('book_dirty_markets', sa.Integer(), nullable=False),
    sa.Column('ws_last_event_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('executor_version', sa.String(length=16), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('fair_values',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('market_type', sa.String(length=16), nullable=False),
    sa.Column('outcome_team_id', sa.Integer(), nullable=True),
    sa.Column('outcome_side', sa.String(length=8), nullable=True),
    sa.Column('threshold', sa.Numeric(precision=6, scale=1), nullable=True),
    sa.Column('fair_p', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('fair_source', sa.String(length=8), nullable=False),
    sa.Column('n_groups', sa.Integer(), nullable=False),
    sa.Column('disagreement', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('newest_book_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('staleness_s', sa.Integer(), nullable=True),
    sa.Column('feed_kind', sa.String(length=9), nullable=True),
    sa.Column('feed_lag_s', sa.Integer(), nullable=True),
    sa.Column('stale_allowance_s', sa.Integer(), nullable=True),
    sa.Column('pricing_version', sa.String(length=16), nullable=True),
    sa.Column('model_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_index('ix_fair_game_type_created', 'fair_values', ['game_id', 'market_type', 'created_at'], unique=False, if_not_exists=True)
    op.create_table('fills',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('prob', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('contracts', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('fee', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('fee_type', sa.String(length=32), nullable=True),
    sa.Column('fee_multiplier', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('maker_rate', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('filled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('simulated', sa.Boolean(), nullable=False),
    sa.Column('fill_method', sa.String(length=16), nullable=False),
    sa.Column('source_trade_id', sa.String(length=64), nullable=True),
    sa.Column('source_event_id', sa.BigInteger(), nullable=True),
    sa.Column('taker_side', sa.String(length=4), nullable=True),
    sa.Column('through', sa.Boolean(), nullable=False),
    sa.Column('tape_source', sa.String(length=4), nullable=True),
    sa.Column('has_print', sa.Boolean(), nullable=False),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('game_score_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('period', sa.SmallInteger(), nullable=True),
    sa.Column('clock', sa.String(length=8), nullable=True),
    sa.Column('home_score', sa.SmallInteger(), nullable=True),
    sa.Column('away_score', sa.SmallInteger(), nullable=True),
    sa.Column('raw_id', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('games',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('sport', sa.String(length=8), nullable=False),
    sa.Column('home_team_id', sa.Integer(), nullable=False),
    sa.Column('away_team_id', sa.Integer(), nullable=False),
    sa.Column('kickoff_utc', sa.DateTime(timezone=True), nullable=False),
    sa.Column('odds_api_event_id', sa.String(length=64), nullable=True),
    sa.Column('espn_event_id', sa.String(length=32), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('home_score', sa.Integer(), nullable=True),
    sa.Column('away_score', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('espn_event_id'),
    sa.UniqueConstraint('odds_api_event_id'),
    if_not_exists=True,
    )
    op.create_index('ix_games_sport_kick', 'games', ['sport', 'kickoff_utc'], unique=False, if_not_exists=True)
    op.create_table('gap_outcomes',
    sa.Column('gap_snapshot_id', sa.BigInteger(), nullable=False),
    sa.Column('benchmark_type', sa.String(length=32), nullable=False),
    sa.Column('p_bench', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_mid_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_bid_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_target_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_target_p_net', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_target_roi_net', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('p_used_kind', sa.String(length=8), nullable=True),
    sa.PrimaryKeyConstraint('gap_snapshot_id', 'benchmark_type'),
    if_not_exists=True,
    )
    op.create_table('gate_reports',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('gate_variant', sa.Boolean(), nullable=False),
    sa.Column('criteria_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('criteria_hash', sa.String(length=64), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('intents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('signal_id', sa.BigInteger(), nullable=False),
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('venue_market_id', sa.Integer(), nullable=False),
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('target_prob', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('target_contracts', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('edge', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('edge_min', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fair_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fair_row_id', sa.BigInteger(), nullable=True),
    sa.Column('game_id', sa.Integer(), nullable=True),
    sa.Column('kickoff_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stake', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('signal_created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('signal_id'),
    if_not_exists=True,
    )
    op.create_table('job_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('job', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('budget_exhausted', sa.Boolean(), nullable=False),
    sa.Column('notes', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('job_state',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key'),
    if_not_exists=True,
    )
    op.create_table('kill_switch',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('set_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('ledger',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('fill_id', sa.BigInteger(), nullable=True),
    sa.Column('ticker', sa.String(length=64), nullable=True),
    sa.Column('side', sa.String(length=4), nullable=True),
    sa.Column('contracts', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('price', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fee', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('payout', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('cash_delta', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('market_gap_snapshots',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('venue_market_id', sa.Integer(), nullable=False),
    sa.Column('fair_value_id', sa.BigInteger(), nullable=True),
    sa.Column('fair_source', sa.String(length=8), nullable=True),
    sa.Column('fair_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('no_fair_reason', sa.String(length=32), nullable=True),
    sa.Column('prev_fair_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('prev_fair_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('venue_mid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('best_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('best_ask', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('bid_size', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('ask_size', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('n_groups', sa.Integer(), nullable=False),
    sa.Column('disagreement', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('staleness_s', sa.Integer(), nullable=True),
    sa.Column('feed_kind', sa.String(length=9), nullable=True),
    sa.Column('feed_lag_s', sa.Integer(), nullable=True),
    sa.Column('stale_allowance_s', sa.Integer(), nullable=True),
    sa.Column('gap_mid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('gap_taker_net', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('gap_maker_net', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('ttk_minutes', sa.Integer(), nullable=True),
    sa.Column('dow', sa.Integer(), nullable=False),
    sa.Column('hour_ct', sa.Integer(), nullable=False),
    sa.Column('price_bucket', sa.Integer(), nullable=True),
    sa.Column('volume_24h', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('open_interest', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('soft_minus_sharp', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('home_popularity_tier', sa.Integer(), nullable=False),
    sa.Column('away_popularity_tier', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'venue_market_id', name='uq_gap_run_market'),
    if_not_exists=True,
    )
    op.create_index('ix_gap_market_created', 'market_gap_snapshots', ['venue_market_id', 'created_at'], unique=False, if_not_exists=True)
    op.create_table('markouts',
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('anchor', sa.String(length=10), nullable=False),
    sa.Column('horizon', sa.String(length=6), nullable=False),
    sa.Column('at_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('horizon_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('p_used', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fee_per_contract', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('fair_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fair_row_id', sa.BigInteger(), nullable=True),
    sa.Column('fair_book_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fair_changed', sa.Boolean(), nullable=False),
    sa.Column('fair_age_s', sa.Integer(), nullable=True),
    sa.Column('venue_mid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('mid_age_s', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=8), nullable=True),
    sa.PrimaryKeyConstraint('order_id', 'anchor', 'horizon'),
    if_not_exists=True,
    )
    op.create_table('metric_samples',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=48), nullable=False),
    sa.Column('labels', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('value', sa.Numeric(precision=18, scale=6), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('normalize_state',
    sa.Column('family', sa.String(length=32), nullable=False),
    sa.Column('last_raw_id', sa.BigInteger(), nullable=False),
    sa.PrimaryKeyConstraint('family'),
    if_not_exists=True,
    )
    op.create_table('odds_snapshots',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('raw_id', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('book', sa.String(length=32), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=True),
    sa.Column('market_type', sa.String(length=24), nullable=False),
    sa.Column('outcome_team_id', sa.Integer(), nullable=True),
    sa.Column('outcome_side', sa.String(length=8), nullable=True),
    sa.Column('point', sa.Numeric(precision=6, scale=1), nullable=True),
    sa.Column('price_decimal', sa.Numeric(precision=10, scale=4), nullable=False),
    sa.Column('book_last_update', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.execute("create index if not exists ix_odds_game_type_fetched on odds_snapshots (game_id, market_type, fetched_at)")
    op.create_table('operator_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('summary', sa.String(length=200), nullable=False),
    sa.Column('ref', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('order_clv',
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('benchmark_type', sa.String(length=32), nullable=False),
    sa.Column('p_bench', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('p_used', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('p_used_kind', sa.String(length=8), nullable=True),
    sa.Column('clv_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_p_net', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('clv_roi_net', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('stale', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('order_id', 'benchmark_type'),
    if_not_exists=True,
    )
    op.create_table('order_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=True),
    sa.Column('intent_id', sa.Uuid(), nullable=True),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('prob', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('contracts', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('fair_p_at_event', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('reason', sa.String(length=48), nullable=True),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('order_watch_samples',
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('queue_remaining', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('nw_queue_remaining', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('best_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('best_ask', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fair_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('book_dirty', sa.Boolean(), nullable=False),
    sa.Column('terminal', sa.String(length=12), nullable=True),
    sa.PrimaryKeyConstraint('order_id', 'ts'),
    if_not_exists=True,
    )
    op.create_table('orderbook_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sid', sa.Integer(), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=True),
    sa.Column('price', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('delta', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.PrimaryKeyConstraint('id', 'ts'),
    postgresql_partition_by='RANGE (ts)',
    if_not_exists=True,
    )
    op.execute("create index if not exists ix_obe_ticker_ts on orderbook_events (ticker, ts)")
    op.create_table('orderbook_snapshots',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('raw_id', sa.BigInteger(), nullable=False),
    sa.Column('venue_market_id', sa.Integer(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('yes_bids', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('no_bids', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_id'),
    if_not_exists=True,
    )
    op.create_index('ix_ob_market_fetched', 'orderbook_snapshots', ['venue_market_id', 'fetched_at'], unique=False, if_not_exists=True)
    op.create_table('orders',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('intent_id', sa.Uuid(), nullable=False),
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('mode', sa.String(length=8), nullable=False),
    sa.Column('client_order_id', sa.String(length=64), nullable=False),
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('venue_market_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('prob', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('contracts', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expiry', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.String(length=32), nullable=True),
    sa.Column('fair_p_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fair_row_id_at_place', sa.BigInteger(), nullable=True),
    sa.Column('fair_books_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('venue_bid_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('venue_ask_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('venue_mid_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('queue_ahead_at_place', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('book_source', sa.String(length=4), nullable=True),
    sa.Column('book_age_s', sa.Integer(), nullable=True),
    sa.Column('book_first_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('edge_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('edge_min_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('as_at_place', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('staleness_at_place', sa.Integer(), nullable=True),
    sa.Column('stale_allowance_at_place', sa.Integer(), nullable=True),
    sa.Column('feed_kind', sa.String(length=9), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=True),
    sa.Column('gap_snapshot_id', sa.BigInteger(), nullable=True),
    sa.Column('game_id', sa.Integer(), nullable=True),
    sa.Column('sport', sa.String(length=8), nullable=True),
    sa.Column('kickoff_utc', sa.DateTime(timezone=True), nullable=True),
    sa.Column('match_key', sa.String(length=64), nullable=True),
    sa.Column('worst_case_fill', sa.Boolean(), nullable=False),
    sa.Column('fair_cross_fill', sa.Boolean(), nullable=False),
    sa.Column('filled_contracts', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('queue_remaining', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('traded_at_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('tape_cursor_event_id', sa.BigInteger(), nullable=True),
    sa.Column('nw_filled_contracts', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('nw_queue_remaining', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('nw_traded_at_price', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('nw_tape_cursor_event_id', sa.BigInteger(), nullable=True),
    sa.Column('nw_done', sa.Boolean(), nullable=False),
    sa.Column('crossed', sa.Boolean(), nullable=False),
    sa.Column('last_print_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_print_ids', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('nw_crossed', sa.Boolean(), nullable=False),
    sa.Column('nw_last_print_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('nw_last_print_ids', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('dirty_minutes', sa.Integer(), nullable=False),
    sa.Column('dirty_seconds', sa.Integer(), nullable=False),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.Column('venue_order_id', sa.String(length=64), nullable=True),
    sa.Column('order_group_id', sa.String(length=64), nullable=True),
    sa.Column('exchange_index_at_place', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_order_id'),
    if_not_exists=True,
    )
    op.create_table('raw_responses',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('endpoint', sa.String(length=128), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('http_status', sa.Integer(), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.PrimaryKeyConstraint('id', 'fetched_at'),
    postgresql_partition_by='RANGE (fetched_at)',
    if_not_exists=True,
    )
    op.create_table('report_cells',
    sa.Column('report_run_id', sa.BigInteger(), nullable=False),
    sa.Column('table_key', sa.String(length=4), nullable=False),
    sa.Column('row_key', sa.String(length=64), nullable=False),
    sa.Column('col_key', sa.String(length=48), nullable=False),
    sa.Column('estimate', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('n_obs', sa.Integer(), nullable=True),
    sa.Column('n_clusters', sa.Integer(), nullable=True),
    sa.Column('lo', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('hi', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.Column('text', sa.String(length=64), nullable=True),
    sa.Column('flags', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.PrimaryKeyConstraint('report_run_id', 'table_key', 'row_key', 'col_key'),
    if_not_exists=True,
    )
    op.create_table('report_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('year', sa.SmallInteger(), nullable=False),
    sa.Column('week', sa.SmallInteger(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('provisional', sa.Boolean(), nullable=False),
    sa.Column('build_sha', sa.String(length=40), nullable=False),
    sa.Column('criteria_hash', sa.String(length=64), nullable=False),
    sa.Column('config_hashes', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('markdown', sa.Text(), nullable=True),
    sa.Column('markdown_sha256', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('n_requests', sa.Integer(), nullable=False),
    sa.Column('credits_used', sa.Integer(), nullable=False),
    sa.Column('odds_remaining', sa.Integer(), nullable=True),
    sa.Column('budget_exhausted', sa.Boolean(), nullable=False),
    sa.Column('notes', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('build_sha', sa.String(length=24), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('settlements',
    sa.Column('game_id', sa.Integer(), nullable=False),
    sa.Column('home_score', sa.Integer(), nullable=True),
    sa.Column('away_score', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('game_id'),
    if_not_exists=True,
    )
    op.create_table('signals',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('gap_snapshot_id', sa.BigInteger(), nullable=False),
    sa.Column('venue_market_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('fair_p', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fair_source', sa.String(length=8), nullable=True),
    sa.Column('venue_best_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('venue_best_ask', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('price_target', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('fee_at_target', sa.Numeric(precision=8, scale=4), nullable=True),
    sa.Column('as_estimate', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('edge', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('edge_min', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('stake', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('contracts', sa.Integer(), nullable=True),
    sa.Column('decision', sa.String(length=12), nullable=False),
    sa.Column('rejection_reason', sa.String(length=48), nullable=True),
    sa.Column('labels', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('as_measured', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('replay', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'variant_id', 'venue_market_id', 'side', 'replay', name='uq_signal_key'),
    if_not_exists=True,
    )
    op.create_index('ix_signal_market_created', 'signals', ['venue_market_id', 'created_at'], unique=False, if_not_exists=True)
    op.create_index('ix_signal_variant_created', 'signals', ['variant_id', 'created_at'], unique=False, if_not_exists=True)
    op.create_table('source_state',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('last_fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key'),
    if_not_exists=True,
    )
    op.create_table('strategy_variants',
    sa.Column('variant_id', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('tier', sa.String(length=16), nullable=False),
    sa.Column('config_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('registered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('variant_id'),
    sa.UniqueConstraint('name'),
    if_not_exists=True,
    )
    op.create_table('team_aliases',
    sa.Column('sport', sa.String(length=8), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('raw_name', sa.String(length=160), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('sport', 'source', 'raw_name'),
    if_not_exists=True,
    )
    op.create_index('ix_alias_sport_team', 'team_aliases', ['sport', 'team_id'], unique=False, if_not_exists=True)
    op.create_table('teams',
    sa.Column('sport', sa.String(length=8), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('display_name', sa.String(length=128), nullable=False),
    sa.Column('location', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('abbreviation', sa.String(length=16), nullable=False),
    sa.Column('short_display_name', sa.String(length=64), nullable=False),
    sa.Column('popularity_tier', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('sport', 'id'),
    if_not_exists=True,
    )
    op.create_table('trade_watermarks',
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('last_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_volume_fp', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.PrimaryKeyConstraint('ticker'),
    if_not_exists=True,
    )
    op.create_table('venue_markets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('event_ticker', sa.String(length=64), nullable=False),
    sa.Column('series_ticker', sa.String(length=32), nullable=False),
    sa.Column('game_id', sa.Integer(), nullable=True),
    sa.Column('market_type', sa.String(length=16), nullable=False),
    sa.Column('threshold', sa.Numeric(precision=6, scale=1), nullable=True),
    sa.Column('side_team_id', sa.Integer(), nullable=True),
    sa.Column('side', sa.String(length=8), nullable=True),
    sa.Column('kalshi_team_uuid', sa.String(length=64), nullable=True),
    sa.Column('close_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('match_confidence', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('match_status', sa.String(length=16), nullable=False),
    sa.Column('match_reason', sa.Text(), nullable=False),
    sa.Column('first_seen_raw_id', sa.BigInteger(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expected_expiration_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('price_level_structure', sa.String(length=48), nullable=True),
    sa.Column('price_ranges', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('fee_type', sa.String(length=32), nullable=True),
    sa.Column('fee_multiplier', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('exchange_index', sa.Integer(), nullable=False),
    sa.Column('match_key', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticker'),
    if_not_exists=True,
    )
    op.create_index(op.f('ix_venue_markets_game_id'), 'venue_markets', ['game_id'], unique=False, if_not_exists=True)
    op.create_table('venue_quotes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('raw_id', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('venue_market_id', sa.Integer(), nullable=False),
    sa.Column('yes_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('yes_ask', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('no_bid', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('no_ask', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('yes_bid_size', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('yes_ask_size', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('volume', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('volume_24h', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('open_interest', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('updated_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('raw_id', 'venue_market_id', name='uq_quote_raw_market'),
    if_not_exists=True,
    )
    op.execute("create index if not exists ix_quotes_market_fetched on venue_quotes (venue_market_id, fetched_at)")
    op.create_table('venue_requests',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('env', sa.String(length=8), nullable=False),
    sa.Column('method', sa.String(length=8), nullable=False),
    sa.Column('path', sa.String(length=128), nullable=False),
    sa.Column('status', sa.Integer(), nullable=True),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('elapsed_ms', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    if_not_exists=True,
    )
    op.create_table('venue_settlements',
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('result', sa.String(length=4), nullable=True),
    sa.Column('payout', sa.Numeric(precision=3, scale=2), nullable=True),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('raw_id', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('venue', 'ticker', 'source'),
    if_not_exists=True,
    )
    op.create_table('venue_status',
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('env', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=120), nullable=True),
    sa.Column('since', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('venue', 'env'),
    if_not_exists=True,
    )
    op.create_table('venue_trades',
    sa.Column('venue', sa.String(length=16), nullable=False),
    sa.Column('trade_id', sa.String(length=64), nullable=False),
    sa.Column('ticker', sa.String(length=64), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('yes_price', sa.Numeric(precision=6, scale=4), nullable=False),
    sa.Column('count', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('taker_side', sa.String(length=4), nullable=False),
    sa.Column('taker_outcome_side', sa.String(length=4), nullable=True),
    sa.Column('taker_book_side', sa.String(length=4), nullable=True),
    sa.Column('is_block', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=4), nullable=False),
    sa.Column('raw_id', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('venue', 'trade_id', 'ts'),
    postgresql_partition_by='RANGE (ts)',
    if_not_exists=True,
    )
    op.execute("create index if not exists ix_trades_ticker_ts on venue_trades (ticker, ts)")

    # --- columns added after a table first shipped (create_schema's _COLUMN_DDL) ---
    op.execute("alter table market_gap_snapshots add column if not exists no_fair_reason varchar(32)")
    op.execute("alter table fair_values add column if not exists feed_kind varchar(9)")
    op.execute("alter table fair_values add column if not exists feed_lag_s integer")
    op.execute("alter table fair_values add column if not exists stale_allowance_s integer")
    op.execute("alter table fair_values add column if not exists pricing_version varchar(16)")
    op.execute("alter table market_gap_snapshots add column if not exists feed_kind varchar(9)")
    op.execute("alter table market_gap_snapshots add column if not exists feed_lag_s integer")
    op.execute("alter table market_gap_snapshots add column if not exists stale_allowance_s integer")
    op.execute("alter table venue_markets add column if not exists expected_expiration_time timestamptz")
    op.execute("alter table venue_markets add column if not exists price_level_structure varchar(48)")
    op.execute("alter table venue_markets add column if not exists price_ranges jsonb")
    op.execute("alter table venue_markets add column if not exists fee_type varchar(32)")
    op.execute("alter table venue_markets add column if not exists fee_multiplier numeric(6,4)")
    op.execute("alter table venue_markets add column if not exists exchange_index integer not null default 0")
    op.execute("alter table venue_markets add column if not exists match_key varchar(64)")
    op.execute("alter table runs add column if not exists build_sha varchar(24)")
    op.execute("alter table signals add column if not exists as_measured numeric(6,4)")
    op.execute("alter table orders add column if not exists crossed boolean not null default false")
    op.execute("alter table orders add column if not exists last_print_ts timestamptz")
    op.execute("alter table orders add column if not exists last_print_ids jsonb")
    op.execute("alter table orders add column if not exists nw_crossed boolean not null default false")
    op.execute("alter table orders add column if not exists nw_last_print_ts timestamptz")
    op.execute("alter table orders add column if not exists nw_last_print_ids jsonb")
    op.execute("alter table orders add column if not exists dirty_seconds integer not null default 0")
    op.execute("alter table equity_snapshots add column if not exists peak_equity_7d numeric(12,2)")
    op.execute("alter table equity_snapshots add column if not exists drawdown_pct numeric(6,4)")
    op.execute("alter table equity_snapshots add column if not exists drawdown_stop boolean")
    op.execute("alter table orders add column if not exists venue_order_id varchar(64)")
    op.execute("alter table orders add column if not exists order_group_id varchar(64)")
    op.execute("alter table orders add column if not exists exchange_index_at_place integer")

    # --- the partial, functional and BRIN indexes (create_schema's _INDEX_DDL) -----
    op.execute("create index if not exists ix_raw_source_fetched on raw_responses (source, fetched_at)")
    op.execute("create index if not exists ix_raw_fetched_brin on raw_responses using brin (fetched_at)")
    op.execute("create index if not exists ix_raw_run on raw_responses (run_id)")
    op.execute(
        "create unique index if not exists uq_odds_snapshot_row on odds_snapshots (raw_id, book, "
        "market_type, coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), coalesce(point, 0))")
    op.execute(
        "create unique index if not exists uq_fair_value_row on fair_values (run_id, game_id, "
        "market_type, coalesce(outcome_team_id,-1), coalesce(outcome_side,''), coalesce(threshold,0), "
        "fair_source)")
    op.execute(
        "create unique index if not exists uq_open_order on orders (venue, ticker, side, variant_id) "
        "where status in ('open', 'partially_filled') and replay = false")
    op.execute("create index if not exists ix_orders_status on orders (status, replay)")
    op.execute("create index if not exists ix_orders_nw on orders (nw_done, expiry) where nw_done = false")
    op.execute("create index if not exists ix_orders_game on orders (game_id)")
    op.execute(
        "create index if not exists ix_intents_key on intents (variant_id, venue_market_id, side, "
        "signal_created_at desc)")
    op.execute(
        "create unique index if not exists uq_order_event on order_events (order_id, kind, ts) where "
        "order_id is not null")
    op.execute(
        "create unique index if not exists uq_skip_once on order_events (intent_id, kind, reason) "
        "where kind in ('skipped', 'cap_gate')")
    op.execute(
        "create unique index if not exists uq_fill_source on fills (order_id, fill_method, "
        "coalesce(source_trade_id, ''), coalesce(source_event_id, -1))")
    op.execute("create index if not exists ix_fills_order on fills (order_id)")
    op.execute("create index if not exists ix_fills_filled_at on fills (filled_at)")
    op.execute(
        "create unique index if not exists uq_benchmark_row on benchmarks (game_id, market_type, "
        "coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), coalesce(threshold, 0), "
        "benchmark_type)")
    op.execute("create unique index if not exists uq_ledger_fill on ledger (fill_id, kind)")
    op.execute("create index if not exists ix_job_runs on job_runs (job, started_at desc)")
    op.execute("create index if not exists ix_markouts_as_measured on markouts (anchor, horizon, at_ts)")
    op.execute("create unique index if not exists uq_gate_report on gate_reports (evaluated_at, variant_id)")
    op.execute("create index if not exists ix_metric_samples_name_ts on metric_samples (name, ts desc)")
    op.execute("create index if not exists ix_operator_events_ts on operator_events (ts desc)")
    op.execute(
        "create index if not exists ix_game_score_events_game_ts on game_score_events (game_id, ts "
        "desc)")
    op.execute("create index if not exists ix_check_results_ts on check_results (ts desc)")
    op.execute(
        "create index if not exists ix_report_runs_week on report_runs (year, week, generated_at "
        "desc)")
    op.execute("create index if not exists ix_venue_requests_ts on venue_requests (ts desc)")
    op.execute("create index if not exists ix_equity_variant_ts on equity_snapshots (variant_id, ts)")

    # --- the views (create_schema's _VIEW_DDL) -------------------------------------
    op.execute("""
create or replace view positions as
select o.variant_id,
       o.ticker,
       o.side,
       sum(f.contracts) as open_contracts,
       sum(f.contracts * f.prob) / nullif(sum(f.contracts), 0) as avg_price
from fills f
join orders o on o.id = f.order_id
where f.fill_method in ('queue_model', 'venue')
  and o.replay = false
  and o.status <> 'settled'
group by o.variant_id, o.ticker, o.side
""")
    op.execute("""
create or replace view clv as
select c.order_id,
       o.variant_id,
       o.side,
       c.benchmark_type,
       c.p_bench,
       c.p_used,
       c.clv_p,
       c.clv_p_net,
       c.clv_roi_net,
       c.stale
from order_clv c
join orders o on o.id = c.order_id
""")
    op.execute("""
create or replace view order_episodes as
with recursive ordered as (
    select o.id,
           o.variant_id,
           o.venue_market_id,
           o.side,
           o.placed_at,
           lag(o.id) over w as prev_id,
           lag(o.cancel_reason) over w as prev_cancel_reason,
           lag(o.cancelled_at) over w as prev_cancelled_at
    from orders o
    where o.replay = false
    window w as (partition by o.variant_id, o.venue_market_id, o.side order by o.placed_at, o.id)
),
linked as (
    select id, variant_id, venue_market_id, side,
           case when prev_cancel_reason = 'reprice' and prev_cancelled_at <= placed_at
                then prev_id end as parent_id
    from ordered
),
chain as (
    select id, id as episode_id, variant_id, venue_market_id, side
    from linked
    where parent_id is null
    union all
    select l.id, c.episode_id, l.variant_id, l.venue_market_id, l.side
    from linked l
    join chain c on l.parent_id = c.id
)
select episode_id,
       variant_id,
       venue_market_id,
       side,
       min(id) as first_order_id,
       max(id) as last_order_id,
       count(*)::int as n_orders
from chain
group by episode_id, variant_id, venue_market_id, side
""")

    # --- the one additive backfill (create_schema's _BACKFILL_DDL) -----------------
    op.execute(
        "update venue_markets set match_key = game_id::text || ':' || market_type || ':' || "
        "coalesce(side_team_id::text, '') || ':' || coalesce(side, '') || ':' || "
        "coalesce(threshold::text, '') where match_key is null and game_id is not null")

    # --- the tape: BRIN, the two deprecated-field columns, then the btrees ---------
    op.execute("create index if not exists ix_obe_ts_brin on orderbook_events using brin (ts)")
    op.execute("create index if not exists ix_trades_ts_brin on venue_trades using brin (ts)")
    op.execute("alter table venue_trades add column if not exists taker_outcome_side varchar(4)")
    op.execute("alter table venue_trades add column if not exists taker_book_side varchar(4)")

    op.execute(
        "create index if not exists ix_obe_snapshot on orderbook_events (ticker, ts desc) where kind "
        "= 'snapshot'")
    op.execute("create index if not exists ix_obe_ticker_id on orderbook_events (ticker, id)")
    op.execute("create index if not exists ix_obe_gap on orderbook_events (sid, id) where kind = 'gap'")
    op.execute("create index if not exists ix_trades_venue_trade_id on venue_trades (venue, trade_id)")

    # --- the one index create_schema builds CONCURRENTLY ---------------------------
    concurrent_index("ix_fair_created_brin", "fair_values", ['created_at'], using='brin')


def downgrade() -> None:
    raise NotImplementedError(
        "phase 4 baseline: rollback is git checkout + make deploy-nas, "
        "see docs/runbooks/alembic.md")
