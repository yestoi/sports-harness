"""carried fix 56 (second row): the `positions` view decides open by the fill, not the status

Revision ID: 0008_positions_open_fill
Revises: 0007_raw_events_lookup
Create Date: 2026-09-13

Order 157 (`KXNCAAFTOTAL-26SEP12MTUMRSH-59`) filled 38.92 YES at 0.45 by `queue_model` and was
then cancelled `fair_stale`. Game 408 went final and the settle job wrote the fill's ledger
`settlement` row (payout 0) on 2026-09-13 at 03:02:29Z, but the order still reads
`status = cancelled`: `harness/settlement/settle.py`'s `SETTLEABLE` is
`("filled", "partially_filled")`, because a cancelled or expired order's status is the
executor's record of what it did. Every reader of open positions asked only whether the status
had become `settled`, so the paid fill stayed "open" forever -- listed under the legacy page's
exposure, counted against the executor's caps by `store.load_positions`, and carried in
`open_stake`/`mtm_open`. Every partially filled order that is later cancelled or expires strands
the same way.

The fix is one shared predicate, `harness/db/schema.py`'s `OPEN_FILL_SQL`: a fill is open while
the order is not `settled` **and** no ledger row with `kind = 'settlement'` exists for it. The
`positions` view, `harness/execution/store.py`'s `_POSITIONS` and
`harness/dashboard/snapshots/floor.py`'s `_EXPOSURE` all embed that one string.

Why this revision exists at all. `create_schema` owns the views and re-issues `_VIEW_DDL` at
every full deploy, so the deployed database gets the new text from `init-db` without Alembic.
What a migration is needed for is history: `0001_baseline` carries this view's *previous* text,
and `tests/test_alembic.py`'s catalogue check compares a database built by `upgrade_head` with
one built by `create_schema`, view definitions included. Without this file those two databases
disagree. `0001_baseline` is history and is not edited -- it is the schema of its own commit --
and the catalogue check is not relaxed; the controller ruled (hotfix 2026-09-12/13 ledger) that
the new revision is the additive way out.

`create or replace view` is additive in the sense conformance item 4 means: the column list is
unchanged, the statement creates the object where it is absent and replaces its body where it is
present, it takes no lock any deploy does not already take through `init-db`, and no row is
touched. Revision `0004_phase5` already issues a view this same way. `tests/test_alembic.py`'s
FORBIDDEN list bans the Alembic op helper that replaces a view and the SQL that takes one away;
this file uses neither -- it is one `op.execute` of the same statement `create_schema` runs.

`_VIEW_DDL` below is `harness/db/schema.py`'s `_POSITIONS_VIEW` with `OPEN_FILL_SQL` expanded,
byte for byte -- the long predicate line is wrapped with a backslash continuation, which does not
change the string's value. `test_the_positions_view_ddl_agrees_between_schema_and_migration`
compares the two Python values, so a later edit to the view in `schema.py` that forgets this
copy fails there rather than in a catalogue diff nobody is looking at.

`downgrade()` is `pass` (roadmap invariant 5, as every revision since `0002_phase45` except
`0007_raw_events_lookup`): re-issuing a view has nothing additive to undo, the previous text
lives in `0001_baseline`, and a rollback here is a code rollback -- `git checkout <sha> &&
make deploy-omarchy-app` -- whose `init-db` puts the old text back through `create_schema`.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008_positions_open_fill"
down_revision: str | None = "0007_raw_events_lookup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The identical text as `harness/db/schema.py`'s `_POSITIONS_VIEW` after `OPEN_FILL_SQL` is
#: expanded into it. Compared as the Python string values the two modules hold, not as raw file
#: text, so the backslash continuation below is free to wrap a line the other copy does not.
_VIEW_DDL = """
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
  and o.status <> 'settled' and not exists (\
select 1 from ledger l where l.fill_id = f.id and l.kind = 'settlement')
group by o.variant_id, o.ticker, o.side
"""


def upgrade() -> None:
    op.execute(_VIEW_DDL)


def downgrade() -> None:
    # Nothing to undo: see the module docstring (roadmap invariant 5, and the view's previous
    # text is in `0001_baseline` where a code rollback's `init-db` finds it).
    pass
