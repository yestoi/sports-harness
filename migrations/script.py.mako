## Template for a new migration. Additive only: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT
## EXISTS, CREATE INDEX IF NOT EXISTS. An index on a bulk table goes through `concurrent_index`.
## A view is never a migration's business: views live in create_schema as CREATE OR REPLACE VIEW.
## See docs/runbooks/alembic.md.
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401

from migrations.env import concurrent_index  # noqa: F401

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    raise NotImplementedError(
        "rollback here is git checkout + make deploy-nas, see docs/runbooks/alembic.md")
