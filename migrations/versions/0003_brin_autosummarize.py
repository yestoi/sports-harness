"""fix 32: the one alterable BRIN index gets autosummarize = on

Revision ID: 0003_brin_autosummarize
Revises: 0002_phase45
Create Date: 2026-09-10

`harness/db/schema.py`'s four `using brin (...)` DDL statements now declare `with (autosummarize
= on)` directly, so a fresh `create_schema` database carries the option from each index's first
CREATE. `upgrade_head` replays 0001/0002 as they always have; without this revision,
`ix_fair_created_brin` would carry the option on one side and not the other, forever --
`tests/test_alembic.py`'s inspector surfaces an index's storage parameters
(`dialect_options['postgresql_with']`), so the catalogue diff would never converge.

Just the one, not the other three: `ix_raw_fetched_brin`, `ix_obe_ts_brin` and `ix_trades_ts_brin`
are missing here on purpose. `raw_responses`, `orderbook_events` and `venue_trades` are each
declared `postgresql_partition_by` in `0001_baseline` itself -- partitioned parents from the very
statement that creates them, on both this path and `create_schema`'s -- and Postgres refuses
`ALTER INDEX ... SET` on a partitioned index outright ("not supported for partitioned indexes"),
children or none; there is no ALTER that could add the option after the fact. `0001_baseline`'s
own CREATE for each of those three indexes carries the option directly instead (see the comments
there), which is why this revision does not touch them.

`ix_fair_created_brin` (`fair_values`) is never partitioned, so `ALTER INDEX ... SET` on it is
exactly the metadata-only flip it is anywhere else. Named, not the `pg_class`/`pg_am` sweep
`create_schema` also runs: a migration is a fixed, known set of objects.

`downgrade()` is `pass` (roadmap invariant 5, same as 0002): autosummarize is a habit, not a
rollback boundary, and Alembic's history does not distinguish "removed" from "never had it".
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003_brin_autosummarize"
down_revision: str | None = "0002_phase45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter index if exists ix_fair_created_brin set (autosummarize = on)")


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is
    # actually done.
    pass
