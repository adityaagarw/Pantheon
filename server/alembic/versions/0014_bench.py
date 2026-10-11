"""bench runs: SwarmBench runs of organizations

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'bench_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('org_id', sa.String(), nullable=True),
        sa.Column('org_name', sa.String(), nullable=False, server_default=''),
        sa.Column('status', sa.String(), nullable=False, server_default='queued'),
        sa.Column('config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('results', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('detail', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_bench_runs_org_id', 'bench_runs', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_bench_runs_org_id', 'bench_runs')
    op.drop_table('bench_runs')
