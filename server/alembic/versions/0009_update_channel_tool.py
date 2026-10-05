"""grant update_channel to agents that can create channels

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE agents
        SET tools = (tools::jsonb || '[{"name": "update_channel", "approval": "auto"}]'::jsonb)::json
        WHERE tools::jsonb @> '[{"name": "create_channel"}]'::jsonb
          AND NOT tools::jsonb @> '[{"name": "update_channel"}]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE agents
        SET tools = COALESCE((SELECT jsonb_agg(t) FROM jsonb_array_elements(tools::jsonb) t
                              WHERE t->>'name' <> 'update_channel'), '[]'::jsonb)::json
    """)
