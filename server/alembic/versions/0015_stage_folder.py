"""stage page folders: an archive for superseded pages

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-12 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('stage_pages', sa.Column('folder', sa.String(), nullable=False,
                                           server_default=''))


def downgrade() -> None:
    op.drop_column('stage_pages', 'folder')
