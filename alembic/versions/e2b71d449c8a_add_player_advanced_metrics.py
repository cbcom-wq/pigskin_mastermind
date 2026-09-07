"""add player advanced metrics

Revision ID: e2b71d449c8a
Revises: d5f92a604e1b
Create Date: 2026-09-07 17:44:02.881350

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2b71d449c8a'
down_revision: Union[str, Sequence[str], None] = 'd5f92a604e1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'player_advanced_metrics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('metric', sa.String(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'player_id', 'year', 'week', 'metric', name='uq_player_metric_week',
        ),
    )
    op.create_index(
        op.f('ix_player_advanced_metrics_player_id'),
        'player_advanced_metrics', ['player_id'], unique=False,
    )
    op.create_index(
        'ix_metric_week_scan',
        'player_advanced_metrics', ['year', 'week', 'metric'], unique=False,
    )
    op.create_index(
        'ix_metric_player_series',
        'player_advanced_metrics', ['player_id', 'metric'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_metric_player_series', table_name='player_advanced_metrics')
    op.drop_index('ix_metric_week_scan', table_name='player_advanced_metrics')
    op.drop_index(
        op.f('ix_player_advanced_metrics_player_id'),
        table_name='player_advanced_metrics',
    )
    op.drop_table('player_advanced_metrics')
