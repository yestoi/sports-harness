"""phase 4.6: the fun-ticket columns, the three new tables and the story indexes

Revision ID: 0009_phase46_fun_tickets
Revises: 0008_positions_open_fill
Create Date: 2026-09-14

An additive mirror of `harness/db/models.py` and `harness/db/schema.py`. The models and
`create_schema` stay the schema authority; this file records the history, and
`tests/test_alembic.py` builds one database each way and compares the catalogues -- columns,
types, nullability, keys, indexes and views -- so the two cannot drift apart quietly. The four
statement tuples below are the identical strings `harness/db/schema.py` runs (`_COLUMN_DDL`,
`_INDEX_DDL`, `_CONCURRENT_INDEX_DDL`), which is what `test_the_phase46_statements_match_create_
schema_exactly` checks; the tables are the one part `create_schema` does not spell out, because
`Base.metadata.create_all` builds a table this phase adds from the model itself.

What the phase adds, and why each piece is additive (addendum section 9):

* Twenty-two columns on five existing tables -- eleven on `parlay_legs` (the prop leg's player,
  stat, period, operator, market definition, deep link, build probability and context line),
  eight on `parlay_cards` (the policy version, the replacement link, the decline reason, the
  combined price and its capability), `parlay_placements.confirmation_id`,
  `parlay_ledger.source` and `source_state.credits_used`. Every one is `ADD COLUMN IF NOT
  EXISTS`; each not-null one carries the same default the model declares, so an existing row
  reads the documented value and nothing is backfilled. On PostgreSQL 16 an ADD COLUMN with a
  non-volatile default is metadata-only, so none of these rewrites a table.
* Three tables, each `CREATE TABLE IF NOT EXISTS`: `odds_prop_snapshots` (the prop pool, D23),
  `parlay_placement_corrections` (the owner's corrections, section 5.2) -- `players` and
  `player_stat_events` came with Tasks 3's models and are created here for the same reason.
  Their indexes are built plainly: a table this same revision creates takes no concurrent
  writes, so none of them is a bulk-table index.
* The story indexes of section 7.2 on `intents`, `order_events`, `fills` and `ledger`, every one
  CONCURRENTLY inside an autocommit block. Those four tables take the executor's writes on its
  15 s loop while `init-db` runs on every deploy, and fix 25's F65 rule is that every index on a
  table under a live writer is built CONCURRENTLY, no carve-out (plan review CR-4, D13). CREATE
  INDEX CONCURRENTLY cannot run inside a transaction, and a plain build would take a ShareLock
  on a table the executor is writing to for the whole build. The fifth index that section names,
  `ix_gap_outcomes_order on gap_outcomes (order_id)`, is absent: `gap_outcomes` is keyed
  `(gap_snapshot_id, benchmark_type)` and has no `order_id` column on any branch, so the
  statement cannot be written; the read reaches a gap outcome through `market_gap_snapshots`,
  whose id is this table's leading primary-key column, so the primary key already serves it.
  Recorded by 4.6 Task 4 for the controller.

`odds_snapshots` is untouched, every index on it included (D23, which replaced plan review
CR-2). A prop outcome is keyed by the player and `uq_odds_snapshot_row` carries no `where`
clause, so two scorers in one `prop:anytime_td` market are one key to it; the only fix inside
`odds_snapshots` would be rebuilding a unique index on a bulk table, which is not additive and
is a gate. A new table is additive, so the props live in `odds_prop_snapshots` instead.

The one `alter column ... type`. `parlay_legs.market_type` widens `varchar(6)` to `varchar(12)`
so the internal prop keys of section 3.3 (`prop:anytime_td`) fit beside `ml|spread|total`
(addendum section 14.4, conformance item 4). It is the single line the phase audit's
`alter column .* type` grep finds, and it is explained in the addendum, in the plan and here. It
is additive in meaning: no value is lost, no row is read, and `parlay_legs` holds tens of rows,
so the widening needs no table rewrite on PostgreSQL 16 -- a varchar length increase is a
catalogue change. `tests/test_alembic.py` keeps the rule rather than relaxing it: the literal
left the `FORBIDDEN` grep and `_ALLOWED_ALTER_COLUMN` now matches this exact statement and no
other, over what each revision *executes*, so a second widening anywhere still fails.

`downgrade()` is `pass`, deliberately. Roadmap invariant 5 is that nothing non-additive ever
runs, and the phase audit greps every migration for non-additive statements. Rolling back is
`git checkout <sha> && make deploy-omarchy-app`, which never runs `migrate ensure`, and old code
survives because it ignores the new columns and the new tables. A later *full* deploy on a
rolled-back sha aborts at `ensure` and needs a hand `alembic stamp 0008_positions_open_fill`
first; that is the user's action, never the loop's.

The revision number (D9, and the controller's ruling of 2026-09-14). D9 says the number is
assigned at merge time, verbatim: "Phase 6B's plan names its revision `0008_phase6b_execution`
on the branch `phase6b-repair-execution`, which is not on `main`; the controller renumbers
whichever of the two merges second to `0009` and updates its `down_revision` in a scoped,
re-reviewed rebase." Both numbers in that sentence moved by one before this file was written:
fix 56 merged `0008_positions_open_fill` to main on 2026-09-13, so this phase's revision is
`0009` on top of it, 6B's unmerged `0008_phase6b_execution` and 6D's unmerged
`0009_phase6d_sustained_evaluation` are the other two claims on those numbers, and whichever
branch merges second is renumbered by the controller at merge time, in that same scoped,
re-reviewed commit. No task in this plan waits for 6B or 6D, and neither plan is changed by
this one.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009_phase46_fun_tickets"
down_revision: str | None = "0008_positions_open_fill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Byte-identical to the phase 4.6 block of `harness/db/schema.py`'s `_COLUMN_DDL`.
_COLUMNS = (
    "alter table parlay_legs add column if not exists player_id integer",
    "alter table parlay_legs add column if not exists stat varchar(12)",
    "alter table parlay_legs add column if not exists period varchar(6) not null default 'game'",
    "alter table parlay_legs add column if not exists operator varchar(8)",
    "alter table parlay_legs add column if not exists market_def varchar(120)",
    "alter table parlay_legs add column if not exists dk_link varchar(300)",
    "alter table parlay_legs add column if not exists dk_sid varchar(64)",
    "alter table parlay_legs add column if not exists offered boolean not null default true",
    "alter table parlay_legs add column if not exists p_at_build numeric(6,4)",
    "alter table parlay_legs add column if not exists p_source varchar(10) not null default 'none'",
    "alter table parlay_legs add column if not exists context_text varchar(80)",
    "alter table parlay_legs alter column market_type type varchar(12)",
    "alter table parlay_cards add column if not exists policy_version varchar(16)",
    "alter table parlay_cards add column if not exists parent_card_id integer",
    "alter table parlay_cards add column if not exists declined_reason varchar(16)",
    "alter table parlay_cards add column if not exists combined_kind varchar(10) not null "
    "default 'calculated'",
    "alter table parlay_cards add column if not exists dk_combined_american integer",
    "alter table parlay_cards add column if not exists dk_combined_at timestamptz",
    "alter table parlay_cards add column if not exists link_capability varchar(10) not null "
    "default 'none'",
    "alter table parlay_cards add column if not exists p_source_min varchar(10) not null "
    "default 'sharp'",
    "alter table parlay_placements add column if not exists confirmation_id varchar(36)",
    "alter table parlay_ledger add column if not exists source varchar(10) not null "
    "default 'computed'",
    "alter table source_state add column if not exists credits_used bigint",
)

#: The three tables this phase adds. `create_schema` has no copy of these: `create_all` builds a
#: new table from its model. Written to match what `create_all` emits column for column --
#: SERIAL/BIGSERIAL for the autoincrementing primary keys, `timestamptz` for every timestamp,
#: and no server default on `player_stat_events.correction`, whose model default is client-side.
_TABLES = (
    "create table if not exists odds_prop_snapshots ("
    "id bigserial not null, "
    "raw_id bigint not null, "
    "book varchar(32) not null, "
    "game_id integer, "
    "market_type varchar(24) not null, "
    "player_name varchar(80) not null, "
    "player_id integer, "
    "outcome_side varchar(8), "
    "point numeric(6, 1), "
    "price_decimal numeric(10, 4) not null, "
    "book_last_update timestamptz, "
    "fetched_at timestamptz not null, "
    "link varchar(300), "
    "sid varchar(64), "
    "primary key (id))",
    "create table if not exists players ("
    "id serial not null, "
    "sport varchar(8) not null, "
    "espn_id varchar(16) not null, "
    "name varchar(80) not null, "
    "team_id integer, "
    "position varchar(6), "
    "updated_at timestamptz not null, "
    "primary key (id))",
    "create table if not exists player_stat_events ("
    "id bigserial not null, "
    "game_id integer not null, "
    "player_id integer not null, "
    "ts timestamptz not null, "
    "source_ts timestamptz, "
    "stat varchar(12) not null, "
    "value numeric(8, 2) not null, "
    "source varchar(12) not null, "
    "raw_id bigint, "
    "correction boolean not null, "
    "primary key (id))",
    "create table if not exists parlay_placement_corrections ("
    "id serial not null, "
    "card_id integer not null, "
    "ts timestamptz not null, "
    "field varchar(16) not null, "
    "old_value varchar(32), "
    "new_value varchar(32), "
    "note varchar(200), "
    "primary key (id))",
)

#: Byte-identical to the phase 4.6 block of `harness/db/schema.py`'s `_INDEX_DDL`. Every one
#: rides a table this revision creates, or `parlay_placements`, which one hand writes twice a
#: week: none is a bulk-table index, so none needs CONCURRENTLY.
_INDEXES = (
    "create unique index if not exists uq_odds_prop_row on odds_prop_snapshots "
    "(raw_id, book, market_type, player_name, coalesce(outcome_side, ''), coalesce(point, 0))",
    "create index if not exists ix_odds_prop_lookup on odds_prop_snapshots "
    "(game_id, market_type, player_id, fetched_at desc) where player_id is not null",
    "create index if not exists ix_parlay_corrections_card_ts "
    "on parlay_placement_corrections (card_id, ts)",
    "create unique index if not exists uq_players_sport_espn on players (sport, espn_id)",
    "create index if not exists ix_player_stat_game_player_ts "
    "on player_stat_events (game_id, player_id, ts desc)",
    "create unique index if not exists uq_parlay_placement_confirmation "
    "on parlay_placements (confirmation_id) where confirmation_id is not null",
)

#: Byte-identical to the phase 4.6 block of `harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL`.
_CONCURRENT = (
    "create index concurrently if not exists ix_intents_market_created "
    "on intents (venue_market_id, created_at)",
    "create index concurrently if not exists ix_order_events_order_ts "
    "on order_events (order_id, ts)",
    "create index concurrently if not exists ix_fills_order_ts on fills (order_id, filled_at)",
    "create index concurrently if not exists ix_ledger_order on ledger (order_id)",
)


def upgrade() -> None:
    for statement in _COLUMNS + _TABLES + _INDEXES:
        op.execute(statement)
    with op.get_context().autocommit_block():
        for statement in _CONCURRENT:
            op.execute(statement)


def downgrade() -> None:
    pass
