"""fix 35: the RFQ quote's covering index on fair_values

Revision ID: 0005_rfq_lookup
Revises: 0004_phase5
Create Date: 2026-09-11

The 03:15-03:45 CT incident (journal 109): 4,902 frames in 30 minutes, almost all combos on
non-football series the harness never prices, every leg of every one running the RFQ quote's
`fair_values` lateral (`_LEG` in `harness/venues/kalshi/rfq_quote.py`) to find nothing --
16,264 rows scanned to keep 236, 14 s cold. The application half of fix 35 makes that lateral run
only for a combo already known, cheaply, to be all priced football markets; this revision is the
other half, an index that covers the lateral's own five-column shape (and `_CLOSING_LEG`'s in
`harness/settlement/rfq_grade.py`, the settlement-time read of the same shape) so the scan that
does still run is an index scan, not a walk down the wider `ix_fair_game_type_created (game_id,
market_type, created_at)` with every candidate row's `outcome_team_id`/`outcome_side`/`threshold`
rechecked by Postgres rather than the index.

`create_schema`'s `_CONCURRENT_INDEX_DDL` (`harness/db/schema.py`) carries the identical
statement; the two must land together or `tests/test_alembic.py`'s catalogue diff fails. Not
routed through `migrations.env.concurrent_index`: that helper builds a plain `(cols)` index and
has no way to add this index's partial predicate (`where fair_source = 'direct'`), so this
migration takes the statement out of the transaction the same way that helper does --
`op.get_context().autocommit_block()` -- and issues the identical raw SQL directly. CONCURRENTLY
because `fair_values` takes a write on every pricing tick and `init-db` runs on every deploy; the
connection is already AUTOCOMMIT, which is what CONCURRENTLY requires.

`downgrade()` is `pass` (roadmap invariant 5, the same as every revision since 0002): additive
only, and rolling back is a code rollback (`git checkout <sha> && make deploy-nas-app`), never a
schema one.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005_rfq_lookup"
down_revision: str | None = "0004_phase5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The identical statement as `harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL` entry --
#: `tests/test_alembic.py`'s `test_the_rfq_lookup_ddl_agrees_between_schema_and_migration`
#: compares the two Python string values (not the raw file text either copy happens to be
#: line-wrapped as) so a hand-edit to one copy cannot drift from the other unnoticed.
_INDEX_DDL = ("create index concurrently if not exists ix_fair_leg_lookup "
              "on fair_values (game_id, market_type, outcome_team_id, outcome_side, threshold, "
              "created_at desc) where fair_source = 'direct'")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_INDEX_DDL)


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is
    # actually done.
    pass
