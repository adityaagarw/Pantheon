"""physical world, runtime assets and meta agents

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('meta_role', sa.String(), nullable=True))
    op.add_column('agents', sa.Column('location', sa.JSON(), nullable=True))
    # The existing supervisor becomes Zeus.
    op.execute("UPDATE agents SET meta_role = 'zeus' WHERE is_supervisor")
    op.create_table(
        'world_objects',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('asset', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('holder_id', sa.String(), nullable=True),
        sa.Column('place', sa.JSON(), nullable=False),
        sa.Column('state', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['holder_id'], ['agents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_world_objects_org_id', 'world_objects', ['org_id'])
    op.create_index('ix_world_objects_holder_id', 'world_objects', ['holder_id'])
    op.create_table(
        'assets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('file', sa.String(), nullable=True),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('size', sa.JSON(), nullable=False),
        sa.Column('scale', sa.Float(), nullable=False),
        sa.Column('carryable', sa.Boolean(), nullable=False),
        sa.Column('blocking', sa.Boolean(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('source_url', sa.String(), nullable=False),
        sa.Column('license', sa.String(), nullable=False),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('assets')
    op.drop_index('ix_world_objects_holder_id', table_name='world_objects')
    op.drop_index('ix_world_objects_org_id', table_name='world_objects')
    op.drop_table('world_objects')
    op.drop_column('agents', 'location')
    op.drop_column('agents', 'meta_role')
