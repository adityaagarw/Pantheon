"""polls: schedules that wake an agent every few seconds, for a while

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('schedules') as batch:
        batch.add_column(sa.Column('every_seconds', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('ends_at', app.core.types.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('schedules') as batch:
        batch.drop_column('ends_at')
        batch.drop_column('every_seconds')
