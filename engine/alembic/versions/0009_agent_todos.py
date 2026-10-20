"""agents.todos: an agent's own checklist for the work in hand

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.add_column(sa.Column('todos', sa.JSON(), server_default='[]', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('agents') as batch:
        batch.drop_column('todos')
