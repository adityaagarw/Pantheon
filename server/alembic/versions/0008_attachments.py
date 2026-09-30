"""file uploads: attachments and searchable chunks

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-30 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('message_id', sa.String(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('mime', sa.String(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('error', sa.Text(), nullable=False),
        sa.Column('info', sa.JSON(), nullable=False),
        sa.Column('uploaded_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attachments_org_id', 'attachments', ['org_id'])
    op.create_index('ix_attachments_agent_id', 'attachments', ['agent_id'])
    op.create_table(
        'attachment_chunks',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('attachment_id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('idx', sa.Integer(), nullable=False),
        sa.Column('start', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(384), nullable=True),
        sa.ForeignKeyConstraint(['attachment_id'], ['attachments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_attachment_chunks_attachment_id', 'attachment_chunks', ['attachment_id'])
    op.create_index('ix_attachment_chunks_org_id', 'attachment_chunks', ['org_id'])
    op.execute("CREATE INDEX IF NOT EXISTS ix_attachment_chunks_embedding ON attachment_chunks "
               "USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attachment_chunks_embedding")
    op.drop_table('attachment_chunks')
    op.drop_table('attachments')
