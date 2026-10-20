"""flows: steps as a directed acyclic graph, and their runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.core.types

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'flows',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('graph', sa.JSON(), nullable=False),
        sa.Column('created_at', app.core.types.UTCDateTime(), server_default=sa.text("(strftime('%Y-%m-%d %H:%M:%f', 'now'))"), nullable=False),
        sa.Column('updated_at', app.core.types.UTCDateTime(), server_default=sa.text("(strftime('%Y-%m-%d %H:%M:%f', 'now'))"), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_flows_org_id', 'flows', ['org_id'])
    op.create_table(
        'flow_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('flow_id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('input', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('graph', sa.JSON(), nullable=False),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('created_at', app.core.types.UTCDateTime(), server_default=sa.text("(strftime('%Y-%m-%d %H:%M:%f', 'now'))"), nullable=False),
        sa.Column('finished_at', app.core.types.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(['flow_id'], ['flows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_flow_runs_flow_id', 'flow_runs', ['flow_id'])
    op.create_index('ix_flow_runs_org_id', 'flow_runs', ['org_id'])


def downgrade() -> None:
    op.drop_table('flow_runs')
    op.drop_table('flows')
