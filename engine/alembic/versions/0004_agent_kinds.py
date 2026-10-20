"""agents of other kinds: CLI agents, remote agents, humans

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.add_column(sa.Column('kind', sa.String(), server_default='llm', nullable=False))
        batch.add_column(sa.Column('runner', sa.JSON(), server_default='{}', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.drop_column('runner')
        batch.drop_column('kind')
