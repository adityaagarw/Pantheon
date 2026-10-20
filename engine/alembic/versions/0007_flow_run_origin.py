"""flow_runs: the conversation a run was started from, for its answer

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('flow_runs') as batch:
        batch.add_column(sa.Column('origin', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('flow_runs') as batch:
        batch.drop_column('origin')
