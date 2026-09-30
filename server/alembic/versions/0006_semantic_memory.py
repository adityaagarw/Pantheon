"""semantic memory: pgvector embeddings on memories

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-29 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column('memories', sa.Column('embedding', Vector(384), nullable=True))
    op.add_column('memories', sa.Column('source', sa.String(), server_default='agent', nullable=False))
    op.add_column('memories', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.execute("CREATE INDEX IF NOT EXISTS ix_memories_embedding ON memories "
               "USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding")
    op.drop_column('memories', 'updated_at')
    op.drop_column('memories', 'source')
    op.drop_column('memories', 'embedding')
