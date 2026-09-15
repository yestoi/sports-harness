"""fix 64 (journal 207): the score-correction marker on game_score_events

Revision ID: 0009_score_correction
Revises: 0008_positions_open_fill
Create Date: 2026-09-14

`game_score_went_down_24h` (`harness/ops/checks.py:320`) counted 11 rows on 2026-09-14: three
decreases (games 454, 396, 416) that match ESPN's own scoreboard bodies (Minnesota 19 then 13,
Texas 31 then 17, UT Martin 6 then 0). ESPN corrected its own linescore and `link_espn_scoreboard`
recorded both bodies, appended never updated (the design the whole table is built on) -- the
check's premise ("a game's score never goes down") is false for this feed. The user's ruling
(journal 207) authorizes the row's own remedy: the writer marks a lower-scoring row as a
correction, and the check excludes marked rows. No stored row changes (gate 3): the remedy is a
marker on future rows plus the check's predicate, which the same ruling authorizes under gate 13
for this one predicate only.

An additive mirror of `harness/db/models.py`'s `GameScoreEvent.correction` and
`harness/db/schema.py`'s `_COLUMN_DDL` entry -- the identical statement below. The models and
`create_schema` stay the schema authority; this file records the history, and
`tests/test_alembic.py` builds one database each way and compares the catalogues, so the two
cannot drift apart quietly.

The column carries a server default (`not null default false`), and the model declares the
identical one (`server_default=text("false")`) rather than the client-side-only `default=False`
`GameScoreEvent`'s sibling columns use: `game_score_events` predates `0001_baseline`, so on a
fresh test database `create_all` builds the table -- and this column -- directly, while the
migrated path builds the table from `0001_baseline`'s frozen shape (which predates this fix) and
then runs this revision's `ADD COLUMN`. Without a matching server default the two paths would
leave the column with a different Postgres-level default and the catalogue diff would fail; phase
4.6's `ParlayCard.combined_kind`/`ParlayLeg.offered` etc. (`harness/db/models.py`) establish the
same pattern for the same reason, stated in their own comments.

`downgrade()` is `pass` (roadmap invariant 5, every revision since `0002_phase45` except
`0007_raw_events_lookup`'s index drop, which the module docstring there explains carries none of
the data-loss risk this rule guards against -- a stored boolean does). Dropping the column would
also cross the user's own "No row changes" ruling if a downgrade ever ran against a database
holding rows the writer had already marked. Rolling back is `git checkout <sha> &&
make deploy-omarchy-app`, which never runs `migrate ensure`; old code simply ignores the column.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009_score_correction"
down_revision: str | None = "0008_positions_open_fill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Byte-identical to `harness/db/schema.py`'s `_COLUMN_DDL` entry for this column.
_COLUMNS = (
    "alter table game_score_events add column if not exists correction boolean not null default false",
)


def upgrade() -> None:
    for statement in _COLUMNS:
        op.execute(statement)


def downgrade() -> None:
    # Additive only (roadmap invariant 5); see the module docstring for how a rollback is done
    # and why this one, unlike 0007_raw_events_lookup's index drop, does not undo its statement.
    pass
