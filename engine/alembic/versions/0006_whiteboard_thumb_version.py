"""whiteboards: which version of the board the thumbnail shows

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('whiteboards') as batch:
        batch.add_column(sa.Column('thumb_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('whiteboards') as batch:
        batch.drop_column('thumb_version')
