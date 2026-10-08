"""stage documents: spreadsheets, PDFs and notebooks on the Stage

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOC_TOOLS = ["doc_list", "sheet_new", "sheet_read", "sheet_write", "pdf_open", "pdf_read",
             "pdf_show", "notebook_new", "notebook_read", "notebook_add_cell",
             "notebook_edit_cell", "notebook_run"]


def upgrade() -> None:
    op.create_table(
        'stage_docs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(), nullable=False),
        sa.Column('updated_by', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_stage_docs_org_id', 'stage_docs', ['org_id'])
    # Agents that already work on the Stage (they have the whiteboard tools) get the
    # document tools too.
    add = ", ".join(f'{{"name": "{n}", "approval": "auto"}}' for n in DOC_TOOLS)
    op.execute(f"""
        UPDATE agents
        SET tools = (tools::jsonb || '[{add}]'::jsonb)::json
        WHERE tools::jsonb @> '[{{"name": "whiteboard_read"}}]'::jsonb
          AND NOT tools::jsonb @> '[{{"name": "sheet_read"}}]'::jsonb
    """)


def downgrade() -> None:
    op.drop_table('stage_docs')
