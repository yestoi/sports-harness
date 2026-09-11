"""fix 42: the pricing read's index on venue_quotes (run_id, venue_market_id)

Revision ID: 0006_quotes_run_index
Revises: 0005_rfq_lookup
Create Date: 2026-09-11

From 13:03 CT on 2026-09-11 every pricing run on the NAS failed. `build_gap_snapshots`
(`harness/pricing/gaps.py`) reads a whole run's quotes -- `venue_quotes.run_id = :run_id`, joined
to `venue_markets` on a matched `match_status` and to `games` on a kickoff inside the last four
hours or later -- and `venue_quotes` (3.58 M rows, 773 MB, one of the five bulk tables) carried
only `venue_quotes_pkey (id)`, `uq_quote_raw_market (raw_id, venue_market_id)` and
`ix_quotes_market_fetched (venue_market_id, fetched_at)`. Not one of them leads with `run_id`.

The plan, EXPLAINed on the NAS at 14:10 CT, was a nested loop: for each of the 1,871 matched
markets with a future kickoff, an index scan of `ix_quotes_market_fetched` on that market with
`run_id` as a *filter* -- so every quote ever recorded for the market was walked to keep the
three or so belonging to this run. Cost 52,000, cold on a swapping NAS, past the 30 s
`statement_timeout` on every attempt (`QueryCanceled`): `runs.status = degraded`, `pricing = {}`,
no fair values, no signals, and the executor's open orders at 0 before the evening's games. The
weekday cadence and Thursday's smaller matched set hid it; Friday noon, with Saturday's ~85 games
matched, crossed the line. `(run_id, venue_market_id)` makes `run_id` the scan key and the join
column the second, so the whole read is one index scan.

CONCURRENTLY because `venue_quotes` is a bulk table the recorder is inserting into and `init-db`
runs on every deploy -- fix 25's F65 rule is that every index on a bulk table is built
CONCURRENTLY, no carve-out. Not routed through `migrations.env.concurrent_index`, but for a
different reason than `0005_rfq_lookup`: that revision's index carries a partial predicate the
helper has no way to express, while this one the helper could build verbatim. What it cannot do
is hold the statement as one module-level string identical to `harness/db/schema.py`'s copy,
which is what `tests/test_alembic.py` compares to keep the two from drifting. The statement is
taken out of the surrounding transaction with `op.get_context().autocommit_block()` -- the same
thing the helper does -- which is what CONCURRENTLY requires.

`if not exists` is load-bearing here rather than merely defensive: the controller built this
index by hand on the NAS at 14:15 CT as an emergency measure, so both this revision and
`init-db` find it already present on the live database and do nothing.

`create_schema`'s `_CONCURRENT_INDEX_DDL` carries the identical statement and
`VenueQuote.__table_args__` declares the index to `create_all`; all three must land together or
`tests/test_alembic.py`'s catalogue diff fails.

`downgrade()` is `pass` (roadmap invariant 5, the same as every revision since 0002): additive
only, and rolling back is a code rollback (`git checkout <sha> && make deploy-nas-app`), never a
schema one. Removing this index is non-additive and therefore a gate the user opens by hand, not
something a revision the loop wrote may do on its own.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0006_quotes_run_index"
down_revision: str | None = "0005_rfq_lookup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The identical statement as `harness/db/schema.py`'s `_CONCURRENT_INDEX_DDL` entry --
#: `tests/test_alembic.py`'s `test_the_quotes_run_index_ddl_agrees_between_schema_and_migration`
#: compares the two Python string values (not the raw file text either copy happens to be
#: line-wrapped as) so a hand-edit to one copy cannot drift from the other unnoticed.
_INDEX_DDL = ("create index concurrently if not exists ix_quotes_run_market "
              "on venue_quotes (run_id, venue_market_id)")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_INDEX_DDL)


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is
    # actually done.
    pass
