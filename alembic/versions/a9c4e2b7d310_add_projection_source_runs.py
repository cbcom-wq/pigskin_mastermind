"""add projection source runs

Revision ID: a9c4e2b7d310
Revises: f7a2b8c3d9e1
Create Date: 2026-09-06 10:12:04.113927

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c4e2b7d310'
down_revision: Union[str, Sequence[str], None] = 'f7a2b8c3d9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'projection_source_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('week', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('rows_written', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'source', 'year', 'week', name='uq_projection_source_run',
        ),
    )
    op.create_index(
        op.f('ix_projection_source_runs_source'),
        'projection_source_runs', ['source'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_projection_source_runs_source'),
        table_name='projection_source_runs',
    )
    op.drop_table('projection_source_runs')
