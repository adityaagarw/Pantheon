"""secrets agents use by name, and their usage log

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-05 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'secrets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('value_enc', sa.Text(), nullable=False),
        sa.Column('agent_ids', sa.JSON(), nullable=False),
        sa.Column('domains', sa.JSON(), nullable=False),
        sa.Column('allow_shell', sa.Boolean(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'name'),
    )
    op.create_index('ix_secrets_org_id', 'secrets', ['org_id'])
    op.create_table(
        'secret_uses',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('secret_id', sa.String(), nullable=False),
        sa.Column('secret_name', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('tool', sa.String(), nullable=False),
        sa.Column('target', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_secret_uses_org_id', 'secret_uses', ['org_id'])
    op.create_index('ix_secret_uses_secret_id', 'secret_uses', ['secret_id'])
    # Every agent can see which secrets it may use (names only).
    op.execute("""
        UPDATE agents
        SET tools = (tools::jsonb || '[{"name": "list_secrets", "approval": "auto"}]'::jsonb)::json
        WHERE NOT tools::jsonb @> '[{"name": "list_secrets"}]'::jsonb
          AND NOT is_supervisor
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE agents
        SET tools = COALESCE((SELECT jsonb_agg(t) FROM jsonb_array_elements(tools::jsonb) t
                              WHERE t->>'name' <> 'list_secrets'), '[]'::jsonb)::json
    """)
    op.drop_table('secret_uses')
    op.drop_table('secrets')
