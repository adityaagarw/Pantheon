"""excalidraw whiteboards

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-30 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'whiteboards',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('elements', sa.JSON(), nullable=False),
        sa.Column('app_state', sa.JSON(), nullable=False),
        sa.Column('files', sa.JSON(), nullable=False),
        sa.Column('pending', sa.JSON(), nullable=False),
        sa.Column('thumbnail', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_whiteboards_org_id', 'whiteboards', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_whiteboards_org_id', table_name='whiteboards')
    op.drop_table('whiteboards')
