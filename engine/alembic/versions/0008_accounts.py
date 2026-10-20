"""accounts: sign-ins and API keys for command-line agents (Claude Code, Codex)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'accounts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tool', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('key_enc', sa.Text(), nullable=True),
        sa.Column('status', sa.JSON(), nullable=False),
        sa.Column('cooling_until', app.core.types.UTCDateTime(), nullable=True),
        sa.Column('cooling_reason', sa.Text(), nullable=False, server_default=''),
        sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0'),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', app.core.types.UTCDateTime(), server_default=sa.text("(strftime('%Y-%m-%d %H:%M:%f', 'now'))"), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('accounts')
